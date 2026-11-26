from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.errors import APIError
from ..services.document_metrics import requirement_awarded_points
from .dependencies import get_mongo_database
from .mongo_helpers import id_query, public_document

logger = logging.getLogger(__name__)
router = APIRouter()


def _status_is(status: str, *values: str) -> bool:
    return status.upper() in values


def _as_utc_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


@router.get("/")
@router.get("")
async def dashboard(
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        customers = await database["customers"].find({}).to_list(length=None)
        applications = [
            application
            for application in await database["visaapplications"].find(
                {}
            ).to_list(length=None)
            if not application.get("deletedAt")
        ]
        active_application_ids = {
            str(application["_id"])
            for application in applications
            if application.get("_id") is not None
        }
        documents = [
            document
            for document in await database["documents"].find({}).to_list(
                length=None
            )
            if str(document.get("status") or "").upper()
            not in {"DELETED", "REPLACED"}
            and (
                not document.get("applicationId")
                or str(document["applicationId"])
                in active_application_ids
            )
        ]
        visa_types = await database["visatypes"].find({}).to_list(length=None)

        recent_applications: list[dict[str, Any]] = []
        for application in sorted(
            applications,
            key=lambda item: item.get("createdAt")
            or item.get("updatedAt")
            or "",
            reverse=True,
        )[:10]:
            enriched = public_document(application)
            customer_id = application.get("customer") or application.get(
                "customerId"
            )
            if isinstance(customer_id, dict):
                customer_id = customer_id.get("_id") or customer_id.get("id")
            customer = (
                await database["customers"].find_one(id_query(str(customer_id)))
                if customer_id
                else None
            )
            visa_type_ref = application.get("visaTypeId") or application.get(
                "visaType"
            )
            embedded_visa_type = (
                visa_type_ref if isinstance(visa_type_ref, dict) else {}
            )
            visa_type_id = (
                embedded_visa_type.get("_id") or embedded_visa_type.get("id")
                if embedded_visa_type
                else visa_type_ref
            )
            visa_type = (
                await database["visatypes"].find_one(id_query(str(visa_type_id)))
                if visa_type_id
                else None
            )
            enriched["applicant"] = (
                customer.get("fullName") or customer.get("name")
                if customer
                else application.get("applicant")
                or application.get("fullName", "")
            )
            enriched["visaTypeName"] = (
                visa_type.get("name") or visa_type.get("visaTypeCode")
                if visa_type
                else embedded_visa_type.get("name")
                or embedded_visa_type.get("visaTypeCode")
                or application.get("visaCategory", "")
            )
            enriched["visaTypeCode"] = (
                visa_type.get("visaTypeCode")
                if visa_type
                else embedded_visa_type.get("visaTypeCode")
                or application.get("visaTypeCode", "")
            ) or application.get("visaTypeCode", "")
            requirement_snapshot = application.get("requirementSnapshot") or []
            if not requirement_snapshot:
                requirement_snapshot = await database["visarequirements"].find(
                    {
                        "visaTypeId": application.get("visaTypeId"),
                        "status": "ACTIVE",
                    }
                ).to_list(length=None)
                requirement_snapshot = [
                    {
                        **item,
                        "requirementId": str(item.get("_id") or ""),
                    }
                    for item in requirement_snapshot
                ]
            application_id = str(application.get("_id") or "")
            enriched["documentVerification"] = []
            for document in documents:
                if str(document.get("applicationId") or "") != application_id:
                    continue
                requirement_id = str(document.get("requirementId") or "")
                requirement = next(
                    (
                        item
                        for item in requirement_snapshot
                        if str(item.get("requirementId") or item.get("_id") or "")
                        == requirement_id
                    ),
                    {},
                )
                maximum = requirement.get(
                    "maximumEarnedPoints",
                    requirement.get("weight", requirement.get("points", 0)),
                ) or 0
                score = {
                    "points": requirement_awarded_points(requirement, document),
                    "maximum": maximum,
                }
                enriched["documentVerification"].append(
                    {
                        "documentId": str(document.get("_id") or ""),
                        "requirementName": requirement.get("name")
                        or document.get("requirementName")
                        or document.get("requirementCode")
                        or document.get("fileName")
                        or "Document",
                        "status": str(document.get("status") or "PENDING").upper(),
                        "score": score,
                    }
                )
            recent_applications.append(enriched)
    except PyMongoError as error:
        logger.exception("Could not build dashboard: %s", error.__class__.__name__)
        raise APIError(503, "Dashboard data is unavailable") from error

    statuses = [str(item.get("status") or "").upper() for item in applications]
    scores = [
        float(item["completenessScore"])
        for item in applications
        if isinstance(item.get("completenessScore"), (int, float))
    ]
    verified_documents = sum(
        _status_is(str(item.get("status") or ""), "VERIFIED") for item in documents
    )
    pending_documents = sum(
        _status_is(str(item.get("status") or ""), "PENDING", "PROCESSING")
        for item in documents
    )
    document_status_counts = {
        "totalDocuments": len(documents),
        "processingDocuments": sum(
            _status_is(str(item.get("status") or ""), "PROCESSING")
            or _status_is(
                str(item.get("ocrStatus") or ""),
                "OCR_PENDING",
                "OCR_PROCESSING",
            )
            for item in documents
        ),
        "verifiedDocuments": verified_documents,
        "rejectedDocuments": sum(
            _status_is(str(item.get("status") or ""), "REJECTED")
            for item in documents
        ),
        "needsReviewDocuments": sum(
            _status_is(
                str(item.get("status") or ""),
                "NEEDS_REVIEW",
                "SUSPICIOUS",
            )
            for item in documents
        ),
        "insufficientDataDocuments": sum(
            _status_is(str(item.get("status") or ""), "INSUFFICIENT_DATA")
            for item in documents
        ),
        "failedDocuments": sum(
            _status_is(str(item.get("status") or ""), "FAILED")
            or _status_is(str(item.get("ocrStatus") or ""), "OCR_FAILED")
            for item in documents
        ),
    }
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=29)
    trend_by_day = {
        first_day + timedelta(days=offset): {
            "approved": 0,
            "pending": 0,
            "rejected": 0,
        }
        for offset in range(30)
    }
    approved_statuses = {"VERIFIED", "APPROVED", "FINAL_PASS"}
    rejected_statuses = {"REJECTED", "FINAL_FAIL"}
    finalized_statuses = approved_statuses | rejected_statuses
    for application in applications:
        application_date = _as_utc_date(application.get("createdAt"))
        if application_date not in trend_by_day:
            continue
        status = str(application.get("status") or "").upper()
        bucket = (
            "approved"
            if status in approved_statuses
            else "rejected"
            if status in rejected_statuses
            else "pending"
        )
        trend_by_day[application_date][bucket] += 1

    return JSONResponse(
        content={
            "metrics": {
                **document_status_counts,
                "totalCustomers": len(customers),
                "totalVisaApplications": len(applications),
                "approvedApplications": sum(
                    status in approved_statuses for status in statuses
                ),
                "rejectedApplications": sum(
                    status in rejected_statuses for status in statuses
                ),
                "pendingApplications": sum(
                    status not in finalized_statuses for status in statuses
                ),
                "pendingVerification": sum(
                    status in {"UNDER_VERIFICATION", "FINAL_REVIEW"}
                    for status in statuses
                ),
                "pendingDocuments": pending_documents,
                "completeApplications": sum(score >= 100 for score in scores),
                "incompleteApplications": sum(score < 100 for score in scores),
                "averageReadinessScore": (
                    round(sum(scores) / len(scores), 1) if scores else 0
                ),
            },
            "analytics": {
                "visaTypeDistribution": [
                    {
                        "id": str(item["_id"]),
                        "name": item.get("name") or item.get("visaTypeCode", "Unknown"),
                        "value": sum(
                            str(application.get("visaTypeId") or application.get("visaType"))
                            == str(item.get("_id"))
                            for application in applications
                        ),
                    }
                    for item in visa_types
                    if any(
                        str(application.get("visaTypeId") or application.get("visaType"))
                        == str(item.get("_id"))
                        for application in applications
                    )
                ],
                "applicationTrend": [
                    {
                        "date": day.isoformat(),
                        **counts,
                        "total": sum(counts.values()),
                    }
                    for day, counts in trend_by_day.items()
                ],
            },
            "recentApplications": recent_applications,
            "recentActivities": [],
        }
    )


@router.get("/stats")
async def dashboard_stats(
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    response = await dashboard(database)
    payload = response.body
    import json

    return JSONResponse(content=json.loads(payload).get("metrics", {}))


@router.get("/recent-applications")
async def dashboard_recent_applications(
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    response = await dashboard(database)
    import json

    payload = json.loads(response.body)
    return JSONResponse(content={"applications": payload["recentApplications"]})
