from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from typing import Any

from .identity_normalization import name_tokens

_INACTIVE = {"DELETED", "REPLACED"}
_FIELD_ALIASES = {
    "fullName": (
        "fullName",
        "full_name",
        "applicantName",
        "applicant_name",
        "name",
    ),
    "passportNumber": (
        "passportNumber",
        "passport_number",
        "passportNo",
        "passport_no",
    ),
    "nationalIdNumber": (
        "nationalIdNumber",
        "national_id_number",
        "nationalId",
        "identityNumber",
        "idNumber",
    ),
    "dateOfBirth": ("dateOfBirth", "date_of_birth", "birthDate", "dob"),
    "nationality": ("nationality", "nationality_name"),
    "monthlySalary": ("monthlySalary", "monthly_salary"),
    "depositAmount": ("depositAmount", "deposit_amount"),
}
_FORMAT_BY_MIME = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
}
_NATIONALITY_ALIASES = {
    "kh": "cambodia",
    "khm": "cambodia",
    "cambodian": "cambodia",
    "usa": "unitedstates",
    "us": "unitedstates",
    "american": "unitedstates",
    "gbr": "unitedkingdom",
    "gb": "unitedkingdom",
    "british": "unitedkingdom",
}


def document_kind(requirement: dict[str, Any], document: dict[str, Any]) -> str:
    value = " ".join(
        str(item or "")
        for item in (
            requirement.get("code"),
            requirement.get("name"),
            document.get("requirementCode"),
        )
    ).casefold().replace("-", "_")
    if "bank" in value and "statement" in value:
        return "bank_statement"
    if "photo" in value or "photograph" in value:
        return "photo"
    if "passport" in value:
        return "passport"
    if "national" in value and ("id" in value or "identity" in value):
        return "national_id"
    if "support" in value or "other" in value:
        return "supporting"
    return "other"


def _fields(document: dict[str, Any]) -> dict[str, Any]:
    fields = document.get("extractedFields")
    return fields if isinstance(fields, dict) else {}


def _value(fields: dict[str, Any], canonical: str) -> Any:
    for key in _FIELD_ALIASES[canonical]:
        value = fields.get(key)
        if value not in (None, "", []):
            return value
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value[:10], pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _normalized(canonical: str, value: Any) -> Any:
    if canonical == "fullName":
        return frozenset(name_tokens(str(value)))
    if canonical in {"dateOfBirth"}:
        return _parse_date(value)
    if canonical == "nationality":
        normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
        return _NATIONALITY_ALIASES.get(normalized, normalized)
    if canonical in {"monthlySalary", "depositAmount"}:
        try:
            return Decimal(re.sub(r"[, $]", "", str(value))).normalize()
        except (InvalidOperation, ValueError):
            return None
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _check(code: str, label: str, status: str, reason: str, fields: list[str]) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "reason": reason,
        "fields": fields,
    }


def document_consistency_checks(
    application: dict[str, Any],
    applicant: dict[str, Any],
    requirements: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    active_documents = [
        item
        for item in documents
        if str(item.get("status") or "").upper() not in _INACTIVE
    ]
    requirements_by_id = {
        str(item.get("requirementId") or item.get("_id") or ""): item
        for item in requirements
    }
    doc_context = []
    for document in active_documents:
        requirement_id = str(document.get("requirementId") or "")
        requirement = requirements_by_id.get(requirement_id, {})
        doc_context.append(
            {
                "document": document,
                "requirement": requirement,
                "kind": document_kind(requirement, document),
                "fields": _fields(document),
            }
        )

    checks_by_document: dict[str, list[dict[str, Any]]] = {}
    for context in doc_context:
        document = context["document"]
        kind = context["kind"]
        if kind not in {"bank_statement", "supporting"}:
            continue
        document_id = str(document.get("_id") or document.get("id") or "")
        checks: list[dict[str, Any]] = []
        checks_by_document[document_id] = checks
        requirement = context["requirement"]
        if not requirement:
            checks.append(
                _check(
                    "CROSS_REQUIREMENT_MATCH",
                    "Selected VISA requirement",
                    "NEEDS_REVIEW",
                    "This document is not linked to a requirement in the selected VISA type.",
                    ["requirementId"],
                )
            )
        else:
            formats = {
                str(item).casefold().lstrip(".")
                for item in requirement.get("acceptedFormats", [])
                if isinstance(item, str)
            }
            extension = PurePath(str(document.get("fileName") or "")).suffix.casefold().lstrip(".")
            file_format = _FORMAT_BY_MIME.get(str(document.get("mimeType") or "").casefold(), extension)
            format_ok = bool(file_format and file_format in formats)
            checks.append(
                _check(
                    "CROSS_REQUIREMENT_FORMAT",
                    "VISA requirement file format",
                    "PASSED" if format_ok else "NEEDS_REVIEW",
                    (
                        f"The submitted {file_format.upper()} file is accepted by the selected VISA requirement."
                        if format_ok
                        else "The submitted file format is not accepted by the selected VISA requirement."
                    ),
                    ["fileName", "mimeType", "acceptedFormats"],
                )
            )

        target_fields = context["fields"]
        compared_fields: set[str] = set()
        for canonical in _FIELD_ALIASES:
            if kind == "bank_statement" and canonical == "fullName":
                continue
            target_value = _value(target_fields, canonical)
            if target_value is None:
                continue
            related_values = []
            for other in doc_context:
                if other is context:
                    continue
                if canonical == "fullName" and other["kind"] == "bank_statement":
                    continue
                other_value = _value(other["fields"], canonical)
                if other_value is not None:
                    related_values.append(other_value)
            profile_value = _value(applicant, canonical)
            if profile_value is not None:
                related_values.append(profile_value)
            if not related_values:
                continue
            compared_fields.add(canonical)
            target_normalized = _normalized(canonical, target_value)
            related_normalized = {
                _normalized(canonical, value)
                for value in related_values
            }
            matches = (
                target_normalized is not None
                and bool(target_normalized)
                and related_normalized == {target_normalized}
            )
            label = canonical.replace("Name", " name").replace("Number", " number")
            checks.append(
                _check(
                    f"CROSS_{canonical.upper()}",
                    f"Cross-document {label} consistency",
                    "PASSED" if matches else "NEEDS_REVIEW",
                    (
                        f"The {label} is consistent with the applicant and other submitted documents."
                        if matches
                        else f"The {label} conflicts with the applicant information or another submitted document."
                    ),
                    [canonical],
                )
            )

        if kind == "bank_statement":
            required_kinds = {
                document_kind(requirement, {})
                for requirement in requirements
                if requirement.get("required", True)
            }
            for required_kind in ("passport", "photo", "national_id", "supporting"):
                if required_kind not in required_kinds:
                    continue
                companions = [item for item in doc_context if item["kind"] == required_kind]
                if not companions:
                    checks.append(
                        _check(
                            f"CROSS_{required_kind.upper()}_MISSING",
                            f"Required {required_kind.replace('_', ' ')}",
                            "NEEDS_REVIEW",
                            f"The selected VISA type requires a {required_kind.replace('_', ' ')} document, but none was submitted.",
                            ["requirementId"],
                        )
                    )
                elif required_kind in {"passport", "photo", "national_id"} and any(
                    str(item["document"].get("status") or "").upper() != "VERIFIED"
                    for item in companions
                ):
                    checks.append(
                        _check(
                            f"CROSS_{required_kind.upper()}_STATUS",
                            f"{required_kind.replace('_', ' ').title()} verification",
                            "NEEDS_REVIEW",
                            f"The submitted {required_kind.replace('_', ' ')} has not passed its document verification checks.",
                            ["status"],
                        )
                    )

            start = _parse_date(target_fields.get("statementStartDate"))
            end = _parse_date(target_fields.get("statementEndDate"))
            if start and end:
                checks.append(
                    _check(
                        "CROSS_BANK_STATEMENT_DATES",
                        "Bank statement date range",
                        "PASSED" if start <= end else "NEEDS_REVIEW",
                        (
                            "The statement start date is on or before its end date."
                            if start <= end
                            else "The statement end date precedes its start date."
                        ),
                        ["statementStartDate", "statementEndDate"],
                    )
                )
            else:
                checks.append(
                    _check(
                        "CROSS_BANK_STATEMENT_DATES",
                        "Bank statement date range",
                        "NEEDS_REVIEW",
                        "The statement dates are missing or could not be parsed.",
                        ["statementStartDate", "statementEndDate"],
                    )
                )

            travel_start = _parse_date(application.get("travelStartDate"))
            if travel_start:
                for other in doc_context:
                    if other["kind"] != "passport":
                        continue
                    expiry = _parse_date(other["fields"].get("expiryDate") or other["fields"].get("expiry_date"))
                    if expiry is not None:
                        valid = expiry >= travel_start
                        checks.append(
                            _check(
                                "CROSS_TRAVEL_PASSPORT_VALIDITY",
                                "Passport validity for travel",
                                "PASSED" if valid else "NEEDS_REVIEW",
                                (
                                    "The submitted passport remains valid for the application travel start date."
                                    if valid
                                    else "The submitted passport expires before the application travel start date."
                                ),
                                ["expiryDate", "travelStartDate"],
                            )
                        )
                        break

            supporting_docs = [item for item in doc_context if item["kind"] == "supporting"]
            if supporting_docs and not any(
                _value(item["fields"], field) is not None
                for item in supporting_docs
                for field in ("fullName", "passportNumber", "nationalIdNumber", "dateOfBirth", "monthlySalary", "depositAmount")
            ):
                checks.append(
                    _check(
                        "CROSS_SUPPORTING_EVIDENCE",
                        "Supporting document evidence",
                        "NEEDS_REVIEW",
                        "No applicant identity or comparable financial details could be extracted from the submitted supporting documents.",
                        ["extractedFields"],
                    )
                )
        elif not compared_fields:
            checks.append(
                _check(
                    "CROSS_SUPPORTING_EVIDENCE",
                    "Supporting document consistency",
                    "NEEDS_REVIEW",
                    "No shared applicant identity or financial evidence was available to compare with the other submitted documents.",
                    ["extractedFields"],
                )
            )

    return checks_by_document
