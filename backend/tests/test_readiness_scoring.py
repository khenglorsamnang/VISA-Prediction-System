from copy import deepcopy
from datetime import date, timedelta

from app.services.readiness_scoring import (
    calculate_bank_score_assessment,
    calculate_readiness_score,
)


def _daily_bank_document(balance: float = 5000) -> dict:
    start = date(2026, 1, 1)
    transactions = [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "amount": 0.01,
            "type": "credit",
            "balance": balance,
            "sourceRowId": f"row-{offset}",
            "page": "1",
        }
        for offset in range(90)
    ]
    return {
        "_id": "bank-doc",
        "requirementId": "bank",
        "status": "NEEDS_REVIEW",
        "extractedFields": {
            "currency": "USD",
            "statementStartDate": start.isoformat(),
            "statementEndDate": (start + timedelta(days=89)).isoformat(),
            "openingBalance": balance,
            "transactionHistory": transactions,
        },
        "ocrConfidence": None,
    }


def _canonical_bank_analysis(
    *,
    top_level_points: float = 30.04,
    transaction_reviews: list[dict] | None = None,
) -> dict:
    component_values = {
        "ending_balance": ("Ending Balance", 10.88, 22.5),
        "starting_balance": ("Starting Balance", 6.66, 6.75),
        "transaction_history": ("Transaction History", 5.75, 9.0),
        "transaction_period": ("Transaction Period", 6.75, 6.75),
    }
    return {
        "financialRequirementStatus": "PASS",
        "reconciliation": {"status": "RECONCILED"},
        "bankStatementScore": {
            "available": True,
            "points": top_level_points,
            "maximum": 45,
            "totalPenaltyPoints": 0.5,
            "transactionReviews": transaction_reviews or [],
            "components": {
                key: {
                    "points": points,
                    "maximum": maximum,
                    "available": True,
                    "explanation": name,
                    "evidence": {},
                }
                for key, (name, points, maximum) in component_values.items()
            },
        },
    }


def test_missing_unclassified_required_document_applies_gate():
    result = calculate_readiness_score(
        requirements=[
            {"requirementId": "r1", "code": "travel_itinerary", "name": "Travel itinerary", "required": True}
        ],
        documents=[],
        as_of=date(2026, 11, 16),
    )
    assert result["gateApplied"] is True
    assert result["score"] <= 40
    assert result["missingRequiredDocuments"] == ["Travel itinerary"]


def test_optional_missing_document_does_not_apply_gate():
    result = calculate_readiness_score(
        requirements=[
            {"requirementId": "r1", "code": "travel_itinerary", "name": "Travel itinerary", "required": False}
        ],
        documents=[],
        as_of=date(2026, 11, 16),
    )
    assert result["gateApplied"] is False
    assert result["missingRequiredDocuments"] == []


def test_score_is_readiness_not_approval_probability():
    result = calculate_readiness_score([], [], as_of=date(2026, 11, 16))
    assert result["scoreType"] == "READINESS_SCORE"
    assert "not a visa approval probability" in result["disclaimer"]


def test_bank_account_holder_name_does_not_change_the_45_point_financial_score():
    requirement = {
        "requirementId": "bank",
        "code": "bank_statement",
        "name": "Bank statement",
    }
    financial_fields = {
        "currency": "USD",
        "statementStartDate": "2026-01-01",
        "statementEndDate": "2026-03-31",
        "openingBalance": 5000,
        "closingBalance": 6000,
        "transactionHistory": [
            {
                "date": "2026-01-15",
                "amount": 1000,
                "type": "credit",
                "balance": 6000,
            }
        ],
    }

    def score_for(account_holder: str):
        return calculate_readiness_score(
            [requirement],
            [
                {
                    "requirementId": "bank",
                    "status": "VERIFIED",
                    "extractedFields": {
                        **financial_fields,
                        "accountHolderName": account_holder,
                    },
                }
            ],
            applicant={"fullName": "KHENG LORSAMNANG"},
            as_of=date(2026, 11, 16),
        )

    matching = score_for("KHENG LORSAMNANG")
    different = score_for("SIM KEE SIANG")

    assert matching["components"]["bank_statement"]["maximum"] == 45
    assert different["components"]["bank_statement"]["points"] == matching[
        "components"
    ]["bank_statement"]["points"]
    assert different["score"] == matching["score"]
    assert not any(
        check["field"] == "fullName"
        and check["documentType"] == "bank_statement"
        for check in different["components"]["consistency"]["checks"]
    )


def test_score_components_override_conflicting_top_level_total():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis(
        transaction_reviews=[{"transactionIndex": 0, "status": "PENDING_REVIEW"}]
    )
    document["bankStatementAnalysis"] = bank_analysis
    assessment = calculate_bank_score_assessment(
        document,
        bank_analysis,
        as_of=date(2026, 3, 31),
    )

    assert assessment["computedBankScore"]["available"] is True
    assert assessment["computedBankScore"]["points"] == 30.04
    assert assessment["documentScore"]["points"] == 30.04
    assert assessment["bankStatementScore"]["points"] == 30.04
    assert assessment["computedBankScore"]["points"] == round(
        sum(item["points"] for item in assessment["computedBankScore"]["components"]), 2
    )
    assert assessment["requirementContribution"]["available"] is True
    assert assessment["requirementContribution"]["maximum"] == 45
    assert assessment["requirementContribution"]["points"] == assessment[
        "computedBankScore"
    ]["points"]
    assert assessment["bankStatementScore"]["components"]["transaction_history"]["points"] == 5.75
    assert assessment["confirmedBankScore"]["points"] is None
    assert assessment["confirmedBankScore"]["status"] == "NOT_YET_CONFIRMED"
    assert assessment["staffReviewStatus"] == "PENDING_REVIEW"
    assert assessment["sourceReviewStatus"] == "PENDING_REVIEW"
    assert assessment["bankStatementScore"]["confirmedPoints"] is None
    assert assessment["financialEvidenceStatus"] == "RECONCILED"
    readiness = calculate_readiness_score(
        [{"requirementId": "bank", "code": "bank_statement", "name": "Bank statement"}],
        [document],
        bank_statement_analyses={"bank-doc": bank_analysis},
    )
    assert readiness["components"]["bank_statement"]["points"] == 30.04


def test_computed_score_uses_canonical_component_sum_without_reapplying_source_penalty():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis(
        top_level_points=30.04,
        transaction_reviews=[{
            "transactionIndex": 0,
            "status": "UNVERIFIED",
            "sourceCategory": "UNKNOWN",
            "reviewerId": "staff-1",
        }],
    )
    bank_analysis["bankStatementScore"]["components"]["transaction_history"]["points"] = 2.25
    bank_analysis["bankStatementScore"]["totalPenaltyPoints"] = 4.5

    assessment = calculate_bank_score_assessment(document, bank_analysis)
    component_total = round(
        sum(item["points"] for item in assessment["computedBankScore"]["components"]),
        2,
    )

    assert assessment["computedBankScore"]["points"] == 26.54
    assert assessment["bankStatementScore"]["points"] == 26.54
    assert assessment["requirementContribution"]["points"] == 26.54
    assert component_total == 26.54
    assert assessment["bankStatementScore"]["components"]["transaction_history"]["points"] == 2.25
    assert assessment["bankStatementScore"]["totalPenaltyPoints"] == 4.5


def test_source_unverified_history_score_drives_all_computed_totals():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis(
        top_level_points=30.04,
        transaction_reviews=[{
            "transactionIndex": 0,
            "status": "UNVERIFIED",
            "sourceCategory": "UNKNOWN",
            "reviewerId": "staff-1",
        }],
    )
    bank_analysis["bankStatementScore"]["components"]["transaction_history"]["points"] = 6.25
    bank_analysis["bankStatementScore"]["totalPenaltyPoints"] = 0.5

    assessment = calculate_bank_score_assessment(document, bank_analysis)
    history = next(
        component
        for component in assessment["computedBankScore"]["components"]
        if component["key"] == "transaction_history"
    )

    assert history["points"] == 6.25
    assert assessment["computedBankScore"]["points"] == 30.54
    assert assessment["bankStatementScore"]["points"] == 30.54
    assert assessment["requirementContribution"]["points"] == 30.54
    assert assessment["confirmedBankScore"]["status"] == "CONFIRMED"
    assert assessment["confirmedBankScore"]["points"] == 30.54


def test_stale_component_maxima_do_not_change_the_four_category_score_scale():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis()
    components = bank_analysis["bankStatementScore"]["components"]
    components["ending_balance"]["maximum"] = 14.75
    components["starting_balance"]["maximum"] = 16.75

    assessment = calculate_bank_score_assessment(document, bank_analysis)

    assert assessment["computedBankScore"]["available"] is False
    assert assessment["computedBankScore"]["points"] is None
    assert assessment["computedBankScore"]["maximum"] == 45
    assert {
        key: item["maximum"]
        for key, item in assessment["bankStatementScore"]["components"].items()
    } == {
        "ending_balance": 22.5,
        "starting_balance": 6.75,
        "transaction_history": 9.0,
        "transaction_period": 6.75,
    }


def test_authenticity_not_verified_does_not_zero_computed_bank_score():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis()
    bank_analysis["authenticityStatus"] = "NOT_VERIFIED"
    assessment = calculate_bank_score_assessment(
        document,
        bank_analysis,
        as_of=date(2026, 3, 31),
    )

    assert assessment["computedBankScore"]["available"] is True
    assert assessment["computedBankScore"]["points"] == 30.04
    assert assessment["authenticityStatus"] == "NOT_VERIFIED"
    assert assessment["confirmedBankScore"]["points"] is None
    assert assessment["confirmedBankScore"]["status"] == "NOT_YET_CONFIRMED"


def test_saved_unverified_source_decision_is_not_reported_as_pending():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis(
        transaction_reviews=[{
            "transactionIndex": 0,
            "status": "UNVERIFIED",
            "sourceCategory": "UNKNOWN",
            "reviewerId": "staff-1",
        }]
    )

    assessment = calculate_bank_score_assessment(document, bank_analysis)

    assert assessment["sourceReviewStatus"] == "UNVERIFIED"
    assert assessment["bankStatementScore"]["pendingTransactionReviews"] == 0
    assert assessment["staffReviewStatus"] == "VERIFIED"
    assert assessment["documentReviewStatus"] == "VERIFIED"
    assert assessment["authenticityStatus"] == "NOT_VERIFIED"
    assert assessment["bankStatementScore"]["transactionReviews"][0]["status"] == "UNVERIFIED"


def test_staff_and_document_review_statuses_require_their_own_saved_decisions():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis(
        transaction_reviews=[{
            "transactionIndex": 0,
            "status": "UNVERIFIED",
            "sourceCategory": "UNKNOWN",
            "reviewerId": "staff-1",
        }]
    )
    document["staffReviewStatus"] = "CONFIRMED"

    staff_reviewed = calculate_bank_score_assessment(document, bank_analysis)

    assert staff_reviewed["staffReviewStatus"] == "VERIFIED"
    assert staff_reviewed["documentReviewStatus"] == "VERIFIED"
    assert staff_reviewed["sourceReviewStatus"] == "UNVERIFIED"
    assert staff_reviewed["confirmedBankScore"]["status"] == "CONFIRMED"

    document.pop("staffReviewStatus")
    document["documentReviewStatus"] = "CONFIRMED"
    document_reviewed = calculate_bank_score_assessment(document, bank_analysis)

    assert document_reviewed["staffReviewStatus"] == "VERIFIED"
    assert document_reviewed["documentReviewStatus"] == "VERIFIED"
    assert document_reviewed["confirmedBankScore"]["status"] == "CONFIRMED"


def test_balance_reconciliation_does_not_imply_financial_requirements_passed():
    assessment = calculate_bank_score_assessment(
        _daily_bank_document(),
        {"reconciliation": {"status": "RECONCILED"}},
        as_of=date(2026, 3, 31),
    )

    assert assessment["financialEvidenceStatus"] == "RECONCILED"
    assert assessment["financialAssessmentStatus"] == "INSUFFICIENT_DATA"


def test_different_account_holder_name_does_not_change_computed_score():
    document = _daily_bank_document()
    bank_analysis = _canonical_bank_analysis()
    document["extractedFields"]["accountHolderName"] = "SIM KEE SIANG"
    assessment = calculate_bank_score_assessment(
        document,
        bank_analysis,
        applicant={"fullName": "KHENG LORSAMNANG"},
        as_of=date(2026, 3, 31),
    )
    matching_document = {
        **document,
        "extractedFields": {
            **document["extractedFields"],
            "accountHolderName": "KHENG LORSAMNANG",
        },
    }
    matching_assessment = calculate_bank_score_assessment(
        matching_document,
        bank_analysis,
        applicant={"fullName": "KHENG LORSAMNANG"},
        as_of=date(2026, 3, 31),
    )

    assert document["extractedFields"]["accountHolderName"] == "SIM KEE SIANG"
    assert assessment["computedBankScore"]["points"] == matching_assessment[
        "computedBankScore"
    ]["points"]


def test_missing_ocr_confidence_remains_unavailable_without_affecting_score():
    assessment = calculate_bank_score_assessment(
        _daily_bank_document(),
        _canonical_bank_analysis(),
        as_of=date(2026, 3, 31),
    )

    assert assessment["ocrConfidence"] is None
    assert assessment["computedBankScore"]["available"] is True


def test_staff_verified_statement_confirms_computed_score():
    document = _daily_bank_document()
    document.update({"status": "VERIFIED", "reviewedBy": "staff-1"})
    assessment = calculate_bank_score_assessment(
        document,
        _canonical_bank_analysis(),
        as_of=date(2026, 3, 31),
    )

    assert assessment["staffReviewStatus"] == "VERIFIED"
    assert assessment["documentReviewStatus"] == "VERIFIED"
    assert assessment["confirmedBankScore"]["status"] == "CONFIRMED"
    assert assessment["confirmedBankScore"]["points"] == assessment[
        "computedBankScore"
    ]["points"]


def test_failed_balance_evidence_is_scored_separately_from_missing_evidence():
    document = _daily_bank_document(balance=0)
    document["bankStatementAnalysis"] = {
        "reconciliation": {"status": "MISMATCH"},
    }
    assessment = calculate_bank_score_assessment(
        document,
        {
            "financialRequirementStatus": "FAIL",
            "reconciliation": {"status": "MISMATCH"},
        },
        as_of=date(2026, 3, 31),
    )

    assert assessment["financialAssessmentStatus"] == "FAIL"
    assert assessment["balanceReconciliationStatus"] == "MISMATCH"
    assert assessment["monthlyBalanceVerification"] == "FAIL"
    readiness = calculate_readiness_score(
        [{"requirementId": "bank", "code": "bank_statement", "name": "Bank statement"}],
        [document],
        as_of=date(2026, 3, 31),
        bank_statement_analyses={"bank-doc": _canonical_bank_analysis()},
    )
    bank_component = readiness["components"]["bank_statement"]
    assert bank_component["points"] == 30.04
    assert bank_component["balanceReconciliationStatus"] == "MISMATCH"
    assert "features" not in bank_component
    assert "evidence" not in bank_component


def test_legacy_bank_metrics_do_not_change_the_four_category_contribution():
    requirement = {
        "requirementId": "bank",
        "code": "bank_statement",
        "name": "Bank statement",
    }
    original_document = _daily_bank_document()
    changed_document = deepcopy(original_document)
    changed_fields = changed_document["extractedFields"]
    changed_fields["transactionHistory"] = [
        {**transaction, "balance": 0}
        for transaction in changed_fields["transactionHistory"]
    ]
    bank_analyses = {"bank-doc": _canonical_bank_analysis()}

    original = calculate_readiness_score(
        [requirement],
        [original_document],
        as_of=date(2026, 3, 31),
        bank_statement_analyses=bank_analyses,
    )
    changed = calculate_readiness_score(
        [requirement],
        [changed_document],
        as_of=date(2026, 3, 31),
        bank_statement_analyses=bank_analyses,
    )

    assert original["components"]["bank_statement"]["points"] == 30.04
    assert changed["components"]["bank_statement"]["points"] == 30.04
    assert "features" not in original["components"]["bank_statement"]
    assert changed["score"] == original["score"]
