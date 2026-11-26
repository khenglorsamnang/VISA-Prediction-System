import pytest

from backend.app.api.bank_statements import (
    _analysis_record,
    _bank_statement_verification_blockers,
    _build_result,
)
from backend.app.services.bank_statement_scoring import (
    analyze_bank_statement,
    calculate_bank_statement_score,
)
from backend.app.services.business_rules import (
    DEFAULT_RULE_POLICY,
    configured_bank_balance_requirement,
    validate_rule_policy,
)


def _statement(
    balances=None,
    currency="USD",
    closing_balance=5000,
    applicant_name="Sample Applicant",
    transactions=None,
):
    if transactions is None:
        transactions = [
            {
                "date": "2026-02-15",
                "description": "Monthly salary",
                "amount": 2000,
                "type": "credit",
                "balance": 6000,
                "page": 1,
                "referenceNumber": "SAL-1",
            },
            {
                "date": "2026-03-10",
                "description": "Rent payment",
                "amount": 1000,
                "type": "debit",
                "balance": 5000,
                "page": 1,
                "referenceNumber": "RENT-1",
            },
        ]
    if balances is None:
        balances = (4000, 4200, closing_balance)
    return {
        "bankName": "Example Bank",
        "accountHolderName": applicant_name,
        "accountNumber": "123456789",
        "openingBalance": 4000,
        "closingBalance": closing_balance,
        "currency": currency,
        "statementStartDate": "2026-01-01",
        "statementEndDate": "2026-03-31",
        "deposits": 2000,
        "withdrawals": 1000,
        "averageBalance": 4200,
        "transactionHistory": transactions,
        "monthlyClosingBalances": [
            {
                "month": month,
                "closingBalance": balance,
                "currency": currency,
                "page": 1,
                "sourceText": f"{month} Closing balance: {balance}",
            }
            for month, balance in zip(
                ("2026-01", "2026-02", "2026-03"), balances
            )
        ],
    }


def test_three_month_threshold_is_inclusive_at_four_thousand_usd():
    result = analyze_bank_statement(_statement())

    assert [item["status"] for item in result["monthlyBalanceChecks"]] == [
        "AVAILABLE",
        "AVAILABLE",
        "AVAILABLE",
    ]
    assert [
        item["minimumBalanceStatus"]
        for item in result["monthlyBalanceChecks"]
    ] == ["MEETS_MINIMUM", "MEETS_MINIMUM", "MEETS_MINIMUM"]
    assert result["financialRequirementStatus"] == "PASS"
    assert result["minimumMonthlyClosingBalanceUsd"] == 4000
    assert result["bankStatementScore"]["maximum"] == 45
    assert set(result["bankStatementScore"]["components"]) == {
        "ending_balance",
        "starting_balance",
        "transaction_history",
        "transaction_period",
    }
    assert result["bankStatementScore"]["available"] is True
    assert result["bankStatementScore"]["percentage"] is not None
    assert 0 <= result["bankStatementScore"]["evidenceCoveragePercentage"] <= 100
    assert result["bankStatementScore"]["assessment"] == "COMPUTED"
    assert result["authenticityStatus"] == "AUTHENTICITY_NOT_CONFIRMED"


def test_scoring_threshold_is_uniform_across_applicant_contexts():
    fields = _statement(balances=(4750, 4750, 4750), closing_balance=4750)
    applicant_contexts = (
        {"destination_country": "Australia", "nationality": "Cambodian"},
        {"destination_country": "Australia", "nationality": "French"},
    )

    results = [
        _build_result(
            {"fields": fields, "status": "completed"},
            applicant_context=context,
        )["bank_statement_analysis"]
        for context in applicant_contexts
    ]

    assert [
        result["minimumMonthlyClosingBalanceUsd"] for result in results
    ] == [4000, 4000]
    assert [
        [item["status"] for item in result["monthlyBalanceChecks"]]
        for result in results
    ] ==     [["AVAILABLE", "AVAILABLE", "AVAILABLE"], ["AVAILABLE", "AVAILABLE", "AVAILABLE"]]
    assert results[0]["bankStatementScore"]["points"] == results[1][
        "bankStatementScore"
    ]["points"]


def test_intermediate_month_balance_is_reconciliation_evidence_not_a_score_threshold():
    result = analyze_bank_statement(
        _statement(balances=(4000, 3999.99, 9000), closing_balance=9000)
    )
    comparison = analyze_bank_statement(
        _statement(balances=(4000, 4200, 9000), closing_balance=9000)
    )

    assert [item["status"] for item in result["monthlyBalanceChecks"]] == [
        "AVAILABLE",
        "AVAILABLE",
        "AVAILABLE",
    ]
    assert result["monthlyBalanceChecks"][1]["minimumBalanceStatus"] == "BELOW_MINIMUM"
    assert result["monthlyBalanceChecks"][1]["shortfall"] == 0.01
    assert result["financialRequirementStatus"] == "PASS"
    assert result["bankStatementScore"]["points"] == comparison[
        "bankStatementScore"
    ]["points"]
    monthly_flags = [
        flag
        for flag in result["bankStatementScore"]["triggeredFlags"]
        if flag["flagType"] == "MONTHLY_CLOSING_BALANCE_BELOW_MINIMUM"
    ]
    assert len(monthly_flags) == 1
    assert monthly_flags[0]["deductionPoints"] == 0
    assert monthly_flags[0]["deductionStatus"] == "INFORMATIONAL_ONLY"
    assert not any(
        flag["code"] == "PERIOD_END_BALANCE_BELOW_REQUIRED"
        for flag in result["riskFlags"]
    )


def test_period_end_assessment_and_monthly_checks_use_the_balance_threshold():
    result = analyze_bank_statement(
        _statement(balances=(9000, 8000, 3999.99), closing_balance=3999.99)
    )

    assert result["financialRequirementStatus"] == "BELOW_MINIMUM"
    assert result["minimumBalanceAssessment"] == {
        "status": "BELOW_MINIMUM",
        "balance": 3999.99,
        "currency": "USD",
        "minimum": 4000,
        "shortfall": 0.01,
    }
    assert [
        item["minimumBalanceStatus"]
        for item in result["monthlyBalanceChecks"]
    ] == ["MEETS_MINIMUM", "MEETS_MINIMUM", "BELOW_MINIMUM"]
    assert any(
        flag["code"] == "PERIOD_END_BALANCE_BELOW_REQUIRED"
        for flag in result["riskFlags"]
    )


def test_configured_minimum_reports_the_actual_period_end_shortfall_as_a_warning():
    result = analyze_bank_statement(
        _statement(
            balances=(17188.07, 10626.07, 3084.87),
            closing_balance=3084.87,
        )
    )

    assert result["financialRequirementStatus"] == "BELOW_MINIMUM"
    assert result["minimumBalanceAssessment"] == {
        "status": "BELOW_MINIMUM",
        "balance": 3084.87,
        "currency": "USD",
        "minimum": 4000,
        "shortfall": 915.13,
    }
    assert result["bankStatementScore"]["available"] is True
    assert result["bankStatementScore"]["points"] is not None
    warning = next(
        flag
        for flag in result["riskFlags"]
        if flag["code"] == "PERIOD_END_BALANCE_BELOW_REQUIRED"
    )
    assert warning["severity"] == "low"
    assert warning["manualReviewRequired"] is True
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_below_minimum_warning_does_not_override_reconciliation_blockers():
    fields = _statement(
        closing_balance=3084.87,
        balances=(17188.07, 10626.07, 3084.87),
        transactions=[
            {
                "date": "2026-02-15",
                "description": "Small deposit",
                "amount": 100,
                "type": "credit",
                "balance": 5000,
            },
            {
                "date": "2026-03-10",
                "description": "Small payment",
                "amount": 100,
                "type": "debit",
                "balance": 3084.87,
            },
        ],
    )
    bank_analysis = analyze_bank_statement(fields)
    review_analysis = {"reconciliationStatus": "RECONCILED"}
    document = {"extractedFields": fields, "transactionSourceReviews": {}}

    assert bank_analysis["financialRequirementStatus"] == "BELOW_MINIMUM"
    assert _bank_statement_verification_blockers(
        document, review_analysis, bank_analysis
    ) == []

    review_analysis["reconciliationStatus"] = "MISMATCHED"
    blockers = _bank_statement_verification_blockers(
        document, review_analysis, bank_analysis
    )
    assert "Financial reconciliation must be RECONCILED." in blockers


def test_missing_intermediate_month_close_does_not_replace_period_boundaries():
    fields = _statement()
    fields["monthlyClosingBalances"].pop(1)

    result = analyze_bank_statement(fields)

    assert result["monthlyBalanceChecks"][1]["status"] == "INSUFFICIENT_DATA"
    assert (
        result["monthlyBalanceChecks"][1]["minimumBalanceStatus"]
        == "INSUFFICIENT_DATA"
    )
    assert result["monthlyBalanceChecks"][1]["shortfall"] is None
    assert result["financialRequirementStatus"] == "PASS"
    assert result["bankStatementScore"]["components"]["ending_balance"]["points"] is not None
    assert result["bankStatementScore"]["components"]["starting_balance"]["points"] is not None
    assert result["bankStatementScore"]["available"] is True


def test_missing_month_and_non_usd_balances_are_not_assumed_to_pass():
    missing_month = _statement()
    missing_month["monthlyClosingBalances"].pop()
    missing_month["closingBalance"] = None
    incomplete = analyze_bank_statement(missing_month)
    non_usd = analyze_bank_statement(_statement(currency="KHR"))

    assert incomplete["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert incomplete["bankStatementScore"]["available"] is False
    assert incomplete["bankStatementScore"]["points"] is None
    assert incomplete["bankStatementScore"]["percentage"] is None
    assert (
        incomplete["bankStatementScore"]["components"]["ending_balance"]["points"]
        is None
    )
    assert incomplete["monthlyBalanceChecks"][-1]["status"] == "INSUFFICIENT_DATA"
    assert (
        incomplete["monthlyBalanceChecks"][-1]["minimumBalanceStatus"]
        == "INSUFFICIENT_DATA"
    )
    assert incomplete["monthlyBalanceChecks"][-1]["shortfall"] is None
    assert non_usd["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert all(item["status"] == "AVAILABLE" for item in non_usd["monthlyBalanceChecks"])
    assert all(
        item["minimumBalanceStatus"] == "NOT_ASSESSED"
        for item in non_usd["monthlyBalanceChecks"]
    )
    assert any(flag["code"] == "CURRENCY_NOT_USD" for flag in non_usd["riskFlags"])


def test_bank_score_is_unavailable_without_any_scoring_evidence():
    result = analyze_bank_statement({})

    assert result["bankStatementScore"]["available"] is False
    assert result["bankStatementScore"]["points"] is None
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_incomplete_ocr_never_returns_a_final_bank_statement_score():
    result = analyze_bank_statement(_statement(), ocr_complete=False)

    assert result["bankStatementScore"]["available"] is False
    assert result["bankStatementScore"]["points"] is None
    assert result["bankStatementScore"]["percentage"] is None
    assert result["bankStatementScore"]["assessment"] == "INSUFFICIENT_DATA"
    assert all(
        component["points"] is None
        for component in result["bankStatementScore"]["components"].values()
    )
    assert result["financialAnalysis"]["financial_score"] is None
    assert result["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_partial_bank_statement_result_exposes_no_final_verification_scores():
    result = _build_result(
        {
            "status": "partial",
            "failed_pages": [{"page": 2, "error": "OCR timed out"}],
            "confidence": 0.9,
            "fields": _statement(),
            "pages": [
                {"page": 1, "text": "Transactions from page one"},
                {"page": 2, "text": ""},
            ],
            "metrics": {"status": "OCR_PARTIAL", "page_count": 2},
        }
    )
    record = _analysis_record(
        "partial-statement",
        "statement.pdf",
        {
            "applicant_name": "Sample Applicant",
            "visa_type": "Tourist",
            "requirements": {},
            "applicant_context": {},
            "storage_path": "private/statement.pdf",
            "result": result,
        },
    )

    assert result["extracted"]["pages"][1]["page"] == 2
    assert result["score"]["document_verification_score"] is None
    assert result["financial_analysis"]["financial_score"] is None
    assert (
        result["bank_statement_analysis"]["bankStatementScore"]["points"]
        is None
    )
    assert result["human_review_required"] is True
    assert record["extraction_status"] == "partial"
    assert record["financial_score"] is None
    assert record["document_verification_score"] is None


def test_missing_ocr_confidence_stays_unavailable_in_bank_score():
    result = _build_result(
        {
            "fields": _statement(),
            "pages": [{"page": 1, "text": "Statement transactions"}],
            "metrics": {"page_count": 1, "pages_processed": 1},
        }
    )

    assert result["score"]["ocr_confidence"] is None
    assert result["bank_statement_analysis"]["ocrConfidence"] is None
    assert not any("confidence is low" in warning for warning in result["warnings"])


def test_duplicate_pages_and_transactions_are_manual_review_candidates():
    duplicate_transaction = {
        "date": "2026-02-15",
        "description": "Monthly salary",
        "amount": 2000,
        "type": "credit",
    }
    fields = _statement(
        transactions=[
            duplicate_transaction,
            duplicate_transaction.copy(),
        ]
    )
    raw_text = (
        "--- PAGE 1\n"
        + ("Example Bank statement transaction details " * 5)
        + "\n--- PAGE 2\n"
        + ("Example Bank statement transaction details " * 5)
    )

    result = analyze_bank_statement(fields, raw_text=raw_text)
    flag_codes = {item["code"] for item in result["riskFlags"]}

    assert result["duplicatePages"] == [{"page": 2, "duplicate_of_page": 1}]
    assert len(result["duplicateTransactions"]) == 1
    assert "DUPLICATE_PAGE_CANDIDATE" in flag_codes
    assert "DUPLICATE_TRANSACTION_CANDIDATE" in flag_codes
    history = result["bankStatementScore"]["components"]["transaction_history"]
    assert history["points"] == history["maximum"]
    assert history["evidence"]["duplicateTransactionCount"] == 1
    assert history["evidence"]["reconciliationStatus"] == "MISMATCHED"
    assert result["authenticityVerified"] is False


def test_large_incoming_deposit_followed_by_transfer_is_a_review_flag():
    result = analyze_bank_statement(
        _statement(
            transactions=[
                {
                    "date": "2026-03-10",
                    "description": "Salary",
                    "amount": 5000,
                    "type": "credit",
                },
                {
                    "date": "2026-03-12",
                    "description": "Wire transfer out",
                    "amount": 4500,
                    "type": "debit",
                },
            ]
        )
    )

    flag = next(
        item
        for item in result["riskFlags"]
        if item["code"] == "RAPID_FUNDS_OUTFLOW"
    )
    assert flag["relatedDepositDate"] == "2026-03-10"
    assert "verify context" in flag["reason"]


def test_account_holder_name_is_audited_but_does_not_change_financial_scoring():
    applicant_name = "KHENG LORSAMNANG"
    matching = analyze_bank_statement(
        _statement(
            closing_balance=4500,
            applicant_name=applicant_name,
        ),
        applicant_name=applicant_name,
    )
    different = analyze_bank_statement(
        _statement(
            closing_balance=4500,
            applicant_name="SIM KEE SIANG",
        ),
        applicant_name=applicant_name,
    )
    flag_codes = {item["code"] for item in different["riskFlags"]}

    assert different["reconciliation"]["status"] == "MISMATCHED"
    assert "RECONCILIATION_MISMATCH" in flag_codes
    assert "ACCOUNT_HOLDER_NAME_MISMATCH" not in flag_codes
    assert different["accountHolderCheck"]["status"] == "NOT_ASSESSED"
    assert different["accountHolderCheck"]["accountHolderName"] == "SIM KEE SIANG"
    assert different["accountHolderCheck"]["originalExtractedName"] == "SIM KEE SIANG"
    assert different["bankStatementScore"]["points"] == matching["bankStatementScore"]["points"]
    assert "Legal ownership" in different["accountHolderCheck"]["reason"]


def test_transaction_report_preserves_source_ocr_line():
    fields = _statement(
        transactions=[
            {
                "date": "2026-02-15",
                "description": "Monthly salary",
                "amount": "2000.00",
                "type": "credit",
                "page": "4",
                "source_text": "2026-02-15 Monthly salary +2,000.00 6,800.50",
            }
        ]
    )

    result = analyze_bank_statement(fields)

    assert result["transactions"][0]["sourceText"] == (
        "2026-02-15 Monthly salary +2,000.00 6,800.50"
    )


def test_bank_score_weights_are_fixed_to_the_four_required_categories():
    policy = validate_rule_policy(DEFAULT_RULE_POLICY)
    required_weights = {
        "ending_balance": 22.5,
        "starting_balance": 6.75,
        "transaction_history": 9.0,
        "transaction_period": 6.75,
    }
    assert policy["bank_statement_scoring"]["weights"] == required_weights
    assert sum(required_weights.values()) == 45
    legacy_policy = {
        **DEFAULT_RULE_POLICY,
        "bank_statement_scoring": {
            **DEFAULT_RULE_POLICY["bank_statement_scoring"],
            "weights": {
                "ending_balance": 15.75,
                "starting_balance": 15.75,
                "transaction_history": 6.75,
                "transaction_period": 6.75,
            },
        },
    }
    assert validate_rule_policy(legacy_policy)["bank_statement_scoring"][
        "weights"
    ] == required_weights

    redistributed_weights = {
        **required_weights,
        "ending_balance": 21.5,
        "starting_balance": 7.75,
    }
    changed = {
        **DEFAULT_RULE_POLICY,
        "bank_statement_scoring": {
            **DEFAULT_RULE_POLICY["bank_statement_scoring"],
            "weights": redistributed_weights,
        },
    }

    with pytest.raises(ValueError, match="must be Ending Balance 22.50"):
        validate_rule_policy(changed)
    with pytest.raises(ValueError, match="must be Ending Balance 22.50"):
        analyze_bank_statement(_statement(), config={"weights": redistributed_weights})


def test_four_bank_score_categories_use_evidence_and_leave_account_age_unknown():
    fields = _statement(
        balances=(5000, 5000, 5000),
        transactions=[
            {
                "date": "2026-01-01",
                "description": "Monthly salary",
                "amount": 999.99,
                "type": "credit",
                "balance": 5000,
                "sender": "Acme Payroll",
                "source_text": "Payroll deposit",
            }
        ],
    )
    fields["deposits"] = 999.99
    fields["withdrawals"] = 0
    result = analyze_bank_statement(fields)
    score = result["bankStatementScore"]

    assert score["available"] is True
    assert score["maximum"] == 45
    assert {
        key: item["maximum"] for key, item in score["components"].items()
    } == {
        "ending_balance": 22.5,
        "starting_balance": 6.75,
        "transaction_history": 9.0,
        "transaction_period": 6.75,
    }
    assert sum(item["maximum"] for item in score["components"].values()) == 45
    assert score["points"] == 45
    assert score["confirmedPoints"] is None
    assert score["confirmedStatus"] == "NOT_YET_CONFIRMED"
    assert score["components"]["ending_balance"]["points"] == 22.5
    assert score["components"]["starting_balance"]["points"] == 6.75
    assert score["components"]["starting_balance"]["evidence"]["openingBalance"] == 4000
    assert score["components"]["transaction_history"]["points"] == 9.0
    assert score["components"]["transaction_history"]["evidence"]["moneyIn"] == 999.99
    assert score["components"]["transaction_history"]["evidence"]["moneyOut"] == 0
    assert score["components"]["transaction_period"]["points"] == 6.75
    assert score["components"]["transaction_period"]["evidence"][
        "monthsMissingTransactionActivity"
    ] == ["2026-02", "2026-03"]
    assert score["points"] == round(
        sum(item["points"] for item in score["components"].values()), 2
    )
    assert score["components"]["starting_balance"]["evidence"]["accountAgeStatus"] == "INSUFFICIENT_DATA"


def test_shared_bank_score_calculation_clamps_categories_and_total():
    components = {
        "ending_balance": {"points": 20, "maximum": 22.5, "available": True},
        "starting_balance": {"points": -1, "maximum": 6.75, "available": True},
        "transaction_history": {"points": 8, "maximum": 9, "available": True},
        "transaction_period": {"points": 6.75, "maximum": 6.75, "available": True},
    }

    score = calculate_bank_statement_score(components)

    assert [score["components"][key]["points"] for key in components] == [
        20,
        0,
        8,
        6.75,
    ]
    assert score["points"] == 34.75
    assert score["points"] == round(
        sum(item["points"] for item in score["components"].values()), 2
    )
    assert score["maximum"] == 45

    maximum_score = calculate_bank_statement_score(
        {
            key: {**item, "points": 100}
            for key, item in components.items()
        }
    )
    assert maximum_score["points"] == 45


def test_ending_balance_uses_latest_verified_monthly_close_not_transaction_balance():
    fields = _statement(
        balances=(4000, 4200, 9000),
        closing_balance=9000,
    )
    fields["transactionHistory"][-1]["balance"] = 2000
    result = analyze_bank_statement(fields)
    ending = result["bankStatementScore"]["components"]["ending_balance"]

    assert ending["evidence"]["latestMonth"] == "2026-03"
    assert ending["evidence"]["latestVerifiedClosingBalance"] == 9000
    assert ending["evidence"]["closingBalanceSource"] == "extracted_monthly_closing_balance"
    assert ending["evidence"]["closingBalanceSourcePage"] == 1
    assert [
        item["closingBalance"] for item in result["monthlyBalanceChecks"]
    ] == [4000, 4200, 9000]


def test_ending_balance_uses_extracted_monthly_history_when_statement_close_is_missing():
    fields = _statement(balances=(4000, 4200, 9000), closing_balance=9000)
    fields["monthlyBalanceHistory"] = fields.pop("monthlyClosingBalances")
    fields["closingBalance"] = None

    result = analyze_bank_statement(fields)
    ending = result["bankStatementScore"]["components"]["ending_balance"]

    assert ending["points"] is not None
    assert ending["evidence"]["latestVerifiedClosingBalance"] == 9000
    assert ending["evidence"]["closingBalanceSource"] == (
        "extracted_monthly_balance_history"
    )


def test_conflicting_latest_month_closes_are_unavailable_and_flagged_for_review():
    result = analyze_bank_statement(
        _statement(
            balances=(4000, 4200, 4500),
            closing_balance=9000,
        )
    )
    latest_check = result["monthlyBalanceChecks"][-1]
    ending = result["bankStatementScore"]["components"]["ending_balance"]

    assert latest_check["ambiguous"] is True
    assert latest_check["closingBalance"] is None
    assert latest_check["status"] == "CONFLICTING_DATA"
    assert ending["points"] is None
    assert any(
        flag["code"] == "CONFLICTING_MONTHLY_BALANCES"
        for flag in result["riskFlags"]
    )


def test_intermediate_monthly_closes_do_not_change_period_boundary_scores():
    first = analyze_bank_statement(
        _statement(balances=(2000, 9000, 6000), closing_balance=6000)
    )["bankStatementScore"]
    second = analyze_bank_statement(
        _statement(balances=(9000, 2000, 6000), closing_balance=6000)
    )["bankStatementScore"]

    assert first["components"]["ending_balance"]["points"] == second[
        "components"
    ]["ending_balance"]["points"]
    assert first["components"]["starting_balance"]["points"] == second[
        "components"
    ]["starting_balance"]["points"]
    assert first["points"] == second["points"]


def test_period_score_uses_boundary_balances_and_monthly_closes_as_supporting_evidence():
    fields = _statement(
        balances=(4000, 4200, 5000),
        closing_balance=5000,
        transactions=[
            {
                "date": "2026-01-31",
                "description": "January withdrawal",
                "amount": 100,
                "type": "debit",
                "balance": 4000,
            },
            {
                "date": "2026-01-31",
                "description": "January deposit",
                "amount": 100,
                "type": "credit",
                "balance": 4100,
            },
            {
                "date": "2026-02-28",
                "description": "February deposit",
                "amount": 300,
                "type": "credit",
                "balance": 4300,
            },
            {
                "date": "2026-03-31",
                "description": "March withdrawal",
                "amount": 200,
                "type": "debit",
                "balance": 5000,
            },
        ],
    )
    fields["monthlyClosingBalances"][0]["closingBalance"] = 4100
    fields["monthlyClosingBalances"] = [
        fields["monthlyClosingBalances"][0],
        fields["monthlyClosingBalances"][2],
    ]

    result = analyze_bank_statement(fields)
    score = result["bankStatementScore"]

    assert [item["closingBalance"] for item in result["transactionSummary"]["monthly"]] == [
        4100,
        4300,
        5000,
    ]
    assert result["monthlyBalanceChecks"][1]["closingBalance"] == 4300
    assert score["maximum"] == 45
    assert set(score["components"]) == {
        "ending_balance",
        "starting_balance",
        "transaction_history",
        "transaction_period",
    }
    assert score["components"]["ending_balance"]["evidence"][
        "latestVerifiedClosingBalance"
    ] == 5000
    assert score["components"]["starting_balance"]["evidence"]["openingBalance"] == 4000
    assert score["points"] == round(
        sum(item["points"] for item in score["components"].values()), 2
    )
    assert all(item["available"] for item in score["components"].values())


def test_extracted_monthly_closes_retain_values_and_source_references_for_complete_period():
    fields = _statement(
        closing_balance=3084.87,
        transactions=[
            {
                "date": "2026-07-15",
                "description": "July deposit",
                "amount": 200,
                "type": "credit",
                "balance": 5200,
            },
            {
                "date": "2026-08-15",
                "description": "August payment",
                "amount": 100,
                "type": "debit",
                "balance": 10626.07,
            },
            {
                "date": "2026-09-15",
                "description": "September deposit",
                "amount": 200,
                "type": "credit",
                "balance": 3084.87,
            },
        ],
    )
    fields["openingBalance"] = 5000
    fields["statementStartDate"] = "2026-07-01"
    fields["statementEndDate"] = "2026-09-30"
    fields["monthlyClosingBalances"] = [
        {
            "month": "2026-07",
            "closing_balance": 17188.07,
            "currency_code": "USD",
            "source_page": 13,
            "date": "2026-07-31",
            "row_number": 109,
        },
        {
            "month": "2026-08",
            "closing_balance": 10626.07,
            "currency_code": "USD",
            "source_page": 26,
            "date": "2026-08-31",
            "row_number": 237,
        },
        {
            "month": "2026-09",
            "closing_balance": 3084.87,
            "currency_code": "USD",
            "source_page": 37,
            "date": "2026-09-30",
            "row_number": 336,
        },
    ]

    result = analyze_bank_statement(fields)

    monthly = result["transactionSummary"]["monthly"]
    assert [item["closingBalance"] for item in monthly] == [
        17188.07,
        10626.07,
        3084.87,
    ]
    assert [
        (item["sourcePage"], item["sourceTransaction"]) for item in monthly
    ] == [(13, 109), (26, 237), (37, 336)]
    assert result["bankStatementScore"]["available"] is True
    assert result["financialRequirementStatus"] == "BELOW_MINIMUM"
    assert result["minimumBalanceAssessment"]["shortfall"] == 915.13
    assert [
        (item["minimumBalanceStatus"], item["shortfall"])
        for item in result["monthlyBalanceChecks"]
    ] == [
        ("MEETS_MINIMUM", 0.0),
        ("MEETS_MINIMUM", 0.0),
        ("BELOW_MINIMUM", 915.13),
    ]
    assert [
        item["minimumBalanceStatus"]
        for item in result["transactionSummary"]["monthly"]
    ] == [
        "MEETS_MINIMUM",
        "MEETS_MINIMUM",
        "BELOW_MINIMUM",
    ]


def test_complete_three_calendar_month_period_receives_full_transaction_period_points():
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            {
                "date": "2025-02-01",
                "description": "Period start deposit",
                "amount": 100,
                "type": "credit",
                "balance": 5100,
            },
            {
                "date": "2025-04-30",
                "description": "Period end withdrawal",
                "amount": 100,
                "type": "debit",
                "balance": 5000,
            },
        ],
    )
    fields["statementStartDate"] = "2025-02-01"
    fields["statementEndDate"] = "2025-04-30"
    fields["monthlyClosingBalances"] = [
        {
            "month": month,
            "closingBalance": 5000,
            "currency": "USD",
        }
        for month in ("2025-02", "2025-03", "2025-04")
    ]

    result = analyze_bank_statement(fields)

    assert result["statementPeriod"]["completeThreeMonths"] is True
    assert result["bankStatementScore"]["components"]["transaction_period"]["points"] == 6.75


def test_period_opening_balance_conflicts_are_unavailable_and_flagged():
    fields = _statement()
    fields["monthlyBalanceHistory"] = [
        {
            "month": month,
            "openingBalance": opening,
            "closingBalance": closing,
            "currency": "USD",
        }
        for month, opening, closing in (
            ("2026-01", 4500, 4000),
            ("2026-02", 4000, 4200),
            ("2026-03", 4200, 5000),
        )
    ]

    result = analyze_bank_statement(fields)

    assert result["periodOpeningBalanceAmbiguous"] is True
    assert result["bankStatementScore"]["components"]["starting_balance"][
        "points"
    ] is None
    assert result["bankStatementScore"]["points"] is None
    assert any(
        flag["code"] == "CONFLICTING_PERIOD_OPENING_BALANCES"
        for flag in result["riskFlags"]
    )


def test_longer_statement_scores_only_latest_three_months_and_uses_that_opening():
    fields = _statement()
    fields["statementStartDate"] = "2025-10-01"
    fields["openingBalance"] = 3000
    fields["monthlyBalanceHistory"] = [
        {
            "month": "2026-01",
            "openingBalance": 4800,
            "closingBalance": 4000,
            "currency": "USD",
        }
    ]
    fields["transactionHistory"].append(
        {
            "date": "2025-12-15",
            "description": "Older deposit",
            "amount": 5000,
            "type": "credit",
            "balance": 9000,
        }
    )

    result = analyze_bank_statement(
        fields,
        transaction_source_reviews={
            "2": {
                "status": "UNVERIFIED",
                "penaltyPoints": 0.5,
            }
        },
    )
    score = result["bankStatementScore"]

    assert result["statementPeriod"]["scoringStartDate"] == "2026-01-01"
    assert score["components"]["starting_balance"]["evidence"]["openingBalance"] == 4800
    assert score["components"]["transaction_history"]["evidence"][
        "transactionCount"
    ] == 2
    assert score["components"]["transaction_history"]["evidence"]["moneyIn"] == 2000
    assert score["totalPenaltyPoints"] == 0
    assert all(
        flag.get("transactionIndex") != 2 for flag in score["triggeredFlags"]
    )
    assert all(
        item["transactionIndex"] != 2 for item in score["transactionReviews"]
    )
    assert score["components"]["transaction_period"]["evidence"][
        "monthsMissingTransactionActivity"
    ] == ["2026-01"]


def test_latest_three_month_start_uses_previous_month_close_when_opening_is_not_extracted():
    fields = _statement()
    fields["statementStartDate"] = "2025-10-01"
    fields["openingBalance"] = 3000
    fields["monthlyBalanceHistory"] = [
        {
            "month": "2025-12",
            "closingBalance": 4800,
            "currency": "USD",
            "page": 12,
        }
    ]

    result = analyze_bank_statement(fields)

    starting = result["bankStatementScore"]["components"]["starting_balance"]
    assert starting["available"] is True
    assert starting["evidence"]["openingBalance"] == 4800
    assert starting["evidence"]["openingBalanceEvidence"][0]["source"] == (
        "previous_month_closing_balance"
    )


def test_transaction_source_confirmation_removes_only_the_source_deduction():
    fields = _statement(
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Wire transfer",
                "amount": 2000,
                "type": "credit",
                "balance": 6000,
                "page": 2,
                "referenceNumber": "WIRE-1",
                "source_text": "2026-03-20 incoming wire 2,000.00",
            },
            {
                "date": "2026-03-25",
                "description": "Rent payment",
                "amount": 999.99,
                "type": "debit",
                "balance": 5000,
                "page": 2,
                "referenceNumber": "RENT-2",
            },
        ]
    )
    baseline = analyze_bank_statement(fields)["bankStatementScore"]
    confirmed = analyze_bank_statement(
        fields,
        transaction_source_reviews={
            "0": {
                "status": "SOURCE_CONFIRMED",
                "reviewerId": "staff-1",
                "reviewedAt": "2026-04-01T10:00:00Z",
                "evidenceReferences": ["payroll-2026-03"],
            }
        },
    )["bankStatementScore"]
    pending = analyze_bank_statement(
        fields,
        transaction_source_reviews={"0": {"status": "PENDING_REVIEW"}},
    )["bankStatementScore"]

    assert confirmed["points"] == baseline["points"]
    assert confirmed["confirmedPoints"] is None
    assert confirmed["confirmedStatus"] == "NOT_YET_CONFIRMED"
    assert confirmed["transactionReviews"][0]["reviewStatus"] == "SOURCE_CONFIRMED"
    assert pending["points"] == baseline["points"]
    assert pending["confirmedPoints"] is None
    assert pending["pendingTransactionReviews"] == 1
    assert confirmed["totalPenaltyPoints"] == 0
    assert pending["totalPenaltyPoints"] == 0
    assert not any(
        flag["flagType"] == "SOURCE_OF_FUNDS_UNCONFIRMED"
        for flag in confirmed["triggeredFlags"]
    )
    assert not any(
        flag["flagType"] == "SOURCE_OF_FUNDS_UNCONFIRMED"
        for flag in pending["triggeredFlags"]
    )
    assert pending["transactionReviews"][0]["status"] == "PENDING_REVIEW"
    assert pending["transactionReviews"][0]["penaltyPoints"] == 0
    assert pending["transactionReviews"][0]["sourceDeduction"] == {
        "points": 0.0,
        "status": "PENDING_REVIEW",
    }


def test_pending_source_never_deducts_even_when_legacy_policy_enables_it():
    fields = _statement(
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Payroll transfer",
                "amount": 2000,
                "type": "credit",
                "balance": 6000,
                "sender": "Named Payroll Provider",
                "referenceNumber": "PAY-3",
            }
        ]
    )
    pending_review = {"0": {"status": "PENDING_REVIEW"}}

    default_policy_score = analyze_bank_statement(
        fields,
        transaction_source_reviews=pending_review,
    )["bankStatementScore"]
    explicit_policy_score = analyze_bank_statement(
        fields,
        config={"deduct_pending_source_review": True},
        transaction_source_reviews=pending_review,
    )["bankStatementScore"]

    assert default_policy_score["transactionReviews"][0]["status"] == "PENDING_REVIEW"
    assert default_policy_score["transactionReviews"][0]["sender"] == "Named Payroll Provider"
    assert default_policy_score["totalPenaltyPoints"] == 0
    assert explicit_policy_score["totalPenaltyPoints"] == 0
    assert explicit_policy_score["transactionReviews"][0]["sourceDeduction"] == {
        "points": 0.0,
        "status": "PENDING_REVIEW",
    }


def test_money_in_and_money_out_flags_include_direction_reference_without_deduction():
    fields = _statement(
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Large deposit",
                "amount": 2000,
                "type": "credit",
                "balance": 6000,
                "referenceNumber": "IN-1",
            },
            {
                "date": "2026-03-25",
                "description": "Large withdrawal",
                "amount": 1000,
                "type": "debit",
                "balance": 5000,
                "referenceNumber": "OUT-1",
            },
        ]
    )
    fields["deposits"] = 2000
    fields["withdrawals"] = 1000
    score = analyze_bank_statement(fields, document_id="flag-statement")[
        "bankStatementScore"
    ]

    flags_by_type = {flag["flagType"]: flag for flag in score["triggeredFlags"]}
    assert flags_by_type["MONEY_IN_THRESHOLD"]["direction"] == "credit"
    assert flags_by_type["MONEY_IN_THRESHOLD"]["transactionReference"] == "IN-1"
    assert flags_by_type["MONEY_IN_THRESHOLD"]["thresholdUsd"] == 2000
    assert flags_by_type["MONEY_OUT_THRESHOLD"]["direction"] == "debit"
    assert flags_by_type["MONEY_OUT_THRESHOLD"]["transactionReference"] == "OUT-1"
    assert flags_by_type["MONEY_OUT_THRESHOLD"]["thresholdUsd"] == 1000

    reviews_by_reference = {
        review["reference"]: review for review in score["transactionReviews"]
    }
    for reference, flag_type in (
        ("IN-1", "MONEY_IN_THRESHOLD"),
        ("OUT-1", "MONEY_OUT_THRESHOLD"),
    ):
        review = reviews_by_reference[reference]
        assert review["status"] == "PENDING_REVIEW"
        assert [flag["flagType"] for flag in review["transactionFlags"]] == [flag_type]
        assert review["transactionFlags"][0]["deductionPoints"] == 0
        assert review["transactionFlags"][0]["deductionStatus"] == "INFORMATIONAL_ONLY"
        assert review["appliedDeductions"] == []


def test_money_out_example_deduction_depends_only_on_source_review_status():
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Outgoing transfer",
                "amount": 1503.75,
                "type": "debit",
                "balance": 5000,
                "referenceNumber": "OUT-150375",
            }
        ],
    )
    fields["deposits"] = 0
    fields["withdrawals"] = 1503.75

    pending = analyze_bank_statement(fields)["bankStatementScore"]
    confirmed = analyze_bank_statement(
        fields,
        transaction_source_reviews={"0": {"status": "CONFIRMED"}},
    )["bankStatementScore"]
    unverified = analyze_bank_statement(
        fields,
        transaction_source_reviews={"0": {"status": "UNVERIFIED"}},
    )["bankStatementScore"]

    money_out_flag = next(
        flag for flag in pending["triggeredFlags"]
        if flag["flagType"] == "MONEY_OUT_THRESHOLD"
    )
    assert money_out_flag["amountUsd"] == 1503.75
    assert money_out_flag["transactionReference"] == "OUT-150375"
    assert money_out_flag["deductionPoints"] == 0
    assert pending["totalPenaltyPoints"] == 0
    assert confirmed["totalPenaltyPoints"] == 0
    assert confirmed["points"] == pending["points"]
    assert unverified["totalPenaltyPoints"] == 0.5
    assert unverified["points"] == round(pending["points"] - 0.5, 2)


def test_threshold_boundaries_and_all_low_transaction_balances_are_informational():
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Below money-in threshold",
                "amount": 1999.99,
                "type": "credit",
                "balance": 5000,
                "referenceNumber": "IN-BELOW",
            },
            {
                "date": "2026-03-21",
                "description": "At money-in threshold",
                "amount": 2000,
                "type": "credit",
                "balance": 3999.99,
                "referenceNumber": "IN-AT",
            },
            {
                "date": "2026-03-22",
                "description": "Below money-out threshold",
                "amount": 999.99,
                "type": "debit",
                "balance": 4000,
                "referenceNumber": "OUT-BELOW",
            },
            {
                "date": "2026-03-23",
                "description": "At money-out threshold",
                "amount": 1000,
                "type": "debit",
                "balance": 2500,
                "referenceNumber": "OUT-AT",
            },
            {
                "date": "2026-03-24",
                "description": "Unavailable balance",
                "amount": 50,
                "type": "debit",
                "balance": None,
                "referenceNumber": "NO-BALANCE",
            },
        ],
    )
    fields["deposits"] = 3999.99
    fields["withdrawals"] = 2049.99
    score = analyze_bank_statement(fields)["bankStatementScore"]

    flags_by_type = {}
    for flag in score["triggeredFlags"]:
        flags_by_type.setdefault(flag["flagType"], []).append(flag)
    assert [flag["transactionReference"] for flag in flags_by_type["MONEY_IN_THRESHOLD"]] == ["IN-AT"]
    assert [flag["transactionReference"] for flag in flags_by_type["MONEY_OUT_THRESHOLD"]] == ["OUT-AT"]
    low_balances = flags_by_type["TRANSACTION_BALANCE_BELOW_MINIMUM"]
    assert {flag["transactionReference"] for flag in low_balances} == {"IN-AT", "OUT-AT"}
    assert all(flag["deductionPoints"] == 0 for flag in score["triggeredFlags"])
    assert score["totalPenaltyPoints"] == 0


def test_staff_review_threshold_is_inclusive_and_uses_absolute_usd_amount():
    cases = (
        (999.99, False),
        (1000.00, True),
        (1000.01, True),
        (-1000.00, True),
    )
    for amount, requires_review in cases:
        result = analyze_bank_statement(
            _statement(
                transactions=[
                    {
                        "date": "2026-03-20",
                        "description": "Routine account transaction",
                        "amount": amount,
                        "type": "debit" if amount < 0 else "credit",
                        "currency": "USD",
                        "balance": 4321.09,
                    }
                ]
            )
        )
        reviews = result["bankStatementScore"]["transactionReviews"]
        assert bool(reviews) is requires_review
        if requires_review:
            assert reviews[0]["requiresStaffReview"] is True
            assert reviews[0]["amountUsd"] == round(abs(amount), 2)
            assert reviews[0]["balance"] == 4321.09
            assert reviews[0]["exchangeRateToUsd"] == 1
            assert reviews[0]["staffReviewThresholdUsd"] == 1000


def test_non_usd_staff_review_uses_only_configured_usd_exchange_rates():
    fields = _statement(
        currency="KHR",
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Incoming transfer",
                "amount": 4_000_000,
                "type": "credit",
                "currency": "KHR",
            }
        ],
    )
    without_rate = analyze_bank_statement(fields)["bankStatementScore"]
    with_rate = analyze_bank_statement(
        fields,
        config={
            "staff_review_exchange_rates_to_usd": {
                "USD": 1,
                "KHR": 0.00025,
            }
        },
    )["bankStatementScore"]

    assert without_rate["transactionReviews"] == []
    assert len(with_rate["transactionReviews"]) == 1
    assert with_rate["transactionReviews"][0]["amountUsd"] == 1000
    assert with_rate["transactionReviews"][0]["exchangeRateToUsd"] == 0.00025


def test_unverified_source_penalty_is_auditable_and_capped_to_category_maximum():
    fields = _statement(
        transactions=[
            {
                "date": "2026-03-20",
                "description": "Wire transfer",
                "amount": 2000,
                "type": "credit",
                "balance": 6000,
                "page": 2,
                "referenceNumber": "WIRE-1",
            },
            {
                "date": "2026-03-25",
                "description": "Wire transfer out",
                "amount": 1000,
                "type": "debit",
                "balance": 5000,
                "page": 2,
                "referenceNumber": "RENT-2",
            },
        ]
    )
    result = analyze_bank_statement(
        fields,
        transaction_source_reviews={
            "0": {
                "status": "SOURCE_UNVERIFIED",
                "reviewerId": "staff-2",
                "reviewedAt": "2026-04-01T10:00:00Z",
                "penaltyPoints": 4,
                "penaltyReason": "No supporting source evidence provided.",
            },
            "1": {
                "status": "SOURCE_UNVERIFIED",
                "reviewerId": "staff-2",
                "reviewedAt": "2026-04-01T10:00:00Z",
                "penaltyPoints": 4,
                "penaltyReason": "Counterparty could not be established.",
            },
        },
    )
    score = result["bankStatementScore"]

    assert score["totalPenaltyPoints"] == 1.0
    assert score["components"]["transaction_history"]["points"] == 8.0
    assert score["components"]["transaction_history"]["evidence"]["penaltyPoints"] == score["totalPenaltyPoints"]
    assert score["points"] == round(
        sum(item["points"] for item in score["components"].values()), 2
    )
    assert len(score["deductions"]) == 2
    source_deductions = [
        item
        for item in score["deductions"]
        if item["flagType"] == "SOURCE_OF_FUNDS_UNCONFIRMED"
    ]
    assert len(source_deductions) == 2
    assert all(item["reviewerId"] == "staff-2" for item in source_deductions)
    assert not any("FRAUD" in flag["code"] for flag in result["riskFlags"])


def test_source_review_penalty_reduces_transaction_history_and_total_once():
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            {
                "date": "2026-01-15",
                "description": "Monthly salary",
                "amount": 600,
                "type": "credit",
                "balance": 5600,
                "sender": "Acme Payroll",
                "source_text": "Payroll deposit",
            },
            {
                "date": "2026-02-15",
                "description": "Utility bill payment",
                "amount": 500,
                "type": "debit",
                "balance": 5100,
                "receiver": "Power Company",
            },
        ],
    )
    fields["deposits"] = 600
    fields["withdrawals"] = 500
    baseline = analyze_bank_statement(fields)["bankStatementScore"]
    reviewed = analyze_bank_statement(
        fields,
        transaction_source_reviews={
            "0": {
                "status": "SOURCE_UNVERIFIED",
                "reviewerId": "staff-1",
                "reviewedAt": "2026-04-01T10:00:00Z",
            }
        },
    )["bankStatementScore"]
    penalty = reviewed["totalPenaltyPoints"]

    assert penalty == 0.5
    assert reviewed["components"]["transaction_history"]["points"] == (
        baseline["components"]["transaction_history"]["points"] - penalty
    )
    assert reviewed["points"] == round(baseline["points"] - penalty, 2)
    assert reviewed["points"] == round(
        sum(item["points"] for item in reviewed["components"].values()), 2
    )
    assert len(reviewed["deductions"]) == 1


def test_13_unverified_transactions_apply_exact_source_review_deduction():
    transactions = [
        {
            "transactionId": f"TX-{index}",
            "date": f"2026-0{1 + index // 31}-15",
            "description": f"Unverified transfer {index}",
            "amount": 1500,
            "type": "credit",
            "balance": 5000 + index,
        }
        for index in range(13)
    ]
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=transactions,
    )
    fields["deposits"] = 19500
    fields["withdrawals"] = 0

    score = analyze_bank_statement(
        fields,
        document_id="DOC-0E330145DE71",
        transaction_source_reviews={
            str(index): {"status": "UNVERIFIED"}
            for index in range(13)
        },
    )["bankStatementScore"]

    audit = score["sourceReviewDeductionAudit"]
    assert audit["unverifiedTransactionCount"] == 13
    assert audit["totalDeductionPoints"] == 6.5
    assert audit["deductionPerTransaction"] == 0.5
    assert audit["transactionHistoryBasePoints"] == 9
    assert audit["transactionHistoryCurrentPoints"] == 2.5
    assert score["components"]["transaction_history"]["points"] == 2.5
    assert score["points"] == 38.5


def test_flag_scoring_uses_the_full_available_component_baseline_and_deduplicates():
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            {
                "transactionId": "TX-1",
                "date": "2026-01-15",
                "description": "Monthly salary",
                "amount": 2000,
                "type": "credit",
                "balance": 7000,
                "referenceNumber": "PAY-1",
                "sender": "Acme Payroll",
                "source_text": "Payroll deposit",
            },
            {
                "transactionId": "TX-2",
                "date": "2026-01-16",
                "description": "Monthly salary",
                "amount": 2000,
                "type": "credit",
                "balance": 9000,
                "referenceNumber": "PAY-1",
                "sender": "Acme Payroll",
                "source_text": "Payroll deposit",
            },
        ],
    )
    fields["deposits"] = 4000
    fields["withdrawals"] = 0
    score = analyze_bank_statement(
        fields,
        document_id="statement-1",
        transaction_source_reviews={
            "0": {"status": "UNVERIFIED"},
            "1": {"status": "UNVERIFIED"},
        },
    )["bankStatementScore"]
    money_in_flags = [
        flag
        for flag in score["triggeredFlags"]
        if flag["flagType"] == "MONEY_IN_THRESHOLD"
    ]
    source_flags = [
        flag
        for flag in score["triggeredFlags"]
        if flag["flagType"] == "SOURCE_OF_FUNDS_UNCONFIRMED"
    ]

    assert len(money_in_flags) == 1
    assert len(source_flags) == 1
    assert money_in_flags[0]["documentId"] == "statement-1"
    assert money_in_flags[0]["transactionReference"] == "PAY-1"
    assert money_in_flags[0]["deductionPoints"] == 0
    assert source_flags[0]["transactionReference"] == "PAY-1"
    assert source_flags[0]["deductionPoints"] == 0.5
    assert score["totalPenaltyPoints"] == 0.5
    assert score["points"] < 45
    assert score["points"] == round(
        sum(item["points"] for item in score["components"].values()), 2
    )


def test_flag_scoring_uses_all_133_transactions_not_the_review_subset():
    transactions = [
        {
            "date": "2026-02-15",
            "description": f"Statement row {index}",
            "amount": 2000 if index == 0 else 1,
            "type": "credit" if index == 0 else "debit",
            "balance": 6000,
            "referenceNumber": f"TX-{index}",
        }
        for index in range(133)
    ]
    fields = _statement(transactions=transactions)
    fields["deposits"] = 2000
    fields["withdrawals"] = 132
    result = analyze_bank_statement(fields, document_id="full-statement")
    score = result["bankStatementScore"]

    assert score["components"]["transaction_history"]["evidence"]["validTransactionCount"] == 133
    assert sum(
        row["transactionCount"]
        for row in result["transactionSummary"]["monthly"]
    ) == 133
    assert len(
        [flag for flag in score["triggeredFlags"] if flag["flagType"] == "MONEY_IN_THRESHOLD"]
    ) == 1


def test_legacy_threshold_deduction_configuration_does_not_change_the_score():
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            {
                "date": "2026-01-15",
                "description": "Monthly salary",
                "amount": 2100,
                "type": "credit",
                "balance": 7100,
                "sender": "Acme Payroll",
                "source_text": "Payroll deposit",
            },
            {
                "date": "2026-02-15",
                "description": "Utility bill payment",
                "amount": 500,
                "type": "debit",
                "balance": 6600,
                "receiver": "Power Company",
            },
        ],
    )
    fields["deposits"] = 2100
    fields["withdrawals"] = 500
    score = analyze_bank_statement(
        fields,
        config={"money_in_flag_deduction_percent": 20},
        transaction_source_reviews={"0": {"status": "CONFIRMED"}},
    )["bankStatementScore"]

    money_in_flag = next(
        flag
        for flag in score["triggeredFlags"]
        if flag["flagType"] == "MONEY_IN_THRESHOLD"
    )
    assert money_in_flag["configuredDeduction"] == 0
    assert money_in_flag["deductionPoints"] == 0
    assert money_in_flag["deductionStatus"] == "INFORMATIONAL_ONLY"
    assert score["totalPenaltyPoints"] == 0


def test_threshold_flags_do_not_change_the_score():
    clear_transactions = [
        {
            "date": "2026-01-15",
            "description": "Monthly salary",
            "amount": 600,
            "type": "credit",
            "balance": 5600,
            "sender": "Acme Payroll",
            "source_text": "Payroll deposit",
        },
        {
            "date": "2026-02-15",
            "description": "Utility bill payment",
            "amount": 500,
            "type": "debit",
            "balance": 5100,
            "receiver": "Power Company",
        },
    ]
    clean_fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=clear_transactions,
    )
    clean_fields["deposits"] = 600
    clean_fields["withdrawals"] = 500
    no_flags = analyze_bank_statement(clean_fields)["bankStatementScore"]

    flagged_fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=[
            clear_transactions[0],
            {**clear_transactions[1], "amount": 1000},
        ],
    )
    flagged_fields["deposits"] = 600
    flagged_fields["withdrawals"] = 1000
    one_flag = analyze_bank_statement(
        flagged_fields,
        transaction_source_reviews={"1": {"status": "CONFIRMED"}},
    )["bankStatementScore"]

    multiple_fields = _statement(
        balances=(5000, 3999, 5000),
        closing_balance=5000,
        transactions=[
            {**clear_transactions[0], "amount": 2000},
            {**clear_transactions[1], "amount": 1000},
        ],
    )
    multiple_fields["deposits"] = 2000
    multiple_fields["withdrawals"] = 1000
    multiple_flags = analyze_bank_statement(
        multiple_fields,
        transaction_source_reviews={
            "0": {"status": "CONFIRMED"},
            "1": {"status": "CONFIRMED"},
        },
    )["bankStatementScore"]

    assert no_flags["triggeredFlags"] == []
    assert no_flags["points"] == 45
    assert one_flag["points"] == no_flags["points"]
    assert len(one_flag["triggeredFlags"]) == 1
    assert multiple_flags["points"] == one_flag["points"]
    assert len(multiple_flags["triggeredFlags"]) == 3
    assert multiple_flags["points"] == round(
        sum(item["points"] for item in multiple_flags["components"].values()), 2
    )


def test_source_deductions_never_exceed_the_transaction_history_component():
    transactions = [
        {
            "id": f"PAY-{index}",
            "date": f"2026-01-{day:02d}",
            "description": "Monthly salary",
            "amount": 2000,
            "type": "credit",
            "balance": 5000 + index * 2000,
            "sender": "Acme Payroll",
            "source_text": "Payroll deposit",
        }
        for index, day in enumerate(range(1, 21))
    ]
    fields = _statement(
        balances=(5000, 5000, 5000),
        closing_balance=5000,
        transactions=transactions,
    )
    fields["deposits"] = 40000
    fields["withdrawals"] = 0
    reviews = {
        str(index): {"status": "UNVERIFIED"}
        for index in range(len(transactions))
    }

    score = analyze_bank_statement(
        fields,
        transaction_source_reviews=reviews,
    )["bankStatementScore"]

    assert score["components"]["transaction_history"]["maximum"] == 9
    assert score["components"]["transaction_history"]["points"] == 0
    assert score["components"]["transaction_history"]["deductionPoints"] == 9
    assert score["totalPenaltyPoints"] == 9
    assert all(
        0 <= item["points"] <= item["maximum"]
        for item in score["components"].values()
    )
    assert score["points"] == round(
        sum(item["points"] for item in score["components"].values()), 2
    )


def test_insufficient_period_and_unsupported_currency_do_not_fabricate_points():
    fields = _statement(currency="KHR")
    result = analyze_bank_statement(fields)

    assert result["bankStatementScore"]["components"]["ending_balance"]["points"] is None
    assert result["bankStatementScore"]["components"]["starting_balance"]["points"] is None
    assert result["bankStatementScore"]["points"] is None
    assert result["bankStatementScore"]["assessment"] == "INSUFFICIENT_DATA"


def test_historical_pattern_deviation_is_flagged_only_with_supplied_history():
    fields = _statement()
    fields["historicalTransactionHistory"] = [
        {
            "date": f"2025-{month:02d}-{day:02d}",
            "description": "Salary",
            "amount": 100,
            "type": "credit",
        }
        for month in (10, 11, 12)
        for day in (1, 8, 15, 22)
    ]
    result = analyze_bank_statement(fields)

    assert result["historicalPatternAssessment"]["status"] == "AVAILABLE"
    assert result["historicalPatternAssessment"]["significantDeviation"] is True
    assert any(flag["code"] == "HISTORICAL_PATTERN_DEVIATION" for flag in result["riskFlags"])
    assert all(
        item["reviewStatus"] == "PENDING_REVIEW"
        for item in result["bankStatementScore"]["transactionReviews"]
    )


def test_bank_penalty_controls_and_destination_balance_rules_are_configurable():
    policy = validate_rule_policy(
        {
            **DEFAULT_RULE_POLICY,
            "bank_statement_scoring": {
                **DEFAULT_RULE_POLICY["bank_statement_scoring"],
                "unverified_source_penalty_points": 1.25,
                "maximum_transaction_history_penalty": 9,
                "money_in_flag_threshold_usd": 2500,
                "money_out_flag_threshold_usd": 1500,
                "money_in_flag_deduction_percent": 12.5,
                "money_out_flag_deduction_percent": 7.5,
                "monthly_low_balance_flag_deduction_percent": 20,
                "deduct_pending_source_review": True,
                "require_source_evidence_for_confirmation": False,
                "minimum_source_review_notes_length": 12,
            },
        }
    )

    assert policy["bank_statement_scoring"]["unverified_source_penalty_points"] == 0.5
    assert policy["bank_statement_scoring"]["money_in_flag_threshold_usd"] == 2500
    assert policy["bank_statement_scoring"]["money_out_flag_threshold_usd"] == 1500
    assert policy["bank_statement_scoring"]["money_in_flag_deduction_percent"] == 0
    assert policy["bank_statement_scoring"]["money_out_flag_deduction_percent"] == 0
    assert policy["bank_statement_scoring"]["monthly_low_balance_flag_deduction_percent"] == 0
    assert policy["bank_statement_scoring"]["deduct_pending_source_review"] is False
    assert policy["bank_statement_scoring"]["require_source_evidence_for_confirmation"] is False
    assert policy["bank_statement_scoring"]["minimum_source_review_notes_length"] == 12
    assert configured_bank_balance_requirement(policy, "Australia", "Cambodian") == 5000
    assert configured_bank_balance_requirement(policy, "Australia", "French") == 4500
    assert configured_bank_balance_requirement(policy, "Australia", "") is None

    with pytest.raises(ValueError, match="deduct_pending_source_review must be boolean"):
        validate_rule_policy(
            {
                **DEFAULT_RULE_POLICY,
                "bank_statement_scoring": {
                    **DEFAULT_RULE_POLICY["bank_statement_scoring"],
                    "deduct_pending_source_review": "yes",
                },
            }
        )


def test_admin_can_configure_staff_review_threshold_and_exchange_rates():
    policy = validate_rule_policy(
        {
            **DEFAULT_RULE_POLICY,
            "bank_statement_scoring": {
                **DEFAULT_RULE_POLICY["bank_statement_scoring"],
                "staff_review_threshold_usd": 1250,
                "staff_review_exchange_rates_to_usd": {
                    "usd": 1,
                    "khr": 0.00025,
                },
            },
        }
    )["bank_statement_scoring"]

    assert policy["staff_review_threshold_usd"] == 1250
    assert policy["staff_review_exchange_rates_to_usd"] == {
        "USD": 1.0,
        "KHR": 0.00025,
    }
    assert policy["weights"] == DEFAULT_RULE_POLICY["bank_statement_scoring"]["weights"]


def test_source_review_penalty_display_value_is_fixed():
    score = analyze_bank_statement(
        _statement(),
        config={"unverified_source_penalty_points": 2.0},
    )["bankStatementScore"]

    assert score["transactionReviewRequirements"]["unverifiedSourcePenaltyPoints"] == 0.5
    assert score["transactionReviewRequirements"]["maximumBankScore"] == 45
