from datetime import datetime, timezone

import pytest

from backend.app.services.automated_document_verification import (
    evaluate_automated_document_verification,
)


_NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def _passport(
    *,
    passport_number: str = "K1234567",
    expiry_date: str = "2030-04-03",
    full_name: str = "Ada Example",
) -> dict:
    return {
        "requirementCode": "passport",
        "ocrStatus": "OCR_COMPLETED",
        "ocrConfidence": 0.96,
        "extractedFields": {
            "passportNumber": passport_number,
            "fullName": full_name,
            "dateOfBirth": "1990-04-03",
            "expiryDate": expiry_date,
            "nationality": "Cambodian",
        },
    }


def _applicant() -> dict:
    return {
        "passportNumber": "K1234567",
        "fullName": "Ada Example",
        "dateOfBirth": "1990-04-03",
        "nationality": "Cambodian",
    }


def _bank_statement(
    closing_balance: str = "1050.00",
    account_holder_name: str = "Ada Example",
) -> dict:
    return {
        "requirementCode": "bank_statement",
        "ocrStatus": "OCR_COMPLETED",
        "ocrConfidence": 0.92,
        "extractedFields": {
            "bankName": "Example Bank",
            "accountHolderName": account_holder_name,
            "accountNumber": "123456789",
            "openingBalance": "1000.00",
            "closingBalance": closing_balance,
            "currency": "USD",
            "statementStartDate": "2026-01-01",
            "statementEndDate": "2026-03-31",
            "deposits": "100.00",
            "withdrawals": "50.00",
            "averageBalance": "1025.00",
            "transactionHistory": [
                {
                    "date": "2026-02-01",
                    "description": "Salary",
                    "amount": "100.00",
                    "type": "credit",
                },
                {
                    "date": "2026-02-02",
                    "description": "Payment",
                    "amount": "50.00",
                    "type": "debit",
                },
            ],
        },
    }


def _national_id(**field_overrides) -> dict:
    fields = {
        "full_name": "Ada Example",
        "national_id_number": "KH123456789",
        "date_of_birth": "1990-04-03",
        "nationality": "Cambodian",
        "gender": "Female",
        "address": "10 Example Street",
    }
    fields.update(field_overrides)
    return {
        "requirementCode": "national_id",
        "ocrStatus": "OCR_COMPLETED",
        "ocrConfidence": 0.783,
        "extractedFields": fields,
    }


def test_complete_passport_that_matches_applicant_is_verified_not_authenticity_certified():
    result = evaluate_automated_document_verification(
        _passport(),
        _applicant(),
        {"code": "passport"},
        now=_NOW,
    )

    assert result["status"] == "VERIFIED"
    assert result["documentScore"] == 100
    assert result["ocrConfidencePercent"] == 96
    assert result["authenticityVerified"] is False
    assert result["authoritativeValidation"] == "NOT_PERFORMED"
    assert any("do not establish document authenticity" in item for item in result["warnings"])


def test_expired_passport_fails_a_defined_mandatory_requirement():
    result = evaluate_automated_document_verification(
        _passport(expiry_date="2020-04-03"),
        _applicant(),
        {"code": "passport"},
        now=_NOW,
    )

    assert result["status"] == "REJECTED"
    assert result["documentScore"] < 100
    assert any(check["code"] == "PASSPORT_EXPIRY" for check in result["failedChecks"])
    assert result["riskLevel"] == "HIGH"


def test_identity_mismatch_is_suspicious_and_missing_fields_are_insufficient():
    mismatch = evaluate_automated_document_verification(
        _passport(passport_number="X7654321"),
        _applicant(),
        {"code": "passport"},
        now=_NOW,
    )
    incomplete_document = _passport()
    incomplete_document["extractedFields"].pop("dateOfBirth")
    incomplete = evaluate_automated_document_verification(
        incomplete_document,
        _applicant(),
        {"code": "passport"},
        now=_NOW,
    )

    assert mismatch["status"] == "NEEDS_REVIEW"
    assert any(check["code"] == "MATCH_PASSPORTNUMBER" for check in mismatch["checks"])
    assert incomplete["status"] == "INSUFFICIENT_DATA"
    assert incomplete["documentScore"] < 100


@pytest.mark.parametrize(
    "document_type",
    [
        "National ID",
        "National ID Card",
        "National Id Card",
        "Identity Card",
        "ID Card",
        "national_id",
    ],
)
def test_national_id_aliases_validate_required_fields_and_profile_values(document_type):
    document = _national_id()
    document["requirementCode"] = document_type
    result = evaluate_automated_document_verification(
        document,
        {
            "fullName": " ADA,   EXAMPLE ",
            "dateOfBirth": "03/04/1990",
            "nationality": "KH",
        },
        {
            "code": document_type,
            "requiredFields": [
                "full_name",
                "national_id_number",
                "date_of_birth",
            ],
        },
        now=_NOW,
    )

    assert result["status"] == "VERIFIED"
    assert result["missingRequiredFields"] == []
    assert not any(
        check["code"] == "UNSUPPORTED_DOCUMENT_TYPE" for check in result["checks"]
    )
    assert result["authenticityVerified"] is False
    assert {
        "MATCH_FULLNAME",
        "MATCH_DATEOFBIRTH",
        "MATCH_NATIONALITY",
    }.issubset({check["code"] for check in result["passedChecks"]})
    reasons = {check["code"]: check["reason"] for check in result["checks"]}
    assert "name matches" in reasons["MATCH_FULLNAME"]
    assert "date of birth matches" in reasons["MATCH_DATEOFBIRTH"]
    assert "nationality matches" in reasons["MATCH_NATIONALITY"]


def test_national_id_missing_required_fields_are_named_and_not_verified():
    result = evaluate_automated_document_verification(
        _national_id(national_id_number="", date_of_birth="not a date"),
        {
            "fullName": "Ada Example",
            "dateOfBirth": "1990-04-03",
            "nationality": "Cambodia",
        },
        {"name": "National ID Card"},
        now=_NOW,
    )

    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["missingRequiredFields"] == [
        "nationalIdNumber",
        "dateOfBirth",
    ]
    assert {
        "FIELD_NATIONALIDNUMBER",
        "FIELD_DATEOFBIRTH",
    }.issubset(
        {
            check["code"]
            for check in result["checks"]
            if check["status"] == "INSUFFICIENT_DATA"
        }
    )


def test_national_id_profile_mismatches_require_review():
    result = evaluate_automated_document_verification(
        _national_id(
            full_name="Different Person",
            date_of_birth="1985-02-01",
            nationality="Thai",
        ),
        {
            "fullName": "Ada Example",
            "dateOfBirth": "1990-04-03",
            "nationality": "Cambodian",
        },
        {"code": "national_id"},
        now=_NOW,
    )

    assert result["status"] == "NEEDS_REVIEW"
    assert {
        "MATCH_FULLNAME",
        "MATCH_DATEOFBIRTH",
        "MATCH_NATIONALITY",
    }.issubset({check["code"] for check in result["needsReviewChecks"]})
    assert result["authenticityVerified"] is False


def test_national_id_accepts_reversed_name_order_and_future_cambodian_expiry():
    result = evaluate_automated_document_verification(
        _national_id(
            full_name="S: KONG RATHA",
            national_id_number="011279084",
            date_of_birth="1986-05-13",
            nationality="Cambodia",
            expiry_date="2026-11-27",
        ),
        {
            "fullName": "Ratha, Kong",
            "dateOfBirth": "1986-05-13",
            "nationality": "KHM",
        },
        {"code": "national_id_card", "weight": 15},
        now=datetime(2026, 11, 18, 15, 56, tzinfo=timezone.utc),
    )

    assert result["status"] == "VERIFIED"
    assert result["documentScore"] == 100
    assert next(
        check for check in result["checks"] if check["code"] == "MATCH_FULLNAME"
    )["status"] == "PASSED"
    assert next(
        check
        for check in result["checks"]
        if check["code"] == "NATIONAL_ID_EXPIRY"
    )["status"] == "PASSED"
    assert result["authenticityVerified"] is False


def test_national_id_expiry_uses_phnom_penh_date_boundary():
    document = _national_id(expiry_date="2026-11-18")
    applicant = {
        "fullName": "Ada Example",
        "dateOfBirth": "1990-04-03",
        "nationality": "Cambodia",
    }

    before_local_midnight = evaluate_automated_document_verification(
        document,
        applicant,
        {"code": "national_id"},
        now=datetime(2026, 11, 18, 16, 59, tzinfo=timezone.utc),
    )
    after_local_midnight = evaluate_automated_document_verification(
        document,
        applicant,
        {"code": "national_id"},
        now=datetime(2026, 11, 18, 17, 1, tzinfo=timezone.utc),
    )

    assert next(
        check
        for check in before_local_midnight["checks"]
        if check["code"] == "NATIONAL_ID_EXPIRY"
    )["status"] == "PASSED"
    assert after_local_midnight["status"] == "REJECTED"
    assert next(
        check
        for check in after_local_midnight["failedChecks"]
        if check["code"] == "NATIONAL_ID_EXPIRY"
    )["status"] == "FAILED"


def test_partial_national_id_name_is_reviewed_as_ambiguous():
    result = evaluate_automated_document_verification(
        _national_id(full_name="KONG"),
        {
            "fullName": "KONG RATHA",
            "dateOfBirth": "1990-04-03",
            "nationality": "Cambodia",
        },
        {"code": "national_id"},
        now=_NOW,
    )

    name_check = next(
        check for check in result["checks"] if check["code"] == "MATCH_FULLNAME"
    )
    assert result["status"] == "NEEDS_REVIEW"
    assert name_check["status"] == "NEEDS_REVIEW"
    assert "part of the name" in name_check["reason"]


def test_bank_statement_reconciliation_and_exact_duplicate_detection():
    reconciled = evaluate_automated_document_verification(
        _bank_statement(),
        _applicant(),
        {"code": "bank_statement"},
        bank_statement_analysis={
            "financialRequirementStatus": "PASS",
            "reviewFlags": [],
        },
        now=_NOW,
    )
    mismatch = evaluate_automated_document_verification(
        _bank_statement(closing_balance="1150.00"),
        _applicant(),
        {"code": "bank_statement"},
        bank_statement_analysis={
            "financialRequirementStatus": "PASS",
            "reviewFlags": [],
        },
        now=_NOW,
    )
    duplicate = evaluate_automated_document_verification(
        _bank_statement(),
        _applicant(),
        {"code": "bank_statement"},
        duplicate_reference="DOC-1234",
        bank_statement_analysis={
            "financialRequirementStatus": "PASS",
            "reviewFlags": [],
        },
        now=_NOW,
    )

    assert reconciled["status"] == "VERIFIED"
    assert any(check["code"] == "BALANCE_RECONCILIATION" for check in reconciled["passedChecks"])
    assert mismatch["status"] == "NEEDS_REVIEW"
    assert duplicate["status"] == "NEEDS_REVIEW"
    assert any(check["code"] == "DUPLICATE_DOCUMENT" for check in duplicate["checks"])


def test_bank_statement_with_different_account_holder_is_not_name_reviewed():
    financial_analysis = {
        "financialRequirementStatus": "PASS",
        "reviewFlags": [],
    }
    matching = evaluate_automated_document_verification(
        _bank_statement(),
        _applicant(),
        {"code": "bank_statement"},
        bank_statement_analysis=financial_analysis,
        now=_NOW,
    )
    different_holder = evaluate_automated_document_verification(
        _bank_statement(account_holder_name="SIM KEE SIANG"),
        _applicant(),
        {"code": "bank_statement"},
        bank_statement_analysis=financial_analysis,
        now=_NOW,
    )

    assert different_holder["status"] == matching["status"] == "VERIFIED"
    assert different_holder["documentScore"] == matching["documentScore"]
    assert not any(
        check["code"] in {"ACCOUNT_HOLDER_MATCH", "NAME_MISMATCH"}
        for check in different_holder["checks"]
    )


def test_bank_statement_without_three_month_financial_analysis_is_not_verified():
    result = evaluate_automated_document_verification(
        _bank_statement(),
        _applicant(),
        {"code": "bank_statement"},
        now=_NOW,
    )

    assert result["status"] == "INSUFFICIENT_DATA"
    assert any(
        check["code"] == "BANK_FINANCIAL_REQUIREMENT"
        and check["status"] == "INSUFFICIENT_DATA"
        for check in result["checks"]
    )


def test_bank_statement_below_minimum_is_warning_for_manual_review_not_failure():
    result = evaluate_automated_document_verification(
        _bank_statement(),
        _applicant(),
        {"code": "bank_statement"},
        bank_statement_analysis={
            "financialRequirementStatus": "BELOW_MINIMUM",
            "minimumBalanceAssessment": {
                "balance": 3084.87,
                "currency": "USD",
                "minimum": 4000,
                "shortfall": 915.13,
            },
            "reviewFlags": [],
        },
        now=_NOW,
    )

    financial_check = next(
        check
        for check in result["checks"]
        if check["code"] == "BANK_FINANCIAL_REQUIREMENT"
    )
    assert result["status"] == "NEEDS_REVIEW"
    assert financial_check["status"] == "NEEDS_REVIEW"
    assert "915.13" in financial_check["reason"]
    assert not result["failedChecks"]


def test_partial_ocr_and_unsupported_types_cannot_be_verified():
    partial = evaluate_automated_document_verification(
        {**_passport(), "ocrStatus": "OCR_PARTIAL"},
        _applicant(),
        {"code": "passport"},
        now=_NOW,
    )
    unknown = evaluate_automated_document_verification(
        {
            "requirementCode": "unknown",
            "ocrStatus": "OCR_COMPLETED",
            "ocrConfidence": 0.99,
            "extractedFields": {"value": "extracted"},
        },
        _applicant(),
        {"code": "unknown"},
        now=_NOW,
    )

    assert partial["status"] == "INSUFFICIENT_DATA"
    assert unknown["status"] == "INSUFFICIENT_DATA"
