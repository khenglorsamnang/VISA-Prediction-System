from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.config import BANK_STATEMENT_MAX_FILE_SIZE, PROJECT_ROOT, settings
from ..core.errors import APIError
from ..services.bank_statement_scoring import calculate_document_verification_score
from ..services.aba_integration import analyze_aba_pdf, aba_to_legacy_ocr, decrypted_render_bytes, reviewed_analysis
from ..services.automated_document_verification import (
    evaluate_automated_document_verification,
)
from ..services.bank_statement_scoring import analyze_bank_statement
from ..services.business_rules import (
    DEFAULT_RULE_POLICY,
    validate_rule_policy,
)
from ..services.document_metrics import (
    bank_requirement_points,
    summarize_documents,
)
from ..services.photo_validation import validate_applicant_photo
from ..services.assessment_context import application_requirements, canonical_summary, policy_snapshot
from ..services.readiness_scoring import calculate_bank_score_assessment
from ..services.cross_document_consistency import (
    document_consistency_checks,
    document_kind,
)
from .document_workflow import (
    document_workflow_status,
    has_completed_ocr,
    public_workflow_document,
)
from .dependencies import get_current_principal, get_mongo_database, require_roles
from .mongo_helpers import id_query, public_document, redact_sensitive_data

logger = logging.getLogger(__name__)
router = APIRouter()

_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}
_PRIVATE_DIR = PROJECT_ROOT / "backend" / "storage" / "private"
_OCR_TASKS: dict[str, asyncio.Task[None]] = {}
_MAX_OCR_ATTEMPTS = 3
# Passwords are consumed by one in-process job and never persisted or logged.
_PDF_PASSWORDS: dict[str, str] = {}


def _is_bank_requirement(requirement: dict[str, Any], document: dict[str, Any] | None = None) -> bool:
    value = " ".join(
        str(item or "")
        for item in (
            requirement.get("code"),
            requirement.get("name"),
            (document or {}).get("requirementCode"),
        )
    ).casefold()
    return "bank" in value and ("statement" in value or "3 month" in value or "3_month" in value)


def _aba_review_messages(analysis: dict[str, Any]) -> list[str]:
    blockers = analysis.get("reviewBlockers") or []
    messages = [
        str(item.get("message"))
        for item in blockers
        if isinstance(item, dict) and item.get("message")
    ]
    return messages


async def _refresh_application_score(database: AsyncDatabase, application: dict[str, Any] | None) -> None:
    """Keep the cached application summary current after document state changes."""
    if not application or not application.get("_id"):
        return
    try:
        # The caller may hold an old application object (for example, before a
        # primary statement was selected). Never overwrite that newer context.
        application = await database["visaapplications"].find_one(id_query(str(application["_id"]))) or application
        requirements = await database["visarequirements"].find(
            {"visaTypeId": application.get("visaTypeId"), "status": "ACTIVE"}
        ).to_list(length=None)
        if not requirements:
            requirements = application.get("requirementSnapshot") or []
        requirements = application_requirements(application, requirements)
        requirements = [{**item, "requirementId": str(item.get("_id") or item.get("requirementId"))} for item in requirements]
        documents = await database["documents"].find({"applicationId": str(application["_id"])}).to_list(length=None)
        documents = [d for d in documents if str(d.get("applicationId") or "") == str(application["_id"])]
        summary = canonical_summary(application, requirements, documents)
        snapshot = policy_snapshot(application)
        now = datetime.now(timezone.utc)
        await database["visaapplications"].update_one(
            {"_id": application["_id"]},
            {"$set": {
                "completenessScore": summary["documentAssessmentScore"],
                "documentReadiness": summary["documentAssessmentScore"],
                "earnedPoints": summary["earnedPoints"],
                "maximumPoints": summary["maximumPoints"],
                "verificationPercentage": summary["verificationPercentage"],
                "submissionCompleteness": summary["submissionCompleteness"],
                "verificationMetricsCurrent": summary.get("processingDocuments", 0) == 0,
                "predictionStatus": "STALE" if application.get("predictionStatus") == "COMPLETED" else application.get("predictionStatus", "UNAVAILABLE"),
                "predictionInputsCurrent": False,
                "approvalProbability": None,
                "approvalProbabilityNormalized": None,
                "rejectionProbabilityNormalized": None,
                "lastCalculationAt": now,
                "scoringVersion": summary.get("scoringVersion", "document-requirements-v2"),
                **({"evidenceAssessment": summary, "assessmentInputFingerprint": summary.get("inputFingerprint"),
                    "assessmentStatus": summary.get("status"), "assessmentStale": False} if snapshot else {}),
                "updatedAt": now,
            }},
        )
    except PyMongoError:
        logger.exception("Could not refresh application score")


async def _refresh_cross_document_consistency(
    database: AsyncDatabase,
    application_id: str,
) -> None:
    application = await database["visaapplications"].find_one(id_query(application_id))
    if not application:
        return
    owner_id = str(application.get("customer") or application.get("customerId") or "")
    applicant = (
        await database["customers"].find_one(id_query(owner_id)) if owner_id else None
    ) or {}
    requirements = application.get("requirementSnapshot") or []
    if not requirements:
        requirements = await database["visarequirements"].find(
            {"visaTypeId": application.get("visaTypeId"), "status": "ACTIVE"}
        ).to_list(length=None)
    requirements = application_requirements(application, requirements)
    documents = await database["documents"].find(
        {"applicationId": application_id}
    ).to_list(length=None)
    documents = [
        item
        for item in documents
        if str(item.get("applicationId") or "") == application_id
        and str(item.get("status") or "").upper() not in {"DELETED", "REPLACED"}
    ]
    checks_by_document = document_consistency_checks(
        application, applicant, requirements, documents
    )
    updated_any = False
    now = datetime.now(timezone.utc)
    for document in documents:
        document_id = str(document.get("_id") or "")
        cross_checks = checks_by_document.get(document_id)
        if cross_checks is None:
            continue
        verification = document.get("automatedVerification")
        if not isinstance(verification, dict):
            continue
        base_checks = [
            item
            for item in verification.get("checks") or []
            if isinstance(item, dict)
            and not str(item.get("code") or "").startswith("CROSS_")
        ]
        base_status = str(
            verification.get("baseStatus") or verification.get("status") or ""
        ).upper()
        kind = document_kind(
            next(
                (
                    item
                    for item in requirements
                    if str(item.get("requirementId") or item.get("_id") or "")
                    == str(document.get("requirementId") or "")
                ),
                {},
            ),
            document,
        )
        if (
            kind == "bank_statement"
            and str(document.get("ocrStatus") or "").upper() == "OCR_FAILED"
        ):
            base_status = "FAILED"
        aba_verified = False
        if kind == "bank_statement" and document.get("abaStatementAnalysis"):
            aba = document["abaStatementAnalysis"]
            aba_verified = (
                aba.get("reviewStatus") == "VERIFIED"
                and aba.get("reconciliationStatus") == "RECONCILED"
                and aba.get("statementStrengthScore") is not None
                and not (aba.get("score") or {}).get("provisional")
            )
            blockers = _aba_review_messages(aba)
            cross_checks.append(
                {
                    "code": "CROSS_ABA_REVIEW",
                    "label": "Exact bank evidence review",
                    "status": "PASSED" if aba_verified else "NEEDS_REVIEW",
                    "reason": (
                        "Financial evidence and reconciliation passed staff review; authenticity remains unverified and is tracked separately."
                        if aba_verified
                        else "; ".join(blockers)
                        or (
                            "Financial evidence is reconciled with no extraction blockers; staff bank-evidence review is pending. "
                            "Authenticity remains unverified and is tracked separately."
                        )
                    ),
                    "fields": ["abaStatementAnalysis"],
                }
            )
        cross_failed = any(
            str(item.get("status") or "").upper() != "PASSED"
            for item in cross_checks
        )
        if kind == "bank_statement":
            internal_checks_passed = (
                aba_verified
                if document.get("abaStatementAnalysis")
                else all(
                    str(item.get("status") or "").upper() == "PASSED"
                    for item in base_checks
                    if str(item.get("code") or "").upper() != "OCR_CONFIDENCE"
                )
                and bool(base_checks)
            )
            if (
                base_status == "FAILED"
                and str(document.get("ocrStatus") or "").upper() == "OCR_FAILED"
            ):
                status = "FAILED"
            elif cross_failed:
                status = "NEEDS_REVIEW"
            elif base_status in {"REJECTED", "FAILED"}:
                status = base_status
            elif internal_checks_passed:
                status = "VERIFIED"
            else:
                status = "NEEDS_REVIEW"
        else:
            status = (
                "NEEDS_REVIEW"
                if cross_failed
                else base_status
                if base_status in {"VERIFIED", "REJECTED", "FAILED"}
                else "NEEDS_REVIEW"
            )
        previous_cross_reasons = {
            str(item.get("reason") or "")
            for item in verification.get("crossDocumentChecks") or []
            if isinstance(item, dict)
        }
        base_reasons = list(
            verification.get("baseReasons")
            or [
                reason
                for reason in verification.get("reasons") or []
                if reason not in previous_cross_reasons
            ]
        )
        reasons = list(
            dict.fromkeys(
                [
                    *base_reasons,
                    *[
                        item["reason"]
                        for item in cross_checks
                        if str(item.get("status") or "").upper() != "PASSED"
                    ],
                ]
            )
        )
        updated_verification = {
            **verification,
            "baseStatus": base_status,
            "baseReasons": base_reasons,
            "status": status,
            "checks": [*base_checks, *cross_checks],
            "crossDocumentChecks": cross_checks,
            "reasons": reasons,
        }
        if (
            str(document.get("status") or "").upper() == status
            and verification.get("crossDocumentChecks") == cross_checks
            and verification.get("status") == status
        ):
            continue
        history = list(document.get("automatedVerificationHistory") or [])
        history.append(updated_verification)
        update_result = await database["documents"].update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "status": status,
                    "workflowStatus": status,
                    "automatedVerificationStatus": status,
                    "automatedVerification": updated_verification,
                    "automatedVerificationHistory": history,
                    "crossDocumentConsistency": cross_checks,
                    "updatedAt": now,
                }
            },
        )
        updated_any = updated_any or bool(update_result.matched_count)
    if updated_any or documents:
        await _refresh_application_score(database, application)


async def _persist_initial_bank_statement_analysis(
    document: dict[str, Any],
    database: AsyncDatabase,
) -> None:
    if not _is_bank_requirement(
        {"code": document.get("requirementCode")},
        document,
    ):
        return
    fresh_document = await database["documents"].find_one(
        id_query(str(document.get("_id") or ""))
    )
    if not fresh_document or isinstance(fresh_document.get("bankStatementAnalysis"), dict):
        return
    application_id = str(fresh_document.get("applicationId") or "")
    application = await database["visaapplications"].find_one(id_query(application_id))
    owner_id = str(
        (application or {}).get("customer")
        or (application or {}).get("customerId")
        or fresh_document.get("customerId")
        or ""
    )
    applicant = (
        await database["customers"].find_one(id_query(owner_id)) if owner_id else None
    ) or {}
    policy_record = await database["business_rules"].find_one(
        {"_id": "visa_financial_rules"}
    )
    policy = validate_rule_policy(
        policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
    )
    analysis = analyze_bank_statement(
        fresh_document.get("extractedFields") or {},
        raw_text=str(fresh_document.get("ocrText") or ""),
        applicant_name=str(applicant.get("fullName") or ""),
        config=policy["bank_statement_scoring"],
        financial_config=policy["financial_scoring"],
        ocr_complete=str(fresh_document.get("ocrStatus") or "").upper()
        == "OCR_COMPLETED",
        transaction_source_reviews=fresh_document.get("transactionSourceReviews") or {},
        document_id=str(fresh_document.get("_id") or ""),
    )
    analysis.update(
        calculate_bank_score_assessment(
            fresh_document,
            analysis,
            applicant,
            requirement_maximum=fresh_document.get("requirementMaximumPoints"),
        )
    )
    await database["documents"].update_one(
        {"_id": fresh_document["_id"]},
        {"$set": {"bankStatementAnalysis": analysis}},
    )


class OCRJobContract(BaseModel):
    id: str = Field(min_length=1)
    status: Literal[
        "QUEUED",
        "OCR_PENDING",
        "OCR_PROCESSING",
        "OCR_COMPLETED",
        "OCR_PARTIAL",
        "OCR_FAILED",
    ]
    progressPercent: int = Field(default=0, ge=0, le=100)
    pagesProcessed: int = Field(default=0, ge=0)
    totalPages: int = Field(default=0, ge=0)
    failedPages: list[dict[str, Any]] = Field(default_factory=list)
    message: str = "OCR job queued"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | str | None = None


def _document_type(requirement: dict[str, Any] | None) -> str:
    if not requirement:
        return "unknown"
    if requirement.get("qualityScorer") == "ABA_STATEMENT_V1":
        return "bank_statement"
    code = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(requirement.get("code") or "").strip().casefold(),
    ).strip("_")
    if "photo" in code or "photograph" in code:
        return "photo"
    if code in {
        "national_id",
        "national_id_card",
        "nationalid",
        "nationalidcard",
        "national_identity",
        "national_identity_card",
        "identity_card",
        "id_card",
        "identity_document",
        "nid",
    }:
        return "national_id"
    if code in {
        "passport",
        "photo",
        "bank_statement",
        "fixed_deposit",
        "employment_letter",
        "salary_slip",
        "salary_certificate",
        "employment_certificate",
        "national_id",
        "marriage_certificate",
        "birth_certificate",
        "invitation_letter",
        "hotel_booking",
        "flight_booking",
        "flight_reservation",
        "other_supporting_document",
        "travel_insurance",
    }:
        return {
            "salary_certificate": "salary_slip",
            "employment_certificate": "employment_letter",
            "flight_reservation": "flight_booking",
            "other_supporting_document": "other",
        }.get(code, code)
    label = str(requirement.get("name") or "").casefold()
    if "passport" in label:
        return "passport"
    if any(
        term in label
        for term in (
            "national id",
            "national identity",
            "identity card",
            "id card",
        )
    ):
        return "national_id"
    if "photo" in label or "photograph" in label:
        return "photo"
    if "bank" in label:
        return "bank_statement"
    if "fixed deposit" in label:
        return "fixed_deposit"
    if "employment" in label:
        return "employment_letter"
    if "salary" in label:
        return "salary_slip"
    if "marriage" in label:
        return "marriage_certificate"
    if "birth" in label:
        return "birth_certificate"
    if "invitation" in label:
        return "invitation_letter"
    if "hotel" in label or "accommodation" in label:
        return "hotel_booking"
    if "flight" in label or "air ticket" in label:
        return "flight_booking"
    return "unknown"


async def _is_photo_document(
    database: AsyncDatabase,
    document: dict[str, Any],
) -> bool:
    if _document_type(
        {
            "code": document.get("requirementCode"),
            "name": document.get("requirementName"),
        }
    ) == "photo":
        return True
    requirement_id = document.get("requirementId")
    if not requirement_id:
        return False
    requirement = await database["visarequirements"].find_one(
        id_query(str(requirement_id))
    )
    return _document_type(requirement) == "photo"


def _normalize_legacy_ocr_result(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    pages = result.get("pages")
    if isinstance(pages, list):
        normalized_pages = []
        confidences = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_text = page.get("text")
            page_confidence = page.get("confidence")
            if isinstance(page.get("results"), list):
                values = [
                    item
                    for item in page["results"]
                    if isinstance(item, dict)
                ]
                page_text = "\n".join(
                    str(item["text"])
                    for item in values
                    if item.get("text")
                )
                page_confidences = [
                    float(item["confidence"])
                    for item in values
                    if isinstance(item.get("confidence"), (int, float))
                ]
                if page_confidences:
                    page_confidence = sum(page_confidences) / len(page_confidences)
            if isinstance(page_confidence, (int, float)):
                confidences.append(float(page_confidence))
            normalized_pages.append(
                {
                    "page": page.get("page"),
                    "text": str(page_text or ""),
                    "confidence": page_confidence,
                    "method": page.get("method") or "ocr",
                    "boxes": page.get("boxes") or [],
                    "status": page.get("status"),
                    "error": page.get("error"),
                }
            )
        normalized["pages"] = normalized_pages
        normalized["metrics"] = {
            **(
                result.get("metrics")
                if isinstance(result.get("metrics"), dict)
                else {}
            ),
            "page_count": result.get("total_pages") or len(normalized_pages),
        }
        normalized.setdefault(
            "extracted_text",
            result.get("full_text")
            or "\n\n".join(page["text"] for page in normalized_pages),
        )
        if "confidence" not in normalized and confidences:
            normalized["confidence"] = sum(confidences) / len(confidences)
    normalized.setdefault(
        "failed_pages",
        [
            {"page": page.get("page"), "error": page.get("error")}
            for page in normalized.get("pages", [])
            if isinstance(page, dict) and page.get("status") == "OCR_FAILED"
        ],
    )
    normalized.setdefault("missing_fields", [])
    return normalized


def _parse_ocr_job(payload: Any, response_context: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(
            f"OCR service returned invalid {response_context}: expected a JSON object."
        )
    raw_job = payload.get("job", payload)
    if not isinstance(raw_job, dict):
        raise ValueError(
            f"OCR service returned invalid {response_context}: missing job object."
        )
    job_id = raw_job.get("id") or raw_job.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError(
            f"OCR service returned invalid {response_context}: missing job ID."
        )
    raw_status = str(raw_job.get("status") or "").strip().upper()
    status = {
        "QUEUED": "QUEUED",
        "PENDING": "QUEUED",
        "OCR_PENDING": "QUEUED",
        "PROCESSING": "OCR_PROCESSING",
        "OCR_PROCESSING": "OCR_PROCESSING",
        "COMPLETED": "OCR_COMPLETED",
        "SUCCESS": "OCR_COMPLETED",
        "OCR_COMPLETED": "OCR_COMPLETED",
        "PARTIAL": "OCR_PARTIAL",
        "OCR_PARTIAL": "OCR_PARTIAL",
        "FAILED": "OCR_FAILED",
        "OCR_FAILED": "OCR_FAILED",
    }.get(raw_status, raw_status)
    result = _normalize_legacy_ocr_result(raw_job.get("result"))
    total_pages = raw_job.get("totalPages", raw_job.get("total_pages"))
    if total_pages is None and isinstance(result, dict):
        total_pages = (result.get("metrics") or {}).get("page_count")
    try:
        total_pages = max(0, int(total_pages or 0))
        pages_processed = max(
            0,
            int(
                raw_job.get(
                    "pagesProcessed",
                    raw_job.get(
                        "pages_processed",
                        total_pages
                        if status in {"OCR_COMPLETED", "OCR_PARTIAL"}
                        else 0,
                    ),
                )
                or 0
            ),
        )
        progress = int(
            raw_job.get(
                "progressPercent",
                raw_job.get(
                    "progress_percent",
                    100 if status in {"OCR_COMPLETED", "OCR_PARTIAL"} else (
                        round(100 * pages_processed / total_pages)
                        if total_pages
                        else 0
                    ),
                ),
            )
            or 0
        )
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(
            f"OCR service returned invalid {response_context}: invalid progress values."
        ) from error
    raw_error = raw_job.get("error")
    if isinstance(raw_error, dict):
        error_value: dict[str, Any] | str | None = {
            **raw_error,
            "code": str(raw_error.get("code") or "OCR_PROCESSING_ERROR"),
            "message": str(raw_error.get("message") or "OCR processing failed."),
        }
    elif raw_error:
        error_value = str(raw_error)
    else:
        error_value = None
    normalized_job = {
        "id": job_id.strip(),
        "status": status,
        "progressPercent": progress,
        "pagesProcessed": pages_processed,
        "totalPages": total_pages,
        "failedPages": raw_job.get("failedPages")
        or raw_job.get("failed_pages")
        or (result or {}).get("failed_pages")
        or [],
        "message": str(
            raw_job.get("message")
            or (
                "OCR completed successfully"
                if status == "OCR_COMPLETED"
                else "OCR processing failed"
                if status == "OCR_FAILED"
                else "OCR partially completed"
                if status == "OCR_PARTIAL"
                else "OCR job queued"
                if status == "QUEUED"
                else "Processing document"
            )
        ),
        "result": result,
        "error": error_value,
    }
    try:
        return OCRJobContract.model_validate(normalized_job).model_dump()
    except ValidationError as error:
        invalid_fields = ", ".join(
            ".".join(str(part) for part in item["loc"])
            for item in error.errors()
        )
        raise ValueError(
            f"OCR service returned invalid {response_context}; check fields: {invalid_fields}."
        ) from error


def _bank_statement_field_score(
    requirement_code: str,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    fields = result.get("fields")
    if requirement_code != "bank_statement" or not isinstance(fields, dict):
        return None
    if (
        str(result.get("status") or "").lower() == "partial"
        or result.get("failed_pages")
        or (result.get("metrics") or {}).get("status") == "OCR_PARTIAL"
    ):
        return None
    return calculate_document_verification_score(fields)


async def _run_ocr(
    content: bytes, filename: str, requirement_code: str
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_service_timeout_seconds, connect=5.0)
        ) as client:
            response = await client.post(
                f"{settings.ai_service_url}/ocr/extract",
                files={"file": (filename, content)},
                data={"requirement_code": requirement_code},
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (httpx.HTTPError, ValueError):
        logger.warning(
            "OCR service unavailable or returned invalid data for file=%s",
            filename,
            exc_info=True,
        )
        return None


def _schedule_document_ocr(
    document: dict[str, Any],
    database: AsyncDatabase,
    attempt: int,
) -> bool:
    document_id = str(document["_id"])
    existing_task = _OCR_TASKS.get(document_id)
    if existing_task and not existing_task.done():
        return False

    task = asyncio.create_task(
        _process_document_ocr(document, database, attempt)
    )
    _OCR_TASKS[document_id] = task

    def clear_task(completed_task: asyncio.Task[None]) -> None:
        if _OCR_TASKS.get(document_id) is completed_task:
            _OCR_TASKS.pop(document_id, None)
        if not completed_task.cancelled() and completed_task.exception():
            logger.error(
                "Document OCR task failed unexpectedly: document_id=%s",
                document_id,
                exc_info=completed_task.exception(),
            )

    task.add_done_callback(clear_task)
    return True


def _valid_ocr_result(
    job: dict[str, Any],
    *,
    require_all_pages_success: bool = True,
) -> tuple[bool, int, dict[str, Any]]:
    result = job.get("result")
    if not isinstance(result, dict):
        return False, 0, {}
    pages = result.get("pages")
    try:
        total_pages = int(job.get("totalPages") or 0)
        processed_pages = int(job.get("pagesProcessed") or 0)
        progress = int(job.get("progressPercent") or 0)
        failed_pages = job.get("failedPages") or result.get("failed_pages") or []
        page_numbers = sorted(
            int(page.get("page") or 0)
            for page in pages
            if isinstance(page, dict)
        )
    except (TypeError, ValueError):
        return False, 0, result
    valid_page_methods = all(
        isinstance(page, dict)
        and page.get("method") in {"ocr", "embedded_text"}
        for page in pages
    )
    failed_page_numbers = {
        int(item.get("page") or 0)
        for item in failed_pages
        if isinstance(item, dict)
    }
    page_failed_numbers = {
        int(page.get("page") or 0)
        for page in pages
        if isinstance(page, dict) and page.get("status") == "OCR_FAILED"
    }
    job_status = str(job.get("status") or "").upper()
    failure_metadata_valid = failed_page_numbers == page_failed_numbers
    if require_all_pages_success:
        failure_metadata_valid = (
            failure_metadata_valid
            and not failed_page_numbers
            and job_status == "OCR_COMPLETED"
        )
    else:
        failure_metadata_valid = failure_metadata_valid and (
            job_status == "OCR_COMPLETED"
            and not failed_page_numbers
            or job_status == "OCR_PARTIAL"
            and bool(failed_page_numbers)
        )
    valid = (
        isinstance(pages, list)
        and total_pages > 0
        and len(pages) == total_pages
        and page_numbers == list(range(1, total_pages + 1))
        and processed_pages == total_pages
        and progress == 100
        and valid_page_methods
        and failure_metadata_valid
    )
    return valid, total_pages, result


async def _run_automated_document_verification(
    document: dict[str, Any],
    database: AsyncDatabase,
    *,
    ocr_status: str,
    ocr_confidence: float = 0,
    extracted_fields: dict[str, Any] | None = None,
    content_hash: str | None = None,
    detected_document_type: str | None = None,
    ocr_text: str | None = None,
) -> dict[str, Any]:
    fresh_document = await database["documents"].find_one(
        id_query(str(document.get("_id") or ""))
    )
    if fresh_document:
        document = {**document, **fresh_document}
    application_id = str(document.get("applicationId") or "")
    application = await database["visaapplications"].find_one(
        id_query(application_id)
    )
    owner_id = str(
        (application or {}).get("customer")
        or (application or {}).get("customerId")
        or document.get("customerId")
        or ""
    )
    applicant = (
        await database["customers"].find_one(id_query(owner_id))
        if owner_id
        else {}
    ) or {}
    requirements = (application or {}).get("requirementSnapshot") or []
    requirement = next(
        (
            item
            for item in requirements
            if str(item.get("requirementId") or item.get("_id") or "")
            == str(document.get("requirementId") or "")
        ),
        {},
    )
    if not requirement and document.get("requirementId"):
        requirement = (
            await database["visarequirements"].find_one(
                id_query(str(document["requirementId"]))
            )
            or {}
        )
    duplicate_reference = None
    active_documents = await database["documents"].find(
        {"applicationId": application_id}
    ).to_list(length=None)
    if content_hash:
        duplicate = next(
            (
                item
                for item in active_documents
                if str(item.get("_id")) != str(document.get("_id"))
                and str(item.get("status") or "").upper()
                not in {"DELETED", "REPLACED"}
                and item.get("contentHash") == content_hash
            ),
            None,
        )
        if duplicate:
            duplicate_reference = str(
                duplicate.get("documentReference")
                or duplicate.get("_id")
                or "existing document"
            )

    verification_document = {
        **document,
        "ocrStatus": ocr_status,
        "ocrConfidence": ocr_confidence,
        "extractedFields": extracted_fields or {},
        "detectedDocumentType": detected_document_type
        or document.get("detectedDocumentType"),
        "ocrText": ocr_text
        if ocr_text is not None
        else document.get("ocrText", ""),
    }
    is_photo = _document_type(requirement or {"code": document.get("requirementCode")}) == "photo"
    if is_photo:
        image_path = Path(str(document.get("storagePath") or "")).resolve()
        try:
            image_path.relative_to(_PRIVATE_DIR.resolve())
            photo_content = image_path.read_bytes() if image_path.is_file() else b""
        except (OSError, ValueError):
            logger.warning(
                "Photo image is unavailable for validation: document_id=%s",
                document.get("_id"),
            )
            photo_content = b""
        if photo_content:
            verification_document["photoValidation"] = validate_applicant_photo(
                photo_content,
                str(document.get("fileName") or ""),
                str(document.get("mimeType") or ""),
                requirement.get("photoRules"),
            )
        else:
            verification_document["photoValidation"] = {
                "checks": [
                    {
                        "code": "PHOTO_VALIDATION_UNAVAILABLE",
                        "label": "Photo image validation",
                        "status": "NEEDS_REVIEW",
                        "reason": "The stored image is unavailable; staff must inspect or request the photo again.",
                        "fields": [],
                    }
                ],
                "manualReviewRequired": bool(
                    (requirement.get("photoRules") or {}).get("manualReviewRequired")
                ),
            }
    if content_hash:
        verification_document["contentHash"] = content_hash
    bank_statement_analysis = None
    if _is_bank_requirement(requirement, document):
        stored_analysis = document.get("bankStatementAnalysis")
        if isinstance(stored_analysis, dict):
            bank_statement_analysis = stored_analysis
    verification_requirement = {
        **requirement,
        "code": "bank_statement" if _is_bank_requirement(requirement, document) else (
            requirement.get("code") or document.get("requirementCode")
        ),
    }
    result = evaluate_automated_document_verification(
        verification_document,
        applicant,
        verification_requirement,
        duplicate_reference=duplicate_reference,
        bank_statement_analysis=bank_statement_analysis,
    )
    # Retrying legacy verification is not an alternative route around exact ABA
    # reconciliation, selected-account review, or documented corrections.
    fresh_document = await database["documents"].find_one(id_query(str(document["_id"])))
    if fresh_document and fresh_document.get("abaStatementAnalysis"):
        result["status"] = "NEEDS_REVIEW"
        aba_review = fresh_document["abaStatementAnalysis"]
        blockers = [
            str(item.get("message"))
            for item in aba_review.get("reviewBlockers") or []
            if isinstance(item, dict) and item.get("message")
        ]
        result["reasons"] = [
            *(result.get("reasons") or []),
            *blockers,
            *(
                ["Staff financial-evidence review remains pending; authenticity is not verified."]
                if not blockers
                else []
            ),
        ]
    assigned_points = 0.0
    try:
        assigned_points = max(
            0.0,
            float(
                requirement.get(
                    "maximumEarnedPoints",
                    requirement.get("weight", requirement.get("points", 0)),
                )
                or 0
            ),
        )
    except (TypeError, ValueError):
        logger.warning(
            "Invalid assigned document points for document_id=%s",
            document.get("_id"),
        )
    now = datetime.now(timezone.utc)
    result["verificationTimestamp"] = now.isoformat()
    if isinstance(bank_statement_analysis, dict):
        score_document = {
            **verification_document,
            "status": result.get("status"),
            "reviewedBy": document.get("reviewedBy"),
            "manualReviewHistory": document.get("manualReviewHistory"),
        }
        bank_statement_analysis.update(
            calculate_bank_score_assessment(
                score_document,
                bank_statement_analysis,
                applicant,
                requirement_maximum=assigned_points,
            )
        )
    history = list(document.get("automatedVerificationHistory") or [])
    history.append(result)
    update = {
        "status": result["status"],
        "workflowStatus": result["status"],
        "automatedVerification": result,
        "automatedVerificationStatus": result["status"],
        "automatedVerificationAt": now,
        "automatedVerificationHistory": history,
        "awardedPoints": (
            bank_requirement_points(bank_statement_analysis, assigned_points)
            if _is_bank_requirement(requirement, document) and result["status"] == "VERIFIED"
            else round(assigned_points * float(result.get("photoPointsRatio") or 0), 2)
            if is_photo and result["status"] != "REJECTED"
            else assigned_points if result["status"] == "VERIFIED" else 0
        ),
        "updatedAt": now,
    }
    if isinstance(bank_statement_analysis, dict):
        update["bankStatementAnalysis"] = bank_statement_analysis
    if is_photo:
        update.update(
            {
                "ocrStatus": "NOT_REQUIRED",
                "ocrError": "",
                "ocrMessage": "OCR is not required for applicant photos.",
            }
        )
    if verification_document.get("detectedDocumentType"):
        update["detectedDocumentType"] = verification_document[
            "detectedDocumentType"
        ]
    if result["status"] == "REJECTED":
        update["rejectionReason"] = "; ".join(result["reasons"])
    try:
        await database["documents"].update_one(
            {"_id": document["_id"]},
            {"$set": update},
        )
        if application:
            await _refresh_cross_document_consistency(
                database, str(application["_id"])
            )
            refreshed = await database["documents"].find_one(
                id_query(str(document["_id"]))
            )
            if refreshed and isinstance(refreshed.get("automatedVerification"), dict):
                result = refreshed["automatedVerification"]
    except PyMongoError:
        logger.exception(
            "Could not persist automated verification: document_id=%s",
            document.get("_id"),
        )
        raise
    return result


async def _complete_local_aba(document: dict[str, Any], database: AsyncDatabase,
                              analysis: dict[str, Any], content_hash: str, lease_id: str) -> None:
    """Persist locally extracted ABA evidence, pending an explicit staff review.

    The lease/status comparison prevents an old job overwriting a deleted or
    replaced document. Native text is complete extraction, not fake OCR confidence.
    """
    collection = database["documents"]
    fresh = await collection.find_one(id_query(str(document["_id"])))
    if not fresh or fresh.get("status") in {"DELETED", "REPLACED"} or fresh.get("ocrLeaseOwner") != lease_id:
        return
    legacy = aba_to_legacy_ocr(analysis)
    fields = legacy["fields"]
    count = int((analysis.get("statement") or {}).get("pages") or 0)
    complete = bool(analysis.get("statement"))
    review_messages = _aba_review_messages(analysis)
    check = calculate_document_verification_score(fields) if complete else None
    compatibility_analysis = None
    if complete:
        policy_record = await database["business_rules"].find_one({"_id":"visa_financial_rules"})
        policy = validate_rule_policy(policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY)
        application = await database["visaapplications"].find_one(
            id_query(str(fresh.get("applicationId") or ""))
        )
        applicant_id = str(
            (application or {}).get("customer")
            or (application or {}).get("customerId")
            or fresh.get("customerId")
            or ""
        )
        applicant = (
            await database["customers"].find_one(id_query(applicant_id))
            if applicant_id
            else None
        ) or {}
        compatibility_analysis = analyze_bank_statement(
            fields,
            raw_text="",
            config=policy["bank_statement_scoring"],
            financial_config=policy["financial_scoring"],
            applicant_name=str(applicant.get("fullName") or ""),
            ocr_complete=True,
            transaction_source_reviews=fresh.get("transactionSourceReviews") or {},
            document_id=str(fresh.get("_id") or ""),
        )
        compatibility_analysis["evidenceConvention"] = "Legacy major-unit diagnostic only; authoritative ABA values are in abaStatementAnalysis."
        compatibility_analysis.update(
            calculate_bank_score_assessment(
                {
                    **fresh,
                    "extractedFields": fields,
                    "abaStatementAnalysis": analysis,
                },
                compatibility_analysis,
                requirement_maximum=fresh.get("requirementMaximumPoints"),
            )
        )
    now = datetime.now(timezone.utc)
    await collection.update_one(
        {"_id": document["_id"], "ocrLeaseOwner": lease_id, "status": {"$nin": ["DELETED", "REPLACED"]}},
        {"$set": {
            "abaStatementAnalysis": analysis, "status": "NEEDS_REVIEW" if complete else "FAILED", "workflowStatus": "NEEDS_REVIEW" if complete else "OCR_FAILED",
            "bankStatementAnalysis": compatibility_analysis,
            "ocrStatus": "OCR_COMPLETED" if complete else "OCR_FAILED", "ocrPagesProcessed": count,
            "ocrTotalPages": count, "ocrPageNumbers": list(range(1,count+1)), "ocrProgressPercent": 100 if complete else 0,
            "ocrMessage": (
                "ABA statement extracted locally. Review the listed unresolved financial or extraction evidence; authenticity remains unverified."
                if review_messages
                else "ABA statement extracted locally with no listed extraction or financial blockers. Staff review and authenticity verification remain separate."
            ),
            "ocrError": "" if complete else "; ".join(i["message"] for i in analysis.get("issues", [])),
            "ocrFieldEvidence": {
                "method": "ABA_NATIVE_LOCAL",
                "numericConfidenceAvailable": False,
                "sourcePages": analysis.get("fieldEvidence") or {},
            },
            "ocrConfidence": None,
            "extractedFields": fields, "contentHash": content_hash,
            "ocrWarnings": legacy["warnings"], "detectedDocumentType": "bank_statement",
            "bankStatementCheckPercent": check["document_verification_score"] if check else None,
            "bankStatementMissingFields": check["missing_fields"] if check else [],
            "awardedPoints": 0, "automatedVerificationStatus": "NEEDS_REVIEW" if complete else "FAILED",
            "automatedVerification": {
                "status": "NEEDS_REVIEW" if complete else "FAILED",
                "reasons": (
                    review_messages
                    or ["No extraction or reconciliation blockers were detected; staff bank-evidence review remains pending and authenticity is not verified."]
                    if complete
                    else ["Bank statement extraction failed."]
                ),
                "authenticityVerified": False,
            },
            "updatedAt": now,
        }},
    )
    application = await database["visaapplications"].find_one(id_query(str(document.get("applicationId") or "")))
    if application:
        await _refresh_cross_document_consistency(database, str(application["_id"]))


async def _process_document_ocr(
    document: dict[str, Any],
    database: AsyncDatabase,
    attempt: int,
) -> None:
    document_id = str(document["_id"])
    collection = database["documents"]
    lease_id = str(uuid4())
    lease_started = datetime.now(timezone.utc)
    lease_result = await collection.update_one(
        {
            "_id": document["_id"],
            "$or": [
                {"ocrLeaseExpiresAt": {"$exists": False}},
                {"ocrLeaseExpiresAt": {"$lte": lease_started}},
            ],
        },
        {
            "$set": {
                "ocrLeaseOwner": lease_id,
                "ocrLeaseExpiresAt": lease_started
                + timedelta(
                    seconds=max(60, settings.ai_service_timeout_seconds + 30)
                ),
            }
        },
    )
    if not lease_result.matched_count:
        logger.info("OCR task already claimed by another worker: document_id=%s", document_id)
        return
    password = _PDF_PASSWORDS.pop(document_id, "")
    storage_path = Path(str(document.get("storagePath") or ""))
    content_hash: str | None = None
    try:
        storage_path.resolve().relative_to(_PRIVATE_DIR.resolve())
        if not storage_path.is_file():
            raise FileNotFoundError("Stored document file is unavailable.")
        content = await asyncio.to_thread(storage_path.read_bytes)
        content_hash = hashlib.sha256(content).hexdigest()
        filename = str(document.get("fileName") or "document")
        requirement_code = _document_type(
            {"code": document.get("requirementCode")}
        )
        if requirement_code == "photo":
            await _run_automated_document_verification(
                document,
                database,
                ocr_status="NOT_REQUIRED",
                content_hash=content_hash,
            )
            return
        await collection.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "workflowStatus": "OCR_PENDING",
                    "ocrStatus": "OCR_PENDING",
                    "ocrMessage": "Reading document...",
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )
        if requirement_code == "bank_statement" and content.startswith(b"%PDF-"):
            loop = asyncio.get_running_loop()
            async def save_local_progress(done: int, total: int) -> None:
                await collection.update_one(
                    {"_id": document["_id"], "ocrLeaseOwner": lease_id, "status": {"$nin": ["DELETED", "REPLACED"]}},
                    {"$set": {"ocrStatus": "OCR_PROCESSING", "workflowStatus": "OCR_PROCESSING",
                              "ocrPagesProcessed": done, "ocrTotalPages": total,
                              "ocrProgressPercent": round(done*100/total) if total else 0,
                              "ocrMessage": f"Reading ABA page {done} of {total} locally.",
                              "updatedAt": datetime.now(timezone.utc)}},
                )
            def local_progress(done: int, total: int) -> None:
                asyncio.run_coroutine_threadsafe(save_local_progress(done, total), loop).result(timeout=15)
            analysis = await asyncio.to_thread(
                analyze_aba_pdf, content, filename, password=password,
                document_id=document_id, application_id=str(document.get("applicationId") or ""),
                requirement_id=str(document.get("requirementId") or ""),
                progress=local_progress,
            )
            if analysis is not None:
                await _complete_local_aba(document, database, analysis, content_hash, lease_id)
                if password and analysis.get("statement"):
                    render_bytes = await asyncio.to_thread(decrypted_render_bytes, content, password)
                    fresh = await collection.find_one(id_query(document_id))
                    if render_bytes and fresh and fresh.get("status") not in {"DELETED", "REPLACED"} and fresh.get("ocrLeaseOwner") == lease_id and storage_path.is_file():
                        await asyncio.to_thread((_PRIVATE_DIR / f"{document_id}.aba-render.pdf").write_bytes,render_bytes)
                password = ""
                return
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0)
        ) as client:
            start_response = await client.post(
                f"{settings.ai_service_url}/ocr/jobs",
                files={"file": (filename, content)},
                data={"requirement_code": requirement_code},
                headers={"Idempotency-Key": f"{document_id}:{attempt}"},
            )
            start_response.raise_for_status()
            job = _parse_ocr_job(start_response.json(), "job response")
            await collection.update_one(
                {"_id": document["_id"], "ocrLeaseOwner": lease_id, "status": {"$nin": ["DELETED", "REPLACED"]}},
                {
                    "$set": {
                        "ocrJobId": job["id"],
                        "workflowStatus": "OCR_PROCESSING",
                        "ocrStatus": "OCR_PROCESSING",
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )
            deadline = time.monotonic() + settings.ai_service_timeout_seconds
            while True:
                status_response = await client.get(
                    f"{settings.ai_service_url}/ocr/jobs/{quote(job['id'], safe='')}"
                )
                status_response.raise_for_status()
                job = _parse_ocr_job(status_response.json(), "job status")
                pages_processed = max(0, int(job.get("pagesProcessed") or 0))
                total_pages = max(0, int(job.get("totalPages") or 0))
                percent = min(100, max(0, int(job.get("progressPercent") or 0)))
                job_status = str(job.get("status") or "").upper()
                await collection.update_one(
                    {"_id": document["_id"]},
                    {
                        "$set": {
                            "workflowStatus": (
                                "OCR_PROCESSING"
                                if job_status
                                not in {
                                    "OCR_COMPLETED",
                                    "OCR_PARTIAL",
                                    "OCR_FAILED",
                                }
                                else job_status
                            ),
                            "ocrStatus": (
                                "OCR_PROCESSING"
                                if job_status
                                not in {
                                    "OCR_COMPLETED",
                                    "OCR_PARTIAL",
                                    "OCR_FAILED",
                                }
                                else job_status
                            ),
                            "ocrPagesProcessed": pages_processed,
                            "ocrTotalPages": total_pages,
                            "ocrProgressPercent": percent,
                            "ocrMessage": str(
                                job.get("message") or "Reading document..."
                            ),
                            "ocrFailedPages": job.get("failedPages") or [],
                            "updatedAt": datetime.now(timezone.utc),
                        }
                    },
                )
                if job_status == "OCR_FAILED":
                    job_error = job.get("error")
                    error_message = (
                        str(job_error.get("message") or "")
                        if isinstance(job_error, dict)
                        else str(job_error or "")
                    )
                    user_message = (
                        error_message
                        if isinstance(job_error, dict)
                        and job_error.get("code") == "PDF_PAGE_LIMIT_EXCEEDED"
                        else "Document reading failed. Please retry."
                    )
                    await collection.update_one(
                        {"_id": document["_id"]},
                        {
                            "$set": {
                                "workflowStatus": "OCR_FAILED",
                                "ocrStatus": "OCR_FAILED",
                                "status": "FAILED",
                                "ocrError": error_message,
                                "ocrMessage": user_message,
                                "updatedAt": datetime.now(timezone.utc),
                            }
                        },
                    )
                    await _run_automated_document_verification(
                        document,
                        database,
                        ocr_status="OCR_FAILED",
                        content_hash=content_hash,
                    )
                    return
                if job_status in {"OCR_COMPLETED", "OCR_PARTIAL"}:
                    is_partial = job_status == "OCR_PARTIAL"
                    valid, total_pages, result = _valid_ocr_result(
                        job,
                        require_all_pages_success=not is_partial,
                    )
                    if not valid:
                        raise ValueError(
                            "OCR returned incomplete or inconsistent page results."
                        )
                    fields = result.get("fields")
                    bank_statement_check = _bank_statement_field_score(
                        requirement_code,
                        result,
                    )
                    await collection.update_one(
                        {"_id": document["_id"]},
                        {
                            "$set": {
                                "workflowStatus": job_status,
                                "ocrStatus": job_status,
                                "ocrResultStatus": str(
                                    result.get("status") or "manual_review"
                                ),
                                "ocrPagesProcessed": total_pages,
                                "ocrTotalPages": total_pages,
                                "ocrPageNumbers": [
                                    int(page["page"])
                                    for page in result["pages"]
                                ],
                                "ocrProgressPercent": 100,
                                "ocrConfidence": float(result.get("confidence") or 0),
                                "contentHash": content_hash,
                                "ocrMessage": str(
                                    job.get("message")
                                    or (
                                        "Document reading failed. Please retry."
                                        if is_partial
                                        else "OCR completed. Ready for verification."
                                    )
                                ),
                                "ocrError": (
                                    str(job.get("error", {}).get("message") or "")
                                    if isinstance(job.get("error"), dict)
                                    else str(job.get("error") or "")
                                ),
                                "ocrFailedPages": job.get("failedPages") or [],
                                "ocrText": str(result.get("extracted_text") or ""),
                                "extractedFields": fields if isinstance(fields, dict) else {},
                                "ocrFieldEvidence": result.get("field_evidence") or {},
                                "ocrAnalysis": result.get("ai_analysis") or {},
                                "ocrWarnings": result.get("warnings") or [],
                                "detectedDocumentType": str(
                                    result.get("document_type") or ""
                                ),
                                "missingFields": result.get("missing_fields") or [],
                                "bankStatementCheckPercent": (
                                    bank_statement_check["document_verification_score"]
                                    if bank_statement_check
                                    else None
                                ),
                                "bankStatementMissingFields": (
                                    bank_statement_check["missing_fields"]
                                    if bank_statement_check
                                    else []
                                ),
                                "updatedAt": datetime.now(timezone.utc),
                            }
                        },
                    )
                    if is_partial:
                        await _run_automated_document_verification(
                            document,
                            database,
                            ocr_status="OCR_PARTIAL",
                            ocr_confidence=float(result.get("confidence") or 0),
                            extracted_fields=(
                                fields if isinstance(fields, dict) else {}
                            ),
                            content_hash=content_hash,
                            detected_document_type=str(
                                result.get("document_type") or ""
                            )
                            or None,
                            ocr_text=str(result.get("extracted_text") or ""),
                        )
                        return
                    await _persist_initial_bank_statement_analysis(
                        {
                            **document,
                            "requirementCode": requirement_code,
                        },
                        database,
                    )
                    await _run_automated_document_verification(
                        document,
                        database,
                        ocr_status="OCR_COMPLETED",
                        ocr_confidence=float(result.get("confidence") or 0),
                        extracted_fields=(
                            fields if isinstance(fields, dict) else {}
                        ),
                        content_hash=content_hash,
                        detected_document_type=str(
                            result.get("document_type") or ""
                        )
                        or None,
                        ocr_text=str(result.get("extracted_text") or ""),
                    )
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("OCR processing exceeded the configured time limit.")
                await asyncio.sleep(settings.ocr_job_poll_interval_seconds)
    except PyMongoError as error:
        logger.exception(
            "Could not update document OCR status: document_id=%s",
            document_id,
        )
    except Exception as error:
        logger.exception("Document OCR failed: document_id=%s", document_id)
        try:
            failed = await collection.update_one(
                {"_id": document["_id"], "ocrLeaseOwner": lease_id, "status": {"$nin": ["DELETED", "REPLACED"]}},
                {
                    "$set": {
                        "workflowStatus": "OCR_FAILED",
                        "ocrStatus": "OCR_FAILED",
                        "status": "FAILED",
                        "ocrMessage": "Document reading failed. Please retry.",
                        "ocrError": str(error),
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )
            if failed.matched_count:
                await _run_automated_document_verification(
                    document,
                    database,
                    ocr_status="OCR_FAILED",
                    content_hash=content_hash,
                )
        except PyMongoError:
            logger.exception(
                "Could not save OCR or automated verification failure: document_id=%s",
                document_id,
            )
    finally:
        try:
            await collection.update_one(
                {"_id": document["_id"], "ocrLeaseOwner": lease_id},
                {
                    "$unset": {
                        "ocrLeaseOwner": "",
                        "ocrLeaseExpiresAt": "",
                    }
                },
            )
        except PyMongoError:
            logger.exception(
                "Could not release OCR lease: document_id=%s",
                document_id,
            )


@router.get("/")
@router.get("")
async def list_documents(
    application_id: str | None = Query(default=None, alias="applicationId"),
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    query = {"applicationId": application_id} if application_id else {}
    if principal["role"] == "CUSTOMER":
        query["customerId"] = principal["id"]
    try:
        documents = await database["documents"].find(query).to_list(length=None)
    except PyMongoError as error:
        logger.exception("Could not load documents: %s", error.__class__.__name__)
        raise APIError(503, "Documents are unavailable") from error
    documents.sort(key=lambda item: item.get("createdAt") or item.get("uploadDate") or "", reverse=True)
    return JSONResponse(
        content={
            "documents": [
                public_document(public_workflow_document(item))
                for item in documents
            ]
        }
    )


@router.get("/application/{application_id}")
async def list_application_documents(
    application_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return await list_documents(
        application_id=application_id,
        database=database,
        principal=principal,
    )


@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    application_id: str = Form(alias="applicationId"),
    requirement_id: str = Form(default="", alias="requirementId"),
    legacy_requirement_id: str = Form(default="", alias="requirement"),
    replace_document_id: str | None = Form(default=None, alias="replaceDocumentId"),
    pdf_password: str = Form(default="", alias="pdfPassword"),
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    requirement_id = requirement_id or legacy_requirement_id
    if len(pdf_password) > 1024:
        raise APIError(400, "PDF password must contain at most 1024 characters")
    if not requirement_id:
        return JSONResponse(status_code=400, content={"message": "Requirement ID is required"})
    if not file.filename:
        return JSONResponse(status_code=400, content={"message": "A document file is required"})
    safe_filename = Path(file.filename).name[:255]
    extension = Path(safe_filename).suffix.lower()
    signatures = _SIGNATURES.get(extension)
    if not signatures:
        return JSONResponse(status_code=400, content={"message": "Only PDF, JPG, JPEG, and PNG files are supported"})

    try:
        application = await database["visaapplications"].find_one(id_query(application_id))
        if not application:
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        owner_id = str(application.get("customer") or application.get("customerId") or "")
        if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        current_requirements = await database["visarequirements"].find(
            {"visaTypeId": application.get("visaTypeId"), "status": "ACTIVE"}
        ).to_list(length=None)
        application_snapshot = application_requirements(
            application,
            current_requirements or application.get("requirementSnapshot") or [],
        )
        requirement = next(
            (
                item
                for item in application_snapshot
                if str(item.get("requirementId") or item.get("_id") or "")
                == requirement_id
            ),
            None,
        )
        if not requirement:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        if str(requirement.get("visaTypeId")) != str(application.get("visaTypeId")):
            snapshot_ids = {
                str(item.get("requirementId"))
                for item in application.get("requirementSnapshot", [])
            }
            if requirement_id not in snapshot_ids:
                return JSONResponse(status_code=400, content={"message": "Requirement does not belong to this application"})
    except PyMongoError as error:
        logger.exception("Could not verify document upload: %s", error.__class__.__name__)
        raise APIError(503, "Document upload could not be verified") from error

    detected_type = _document_type(requirement)
    max_size = (
        BANK_STATEMENT_MAX_FILE_SIZE
        if detected_type == "bank_statement"
        else min(
            int(requirement.get("maxFileSize") or settings.max_file_size),
            settings.max_file_size,
        )
    )
    accepted = [str(item).lower().lstrip(".") for item in requirement.get("acceptedFormats", [])]
    if accepted and extension.lstrip(".") not in accepted:
        return JSONResponse(status_code=400, content={"message": "File format is not accepted for this requirement"})
    content = await file.read(max_size + 1)
    await file.close()
    if not content:
        return JSONResponse(status_code=400, content={"message": "The uploaded file is empty"})
    if len(content) > max_size:
        return JSONResponse(status_code=413, content={"message": "File exceeds the requirement size limit"})
    if not any(content.startswith(signature) for signature in signatures):
        return JSONResponse(status_code=400, content={"message": "File content does not match its extension"})

    try:
        duplicate = await database["documents"].find_one(
            {
                "applicationId": application_id,
                "requirementId": requirement_id,
                "status": {"$ne": "DELETED"},
            }
        )
        if duplicate and not requirement.get("allowMultiple", False) and not replace_document_id:
            return JSONResponse(status_code=409, content={"message": "A document already exists for this requirement"})
        previous = (
            await database["documents"].find_one(id_query(replace_document_id))
            if replace_document_id
            else None
        )
        if replace_document_id and (
            not previous
            or previous.get("applicationId") != application_id
            or previous.get("requirementId") != requirement_id
        ):
            return JSONResponse(status_code=404, content={"message": "Document to replace was not found"})

        document_id = str(uuid4())
        storage_path = _PRIVATE_DIR / f"{document_id}{extension}"
        _PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
        now = datetime.now(timezone.utc)
        document = {
            "_id": document_id,
            "documentReference": f"DOC-{document_id.replace('-', '')[:12].upper()}",
            "applicationId": application_id,
            "customerId": str(application.get("customer") or application.get("customerId") or ""),
            "visaTypeId": str(application.get("visaTypeId") or ""),
            "requirementId": requirement_id,
            "requirementMaximumPoints": requirement.get(
                "maximumEarnedPoints",
                requirement.get("weight", requirement.get("points", 0)),
            ),
            "requirementCode": detected_type,
            "fileName": safe_filename,
            "mimeType": {
                ".pdf": "application/pdf",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
            }[extension],
            "size": len(content),
            "storagePath": str(storage_path),
            "status": "PROCESSING",
            "workflowStatus": "OCR_PENDING",
            "ocrStatus": "OCR_PENDING",
            "ocrPagesProcessed": 0,
            "ocrTotalPages": 0,
            "ocrPageNumbers": [],
            "ocrFailedPages": [],
            "ocrProgressPercent": 0,
            "ocrAttempt": 1,
            "ocrJobId": None,
            "ocrError": "",
            "ocrMessage": "Reading document...",
            "ocrResultStatus": "",
            "rejectionReason": "",
            "ocrConfidence": 0,
            "extractedFields": {},
            "missingFields": [],
            "bankStatementCheckPercent": None,
            "bankStatementMissingFields": [],
            "uploadedBy": principal["id"],
            "uploadDate": now,
            "createdAt": now,
            "updatedAt": now,
            "verificationHistory": [],
        }
        await database["documents"].insert_one(document)
        if previous:
            await database["documents"].update_one(
                {"_id": previous["_id"]},
                {"$set": {"status": "REPLACED", "updatedAt": now}},
            )
        updated_application = dict(application)
        updated_application["verificationMetricsCurrent"] = False
        updated_application["updatedAt"] = now
        await database["visaapplications"].replace_one(
            {"_id": application["_id"]}, updated_application
        )
        await _refresh_application_score(database, updated_application)
    except PyMongoError as error:
        if "storage_path" in locals():
            storage_path.unlink(missing_ok=True)
        logger.exception("Could not save uploaded document: %s", error.__class__.__name__)
        raise APIError(503, "Document could not be saved") from error

    if detected_type == "photo":
        try:
            await _run_automated_document_verification(
                document,
                database,
                ocr_status="NOT_REQUIRED",
                content_hash=hashlib.sha256(content).hexdigest(),
            )
            document = (
                await database["documents"].find_one(id_query(document_id))
                or document
            )
        except PyMongoError as error:
            logger.exception(
                "Could not validate uploaded applicant photo: document_id=%s",
                document_id,
            )
            raise APIError(503, "Applicant photo validation could not be saved") from error

    if isinstance(pdf_password,str) and pdf_password:
        _PDF_PASSWORDS[document_id] = pdf_password
    pdf_password = ""
    if detected_type != "photo" and not _schedule_document_ocr(document, database, 1):
        _PDF_PASSWORDS.pop(document_id, None)
        logger.error("Duplicate OCR task prevented for document_id=%s", document_id)
        document.update(
            {
                "workflowStatus": "OCR_FAILED",
                "ocrStatus": "OCR_FAILED",
                "status": "FAILED",
                "ocrMessage": "Document reading failed. Please retry.",
            }
        )
        await database["documents"].update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "workflowStatus": "OCR_FAILED",
                    "ocrStatus": "OCR_FAILED",
                    "status": "FAILED",
                    "ocrMessage": "Document reading failed. Please retry.",
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )
    return JSONResponse(
        status_code=201,
        content={
            "document": public_document(public_workflow_document(document)),
            "ocr": {
                "status": document["ocrStatus"],
                "confidence": 0,
                "fields": {},
                "missing_fields": [],
                "pages_processed": 0,
                "total_pages": 0,
                "progress_percent": 0,
            },
        },
    )


@router.get("/{document_id}/processing")
async def get_document_processing_status(
    document_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    try:
        document = await database["documents"].find_one(id_query(document_id))
    except PyMongoError as error:
        raise APIError(503, "Document processing status is unavailable") from error
    if not document or (
        principal["role"] == "CUSTOMER"
        and str(document.get("customerId") or "") != principal["id"]
    ):
        return JSONResponse(status_code=404, content={"message": "Document not found"})
    public = public_document(public_workflow_document(document))
    return JSONResponse(
        content={
            "documentId": str(document["_id"]),
            "workflowStatus": public["workflowStatus"],
            "ocrStatus": public["ocrStatus"],
            "status": str(document.get("status") or "PENDING").upper(),
            "pagesProcessed": public["ocrPagesProcessed"],
            "totalPages": public["ocrTotalPages"],
            "progressPercent": public["ocrProgressPercent"],
            "message": str(document.get("ocrMessage") or ""),
            "error": str(document.get("ocrError") or ""),
            "failedPages": document.get("ocrFailedPages") or [],
            "bankStatementCheckPercent": document.get(
                "bankStatementCheckPercent"
            ),
            "bankStatementMissingFields": document.get(
                "bankStatementMissingFields"
            ) or [],
            "bankStatementAnalysis": public.get("bankStatementAnalysis"),
            "abaStatementAnalysis": public.get("abaStatementAnalysis"),
            "automatedVerification": public.get("automatedVerification"),
        }
    )


@router.post("/{document_id}/ocr/retry")
async def retry_document_ocr(
    document_id: str,
    payload: dict[str, Any] | None = None,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    collection = database["documents"]
    try:
        document = await collection.find_one(id_query(document_id))
    except PyMongoError as error:
        raise APIError(503, "Document could not be retried") from error
    if not document or (
        principal["role"] == "CUSTOMER"
        and str(document.get("customerId") or "") != principal["id"]
    ):
        return JSONResponse(status_code=404, content={"message": "Document not found"})
    if await _is_photo_document(database, document):
        await _run_automated_document_verification(
            document,
            database,
            ocr_status="NOT_REQUIRED",
            content_hash=document.get("contentHash"),
        )
        refreshed = await collection.find_one(id_query(document_id))
        return JSONResponse(
            content={
                "document": public_document(
                    public_workflow_document(refreshed or document)
                ),
                "message": "Photo checks completed; OCR is not required.",
            }
        )
    password = (payload or {}).get("pdfPassword")
    if password is not None and (not isinstance(password, str) or len(password) > 1024):
        raise APIError(400, "PDF password must be a string of at most 1024 characters")
    retryable_status = document_workflow_status(document)
    if retryable_status not in {
        "OCR_FAILED",
        "OCR_PARTIAL",
        "FAILED",
        "UPLOADED",
    }:
        return JSONResponse(
            status_code=409,
            content={
                "message": "Only unread or incomplete documents can be sent for OCR"
            },
        )
    if int(document.get("ocrAttempt") or 0) >= _MAX_OCR_ATTEMPTS:
        return JSONResponse(
            status_code=409,
            content={
                "message": (
                    "The document reached the OCR retry limit. "
                    "An administrator must review the processing error."
                )
            },
        )

    active_task = _OCR_TASKS.get(str(document["_id"]))
    if active_task and not active_task.done():
        await asyncio.gather(active_task, return_exceptions=True)
    attempt = int(document.get("ocrAttempt") or 0)
    next_attempt = attempt + 1
    now = datetime.now(timezone.utc)
    try:
        update_result = await collection.update_one(
            {
                "_id": document["_id"],
                "workflowStatus": document.get("workflowStatus"),
                "ocrAttempt": document.get("ocrAttempt"),
            },
            {
                "$set": {
                    "workflowStatus": "OCR_PENDING",
                    "ocrStatus": "OCR_PENDING",
                    "status": "PROCESSING",
                    "ocrPagesProcessed": 0,
                    "ocrTotalPages": 0,
                    "ocrPageNumbers": [],
                    "ocrProgressPercent": 0,
                    "ocrAttempt": next_attempt,
                    "ocrJobId": None,
                    "ocrError": "",
                    "ocrFailedPages": [],
                    "ocrMessage": "Reading document...",
                    "ocrText": "",
                    "extractedFields": {},
                    "detectedDocumentType": "",
                    "bankStatementCheckPercent": None,
                    "bankStatementMissingFields": [],
                    "bankStatementAnalysis": None,
                    "abaStatementAnalysis": None,
                    "automatedVerification": None,
                    "automatedVerificationStatus": "PROCESSING",
                    "rejectionReason": "",
                    "awardedPoints": 0,
                    "updatedAt": now,
                }
            },
        )
        if update_result.matched_count:
            await database["visaapplications"].update_one(
                id_query(str(document.get("applicationId") or "")),
                {"$set": {"verificationMetricsCurrent": False, "predictionInputsCurrent": False, "predictionStatus": "STALE"}},
            )
    except PyMongoError as error:
        logger.exception("Could not retry OCR: document_id=%s", document_id)
        raise APIError(503, "Document could not be retried") from error
    if not update_result.matched_count:
        return JSONResponse(
            status_code=409,
            content={"message": "Document reading has already been retried"},
        )
    retried_document = {
        **document,
        "workflowStatus": "OCR_PENDING",
        "ocrStatus": "OCR_PENDING",
        "status": "PROCESSING",
        "ocrPagesProcessed": 0,
        "ocrTotalPages": 0,
        "ocrPageNumbers": [],
        "ocrProgressPercent": 0,
        "ocrError": "",
        "ocrFailedPages": [],
        "ocrText": "",
        "extractedFields": {},
        "detectedDocumentType": "",
        "bankStatementCheckPercent": None,
        "bankStatementMissingFields": [],
        "bankStatementAnalysis": None,
                    "abaStatementAnalysis": None,
        "automatedVerification": None,
        "automatedVerificationStatus": "PROCESSING",
        "rejectionReason": "",
        "awardedPoints": 0,
        "ocrAttempt": next_attempt,
    }
    if password is not None:
        if password:
            _PDF_PASSWORDS[document_id] = password
    if not _schedule_document_ocr(retried_document, database, next_attempt):
        _PDF_PASSWORDS.pop(document_id, None)
        return JSONResponse(
            status_code=409,
            content={"message": "Document reading is already in progress"},
        )
    return JSONResponse(
        content={
            "document": public_document(
                public_workflow_document(retried_document)
            ),
            "message": "Reading document...",
        }
    )


@router.post("/{document_id}/verification/retry")
async def retry_document_verification(
    document_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    try:
        document = await database["documents"].find_one(id_query(document_id))
        if not document:
            return JSONResponse(
                status_code=404, content={"message": "Document not found"}
            )
        is_photo_document = _document_type({"code": document.get("requirementCode")}) == "photo"
        if not has_completed_ocr(document) and not is_photo_document:
            return JSONResponse(
                status_code=409,
                content={
                    "message": "Document verification requires complete OCR evidence."
                },
            )
        result = await _run_automated_document_verification(
            document,
            database,
            ocr_status=(
                str(document.get("ocrStatus") or "OCR_FAILED")
                if is_photo_document
                else "OCR_COMPLETED"
            ),
            ocr_confidence=float(document.get("ocrConfidence") or 0),
            extracted_fields=document.get("extractedFields")
            if isinstance(document.get("extractedFields"), dict)
            else {},
            content_hash=str(document.get("contentHash") or "") or None,
            detected_document_type=str(
                document.get("detectedDocumentType") or ""
            )
            or None,
        )
    except PyMongoError as error:
        logger.exception("Could not rerun document verification: %s", document_id)
        raise APIError(503, "Document verification could not be rerun") from error
    fields = document.get("extractedFields")
    account_number = (
        fields.get("accountNumber") or fields.get("accountNo")
        if isinstance(fields, dict)
        else None
    )
    return JSONResponse(
        content={
            "documentId": document_id,
            "automatedVerification": redact_sensitive_data(
                result,
                {str(account_number)} if account_number else set(),
            ),
        }
    )


@router.post("/ocr")
async def ocr_document(
    file: UploadFile = File(...),
    requirement_code: str = Form(default="unknown", alias="requirement_code"),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    filename = Path(file.filename or "document").name
    extension = Path(filename).suffix.lower()
    signatures = _SIGNATURES.get(extension)
    if not signatures:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "Only PDF, JPG, JPEG, and PNG files are supported"},
        )
    normalized_requirement_code = requirement_code.strip().lower() or "unknown"
    max_file_size = (
        BANK_STATEMENT_MAX_FILE_SIZE
        if normalized_requirement_code == "bank_statement"
        else settings.max_file_size
    )
    content = await file.read(max_file_size + 1)
    await file.close()
    if not content:
        return JSONResponse(status_code=400, content={"message": "The uploaded file is empty"})
    if len(content) > max_file_size:
        return JSONResponse(status_code=413, content={"message": "Uploaded file exceeds the configured maximum size"})
    if not any(content.startswith(signature) for signature in signatures):
        return JSONResponse(status_code=400, content={"message": "File content does not match its extension"})
    result = await _run_ocr(
        content,
        filename,
        normalized_requirement_code,
    )
    if result is None:
        return JSONResponse(status_code=503, content={"message": "OCR service is unavailable"})
    fields = result.get("fields")
    account_number = (
        fields.get("accountNumber") or fields.get("accountNo")
        if isinstance(fields, dict)
        else None
    )
    return JSONResponse(
        content=redact_sensitive_data(
            result,
            {str(account_number)} if account_number else set(),
        )
    )


@router.get("/{document_id}/content")
async def get_document_content(
    document_id: str,
    download: bool = Query(default=False),
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> Response:
    try:
        document = await database["documents"].find_one(id_query(document_id))
    except PyMongoError as error:
        raise APIError(503, "Document is unavailable") from error
    if not document:
        return JSONResponse(status_code=404, content={"message": "Document not found"})
    if (
        _principal["role"] == "CUSTOMER"
        and str(document.get("customerId") or "") != _principal["id"]
    ):
        return JSONResponse(status_code=404, content={"message": "Document not found"})
    raw_path = document.get("storagePath")
    if not raw_path:
        return JSONResponse(status_code=404, content={"message": "Stored document file is unavailable"})
    path = Path(raw_path).resolve()
    try:
        path.relative_to(_PRIVATE_DIR.resolve())
    except ValueError:
        logger.error("Rejected document path outside private storage for document id=%s", document_id)
        return JSONResponse(status_code=404, content={"message": "Document file is unavailable"})
    if not path.is_file():
        return JSONResponse(status_code=404, content={"message": "Document file is unavailable"})
    return FileResponse(
        path,
        media_type=document.get("mimeType") or "application/octet-stream",
        filename=document.get("fileName") if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    try:
        collection = database["documents"]
        document = await collection.find_one(id_query(document_id))
        if not document:
            return JSONResponse(status_code=404, content={"message": "Document not found"})
        if (
            _principal["role"] == "CUSTOMER"
            and str(document.get("customerId") or "") != _principal["id"]
        ):
            return JSONResponse(status_code=404, content={"message": "Document not found"})
        await collection.update_one(
            {"_id": document["_id"]},
            {"$set": {"status": "DELETED", "updatedAt": datetime.now(timezone.utc)}},
        )
        await database["visaapplications"].update_one(
            id_query(str(document.get("applicationId") or "")),
            {"$set": {"verificationMetricsCurrent": False, "predictionInputsCurrent": False, "predictionStatus": "STALE"}},
        )
        application = await database["visaapplications"].find_one(
            id_query(str(document.get("applicationId") or ""))
        )
        if application:
            await _refresh_cross_document_consistency(
                database, str(application["_id"])
            )
        storage_path = document.get("storagePath")
        if storage_path:
            path = Path(storage_path).resolve()
            try:
                path.relative_to(_PRIVATE_DIR.resolve())
                path.unlink(missing_ok=True)
                (_PRIVATE_DIR / f"{document_id}.aba-render.pdf").unlink(missing_ok=True)
                _PDF_PASSWORDS.pop(document_id,None)
            except ValueError:
                logger.error("Rejected deletion path outside private storage for document id=%s", document_id)
    except PyMongoError as error:
        logger.exception("Could not delete document: %s", error.__class__.__name__)
        raise APIError(503, "Document could not be removed") from error
    return JSONResponse(content={"message": "Document deleted"})


@router.patch("/{document_id}/review")
async def review_document(
    document_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    status = str(payload.get("status") or "").strip().upper()
    reason = str(payload.get("reason") or payload.get("rejectionReason") or "").strip()
    allowed_statuses = {
        "VERIFIED",
        "REJECTED",
        "NEEDS_REVIEW",
        "INSUFFICIENT_DATA",
    }
    if status not in allowed_statuses:
        return JSONResponse(
            status_code=400,
            content={
                "message": (
                    "Review status must be VERIFIED, REJECTED, NEEDS_REVIEW, "
                    "or INSUFFICIENT_DATA."
                )
            },
        )
    if len(reason) < 8:
        return JSONResponse(
            status_code=400,
            content={"message": "A review reason of at least 8 characters is required."},
        )
    try:
        document = await database["documents"].find_one(id_query(document_id))
        if not document:
            return JSONResponse(status_code=404, content={"message": "Document not found"})
        if (
            not has_completed_ocr(document)
            and _document_type({"code": document.get("requirementCode")}) != "photo"
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "message": (
                        "A manual override requires complete OCR evidence. "
                        "Retry OCR or review the processing error first."
                    )
                },
            )
        application = await database["visaapplications"].find_one(
            id_query(str(document.get("applicationId") or ""))
        )
        requirement_id = str(document.get("requirementId") or "")
        requirement = next(
            (
                item
                for item in (application or {}).get("requirementSnapshot") or []
                if str(item.get("requirementId") or item.get("_id") or "")
                == requirement_id
            ),
            {},
        )
        if not requirement and requirement_id:
            requirement = await database["visarequirements"].find_one(
                id_query(requirement_id)
            ) or {}
        try:
            awarded_points = max(
                0.0,
                float(
                    requirement.get(
                        "maximumEarnedPoints",
                        requirement.get("weight", requirement.get("points", 0)),
                    )
                    or 0
                ),
            )
        except (TypeError, ValueError):
            awarded_points = 0.0
        aba_review = None
        if document.get("abaStatementAnalysis"):
            try:
                aba_review = reviewed_analysis(document["abaStatementAnalysis"], {**payload,"status":status,"reason":reason},principal["id"])
            except ValueError as error:
                return JSONResponse(status_code=409,content={"message":str(error)})
        now = datetime.now(timezone.utc)
        review_event = {
            "status": status,
            "reason": reason,
            "actorId": principal["id"],
            "actorRole": principal["role"],
            "reviewedAt": now.isoformat(),
            "previousStatus": str(document.get("status") or "PROCESSING").upper(),
        }
        bank_statement_analysis = document.get("bankStatementAnalysis")
        if isinstance(bank_statement_analysis, dict):
            reviewed_document = {
                **document,
                "status": status,
                "reviewedBy": principal["id"],
                "manualReviewHistory": [
                    *(document.get("manualReviewHistory") or []),
                    review_event,
                ],
                **({"abaStatementAnalysis": aba_review} if aba_review is not None else {}),
            }
            bank_statement_analysis = {
                **bank_statement_analysis,
                **calculate_bank_score_assessment(
                    reviewed_document,
                    bank_statement_analysis,
                    requirement_maximum=awarded_points,
                ),
            }
        awarded_points = (
            bank_requirement_points(bank_statement_analysis, awarded_points)
            if _is_bank_requirement(requirement, document) and status == "VERIFIED"
            else awarded_points if status == "VERIFIED" else 0
        )
        review_query = {"_id": document["_id"], "status": {"$nin": ["DELETED", "REPLACED"]}}
        if aba_review is not None:
            review_query["abaStatementAnalysis.analysisRevision"] = document["abaStatementAnalysis"]["analysisRevision"]
        update_result = await database["documents"].update_one(
            review_query,
            {
                "$set": {
                    "status": status,
                    "workflowStatus": status,
                    "reviewedBy": principal["id"],
                    "reviewedAt": now,
                    "reviewReason": reason,
                    "awardedPoints": awarded_points,
                    **(
                        {"bankStatementAnalysis": bank_statement_analysis}
                        if isinstance(bank_statement_analysis, dict)
                        else {}
                    ),
                    **({"abaStatementAnalysis":aba_review} if aba_review is not None else {}),
                    "updatedAt": now,
                },
                "$push": {"manualReviewHistory": review_event},
            },
        )
        if not update_result.matched_count:
            return JSONResponse(
                status_code=409,
                content={"message": "Document status changed; reload before reviewing."},
            )
        if application:
            await _refresh_cross_document_consistency(
                database, str(application["_id"])
            )
        document = {
            **document,
            "status": status,
            "workflowStatus": status,
            "reviewedBy": principal["id"],
            "reviewedAt": now,
            "reviewReason": reason,
            **({"abaStatementAnalysis":aba_review} if aba_review is not None else {}),
            "manualReviewHistory": [
                *(document.get("manualReviewHistory") or []),
                review_event,
            ],
        }
    except PyMongoError as error:
        logger.exception(
            "Could not load document for review: %s", error.__class__.__name__
        )
        raise APIError(503, "Document status is unavailable") from error
    return JSONResponse(
        content={
            "document": public_document(public_workflow_document(document)),
            "manualReview": review_event,
        }
    )
