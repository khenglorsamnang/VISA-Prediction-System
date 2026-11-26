"""Immutable evidence policy releases and historical application snapshots."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .evidence_scoring import SCORING_FIELDS, VERSION, requirement_id, validate_profile_requirements, normalize_scoring_fields


def profile_time(value: Any, fallback: datetime | None = None) -> datetime:
    if value is None:
        return fallback or datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def effective_time(profile: dict[str, Any], field: str, fallback: datetime | None = None) -> datetime:
    """Date-only and timezone-less effective dates belong to the profile's local calendar."""
    value = profile.get(field)
    if value in (None, ""):
        return fallback or datetime.now(timezone.utc)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=ZoneInfo(profile.get("timezone") or "Asia/Phnom_Penh")) if parsed.tzinfo is None else parsed


def validate_profile(payload: dict[str, Any], *, publishing: bool = False) -> dict[str, Any]:
    data = deepcopy(payload)
    if not str(data.get("name") or "").strip() or not data.get("visaTypeId"):
        raise ValueError("Profile name and visaTypeId are required")
    data["name"] = str(data["name"]).strip()
    data["version"] = str(data.get("version") or "").strip()
    if not data["version"] or len(data["version"]) > 100:
        raise ValueError("A policy version of at most 100 characters is required")
    data["timezone"] = str(data.get("timezone") or "Asia/Phnom_Penh")
    try:
        ZoneInfo(data["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown assessment timezone") from exc
    if data.get("effectiveFrom"):
        effective_time(data, "effectiveFrom")
    if data.get("effectiveUntil"):
        if effective_time(data, "effectiveUntil") <= effective_time(data, "effectiveFrom"):
            raise ValueError("effectiveUntil must be later than effectiveFrom")
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        raise ValueError("Profile requirements must be a list")
    if any(not isinstance(requirement, dict) for requirement in requirements):
        raise ValueError("Profile requirements must be objects")
    # Drafts may have an incomplete budget/rubric, but never invalid finite ranges.
    data["requirements"] = [normalize_scoring_fields(requirement) for requirement in requirements]
    if publishing:
        data["requirements"] = validate_profile_requirements(data["requirements"])
    data["scoringVersion"] = VERSION
    return data


def release_content(profile: dict[str, Any]) -> dict[str, Any]:
    keys = ("name","version","visaTypeId","countryId","requirements","effectiveFrom","effectiveUntil","timezone","scoringVersion","description")
    return {key:deepcopy(profile.get(key)) for key in keys}


def content_hash(profile: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(release_content(profile),sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()


def profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    """A complete immutable copy, not a reference to today's active requirements."""
    snapshot = release_content(profile)
    snapshot.update(id=str(profile.get("id") or profile.get("_id")),status="PUBLISHED",publishedAt=profile.get("publishedAt"),publishedBy=profile.get("publishedBy"),contentHash=profile.get("contentHash") or content_hash(profile))
    return snapshot


def resolve_application_requirements(application: dict[str, Any], current_requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshot = application.get("evidencePolicySnapshot")
    if isinstance(snapshot,dict) and snapshot.get("scoringVersion") == VERSION:
        return deepcopy(snapshot.get("requirements") or [])
    return current_requirements


async def load_published_profile(database: Any, visa_type_id: Any, *, as_of: datetime | None = None, profile_id: str | None = None) -> dict[str, Any] | None:
    now = profile_time(as_of) if as_of is not None else datetime.now(timezone.utc)
    records = await database["evidencepolicyprofiles"].find({"visaTypeId":str(visa_type_id),"status":"PUBLISHED"}).to_list(length=None)
    eligible=[]
    for record in records:
        if str(record.get("visaTypeId")) != str(visa_type_id) or record.get("status") != "PUBLISHED":
            continue
        if profile_id and str(record.get("_id")) != profile_id:
            continue
        if effective_time(record,"effectiveFrom",profile_time(record.get("publishedAt"))) > now:
            continue
        if record.get("effectiveUntil") and effective_time(record,"effectiveUntil") <= now:
            continue
        eligible.append(record)
    return max(eligible,key=lambda r:(effective_time(r,"effectiveFrom",profile_time(r.get("publishedAt"))),profile_time(r.get("publishedAt")),str(r.get("_id")))) if eligible else None


async def application_profile_fields(database: Any, visa_type_id: Any, *, as_of: datetime | None = None) -> dict[str, Any]:
    profile = await load_published_profile(database,visa_type_id,as_of=as_of)
    if not profile:
        return {}
    snapshot=profile_snapshot(profile)
    return {"evidencePolicySnapshot":snapshot,"policyVersionId":snapshot["id"],"scoringVersion":VERSION,"requirementSnapshot":deepcopy(snapshot["requirements"]),"assessmentStatus":"DRAFT","assessmentStale":True,"approvalProbability":None,"approvalProbabilityNormalized":None,"rejectionProbabilityNormalized":None}


def requirement_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    fields = ("name","code","description","required","conditional","weight","maximumEarnedPoints","displayOrder","acceptedFormats","maxFileSize","allowMultiple","source","sourceStatus","effectiveFrom","status","configurationVersion",*SCORING_FIELDS)
    snapshot = {"requirementId":requirement_id(item),**{key:deepcopy(item[key]) for key in fields if key in item}}
    maximum = item.get("maximumEarnedPoints", item.get("weight", item.get("points", 0)))
    snapshot["weight"] = deepcopy(maximum)
    snapshot["maximumEarnedPoints"] = deepcopy(maximum)
    return snapshot
