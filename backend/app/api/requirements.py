from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.errors import APIError
from ..services.document_metrics import summarize_documents
from ..services.evidence_scoring import SCORING_FIELDS, normalize_scoring_fields
from .dependencies import get_mongo_database, require_roles
from .mongo_helpers import id_query, public_document, reference_query

logger = logging.getLogger(__name__)
router = APIRouter()

REQUIREMENT_FIELDS = (
    "name",
    "code",
    "description",
    "required",
    "conditional",
    "weight",
    "displayOrder",
    "maxFileSize",
    "source",
    "sourceStatus",
    "effectiveFrom",
    "acceptedFormats",
    "photoRules",
    "allowMultiple",
    "status",
    *SCORING_FIELDS,
)


def _validate(payload: dict[str, Any], creating: bool = False) -> str | None:
    if creating and (not str(payload.get("name") or "").strip() or not payload.get("visaTypeId")):
        return "Requirement name and visa type are required"
    if "name" in payload and not str(payload.get("name") or "").strip():
        return "Requirement name is required"
    if "code" in payload and not str(payload.get("code") or "").strip():
        return "Requirement code is required"
    if creating and not any(field in payload for field in ("maximumEarnedPoints", "weight")):
        return "Maximum points are required"
    for field in ("required", "conditional", "allowMultiple", "requiredForFinalAssessment"):
        if field in payload and not isinstance(payload[field], bool):
            return f"{field} must be true or false"
    points_field = (
        "maximumEarnedPoints"
        if "maximumEarnedPoints" in payload
        else "weight"
        if "weight" in payload
        else None
    )
    if points_field:
        if isinstance(payload[points_field], bool):
            return "Points must be between 0 and 100."
        try:
            weight = float(payload[points_field])
        except (TypeError, ValueError):
            return "Points must be between 0 and 100."
        if (
            not math.isfinite(weight)
            or weight < 0
            or weight > 100
        ):
            return "Points must be between 0 and 100."
        if "weight" in payload and "maximumEarnedPoints" in payload:
            try:
                if float(payload["weight"]) != float(payload["maximumEarnedPoints"]):
                    return "weight and maximumEarnedPoints must match"
            except (TypeError, ValueError):
                return "Points must be between 0 and 100."
    for field in ("displayOrder", "maxFileSize"):
        if field in payload and payload[field] is not None:
            try:
                number = float(payload[field])
            except (TypeError, ValueError):
                return f"{field} must be numeric"
            if not math.isfinite(number):
                return f"{field} must be finite"
            if number < 0:
                return f"{field} cannot be negative"
            if not number.is_integer():
                return f"{field} must be an integer"
    if "acceptedFormats" in payload and (
        not isinstance(payload["acceptedFormats"], list)
        or not payload["acceptedFormats"]
        or any(not isinstance(item, str) for item in payload["acceptedFormats"])
    ):
        return "acceptedFormats must be a non-empty list of file extensions"
    if "acceptedFormats" in payload:
        formats = {
            item.strip().lower().lstrip(".")
            for item in payload["acceptedFormats"]
        }
        if formats - {"pdf", "jpg", "jpeg", "png"}:
            return "Only PDF, JPG, JPEG, and PNG file formats are supported"
    if "status" in payload and str(payload["status"]).upper() not in {
        "ACTIVE",
        "INACTIVE",
        "ARCHIVED",
    }:
        return "Status must be ACTIVE, INACTIVE, or ARCHIVED"
    if "photoRules" in payload:
        rules = payload["photoRules"]
        allowed_rules = {
            "minWidth",
            "minHeight",
            "minSharpness",
            "minBrightness",
            "maxBrightness",
            "faceCenterTolerance",
            "minFaceHeightRatio",
            "manualReviewRequired",
        }
        if not isinstance(rules, dict) or set(rules) - allowed_rules:
            return "photoRules must contain supported applicant photo settings"
        if "manualReviewRequired" in rules and not isinstance(
            rules["manualReviewRequired"], bool
        ):
            return "photoRules.manualReviewRequired must be true or false"
        bounds = {
            "minWidth": (1, 10000),
            "minHeight": (1, 10000),
            "minSharpness": (0, 10000),
            "minBrightness": (0, 255),
            "maxBrightness": (0, 255),
            "faceCenterTolerance": (0, 0.5),
            "minFaceHeightRatio": (0, 1),
        }
        for field, (minimum, maximum) in bounds.items():
            if field not in rules:
                continue
            value = rules[field]
            if isinstance(value, bool):
                return f"photoRules.{field} is out of range"
            try:
                number = float(value)
            except (TypeError, ValueError):
                return f"photoRules.{field} must be numeric"
            if not math.isfinite(number) or not minimum <= number <= maximum:
                return f"photoRules.{field} is out of range"
        min_brightness = float(rules.get("minBrightness", 40))
        max_brightness = float(rules.get("maxBrightness", 220))
        if min_brightness > max_brightness:
            return "photoRules.minBrightness cannot exceed photoRules.maxBrightness"
    code = str(payload.get("code") or "").casefold()
    name = str(payload.get("name") or "").casefold()
    if "photo" in code or "photo" in name or "photograph" in name:
        allowed_photo_formats = {"jpg", "jpeg", "png"}
        if "acceptedFormats" in payload and (
            not payload["acceptedFormats"]
            or {item.lower().lstrip(".") for item in payload["acceptedFormats"]}
            - allowed_photo_formats
        ):
            return "Applicant photos support JPG, JPEG, and PNG only"
    if any(field in payload for field in SCORING_FIELDS) or points_field:
        try:
            # A partial update's range is validated against the stored record below.
            if creating or points_field:
                normalize_scoring_fields(
                    {
                        **payload,
                        **(
                            {"maximumEarnedPoints": payload["weight"]}
                            if "weight" in payload and "maximumEarnedPoints" not in payload
                            else {}
                        ),
                    }
                )
        except ValueError as error:
            return str(error)
    return None


def _public_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    serialized = public_document(requirement)
    if "weight" not in serialized:
        serialized["weight"] = serialized.get("maximumEarnedPoints", serialized.get("points", 0))
    if "maximumEarnedPoints" not in serialized:
        serialized["maximumEarnedPoints"] = serialized.get(
            "weight", serialized.get("points", 0)
        )
    rules = serialized.get("photoRules")
    if isinstance(rules, dict):
        serialized["photoRules"] = {
            key: value for key, value in rules.items() if key != "exactlyOneFace"
        }
    return serialized


async def _write_history(
    database: AsyncDatabase,
    requirement: dict[str, Any],
    action: str,
    admin_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    configuration_version: int,
) -> None:
    now = datetime.now(timezone.utc)
    await database["requirementversions"].insert_one(
        {
            "_id": ObjectId(),
            "requirementId": requirement["_id"],
            "visaTypeId": requirement.get("visaTypeId"),
            "action": action,
            "adminId": admin_id,
            "configurationVersion": configuration_version,
            "previous": previous,
            "current": current,
            "createdAt": now,
            "updatedAt": now,
        }
    )


async def _bump_configuration_version(
    database: AsyncDatabase,
    visa_type_id: Any,
) -> int:
    visa = await database["visatypes"].find_one(id_query(str(visa_type_id)))
    if not visa:
        raise APIError(404, "Visa type not found")
    current = int(visa.get("requirementsConfigurationVersion") or 0)
    version = current + 1
    now = datetime.now(timezone.utc)
    result = await database["visatypes"].update_one(
        {"_id": visa["_id"], "requirementsConfigurationVersion": visa.get("requirementsConfigurationVersion")},
        {"$set": {
            "requirementsConfigurationVersion": version,
            "requirementsConfigurationUpdatedAt": now,
        }},
    )
    if not result.matched_count:
        raise APIError(409, "Requirements changed concurrently; reload and retry")
    return version


def _validate_configuration_total(requirements: list[dict[str, Any]]) -> str | None:
    active = [
        item
        for item in requirements
        if str(item.get("status", "ACTIVE")).upper() == "ACTIVE"
    ]
    try:
        total = math.fsum(
            float(
                item.get(
                    "maximumEarnedPoints",
                    item.get("weight", item.get("points", 0)),
                )
            )
            for item in active
        )
    except (OverflowError, TypeError, ValueError):
        return "Total maximum points must be finite"
    if not math.isfinite(total) or total < 0:
        return "Active maximum points must be a valid total"
    if total > 100:
        remaining_values = []
        for item in active:
            others = math.fsum(
                float(
                    other.get(
                        "maximumEarnedPoints",
                        other.get("weight", other.get("points", 0)),
                    )
                )
                for other in active
                if other is not item
            )
            remaining_values.append(max(0.0, 100 - others))
        remaining_text = f"{min(remaining_values):.2f}".rstrip("0").rstrip(".")
        return (
            "Active requirements total cannot exceed 100 points. "
            f"Remaining points: {remaining_text}"
        )
    return None


def _snapshot_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "requirementId": str(item.get("requirementId") or item.get("_id") or ""),
        "maximumEarnedPoints": item.get(
            "maximumEarnedPoints",
            item.get("weight", item.get("points", 0)),
        ),
    }


@router.get("/")
@router.get("")
async def list_requirements(
    database: AsyncDatabase = Depends(get_mongo_database),
    visa_type_id: str | None = Query(default=None, alias="visaTypeId"),
    country_id: str | None = Query(default=None, alias="countryId"),
    include_archived: bool = Query(default=False, alias="includeArchived"),
) -> JSONResponse:
    try:
        requirements = await database["visarequirements"].find({}).to_list(length=None)
        visa_types = {
            str(item["_id"]): item
            for item in await database["visatypes"].find({}).to_list(length=None)
        }
    except PyMongoError as error:
        logger.exception("Could not load requirements: %s", error.__class__.__name__)
        raise APIError(503, "Requirements are unavailable") from error

    result: list[dict[str, Any]] = []
    for item in requirements:
        if not include_archived and str(item.get("status", "ACTIVE")).upper() != "ACTIVE":
            continue
        if visa_type_id and str(item.get("visaTypeId")) != visa_type_id:
            continue
        if country_id:
            visa_type = visa_types.get(str(item.get("visaTypeId")), {})
            if str(item.get("countryId") or visa_type.get("countryId")) != country_id:
                continue
        serialized = _public_requirement(item)
        serialized["visaTypeId"] = str(item.get("visaTypeId") or "")
        serialized["visaType"] = serialized["visaTypeId"]
        visa_type = visa_types.get(str(item.get("visaTypeId")), {})
        serialized["visaTypeCode"] = visa_type.get("visaTypeCode")
        serialized["active"] = str(item.get("status", "ACTIVE")).upper() == "ACTIVE"
        result.append(serialized)
    result.sort(key=lambda item: (item.get("displayOrder", 0), item.get("name", "")))
    return JSONResponse(content={"requirements": result, "count": len(result)})


@router.get("/visa-type/{visa_type_id}")
async def list_requirements_for_visa_type(
    visa_type_id: str,
    include_archived: bool = Query(default=False, alias="includeArchived"),
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    return await list_requirements(
        database=database,
        visa_type_id=visa_type_id,
        country_id=None,
        include_archived=include_archived,
    )


@router.post("/", status_code=201)
@router.post("", status_code=201)
async def create_requirement(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    message = _validate(payload, creating=True)
    if message:
        return JSONResponse(status_code=400, content={"message": message})
    try:
        visa_type = await database["visatypes"].find_one(id_query(str(payload["visaTypeId"])))
        if not visa_type:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        name = str(payload["name"]).strip()
        code = str(
            payload.get("code")
            or re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        ).strip().casefold()
        existing = await database["visarequirements"].find(
            reference_query("visaTypeId", str(visa_type["_id"]))
        ).to_list(length=None)
        duplicate = any(
            str(item.get("status", "ACTIVE")).upper() != "ARCHIVED"
            and (
                str(item.get("name") or "").strip().casefold() == name.casefold()
                or str(item.get("code") or "").strip().casefold() == code
            )
            for item in existing
        )
        if duplicate:
            return JSONResponse(
                status_code=409,
                content={"message": "Requirement name and code must be unique for this visa type"},
            )
        maximum = float(
            payload.get("maximumEarnedPoints", payload.get("weight", 0))
        )
        candidate = {
            "name": name,
            "code": code,
            "weight": maximum,
            "maximumEarnedPoints": maximum,
            "status": str(payload.get("status") or "ACTIVE").upper(),
        }
        total_error = _validate_configuration_total([*existing, candidate])
        if total_error:
            return JSONResponse(status_code=400, content={"message": total_error})
        now = datetime.now(timezone.utc)
        requirement = {
            "_id": ObjectId(),
            "visaTypeId": visa_type["_id"],
            "countryId": visa_type.get("countryId"),
            "name": name,
            "code": code,
            "description": payload.get("description"),
            "required": payload.get("required", True),
            "conditional": payload.get("conditional", False),
            "weight": maximum,
            "maximumEarnedPoints": maximum,
            "displayOrder": int(payload.get("displayOrder", 0)),
            "maxFileSize": int(payload.get("maxFileSize", 10 * 1024 * 1024)),
            "source": str(payload.get("source") or ""),
            "sourceStatus": str(payload.get("sourceStatus") or "UNVERIFIED"),
            "effectiveFrom": payload.get("effectiveFrom"),
            "acceptedFormats": payload.get("acceptedFormats", ["pdf", "jpg", "jpeg", "png"]),
            "allowMultiple": payload.get("allowMultiple", False),
            "status": str(payload.get("status") or "ACTIVE").upper(),
            "createdAt": now,
            "updatedAt": now,
        }
        if "photo" in str(requirement["code"]).casefold() or "photo" in name.casefold() or "photograph" in name.casefold():
            requirement["acceptedFormats"] = ["jpg", "jpeg", "png"]
            requirement["photoRules"] = payload.get(
                "photoRules",
                {
                    "minWidth": 600,
                    "minHeight": 600,
                    "minSharpness": 50,
                    "minBrightness": 40,
                    "maxBrightness": 220,
                    "faceCenterTolerance": 0.25,
                    "minFaceHeightRatio": 0.2,
                    "manualReviewRequired": False,
                },
            )
        if any(field in payload for field in SCORING_FIELDS):
            requirement.update({field: payload[field] for field in SCORING_FIELDS if field in payload})
            requirement = normalize_scoring_fields(requirement)
        await database["visarequirements"].insert_one(requirement)
        version = await _bump_configuration_version(database, visa_type["_id"])
        requirement["configurationVersion"] = version
        await database["visarequirements"].replace_one(
            {"_id": requirement["_id"]}, requirement
        )
        await _write_history(
            database,
            requirement,
            "CREATE",
            principal["id"],
            None,
            _public_requirement(requirement),
            version,
        )
    except DuplicateKeyError:
        return JSONResponse(status_code=409, content={"message": "Requirement already exists"})
    except PyMongoError as error:
        logger.exception("Could not create requirement: %s", error.__class__.__name__)
        raise APIError(503, "Requirement could not be saved") from error
    return JSONResponse(status_code=201, content={"requirement": _public_requirement(requirement)})


@router.get("/{requirement_id}/history")
async def requirement_history(
    requirement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        requirement = await database["visarequirements"].find_one(id_query(requirement_id))
        if not requirement:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        history = await database["requirementversions"].find(
            reference_query("requirementId", str(requirement["_id"]))
        ).to_list(length=None)
    except PyMongoError as error:
        raise APIError(503, "Requirement history is unavailable") from error
    history.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    serialized_history = []
    for item in history:
        serialized = public_document(item)
        for field in ("previous", "current"):
            if isinstance(item.get(field), dict):
                serialized[field] = _public_requirement(item[field])
        serialized_history.append(serialized)
    return JSONResponse(content={"history": serialized_history, "versions": serialized_history})


@router.get("/{requirement_id}")
async def get_requirement(
    requirement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        requirement = await database["visarequirements"].find_one(id_query(requirement_id))
    except PyMongoError as error:
        raise APIError(503, "Requirement is unavailable") from error
    if not requirement:
        return JSONResponse(status_code=404, content={"message": "Requirement not found"})
    return JSONResponse(content={"requirement": _public_requirement(requirement)})


@router.put("/{requirement_id}")
async def update_requirement(
    requirement_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    message = _validate(payload)
    if message:
        return JSONResponse(status_code=400, content={"message": message})
    try:
        collection = database["visarequirements"]
        current = await collection.find_one(id_query(requirement_id))
        if not current:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        visa_type_id = current.get("visaTypeId")
        siblings = await collection.find(
            reference_query("visaTypeId", str(visa_type_id))
        ).to_list(length=None)
        name = str(payload.get("name", current.get("name") or "")).strip()
        code = str(
            payload.get("code", current.get("code") or "")
            or re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        ).strip().casefold()
        duplicate = any(
            str(item.get("_id")) != str(current.get("_id"))
            and str(item.get("status", "ACTIVE")).upper() != "ARCHIVED"
            and (
                str(item.get("name") or "").strip().casefold() == name.casefold()
                or str(item.get("code") or "").strip().casefold() == code
            )
            for item in siblings
        )
        if duplicate:
            return JSONResponse(
                status_code=409,
                content={"message": "Requirement name and code must be unique for this visa type"},
            )
        updated = dict(current)
        for field in REQUIREMENT_FIELDS:
            if field in payload:
                updated[field] = payload[field]
        updated["name"] = name
        updated["code"] = code
        if any(field in payload or field in current for field in SCORING_FIELDS):
            # A legacy weight-only edit updates its canonical maximum alias too.
            if "weight" in payload and "maximumEarnedPoints" not in payload:
                updated["maximumEarnedPoints"] = payload["weight"]
            elif "maximumEarnedPoints" in payload and "weight" not in payload:
                updated["weight"] = payload["maximumEarnedPoints"]
            try:
                updated = normalize_scoring_fields(updated)
            except ValueError as error:
                return JSONResponse(status_code=400, content={"message": str(error)})
        if "weight" in payload or "maximumEarnedPoints" in payload:
            updated["weight"] = float(
                payload.get(
                    "maximumEarnedPoints",
                    payload.get("weight", current.get("weight", 0)),
                )
            )
            updated["maximumEarnedPoints"] = updated["weight"]
        total_error = _validate_configuration_total(
            [
                updated if str(item.get("_id")) == str(current.get("_id")) else item
                for item in siblings
            ]
        )
        if total_error:
            return JSONResponse(status_code=400, content={"message": total_error})
        updated["updatedAt"] = datetime.now(timezone.utc)
        await collection.replace_one({"_id": current["_id"]}, updated)
        version = await _bump_configuration_version(database, visa_type_id)
        updated["configurationVersion"] = version
        await collection.replace_one({"_id": current["_id"]}, updated)
        await _write_history(
            database,
            updated,
            "UPDATE",
            principal["id"],
            _public_requirement(current),
            _public_requirement(updated),
            version,
        )
    except DuplicateKeyError:
        return JSONResponse(status_code=409, content={"message": "Requirement conflicts with an existing record"})
    except PyMongoError as error:
        logger.exception("Could not update requirement: %s", error.__class__.__name__)
        raise APIError(503, "Requirement changes could not be saved") from error
    return JSONResponse(content={"requirement": _public_requirement(updated)})


@router.delete("/{requirement_id}")
async def delete_requirement(
    requirement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        collection = database["visarequirements"]
        current = await collection.find_one(id_query(requirement_id))
        if not current:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        siblings = await collection.find(
            reference_query("visaTypeId", str(current.get("visaTypeId")))
        ).to_list(length=None)
        referenced = await database["visaapplications"].find_one(
            {"requirementSnapshot.requirementId": str(current["_id"])}
        )
        if referenced:
            updated = {**current, "status": "ARCHIVED", "updatedAt": datetime.now(timezone.utc)}
            total_error = _validate_configuration_total(
                [
                    updated if str(item.get("_id")) == str(current.get("_id")) else item
                    for item in siblings
                ]
            )
            if total_error:
                return JSONResponse(status_code=400, content={"message": total_error})
            await collection.replace_one({"_id": current["_id"]}, updated)
            version = await _bump_configuration_version(
                database, current.get("visaTypeId")
            )
            updated["configurationVersion"] = version
            await collection.replace_one({"_id": current["_id"]}, updated)
            await _write_history(
                database,
                updated,
                "ARCHIVE",
                principal["id"],
                public_document(current),
                public_document(updated),
                version,
            )
            return JSONResponse(content={"message": "Requirement is used by applications and has been archived"})
        total_error = _validate_configuration_total(
            [item for item in siblings if str(item.get("_id")) != str(current.get("_id"))]
        )
        if total_error:
            return JSONResponse(status_code=400, content={"message": total_error})
        await collection.delete_one({"_id": current["_id"]})
        version = await _bump_configuration_version(
            database, current.get("visaTypeId")
        )
        await _write_history(
            database,
            current,
            "DELETE",
            principal["id"],
            public_document(current),
            None,
            version,
        )
    except PyMongoError as error:
        logger.exception("Could not delete requirement: %s", error.__class__.__name__)
        raise APIError(503, "Requirement could not be deleted") from error
    return JSONResponse(content={"message": "Requirement deleted"})


@router.post("/applications/{application_id}/recalculate")
async def recalculate_application_requirements(
    application_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 5:
        return JSONResponse(
            status_code=400,
            content={"message": "A recalculation reason of at least 5 characters is required"},
        )
    try:
        application = await database["visaapplications"].find_one(
            id_query(application_id)
        )
        if not application:
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        visa_type = await database["visatypes"].find_one(
            id_query(str(application.get("visaTypeId") or ""))
        )
        if not visa_type:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        active = await database["visarequirements"].find(
            {"visaTypeId": visa_type["_id"], "status": "ACTIVE"}
        ).to_list(length=None)
        total_error = _validate_configuration_total(active)
        if total_error:
            return JSONResponse(status_code=409, content={"message": total_error})
        requirements = [_snapshot_item(item) for item in active]
        requirements.sort(
            key=lambda item: (
                item.get("displayOrder", 0),
                str(item.get("name") or ""),
            )
        )
        documents = await database["documents"].find(
            {"applicationId": str(application["_id"])}
        ).to_list(length=None)
    except PyMongoError as error:
        logger.exception("Could not load application recalculation inputs")
        raise APIError(503, "Application requirements could not be recalculated") from error

    assessment = summarize_documents(requirements, documents)
    now = datetime.now(timezone.utc)
    previous_snapshot = (
        application.get("requirementSnapshot")
        or (application.get("evidencePolicySnapshot") or {}).get("requirements")
        or []
    )
    previous_assessment = application.get("evidenceAssessment")
    history_entry = {
        "previousConfigurationVersion": application.get(
            "requirementsConfigurationVersion"
        ),
        "previousRequirementSnapshot": previous_snapshot,
        "previousAssessment": previous_assessment,
        "configurationVersion": visa_type.get(
            "requirementsConfigurationVersion", 0
        ),
        "requirementSnapshot": requirements,
        "assessment": assessment,
        "reason": reason,
        "reviewedBy": principal["id"],
        "createdAt": now,
    }
    changes = {
        "requirementSnapshot": requirements,
        "requirementsConfigurationVersion": visa_type.get(
            "requirementsConfigurationVersion", 0
        ),
        "completenessScore": assessment["documentAssessmentScore"],
        "documentReadiness": assessment["documentAssessmentScore"],
        "documentAssessmentScore": assessment["documentAssessmentScore"],
        "earnedPoints": assessment["earnedPoints"],
        "maximumPoints": assessment["maximumPoints"],
        "verificationPercentage": assessment["verificationPercentage"],
        "submissionCompleteness": assessment["submissionCompleteness"],
        "evidenceAssessment": assessment,
        "assessmentStatus": assessment["documentProcessingStatus"],
        "assessmentStale": False,
        "verificationMetricsCurrent": assessment.get("processingDocuments", 0) == 0,
        "scoringVersion": "document-requirements-v3",
        "predictionStatus": "STALE",
        "approvalProbability": None,
        "approvalProbabilityNormalized": None,
        "rejectionProbabilityNormalized": None,
        "updatedAt": now,
    }
    query = {"_id": application["_id"]}
    if application.get("updatedAt") is not None:
        query["updatedAt"] = application["updatedAt"]
    try:
        saved = await database["visaapplications"].update_one(
            query,
            {
                "$set": changes,
                "$unset": {
                    "evidencePolicySnapshot": "",
                    "policyVersionId": "",
                },
                "$push": {
                    "requirementRecalculationHistory": history_entry,
                    "assessmentHistory": history_entry,
                    "scoreHistory": {
                        "maximumPoints": assessment["maximumPoints"],
                        "earnedPoints": assessment["earnedPoints"],
                        "documentAssessmentScore": assessment["documentAssessmentScore"],
                        "requirementScores": assessment["requirementScores"],
                        "configurationVersion": history_entry["configurationVersion"],
                        "reason": reason,
                        "reviewedBy": principal["id"],
                        "calculatedAt": now,
                    },
                },
            },
        )
        if not saved.matched_count:
            return JSONResponse(
                status_code=409,
                content={"message": "Application changed while recalculating; reload and retry"},
            )
    except PyMongoError as error:
        logger.exception("Could not save application requirements recalculation")
        raise APIError(503, "Application recalculation could not be saved") from error
    updated_application = {**application, **changes}
    updated_application.pop("evidencePolicySnapshot", None)
    updated_application.pop("policyVersionId", None)
    updated_application["requirementRecalculationHistory"] = [
        *(application.get("requirementRecalculationHistory") or []),
        history_entry,
    ]
    updated_application["assessmentHistory"] = [
        *(application.get("assessmentHistory") or []),
        history_entry,
    ]
    updated_application["scoreHistory"] = [
        *(application.get("scoreHistory") or []),
        {
            "maximumPoints": assessment["maximumPoints"],
            "earnedPoints": assessment["earnedPoints"],
            "documentAssessmentScore": assessment["documentAssessmentScore"],
            "requirementScores": assessment["requirementScores"],
            "configurationVersion": history_entry["configurationVersion"],
            "reason": reason,
            "reviewedBy": principal["id"],
            "calculatedAt": now,
        },
    ]
    return JSONResponse(
        content={
            "application": public_document(updated_application),
            "assessment": public_document(assessment),
            "configurationVersion": history_entry["configurationVersion"],
        }
    )
