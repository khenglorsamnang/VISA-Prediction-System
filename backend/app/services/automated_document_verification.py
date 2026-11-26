from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .financial_analysis import calculate_financial_analysis
from .identity_normalization import name_tokens
from .transaction_normalization import normalize_transactions, parse_amount


_REQUIRED_FIELDS = {
    "passport": (
        "passportNumber",
        "fullName",
        "dateOfBirth",
        "expiryDate",
        "nationality",
    ),
    "national_id": ("fullName", "nationalIdNumber", "dateOfBirth"),
    "bank_statement": (
        "bankName",
        "accountHolderName",
        "accountNumber",
        "openingBalance",
        "closingBalance",
        "currency",
        "statementStartDate",
        "statementEndDate",
        "deposits",
        "withdrawals",
        "transactionHistory",
        "averageBalance",
    ),
    "fixed_deposit": (
        "fullName",
        "depositAmount",
        "currency",
        "maturityDate",
    ),
    "employment_letter": (
        "fullName",
        "employerName",
        "jobTitle",
        "monthlySalary",
        "documentDate",
    ),
    "salary_slip": ("fullName", "monthlySalary", "documentDate"),
    "marriage_certificate": ("spouse1Name", "spouse2Name", "marriageDate"),
    "birth_certificate": ("fullName", "dateOfBirth", "placeOfBirth"),
    "invitation_letter": (
        "inviterName",
        "inviteeName",
        "invitationDate",
        "stayAddress",
    ),
    "hotel_booking": ("guestName", "hotelName", "checkInDate", "checkOutDate"),
    "flight_booking": (
        "passengerName",
        "departureDate",
        "origin",
        "destination",
    ),
}
_DOCUMENT_TYPE_ALIASES = {
    "national_id_card": "national_id",
    "nationalid": "national_id",
    "nationalidcard": "national_id",
    "national_identity": "national_id",
    "national_identity_card": "national_id",
    "identity_card": "national_id",
    "id_card": "national_id",
    "identity_document": "national_id",
    "nid": "national_id",
    "employment_certificate": "employment_letter",
    "salary_certificate": "salary_slip",
    "flight_reservation": "flight_booking",
    "other_supporting_document": "other",
}
_NUMERIC_FIELDS = {
    "openingBalance",
    "closingBalance",
    "deposits",
    "withdrawals",
    "averageBalance",
    "depositAmount",
    "monthlySalary",
}
_DATE_FIELDS = {"dateOfBirth", "expiryDate", "statementStartDate", "statementEndDate", "maturityDate", "documentDate"}
_OCR_CONFIDENCE_MINIMUM = 0.5
_CAMBODIA_TIMEZONE = ZoneInfo("Asia/Phnom_Penh")


def _normalize_document_type(value: Any) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value or "").strip().casefold(),
    ).strip("_")
    return _DOCUMENT_TYPE_ALIASES.get(normalized, normalized)


def _normalized_nationality(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    aliases = {
        "kh": "cambodia",
        "khm": "cambodia",
        "cambodian": "cambodia",
        "kingdomofcambodia": "cambodia",
        "us": "unitedstates",
        "usa": "unitedstates",
        "american": "unitedstates",
        "unitedstatesofamerica": "unitedstates",
        "gb": "unitedkingdom",
        "gbr": "unitedkingdom",
        "british": "unitedkingdom",
        "uk": "unitedkingdom",
        "britain": "unitedkingdom",
        "greatbritain": "unitedkingdom",
        "unitedkingdomofgreatbritainandnorthernireland": "unitedkingdom",
        "canadian": "canada",
        "australian": "australia",
        "indian": "india",
        "chinese": "china",
        "thai": "thailand",
        "vietnamese": "vietnam",
        "laotian": "laos",
        "burmese": "myanmar",
        "filipino": "philippines",
        "indonesian": "indonesia",
        "malaysian": "malaysia",
        "singaporean": "singapore",
    }
    return aliases.get(normalized, normalized)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [])


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not _has_value(value):
        return None
    result, _, _ = parse_amount(value)
    return result


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    for pattern in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(normalized[:20], pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _valid_currency(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{3}|[$€£¥៛]", str(value or "").strip()))


def _valid_account_number(value: Any) -> bool:
    compact = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    return len(compact) >= 4


def _normalized_identity(value: Any) -> str:
    return re.sub(r"[^\w]", "", str(value or "").casefold(), flags=re.UNICODE)


def _normalized_person_name(value: Any) -> set[str]:
    cleaned = re.sub(
        r"^\s*(?:s\s*[:;|]\s*|name\s*[:;|]\s*)",
        "",
        str(value or ""),
        flags=re.I,
    )
    return name_tokens(cleaned)


def _local_today(now: datetime | None) -> date:
    if now is None:
        return datetime.now(_CAMBODIA_TIMEZONE).date()
    if now.tzinfo is None:
        return now.date()
    return now.astimezone(_CAMBODIA_TIMEZONE).date()


def evaluate_automated_document_verification(
    document: dict[str, Any],
    applicant: dict[str, Any],
    requirement: dict[str, Any],
    *,
    duplicate_reference: str | None = None,
    bank_statement_analysis: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    fields = document.get("extractedFields")
    fields = fields if isinstance(fields, dict) else {}
    document_type = _normalize_document_type(
        document.get("requirementCode")
        or requirement.get("code")
        or requirement.get("name")
    )
    if "photo" in document_type or "photo" in str(requirement.get("name") or "").casefold():
        document_type = "photo"
    detected_document_type = _normalize_document_type(
        document.get("detectedDocumentType")
    )
    if document_type == "national_id":
        field_aliases = {
            "fullName": ("full_name", "name"),
            "nationalIdNumber": (
                "national_id_number",
                "nationalId",
                "identityNumber",
                "idNumber",
                "documentNumber",
            ),
            "dateOfBirth": ("date_of_birth", "birthDate", "dob"),
            "nationality": ("nationality_name",),
            "gender": ("sex",),
            "expiryDate": ("expiry_date", "expirationDate"),
            "address": ("residentialAddress",),
        }
        fields = dict(fields)
        for canonical, aliases in field_aliases.items():
            if not _has_value(fields.get(canonical)):
                for alias in aliases:
                    if _has_value(fields.get(alias)):
                        fields[canonical] = fields[alias]
                        break
    configured_fields = requirement.get("requiredFields") or requirement.get(
        "required_fields"
    )
    if document_type == "national_id":
        field_alias_to_canonical = {
            alias: canonical
            for canonical, aliases in field_aliases.items()
            for alias in aliases
        }
        extra_fields = (
            tuple(
                field_alias_to_canonical.get(str(value), str(value))
                for value in configured_fields
                if str(value).strip()
            )
            if isinstance(configured_fields, list)
            else ()
        )
        required_fields = tuple(
            dict.fromkeys((*_REQUIRED_FIELDS["national_id"], *extra_fields))
        )
    else:
        required_fields = (
            tuple(str(value) for value in configured_fields if str(value).strip())
            if isinstance(configured_fields, list)
            else _REQUIRED_FIELDS.get(document_type, ())
        )
    checks: list[dict[str, Any]] = []
    warnings = [
        "Automated content checks do not establish document authenticity; no authoritative source was consulted.",
        "Visual alteration detection is unavailable in the current OCR pipeline.",
    ]
    mandatory_failures = 0
    review_checks = 0
    risk_score = 0
    technical_failure = str(document.get("ocrStatus") or "").upper() == "OCR_FAILED"

    def add_check(
        code: str,
        label: str,
        status: str,
        reason: str,
        *,
        fields_involved: tuple[str, ...] = (),
        risk_points: int = 0,
        mandatory: bool = False,
    ) -> None:
        nonlocal mandatory_failures, review_checks, risk_score
        checks.append(
            {
                "code": code,
                "label": label,
                "status": status,
                "reason": reason,
                "fields": list(fields_involved),
            }
        )
        if mandatory and status == "FAILED":
            mandatory_failures += 1
        if status in {"SUSPICIOUS", "NEEDS_REVIEW"}:
            review_checks += 1
            risk_score += risk_points

    raw_confidence = document.get("ocrConfidence")
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = None
    confidence_valid = confidence is not None and 0 <= confidence <= 1

    if document_type == "photo":
        add_check(
            "PHOTO_OCR_NOT_REQUIRED",
            "OCR text",
            "PASSED",
            "OCR text and OCR confidence are not used to verify an applicant photo.",
        )
        validation = document.get("photoValidation")
        photo_checks = validation.get("checks") if isinstance(validation, dict) else None
        if isinstance(photo_checks, list) and photo_checks:
            for check in photo_checks:
                if not isinstance(check, dict):
                    continue
                add_check(
                    str(check.get("code") or "PHOTO_CHECK"),
                    str(check.get("label") or "Photo validation"),
                    str(check.get("status") or "NEEDS_REVIEW"),
                    str(check.get("reason") or "Photo check requires staff inspection."),
                    fields_involved=tuple(check.get("fields") or ()),
                )
        else:
            add_check(
                "PHOTO_VALIDATION_UNAVAILABLE",
                "Photo image validation",
                "NEEDS_REVIEW",
                "Image checks have not completed; staff must inspect the photo.",
            )
        if isinstance(validation, dict) and validation.get("manualReviewRequired"):
            add_check(
                "PHOTO_MANUAL_REVIEW",
                "Manual photo review",
                "NEEDS_REVIEW",
                "Manual photo review is required by this visa type's configured rules.",
            )

        validation_checks = [check for check in checks if check["code"].startswith("PHOTO_") and check["code"] != "PHOTO_MANUAL_REVIEW"]
        failed_codes = {
            check["code"]
            for check in validation_checks
            if check["status"] == "FAILED"
        }
        rejected_codes = {
            "PHOTO_FORMAT",
            "PHOTO_DECODE",
            "PHOTO_RESOLUTION",
            "PHOTO_FACE_VISIBILITY",
        }
        if failed_codes & rejected_codes:
            status = "REJECTED"
        elif any(check["status"] in {"NEEDS_REVIEW", "SUSPICIOUS"} for check in checks):
            status = "NEEDS_REVIEW"
        elif any(check["status"] == "INSUFFICIENT_DATA" for check in checks):
            status = "NEEDS_REVIEW"
        else:
            status = "VERIFIED"
        scored_checks = [
            check
            for check in validation_checks
            if check["code"] in {
                "PHOTO_FORMAT",
                "PHOTO_DECODE",
                "PHOTO_RESOLUTION",
                "PHOTO_CLARITY",
                "PHOTO_EXPOSURE",
                "PHOTO_FACE_VISIBILITY",
                "PHOTO_FACE_POSITION",
            }
        ]
        photo_points_ratio = (
            sum(check["status"] == "PASSED" for check in scored_checks) / len(scored_checks)
            if scored_checks
            else 0.0
        )
        document_score = round(100 * photo_points_ratio)
        risk_level = "MEDIUM" if status == "NEEDS_REVIEW" else "LOW"
        reasons = [
            check["reason"]
            for check in checks
            if check["status"] != "PASSED"
        ]
        if status == "VERIFIED":
            reasons.append("Configured image and face-layout checks passed. Face detection does not establish identity or photo authenticity.")
        return {
            "status": status,
            "documentScore": document_score,
            "photoPointsRatio": round(photo_points_ratio, 4),
            "photoValidation": validation if isinstance(validation, dict) else None,
            "ocrConfidence": confidence,
            "ocrConfidencePercent": round(confidence * 100, 2) if confidence_valid else None,
            "riskScore": None,
            "riskLevel": risk_level,
            "passedChecks": [check for check in checks if check["status"] == "PASSED"],
            "failedChecks": [check for check in checks if check["status"] == "FAILED"],
            "needsReviewChecks": [check for check in checks if check["status"] in {"NEEDS_REVIEW", "SUSPICIOUS"}],
            "checks": checks,
            "warnings": [
                "Face detection only assesses image layout and does not prove identity or document authenticity."
            ],
            "reasons": list(dict.fromkeys(reasons)),
            "extractedFields": fields,
            "verificationTimestamp": (now or datetime.now(timezone.utc)).isoformat(),
            "verificationMethod": "photo_image_quality_and_face_layout_checks",
            "authenticityVerified": False,
            "authoritativeValidation": "NOT_PERFORMED",
        }

    if technical_failure and document_type != "photo":
        add_check(
            "OCR_FAILED",
            "OCR processing",
            "FAILED",
            "The OCR service could not process the uploaded document.",
        )
    elif document_type == "photo":
        add_check(
            "PHOTO_OCR_NOT_REQUIRED",
            "Applicant photo image validation",
            "PASSED",
            "Photographs are validated as images; OCR text fields are not required.",
        )
    elif str(document.get("ocrStatus") or "").upper() != "OCR_COMPLETED":
        add_check(
            "OCR_INCOMPLETE",
            "OCR completion",
            "INSUFFICIENT_DATA",
            "Every document page must finish OCR before verification.",
        )
    else:
        add_check(
            "OCR_COMPLETED",
            "OCR completion",
            "PASSED",
            "OCR completed for all reported pages.",
        )

    if detected_document_type and document_type and detected_document_type != document_type:
        add_check(
            "DOCUMENT_TYPE_MISMATCH",
            "Document type",
            "NEEDS_REVIEW",
            (
                f"The OCR text indicates {detected_document_type.replace('_', ' ')}, "
                f"but this upload is for {document_type.replace('_', ' ')}."
            ),
            fields_involved=("detectedDocumentType", "requirementCode"),
            risk_points=25,
        )

    if document_type == "photo":
        mime_type = str(document.get("mimeType") or "").casefold()
        file_size = document.get("size")
        try:
            valid_size = float(file_size or 0) > 0
        except (TypeError, ValueError):
            valid_size = False
        image_type_valid = mime_type in {"image/jpeg", "image/jpg", "image/png"}
        add_check(
            "PHOTO_IMAGE_FILE",
            "Photo image file",
            "PASSED" if image_type_valid and valid_size else "FAILED",
            "The uploaded photo is a readable image file."
            if image_type_valid and valid_size
            else "The uploaded photo is not a valid non-empty JPEG or PNG image.",
            mandatory=True,
        )
        quality_status = str(
            fields.get("imageQualityStatus") or fields.get("image_quality_status") or ""
        ).upper()
        if quality_status:
            quality_result = "PASSED" if quality_status in {"PASS", "PASSED", "GOOD"} else "NEEDS_REVIEW"
            quality_reason = (
                "Configured image-quality checks passed."
                if quality_result == "PASSED"
                else "Image quality or required visual evidence needs review."
            )
        else:
            quality_result = "INSUFFICIENT_DATA"
            quality_reason = "Image quality or required visual evidence is insufficient."
        add_check("PHOTO_IMAGE_QUALITY", "Photo image quality", quality_result, quality_reason)
    elif not required_fields:
        add_check(
            "UNSUPPORTED_DOCUMENT_TYPE",
            "Document requirements",
            "INSUFFICIENT_DATA",
            "No automated field-validation rules are configured for this document type.",
        )
    else:
        missing_required_fields = []
        for field in required_fields:
            value = fields.get(field)
            field_valid = _has_value(value)
            if field_valid and field in _NUMERIC_FIELDS:
                field_valid = _number(value) is not None
            if field_valid and field in _DATE_FIELDS:
                field_valid = _date(value) is not None
            if field == "currency":
                field_valid = _valid_currency(value)
            if field in {"accountNumber", "passportNumber", "nationalIdNumber"}:
                field_valid = _valid_account_number(value)
            if document_type == "national_id" and not field_valid:
                missing_required_fields.append(field)
            add_check(
                f"FIELD_{field.upper()}",
                f"Required field: {field}",
                "PASSED" if field_valid else "INSUFFICIENT_DATA",
                (
                    "A usable value was extracted."
                    if field_valid
                    else "A usable value could not be extracted."
                ),
                fields_involved=(field,),
            )

    add_check(
        "OCR_CONFIDENCE",
        "OCR confidence",
        "PASSED"
        if confidence_valid and confidence >= _OCR_CONFIDENCE_MINIMUM
        else "NEEDS_REVIEW"
        if confidence_valid
        else "INSUFFICIENT_DATA",
        (
            "OCR confidence meets the configured minimum; this measures text-reading confidence, not authenticity."
            if confidence_valid and confidence >= _OCR_CONFIDENCE_MINIMUM
            else "OCR confidence is missing, invalid, or too low for reliable field comparison."
        ),
    )

    if document_type == "passport":
        for label, document_field, applicant_field, normalize in (
            ("Passport number", "passportNumber", "passportNumber", _normalized_identity),
            ("Applicant name", "fullName", "fullName", _normalized_identity),
            ("Date of birth", "dateOfBirth", "dateOfBirth", _date),
            ("Nationality", "nationality", "nationality", _normalized_identity),
        ):
            extracted = fields.get(document_field)
            submitted = applicant.get(applicant_field)
            if not _has_value(extracted) or not _has_value(submitted):
                add_check(
                    f"MATCH_{document_field.upper()}",
                    f"{label} match",
                    "INSUFFICIENT_DATA",
                    "The document value or submitted applicant value is unavailable.",
                    fields_involved=(document_field, applicant_field),
                )
                continue
            normalized_extracted = normalize(extracted)
            normalized_submitted = normalize(submitted)
            if (
                normalized_extracted in (None, "")
                or normalized_submitted in (None, "")
            ):
                add_check(
                    f"MATCH_{document_field.upper()}",
                    f"{label} match",
                    "INSUFFICIENT_DATA",
                    "The document or submitted value could not be normalized reliably.",
                    fields_involved=(document_field, applicant_field),
                )
                continue
            matches = normalized_extracted == normalized_submitted
            add_check(
                f"MATCH_{document_field.upper()}",
                f"{label} match",
                "PASSED" if matches else "NEEDS_REVIEW",
                (
                    "The extracted value matches the applicant profile."
                    if matches
                    else "The extracted value differs from the applicant profile."
                ),
                fields_involved=(document_field, applicant_field),
                risk_points=40,
            )
        expiry = _date(fields.get("expiryDate"))
        if expiry:
            expired = expiry < (now or datetime.now(timezone.utc)).date()
            add_check(
                "PASSPORT_EXPIRY",
                "Passport validity",
                "FAILED" if expired else "PASSED",
                (
                    "The passport expiry date has passed."
                    if expired
                    else "The passport expiry date has not passed."
                ),
                fields_involved=("expiryDate",),
                mandatory=True,
            )

    if document_type == "national_id":
        for label, document_field, applicant_field, normalize in (
            ("Applicant name", "fullName", "fullName", _normalized_person_name),
            ("Date of birth", "dateOfBirth", "dateOfBirth", _date),
            ("Nationality", "nationality", "nationality", _normalized_nationality),
        ):
            extracted = fields.get(document_field)
            submitted = applicant.get(applicant_field)
            check_code = f"MATCH_{document_field.upper()}"
            if not _has_value(extracted) or not _has_value(submitted):
                add_check(
                    check_code,
                    f"{label} match",
                    "INSUFFICIENT_DATA",
                    "The document value or submitted applicant value is unavailable.",
                    fields_involved=(document_field, applicant_field),
                )
                continue
            normalized_extracted = normalize(extracted)
            normalized_submitted = normalize(submitted)
            if document_field == "fullName" and (
                not normalized_extracted or not normalized_submitted
            ):
                add_check(
                    check_code,
                    f"{label} match",
                    "NEEDS_REVIEW",
                    "The extracted or submitted name is ambiguous after normalization; manual review is required.",
                    fields_involved=(document_field, applicant_field),
                    risk_points=40,
                )
                continue
            if normalized_extracted in (None, "") or normalized_submitted in (None, ""):
                add_check(
                    check_code,
                    f"{label} match",
                    "INSUFFICIENT_DATA",
                    "The document or submitted value could not be normalized reliably.",
                    fields_involved=(document_field, applicant_field),
                )
                continue
            matches = normalized_extracted == normalized_submitted
            ambiguous_name = (
                document_field == "fullName"
                and not matches
                and bool(normalized_extracted)
                and bool(normalized_submitted)
                and (
                    normalized_extracted.issubset(normalized_submitted)
                    or normalized_submitted.issubset(normalized_extracted)
                )
            )
            add_check(
                check_code,
                f"{label} match",
                "PASSED" if matches else "NEEDS_REVIEW",
                (
                    (
                        "The extracted name matches the applicant profile after case, punctuation, whitespace, and name-order normalization."
                        if document_field == "fullName"
                        else "The extracted date of birth matches the applicant profile."
                        if document_field == "dateOfBirth"
                        else "The extracted nationality matches the applicant profile after country-alias normalization."
                    )
                    if matches
                    else "Only part of the name could be matched after normalization; manual review is required."
                    if ambiguous_name
                    else "The extracted value differs from the applicant profile and requires human review."
                ),
                fields_involved=(document_field, applicant_field),
                risk_points=40,
            )
        expiry_value = fields.get("expiryDate")
        if _has_value(expiry_value):
            expiry = _date(expiry_value)
            if expiry is None:
                add_check(
                    "NATIONAL_ID_EXPIRY",
                    "National ID validity",
                    "INSUFFICIENT_DATA",
                    "An expiry value is present but could not be parsed as a valid date.",
                    fields_involved=("expiryDate",),
                )
            else:
                expired = expiry < _local_today(now)
                add_check(
                    "NATIONAL_ID_EXPIRY",
                    "National ID validity",
                    "FAILED" if expired else "PASSED",
                    (
                        "The National ID expiry date has passed."
                        if expired
                        else "The National ID expiry date has not passed."
                    ),
                    fields_involved=("expiryDate",),
                    risk_points=60 if expired else 0,
                    mandatory=True,
                )

    if document_type == "bank_statement":
        statement_start = _date(fields.get("statementStartDate"))
        statement_end = _date(fields.get("statementEndDate"))
        if statement_start and statement_end:
            ordered = statement_start <= statement_end
            add_check(
                "STATEMENT_DATE_ORDER",
                "Statement date range",
                "PASSED" if ordered else "NEEDS_REVIEW",
                (
                    "The statement start date is on or before its end date."
                    if ordered
                    else "The statement end date precedes its start date."
                ),
                fields_involved=("statementStartDate", "statementEndDate"),
                risk_points=25,
            )
        else:
            add_check(
                "STATEMENT_DATE_ORDER",
                "Statement date range",
                "INSUFFICIENT_DATA",
                "Statement dates could not be parsed for comparison.",
                fields_involved=("statementStartDate", "statementEndDate"),
            )

        transactions = (
            fields.get("transactionHistory")
            or fields.get("transaction_history")
            or fields.get("transactions")
        )
        normalized = normalize_transactions(
            transactions,
            default_currency=fields.get("currency"),
        )
        valid_transactions = bool(normalized["transactions"]) and (
            normalized["valid_count"] == normalized["source_count"]
        )
        add_check(
            "TRANSACTION_HISTORY",
            "Transaction history",
            "PASSED" if valid_transactions else "INSUFFICIENT_DATA",
            (
                "Transaction rows include parseable dates and amounts."
                if valid_transactions
                else "Transaction rows are missing or contain unreadable dates or amounts."
            ),
            fields_involved=("transactionHistory",),
        )
        financial = calculate_financial_analysis(
            fields,
            normalized["transactions"],
        )
        reconciliation = financial.get("reconciliation") or {}
        reconciliation_status = reconciliation.get("status")
        if reconciliation_status == "mismatch":
            add_check(
                "BALANCE_RECONCILIATION",
                "Balance reconciliation",
                "NEEDS_REVIEW",
                "Opening balance plus extracted deposits minus withdrawals does not match the closing balance.",
                fields_involved=(
                    "openingBalance",
                    "deposits",
                    "withdrawals",
                    "closingBalance",
                ),
                risk_points=35,
            )
        elif reconciliation_status == "reconciled":
            add_check(
                "BALANCE_RECONCILIATION",
                "Balance reconciliation",
                "PASSED",
                "The available statement totals reconcile within the configured tolerance.",
                fields_involved=(
                    "openingBalance",
                    "deposits",
                    "withdrawals",
                    "closingBalance",
                ),
            )
        else:
            add_check(
                "BALANCE_RECONCILIATION",
                "Balance reconciliation",
                "INSUFFICIENT_DATA",
                "There is not enough reliable data to reconcile statement balances.",
                fields_involved=(
                    "openingBalance",
                    "deposits",
                    "withdrawals",
                    "closingBalance",
                ),
            )
        if isinstance(bank_statement_analysis, dict):
            financial_status = str(
                bank_statement_analysis.get("financialRequirementStatus") or ""
            ).upper()
            if financial_status == "PASS":
                add_check(
                    "BANK_FINANCIAL_REQUIREMENT",
                    "Configured financial requirements",
                    "PASSED",
                    "The actual ending balance for the complete three-month period meets the configured minimum; monthly balances are supporting evidence only.",
                )
            elif financial_status == "BELOW_MINIMUM":
                assessment = bank_statement_analysis.get("minimumBalanceAssessment") or {}
                add_check(
                    "BANK_FINANCIAL_REQUIREMENT",
                    "Configured financial requirements",
                    "NEEDS_REVIEW",
                    (
                        f"The period-end balance is {assessment.get('currency') or 'USD'} "
                        f"{assessment.get('balance')}; the configured minimum is "
                        f"{assessment.get('currency') or 'USD'} {assessment.get('minimum')}, "
                        f"with a shortfall of {assessment.get('shortfall')}. Manual review is required."
                    ),
                    fields_involved=("closingBalance",),
                )
            elif financial_status == "FAIL":
                add_check(
                    "BANK_FINANCIAL_REQUIREMENT",
                    "Configured financial requirements",
                    "FAILED",
                    "The available three-month balance evidence is below the configured requirement.",
                    fields_involved=("monthlyClosingBalances",),
                    mandatory=True,
                )
            else:
                add_check(
                    "BANK_FINANCIAL_REQUIREMENT",
                    "Configured financial requirements",
                    "INSUFFICIENT_DATA",
                    "Three complete months of usable balance evidence are not available.",
                    fields_involved=("monthlyClosingBalances",),
                )
            review_flags = bank_statement_analysis.get("reviewFlags") or []
            if review_flags:
                reasons = list(
                    dict.fromkeys(
                        str(item.get("reason") or "")
                        for item in review_flags
                        if isinstance(item, dict) and item.get("reason")
                    )
                )
                add_check(
                    "BANK_REVIEW_FLAGS",
                    "Financial analysis warnings",
                    "NEEDS_REVIEW",
                    " ".join(reasons)
                    or "Financial analysis found evidence that requires human review.",
                    fields_involved=("transactionHistory", "monthlyClosingBalances"),
                )
        else:
            add_check(
                "BANK_FINANCIAL_REQUIREMENT",
                "Configured financial requirements",
                "INSUFFICIENT_DATA",
                "Three complete months of usable balance evidence are not available.",
                fields_involved=("monthlyClosingBalances",),
            )
    if duplicate_reference:
        add_check(
            "DUPLICATE_DOCUMENT",
            "Duplicate document check",
            "SUSPICIOUS",
            f"The uploaded file matches document {duplicate_reference} in this application.",
            risk_points=35,
        )
    else:
        add_check(
            "DUPLICATE_DOCUMENT",
            "Duplicate document check",
            "PASSED",
            "No exact-file duplicate was found among the application's active documents.",
        )

    passed_checks = sum(check["status"] == "PASSED" for check in checks)
    document_score = round(100 * passed_checks / len(checks)) if checks else 0
    if technical_failure and document_type != "photo":
        status = "FAILED"
    elif mandatory_failures:
        status = "REJECTED"
    elif review_checks:
        status = "NEEDS_REVIEW"
    elif any(check["status"] == "INSUFFICIENT_DATA" for check in checks):
        status = "INSUFFICIENT_DATA"
    else:
        status = "VERIFIED"
    risk_score = min(100, risk_score + (60 * mandatory_failures))
    risk_level = (
        "UNAVAILABLE"
        if status == "INSUFFICIENT_DATA" and not risk_score
        else "HIGH"
        if risk_score >= 60
        else "MEDIUM"
        if risk_score >= 25
        else "LOW"
    )
    reasons = [
        check["reason"]
        for check in checks
        if check["status"] not in {"PASSED"}
    ]
    if status == "VERIFIED":
        reasons.append(
            "All configured automated content and consistency checks passed. This is not an authenticity determination."
        )
    return {
        "status": status,
        "documentScore": document_score,
        "ocrConfidence": confidence,
        "ocrConfidencePercent": (
            round(confidence * 100, 2) if confidence_valid else None
        ),
        "riskScore": risk_score if risk_level != "UNAVAILABLE" else None,
        "riskLevel": risk_level,
        "passedChecks": [check for check in checks if check["status"] == "PASSED"],
        "failedChecks": [
            check for check in checks if check["status"] == "FAILED"
        ],
        "needsReviewChecks": [
            check for check in checks if check["status"] in {"NEEDS_REVIEW", "SUSPICIOUS"}
        ],
        "checks": checks,
        "warnings": warnings,
        "reasons": list(dict.fromkeys(reasons)),
        "extractedFields": fields,
        "missingRequiredFields": (
            missing_required_fields
            if document_type == "national_id" and required_fields
            else []
        ),
        "verificationTimestamp": (now or datetime.now(timezone.utc)).isoformat(),
        "verificationMethod": "automated_content_and_consistency_checks",
        "authenticityVerified": False,
        "authoritativeValidation": "NOT_PERFORMED",
    }
