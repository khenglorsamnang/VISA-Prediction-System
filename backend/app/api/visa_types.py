from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.errors import APIError
from ..services.evidence_scoring import SCORING_FIELDS, normalize_scoring_fields
from ..services.policy_profiles import requirement_snapshot
from . import requirements as requirements_api
from .dependencies import get_current_principal, get_mongo_database, require_roles
from .mongo_helpers import id_query, public_document, reference_query

logger = logging.getLogger(__name__)
router = APIRouter()
applications_router = APIRouter()

VISA_FIELDS = (
    "name",
    "visaTypeCode",
    "countryId",
    "countryCode",
    "destination",
    "category",
    "categoryCode",
    "entryType",
    "validity",
    "validityDays",
    "allowedStayDays",
    "visaClass",
    "applicantNationality",
    "officialSourceUrl",
    "effectiveDate",
    "additionalNotes",
    "active",
)


def _as_object_id(value: str) -> Any:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return value


def _visa_code(data: dict[str, Any]) -> str:
    country = re.sub(r"[^A-Z0-9]", "", str(data.get("countryCode") or "XX").upper())
    category = re.sub(r"[^A-Z0-9]", "", str(data.get("categoryCode") or data.get("category") or "GEN").upper())
    return f"VISA-{country}-{category[:8]}-{uuid4().hex[:8].upper()}"


def _validate_visa_payload(payload: dict[str, Any], creating: bool = False) -> str | None:
    if creating and not str(payload.get("name") or "").strip():
        return "Visa type name is required"
    if creating and not payload.get("countryId"):
        return "Country is required"
    if "active" in payload and not isinstance(payload["active"], bool):
        return "Active status must be true or false"
    if "visaTypeCode" in payload:
        visa_code = str(payload.get("visaTypeCode") or "").strip()
        if not visa_code or len(visa_code) > 100 or not re.fullmatch(r"[A-Za-z0-9-]+", visa_code):
            return "VISA code must contain only letters, numbers, and hyphens"
    for field in ("validityDays", "allowedStayDays"):
        if field in payload and payload[field] is not None:
            try:
                if int(payload[field]) < 0:
                    return f"{field} cannot be negative"
            except (TypeError, ValueError):
                return f"{field} must be a non-negative integer"
    return None


def _requirement_formats_error(requirement: dict[str, Any]) -> str | None:
    accepted_formats = requirement.get("acceptedFormats")
    if not isinstance(accepted_formats, list) or not accepted_formats:
        return "Accepted file formats are required"
    formats = {str(item).strip().lower().lstrip(".") for item in accepted_formats}
    allowed = {"pdf", "jpg", "jpeg", "png"}
    if formats - allowed:
        return "Only PDF, JPG, JPEG, and PNG file formats are supported"
    name = str(requirement.get("name") or "")
    code = str(requirement.get("code") or "")
    is_photo = "photo" in name.casefold() or "photo" in code.casefold() or "photograph" in name.casefold()
    if is_photo and "pdf" in formats:
        return "Applicant photos support JPG, JPEG, and PNG only"
    return None


class _RequirementSaveFailure(RuntimeError):
    def __init__(self, name: str, error: Exception) -> None:
        self.failed_requirement = name
        super().__init__(f"Requirement '{name}' failed to save: {error}")


async def _save_visa_and_requirements(
    payload: dict[str, Any],
    database: AsyncDatabase,
    principal: dict[str, str],
    visa_type_id: str | None = None,
) -> JSONResponse:
    requirement_payloads = payload.get("requirements")
    if not isinstance(requirement_payloads, list) or not requirement_payloads:
        return JSONResponse(status_code=400, content={"message": "At least one requirement is required"})

    visa_payload = {key: value for key, value in payload.items() if key != "requirements"}
    error_message = _validate_visa_payload(visa_payload, creating=visa_type_id is None)
    if error_message:
        return JSONResponse(status_code=400, content={"message": error_message})

    names: set[str] = set()
    codes: set[str] = set()
    for requirement in requirement_payloads:
        name = str(requirement.get("name") or "").strip() if isinstance(requirement, dict) else ""
        if not name:
            return JSONResponse(
                status_code=400,
                content={"message": "Requirement name is required", "failedRequirement": name},
            )
        key = name.casefold()
        if key in names:
            return JSONResponse(
                status_code=400,
                content={"message": "Requirement names must be unique for this VISA Type", "failedRequirement": name},
            )
        names.add(key)
        code = str(
            requirement.get("code")
            or re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        ).strip().casefold()
        if not code or code in codes:
            return JSONResponse(
                status_code=400,
                content={"message": "Requirement codes must be unique for this VISA Type", "failedRequirement": name},
            )
        codes.add(code)
        validation_error = requirements_api._validate(requirement)
        validation_error = validation_error or _requirement_formats_error(requirement)
        if validation_error:
            return JSONResponse(
                status_code=400,
                content={"message": validation_error, "failedRequirement": name},
            )
        for field in ("required", "status", "acceptedFormats", "maxFileSize"):
            if field not in requirement or requirement[field] is None:
                return JSONResponse(
                    status_code=400,
                    content={"message": f"{field} is required", "failedRequirement": name},
                )
        if str(requirement.get("status", "")).upper() not in {"ACTIVE", "INACTIVE", "ARCHIVED"}:
            return JSONResponse(
                status_code=400,
                content={"message": "Status must be ACTIVE, INACTIVE, or ARCHIVED", "failedRequirement": name},
            )
    total_error = requirements_api._validate_configuration_total(requirement_payloads)
    if total_error:
        return JSONResponse(status_code=400, content={"message": total_error})

    visa_collection = database["visatypes"]
    requirement_collection = database["visarequirements"]
    created_visa = False
    current_visa = None
    original_requirements: list[dict[str, Any]] = []
    written_requirement_ids: set[Any] = set()
    history_ids: list[ObjectId] = []
    try:
        country_id = str(visa_payload.get("countryId") or "")
        country = await database["countries"].find_one(id_query(country_id))
        if not country:
            return JSONResponse(status_code=400, content={"message": "Country not found"})

        if visa_type_id:
            current_visa = await visa_collection.find_one(id_query(visa_type_id))
            if not current_visa:
                return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        original_requirements = await requirement_collection.find(
            reference_query("visaTypeId", str(current_visa["_id"]) if current_visa else visa_type_id or "")
        ).to_list(length=None) if current_visa else []

        now = datetime.now(timezone.utc)
        configuration_version = int(
            (current_visa or {}).get("requirementsConfigurationVersion") or 0
        ) + 1
        if current_visa:
            visa_type = dict(current_visa)
            for field in VISA_FIELDS:
                if field in visa_payload:
                    visa_type[field] = visa_payload[field]
            visa_type["countryId"] = country["_id"]
            visa_type["countryCode"] = visa_payload.get("countryCode") or country.get("isoCode", "")
            visa_type["destination"] = visa_payload.get("destination") or country.get("name", "")
            visa_type["requirementsConfigurationVersion"] = configuration_version
            visa_type["requirementsConfigurationUpdatedAt"] = now
            visa_type["updatedAt"] = now
            await visa_collection.replace_one({"_id": current_visa["_id"]}, visa_type)
        else:
            visa_type = {field: visa_payload[field] for field in VISA_FIELDS if field in visa_payload}
            visa_type.update(
                {
                    "_id": ObjectId(),
                    "countryId": country["_id"],
                    "countryCode": visa_payload.get("countryCode") or country.get("isoCode", ""),
                    "destination": visa_payload.get("destination") or country.get("name", ""),
                    "visaTypeCode": str(visa_payload.get("visaTypeCode") or _visa_code(visa_payload)),
                    "active": visa_payload.get("active", True),
                    "requirementsConfigurationVersion": configuration_version,
                    "requirementsConfigurationUpdatedAt": now,
                    "createdAt": now,
                    "updatedAt": now,
                }
            )
            duplicate = await visa_collection.find_one({"visaTypeCode": visa_type["visaTypeCode"]})
            if duplicate:
                return JSONResponse(status_code=409, content={"message": "Visa type code already exists"})
            await visa_collection.insert_one(visa_type)
            created_visa = True

        previous_by_id = {str(item["_id"]): item for item in original_requirements}
        previous_by_name = {
            str(item.get("name", "")).strip().casefold(): item for item in original_requirements
        }
        retained_ids: set[str] = set()
        persisted_requirements = []
        for requirement_payload in requirement_payloads:
            name = str(requirement_payload["name"]).strip()
            existing = previous_by_id.get(str(requirement_payload.get("requirementId") or ""))
            if not existing:
                existing = previous_by_name.get(name.casefold())
            now = datetime.now(timezone.utc)
            requirement = dict(existing or {})
            requirement_id = existing["_id"] if existing else ObjectId()
            requirement.update(
                {
                    "_id": requirement_id,
                    "visaTypeId": visa_type["_id"],
                    "countryId": visa_type["countryId"],
                    "name": name,
                    "code": str(requirement_payload.get("code") or name.lower().replace(" ", "_")),
                    "description": requirement_payload.get("description"),
                    "required": requirement_payload["required"],
                    "conditional": requirement_payload.get("conditional", False),
                    "weight": float(requirement_payload.get("maximumEarnedPoints", requirement_payload.get("weight", 0))),
                    "maximumEarnedPoints": float(requirement_payload.get("maximumEarnedPoints", requirement_payload.get("weight", 0))),
                    "displayOrder": int(requirement_payload.get("displayOrder") or 0),
                    "maxFileSize": int(requirement_payload["maxFileSize"]),
                    "source": str(requirement_payload.get("source") or ""),
                    "sourceStatus": str(requirement_payload.get("sourceStatus") or "UNVERIFIED"),
                    "effectiveFrom": requirement_payload.get("effectiveFrom"),
                    "acceptedFormats": sorted(
                        {str(item).strip().lower().lstrip(".") for item in requirement_payload["acceptedFormats"]}
                    ),
                    "allowMultiple": requirement_payload.get("allowMultiple", False),
                    "status": str(requirement_payload["status"]).upper(),
                    "configurationVersion": configuration_version,
                    "updatedAt": now,
                }
            )
            if not existing:
                requirement["createdAt"] = now
            if "photo" in requirement["code"].casefold() or "photo" in name.casefold() or "photograph" in name.casefold():
                requirement["photoRules"] = requirement_payload.get("photoRules", {})
            for field in SCORING_FIELDS:
                if field in requirement_payload:
                    requirement[field] = requirement_payload[field]
            if any(field in requirement_payload for field in SCORING_FIELDS):
                requirement = normalize_scoring_fields(requirement)

            try:
                if existing:
                    await requirement_collection.replace_one({"_id": requirement_id}, requirement)
                else:
                    await requirement_collection.insert_one(requirement)
                written_requirement_ids.add(requirement_id)
                retained_ids.add(str(requirement_id))
                history_id = ObjectId()
                history_ids.append(history_id)
                await database["requirementversions"].insert_one(
                    {
                        "_id": history_id,
                        "requirementId": requirement_id,
                        "visaTypeId": visa_type["_id"],
                        "action": "UPDATE" if existing else "CREATE",
                        "adminId": principal["id"],
                        "configurationVersion": configuration_version,
                        "previous": requirements_api._public_requirement(existing) if existing else None,
                        "current": requirements_api._public_requirement(requirement),
                        "createdAt": now,
                        "updatedAt": now,
                    }
                )
            except Exception as error:
                raise _RequirementSaveFailure(name, error) from error
            persisted_requirements.append(requirement)

        for previous in original_requirements:
            if str(previous["_id"]) in retained_ids:
                continue
            try:
                referenced = await database["visaapplications"].find_one(
                    {"requirementSnapshot.requirementId": str(previous["_id"])}
                )
                if referenced:
                    archived = {**previous, "status": "ARCHIVED", "updatedAt": datetime.now(timezone.utc)}
                    await requirement_collection.replace_one({"_id": previous["_id"]}, archived)
                    action = "ARCHIVE"
                else:
                    await requirement_collection.delete_one({"_id": previous["_id"]})
                    archived = None
                    action = "DELETE"
                history_id = ObjectId()
                history_ids.append(history_id)
                await database["requirementversions"].insert_one(
                    {
                        "_id": history_id,
                        "requirementId": previous["_id"],
                        "visaTypeId": visa_type["_id"],
                        "action": action,
                        "adminId": principal["id"],
                        "configurationVersion": configuration_version,
                        "previous": requirements_api._public_requirement(previous),
                        "current": requirements_api._public_requirement(archived) if archived else None,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                )
            except Exception as error:
                raise _RequirementSaveFailure(str(previous.get("name") or "Unknown requirement"), error) from error

        reloaded_visa = await visa_collection.find_one({"_id": visa_type["_id"]})
        reloaded_requirements = await requirement_collection.find(
            reference_query("visaTypeId", str(visa_type["_id"]))
        ).to_list(length=None)
        expected = {item["name"].casefold(): item for item in persisted_requirements}
        actual = {str(item.get("name", "")).casefold(): item for item in reloaded_requirements}
        for name, saved in expected.items():
            restored = actual.get(name)
            if not restored or any(
                restored.get(field) != saved.get(field)
                for field in ("required", "acceptedFormats", "maxFileSize", "status", "displayOrder")
            ):
                raise _RequirementSaveFailure(
                    saved["name"],
                    RuntimeError("Persisted values did not match after reload"),
                )
        if not reloaded_visa or reloaded_visa.get("visaTypeCode") != visa_type.get("visaTypeCode"):
            raise RuntimeError("Persisted VISA Type could not be verified")
    except Exception as error:
        logger.exception("Could not save VISA Type requirements consistently")
        try:
            for requirement_id in written_requirement_ids:
                await requirement_collection.delete_one({"_id": requirement_id})
            for original in original_requirements:
                await requirement_collection.delete_one({"_id": original["_id"]})
                await requirement_collection.insert_one(original)
            if current_visa:
                await visa_collection.replace_one({"_id": current_visa["_id"]}, current_visa)
            elif created_visa:
                await visa_collection.delete_one({"_id": visa_type["_id"]})
            if history_ids:
                for history_id in history_ids:
                    await database["requirementversions"].delete_one({"_id": history_id})
        except Exception as rollback_error:
            logger.exception("VISA Type save rollback failed")
            return JSONResponse(
                status_code=500,
                content={
                    "message": f"Save failed: {error}. Rollback also failed: {rollback_error}",
                },
            )
        failed_name = getattr(error, "failed_requirement", None)
        return JSONResponse(
            status_code=503,
            content={
                "message": str(error),
                **({"failedRequirement": failed_name} if failed_name else {}),
            },
        )

    return JSONResponse(
        status_code=201 if created_visa else 200,
        content={
            "visaType": public_document(reloaded_visa),
            "requirements": [requirements_api._public_requirement(item) for item in persisted_requirements],
        },
    )


@router.post("/with-requirements", status_code=201)
async def create_visa_type_with_requirements(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    return await _save_visa_and_requirements(payload, database, principal)


@router.put("/{visa_type_id}/requirements")
async def update_visa_type_with_requirements(
    visa_type_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    return await _save_visa_and_requirements(payload, database, principal, visa_type_id)


@router.get("/")
@router.get("")
@router.get("/types")
async def list_visa_types(
    database: AsyncDatabase = Depends(get_mongo_database),
    country_id: str | None = Query(default=None, alias="countryId"),
    active: str | None = Query(default=None),
    search: str = Query(default=""),
) -> JSONResponse:
    try:
        documents = await database["visatypes"].find({}).to_list(length=None)
    except PyMongoError as error:
        logger.exception("Could not load visa types: %s", error.__class__.__name__)
        raise APIError(503, "Visa types are unavailable") from error

    normalized_search = search.strip().casefold()
    result = []
    for visa_type in documents:
        if country_id and str(visa_type.get("countryId")) != country_id:
            continue
        if active is not None and str(visa_type.get("active", True)).casefold() != active.casefold():
            continue
        searchable = " ".join(
            str(visa_type.get(field) or "")
            for field in ("name", "visaTypeCode", "destination", "category")
        ).casefold()
        if normalized_search and normalized_search not in searchable:
            continue
        result.append(public_document(visa_type))
    return JSONResponse(content={"visaTypes": result, "count": len(result)})


async def get_visa_type(
    visa_type_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        visa_type = await database["visatypes"].find_one(id_query(visa_type_id))
    except PyMongoError as error:
        raise APIError(503, "Visa type is unavailable") from error
    if not visa_type:
        return JSONResponse(status_code=404, content={"message": "Visa type not found"})
    return JSONResponse(content={"visaType": public_document(visa_type)})


@router.post("/", status_code=201)
@router.post("", status_code=201)
@router.post("/types", status_code=201)
async def create_visa_type(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    error_message = _validate_visa_payload(payload, creating=True)
    if error_message:
        return JSONResponse(status_code=400, content={"message": error_message})

    country_id = _as_object_id(str(payload["countryId"]))
    try:
        country = await database["countries"].find_one(id_query(str(country_id)))
        if not country:
            return JSONResponse(status_code=400, content={"message": "Country not found"})
        now = datetime.now(timezone.utc)
        data = {field: payload[field] for field in VISA_FIELDS if field in payload}
        data.update(
            {
                "_id": ObjectId(),
                "countryId": country["_id"],
                "countryCode": payload.get("countryCode") or country.get("isoCode", ""),
                "destination": payload.get("destination") or country.get("name", ""),
                "visaTypeCode": str(payload.get("visaTypeCode") or _visa_code(payload)),
                "active": payload.get("active", True),
                "createdAt": now,
                "updatedAt": now,
            }
        )
        if await database["visatypes"].find_one({"visaTypeCode": data["visaTypeCode"]}):
            return JSONResponse(status_code=409, content={"message": "Visa type code already exists"})
        await database["visatypes"].insert_one(data)
    except DuplicateKeyError:
        return JSONResponse(status_code=409, content={"message": "Visa type code already exists"})
    except PyMongoError as error:
        logger.exception("Could not create visa type: %s", error.__class__.__name__)
        raise APIError(503, "Visa type could not be saved") from error
    return JSONResponse(status_code=201, content={"visaType": public_document(data)})


@router.put("/{visa_type_id}")
async def update_visa_type(
    visa_type_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    error_message = _validate_visa_payload(payload)
    if error_message:
        return JSONResponse(status_code=400, content={"message": error_message})
    try:
        collection = database["visatypes"]
        current = await collection.find_one(id_query(visa_type_id))
        if not current:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        updated = dict(current)
        for field in VISA_FIELDS:
            if field in payload:
                updated[field] = payload[field]
        if "visaTypeCode" in payload:
            visa_type_code = str(payload["visaTypeCode"]).strip()
            existing_code = await collection.find_one(
                {"visaTypeCode": visa_type_code}
            )
            if (
                existing_code
                and str(existing_code.get("_id")) != str(current.get("_id"))
            ):
                return JSONResponse(
                    status_code=409,
                    content={"message": "Visa type code already exists"},
                )
            updated["visaTypeCode"] = visa_type_code
        if "countryId" in payload:
            country_id = _as_object_id(str(payload["countryId"]))
            country = await database["countries"].find_one(id_query(str(country_id)))
            if not country:
                return JSONResponse(status_code=400, content={"message": "Country not found"})
            updated["countryId"] = country["_id"]
            updated["countryCode"] = payload.get("countryCode") or country.get("isoCode", "")
            updated["destination"] = payload.get("destination") or country.get("name", "")
        updated["updatedAt"] = datetime.now(timezone.utc)
        result = await collection.replace_one({"_id": current["_id"]}, updated)
        if not result.matched_count:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
    except DuplicateKeyError:
        return JSONResponse(
            status_code=409,
            content={"message": "Visa type code already exists"},
        )
    except PyMongoError as error:
        logger.exception("Could not update visa type: %s", error.__class__.__name__)
        raise APIError(503, "Visa type changes could not be saved") from error
    return JSONResponse(content={"visaType": public_document(updated)})


@router.patch("/{visa_type_id}/archive")
async def archive_visa_type(
    visa_type_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    return await update_visa_type(
        visa_type_id,
        {"active": False},
        database,
        _principal,
    )


@router.post("/{visa_type_id}/duplicate", status_code=201)
async def duplicate_visa_type(
    visa_type_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        source = await database["visatypes"].find_one(id_query(visa_type_id))
        if not source:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        now = datetime.now(timezone.utc)
        duplicate = {
            key: value
            for key, value in source.items()
            if key not in {"_id", "__v", "createdAt", "updatedAt", "visaTypeCode"}
        }
        duplicate.update(
            {
                "_id": ObjectId(),
                "name": f"{source.get('name', 'Visa type')} (Copy)",
                "visaTypeCode": _visa_code(source),
                "active": False,
                "createdAt": now,
                "updatedAt": now,
            }
        )
        await database["visatypes"].insert_one(duplicate)
    except PyMongoError as error:
        logger.exception("Could not duplicate visa type: %s", error.__class__.__name__)
        raise APIError(503, "Visa type could not be duplicated") from error
    return JSONResponse(status_code=201, content={"visaType": public_document(duplicate)})


@router.delete("/{visa_type_id}")
async def delete_visa_type(
    visa_type_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        collection = database["visatypes"]
        visa_type = await collection.find_one(id_query(visa_type_id))
        if not visa_type:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        in_use = bool(
            await database["visaapplications"].find_one(
                reference_query("visaTypeId", str(visa_type["_id"]))
            )
            or await database["visarequirements"].find_one(
                reference_query("visaTypeId", str(visa_type["_id"]))
            )
        )
        if in_use:
            await collection.update_one(
                {"_id": visa_type["_id"]},
                {"$set": {"active": False, "updatedAt": datetime.now(timezone.utc)}},
            )
            return JSONResponse(content={"message": "Visa type is in use and has been deactivated"})
        await collection.delete_one({"_id": visa_type["_id"]})
    except PyMongoError as error:
        logger.exception("Could not delete visa type: %s", error.__class__.__name__)
        raise APIError(503, "Visa type could not be deleted") from error
    return JSONResponse(content={"message": "Visa type deleted"})


async def _enrich_application(
    database: AsyncDatabase, application: dict[str, Any]
) -> dict[str, Any]:
    result = public_document(application)
    customer_id = application.get("customer") or application.get("customerId")
    visa_type_id = application.get("visaTypeId") or application.get("visaType")
    customer = (
        await database["customers"].find_one(id_query(str(customer_id)))
        if customer_id
        else None
    )
    visa_type = (
        await database["visatypes"].find_one(id_query(str(visa_type_id)))
        if visa_type_id
        else None
    )
    result["applicant"] = customer.get("fullName", "") if customer else ""
    result["visaType"] = str(visa_type_id or "")
    result["visaTypeName"] = visa_type.get("name", "") if visa_type else ""
    result["visaTypeId"] = str(visa_type_id or "")
    return result


@applications_router.get("/")
@applications_router.get("")
@router.get("/applications")
async def list_applications(
    database: AsyncDatabase = Depends(get_mongo_database),
    customer_id: str | None = Query(default=None, alias="customerId"),
    visa_type_id: str | None = Query(default=None, alias="visaTypeId"),
    status: str | None = Query(default=None),
    search: str = Query(default=""),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    try:
        applications = await database["visaapplications"].find({}).to_list(length=None)
        customer_map = {
            str(customer["_id"]): customer
            for customer in await database["customers"].find({}).to_list(length=None)
        }
    except PyMongoError as error:
        logger.exception("Could not load applications: %s", error.__class__.__name__)
        raise APIError(503, "Applications are unavailable") from error

    result = []
    for application in applications:
        if application.get("deletedAt"):
            continue
        owner_id = str(application.get("customer") or application.get("customerId") or "")
        if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
            continue
        if customer_id and owner_id != customer_id:
            continue
        if visa_type_id and str(application.get("visaTypeId") or application.get("visaType")) != visa_type_id:
            continue
        if status and str(application.get("status", "")).casefold() != status.casefold():
            continue
        enriched = await _enrich_application(database, application)
        if search and search.casefold() not in " ".join(
            str(enriched.get(key) or "")
            for key in ("applicationNumber", "applicant", "visaTypeName", "status")
        ).casefold():
            continue
        has_verification_result = application.get("verificationMetricsCurrent")
        if has_verification_result is None:
            has_verification_result = all(
                application.get(field) is not None
                for field in ("documentReadiness", "verificationPercentage")
            )
        enriched["hasVerificationResult"] = bool(has_verification_result)
        enriched.pop("verificationMetricsCurrent", None)
        if not has_verification_result:
            enriched.pop("documentReadiness", None)
            enriched.pop("verificationPercentage", None)
        result.append(enriched)
    result.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return JSONResponse(content={"applications": result, "count": len(result)})


@applications_router.post("/", status_code=201)
@applications_router.post("", status_code=201)
@router.post("/applications", status_code=201)
async def create_application(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    customer_id = str(payload.get("customer") or payload.get("customerId") or "")
    visa_type_id = str(payload.get("visaType") or payload.get("visaTypeId") or "")
    if not customer_id or not visa_type_id:
        return JSONResponse(status_code=400, content={"message": "Customer and visa type are required"})
    if payload.get("travelStartDate") and payload.get("returnDate") and str(payload["returnDate"]) <= str(payload["travelStartDate"]):
        return JSONResponse(status_code=400, content={"message": "Return date must be after travel start date"})
    try:
        customer = await database["customers"].find_one(id_query(customer_id))
        visa_type = await database["visatypes"].find_one(id_query(visa_type_id))
        if not customer:
            return JSONResponse(status_code=404, content={"message": "Customer not found"})
        if not visa_type or not visa_type.get("active", True):
            return JSONResponse(status_code=404, content={"message": "Active visa type not found"})
        # Snapshot the current active requirements so later edits do not
        # silently change this application's scoring configuration.
        requirements = await database["visarequirements"].find(
            {
                **reference_query("visaTypeId", str(visa_type["_id"])),
                "status": "ACTIVE",
            }
        ).to_list(length=None)
        if not requirements:
            return JSONResponse(status_code=409, content={"message": "Visa type has no active requirements"})
        now = datetime.now(timezone.utc)
        application_id = str(uuid4())
        application = {
            "_id": application_id,
            "applicationNumber": f"VD-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:4].upper()}",
            "customer": str(customer["_id"]),
            "countryId": visa_type.get("countryId"),
            "visaTypeId": visa_type["_id"],
            "visaTypeCode": visa_type.get("visaTypeCode"),
            "destination": visa_type.get("destination"),
            "visaCategory": visa_type.get("category"),
            "requirementSnapshot": [requirement_snapshot(item) for item in requirements],
            "requirementsConfigurationVersion": visa_type.get(
                "requirementsConfigurationVersion", 0
            ),
            "travelStartDate": payload.get("travelStartDate"),
            "returnDate": payload.get("returnDate"),
            "status": "DRAFT",
            "completenessScore": 0,
            "verificationMetricsCurrent": False,
            "createdAt": now,
            "updatedAt": now,
        }
        if isinstance(payload.get("applicantContext"), dict):
            application["applicantContext"] = payload["applicantContext"]
        await database["visaapplications"].insert_one(application)
    except PyMongoError as error:
        logger.exception("Could not create application: %s", error.__class__.__name__)
        raise APIError(503, "Application could not be saved") from error
    enriched = await _enrich_application(database, application)
    enriched["hasVerificationResult"] = False
    return JSONResponse(status_code=201, content={"application": enriched})


@applications_router.get("/{application_id}")
async def get_application(
    application_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    try:
        application = await database["visaapplications"].find_one(id_query(application_id))
    except PyMongoError as error:
        raise APIError(503, "Application is unavailable") from error
    if not application or application.get("deletedAt"):
        return JSONResponse(status_code=404, content={"message": "Application not found"})
    owner_id = str(application.get("customer") or application.get("customerId") or "")
    if principal["role"] == "CUSTOMER" and owner_id != principal["id"]:
        return JSONResponse(status_code=404, content={"message": "Application not found"})
    return JSONResponse(content={"application": await _enrich_application(database, application)})


@applications_router.put("/{application_id}")
async def update_application(
    application_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    allowed = {"travelStartDate", "returnDate", "status"}
    try:
        collection = database["visaapplications"]
        current = await collection.find_one(id_query(application_id))
        if not current or current.get("deletedAt"):
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        updated = dict(current)
        for field in allowed.intersection(payload):
            updated[field] = payload[field]
        if updated.get("travelStartDate") and updated.get("returnDate") and str(updated["returnDate"]) <= str(updated["travelStartDate"]):
            return JSONResponse(status_code=400, content={"message": "Return date must be after travel start date"})
        updated["updatedAt"] = datetime.now(timezone.utc)
        await collection.replace_one({"_id": current["_id"]}, updated)
    except PyMongoError as error:
        logger.exception("Could not update application: %s", error.__class__.__name__)
        raise APIError(503, "Application changes could not be saved") from error
    return JSONResponse(content={"application": await _enrich_application(database, updated)})


@applications_router.delete("/{application_id}")
@router.delete("/applications/{application_id}")
async def delete_application(
    application_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        collection = database["visaapplications"]
        current = await collection.find_one(id_query(application_id))
        if not current:
            return JSONResponse(status_code=404, content={"message": "Application not found"})
        current.update(
            {"status": "CANCELLED", "deletedAt": datetime.now(timezone.utc), "updatedAt": datetime.now(timezone.utc)}
        )
        await collection.replace_one({"_id": current["_id"]}, current)
    except PyMongoError as error:
        logger.exception("Could not cancel application: %s", error.__class__.__name__)
        raise APIError(503, "Application could not be cancelled") from error
    return JSONResponse(content={"message": "Application cancelled"})


router.add_api_route("/{visa_type_id}", get_visa_type, methods=["GET"])
