from __future__ import annotations

import json
import asyncio
import hashlib
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.config import BANK_STATEMENT_MAX_FILE_SIZE, PROJECT_ROOT, settings
from ..core.errors import APIError
from ..services.business_rules import (
    DEFAULT_RULE_POLICY,
    evaluate_business_rules,
    validate_rule_policy,
)
from ..services.financial_analysis import calculate_financial_analysis
from ..services.aba_integration import (analyze_aba_pdf, aba_to_legacy_ocr, apply_correction, reviewed_analysis, decrypted_render_bytes)
from ..services.bank_statement_scoring import (
    SOURCE_UNVERIFIED_PENALTY_POINTS,
    _transaction_identity,
    analyze_bank_statement as score_bank_statement,
    calculate_document_verification_score,
    normalize_bank_fields,
)
from ..services.readiness_scoring import calculate_bank_score_assessment
from .dependencies import get_mongo_database, require_roles
from .mongo_helpers import id_query

logger = logging.getLogger(__name__)
router = APIRouter()

_PRIVATE_DIR = PROJECT_ROOT / "backend" / "storage" / "private"
_SOURCE_REVIEW_CATEGORIES = {
    "SALARY": "Salary / ប្រាក់ខែ",
    "BUSINESS_INCOME": "Business Income / ចំណូលអាជីវកម្ម",
    "FAMILY_SUPPORT": "Family Support / គ្រួសារជួយ",
    "PERSONAL_SAVINGS": "Personal Savings / ប្រាក់សន្សំ",
    "OTHER": "Other / ផ្សេងៗ",
    "UNKNOWN": "Unknown / មិនដឹងប្រភពច្បាស់លាស់",
}
_SIGNATURES = {
    ".pdf": b"%PDF-",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
}
_LOW_CONFIDENCE_THRESHOLD = 0.65
_DISCLAIMER = (
    "OCR and field-completeness results are for document review only. "
    "They do not predict visa approval or verify document authenticity."
)


def _financial_features(fields: dict[str, Any], transactions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "currency": fields.get("currency"),
        "opening_balance": fields.get("openingBalance"),
        "closing_balance": fields.get("closingBalance"),
        "deposits": fields.get("deposits"),
        "withdrawals": fields.get("withdrawals"),
        "average_balance": fields.get("averageBalance"),
        "transaction_count": len(transactions),
    }


def _case_context(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "destination_country",
        "nationality",
        "citizenship",
        "has_overstay_history",
        "passport_expiry_date",
        "passportExpiryDate",
        "previous_visa_denials",
        "employment_letter",
        "business_evidence",
        "ties_to_cambodia",
        "fixed_deposit",
        "sponsor_support",
        "travel_history_count",
        "age",
        "gender",
        "first_time_traveller",
        "marital_status",
    }
    context = {key: value[key] for key in allowed if key in value}
    documents = value.get("documents")
    if isinstance(documents, list):
        context["documents"] = [
            {
                key: item[key]
                for key in ("type", "code", "status")
                if key in item and isinstance(item[key], (str, int, bool))
            }
            for item in documents[:100]
            if isinstance(item, dict)
        ]
    return context


def _mask_account_number(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    if len(normalized) <= 4:
        return "****"
    return f"{'*' * min(8, len(normalized) - 4)}{normalized[-4:]}"


def _account_number_from(record: dict[str, Any]) -> Any:
    extracted = record.get("result", {}).get("extracted", {})
    if not isinstance(extracted, dict):
        return None
    return extracted.get("accountNumber") or extracted.get("accountNo")


def _public_analysis(value: Any, account_numbers: set[str] | None = None) -> Any:
    known_numbers = account_numbers or set()
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized_key in {"accountnumber", "accountno"} and item:
                result[key] = _mask_account_number(item)
            else:
                result[key] = _public_analysis(item, known_numbers)
        return result
    if isinstance(value, list):
        return [_public_analysis(item, known_numbers) for item in value]
    if isinstance(value, str):
        sanitized = value
        for account_number in known_numbers:
            compact = re.sub(r"[^A-Za-z0-9]", "", account_number)
            if len(compact) < 5:
                continue
            variants = {account_number, compact}
            variants.add(" ".join(compact))
            for variant in variants:
                if variant:
                    sanitized = re.sub(
                        re.escape(variant),
                        _mask_account_number(compact),
                        sanitized,
                        flags=re.IGNORECASE,
                    )
        return re.sub(
            r"(?im)(account\s*(?:number|no\.?|#)\s*[:#-]?\s*)([A-Z0-9][A-Z0-9 -]{3,})",
            lambda match: match.group(1) + _mask_account_number(match.group(2)),
            sanitized,
        )
    return value


def _build_result(
    ocr: dict[str, Any],
    applicant_context: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    applicant_name: str = "",
    document_id: str | None = None,
) -> dict[str, Any]:
    fields = normalize_bank_fields(ocr.get("fields"))
    transactions = fields.get("transactionHistory")
    if not isinstance(transactions, list):
        transactions = []
    metrics = ocr.get("metrics") if isinstance(ocr.get("metrics"), dict) else {}
    failed_pages = ocr.get("failed_pages") or []
    ocr_incomplete = (
        str(ocr.get("status") or "").lower() == "partial"
        or str(ocr.get("status") or "").lower() == "failed"
        or bool(failed_pages)
        or metrics.get("status") == "OCR_PARTIAL"
    )
    if ocr_incomplete:
        required_fields = calculate_document_verification_score({})["missing_fields"]
        present_fields = [
            name for name in required_fields if fields.get(name) not in (None, "", [])
        ]
        missing_fields = [
            name for name in required_fields if name not in present_fields
        ]
        verification = {
            "document_verification_score": None,
            "score_type": "document_field_completeness",
            "required_fields": len(required_fields),
            "extracted_fields": len(present_fields),
            "missing_fields": missing_fields,
            "basis": (
                "Unavailable because OCR did not successfully process every page."
            ),
            "score_available": False,
        }
    else:
        verification = calculate_document_verification_score(fields)
    confidence = ocr.get("confidence")
    try:
        numeric_confidence = float(confidence)
    except (TypeError, ValueError):
        numeric_confidence = None
    if numeric_confidence is not None and not math.isfinite(numeric_confidence):
        numeric_confidence = None
    reported_confidence = None if metrics.get("source") == "ABA_NATIVE_LOCAL" else numeric_confidence
    warnings = list(ocr.get("warnings") or [])
    if ocr_incomplete:
        failed_page_numbers = [
            str(item.get("page"))
            for item in failed_pages
            if isinstance(item, dict) and item.get("page") is not None
        ]
        warnings.append(
            "OCR is incomplete; no final bank-statement verification score was generated."
            + (
                f" Failed page(s): {', '.join(failed_page_numbers)}."
                if failed_page_numbers
                else ""
            )
        )
    if metrics.get("source") == "ABA_NATIVE_LOCAL":
        warnings.append("No numeric OCR confidence was assigned to native/local ABA extraction; inspect its source and reconciliation checks.")
    elif numeric_confidence is not None and numeric_confidence < _LOW_CONFIDENCE_THRESHOLD:
        warnings.append("OCR confidence is low; verify extracted values against the original statement.")

    financial_assessment = {
        "status": (
            "manual_review"
            if ocr_incomplete
            or verification["missing_fields"]
            or (
                numeric_confidence is not None
                and numeric_confidence < _LOW_CONFIDENCE_THRESHOLD
            )
            else "extracted"
        ),
        "missing_fields": verification["missing_fields"],
        "warnings": warnings,
        "authenticity_verified": False,
    }
    ml_assessment = {
        "status": "unavailable",
        "reason": "No validated visa outcome model is configured.",
    }
    extracted = {
        **fields,
        "transactionHistory": transactions,
        "rawText": str(ocr.get("extracted_text") or ""),
        "confidence": reported_confidence,
        "pages": ocr.get("pages") or [],
        "metrics": metrics,
        "failed_pages": failed_pages,
    }
    score = {
        **verification,
        "ocr_confidence": reported_confidence,
    }
    if ocr_incomplete:
        score["status"] = "OCR_PARTIAL"
    active_policy = validate_rule_policy(policy or DEFAULT_RULE_POLICY)
    financial_analysis = calculate_financial_analysis(
        fields,
        transactions,
        active_policy["financial_scoring"],
    )
    if ocr_incomplete:
        financial_analysis = {
            **financial_analysis,
            "financial_score": None,
            "status": "manual_review",
            "human_review_required": True,
        }
    business_rules = evaluate_business_rules(
        applicant_context or {},
        financial_analysis,
        active_policy,
    )
    bank_score_config = dict(active_policy["bank_statement_scoring"])
    bank_statement_analysis = score_bank_statement(
        fields,
        raw_text=str(ocr.get("extracted_text") or ""),
        applicant_name=applicant_name or str(
            (applicant_context or {}).get("full_name")
            or (applicant_context or {}).get("fullName")
            or ""
        ),
        config=bank_score_config,
        financial_config=active_policy["financial_scoring"],
        ocr_complete=not ocr_incomplete,
        document_id=document_id,
    )
    normalized_transactions = bank_statement_analysis.get("transactions", [])
    ocr_page_count = metrics.get("page_count") or len(ocr.get("pages") or [])
    bank_statement_analysis.update(
        {
            "documentInformation": {
                "fileName": ocr.get("filename"),
                "ocrPageCount": ocr_page_count,
                "transactionPageCount": None,
            },
            "ocrProcessingSummary": {
                "status": "OCR_PARTIAL" if ocr_incomplete else "OCR_COMPLETED",
                "pagesProcessed": metrics.get("pages_processed") or len(ocr.get("pages") or []),
                "totalPages": ocr_page_count,
                "failedPages": failed_pages,
                "confidence": reported_confidence,
                "metrics": metrics,
            },
            "accountInformation": {
                "bankName": fields.get("bankName") or fields.get("bank_name"),
                "accountHolderName": fields.get("accountHolderName") or fields.get("account_holder_name"),
                "accountNumber": fields.get("accountNumber") or fields.get("account_number") or fields.get("accountNo"),
                "currency": fields.get("currency") or fields.get("currency_code"),
            },
            "documentVerificationScore": verification,
        }
    )
    bank_statement_analysis.update(
        calculate_bank_score_assessment(
            {
                "status": "NEEDS_REVIEW",
                "extractedFields": fields,
                "ocrConfidence": reported_confidence,
            },
            bank_statement_analysis,
            applicant_context,
        )
    )
    extracted["normalizedTransactions"] = normalized_transactions
    extracted["invalidTransactions"] = bank_statement_analysis.get("invalidTransactions", [])
    extracted["transactionNormalizationWarnings"] = bank_statement_analysis.get(
        "normalizationWarnings", []
    )
    if ocr_incomplete:
        business_rules = {
            **business_rules,
            "status": "manual_review",
            "reason": "OCR did not successfully process every page.",
        }
    return {
        "ocr_status": "OCR_PARTIAL" if ocr_incomplete else "OCR_COMPLETED",
        "extracted": extracted,
        "score": score,
        "financial_features": _financial_features(fields, transactions),
        "financial_analysis": financial_analysis,
        "business_rules": business_rules,
        "bank_statement_analysis": bank_statement_analysis,
        "document_information": bank_statement_analysis["documentInformation"],
        "ocr_processing_summary": bank_statement_analysis["ocrProcessingSummary"],
        "account_information": bank_statement_analysis["accountInformation"],
        "statement_period": bank_statement_analysis["statementPeriod"],
        "monthly_financial_summary": bank_statement_analysis["transactionSummary"]["monthly"],
        "balance_reconciliation": bank_statement_analysis["reconciliation"],
        "account_holder_verification": bank_statement_analysis["accountHolderCheck"],
        "source_of_funds_analysis": bank_statement_analysis["sourceOfFundsAnalysis"],
        "financial_scoring": bank_statement_analysis["bankStatementScore"],
        "review_flags": bank_statement_analysis["reviewFlags"],
        "missing_evidence": bank_statement_analysis["missingEvidence"],
        "extraction_warnings": list(dict.fromkeys(warnings + bank_statement_analysis["extractionWarnings"])),
        "financial_assessment": {
            **financial_analysis,
            "missing_fields": verification["missing_fields"],
            "ocr_confidence": reported_confidence,
        },
        "ml_assessment": ml_assessment,
        "warnings": warnings,
        "human_review_required": (
            ocr_incomplete
            or financial_assessment["status"] == "manual_review"
            or financial_analysis["human_review_required"]
            or business_rules["status"] == "manual_review"
            or bank_statement_analysis["status"] != "CONSISTENCY_CHECK_PASSED"
        ),
        "disclaimer": _DISCLAIMER,
    }


def _analysis_record(statement_id: str, filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = {**payload["result"], "statement_id": statement_id}
    score = result["score"]
    now = datetime.now(timezone.utc).isoformat()
    status = "manual_review" if result["human_review_required"] else "complete"
    return {
        "_id": statement_id,
        "statementId": statement_id,
        "fileName": filename,
        "applicantName": payload["applicant_name"],
        "visaType": payload["visa_type"],
        "requirements": payload["requirements"],
        "status": status,
        "verification_status": status,
        "extraction_status": (
            "partial" if result.get("ocr_status") == "OCR_PARTIAL" else "completed"
        ),
        "document_verification_score": score["document_verification_score"],
        "score": score,
        "financial_score": result["financial_analysis"]["financial_score"],
        "financial_features": result["financial_features"],
        "financial_analysis": result["financial_analysis"],
        "business_rules": result["business_rules"],
        "bank_statement_analysis": result["bank_statement_analysis"],
        "bank_statement_score": result["bank_statement_analysis"][
            "bankStatementScore"
        ],
        "financial_assessment": result["financial_assessment"],
        "ml_assessment": result["ml_assessment"],
        "case_context": payload["applicant_context"],
        "warnings": result["warnings"],
        "result": result,
        "storagePath": payload["storage_path"],
        "createdAt": now,
        "updatedAt": now,
    }


async def _audit_statement_analysis(
    database: AsyncDatabase, actor_id: str, statement_id: str
) -> None:
    try:
        await database["ml_business_audit"].insert_one(
            {
                "_id": str(uuid4()),
                "actor_id": actor_id,
                "action": "bank_statement_analyzed",
                "details": {"statement_id": statement_id},
                "created_at": datetime.now(timezone.utc),
            }
        )
    except PyMongoError as error:
        logger.exception("Could not audit bank statement analysis")
        raise APIError(503, "Required audit record could not be saved") from error


async def _load_statement(
    statement_id: str,
    database: AsyncDatabase,
) -> dict[str, Any]:
    try:
        statement = await database["bank_statements"].find_one(id_query(statement_id))
    except PyMongoError as error:
        logger.exception("Could not load bank statement analysis")
        raise APIError(503, "Bank statement analysis is unavailable") from error
    if statement is None:
        raise APIError(404, "Bank statement analysis not found")
    return statement


@router.post("/analyze", status_code=201)
async def analyze_bank_statement(
    file: UploadFile = File(...),
    applicant_name: str = Form(default="", alias="applicant_name"),
    applicant_name_camel: str = Form(default="", alias="applicantName"),
    visa_type: str = Form(default="", alias="visa_type"),
    visa_type_camel: str = Form(default="", alias="visaType"),
    requirements_json: str = Form(default="{}", alias="requirements"),
    applicant_context_json: str = Form(default="{}", alias="applicant_context"),
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
    pdf_password: str = Form(default="", alias="pdfPassword"),
    force_ocr: bool = Form(default=False, alias="forceOcr"),
) -> JSONResponse:
    filename = Path(file.filename or "bank-statement").name[:255]
    if len(pdf_password) > 1024:
        await file.close()
        raise APIError(400, "PDF password must contain at most 1024 characters")
    extension = Path(filename).suffix.lower()
    signature = _SIGNATURES.get(extension)
    if signature is None:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "Only PDF, JPG, JPEG, and PNG files are supported"},
        )

    content = await file.read(BANK_STATEMENT_MAX_FILE_SIZE + 1)
    await file.close()
    if not content:
        return JSONResponse(status_code=400, content={"message": "The uploaded file is empty"})
    if len(content) > BANK_STATEMENT_MAX_FILE_SIZE:
        return JSONResponse(status_code=413, content={"message": "Uploaded file exceeds the configured maximum size"})
    if not content.startswith(signature):
        return JSONResponse(status_code=400, content={"message": "File content does not match its extension"})

    try:
        requirements = json.loads(requirements_json or "{}")
    except json.JSONDecodeError as error:
        raise APIError(400, "Requirements must be valid JSON") from error
    if not isinstance(requirements, (dict, list)):
        raise APIError(400, "Requirements must be a JSON object or array")
    try:
        applicant_context = json.loads(applicant_context_json or "{}")
    except json.JSONDecodeError as error:
        raise APIError(400, "Applicant context must be valid JSON") from error
    if not isinstance(applicant_context, dict):
        raise APIError(400, "Applicant context must be a JSON object")
    applicant_context = _case_context(applicant_context)

    statement_id = str(uuid4())
    aba_analysis = await asyncio.to_thread(analyze_aba_pdf, content, filename, password=pdf_password, document_id=statement_id, force_ocr=force_ocr) if extension == ".pdf" else None
    if aba_analysis is not None:
        ocr = aba_to_legacy_ocr(aba_analysis)
    else:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.ai_service_timeout_seconds, connect=5.0)
            ) as client:
                response = await client.post(
                    f"{settings.ai_service_url}/ocr/extract",
                    files={"file": (filename, content)},
                    data={"requirement_code": "bank_statement"},
                )
            response.raise_for_status()
            ocr = response.json()
        except httpx.TimeoutException as error:
            logger.warning("OCR service timed out during bank-statement analysis")
            raise APIError(504, "OCR service timed out") from error
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("OCR service failed during bank-statement analysis: %s", error.__class__.__name__)
            raise APIError(503, "OCR service is unavailable") from error
        if not isinstance(ocr, dict):
            raise APIError(503, "OCR service returned an invalid response")

    storage_path = _PRIVATE_DIR / f"{statement_id}{extension}"
    try:
        _PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
    except OSError as error:
        logger.exception("Could not retain uploaded bank statement")
        raise APIError(500, "Could not retain uploaded bank statement") from error

    try:
        policy_record = await database["business_rules"].find_one(
            {"_id": "visa_financial_rules"}
        )
        active_policy = validate_rule_policy(
            policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
        )
    except PyMongoError as error:
        logger.exception("Could not load rules for bank statement analysis")
        raise APIError(503, "Business rules are unavailable") from error
    except ValueError as error:
        logger.exception("Stored business rules are invalid")
        raise APIError(503, "Business rules require administrator review") from error

    result = _build_result(
        ocr,
        applicant_context,
        active_policy,
        applicant_name or applicant_name_camel,
        document_id=statement_id,
    )
    if aba_analysis is not None:
        result["abaStatementAnalysis"] = aba_analysis
        result["human_review_required"] = True
        result["bank_statement_analysis"].update(
            calculate_bank_score_assessment(
                {
                    "status": "NEEDS_REVIEW",
                    "extractedFields": result["extracted"],
                    "ocrConfidence": result["score"].get("ocr_confidence"),
                    "abaStatementAnalysis": aba_analysis,
                },
                result["bank_statement_analysis"],
                applicant_context,
            )
        )
        result["warnings"].append(
            "ABA evidence requires staff review and independent reconciliation before points are confirmed."
        )
    payload = {
        "applicant_name": applicant_name or applicant_name_camel,
        "visa_type": visa_type or visa_type_camel,
        "requirements": requirements,
        "applicant_context": applicant_context,
        "storage_path": str(storage_path),
        "result": result,
    }
    record = _analysis_record(statement_id, filename, payload)
    record["createdBy"] = _principal["id"]
    if aba_analysis is not None:
        record["abaStatementAnalysis"] = aba_analysis
        if pdf_password:
            render_bytes = decrypted_render_bytes(content,pdf_password)
            if render_bytes:
                (_PRIVATE_DIR / f"{statement_id}.aba-render.pdf").write_bytes(render_bytes)
    pdf_password = ""
    try:
        await database["bank_statements"].insert_one(record)
    except PyMongoError as error:
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not clean up an unpersisted bank statement upload")
        logger.exception("Could not save bank statement analysis")
        raise APIError(503, "Bank statement analysis could not be saved") from error
    await _audit_statement_analysis(database, _principal["id"], statement_id)

    account_number = _account_number_from(record)
    public_result = _public_analysis(
        record["result"],
        {str(account_number)} if account_number else set(),
    )
    return JSONResponse(
        status_code=201,
        content={
            "statementId": statement_id,
            "abaStatementAnalysis": record.get("abaStatementAnalysis"),
            "status": record["status"],
            "extraction_status": record["extraction_status"],
            "financial_score": record["financial_score"],
            "financial_features": record["financial_features"],
            "financial_analysis": public_result["financial_analysis"],
            "business_rules": public_result["business_rules"],
            "bank_statement_analysis": public_result["bank_statement_analysis"],
            "financial_assessment": public_result["financial_assessment"],
            "ml_assessment": record["ml_assessment"],
            "human_review_required": record["result"]["human_review_required"],
            "document_verification_score": record["document_verification_score"],
            "bank_statement_score": public_result["bank_statement_analysis"][
                "bankStatementScore"
            ],
            "disclaimer": _DISCLAIMER,
            "result": public_result,
        },
    )


@router.get("/{statement_id}/result")
async def get_bank_statement_result(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    account_number = _account_number_from(statement)
    return JSONResponse(
        content={
            "result": _public_analysis(
                statement.get("result"),
                {str(account_number)} if account_number else set(),
            )
        }
    )


@router.get("/{statement_id}")
async def get_bank_statement(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    account_number = _account_number_from(statement)
    public_statement = _public_analysis(
        statement,
        {str(account_number)} if account_number else set(),
    )
    public_statement.pop("storagePath", None)
    return JSONResponse(content={"statement": public_statement})


@router.get("/{statement_id}/score")
async def get_bank_statement_score(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    return JSONResponse(
        content={
            "score": statement.get("score"),
            "abaStatementAnalysis": statement.get("abaStatementAnalysis"),
            "bank_statement_score": statement.get("bank_statement_analysis", {}).get(
                "bankStatementScore"
            ),
            "verification_status": statement.get("verification_status"),
            "document_verification_score": statement.get("document_verification_score"),
        }
    )


@router.post("/{statement_id}/recalculate")
async def recalculate_bank_statement_score(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    extracted = statement.get("result", {}).get("extracted", {})
    if (
        statement.get("extraction_status") == "partial"
        or statement.get("result", {}).get("ocr_status") == "OCR_PARTIAL"
        or extracted.get("failed_pages")
        or (extracted.get("metrics") or {}).get("status") == "OCR_PARTIAL"
    ):
        raise APIError(
            409,
            "OCR did not successfully process every page. Retry OCR before recalculating the bank-statement score.",
        )
    fields = normalize_bank_fields(extracted)
    score = {
        **calculate_document_verification_score(fields),
        "ocr_confidence": extracted.get("confidence"),
    }
    try:
        numeric_confidence = float(score["ocr_confidence"])
    except (TypeError, ValueError):
        numeric_confidence = None
    if numeric_confidence is not None and not math.isfinite(numeric_confidence):
        numeric_confidence = None
    needs_review = (
        bool(score["missing_fields"])
        or (
            numeric_confidence is not None
            and numeric_confidence < _LOW_CONFIDENCE_THRESHOLD
        )
    )
    update = {
        "score": score,
        "document_verification_score": score["document_verification_score"],
        "result.score": score,
        "result.human_review_required": needs_review,
        "result.financial_assessment.status": (
            "manual_review" if needs_review else "extracted"
        ),
        "verification_status": "manual_review" if needs_review else "complete",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        policy_record = await database["business_rules"].find_one(
            {"_id": "visa_financial_rules"}
        )
        policy = validate_rule_policy(
            policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
        )
    except PyMongoError as error:
        logger.exception("Could not load rules for statement recalculation")
        raise APIError(503, "Business rules are unavailable") from error
    financial_analysis = calculate_financial_analysis(
        extracted,
        extracted.get("transactionHistory") or [],
        policy["financial_scoring"],
    )
    bank_statement_analysis = score_bank_statement(
        extracted,
        raw_text=str(extracted.get("rawText") or ""),
        applicant_name=str(statement.get("applicantName") or ""),
        config=policy["bank_statement_scoring"],
        financial_config=policy["financial_scoring"],
        transaction_source_reviews=statement.get("transactionSourceReviews") or {},
        document_id=statement_id,
    )
    bank_statement_analysis.update(
        calculate_bank_score_assessment(
            {
                "status": "NEEDS_REVIEW",
                "extractedFields": extracted,
                "ocrConfidence": score["ocr_confidence"],
                "abaStatementAnalysis": statement.get("abaStatementAnalysis"),
            },
            bank_statement_analysis,
            statement.get("case_context") or {},
        )
    )
    business_rules = evaluate_business_rules(
        statement.get("case_context") or {},
        financial_analysis,
        policy,
    )
    update.update(
        {
            "financial_score": financial_analysis["financial_score"],
            "financial_analysis": financial_analysis,
            "business_rules": business_rules,
            "result.financial_analysis": financial_analysis,
            "result.business_rules": business_rules,
            "bank_statement_analysis": bank_statement_analysis,
            "result.bank_statement_analysis": bank_statement_analysis,
            "result.financial_assessment": {
                **financial_analysis,
                "missing_fields": score["missing_fields"],
                "ocr_confidence": score["ocr_confidence"],
            },
            "result.human_review_required": (
                bool(score["missing_fields"])
                or (
                    numeric_confidence is not None
                    and numeric_confidence < _LOW_CONFIDENCE_THRESHOLD
                )
                or financial_analysis["human_review_required"]
                or business_rules["status"] == "manual_review"
                or bank_statement_analysis["status"]
                != "CONSISTENCY_CHECK_PASSED"
            ),
        }
    )
    needs_review = bool(update["result.human_review_required"]) or bool(statement.get("abaStatementAnalysis") and (statement["abaStatementAnalysis"].get("reviewStatus") != "VERIFIED" or statement["abaStatementAnalysis"].get("reconciliationStatus") != "RECONCILED"))
    update["result.human_review_required"] = needs_review
    update["verification_status"] = "manual_review" if needs_review else "complete"
    update["status"] = update["verification_status"]
    try:
        await database["bank_statements"].update_one(id_query(statement_id), {"$set": update})
    except PyMongoError as error:
        logger.exception("Could not recalculate bank statement score")
        raise APIError(503, "Bank statement score could not be recalculated") from error
    return JSONResponse(
        content={
            "score": score,
            "verification_status": update["verification_status"],
            "document_verification_score": score["document_verification_score"],
            "bank_statement_score": bank_statement_analysis["bankStatementScore"],
            "bank_statement_analysis": bank_statement_analysis,
        }
    )


@router.get("/{statement_id}/transactions")
async def get_bank_statement_transactions(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    result = statement.get("result", {})
    analysis = result.get("bank_statement_analysis", {})
    transactions = analysis.get("transactions")
    if not isinstance(transactions, list):
        transactions = result.get("extracted", {}).get("normalizedTransactions", [])
    invalid_transactions = analysis.get("invalidTransactions", [])
    account_number = _account_number_from(statement)
    return JSONResponse(
        content={
            "transactions": _public_analysis(
                transactions,
                {str(account_number)} if account_number else set(),
            ),
            "invalidTransactions": _public_analysis(
                invalid_transactions,
                {str(account_number)} if account_number else set(),
            ),
            "transactionCount": len(transactions),
            "invalidTransactionCount": len(invalid_transactions),
            "pagination": {
                "dataset": "normalized_transactions",
                "pageNumbersReferTo": "source_document_pages",
                "ocrPageCount": (result.get("extracted", {}).get("metrics") or {}).get("page_count"),
            },
        }
    )


async def _aba_case_document(document_id: str, database: AsyncDatabase) -> tuple[dict[str, Any], dict[str, Any]]:
    document = await database["documents"].find_one(id_query(document_id))
    if not document or document.get("status") in {"DELETED", "REPLACED"}:
        raise APIError(404, "Current bank document not found")
    application = await database["visaapplications"].find_one(id_query(str(document.get("applicationId") or "")))
    if not application or application.get("deletedAt"):
        raise APIError(404, "Application not found")
    if not isinstance(document.get("abaStatementAnalysis"), dict):
        raise APIError(409, "Local ABA extraction is not available for this document")
    requirement_rows = (
        (application.get("evidencePolicySnapshot") or {}).get("requirements")
        or application.get("requirementSnapshot")
        or []
    )
    requirement = next(
        (
            item
            for item in requirement_rows
            if str(item.get("requirementId") or item.get("_id") or "")
            == str(document.get("requirementId") or "")
        ),
        {},
    )
    configured_maximum = requirement.get(
        "maximumEarnedPoints",
        requirement.get("weight", requirement.get("points")),
    )
    if configured_maximum is not None:
        document["requirementMaximumPoints"] = configured_maximum
    return document, application


def _aba_response(document: dict[str, Any], application: dict[str, Any]) -> dict[str, Any]:
    analysis = document["abaStatementAnalysis"]
    bank_statement_analysis = document.get("bankStatementAnalysis") or {}
    bank_statement_analysis = {
        **bank_statement_analysis,
        **calculate_bank_score_assessment(document, bank_statement_analysis),
    }
    already_verified = (
        document.get("staffReviewStatus") == "CONFIRMED"
        or analysis.get("staffReviewStatus") == "CONFIRMED"
        or document.get("status") == "VERIFIED"
        or analysis.get("reviewStatus") == "VERIFIED"
    )
    verification_blockers = (
        []
        if already_verified
        else _bank_statement_verification_blockers(
            document, analysis, bank_statement_analysis
        )
    )
    return {
        "analysis": analysis,
        "documentId": str(document["_id"]),
        "bankStatementAnalysis": bank_statement_analysis,
        "transactionSourceReviewAudit": document.get("transactionSourceReviewAudit") or [],
        "verificationBlockers": verification_blockers,
        "staffReviewStatus": document.get("staffReviewStatus") or analysis.get("staffReviewStatus"),
        "documentReviewStatus": document.get("documentReviewStatus") or analysis.get("documentReviewStatus"),
        "financialEvidenceStatus": analysis.get("financialEvidenceStatus"),
        "confirmedBankScore": bank_statement_analysis.get("confirmedBankScore"),
        "reviewerId": document.get("reviewerId"),
        "reviewedAt": document.get("reviewedAt"),
        "reviewNote": document.get("reviewNote"),
        "manualReviewHistory": document.get("manualReviewHistory") or [],
        "status": document.get("status"),
        "selectedPrimary": str(application.get("selectedBankDocumentId") or "") == str(document["_id"])
        and application.get("selectedBankAnalysisRevision") == analysis.get("analysisRevision"),
    }


async def _invalidate_aba_case(database: AsyncDatabase, application: dict[str, Any]) -> None:
    await database["visaapplications"].update_one(
        {"_id": application["_id"]}, {"$set": {"verificationMetricsCurrent": False, "predictionInputsCurrent": False,
            "approvalProbability": None, "approvalProbabilityNormalized": None, "rejectionProbabilityNormalized": None,
            "predictionStatus": "STALE" if application.get("predictionStatus") == "COMPLETED" else application.get("predictionStatus", "UNAVAILABLE"),
            "updatedAt": datetime.now(timezone.utc)}})
    from .documents import _refresh_application_score
    await _refresh_application_score(database, application)


async def _save_aba_document(database: AsyncDatabase, document: dict[str, Any], application: dict[str, Any],
                             analysis: dict[str, Any], status: str,
                             review_event: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    fields = aba_to_legacy_ocr(analysis)["fields"]
    policy_record = await database["business_rules"].find_one(
        {"_id": "visa_financial_rules"}
    )
    policy = validate_rule_policy(
        policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
    )
    owner_id = str(
        application.get("customer")
        or application.get("customerId")
        or document.get("customerId")
        or ""
    )
    applicant = (
        await database["customers"].find_one(id_query(owner_id))
        if owner_id
        else None
    ) or {}
    score_config = dict(policy["bank_statement_scoring"])
    bank_statement_analysis = score_bank_statement(
        fields,
        raw_text=str(document.get("ocrText") or ""),
        applicant_name=str(applicant.get("fullName") or ""),
        config=score_config,
        financial_config=policy["financial_scoring"],
        ocr_complete=str(document.get("ocrStatus") or "").upper()
        == "OCR_COMPLETED",
        transaction_source_reviews=document.get("transactionSourceReviews") or {},
        document_id=str(document.get("_id") or ""),
    )
    bank_statement_analysis["sourceReviewAudit"] = (
        document.get("transactionSourceReviewAudit") or []
    )
    score_document = {
        **document,
        "status": status,
        "extractedFields": fields,
        "abaStatementAnalysis": analysis,
        "legacyBankDiagnosticsStale": False,
    }
    bank_statement_analysis.update(
        calculate_bank_score_assessment(
            score_document,
            bank_statement_analysis,
        )
    )
    # Compatibility fields are recalculated from the corrected local extraction.
    update: dict[str, Any] = {
        "$set": {"abaStatementAnalysis": analysis, "status": status, "workflowStatus": status,
                 "extractedFields": fields, "legacyBankDiagnosticsStale": False,
                 "bankStatementAnalysis": bank_statement_analysis,
                 "awardedPoints": 0, "updatedAt": now},
    }
    if review_event:
        update["$set"].update({
            "staffReviewStatus": "CONFIRMED",
            "documentReviewStatus": "CONFIRMED",
            "financialEvidenceStatus": "VERIFIED",
            "reviewedBy": review_event["reviewerId"],
            "reviewerId": review_event["reviewerId"],
            "reviewedAt": review_event["reviewedAt"],
            "reviewReason": review_event["note"],
            "reviewNote": review_event["note"],
        })
        update["$push"] = {"manualReviewHistory": review_event}
    updated = await database["documents"].update_one(
        {"_id": document["_id"], "abaStatementAnalysis.analysisRevision": document["abaStatementAnalysis"]["analysisRevision"],
         "status": {"$nin": ["DELETED", "REPLACED"]}},
        update)
    if not updated.matched_count:
        raise APIError(409, "Document or analysis changed; reload before reviewing")
    await _invalidate_aba_case(database, application)
    from .documents import _refresh_cross_document_consistency

    await _refresh_cross_document_consistency(database, str(application["_id"]))
    return {**document, "abaStatementAnalysis": analysis, "extractedFields": fields,
            "bankStatementAnalysis": bank_statement_analysis,
            **({"staffReviewStatus": "CONFIRMED", "documentReviewStatus": "CONFIRMED",
                "financialEvidenceStatus": "VERIFIED", "reviewedBy": review_event["reviewerId"],
                "reviewerId": review_event["reviewerId"], "reviewedAt": review_event["reviewedAt"],
                "reviewReason": review_event["note"], "reviewNote": review_event["note"],
                "manualReviewHistory": [*(document.get("manualReviewHistory") or []), review_event]}
               if review_event else {}),
            "legacyBankDiagnosticsStale": False, "status": status, "workflowStatus": status, "updatedAt": now}


@router.get("/documents/{document_id}/aba")
async def get_document_aba(document_id: str, database: AsyncDatabase = Depends(get_mongo_database),
                           _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    return JSONResponse(content=_aba_response(document, application), headers={"Cache-Control": "no-store"})


@router.get("/documents/{document_id}/aba/page/{page}")
async def get_document_aba_page(document_id: str, page: int, row: int | None = None,
                                database: AsyncDatabase = Depends(get_mongo_database),
                                _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> Response:
    document, _ = await _aba_case_document(document_id, database)
    return _render_aba_page(document, page, row)


def _render_aba_page(document: dict[str, Any], page: int, row: int | None = None) -> Response:
    import fitz
    original = Path(str(document.get("storagePath") or "")).resolve()
    render = (_PRIVATE_DIR / f'{document["_id"]}.aba-render.pdf').resolve()
    path = render if render.is_file() else original
    if not path.is_relative_to(_PRIVATE_DIR.resolve()) or not path.is_file():
        raise APIError(404, "Private source file is unavailable")
    with fitz.open(path) as pdf:
        if pdf.needs_pass:
            raise APIError(409, "Retry extraction with the PDF password to enable source images")
        if not 1 <= page <= len(pdf):
            raise APIError(404, "Source page not found")
        source = pdf[page-1]
        if row is not None:
            transaction = next((t for t in document["abaStatementAnalysis"].get("transactions", []) if t["id"] == row and t["page"] == page), None)
            if transaction and transaction.get("bbox"):
                source.draw_rect(fitz.Rect(transaction["bbox"]), color=(.08,.45,.39), width=1.5, fill=(.2,.7,.5), fill_opacity=.12)
        return Response(source.get_pixmap(matrix=fitz.Matrix(1.5,1.5), alpha=False).tobytes("png"), media_type="image/png", headers={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"})


@router.post("/documents/{document_id}/aba/corrections")
async def correct_document_aba(document_id: str, payload: dict[str, Any],
                                database: AsyncDatabase = Depends(get_mongo_database),
                                principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    try:
        analysis = apply_correction(document["abaStatementAnalysis"], payload, principal["id"])
    except (ValueError, ArithmeticError) as error:
        raise APIError(409, str(error)) from error
    updated = await _save_aba_document(database, document, application, analysis, "NEEDS_REVIEW")
    return JSONResponse(content=_aba_response(updated, application), headers={"Cache-Control":"no-store"})


@router.post("/documents/{document_id}/aba/review")
async def review_document_aba(document_id: str, payload: dict[str, Any],
                               database: AsyncDatabase = Depends(get_mongo_database),
                               principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    try:
        analysis = reviewed_analysis(document["abaStatementAnalysis"], payload, principal["id"])
    except (ValueError, ArithmeticError) as error:
        raise APIError(409, str(error)) from error
    updated = await _save_aba_document(database, document, application, analysis, analysis["reviewStatus"])
    return JSONResponse(content=_aba_response(updated, application), headers={"Cache-Control":"no-store"})


def _bank_statement_verification_blockers(
    document: dict[str, Any],
    analysis: dict[str, Any],
    bank_statement_analysis: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if analysis.get("reconciliationStatus") != "RECONCILED":
        blockers.append("Financial reconciliation must be RECONCILED.")
    financial_status = str(
        bank_statement_analysis.get("financialRequirementStatus") or ""
    ).upper()
    if financial_status not in {"PASS", "BELOW_MINIMUM"}:
        status = financial_status or "NOT_AVAILABLE"
        blockers.append(f"Required financial evidence checks must pass (current status: {status}).")

    score = bank_statement_analysis.get("bankStatementScore") or {}
    expected_weights = {
        "ending_balance": 22.5,
        "starting_balance": 6.75,
        "transaction_history": 9.0,
        "transaction_period": 6.75,
    }
    components = score.get("components") or {}
    missing_categories = [
        key.replace("_", " ").title()
        for key, maximum in expected_weights.items()
        if not isinstance(components.get(key), dict)
        or components[key].get("available") is not True
        or components[key].get("points") is None
        or components[key].get("maximum") != maximum
    ]
    if missing_categories or score.get("available") is not True or score.get("provisional"):
        categories = ", ".join(missing_categories) or "the complete 45-point score"
        blockers.append(f"Required financial score checks are incomplete: {categories}.")

    required_fields = calculate_document_verification_score(
        document.get("extractedFields") or {}
    )["missing_fields"]
    if required_fields:
        blockers.append(
            "Required document review checklist items are incomplete: "
            + ", ".join(required_fields)
            + "."
        )

    review_blockers = analysis.get("reviewBlockers") or []
    if review_blockers:
        messages = [
            str(item.get("message") or "A required extraction or financial review remains unresolved.")
            for item in review_blockers
            if isinstance(item, dict)
        ]
        if messages:
            blockers.append("Unresolved statement review items: " + " ".join(messages))

    saved_source_reviews = document.get("transactionSourceReviews")
    if not isinstance(saved_source_reviews, dict):
        saved_source_reviews = {}
    transaction_reviews = [
        item
        for item in score.get("transactionReviews") or []
        if isinstance(item, dict) and item.get("requiresStaffReview") is True
    ]
    pending = []
    invalid_resolutions = []
    status_aliases = {
        "SOURCE_CONFIRMED": "CONFIRMED",
        "SOURCE_UNVERIFIED": "UNVERIFIED",
    }
    for item in transaction_reviews:
        index = str(item.get("transactionIndex"))
        saved_review = saved_source_reviews.get(index)
        review_status = status_aliases.get(
            str(item.get("status") or "PENDING_REVIEW").upper(),
            str(item.get("status") or "PENDING_REVIEW").upper(),
        )
        saved_status = (
            status_aliases.get(
                str(saved_review.get("status") or "PENDING_REVIEW").upper(),
                str(saved_review.get("status") or "PENDING_REVIEW").upper(),
            )
            if isinstance(saved_review, dict)
            else "PENDING_REVIEW"
        )
        if review_status not in {"CONFIRMED", "UNVERIFIED"} or saved_status != review_status:
            pending.append(item)
            continue
        if (
            not isinstance(saved_review, dict)
            or not saved_review.get("reviewerId")
            or not saved_review.get("reviewedAt")
            or saved_review.get("sourceCategory") not in _SOURCE_REVIEW_CATEGORIES
            or item.get("reviewerId") != saved_review.get("reviewerId")
            or item.get("reviewedAt") != saved_review.get("reviewedAt")
            or item.get("sourceCategory") != saved_review.get("sourceCategory")
        ):
            invalid_resolutions.append(item)
    if pending:
        indexes = ", ".join(
            str(item.get("transactionIndex")) for item in pending
        )
        blockers.append(
            f"Source review is unresolved for transaction(s): {indexes}. "
            "Confirm the source or explicitly mark it unverified."
        )
    if invalid_resolutions:
        indexes = ", ".join(
            str(item.get("transactionIndex")) for item in invalid_resolutions
        )
        blockers.append(
            f"Source-review decision audit is incomplete for transaction(s): {indexes}."
        )
    return blockers


@router.post("/documents/{document_id}/aba/verify")
async def verify_document_aba(
    document_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    analysis = document["abaStatementAnalysis"]
    if (
        document.get("staffReviewStatus") == "CONFIRMED"
        or analysis.get("staffReviewStatus") == "CONFIRMED"
        or document.get("status") == "VERIFIED"
        or analysis.get("reviewStatus") == "VERIFIED"
    ):
        raise APIError(409, "Bank statement has already been verified")
    if payload.get("analysisRevision") != analysis.get("analysisRevision"):
        raise APIError(409, "Analysis changed. Reload before verification.")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 8:
        raise APIError(400, "A review note of at least 8 characters is required.")

    blockers = _bank_statement_verification_blockers(
        document, analysis, document.get("bankStatementAnalysis") or {}
    )
    if blockers:
        raise APIError(409, "Verification blocked: " + " ".join(blockers))

    now = datetime.now(timezone.utc).isoformat()
    try:
        verified_analysis = reviewed_analysis(
            analysis,
            {
                "analysisRevision": analysis["analysisRevision"],
                "status": "VERIFIED",
                "reason": reason,
            },
            principal["id"],
        )
    except (ValueError, ArithmeticError) as error:
        raise APIError(409, str(error)) from error
    verified_analysis.update({
        "staffReviewStatus": "CONFIRMED",
        "documentReviewStatus": "CONFIRMED",
        "financialEvidenceStatus": "VERIFIED",
        "authenticityStatus": analysis.get("authenticityStatus") or "NOT_VERIFIED",
    })
    review_event = {
        "status": "CONFIRMED",
        "reviewerId": principal["id"],
        "actorRole": principal["role"],
        "reviewedAt": now,
        "note": reason,
        "previousStatus": str(document.get("status") or "NEEDS_REVIEW").upper(),
    }
    updated = await _save_aba_document(
        database, document, application, verified_analysis, "VERIFIED", review_event
    )
    return JSONResponse(
        content=_aba_response(updated, application),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/documents/{document_id}/transactions/{transaction_index}/source-review")
async def review_bank_transaction_source(
    document_id: str,
    transaction_index: int,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    if transaction_index < 0:
        raise APIError(400, "Transaction index must be non-negative")
    document = await database["documents"].find_one(id_query(document_id))
    if (
        not document
        or document.get("status") in {"DELETED", "REPLACED"}
        or not (
            str(document.get("requirementCode") or "").casefold() == "bank_statement"
            or isinstance(document.get("abaStatementAnalysis"), dict)
        )
    ):
        raise APIError(404, "Current bank statement not found")
    analysis = document.get("bankStatementAnalysis")
    if not isinstance(analysis, dict):
        raise APIError(409, "Bank statement scoring is not available for this document")
    action = str(payload.get("action") or "").strip().upper()
    status_by_action = {
        "CONFIRM_SOURCE": "CONFIRMED",
        "SOURCE_UNVERIFIED": "UNVERIFIED",
        "REQUEST_MORE_EVIDENCE": "PENDING_REVIEW",
    }
    if action not in status_by_action:
        raise APIError(400, "Unsupported transaction source-review action")
    evidence_references = payload.get("evidenceReferences") or []
    if isinstance(evidence_references, str):
        evidence_references = [evidence_references]
    if (
        not isinstance(evidence_references, list)
        or any(not isinstance(item, str) or not item.strip() for item in evidence_references)
    ):
        raise APIError(400, "Evidence references must be a list of non-empty strings")
    evidence_references = list(dict.fromkeys(item.strip()[:500] for item in evidence_references))
    notes = str(payload.get("notes") or "").strip()
    source_category = str(payload.get("sourceCategory") or "").strip().upper()
    if source_category and source_category not in _SOURCE_REVIEW_CATEGORIES:
        raise APIError(400, "Choose a valid source category")
    if not source_category:
        raise APIError(400, "A source category is required for every review decision")
    if action == "CONFIRM_SOURCE" and source_category == "UNKNOWN":
        raise APIError(400, "Unknown source cannot be confirmed; mark it unverified after review")
    if source_category == "OTHER" and not notes:
        raise APIError(400, "A Staff Review Note is required when the source category is Other")
    policy_record = await database["business_rules"].find_one(
        {"_id": "visa_financial_rules"}
    )
    policy = validate_rule_policy(
        policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
    )
    bank_policy = policy["bank_statement_scoring"]
    application = await database["visaapplications"].find_one(
        id_query(str(document.get("applicationId") or ""))
    )
    applicant_id = str(
        (application or {}).get("customer")
        or (application or {}).get("customerId")
        or document.get("customerId")
        or ""
    )
    applicant = (
        await database["customers"].find_one(id_query(applicant_id))
        if applicant_id
        else None
    ) or {}
    score_config = dict(bank_policy)
    fields = document.get("extractedFields") or {}
    source_reviews = dict(document.get("transactionSourceReviews") or {})
    current_scoring = score_bank_statement(
        fields,
        raw_text=str(document.get("ocrText") or ""),
        config=score_config,
        financial_config=policy["financial_scoring"],
        applicant_name=str(applicant.get("fullName") or ""),
        ocr_complete=str(document.get("ocrStatus") or "").upper() == "OCR_COMPLETED",
        transaction_source_reviews=source_reviews,
        document_id=str(document.get("_id") or ""),
    )
    transaction_review = next(
        (
            item
            for item in (current_scoring.get("bankStatementScore") or {}).get(
                "transactionReviews", []
            )
            if item.get("transactionIndex") == transaction_index
        ),
        None,
    )
    if transaction_review is None:
        raise APIError(404, "Transaction does not meet the configured staff-review threshold")
    requested_evidence = str(payload.get("requestedEvidence") or "").strip()
    if action == "REQUEST_MORE_EVIDENCE" and not requested_evidence:
        raise APIError(400, "Describe the supporting evidence requested")

    now = datetime.now(timezone.utc).isoformat()
    status = status_by_action[action]
    penalty_points = (
        SOURCE_UNVERIFIED_PENALTY_POINTS if status == "UNVERIFIED" else 0.0
    )
    existing_review = source_reviews.get(str(transaction_index)) or {}
    if not evidence_references:
        evidence_references = list(
            transaction_review.get("evidenceReferences")
            or existing_review.get("evidenceReferences")
            or []
        )
    transaction_source_evidence = transaction_review.get("sourceEvidence")
    previous_history = list(existing_review.get("decisionHistory") or [])
    if not previous_history:
        previous_history = [
            event
            for event in document.get("transactionSourceReviewAudit") or []
            if event.get("transactionIndex") == transaction_index
        ]
    if not previous_history and existing_review.get("reviewerId"):
        previous_history = [
            {
                "status": existing_review.get("status"),
                "sourceCategory": existing_review.get("sourceCategory"),
                "sourceCategoryLabel": existing_review.get("sourceCategoryLabel"),
                "reviewerId": existing_review.get("reviewerId"),
                "reviewedAt": existing_review.get("reviewedAt"),
                "notes": existing_review.get("notes"),
                "evidenceReferences": existing_review.get("evidenceReferences", []),
            }
        ]
    audit_event = {
        "transactionIndex": transaction_index,
        "transactionAmount": transaction_review["amount"],
        "transactionCurrency": transaction_review["currency"],
        "amountUsd": transaction_review["amountUsd"],
        "exchangeRateToUsd": transaction_review["exchangeRateToUsd"],
        "staffReviewThresholdUsd": transaction_review["staffReviewThresholdUsd"],
        "transactionReference": transaction_review.get("reference"),
        "transactionSourceEvidence": transaction_source_evidence,
        "action": action,
        "status": status,
        "sourceCategory": source_category or None,
        "sourceCategoryLabel": _SOURCE_REVIEW_CATEGORIES.get(source_category),
        "reviewerId": principal["id"],
        "reviewedAt": now,
        "evidenceReferences": evidence_references,
        "notes": notes,
        "requestedEvidence": requested_evidence if status == "PENDING_REVIEW" else None,
        "penaltyPoints": penalty_points,
        "penaltyReason": notes if status == "UNVERIFIED" else None,
    }
    review = {
        "status": status,
        "transactionIdentity": _transaction_identity(
            (current_scoring.get("transactions") or [])[transaction_index]
        ),
        "transactionAmount": transaction_review["amount"],
        "transactionCurrency": transaction_review["currency"],
        "amountUsd": transaction_review["amountUsd"],
        "exchangeRateToUsd": transaction_review["exchangeRateToUsd"],
        "staffReviewThresholdUsd": transaction_review["staffReviewThresholdUsd"],
        "transactionReference": transaction_review.get("reference"),
        "transactionSourceEvidence": transaction_source_evidence,
        "sourceCategory": source_category or None,
        "sourceCategoryLabel": _SOURCE_REVIEW_CATEGORIES.get(source_category),
        "reviewerId": principal["id"],
        "reviewedAt": now,
        "evidenceReferences": evidence_references,
        "notes": notes,
        "decisionHistory": [*previous_history, audit_event],
        "requestedEvidence": requested_evidence if status == "PENDING_REVIEW" else None,
        "penaltyPoints": penalty_points,
        "penaltyReason": notes if status == "UNVERIFIED" else None,
    }
    source_reviews[str(transaction_index)] = review
    rescored = score_bank_statement(
        fields,
        raw_text=str(document.get("ocrText") or ""),
        config=score_config,
        financial_config=policy["financial_scoring"],
        applicant_name=str(applicant.get("fullName") or ""),
        ocr_complete=str(document.get("ocrStatus") or "").upper() == "OCR_COMPLETED",
        transaction_source_reviews=source_reviews,
        document_id=str(document.get("_id") or ""),
    )
    rescored.update(
        calculate_bank_score_assessment(
            {**document, "bankStatementAnalysis": rescored},
            rescored,
        )
    )
    bank_score = rescored.get("bankStatementScore") or {}
    pending_transaction_reviews = int(
        bank_score.get("pendingTransactionReviews") or 0
    )
    transaction_reviews = [
        item
        for item in bank_score.get("transactionReviews") or []
        if isinstance(item, dict)
    ]
    all_transaction_reviews_complete = bool(transaction_reviews) and pending_transaction_reviews == 0
    completed_review_timestamp = (
        max(
            (
                str(item.get("reviewedAt") or "")
                for item in transaction_reviews
                if item.get("status") in {"CONFIRMED", "UNVERIFIED"}
                and item.get("reviewedAt")
            ),
            default=now,
        )
        if all_transaction_reviews_complete
        else None
    )
    completed_review_fields = (
        {
            "status": "VERIFIED",
            "workflowStatus": "VERIFIED",
            "staffReviewStatus": "VERIFIED",
            "documentReviewStatus": "VERIFIED",
            "financialEvidenceStatus": "VERIFIED",
            "reviewedAt": completed_review_timestamp,
        }
        if all_transaction_reviews_complete
        else {
            "status": "NEEDS_REVIEW",
            "workflowStatus": "NEEDS_REVIEW",
            "staffReviewStatus": "PENDING_REVIEW",
            "documentReviewStatus": "PENDING_REVIEW",
        }
    )
    revision = int(document.get("transactionSourceReviewRevision") or 0)
    rescored["sourceReviewAudit"] = [
        *(document.get("transactionSourceReviewAudit") or []),
        audit_event,
    ]
    updated = await database["documents"].update_one(
        {
            "_id": document["_id"],
            "transactionSourceReviewRevision": revision,
        },
        {
            "$set": {
                "transactionSourceReviews": source_reviews,
                "transactionSourceReviewRevision": revision + 1,
                "bankStatementAnalysis": rescored,
                "updatedAt": datetime.now(timezone.utc),
                **completed_review_fields,
                "abaStatementAnalysis.staffReviewStatus": completed_review_fields["staffReviewStatus"],
                "abaStatementAnalysis.documentReviewStatus": completed_review_fields["documentReviewStatus"],
                "abaStatementAnalysis.financialEvidenceStatus": (
                    completed_review_fields.get("financialEvidenceStatus")
                    or "NEEDS_REVIEW"
                ),
                "abaStatementAnalysis.reviewedAt": completed_review_timestamp,
            },
            "$push": {"transactionSourceReviewAudit": audit_event},
        },
    )
    if not updated.matched_count and revision == 0:
        updated = await database["documents"].update_one(
            {
                "_id": document["_id"],
                "transactionSourceReviewRevision": {"$exists": False},
            },
            {
                "$set": {
                    "transactionSourceReviews": source_reviews,
                    "transactionSourceReviewRevision": 1,
                    "bankStatementAnalysis": rescored,
                    "updatedAt": datetime.now(timezone.utc),
                    **completed_review_fields,
                    "abaStatementAnalysis.staffReviewStatus": completed_review_fields["staffReviewStatus"],
                    "abaStatementAnalysis.documentReviewStatus": completed_review_fields["documentReviewStatus"],
                    "abaStatementAnalysis.financialEvidenceStatus": (
                        completed_review_fields.get("financialEvidenceStatus")
                        or "NEEDS_REVIEW"
                    ),
                    "abaStatementAnalysis.reviewedAt": completed_review_timestamp,
                },
                "$push": {"transactionSourceReviewAudit": audit_event},
            },
        )
    if not updated.matched_count:
        raise APIError(409, "Transaction review changed; reload before saving")
    if application:
        await _invalidate_aba_case(database, application)
    account_number = _account_number_from(document)
    public_analysis = _public_analysis(
        rescored,
        {account_number} if account_number else set(),
    )
    return JSONResponse(
        content={
            "bankStatementAnalysis": public_analysis,
            "audit": audit_event,
            "auditHistory": [
                *(document.get("transactionSourceReviewAudit") or []),
                audit_event,
            ],
            "transactionSourceReviewRevision": revision + 1,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/documents/{document_id}/aba/select-primary")
async def select_primary_document_aba(document_id: str, payload: dict[str, Any],
                                       database: AsyncDatabase = Depends(get_mongo_database),
                                       principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    analysis = document["abaStatementAnalysis"]
    if payload.get("analysisRevision") != analysis["analysisRevision"]:
        raise APIError(409, "Analysis changed; reload before selecting")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 8:
        raise APIError(400, "A selection reason of at least 8 characters is required")
    if document.get("status") != "VERIFIED" or analysis.get("reviewStatus") != "VERIFIED" or analysis.get("reconciliationStatus") != "RECONCILED":
        raise APIError(409, "Verify the exact bank evidence and reconciliation before selecting it")
    event = {"documentId": document_id, "analysisRevision": analysis["analysisRevision"], "reason": reason,
             "actorId": principal["id"], "at": datetime.now(timezone.utc).isoformat()}
    await database["visaapplications"].update_one({"_id": application["_id"]},
        {"$set": {"selectedBankDocumentId": document_id, "selectedBankAnalysisRevision": analysis["analysisRevision"]},
         "$push": {"bankSelectionHistory": event}})
    await _invalidate_aba_case(database, {**application,"selectedBankDocumentId":document_id})
    fresh = await database["documents"].find_one(id_query(document_id))
    if not fresh or fresh.get("status") != "VERIFIED" or (fresh.get("abaStatementAnalysis") or {}).get("analysisRevision") != analysis["analysisRevision"]:
        raise APIError(409, "Bank evidence changed during selection; reload and review the current revision")
    return JSONResponse(content=_aba_response(document, {**application,"selectedBankDocumentId":document_id,"selectedBankAnalysisRevision":analysis["analysisRevision"]}), headers={"Cache-Control":"no-store"})
