from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.errors import APIError
from ..services.document_metrics import summarize_documents
from ..services.assessment_context import application_requirements, canonical_summary, policy_snapshot, prediction_fingerprint
from ..services.photo_validation import is_photo_document
from .document_workflow import has_completed_ocr
from .dependencies import get_mongo_database, require_roles
from .documents import review_document
from .ml import _audit
from .mongo_helpers import id_query, public_document, redact_sensitive_data

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
@router.get("")
async def list_verifications(
    application_id: str | None = Query(default=None, alias="applicationId"),
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(
        require_roles("ADMIN", "STAFF", "CUSTOMER")
    ),
) -> JSONResponse:
    try:
        records = await database["verifications"].find({}).to_list(length=None)
        if principal["role"] == "CUSTOMER":
            owned_applications = await database["visaapplications"].find(
                {"customer": principal["id"]}
            ).to_list(length=None)
            owned_applications += await database["visaapplications"].find(
                {"customerId": principal["id"]}
            ).to_list(length=None)
            allowed_ids = {
                str(item.get("_id"))
                for item in owned_applications
                if item.get("_id") is not None
                and str(item.get("customer") or item.get("customerId") or "")
                == principal["id"]
            }
            records = [
                item
                for item in records
                if str(item.get("applicationId") or "") in allowed_ids
            ]
        if application_id:
            records = [
                item
                for item in records
                if str(item.get("applicationId") or "") == application_id
            ]
    except PyMongoError as error:
        logger.exception("Could not load verification history: %s", error.__class__.__name__)
        raise APIError(503, "Verification history is unavailable") from error
    records.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return JSONResponse(content={"verifications": [public_document(item) for item in records]})


async def _run_profile_verification(application, applicant, requirements, documents, database, principal):
    summary=canonical_summary(application,requirements,documents)
    now=datetime.now(timezone.utc)
    complete=bool(summary.get("complete")) and not summary.get("gates")
    status="VERIFIED" if complete else str(summary.get("status") or "NEEDS_REVIEW")
    if status=="READY_FOR_STAFF_REVIEW":status="VERIFIED" if complete else "NEEDS_REVIEW"
    checks=[]
    for item in summary.get("requirementScores",[]):
        item_status=str(item.get("status") or "NEEDS_REVIEW").upper()
        checks.append({**item,"requirementName":item.get("name") or item.get("requirementName") or "Requirement",
                       "status":"PASS" if item_status in {"VERIFIED","PASS"} else item_status,
                       "explanationEn":item.get("reason") or item.get("explanation") or "See the immutable evidence policy ledger."})
    verification={"_id":str(uuid4()),"applicationId":str(application["_id"]),"verificationMethod":"EVIDENCE_PROFILE",
                  **summary,"status":status,"scoringVersion":"evidence-v3","policyVersionId":application.get("policyVersionId"),
                  "results":checks,"createdAt":now,"updatedAt":now,"bankStatementAnalysisStatus":"SEE_ABA_REVIEW"}
    fingerprint=prediction_fingerprint(application,applicant,requirements,documents)
    verification["assessmentContextFingerprint"] = fingerprint
    # Compare-and-set the evidence revision after the calculation so another
    # concurrent review or replacement cannot be finalized by this stale job.
    original_updated=application.get("updatedAt")
    query={"_id":application["_id"]}
    if original_updated is not None:query["updatedAt"]=original_updated
    changed=application.get("assessmentInputFingerprint")!=fingerprint
    update={"evidenceAssessment":summary,"documentAssessmentScore":summary.get("documentAssessmentScore"),
            "confirmedReadinessPoints":summary.get("confirmedReadinessPoints"),"documentReadiness":summary.get("documentReadiness"),
            "verificationPercentage":summary.get("verificationPercentage",0),"submissionCompleteness":summary.get("submissionCompleteness",0),
            "documentProcessingStatus":summary.get("documentProcessingStatus",status),"assessmentInputFingerprint":fingerprint,
            "assessmentStatus":"READY_FOR_STAFF_REVIEW" if complete else status,"verificationMetricsCurrent":summary.get("processingDocuments",0)==0,
            "predictionStatus":"STALE" if changed and application.get("predictionStatus")=="COMPLETED" else application.get("predictionStatus","UNAVAILABLE"),
            "updatedAt":now}
    if changed and application.get("predictionStatus")=="COMPLETED":update.update(approvalProbability=None,rejectionProbability=None,approvalProbabilityNormalized=None,rejectionProbabilityNormalized=None,predictionMessage="Evidence verification changed. Recalculate prediction.")
    try:
        result=await database["visaapplications"].update_one(query,{"$set":update})
        if not result.matched_count:return JSONResponse(status_code=409,content={"message":"Application changed during verification; recalculate."})
        await database["verifications"].insert_one(verification)
        await _audit(database,principal["id"],"evidence_profile_assessed",{"application_id":str(application["_id"]),"policy_version_id":application.get("policyVersionId"),"input_fingerprint":fingerprint})
    except PyMongoError as error:
        raise APIError(503,"Evidence assessment could not be persisted") from error
    return JSONResponse(content={"verification":public_document(verification)})


@router.post("/run")
async def run_verification(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(
        require_roles("ADMIN", "STAFF", "CUSTOMER")
    ),
) -> JSONResponse:
    application_id = str(payload.get("applicationId") or "")
    if not application_id:
        return JSONResponse(status_code=400, content={"message": "Application ID is required"})
    supplied_exceptions = payload.get("notApplicableRequirements") or {}
    if not isinstance(supplied_exceptions, dict) or any(
        not isinstance(reason, str) for reason in supplied_exceptions.values()
    ):
        return JSONResponse(
            status_code=400,
            content={"message": "Conditional requirement exceptions must include text reasons"},
        )
    exceptions = {
        str(requirement_id): reason.strip()
        for requirement_id, reason in supplied_exceptions.items()
        if reason.strip()
    }
    try:
        application = await database["visaapplications"].find_one(id_query(application_id))
        if not application:
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        owner_id = str(
            application.get("customer") or application.get("customerId") or ""
        )
        if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        live_requirements = await database["visarequirements"].find(
            {"visaTypeId": application.get("visaTypeId"), "status": "ACTIVE"}
        ).to_list(length=None)
        snapshots = application_requirements(
            application,
            live_requirements or application.get("requirementSnapshot") or [],
        )
        documents = await database["documents"].find(
            {
                "applicationId": str(application["_id"]),
                "status": {"$nin": ["DELETED", "REPLACED"]},
            }
        ).to_list(length=None)
        documents = [
            item
            for item in documents
            if item.get("status") not in {"DELETED", "REPLACED"}
        ]
        applicant = (
            await database["customers"].find_one(id_query(owner_id))
            if owner_id
            else {}
        ) or {}
        if application.get("status") in {"VERIFIED", "REJECTED"}:
            receipt=application.get("verificationReport") or {}
            stored=receipt.get("assessmentContextFingerprint") or application.get("receiptInputFingerprint")
            current=prediction_fingerprint(application,applicant,snapshots,documents)
            if not application.get("receiptNumber") or stored==current or principal["role"] not in {"ADMIN","STAFF"}:
                return JSONResponse(status_code=409,content={"message":"Current finalized assessments cannot be re-verified. Staff may reassess a case after its evidence or policy changes."})
    except PyMongoError as error:
        logger.exception("Could not load verification inputs: %s", error.__class__.__name__)
        raise APIError(503, "Verification could not be completed") from error

    if policy_snapshot(application):
        if exceptions:
            return JSONResponse(status_code=409,content={"message":"Published evidence profiles require a reviewed replacement profile or predefined alternative; free-form exceptions cannot shrink the 100-point budget."})
        return await _run_profile_verification(application, applicant, snapshots, documents, database, principal)

    checks: list[dict[str, Any]] = []
    requirement_by_id = {
        str(item.get("requirementId") or item.get("_id") or ""): item
        for item in snapshots
        if isinstance(item, dict)
    }
    documents_requiring_ocr = [
        item
        for item in documents
        if item.get("status") not in {"DELETED", "REPLACED"}
        and not is_photo_document(
            item,
            requirement_by_id.get(str(item.get("requirementId") or "")),
        )
        and not has_completed_ocr(item)
    ]
    if documents_requiring_ocr:
        filenames = [
            str(item.get("fileName") or "Uploaded document")
            for item in documents_requiring_ocr
        ]
        return JSONResponse(
            status_code=409,
            content={
                "message": (
                    "Document reading is incomplete for: "
                    f"{', '.join(filenames)}. Wait for OCR to finish or retry failed "
                    "documents before verification."
                )
            },
        )
    valid_exceptions = {
        str(requirement.get("requirementId")): exceptions[str(requirement.get("requirementId"))]
        for requirement in snapshots
        if requirement.get("conditional")
        and str(requirement.get("requirementId")) in exceptions
    }
    document_summary = summarize_documents(
        snapshots,
        documents,
        set(valid_exceptions),
    )
    requirement_by_id = {
        str(item.get("requirementId") or item.get("_id") or ""): item
        for item in snapshots
        if isinstance(item, dict)
    }
    bank_analyses_by_requirement: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        requirement_id = str(document.get("requirementId") or "")
        requirement = requirement_by_id.get(requirement_id, {})
        requirement_code = str(
            document.get("requirementCode")
            or requirement.get("code")
            or requirement.get("requirementCode")
            or ""
        ).strip().casefold().replace("-", "_").replace(" ", "_")
        requirement_name = str(requirement.get("name") or "").casefold()
        if (
            requirement_code != "bank_statement"
            and "bank statement" not in requirement_name
        ):
            continue
        fields = document.get("extractedFields")
        fields = fields if isinstance(fields, dict) else {}
        account_number = fields.get("accountNumber") or fields.get("accountNo")
        analysis = document.get("bankStatementAnalysis")
        if not isinstance(analysis, dict):
            continue
        analysis_for_report = {
            **analysis,
            "documentInformation": {
            "documentId": str(document.get("_id") or ""),
            "fileName": document.get("fileName"),
            "ocrPageCount": document.get("ocrTotalPages") or 0,
            "transactionPageCount": None,
            },
            "ocrProcessingSummary": {
            "status": str(document.get("ocrStatus") or "OCR_COMPLETED").upper(),
            "pagesProcessed": document.get("ocrPagesProcessed") or 0,
            "totalPages": document.get("ocrTotalPages") or 0,
            "failedPages": document.get("ocrFailedPages") or [],
            "confidence": document.get("ocrConfidence"),
            },
        }
        analysis = redact_sensitive_data(
            analysis_for_report,
            {str(account_number)} if account_number else set(),
        )
        bank_analyses_by_requirement.setdefault(requirement_id, []).append(
            {
                "documentId": str(document.get("_id") or ""),
                "documentReference": document.get("documentReference")
                or f"DOC-{str(document.get('_id') or '').replace('-', '')[:12].upper()}",
                "analysis": analysis,
            }
        )

    scoring = summarize_documents(snapshots, documents, set(valid_exceptions))
    applicable_weight = scoring["maximumPoints"]
    applicable_requirements = [
        requirement
        for requirement in snapshots
        if requirement.get("required", True)
        and str(requirement.get("requirementId")) not in valid_exceptions
    ]
    earned_weight = scoring["earnedPoints"]
    passed_requirements = scoring["verifiedRequiredDocuments"]
    pending_required_documents = False
    for requirement in snapshots:
        requirement_id = str(requirement.get("requirementId") or "")
        if requirement_id in valid_exceptions:
            reason = valid_exceptions[requirement_id]
            checks.append(
                {
                    "requirementId": requirement_id,
                    "requirementName": requirement.get("name", "Requirement"),
                    "conditional": True,
                    "status": "NOT_APPLICABLE",
                    "scoreWeightPercent": requirement.get("weight", 0),
                    "explanationEn": reason,
                    "explanationKh": reason,
                    "documentIds": [],
                }
            )
            continue
        attached = [
            item
            for item in documents
            if str(item.get("requirementId")) == requirement_id
            and item.get("status") not in {"DELETED", "REPLACED"}
        ]
        required = bool(requirement.get("required", True))
        if not attached:
            result_status = "MISSING" if required else "NOT_APPLICABLE"
            explanation = "Required document has not been uploaded." if required else "Optional document was not submitted."
        elif any(str(item.get("status") or "").upper() == "VERIFIED" for item in attached):
            result_status = "PASS"
            explanation = (
                "All configured automated checks passed. This does not establish document authenticity."
            )
            if required:
                # Points were calculated once above by document_metrics. This
                # branch only preserves the legacy verification check payload.
                pass
        elif any(
            str(item.get("status") or "").upper() == "REJECTED"
            for item in attached
        ):
            result_status = "FAIL"
            explanation = "The document failed a defined mandatory automated requirement."
        elif any(
            str(item.get("status") or "").upper()
            in {"NEEDS_REVIEW", "SUSPICIOUS"}
            for item in attached
        ):
            result_status = "NEEDS_REVIEW"
            explanation = "Automated checks found information that conflicts or requires caution."
        elif any(
            str(item.get("status") or "").upper() == "INSUFFICIENT_DATA"
            for item in attached
        ):
            result_status = "INSUFFICIENT_DATA"
            explanation = "Automated checks could not reach a reliable result from the extracted evidence."
        else:
            result_status = "INSUFFICIENT_DATA"
            explanation = "No persisted automated verification result is available for this document."
            if required:
                pending_required_documents = True
        if required and any(
            str(item.get("status") or "").upper()
            not in {
                "VERIFIED",
                "REJECTED",
                "NEEDS_REVIEW",
                "SUSPICIOUS",
                "FAILED",
                "INSUFFICIENT_DATA",
            }
            for item in attached
        ):
            pending_required_documents = True
        bank_statements = bank_analyses_by_requirement.get(requirement_id, [])
        current_requirement_code = str(
            requirement.get("code") or requirement.get("requirementCode") or ""
        ).strip().casefold().replace("-", "_").replace(" ", "_")
        requirement_name = str(requirement.get("name") or "").casefold()
        is_bank_statement_requirement = (
            current_requirement_code == "bank_statement"
            or "bank statement" in requirement_name
        )
        if bank_statements:
            failed_analysis = any(
                item["analysis"]["financialRequirementStatus"] == "FAIL"
                for item in bank_statements
            )
            below_minimum_analysis = any(
                item["analysis"]["financialRequirementStatus"] == "BELOW_MINIMUM"
                for item in bank_statements
            )
            incomplete_analysis = any(
                item["analysis"]["financialRequirementStatus"]
                not in {"PASS", "BELOW_MINIMUM"}
                for item in bank_statements
            )
            if incomplete_analysis:
                explanation += " Automated bank-statement checks found insufficient evidence."
            elif below_minimum_analysis:
                result_status = "NEEDS_REVIEW"
                explanation += (
                    " The complete-period ending balance is below the configured minimum; "
                    "this is a warning requiring manual review, not an automatic failure."
                )
            else:
                explanation += (
                    " The complete-period ending balance meets the configured minimum; "
                    "authenticity is not confirmed."
                )
        else:
            failed_analysis = False
            below_minimum_analysis = False
            incomplete_analysis = False
            if is_bank_statement_requirement:
                result_status = "INSUFFICIENT_DATA"
                explanation = (
                    "Bank statement verification data is unavailable; "
                    "a persisted analysis is required."
                )
                if required:
                    pending_required_documents = True
        checks.append(
            {
                "requirementId": requirement_id,
                "requirementName": requirement.get("name", "Requirement"),
                "conditional": bool(requirement.get("conditional", False)),
                "status": result_status,
                "scoreWeightPercent": requirement.get("weight", 0),
                "explanationEn": explanation,
                "explanationKh": explanation,
                "documentIds": [str(item["_id"]) for item in attached],
                "bankStatements": bank_statements,
                "bankFinancialRequirementStatus": (
                    "FAIL"
                    if failed_analysis
                    else "INSUFFICIENT_DATA"
                    if incomplete_analysis
                    else "BELOW_MINIMUM"
                    if below_minimum_analysis
                    else "PASS"
                    if bank_statements
                    else None
                ),
            }
        )

    completeness = scoring["documentAssessmentScore"]
    # Requirement weights are points, not percentages.  The UI needs the
    # normalized share as well as the raw points so a configuration totalling
    # (for example) 105 points is never presented as "105%".
    requirement_scores_by_id = {
        str(item.get("requirementId") or ""): item
        for item in scoring["requirementScores"]
    }
    for check in checks:
        score = requirement_scores_by_id.get(str(check.get("requirementId") or ""), {})
        configured_points = float(score.get("configuredPoints") or 0)
        earned_points = float(score.get("earnedPoints") or 0)
        check["configuredPoints"] = round(configured_points, 2)
        check["earnedPoints"] = round(earned_points, 2)
        check["scoreWeightPercent"] = (
            round(100 * configured_points / applicable_weight, 2)
            if applicable_weight
            else 0
        )
        check["earnedPercentage"] = (
            round(100 * earned_points / applicable_weight, 2)
            if applicable_weight
            else 0
        )
    verification_percentage = (
        round(100 * passed_requirements / len(applicable_requirements), 2)
        if applicable_requirements
        else 0
    )
    document_summary["verificationPercentage"] = verification_percentage
    document_summary["documentReadiness"] = completeness
    if any(item["status"] == "MISSING" for item in checks):
        overall_status = "INSUFFICIENT_DATA"
    elif any(item["status"] == "FAIL" for item in checks):
        overall_status = "REJECTED"
    elif any(
        item["status"] in {"NEEDS_REVIEW", "SUSPICIOUS"} for item in checks
    ):
        overall_status = "NEEDS_REVIEW"
    elif any(
        item.get("bankFinancialRequirementStatus")
        in {"FAIL", "INSUFFICIENT_DATA"}
        for item in checks
    ):
        overall_status = (
            "REJECTED"
            if any(
                item.get("bankFinancialRequirementStatus") == "FAIL"
                for item in checks
            )
            else "INSUFFICIENT_DATA"
        )
    elif pending_required_documents:
        overall_status = "INSUFFICIENT_DATA"
    else:
        overall_status = "VERIFIED"
    now = datetime.now(timezone.utc)
    verification = {
        "_id": str(uuid4()),
        "applicationId": str(application["_id"]),
        "verificationMethod": "OCR_COMPLETENESS",
        "status": overall_status,
        "completenessScore": completeness,
        "totalPoints": applicable_weight,
        "earnedPoints": earned_weight,
        "verificationPercentage": verification_percentage,
        "bankStatementAnalysisStatus": (
            "FAIL"
            if any(
                item.get("bankFinancialRequirementStatus") == "FAIL"
                for item in checks
            )
            else "INSUFFICIENT_DATA"
            if any(
                item.get("bankFinancialRequirementStatus")
                == "INSUFFICIENT_DATA"
                for item in checks
            )
            else "BELOW_MINIMUM"
            if any(
                item.get("bankFinancialRequirementStatus")
                == "BELOW_MINIMUM"
                for item in checks
            )
            else "PASS"
            if bank_analyses_by_requirement
            else "NOT_AVAILABLE"
        ),
        **document_summary,
        "maximumPoints": scoring["maximumPoints"],
        "earnedPoints": scoring["earnedPoints"],
        "requirementScores": scoring["requirementScores"],
        "lastCalculationAt": now,
        "scoringVersion": "document-requirements-v2",
        "policyVersion": max(
            (item.get("policyVersion") for item in snapshots if item.get("policyVersion")),
            default=None,
        ),
        "results": checks,
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        lock_result = await database["visaapplications"].update_one(
            {
                "_id": application["_id"],
                "verificationInProgress": {"$ne": True},
            },
            {"$set": {"verificationInProgress": True}},
        )
    except PyMongoError as error:
        logger.exception("Could not start verification: %s", error.__class__.__name__)
        raise APIError(503, "Verification could not be started") from error
    if not lock_result.matched_count:
        return JSONResponse(
            status_code=409,
            content={"message": "Verification is already in progress"},
        )
    try:
        await database["verifications"].insert_one(verification)
        for requirement_analyses in bank_analyses_by_requirement.values():
            for entry in requirement_analyses:
                await _audit(
                    database,
                    principal["id"],
                    "application_bank_statement_analyzed",
                    {
                        "application_id": str(application["_id"]),
                        "document_id": entry["documentId"],
                        "status": entry["analysis"]["financialRequirementStatus"],
                    },
                )
        await database["visaapplications"].update_one(
            {"_id": application["_id"]},
            {
                "$set": {
                    "completenessScore": completeness,
                    "verificationPercentage": verification_percentage,
                    "submissionCompleteness": document_summary["submissionCompleteness"],
                    "documentReadiness": completeness,
                    "verificationMetricsCurrent": True,
                    "documentProcessingStatus": document_summary[
                        "documentProcessingStatus"
                    ],
                    "ocrProgress": document_summary["ocrProgress"],
                    "bankStatementAnalysisStatus": verification[
                        "bankStatementAnalysisStatus"
                    ],
                    "updatedAt": now,
                }
            },
        )
    except PyMongoError as error:
        logger.exception("Could not save verification result: %s", error.__class__.__name__)
        raise APIError(503, "Verification result could not be saved") from error
    finally:
        try:
            await database["visaapplications"].update_one(
                {"_id": application["_id"]},
                {"$unset": {"verificationInProgress": ""}},
            )
        except PyMongoError:
            logger.exception(
                "Could not release verification lock: application_id=%s",
                application_id,
            )
    return JSONResponse(content={"verification": public_document(verification)})


@router.put("/{document_id}")
async def update_document_verification(
    document_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    return await review_document(document_id, payload, database, principal)
