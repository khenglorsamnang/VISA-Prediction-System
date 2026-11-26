from __future__ import annotations

from backend.app.api.bank_statements import _build_result
from backend.app.services.bank_statement_scoring import (
    analyze_bank_statement,
    calculate_document_verification_score,
)
from backend.app.services.transaction_normalization import normalize_transactions


def test_normalizes_aliases_amount_sign_currency_balance_page_and_evidence():
    result = normalize_transactions(
        [
            {
                "transaction_date": "13/09/2026",
                "transaction_description": "Card payment",
                "transaction_amount": "($1,234.50)",
                "transaction_type": "DR",
                "runningBalance": "$8,765.50",
                "currency": "USD",
                "page_number": "33",
                "source_text": "13/09/2026 Card payment (1,234.50) 8,765.50",
            }
        ]
    )

    transaction = result["transactions"][0]
    assert transaction == {
        "transactionIndex": 0,
        "date": "2026-09-13",
        "description": "Card payment",
        "amount": 1234.5,
        "direction": "debit",
        "running_balance": 8765.5,
        "currency": "USD",
        "page": 33,
        "evidence": "13/09/2026 Card payment (1,234.50) 8,765.50",
        "extraction_status": "VALID",
        "normalization_errors": [],
        "type": "debit",
        "runningBalance": 8765.5,
        "sourceText": "13/09/2026 Card payment (1,234.50) 8,765.50",
        "referenceNumber": None,
        "sender": None,
        "receiver": None,
        "counterparty": None,
    }
    assert result["valid_count"] == 1
    assert result["invalid_transactions"] == []


def test_parses_multiple_date_and_localized_amount_formats_and_reports_ambiguity():
    result = normalize_transactions(
        [
            {"date": "2026-09-13", "description": "Salary", "amount": "$2,400.00", "type": "CR"},
            {"date": "Sep 13, 2026", "description": "Payment", "amount": "1.234,56 EUR", "type": "DR"},
            {"date": "09/10/2026", "description": "Unknown date order", "amount": "25.00", "type": "debit"},
        ]
    )

    assert result["transactions"][0]["date"] == "2026-09-13"
    assert result["transactions"][0]["amount"] == 2400
    assert result["transactions"][0]["currency"] == "USD"
    assert result["transactions"][1]["date"] == "2026-09-13"
    assert result["transactions"][1]["amount"] == 1234.56
    assert result["transactions"][1]["currency"] == "EUR"
    assert result["transactions"][2]["date"] is None
    assert "ambiguous_date" in result["transactions"][2]["normalization_errors"]
    assert len(result["invalid_transactions"]) == 1


def test_debit_credit_columns_and_missing_running_balances_are_supported():
    result = normalize_transactions(
        [
            {"date": "2026-01-02", "description": "Deposit", "debit": "0.00", "credit": "1,250.00", "currency": "USD"},
            {"date": "2026-01-03", "description": "Fee", "debit_amount": "2.50", "credit_amount": "0", "currency": "USD"},
        ]
    )

    assert [(row["amount"], row["direction"]) for row in result["transactions"]] == [
        (1250.0, "credit"),
        (2.5, "debit"),
    ]
    assert all(row["running_balance"] is None for row in result["transactions"])
    assert result["valid_count"] == 2


def test_legacy_snake_case_statement_fields_are_canonicalized_without_mutation():
    fields = {
        "bank_name": "Test Bank",
        "account_holder_name": "Dara Sok Lee",
        "account_no": "12345678",
        "opening_balance": "5,000.00",
        "closing_balance": "5,100.00",
        "currency_code": "USD",
        "statement_period": {"start_date": "2026-01-01", "end_date": "2026-03-31"},
        "total_credits": "100.00",
        "total_debits": "0.00",
        "transaction_history": [
            {"transaction_date": "2026-02-01", "description": "Deposit", "amount": "100.00", "direction": "credit"}
        ],
        "average_balance": "5,050.00",
    }
    original = fields.copy()
    result = analyze_bank_statement(fields, applicant_name="Lee Dara Sok")
    verification = calculate_document_verification_score(fields)

    assert fields == original
    assert result["statementPeriod"]["startDate"] == "2026-01-01"
    assert result["statementPeriod"]["endDate"] == "2026-03-31"
    assert result["transactionCount"] == 1
    assert verification["extracted_fields"] == verification["required_fields"]


def test_ambiguous_and_empty_rows_are_retained_instead_of_discarded():
    result = normalize_transactions(
        [
            {"date": "2026-02-01", "description": "two-sided row", "debit": "10", "credit": "20"},
            {"date": "2026-02-02", "description": "amount with no direction", "amount": "10"},
            {},
        ]
    )

    assert len(result["transactions"]) == 3
    assert len(result["invalid_transactions"]) == 3
    assert "ambiguous_debit_and_credit" in result["invalid_transactions"][0]["issues"]
    assert "ambiguous_or_missing_direction" in result["invalid_transactions"][1]["issues"]


def test_negative_amount_conflicting_with_explicit_credit_is_retained_for_review():
    result = normalize_transactions(
        [
            {"date": "2026-02-01", "description": "Credit row", "amount": "-10.00", "type": "credit"}
        ]
    )

    assert len(result["transactions"]) == 1
    assert result["transactions"][0]["direction"] is None
    assert "conflicting_amount_sign_and_direction" in result["transactions"][0]["normalization_errors"]
    assert len(result["invalid_transactions"]) == 1


def _three_month_fields(transactions: list[dict], **updates):
    fields = {
        "bankName": "Test Bank",
        "accountHolderName": "Dara Sok Lee",
        "accountNumber": "TEST-1234",
        "openingBalance": "5000.00",
        "closingBalance": "5003.00",
        "currency": "USD",
        "statementStartDate": "2026-01-01",
        "statementEndDate": "2026-03-31",
        "deposits": "87.00",
        "withdrawals": "84.00",
        "averageBalance": "5001.50",
        "transactionHistory": transactions,
        "monthlyClosingBalances": [
            {"month": "2026-01", "closingBalance": "5001.00", "currency": "USD", "page": 13, "sourceText": "Jan closing balance"},
            {"month": "2026-02", "closingBalance": "5002.00", "currency": "USD", "page": 25, "sourceText": "Feb closing balance"},
            {"month": "2026-03", "closingBalance": "5003.00", "currency": "USD", "page": 37, "sourceText": "Mar closing balance"},
        ],
    }
    fields.update(updates)
    return fields


def _three_month_transactions():
    transactions = []
    for month_index, month in enumerate(("01", "02", "03")):
        year = 2026
        for offset in range(57):
            direction = "credit" if offset < 29 else "debit"
            day = offset % 28 + 1
            amount = "1.00"
            transactions.append(
                {
                    "transactionDate": f"{year}-{month}-{day:02d}",
                    "transaction_description": f"Test row {month_index}-{offset}",
                    "transaction_amount": amount,
                    "transaction_type": direction,
                    "page_number": (len(transactions) % 37) + 1,
                    "source_text": f"OCR fixture row {month_index}-{offset}",
                }
            )
    return transactions


def test_37_page_ocr_and_171_transactions_share_one_normalized_analysis_dataset():
    transactions = _three_month_transactions()
    fields = _three_month_fields(transactions)
    pages = [{"page": number, "text": f"OCR test page {number}"} for number in range(1, 38)]
    result = _build_result(
        {
            "filename": "synthetic-regression-fixture.pdf",
            "status": "success",
            "confidence": 0.95,
            "fields": fields,
            "pages": pages,
            "metrics": {"page_count": 37, "pages_processed": 37, "status": "OCR_COMPLETED"},
        },
        applicant_name="Lee Dara Sok",
    )

    bank_analysis = result["bank_statement_analysis"]
    assert bank_analysis["transactionCount"] == 171
    assert result["financial_analysis"]["transaction_frequency"] == 171
    assert bank_analysis["transactions"] == result["financial_analysis"]["normalized_transactions"]
    assert sum(item["transactionCount"] for item in bank_analysis["transactionSummary"]["monthly"]) == 171
    assert bank_analysis["statementPeriod"]["completeThreeMonths"] is True
    assert bank_analysis["financialRequirementStatus"] == "PASS"
    assert bank_analysis["reconciliation"]["status"] == "MATCHED"
    assert bank_analysis["accountHolderCheck"]["status"] == "NOT_ASSESSED"
    assert bank_analysis["accountHolderCheck"]["accountHolderName"] == "Dara Sok Lee"
    assert result["ocr_processing_summary"]["totalPages"] == 37
    assert bank_analysis["documentInformation"]["ocrPageCount"] == 37


def test_partial_calendar_months_never_pass_three_complete_month_requirement():
    result = analyze_bank_statement(
        _three_month_fields(
            _three_month_transactions(),
            statementStartDate="2026-01-15",
            statementEndDate="2026-03-31",
        ),
        applicant_name="Dara Sok Lee",
    )

    assert result["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert result["statementPeriod"]["status"] == "INSUFFICIENT_DATA"


def test_ocr_page_failure_keeps_rows_but_disables_final_scores():
    result = _build_result(
        {
            "filename": "synthetic-partial-fixture.pdf",
            "status": "partial",
            "confidence": 0.9,
            "failed_pages": [{"page": 7, "error": "test OCR failure"}],
            "fields": _three_month_fields(_three_month_transactions()),
            "pages": [{"page": number, "text": "fixture"} for number in range(1, 38)],
            "metrics": {"page_count": 37, "pages_processed": 37, "status": "OCR_PARTIAL"},
        },
        applicant_name="Dara Sok Lee",
    )

    analysis = result["bank_statement_analysis"]
    assert analysis["transactionCount"] == 171
    assert analysis["bankStatementScore"]["available"] is False
    assert analysis["bankStatementScore"]["points"] is None
    assert analysis["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert result["ocr_processing_summary"]["status"] == "OCR_PARTIAL"


def test_account_holder_name_is_preserved_without_applicant_name_comparison():
    matched = analyze_bank_statement(
        _three_month_fields([]), applicant_name="Lee, Dara Sok"
    )
    possible = analyze_bank_statement(
        _three_month_fields([], accountHolderName="Dara Sok"),
        applicant_name="Dara Sok Lee",
    )
    manual = analyze_bank_statement(
        _three_month_fields([], accountHolderName="Other Person"),
        applicant_name="Dara Sok Lee",
    )

    accent_variant = analyze_bank_statement(
        _three_month_fields([], accountHolderName="Dára Sok Lee"),
        applicant_name="LEE, Dara Sok",
    )
    for result in (matched, possible, manual, accent_variant):
        assert result["accountHolderCheck"]["status"] == "NOT_ASSESSED"
        assert "not compared" in result["accountHolderCheck"]["reason"].lower()
        assert "legal ownership of the account is not asserted" in result["accountHolderCheck"]["reason"].lower()
    assert manual["accountHolderCheck"]["accountHolderName"] == "Other Person"
    assert accent_variant["accountHolderCheck"]["accountHolderName"] == "Dára Sok Lee"


def test_unexplained_large_deposits_are_flagged_and_income_requires_recurrence():
    transactions = [
        {"date": "2026-01-15", "description": "Monthly salary ACME", "amount": "2500", "type": "credit"},
        {"date": "2026-02-15", "description": "Monthly salary ACME", "amount": "2500", "type": "credit"},
        {"date": "2026-03-20", "description": "Cash deposit", "amount": "3000", "type": "credit"},
    ]
    result = analyze_bank_statement(_three_month_fields(transactions))

    source = result["sourceOfFundsAnalysis"]
    assert source["status"] == "POSSIBLE_MATCH"
    assert len(source["recurringIncomeCandidates"]) == 1
    assert len(source["unexplainedLargeDeposits"]) == 1
    assert any(flag["code"] == "LARGE_DEPOSIT_SOURCE_REVIEW" for flag in result["riskFlags"])


def test_recurring_transfers_are_candidates_not_automatically_classified_as_income():
    result = analyze_bank_statement(
        _three_month_fields(
            [
                {"date": "2026-01-10", "description": "Transfer from account A", "amount": "300", "type": "credit"},
                {"date": "2026-02-10", "description": "Transfer from account A", "amount": "300", "type": "credit"},
            ]
        )
    )

    candidate = result["sourceOfFundsAnalysis"]["recurringIncomeCandidates"][0]
    assert candidate["category"] == "recurring_transfer_candidate"
    assert candidate["status"] == "POSSIBLE_MATCH"
    assert "verify" in candidate["note"].lower()


def test_missing_balance_evidence_and_empty_transactions_are_not_matched():
    result = analyze_bank_statement(
        {
            "transactionHistory": [],
        }
    )

    assert result["transactions"] == []
    assert result["invalidTransactionCount"] == 0
    assert result["reconciliation"]["status"] == "NOT_AVAILABLE"
    assert result["bankStatementScore"]["available"] is False
    assert result["bankStatementScore"]["points"] is None
    assert "opening_balance" in result["missingEvidence"]
    assert "closing_balance" in result["missingEvidence"]


def test_reconciliation_mismatch_is_explicit_and_never_reported_as_matched():
    fields = _three_month_fields(
        [
            {"date": "2026-01-15", "description": "Deposit", "amount": "100", "type": "credit"}
        ],
        openingBalance="1000",
        closingBalance="1200",
        deposits="100",
        withdrawals="0",
    )
    fields["monthlyClosingBalances"][-1]["closingBalance"] = "1200"
    result = analyze_bank_statement(fields)

    assert result["reconciliation"]["status"] == "MISMATCHED"
    assert result["reconciliation"]["expected_closing_balance"] == 1100
    assert result["reconciliation"]["extracted_closing_balance"] == 1200
    assert any(flag["code"] == "RECONCILIATION_MISMATCH" for flag in result["riskFlags"])
