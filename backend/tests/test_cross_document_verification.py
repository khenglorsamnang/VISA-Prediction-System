from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.api import documents as documents_api


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [deepcopy(row) for row in self.rows]


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, _query: dict[str, Any]) -> _Cursor:
        return _Cursor(self.rows)

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        expected = query.get("_id", {})
        candidates = expected.get("$in", [expected]) if isinstance(expected, dict) else [expected]
        return next(
            (deepcopy(row) for row in self.rows if row.get("_id") in candidates),
            None,
        )

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> SimpleNamespace:
        expected = query.get("_id", {})
        candidates = expected.get("$in", [expected]) if isinstance(expected, dict) else [expected]
        for row in self.rows:
            if row.get("_id") in candidates:
                row.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class _Database:
    def __init__(self, collections: dict[str, _Collection]) -> None:
        self.collections = collections

    def __getitem__(self, name: str) -> _Collection:
        return self.collections[name]


def _case(
    passport_name: str = "Sample Applicant",
    bank_holder_name: str = "Sample Applicant",
) -> tuple[_Database, dict[str, Any]]:
    bank_score = {
        "available": True,
        "points": 21.99,
        "maximum": 45,
        "components": {
            "coverage": {"points": 10.72, "maximum": 12},
            "minimumBalance": {"points": 0.27, "maximum": 6},
            "averageBalance": {"points": 6, "maximum": 6},
            "recency": {"points": 0, "maximum": 5},
            "period": {"points": 3, "maximum": 3},
            "passThrough": {"points": 0, "maximum": 6},
            "sourceConcentration": {"status": "NEEDS_EVIDENCE"},
            "incomeRegularity": {"points": 2, "maximum": 2},
            "savingsRate": {"points": 0, "maximum": 2},
            "ocrConfidence": {"status": "UNAVAILABLE"},
            "balanceReconciliation": {"status": "MATCHED"},
        },
    }
    requirements = [
        {"_id": "bank-req", "code": "bank_statement", "name": "Bank Statement", "required": True, "acceptedFormats": ["pdf"]},
        {"_id": "passport-req", "code": "passport", "name": "Passport", "required": True},
        {"_id": "photo-req", "code": "applicant_photo", "name": "Applicant Photo", "required": True},
        {"_id": "id-req", "code": "national_id", "name": "National ID Card", "required": True},
        {"_id": "support-req", "code": "supporting_documents", "name": "Supporting Documents", "required": True, "acceptedFormats": ["pdf", "jpg", "jpeg", "png"]},
    ]
    bank = {
        "_id": "bank-doc",
        "applicationId": "application",
        "requirementId": "bank-req",
        "requirementCode": "bank_statement",
        "fileName": "statement.pdf",
        "mimeType": "application/pdf",
        "status": "NEEDS_REVIEW",
        "awardedPoints": 0,
        "bankStatementAnalysis": {"bankStatementScore": bank_score, "historicalScore": 21.99},
        "extractedFields": {
            "accountHolderName": bank_holder_name,
            "statementStartDate": "2026-01-01",
            "statementEndDate": "2026-03-31",
        },
        "automatedVerification": {
            "status": "VERIFIED",
            "baseStatus": "VERIFIED",
            "documentScore": 98,
            "reasons": [],
            "checks": [
                {"code": "BANK_INTERNAL", "status": "PASSED"},
                {"code": "OCR_CONFIDENCE", "status": "NEEDS_REVIEW"},
            ],
        },
        "automatedVerificationHistory": [],
    }
    documents = [
        bank,
        {
            "_id": "passport-doc",
            "applicationId": "application",
            "requirementId": "passport-req",
            "requirementCode": "passport",
            "status": "VERIFIED",
            "extractedFields": {"fullName": passport_name},
        },
        {
            "_id": "photo-doc",
            "applicationId": "application",
            "requirementId": "photo-req",
            "requirementCode": "applicant_photo",
            "status": "VERIFIED",
        },
        {
            "_id": "id-doc",
            "applicationId": "application",
            "requirementId": "id-req",
            "requirementCode": "national_id",
            "status": "VERIFIED",
            "extractedFields": {"fullName": "Sample Applicant"},
        },
        {
            "_id": "support-doc",
            "applicationId": "application",
            "requirementId": "support-req",
            "requirementCode": "supporting_documents",
            "fileName": "evidence.pdf",
            "mimeType": "application/pdf",
            "status": "NEEDS_REVIEW",
            "extractedFields": {"fullName": "Sample Applicant"},
            "automatedVerification": {
                "status": "NEEDS_REVIEW",
                "baseStatus": "NEEDS_REVIEW",
                "checks": [],
                "reasons": [],
            },
            "automatedVerificationHistory": [],
        },
    ]
    application = {
        "_id": "application",
        "customerId": "applicant",
        "requirementSnapshot": requirements,
    }
    return (
        _Database(
            {
                "visaapplications": _Collection([application]),
                "customers": _Collection(
                    [{"_id": "applicant", "fullName": "Sample Applicant"}]
                ),
                "visarequirements": _Collection([]),
                "documents": _Collection(documents),
            }
        ),
        bank_score,
    )


@pytest.mark.asyncio
async def test_bank_statement_verifies_with_cross_document_checks_without_changing_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, bank_score = _case()

    async def skip_application_score_refresh(_database: Any, _application: Any) -> None:
        return None

    monkeypatch.setattr(
        documents_api, "_refresh_application_score", skip_application_score_refresh
    )
    await documents_api._refresh_cross_document_consistency(database, "application")

    bank = database["documents"].rows[0]
    assert bank["status"] == "VERIFIED"
    assert all(
        check["status"] == "PASSED"
        for check in bank["automatedVerification"]["crossDocumentChecks"]
    )
    assert bank["bankStatementAnalysis"]["bankStatementScore"] == bank_score
    assert bank["bankStatementAnalysis"]["historicalScore"] == 21.99
    assert bank["awardedPoints"] == 0


@pytest.mark.asyncio
async def test_different_account_holder_name_does_not_block_bank_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, bank_score = _case(bank_holder_name="SIM KEE SIANG")

    async def skip_application_score_refresh(_database: Any, _application: Any) -> None:
        return None

    monkeypatch.setattr(
        documents_api, "_refresh_application_score", skip_application_score_refresh
    )
    await documents_api._refresh_cross_document_consistency(database, "application")

    bank = database["documents"].rows[0]
    assert bank["status"] == "VERIFIED"
    assert not any(
        check["code"] == "CROSS_FULLNAME"
        for check in bank["automatedVerification"]["crossDocumentChecks"]
    )
    assert bank["extractedFields"]["accountHolderName"] == "SIM KEE SIANG"
    assert bank["bankStatementAnalysis"]["bankStatementScore"] == bank_score


@pytest.mark.asyncio
async def test_positive_document_score_alone_does_not_verify_bank_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _ = _case()
    bank = database["documents"].rows[0]
    bank["automatedVerification"]["checks"][0]["status"] = "NEEDS_REVIEW"
    bank["automatedVerification"]["documentScore"] = 99

    async def skip_application_score_refresh(_database: Any, _application: Any) -> None:
        return None

    monkeypatch.setattr(
        documents_api, "_refresh_application_score", skip_application_score_refresh
    )
    await documents_api._refresh_cross_document_consistency(database, "application")

    assert bank["status"] == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_verified_aba_requires_review_and_reconciliation_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _ = _case()
    bank = database["documents"].rows[0]
    bank["status"] = "NEEDS_REVIEW"
    bank["automatedVerification"]["status"] = "NEEDS_REVIEW"
    bank["automatedVerification"]["baseStatus"] = "NEEDS_REVIEW"
    bank["abaStatementAnalysis"] = {
        "reviewStatus": "VERIFIED",
        "reconciliationStatus": "RECONCILED",
        "statementStrengthScore": 5.5,
        "score": {"provisional": False},
    }

    async def skip_application_score_refresh(_database: Any, _application: Any) -> None:
        return None

    monkeypatch.setattr(
        documents_api, "_refresh_application_score", skip_application_score_refresh
    )
    await documents_api._refresh_cross_document_consistency(database, "application")

    assert bank["status"] == "VERIFIED"
    aba_check=next(check for check in bank["automatedVerification"]["crossDocumentChecks"] if check["code"]=="CROSS_ABA_REVIEW")
    assert aba_check["status"]=="PASSED"
    assert "account relationship" not in aba_check["reason"].lower()


@pytest.mark.asyncio
async def test_aba_cross_check_explains_actual_unresolved_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _ = _case()
    bank = database["documents"].rows[0]
    bank["abaStatementAnalysis"] = {
        "reviewStatus": "PENDING_REVIEW",
        "reconciliationStatus": "NEEDS_REVIEW",
        "reviewBlockers": [{
            "code": "summary_field_conflict",
            "field": "ending",
            "pages": [1, 2],
            "message": "Conflicting ending balance values were extracted from multiple pages.",
        }],
    }

    async def skip_application_score_refresh(_database: Any, _application: Any) -> None:
        return None

    monkeypatch.setattr(
        documents_api, "_refresh_application_score", skip_application_score_refresh
    )
    await documents_api._refresh_cross_document_consistency(database, "application")

    aba_check=next(check for check in bank["automatedVerification"]["crossDocumentChecks"] if check["code"]=="CROSS_ABA_REVIEW")
    assert aba_check["status"]=="NEEDS_REVIEW"
    assert "Conflicting ending balance values" in aba_check["reason"]
    assert "account relationship" not in aba_check["reason"].lower()
