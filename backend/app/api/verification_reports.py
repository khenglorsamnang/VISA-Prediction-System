from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.config import PROJECT_ROOT
from ..core.errors import APIError
from ..services.document_metrics import (
    bank_requirement_points,
    requirement_awarded_points,
    summarize_documents,
)
from ..services.assessment_context import (
    ABA_FEATURE_SCHEMA, LEGACY_FEATURE_SCHEMA, application_requirements, bank_verified_for_prediction,
    canonical_summary, policy_snapshot, prediction_fingerprint, selected_bank_document, digest,
)
from ..services.financial_analysis import calculate_financial_analysis
from ..services.photo_validation import is_photo_document
from ..services.readiness_scoring import (
    calculate_bank_score_assessment,
)
from ..services.verification_receipts import create_qr_png, create_receipt_pdf
from .dependencies import get_mongo_database, require_roles
from .document_workflow import has_completed_ocr
from .ml import _audit, _load_policy, predict_from_verified_features as run_visa_model_prediction
from .mongo_helpers import id_query, public_document, redact_sensitive_data

logger = logging.getLogger(__name__)
router = APIRouter()
public_router = APIRouter()

_PRIVATE_DIR = PROJECT_ROOT / "backend" / "storage" / "private"
_RECEIPT_DIR = PROJECT_ROOT / "backend" / "storage" / "receipts"
_RISK_HIGH_REJECTION_MINIMUM = 0.70
_RISK_LOW_REJECTION_MAXIMUM = 0.30


def _mask_passport(value: Any) -> str:
    normalized = "".join(character for character in str(value or "") if character.isalnum())
    return f"{'*' * max(0, len(normalized) - 4)}{normalized[-4:]}" if normalized else "—"


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_usd(value: Any) -> bool:
    return str(value or "").strip().upper() in {"USD", "US DOLLAR", "US DOLLARS", "$"}


async def _load_context(
    application_id: str,
    database: AsyncDatabase,
    principal: dict[str, str],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    try:
        application = await database["visaapplications"].find_one(id_query(application_id))
        if not application or application.get("deletedAt"):
            raise APIError(404, "Application not found")
        owner_id = str(application.get("customer") or application.get("customerId") or "")
        if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
            raise APIError(404, "Application not found")
        customer = (
            await database["customers"].find_one(id_query(owner_id))
            if owner_id
            else {}
        ) or {}
        visa_type_id = str(application.get("visaTypeId") or application.get("visaType") or "")
        visa_type = (
            await database["visatypes"].find_one(id_query(visa_type_id))
            if visa_type_id
            else {}
        ) or {}
        live_requirements = await database["visarequirements"].find(
            {"visaTypeId": visa_type.get("_id") or visa_type_id, "status": "ACTIVE"}
        ).to_list(length=None)
        requirements = application_requirements(application, live_requirements or application.get("requirementSnapshot") or [])
        requirements = [
            {**item, "requirementId": str(item.get("_id") or item.get("requirementId"))}
            for item in requirements
        ]
        documents = await database["documents"].find(
            {"applicationId": str(application.get("_id") or application_id)}
        ).to_list(length=None)
        documents = [
            item
            for item in documents
            if str(item.get("status") or "").upper() not in {"DELETED", "REPLACED"}
        ]
    except APIError:
        raise
    except PyMongoError as error:
        logger.exception("Could not load verification report data")
        raise APIError(503, "Verification report data is unavailable") from error
    return application, customer, visa_type, requirements, documents


def _requirement_points(
    document: dict[str, Any], requirements: list[dict[str, Any]]
) -> float:
    requirement_id = str(document.get("requirementId") or "")
    requirement = next(
        (
            item
            for item in requirements
            if str(item.get("requirementId") or item.get("_id") or "")
            == requirement_id
        ),
        {},
    )
    try:
        return max(
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
        return 0.0


def _public_document_result(
    document: dict[str, Any],
    requirements: list[dict[str, Any]],
    bank_statement_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public = public_document(document)
    scoring_document = {
        **document,
        **(
            {"bankStatementAnalysis": bank_statement_analysis}
            if isinstance(bank_statement_analysis, dict)
            else {}
        ),
    }
    document_id = str(document.get("_id") or "")
    fields = public.get("extractedFields")
    raw_fields = document.get("extractedFields")
    account_number = (
        raw_fields.get("accountNumber") or raw_fields.get("accountNo")
        if isinstance(raw_fields, dict)
        else None
    )
    extracted_text = redact_sensitive_data(
        str(document.get("ocrText") or ""),
        {str(account_number)} if account_number else set(),
    )
    return {
        "documentId": document_id,
        "documentReference": document.get("documentReference")
        or f"DOC-{document_id.replace('-', '')[:12].upper()}",
        "fileName": document.get("fileName") or "Uploaded document",
        "mimeType": document.get("mimeType") or "application/octet-stream",
        "requirementId": str(document.get("requirementId") or ""),
        "requirementCode": document.get("requirementCode") or "unknown",
        "ocrStatus": "OCR_COMPLETED" if has_completed_ocr(document) else str(
            document.get("ocrStatus") or document.get("workflowStatus") or "UPLOADED"
        ).upper(),
        "workflowStatus": str(document.get("workflowStatus") or "").upper(),
        "pagesProcessed": document.get("ocrPagesProcessed") or 0,
        "totalPages": document.get("ocrTotalPages") or 0,
        "progressPercent": document.get("ocrProgressPercent") or 0,
        "confidence": document.get("ocrConfidence"),
        "ocrConfidence": document.get("ocrConfidence"),
        "ocrAnalysis": redact_sensitive_data(
            document.get("ocrAnalysis") or {},
            {str(account_number)} if account_number else set(),
        ),
        "fieldEvidence": redact_sensitive_data(
            document.get("ocrFieldEvidence") or {},
            {str(account_number)} if account_number else set(),
        ),
        "missingFields": document.get("missingFields") or [],
        "ocrWarnings": document.get("ocrWarnings") or [],
        "abaStatementAnalysis": redact_sensitive_data(document.get("abaStatementAnalysis"), {str(account_number)} if account_number else set()),
        "qualityAssessment": document.get("qualityAssessment"),
        "bankStatementCheckPercent": document.get(
            "bankStatementCheckPercent"
        ),
        "bankStatementMissingFields": document.get(
            "bankStatementMissingFields"
        ) or [],
        "bankStatementAnalysis": redact_sensitive_data(
            bank_statement_analysis
            if bank_statement_analysis is not None
            else document.get("bankStatementAnalysis"),
            {str(account_number)} if account_number else set(),
        )
        if bank_statement_analysis is not None
        or isinstance(document.get("bankStatementAnalysis"), dict)
        else None,
        "status": str(document.get("status") or "PENDING").upper(),
        "automatedVerification": redact_sensitive_data(
            document.get("automatedVerification") or {},
            {str(account_number)} if account_number else set(),
        )
        or None,
        "extractedFields": fields if isinstance(fields, dict) else {},
        "extractedText": extracted_text,
        "verificationComments": document.get("verificationComments") or "",
        "rejectionReason": document.get("rejectionReason") or "",
        "reviewReason": document.get("reviewReason") or "",
        "manualReviewHistory": redact_sensitive_data(
            document.get("manualReviewHistory") or [],
            {str(account_number)} if account_number else set(),
        ),
        "assignedPoints": _requirement_points(
            next(
                (
                    item
                    for item in requirements
                    if str(item.get("requirementId") or item.get("_id") or "")
                    == str(document.get("requirementId") or "")
                ),
                {},
            ),
            requirements,
        ),
        "awardedPoints": requirement_awarded_points(
            next(
                (
                    item
                    for item in requirements
                    if str(item.get("requirementId") or item.get("_id") or "")
                    == str(document.get("requirementId") or "")
                ),
                {},
            ),
            scoring_document,
        ),
        "contentUrl": f"/documents/{document_id}/content",
    }


def _normalized_probability(application: dict[str, Any], key: str, legacy_key: str) -> float | None:
    normalized = _numeric(application.get(key))
    if normalized is not None and 0 <= normalized <= 1:
        return normalized
    percent = _numeric(application.get(legacy_key))
    return percent / 100 if percent is not None and 0 <= percent <= 100 else None


def _prediction_from_application(application: dict[str, Any]) -> dict[str, Any]:
    status = str(application.get("predictionStatus") or "UNAVAILABLE").upper()
    if status != "COMPLETED":
        return {
            "status": status if status in {"STALE", "INSUFFICIENT_DATA", "NEEDS_REVIEW"} else "UNAVAILABLE",
            "approvalProbability": None,
            "rejectionProbability": None,
            "approvalProbabilityNormalized": None,
            "rejectionProbabilityNormalized": None,
            "probabilityUnit": "percent",
            "normalizedProbabilityUnit": "0-1",
            "message": application.get("predictionMessage")
            or "Prediction unavailable. No validated active model or complete verified data is available.",
        }
    return {
        "status": "COMPLETED",
        "approvalProbability": application.get("approvalProbability"),
        "rejectionProbability": application.get("rejectionProbability"),
        "approvalProbabilityNormalized": _normalized_probability(application, "approvalProbabilityNormalized", "approvalProbability"),
        "rejectionProbabilityNormalized": _normalized_probability(application, "rejectionProbabilityNormalized", "rejectionProbability"),
        "probabilityUnit": "percent",
        "normalizedProbabilityUnit": "0-1",
        "riskLevel": application.get("riskLevel"),
        "modelVersion": application.get("predictionModelVersion"),
        "inputFingerprint": application.get("predictionInputFingerprint"),
        "featureSchemaVersion": application.get("predictionFeatureSchemaVersion"),
        "policyVersionId": application.get("policyVersionId"),
        "timestamp": application.get("predictionTimestamp"),
        "positiveFactors": application.get("predictionPositiveFactors") or [],
        "riskFactors": application.get("predictionRiskFactors") or [],
        "recommendations": application.get("predictionRecommendations") or [],
        "explanation": application.get("predictionExplanation") or "",
        "disclaimer": application.get("predictionDisclaimer")
        or "A calibrated model estimate for human review only, not a visa decision or guarantee.",
    }


def _build_visa_features(
    application: dict[str, Any],
    customer: dict[str, Any],
    documents: list[dict[str, Any]],
    summary: dict[str, Any],
    financial_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verified_documents = [
        item
        for item in documents
        if str(item.get("status") or "").upper() == "VERIFIED"
        and (is_photo_document(item) or has_completed_ocr(item))
    ]
    document_types = {
        str(item.get("requirementCode") or "").strip().lower()
        for item in verified_documents
    }
    features: dict[str, Any] = {}
    destination = application.get("destination")
    nationality = customer.get("nationality")
    employment_status = customer.get("employmentStatus")
    if destination:
        features["destination_country"] = destination
    if nationality:
        features["nationality"] = nationality
    if employment_status:
        features["employment_status"] = employment_status

    for customer_field, model_field in (
        ("travelHistoryCount", "travel_history_count"),
        ("previousVisaDenials", "previous_visa_denials"),
        ("accountHistoryMonths", "account_history_months"),
    ):
        value = _numeric(customer.get(customer_field))
        if value is not None:
            features[model_field] = value
    if isinstance(customer.get("hasOverstayHistory"), bool):
        features["has_overstay_history"] = int(customer["hasOverstayHistory"])
    if isinstance(customer.get("proofOfTiesToCambodia"), bool):
        features["proof_of_ties_to_cambodia"] = int(
            customer["proofOfTiesToCambodia"]
        )

    features["employment_evidence"] = int(
        bool(document_types.intersection({"employment_letter", "salary_slip"}))
    )
    features["business_evidence"] = int(
        bool(document_types.intersection({"business_license", "tax_document"}))
    )
    features["sponsor_support"] = int(
        bool(document_types.intersection({"sponsorship_letter", "invitation_letter"}))
    )

    fixed_deposit = next(
        (
            _numeric((item.get("extractedFields") or {}).get("depositAmount"))
            for item in verified_documents
            if str(item.get("requirementCode") or "").lower() == "fixed_deposit"
            and _numeric((item.get("extractedFields") or {}).get("depositAmount"))
            is not None
        ),
        None,
    )
    features["fixed_deposit"] = fixed_deposit if fixed_deposit is not None else 0

    bank_document = selected_bank_document(application, verified_documents)
    if bank_document is not None and not bank_verified_for_prediction(bank_document):
        bank_document = None
    if bank_document:
        fields = bank_document.get("extractedFields")
        fields = fields if isinstance(fields, dict) else {}
        transactions = fields.get("transactionHistory")
        if not isinstance(transactions, list):
            transactions = []
        financial = calculate_financial_analysis(fields, transactions, config=(financial_policy or {}).get("financial_scoring"))
        if _is_usd(fields.get("currency")):
            for model_field, value in (
                ("financial_score", financial.get("financial_score")),
                ("average_daily_balance_usd", financial.get("average_daily_balance")),
            ):
                if _numeric(value) is not None:
                    features[model_field] = _numeric(value)
        for customer_field, model_field in (
            ("monthlyIncomeUsd", "monthly_income_usd"),
            ("monthlyExpensesUsd", "monthly_expenses_usd"),
        ):
            value = _numeric(customer.get(customer_field))
            if value is not None:
                features[model_field] = value
        if isinstance(fields.get("transactionHistory"), list):
            features["transaction_count"] = len(fields["transactionHistory"])

    snapshot = policy_snapshot(application)
    if snapshot:
        features["_feature_schema_version"] = ABA_FEATURE_SCHEMA
        features.pop("nationality", None)
        # Legacy models retain their original customer-declared income feature
        # definitions. New schema models must not call those declarations
        # independently verified financial evidence.
        features.pop("monthly_income_usd", None)
        features.pop("monthly_expenses_usd", None)
        salary_candidates = {
            value
            for document in verified_documents
            if str(document.get("requirementCode") or "").casefold() in {"employment_letter", "salary_slip", "salary_certificate"}
            and _is_usd((document.get("extractedFields") or {}).get("currency"))
            if (value := _numeric((document.get("extractedFields") or {}).get("monthlySalary"))) is not None and value >= 0
        }
        if len(salary_candidates) == 1:
            features["monthly_income_usd"] = next(iter(salary_candidates))
        reviewed_inputs = application.get("verifiedPredictionInputs")
        verified_ids = {str(d.get("_id") or "") for d in verified_documents}
        references = reviewed_inputs.get("sourceReferences") if isinstance(reviewed_inputs, dict) else None
        valid_references = isinstance(references, list) and bool(references) and all(isinstance(ref,str) and ref.split(":",1)[0] in verified_ids for ref in references)
        if isinstance(reviewed_inputs, dict) and reviewed_inputs.get("reviewedBy") and reviewed_inputs.get("reason") and valid_references:
            values = reviewed_inputs.get("values") or {}
            for name in ("monthly_income_usd", "monthly_expenses_usd"):
                value = _numeric(values.get(name))
                if value is not None and value >= 0:
                    features[name] = value
        features["visa_category"] = str(application.get("visaCategory") or application.get("category") or snapshot.get("visaCategory") or snapshot.get("category") or application.get("visaTypeId") or "")
        features["evidence_strength_score"] = summary.get("confirmedReadinessPoints", summary.get("documentAssessmentScore"))
        aba = (bank_document or {}).get("abaStatementAnalysis") or {}
        if bank_document and isinstance(aba, dict) and bank_verified_for_prediction(bank_document):
            metrics = aba.get("metrics") or {}
            for key,value in (("aba_statement_strength",aba.get("statementStrengthScore")),("aba_statement_quality",aba.get("normalizedQuality")),("aba_statement_days",metrics.get("days"))):
                number=_numeric(value)
                if number is not None:features[key]=number
            if str((aba.get("source") or {}).get("currency") or (aba.get("statement") or {}).get("currency") or "").upper()=="USD":
                from decimal import Decimal
                for model_key,metric_key in (("aba_average_daily_balance_usd","average"),("aba_ending_balance_usd","ending"),("aba_money_in_usd","money_in"),("aba_money_out_usd","money_out")):
                    value=metrics.get(metric_key)
                    if value is not None:features[model_key]=float(Decimal(str(value))/100)
    else:
        features["_feature_schema_version"] = LEGACY_FEATURE_SCHEMA

    # The model receives the configured-point assessment, not a raw document
    # count, so changing admin points changes the resulting probability input.
    features["document_completeness_score"] = summary.get(
        "documentAssessmentScore", summary.get("documentReadiness", 0)
    )
    return features


def _prediction_factors(result: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    positive: list[str] = []
    risks: list[str] = []
    recommendations: list[str] = []
    for item in result.get("explanation") or []:
        if not isinstance(item, dict):
            continue
        feature = str(item.get("feature") or "model feature").replace("_", " ")
        direction = str(item.get("direction") or "")
        basis = str(item.get("explanation_basis") or "")
        if direction == "supports predicted class":
            positive.append(f"{feature} supports the approval estimate.")
        elif direction == "reduces support for predicted class":
            risks.append(f"{feature} reduces support for the approval estimate.")
        elif item.get("importance") is not None:
            risks.append(
                f"{feature} was influential; this model explanation has no direction."
            )
        if direction == "reduces support for predicted class":
            recommendations.append(
                f"Have a qualified reviewer assess the verified {feature} evidence."
            )
        if basis and not direction and item.get("importance") is not None:
            recommendations.append(
                "Feature importance is non-directional; do not treat it as a finding."
            )
    return positive, risks, list(dict.fromkeys(recommendations))


def _risk_level(rejection_probability: float) -> str:
    if rejection_probability >= _RISK_HIGH_REJECTION_MINIMUM:
        return "HIGH"
    if rejection_probability <= _RISK_LOW_REJECTION_MAXIMUM:
        return "LOW"
    return "MEDIUM"


def _latest_verification_id(application_id: str, verification: dict[str, Any]) -> str:
    return str(verification.get("_id") or "")


async def _latest_verification(
    database: AsyncDatabase, application_id: str
) -> dict[str, Any] | None:
    records = await database["verifications"].find(
        {"applicationId": application_id}
    ).to_list(length=None)
    records = [
        item
        for item in records
        if str(item.get("applicationId") or "") == application_id
    ]
    records.sort(
        key=lambda item: str(item.get("createdAt") or item.get("updatedAt") or ""),
        reverse=True,
    )
    return records[0] if records else None


async def _prediction_context(application, customer, requirements, documents, database):
    active=await database["ml_active_models"].find_one({"_id":"visa"})
    version=(active or {}).get("model_version")
    snapshot=policy_snapshot(application)
    financial_policy=(snapshot or {}).get("financialPolicy") or application.get("financialPolicySnapshot") or await _load_policy(database)
    fingerprint=prediction_fingerprint(application,customer,requirements,documents,version,financial_policy)
    return fingerprint,version,financial_policy


async def _checked_prediction(application,customer,requirements,documents,database):
    if application.get("predictionStatus")!="COMPLETED":
        return _prediction_from_application(application)
    fingerprint,version,_=await _prediction_context(application,customer,requirements,documents,database)
    if not application.get("predictionInputFingerprint") or fingerprint!=application.get("predictionInputFingerprint") or version!=application.get("predictionModelVersion"):
        return {"status":"STALE","approvalProbability":None,"rejectionProbability":None,"approvalProbabilityNormalized":None,"rejectionProbabilityNormalized":None,"probabilityUnit":"percent","normalizedProbabilityUnit":"0-1","message":"Evidence, review, policy, or active model changed. Recalculate the prediction."}
    return _prediction_from_application(application)


def _receipt_summary(application: dict[str, Any]) -> dict[str, Any] | None:
    report=application.get("verificationReport") or {}
    number=application.get("receiptNumber")
    if not number or not isinstance(report,dict):
        return None
    return {"receiptNumber":number,"issuedAt":application.get("receiptGeneratedAt") or report.get("receiptGeneratedAt"),
            "reportType":report.get("reportType") or "LEGACY","policyVersionId":report.get("policyVersionId"),
            "finalStatus":report.get("finalStatus"),"assessmentContextFingerprint":report.get("assessmentContextFingerprint") or application.get("receiptInputFingerprint")}


async def _receipt_is_current(application,customer,requirements,documents,database) -> bool:
    report=application.get("verificationReport")
    if not application.get("receiptNumber") or not isinstance(report,dict):
        return False
    stored=report.get("assessmentContextFingerprint") or application.get("receiptInputFingerprint")
    if not stored or stored!=prediction_fingerprint(application,customer,requirements,documents):
        return False
    # An old prediction remains a historical estimate, not the current model's
    # estimate after activation, policy or evidence changes.
    if (report.get("prediction") or {}).get("status")=="COMPLETED":
        prediction=await _checked_prediction(application,customer,requirements,documents,database)
        if prediction.get("status")!="COMPLETED" or prediction.get("modelVersion")!=(report.get("prediction") or {}).get("modelVersion"):
            return False
    return True


_RECEIPT_HASH_VERSION = "bson-ms-v1"
_RECEIPT_IMMUTABLE_FIELDS = (
    "_id", "applicationId", "report", "receiptFilePath",
    "receiptVerificationToken", "receiptGeneratedAt",
)


def _receipt_storage_value(value):
    """Use MongoDB's UTC millisecond datetime precision before hashing."""
    if isinstance(value, datetime):
        utc = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return utc.replace(microsecond=(utc.microsecond // 1000) * 1000)
    if isinstance(value, dict):
        return {key: _receipt_storage_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_receipt_storage_value(item) for item in value]
    return value


def _receipt_archive_snapshot(application):
    report = application["verificationReport"]
    return {
        "_id": str(application["receiptNumber"]),
        "applicationId": str(application["_id"]),
        "report": report,
        "receiptFilePath": application.get("receiptFilePath"),
        "receiptVerificationToken": application.get("receiptVerificationToken"),
        "receiptGeneratedAt": application.get("receiptGeneratedAt"),
        "contentHashVersion": _RECEIPT_HASH_VERSION,
        "contentHash": digest(_receipt_storage_value(report)),
    }


def _receipt_archive_matches(existing, candidate):
    version = existing.get("contentHashVersion")
    if version == _RECEIPT_HASH_VERSION:
        # Detect changes to a saved versioned snapshot, as well as differences
        # between the immutable archive and the application's old receipt.
        if existing.get("contentHash") != digest(_receipt_storage_value(existing.get("report"))):
            return False
    elif version is None:
        # Original archives hashed Python microseconds, which BSON then lost.
        # Their original digest cannot be reconstructed after storage. Compare
        # the complete persisted snapshot and envelope instead; never rewrite
        # the original record or permit any substantive historical difference.
        if not re.fullmatch(r"[0-9a-f]{64}", str(existing.get("contentHash") or "")):
            return False
    else:
        return False
    old = {key: existing.get(key) for key in _RECEIPT_IMMUTABLE_FIELDS}
    new = {key: candidate.get(key) for key in _RECEIPT_IMMUTABLE_FIELDS}
    return digest(_receipt_storage_value(old)) == digest(_receipt_storage_value(new))


async def _archive_receipt(database,application):
    """Append-only receipt snapshots; older PDFs and tokens retain their meaning."""
    if not application.get("receiptNumber") or not isinstance(application.get("verificationReport"),dict):
        return
    record = _receipt_archive_snapshot(application)
    number = record["_id"]
    existing=await database["verificationreceipts"].find_one({"_id":number})
    if existing:
        if not _receipt_archive_matches(existing, record):
            raise APIError(409,"Historical receipt snapshot does not match its immutable record")
        return
    try:
        await database["verificationreceipts"].insert_one(record)
    except DuplicateKeyError:
        existing=await database["verificationreceipts"].find_one({"_id":number})
        if not existing or not _receipt_archive_matches(existing, record):
            raise APIError(409,"Receipt version changed while preserving its history")


async def _receipt_record(database,*,number=None,token=None):
    query={"_id":number} if number is not None else {"receiptVerificationToken":token}
    record=await database["verificationreceipts"].find_one(query)
    if record:
        application=await database["visaapplications"].find_one(id_query(str(record["applicationId"])))
        return application,record
    application=await database["visaapplications"].find_one({"receiptNumber":number} if number is not None else {"receiptVerificationToken":token})
    if not application or not application.get("verificationReport"):
        return None,None
    return application,{"_id":application.get("receiptNumber"),"applicationId":str(application["_id"]),"report":application["verificationReport"],
                        "receiptFilePath":application.get("receiptFilePath"),"receiptVerificationToken":application.get("receiptVerificationToken"),"receiptGeneratedAt":application.get("receiptGeneratedAt")}


@router.get("/results/{application_id}")
async def get_verification_results(
    application_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF", "CUSTOMER")),
) -> JSONResponse:
    application, customer, visa_type, requirements, documents = await _load_context(
        application_id, database, principal
    )
    try:
        latest = await _latest_verification(database, str(application["_id"]))
    except PyMongoError as error:
        logger.exception("Could not load verification history")
        raise APIError(503, "Verification results are unavailable") from error
    not_applicable = {
        str(item.get("requirementId") or "")
        for item in (latest or {}).get("results") or []
        if item.get("conditional") and item.get("status") == "NOT_APPLICABLE"
    }
    summary = canonical_summary(application, requirements, documents, not_applicable)
    if policy_snapshot(application):
        summary["readinessScore"] = {"score":summary.get("confirmedReadinessPoints"), "normalized":summary.get("confirmedReadinessNormalized"), "maximum":100, "status":summary.get("status"), "components":summary.get("requirementScores",[]), "gates":summary.get("gates",[]), "scoringVersion":"evidence-v3", "disclaimer":"Evidence points, not visa approval probability."}
    required_ids = {
        str(item.get("requirementId") or item.get("_id") or "")
        for item in requirements
        if item.get("required", True)
        and str(item.get("requirementId") or item.get("_id") or "")
        not in not_applicable
    }
    requirement_by_id = {
        str(item.get("requirementId") or item.get("_id") or ""): item
        for item in requirements
        if isinstance(item, dict)
    }
    verified_requirement_ids = {
        str(document.get("requirementId") or "")
        for document in documents
        if str(document.get("status") or "").upper() == "VERIFIED"
        and (
            is_photo_document(
                document,
                requirement_by_id.get(str(document.get("requirementId") or "")),
            )
            or has_completed_ocr(document)
        )
    }
    document_statuses = {
        str(document.get("status") or "").upper()
        for document in documents
    }
    if not documents or not required_ids:
        automated_status = "INSUFFICIENT_DATA"
    elif document_statuses.intersection({"PROCESSING", "UPLOADED"}) or any(
        str(document.get("ocrStatus") or "").upper()
        in {"OCR_PENDING", "OCR_PROCESSING"}
        and not is_photo_document(
            document,
            requirement_by_id.get(str(document.get("requirementId") or "")),
        )
        for document in documents
    ):
        automated_status = "PROCESSING"
    elif "REJECTED" in document_statuses:
        automated_status = "REJECTED"
    elif "FAILED" in document_statuses:
        automated_status = "FAILED"
    elif document_statuses.intersection({"NEEDS_REVIEW", "SUSPICIOUS"}):
        automated_status = "NEEDS_REVIEW"
    elif (
        "INSUFFICIENT_DATA" in document_statuses
        or not required_ids.issubset(verified_requirement_ids)
    ):
        automated_status = "INSUFFICIENT_DATA"
    else:
        automated_status = "VERIFIED"
    if policy_snapshot(application):
        automated_status="VERIFIED" if summary.get("complete") and not summary.get("gates") else str(summary.get("status") or "NEEDS_REVIEW")
    summary["automatedVerificationStatus"] = automated_status
    summary["automatedVerificationTimestamp"] = max(
        (
            document.get("automatedVerificationAt")
            for document in documents
            if document.get("automatedVerificationAt")
        ),
        default=None,
        key=str,
    )
    bank_analyses_by_document: dict[str, dict[str, Any]] = {
        str(document.get("_id") or ""): document["bankStatementAnalysis"]
        for document in documents
        if isinstance(document.get("bankStatementAnalysis"), dict)
    }
    if latest:
        for check in latest.get("results") or []:
            for entry in check.get("bankStatements") or []:
                document_id = str(entry.get("documentId") or "")
                analysis = entry.get("analysis")
                if document_id and isinstance(analysis, dict):
                    # Verification results are historical snapshots; source-review
                    # updates are persisted on the current document.
                    bank_analyses_by_document.setdefault(document_id, analysis)
    saved_financial_statuses = {
        str(analysis.get("financialRequirementStatus") or "").upper()
        for analysis in bank_analyses_by_document.values()
    }
    summary["bankStatementAnalysisStatus"] = (
        "FAIL"
        if "FAIL" in saved_financial_statuses
        else "INSUFFICIENT_DATA"
        if "INSUFFICIENT_DATA" in saved_financial_statuses
        else "BELOW_MINIMUM"
        if "BELOW_MINIMUM" in saved_financial_statuses
        else "PASS"
        if "PASS" in saved_financial_statuses
        else latest.get("bankStatementAnalysisStatus", "NOT_AVAILABLE")
        if latest
        else "NOT_AVAILABLE"
    )
    requirements_by_id = {
        str(item.get("requirementId") or item.get("_id") or ""): item
        for item in requirements
    }
    documents_by_id = {str(item.get("_id") or ""): item for item in documents}
    for document in documents:
        document_id = str(document.get("_id") or "")
        requirement = requirements_by_id.get(
            str(document.get("requirementId") or ""),
            {},
        )
        requirement_code = str(
            document.get("requirementCode")
            or requirement.get("code")
            or requirement.get("requirementCode")
            or ""
        ).casefold()
        requirement_name = str(
            requirement.get("name") or document.get("requirementName") or ""
        ).casefold()
        is_bank_document = (
            "bank_statement" in requirement_code
            or "fixed_deposit" in requirement_code
            or ("bank" in requirement_code and "statement" in requirement_code)
            or ("bank" in requirement_name and "statement" in requirement_name)
        )
        analysis = bank_analyses_by_document.get(document_id)
        if analysis is None and not is_bank_document:
            continue
        document = documents_by_id.get(document_id)
        if document:
            requirement = requirements_by_id.get(
                str(document.get("requirementId") or "")
            )
            bank_analyses_by_document[document_id] = {
                **(analysis or {}),
                **calculate_bank_score_assessment(
                    document,
                    analysis or {},
                    customer,
                    requirement_maximum=_requirement_points(
                        requirement or {}, requirements
                    ),
                ),
            }
    if not policy_snapshot(application):
        requirement_scores = summary.get("requirementScores") or []
        missing_required = summary.get("missingRequiredDocuments") or []
        manual_review_required = any(
            summary.get(field, 0)
            for field in (
                "processingDocuments",
                "needsReviewDocuments",
                "insufficientDataDocuments",
            )
        )
        summary["readinessScore"] = {
            "score": summary.get("documentAssessmentScore", 0),
            "earnedPoints": summary.get("earnedPoints", 0),
            "maximum": summary.get("maximumPoints", 0),
            "riskLevel": "NOT_ASSESSED",
            "manualReviewRequired": manual_review_required,
            "reviewFlags": [
                item.get("name") or item.get("requirementId")
                for item in requirement_scores
                if item.get("status") in {"NEEDS_REVIEW", "INSUFFICIENT_DATA"}
            ],
            "gateApplied": bool(missing_required),
            "missingRequiredDocuments": [
                item.get("name") or "Required document"
                for item in missing_required
            ],
            "components": {
                item.get("requirementId") or str(index): {
                    "label": item.get("name") or "Requirement",
                    "points": item.get("earnedPoints", 0),
                    "maximum": item.get("configuredPoints", 0),
                    "status": item.get("status"),
                }
                for index, item in enumerate(requirement_scores)
            },
            "recommendations": (
                ["Complete required document review before finalizing."]
                if missing_required or manual_review_required
                else []
            ),
            "disclaimer": "This percentage is the earned document requirement contribution divided by the configured maximum; it is not an approval probability.",
        }
    summary["bankStatementScore"] = [
        {
            "documentId": document_id,
            "score": analysis.get("bankStatementScore"),
            "bankDiagnosticScore": analysis.get("bankStatementScore"),
            "documentScore": analysis.get("documentScore"),
            "computedBankScore": analysis.get("computedBankScore"),
            "confirmedBankScore": analysis.get("confirmedBankScore"),
            "requirementContribution": analysis.get("requirementContribution"),
            "staffReviewStatus": analysis.get("staffReviewStatus"),
            "sourceReviewStatus": analysis.get("sourceReviewStatus"),
            "documentReviewStatus": analysis.get("documentReviewStatus"),
            "authenticityStatus": analysis.get("authenticityStatus"),
            "financialEvidenceStatus": analysis.get("financialEvidenceStatus"),
            "financialAssessmentStatus": analysis.get("financialAssessmentStatus"),
            "balanceReconciliationStatus": analysis.get("balanceReconciliationStatus"),
            "monthlyBalanceVerification": analysis.get("monthlyBalanceVerification"),
            "bankRequirementPointsEarned": bank_requirement_points(
                analysis,
                _requirement_points(
                    requirements_by_id.get(
                        str(documents_by_id.get(document_id, {}).get("requirementId") or ""),
                    ),
                    requirements,
                ),
                str(documents_by_id.get(document_id, {}).get("status") or "").upper(),
            ),
            "bankRequirementPointsMaximum": _requirement_points(
                requirements_by_id.get(
                    str(documents_by_id.get(document_id, {}).get("requirementId") or ""),
                ),
                requirements,
            ),
            "financialRequirementStatus": analysis.get("financialRequirementStatus"),
            "minimumBalanceAssessment": analysis.get("minimumBalanceAssessment"),
            "threeMonthCoverage": analysis.get("statementPeriod"),
            "monthlyBalanceChecks": analysis.get("monthlyBalanceChecks") or [],
            "missingFinancialFields": analysis.get("missingEvidence") or [],
            "transactionExtraction": {
                "status": analysis.get("extractionStatus"),
                "count": analysis.get("transactionCount", 0),
                "invalidCount": analysis.get("invalidTransactionCount", 0),
            },
            "accountHolderMatch": analysis.get("accountHolderCheck"),
        }
        for document_id, analysis in bank_analyses_by_document.items()
    ]
    if policy_snapshot(application):
        for diagnostic in summary.get("bankStatementScore",[]):
            diagnostic["bankRequirementPointsEarned"]=None
            diagnostic["legacyDiagnosticOnly"]=True
    summary["abaBankStatements"]=[{"documentId":str(d.get("_id") or ""),"selected":str(d.get("_id") or "")==str(application.get("selectedBankDocumentId") or ""),"analysis":redact_sensitive_data(d.get("abaStatementAnalysis"))} for d in documents if isinstance(d.get("abaStatementAnalysis"),dict)]
    latest_document_bank_analyses = bank_analyses_by_document
    serialized_application = public_document(application)
    report = application.get("verificationReport")
    receipt_current=await _receipt_is_current(application,customer,requirements,documents,database)
    historical_receipts=list(application.get("receiptHistory") or [])
    last_receipt=_receipt_summary(application)
    if last_receipt and not receipt_current and not any(item.get("receiptNumber")==last_receipt["receiptNumber"] for item in historical_receipts):
        historical_receipts.append(last_receipt)
    checked_prediction = await _checked_prediction(application, customer, requirements, documents, database)
    # History is exposed only after _load_context has enforced case authorization.
    # Saved ledgers remain unchanged in storage; apply redaction to a response copy.
    known_account_numbers=set()
    for document in documents:
        fields=document.get("extractedFields")
        if isinstance(fields,dict):
            known_account_numbers.update(str(fields[key]) for key in ("accountNumber","accountNo") if fields.get(key))
    history_allowed={"previousPolicyVersionId","previousPolicySnapshot","previousAssessment","previousAssessmentOrigin","previousAssessmentNote","policyVersionId","assessment","reason","reviewedBy","createdAt"}
    private_history_keys={"password","passwordhash","passwordhashes","verificationcode","storagepath","receiptfilepath","ocrtext","rawtext","token","accesstoken","refreshtoken"}
    def safe_history_value(value):
        if isinstance(value,dict):
            return {key:(_mask_passport(item) if re.sub(r"[^a-z]","",str(key).casefold())=="passportnumber" else safe_history_value(item)) for key,item in value.items() if re.sub(r"[^a-z]","",str(key).casefold()) not in private_history_keys}
        if isinstance(value,list):return [safe_history_value(item) for item in value]
        return value
    assessment_history=redact_sensitive_data([
        safe_history_value({key:entry[key] for key in history_allowed if key in entry})
        for entry in application.get("assessmentHistory") or [] if isinstance(entry,dict)
    ],known_account_numbers)
    return JSONResponse(
        content=jsonable_encoder(
            {
                "application": {
                    "id": str(application["_id"]),
                    "visaTypeId": str(application.get("visaTypeId") or application.get("visaType") or ""),
                    "policyVersionId": application.get("policyVersionId"),
                    "selectedBankDocumentId": application.get("selectedBankDocumentId"),
                    "applicationNumber": application.get("applicationNumber") or "",
                    "status": application.get("status") or "DRAFT",
                    "destination": application.get("destination")
                    or visa_type.get("destination")
                    or "",
                    "visaTypeName": visa_type.get("name") or "",
                    "createdAt": serialized_application.get("createdAt"),
                    "updatedAt": serialized_application.get("updatedAt"),
                },
                "applicant": {
                    "fullName": customer.get("fullName") or "",
                    "passportNumber": _mask_passport(customer.get("passportNumber")),
                    "nationality": customer.get("nationality") or "",
                    "dateOfBirth": customer.get("dateOfBirth") or "",
                },
                "requirements": [
                    {
                        **{key:item.get(key) for key in ("scoringMode","minimumEarnedPoints","maximumEarnedPoints","qualityScorer","qualityScorerVersion","mappingVersion","alternativeGroupId","requiredForFinalAssessment","qualityRubric")},
                        "requirementId": str(
                            item.get("requirementId") or item.get("_id") or ""
                        ),
                        "name": item.get("name") or "Requirement",
                        "required": item.get("required", True),
                        "conditional": item.get("conditional", False),
                        "points": item.get(
                            "maximumEarnedPoints",
                            item.get("weight", item.get("points", 0)),
                        ),
                    }
                    for item in requirements
                ],
                "summary": summary,
                "assessmentHistory": assessment_history,
                "documents": [
                    _public_document_result(
                        document,
                        requirements,
                        latest_document_bank_analyses.get(
                            str(document.get("_id") or "")
                        ),
                    )
                    for document in documents
                ],
                "latestVerification": public_document(latest) if latest else None,
                "prediction": checked_prediction,
                "verificationReport": report if receipt_current else None,
                "receiptNumber": application.get("receiptNumber") if receipt_current else None,
                "receiptGeneratedAt": application.get("receiptGeneratedAt") if receipt_current else None,
                "receiptIsCurrent":receipt_current,
                "receiptStatus":"CURRENT" if receipt_current else "HISTORICAL" if last_receipt else "NONE",
                "assessmentNeedsRefinalization":bool(last_receipt and not receipt_current),
                "historicalReceipts":historical_receipts,
            }
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{application_id}/predict")
async def predict_application(
    application_id: str,
    request: Request,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    application, customer, _visa_type, requirements, documents = await _load_context(
        application_id, database, principal
    )
    app_id = str(application["_id"])
    summary = canonical_summary(application, requirements, documents)
    requirement_by_id = {
        str(item.get("requirementId") or item.get("_id") or ""): item
        for item in requirements
        if isinstance(item, dict)
    }
    pending_ocr = [
        item
        for item in documents
        if not is_photo_document(
            item,
            requirement_by_id.get(str(item.get("requirementId") or "")),
        )
        and not has_completed_ocr(item)
    ]
    if pending_ocr:
        return JSONResponse(
            status_code=409,
            content={"message": "All uploaded documents must finish OCR before prediction."},
        )
    unevaluated = [
        item
        for item in documents
        if str(item.get("status") or "").upper()
        not in {
            "VERIFIED",
            "REJECTED",
            "NEEDS_REVIEW",
            "SUSPICIOUS",
            "FAILED",
            "INSUFFICIENT_DATA",
        }
    ]
    if unevaluated:
        return JSONResponse(
            status_code=409,
            content={
                "message": (
                    "Automated verification must finish for every uploaded document "
                    "before running a prediction."
                )
            },
        )
    if summary["missingRequiredDocuments"]:
        return JSONResponse(
            status_code=409,
            content={"message": "Upload all required documents before running a prediction."},
        )

    if policy_snapshot(application) and (not summary.get("complete") or summary.get("gates")):
        return JSONResponse(status_code=409, content={"prediction":{"status":"INSUFFICIENT_DATA","approvalProbability":None,"message":"Resolve all required evidence gates before prediction."},"gates":summary.get("gates",[])})
    if summary.get("verificationPercentage",0) < 100:
        return JSONResponse(status_code=409, content={"prediction":{"status":"INSUFFICIENT_DATA","approvalProbability":None,"message":"Required evidence must be verified before prediction."}})
    fingerprint,active_model_version,financial_policy=await _prediction_context(application,customer,requirements,documents,database)

    lock_result = await database["visaapplications"].update_one(
        {"_id": application["_id"], "predictionInProgress": {"$ne": True}},
        {"$set": {"predictionInProgress": True}},
    )
    if not lock_result.matched_count:
        return JSONResponse(
            status_code=409,
            content={"message": "A prediction is already in progress"},
        )
    try:
        current = await database["visaapplications"].find_one(id_query(app_id))
        if current and current.get("predictionStatus") == "COMPLETED" and current.get("predictionInputFingerprint")==fingerprint and current.get("predictionModelVersion")==active_model_version:
            return JSONResponse(
                content={"prediction": _prediction_from_application(current)}
            )
        features = _build_visa_features(application, customer, documents, summary, financial_policy)
        now = datetime.now(timezone.utc)
        unavailable_message = ""
        try:
            model_response = await run_visa_model_prediction(
                {
                    "model_type": "visa",
                    "features": features,
                    "application_id": app_id,
                    "input_fingerprint":fingerprint,
                    "feature_schema_version":features.get("_feature_schema_version"),
                    "policy_version_id":application.get("policyVersionId"),
                },
                database,
                principal,
            )
            model_payload = json.loads(model_response.body.decode("utf-8"))
            if model_response.status_code != 200:
                unavailable_message = str(
                    model_payload.get("message")
                    or "Prediction unavailable. No validated active model is available."
                )
        except APIError as error:
            unavailable_message = error.message
            model_payload = {}

        model_result = model_payload.get("result") if not unavailable_message else None
        raw_probability = (
            _numeric(model_result.get("visa_approval_probability"))
            if isinstance(model_result, dict)
            else None
        )
        model_version = model_payload.get("model_version")
        if (
            not unavailable_message
            and (raw_probability is None or not 0 <= raw_probability <= 1)
        ):
            unavailable_message = "Prediction unavailable. The active model returned an invalid calibrated probability."
        if unavailable_message:
            prediction_id = str(uuid4())
            attempt = {
                "_id": prediction_id,
                "prediction_id": prediction_id,
                "model_type": "visa",
                "application_id": app_id,
                "prediction_available": False,
                "status": "UNAVAILABLE",
                "message": unavailable_message[:1000],
                "created_by": principal["id"],
                "created_at": now,
            }
            await database["predictions"].insert_one(attempt)
            await _audit(
                database,
                principal["id"],
                "application_prediction_unavailable",
                {"application_id": app_id, "message": unavailable_message[:300]},
            )
            history = list(application.get("predictionHistory") or [])
            history.append(
                {
                    "predictionId": prediction_id,
                    "status": "UNAVAILABLE",
                    "createdAt": now,
                }
            )
            await database["visaapplications"].update_one(
                {"_id": application["_id"]},
                {
                    "$set": {
                        "predictionStatus": "UNAVAILABLE",
                        "predictionInputFingerprint":fingerprint,
                        "predictionFeatureSchemaVersion":features.get("_feature_schema_version"),
                        "predictionMessage": unavailable_message[:1000],
                        "approvalProbability": None,
                        "rejectionProbability": None,
                        "approvalProbabilityNormalized": None,
                        "rejectionProbabilityNormalized": None,
                        "riskLevel": None,
                        "predictionModelVersion": None,
                        "predictionTimestamp": now,
                        "predictionHistory": history,
                        "updatedAt": now,
                    }
                },
            )
            return JSONResponse(
                content={
                    "prediction": {
                        "status": "UNAVAILABLE",
                        "approvalProbability": None,
                        "rejectionProbability": None,
                        "approvalProbabilityNormalized": None,
                        "rejectionProbabilityNormalized": None,
                        "probabilityUnit": "percent",
                        "normalizedProbabilityUnit": "0-1",
                        "message": unavailable_message,
                    }
                }
            )

        fresh_app,fresh_customer,_,fresh_requirements,fresh_documents=await _load_context(app_id,database,principal)
        fresh_fingerprint,_,_=await _prediction_context(fresh_app,fresh_customer,fresh_requirements,fresh_documents,database)
        if fresh_fingerprint!=fingerprint:
            await database["visaapplications"].update_one({"_id":application["_id"]},{"$set":{"predictionStatus":"STALE","approvalProbability":None,"rejectionProbability":None,"predictionMessage":"Inputs changed while the prediction was running; result discarded."}})
            return JSONResponse(status_code=409,content={"prediction":{"status":"STALE","approvalProbability":None,"message":"Inputs changed while the prediction was running; recalculate."}})
        approval = round(raw_probability * 100, 2)
        rejection = round(100 - approval, 2)
        positive, risks, recommendations = _prediction_factors(model_result)
        prediction = {
            "status": "COMPLETED",
            "approvalProbability": approval,
            "rejectionProbability": rejection,
            "approvalProbabilityNormalized": raw_probability,
            "rejectionProbabilityNormalized": 1 - raw_probability,
            "probabilityUnit": "percent",
            "normalizedProbabilityUnit": "0-1",
            "riskLevel": _risk_level(rejection / 100),
            "modelVersion": model_version,
            "inputFingerprint":fingerprint,
            "featureSchemaVersion":features.get("_feature_schema_version"),
            "timestamp": now,
            "positiveFactors": positive,
            "riskFactors": risks,
            "recommendations": recommendations,
            "explanation": (
                "Factor directions are model explanations, not causal findings. "
                "Risk bands: LOW <= 30% rejection, MEDIUM > 30% and < 70%, HIGH >= 70%."
            ),
            "disclaimer": model_result.get("disclaimer")
            or "A calibrated model estimate for human review only, not a visa decision or guarantee.",
        }
        history = list(application.get("predictionHistory") or [])
        history.append(
            {
                "predictionId": model_payload.get("prediction_id"),
                "modelVersion": model_version,
                "status": "COMPLETED",
                "createdAt": now,
            }
        )
        await database["visaapplications"].update_one(
            {"_id": application["_id"]},
            {
                "$set": {
                    "predictionStatus": "COMPLETED",
                    "predictionInputFingerprint":fingerprint,
                    "predictionFeatureSchemaVersion":features.get("_feature_schema_version"),
                    "predictionMessage": "",
                    "approvalProbability": approval,
                    "rejectionProbability": rejection,
                    "approvalProbabilityNormalized": raw_probability,
                    "rejectionProbabilityNormalized": 1 - raw_probability,
                    "riskLevel": prediction["riskLevel"],
                    "predictionModelVersion": model_version,
                    "predictionTimestamp": now,
                    "predictionPositiveFactors": positive,
                    "predictionRiskFactors": risks,
                    "predictionRecommendations": recommendations,
                    "predictionExplanation": prediction["explanation"],
                    "predictionDisclaimer": prediction["disclaimer"],
                    "predictionHistory": history,
                    "updatedAt": now,
                }
            },
        )
        await _audit(
            database,
            principal["id"],
            "application_prediction_attached",
            {
                "application_id": app_id,
                "prediction_id": model_payload.get("prediction_id"),
                "model_version": model_version,
            },
        )
        return JSONResponse(content={"prediction": jsonable_encoder(prediction)})
    except PyMongoError as error:
        logger.exception("Could not persist application prediction")
        raise APIError(503, "Prediction could not be saved") from error
    finally:
        await database["visaapplications"].update_one(
            {"_id": application["_id"]},
            {"$unset": {"predictionInProgress": ""}},
        )


@router.post("/{application_id}/finalize")
async def finalize_verification(
    application_id: str,
    payload: dict[str, Any],
    request: Request,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    application, initial_customer, _visa_type, initial_requirements, initial_documents = await _load_context(
        application_id, database, principal
    )
    report_type=str(payload.get("reportType") or "EVIDENCE_ASSESSMENT").upper()
    if report_type not in {"EVIDENCE_ASSESSMENT","PREDICTION"}:
        raise APIError(400,"reportType must be EVIDENCE_ASSESSMENT or PREDICTION")
    previous_receipt=_receipt_summary(application)
    current_receipt=await _receipt_is_current(application,initial_customer,initial_requirements,initial_documents,database)
    previous_report=application.get("verificationReport") or {}
    if previous_receipt and current_receipt and previous_report.get("reportType")==report_type and not payload.get("createNewVersion"):
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "report": application["verificationReport"],
                    "receiptNumber": application["receiptNumber"],
                    "receiptGeneratedAt": application.get("receiptGeneratedAt"),
                }
            )
        )
    if application.get("status") in {"VERIFIED", "REJECTED"} and not previous_receipt:
        return JSONResponse(
            status_code=409,
            content={"message": "Finalized application has no generated receipt"},
        )

    final_status = str(payload.get("status") or "VERIFIED").strip().upper()
    comments = str(payload.get("comments") or "").strip()
    revision_reason=str(payload.get("revisionReason") or comments).strip()
    if previous_receipt and len(revision_reason)<8:
        raise APIError(400,"A report revision reason of at least 8 characters is required; historical receipts are preserved")
    if final_status not in {"VERIFIED", "REJECTED"}:
        return JSONResponse(
            status_code=400,
            content={"message": "Final status must be VERIFIED or REJECTED"},
        )
    if len(comments) > 2000 or (final_status == "REJECTED" and not comments):
        return JSONResponse(
            status_code=400,
            content={"message": "A rejection requires comments (up to 2000 characters)"},
        )
    lock_result = await database["visaapplications"].update_one(
        {"_id": application["_id"], "verificationFinalizing": {"$ne": True}},
        {"$set": {"verificationFinalizing": True}},
    )
    if not lock_result.matched_count:
        return JSONResponse(
            status_code=409,
            content={"message": "Final verification is already in progress"},
        )

    receipt_path: Path | None = None
    finalized = False
    try:
        application, customer, visa_type, requirements, documents = await _load_context(
            application_id, database, principal
        )
        requirement_by_id = {
            str(item.get("requirementId") or item.get("_id") or ""): item
            for item in requirements
            if isinstance(item, dict)
        }
        active_incomplete = [
            item
            for item in documents
            if not is_photo_document(
                item,
                requirement_by_id.get(str(item.get("requirementId") or "")),
            )
            and not has_completed_ocr(item)
        ]
        if active_incomplete:
            return JSONResponse(
                status_code=409,
                content={"message": "Every uploaded document must finish OCR before final verification."},
            )
        unevaluated = [
            item
            for item in documents
            if str(item.get("status") or "").upper()
            not in {
                "VERIFIED",
                "REJECTED",
                "NEEDS_REVIEW",
                "SUSPICIOUS",
                "FAILED",
                "INSUFFICIENT_DATA",
            }
        ]
        if unevaluated:
            return JSONResponse(
                status_code=409,
                content={
                    "message": (
                        "Automated verification must finish for every uploaded "
                        "document before finalization."
                    )
                },
            )
        if report_type=="PREDICTION" and (await _checked_prediction(application,customer,requirements,documents,database)).get("status")!="COMPLETED":
            return JSONResponse(
                status_code=409,
                content={"message": "Run the visa prediction first, or record that prediction is unavailable."},
            )
        latest = await _latest_verification(database, str(application["_id"]))
        if not latest:
            return JSONResponse(
                status_code=409,
                content={"message": "Run document verification before finalizing this application."},
            )
        not_applicable = {
            str(item.get("requirementId") or "")
            for item in latest.get("results") or []
            if item.get("conditional") and item.get("status") == "NOT_APPLICABLE"
        }
        summary = canonical_summary(application, requirements, documents, not_applicable)
        assessment_fingerprint = prediction_fingerprint(application, customer, requirements, documents)
        if policy_snapshot(application) and latest.get("assessmentContextFingerprint") != assessment_fingerprint:
            return JSONResponse(status_code=409, content={"message":"Evidence changed since the last staff verification. Run verification again before finalizing."})
        bank_analyses_by_document: dict[str, dict[str, Any]] = {}
        for check in latest.get("results") or []:
            for entry in check.get("bankStatements") or []:
                document_id = str(entry.get("documentId") or "")
                analysis = entry.get("analysis")
                if document_id and isinstance(analysis, dict):
                    bank_analyses_by_document[document_id] = analysis
        if policy_snapshot(application) and final_status=="VERIFIED" and (not summary.get("complete") or summary.get("gates")):
            return JSONResponse(status_code=409,content={"message":"Required evidence gates must pass before final assessment.","gates":summary.get("gates",[])})
        summary["bankStatementAnalysisStatus"] = latest.get(
            "bankStatementAnalysisStatus", "NOT_AVAILABLE"
        )
        final_documents_by_id = {
            str(document.get("_id") or ""): document for document in documents
        }
        for document_id, analysis in list(bank_analyses_by_document.items()):
            document = final_documents_by_id.get(document_id)
            if document:
                bank_analyses_by_document[document_id] = {
                    **analysis,
                    **calculate_bank_score_assessment(
                        document,
                        analysis,
                        customer,
                        requirement_maximum=_requirement_points(
                            requirements_by_id.get(
                                str(document.get("requirementId") or ""),
                                {},
                            ),
                            requirements,
                        ),
                    ),
                }
        summary["bankStatementScore"] = [
            {
                "documentId": document_id,
                "score": analysis.get("bankStatementScore"),
                **(
                    calculate_bank_score_assessment(
                        final_documents_by_id[document_id],
                        analysis,
                        customer,
                        requirement_maximum=_requirement_points(
                            requirements_by_id.get(
                                str(
                                    final_documents_by_id[document_id].get(
                                        "requirementId"
                                    )
                                    or ""
                                ),
                                {},
                            ),
                            requirements,
                        ),
                    )
                    if document_id in final_documents_by_id
                    else {}
                ),
                "financialRequirementStatus": analysis.get(
                    "financialRequirementStatus"
                ),
                "minimumBalanceAssessment": analysis.get(
                    "minimumBalanceAssessment"
                ),
                "monthlyBalanceChecks": analysis.get("monthlyBalanceChecks") or [],
            }
            for document_id, analysis in bank_analyses_by_document.items()
        ]
        if final_status == "VERIFIED" and not policy_snapshot(application) and (
            summary["missingRequiredDocuments"]
            or summary["verificationPercentage"] < 100
            or any(
                str(document.get("status") or "").upper() == "REJECTED"
                and str(document.get("requirementId") or "")
                not in {
                    str(item.get("requirementId") or "")
                    for item in requirements
                    if not item.get("required", True)
                }
                for document in documents
            )
        ):
            return JSONResponse(
                status_code=409,
                content={"message": "All required documents must be verified before an application can be finalized as verified."},
            )

        now = datetime.now(timezone.utc)
        receipt_number = f"VDR-{now:%Y%m%d}-{uuid4().hex[:10].upper()}"
        verification_token = secrets.token_urlsafe(32)
        public_api_base = os.getenv(
            "PUBLIC_API_URL", f"{str(request.base_url).rstrip('/')}/api"
        ).rstrip("/")
        verification_url = (
            f"{public_api_base}/verification/receipts/verify/{verification_token}"
        )
        user = await database["users"].find_one(id_query(principal["id"]))
        verified_by = (
            (user or {}).get("fullName")
            or (user or {}).get("name")
            or principal.get("email")
            or principal["id"]
        )
        document_results = [
            _public_document_result(
                document,
                requirements,
                bank_analyses_by_document.get(str(document.get("_id") or "")),
            )
            for document in documents
        ]
        safe_customer = {
            "fullName": customer.get("fullName") or "",
            "passportNumber": _mask_passport(customer.get("passportNumber")),
            "nationality": customer.get("nationality") or "",
        }
        safe_application = {
            "applicationId": str(application["_id"]),
            "applicationNumber": application.get("applicationNumber") or "",
            "destination": application.get("destination") or visa_type.get("destination") or "",
            "visaTypeName": visa_type.get("name") or "",
            "applicationDate": application.get("createdAt"),
        }
        final_prediction=await _checked_prediction(application,customer,requirements,documents,database)
        if report_type=="EVIDENCE_ASSESSMENT" and final_prediction.get("status")!="COMPLETED":
            final_prediction={"status":"UNAVAILABLE","approvalProbability":None,"rejectionProbability":None,"approvalProbabilityNormalized":None,"rejectionProbabilityNormalized":None,"probabilityUnit":"percent","normalizedProbabilityUnit":"0-1","message":"Evidence-assessment report only. No current validated approval estimate is available."}
        report = {
            "reportType":report_type,
            "receiptVersion":int(application.get("receiptVersion") or (1 if previous_receipt else 0))+1,
            "supersedesReceiptNumber":previous_receipt.get("receiptNumber") if previous_receipt else None,
            "revisionReason":revision_reason if previous_receipt else None,
            "assessmentContextFingerprint":assessment_fingerprint,
            "policyVersionId":application.get("policyVersionId"),
            "evidencePolicySnapshot":policy_snapshot(application),
            "receiptNumber": receipt_number,
            "receiptGeneratedAt": now,
            "verificationUrl": verification_url,
            "applicant": safe_customer,
            "application": safe_application,
            "summary": summary,
            "prediction": final_prediction,
            "documents": document_results,
            "verification": public_document(latest),
            "verifiedBy": str(verified_by),
            "verifiedById": principal["id"],
            "verifiedAt": now,
            "finalStatus": final_status,
            "administratorComments": comments,
        }
        pdf_bytes = await asyncio.to_thread(
            create_receipt_pdf,
            report,
            documents,
            verification_url,
        )
        fresh_application, fresh_customer, _, fresh_requirements, fresh_documents = await _load_context(application_id, database, principal)
        if prediction_fingerprint(fresh_application, fresh_customer, fresh_requirements, fresh_documents) != assessment_fingerprint:
            return JSONResponse(status_code=409, content={"message":"Evidence changed while the report was being generated. Re-run verification before finalizing."})
        # Preserve the old receipt before replacing the application's current
        # pointer. This never changes its PDF, snapshot or verification token.
        if previous_receipt:
            await _archive_receipt(database,application)
        _RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
        receipt_path = _RECEIPT_DIR / f"{receipt_number}.pdf"
        await asyncio.to_thread(receipt_path.write_bytes, pdf_bytes)
        await _audit(
            database,
            principal["id"],
            "application_verification_finalized",
            {
                "application_id": str(application["_id"]),
                "receipt_number": receipt_number,
                "final_status": final_status,
                "verification_id": _latest_verification_id(
                    str(application["_id"]), latest
                ),
            },
        )
        finalize_query = {"_id": application["_id"], "verificationFinalizing": True}
        if fresh_application.get("updatedAt") is not None:
            finalize_query["updatedAt"] = fresh_application["updatedAt"]
        update_result = await database["visaapplications"].update_one(
            finalize_query,
            {
                "$set": {
                    "status": final_status,
                    "documentProcessingStatus": summary["documentProcessingStatus"],
                    "ocrProgress": summary["ocrProgress"],
                    "verificationPercentage": summary["verificationPercentage"],
                    "documentReadiness": summary["documentReadiness"],
                    "verificationMetricsCurrent": True,
                    "approvalProbability": (
                        application.get("approvalProbability")
                        if final_prediction.get("status") == "COMPLETED"
                        else None
                    ),
                    "rejectionProbability": (
                        application.get("rejectionProbability")
                        if final_prediction.get("status") == "COMPLETED"
                        else None
                    ),
                    "approvalProbabilityNormalized": final_prediction.get("approvalProbabilityNormalized"),
                    "rejectionProbabilityNormalized": final_prediction.get("rejectionProbabilityNormalized"),
                    "riskLevel": (
                        application.get("riskLevel")
                        if final_prediction.get("status") == "COMPLETED"
                        else None
                    ),
                    "predictionModelVersion": application.get(
                        "predictionModelVersion"
                    ),
                    "verificationReport": report,
                    "receiptNumber": receipt_number,
                    "receiptGeneratedAt": now,
                    "receiptInputFingerprint":assessment_fingerprint,
                    "receiptVersion":report["receiptVersion"],
                    "receiptVerificationToken": verification_token,
                    "receiptFilePath": str(receipt_path),
                    "verifiedBy": str(verified_by),
                    "verifiedById": principal["id"],
                    "verifiedAt": now,
                    "administratorComments": comments,
                    "verificationFinalizing": False,
                    "updatedAt": now,
                },
                **({"$push":{"receiptHistory":{**previous_receipt,"supersededAt":now,"supersededBy":receipt_number,"revisionReason":revision_reason}}} if previous_receipt else {}),
            },
        )
        if not update_result.matched_count:
            return JSONResponse(
                status_code=409,
                content={"message": "Final verification could not be saved"},
            )
        finalized = True
        # Append-only archive is authoritative for historical receipt routes;
        # the current application remains a compatibility fallback if this
        # final archive write must be retried after a database interruption.
        await _archive_receipt(database,{**application,"verificationReport":report,"receiptNumber":receipt_number,"receiptGeneratedAt":now,"receiptVerificationToken":verification_token,"receiptFilePath":str(receipt_path)})
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "report": report,
                    "receiptNumber": receipt_number,
                    "receiptGeneratedAt": now,
                }
            )
        )
    except APIError:
        raise
    except PyMongoError as error:
        logger.exception("Could not finalize verification")
        raise APIError(503, "Final verification could not be saved") from error
    finally:
        if not finalized and receipt_path is not None:
            receipt_path.unlink(missing_ok=True)
        await database["visaapplications"].update_one(
            {"_id": application["_id"]},
            {"$unset": {"verificationFinalizing": ""}},
        )


@router.get("/receipts/{receipt_number}")
async def get_receipt(
    receipt_number: str,
    request: Request,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF", "CUSTOMER")),
) -> JSONResponse:
    try:
        application,record = await _receipt_record(database,number=receipt_number)
    except PyMongoError as error:
        logger.exception("Could not load verification receipt")
        raise APIError(503, "Receipt is unavailable") from error
    if not application or application.get("deletedAt") or not record:
        raise APIError(404, "Receipt not found")
    owner_id = str(application.get("customer") or application.get("customerId") or "")
    if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
        raise APIError(404, "Receipt not found")
    report = record["report"]
    current_context=await _load_context(str(application["_id"]),database,principal)
    receipt_current=str(application.get("receiptNumber"))==receipt_number and await _receipt_is_current(current_context[0],current_context[1],current_context[3],current_context[4],database)
    verification_url = str(report.get("verificationUrl") or "")
    qr_data = create_qr_png(verification_url)
    import base64

    return JSONResponse(
        content=jsonable_encoder(
            {
                "receipt": report,
                "receiptNumber": receipt_number,
                "pdfUrl": f"/verification/receipts/{receipt_number}/pdf",
                "qrCodeDataUrl": f"data:image/png;base64,{base64.b64encode(qr_data).decode('ascii')}",
                "historical":not receipt_current,
                "receiptIsCurrent":receipt_current,
            }
        )
    )


@router.get("/receipts/{receipt_number}/pdf")
async def download_receipt_pdf(
    receipt_number: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF", "CUSTOMER")),
) -> FileResponse:
    try:
        application,record = await _receipt_record(database,number=receipt_number)
    except PyMongoError as error:
        logger.exception("Could not load verification PDF")
        raise APIError(503, "Receipt PDF is unavailable") from error
    if not application or application.get("deletedAt") or not record:
        raise APIError(404, "Receipt not found")
    owner_id = str(application.get("customer") or application.get("customerId") or "")
    if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
        raise APIError(404, "Receipt not found")
    raw_path = record.get("receiptFilePath")
    if not raw_path:
        raise APIError(404, "Receipt PDF is unavailable")
    path = Path(str(raw_path)).resolve()
    try:
        path.relative_to(_RECEIPT_DIR.resolve())
    except ValueError as error:
        logger.error("Rejected receipt path outside private storage: %s", receipt_number)
        raise APIError(404, "Receipt PDF is unavailable") from error
    if not path.is_file():
        raise APIError(404, "Receipt PDF is unavailable")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{receipt_number}.pdf",
        content_disposition_type="attachment",
    )


@public_router.get("/{token}")
async def verify_receipt_token(
    token: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    if not token or len(token) > 128:
        raise APIError(404, "Receipt verification not found")
    try:
        application,record = await _receipt_record(database,token=token)
    except PyMongoError as error:
        logger.exception("Could not verify receipt token")
        raise APIError(503, "Receipt verification is unavailable") from error
    if not application or application.get("deletedAt") or not record:
        raise APIError(404, "Receipt verification not found")
    return JSONResponse(
        content=jsonable_encoder(
            {
                "valid": True,
                "receiptNumber": record["_id"],
                "issuedAt": record.get("receiptGeneratedAt"),
                "finalStatus": (record.get("report") or {}).get("finalStatus"),
                "historical":str(record["_id"])!=str(application.get("receiptNumber")),
                "verificationScope":"Receipt issuance only; not a current case assessment or a visa decision.",
                "message": "This immutable receipt was generated by VisaDoc AI for the evidence and policy recorded at its issue time.",
            }
        )
    )
