from __future__ import annotations

import calendar
import heapq
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .financial_analysis import calculate_financial_analysis
from .transaction_normalization import normalize_transactions, parse_amount


SOURCE_UNVERIFIED_PENALTY_POINTS = 0.5

REQUIRED_BANK_FIELDS = (
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
)

DEFAULT_BANK_STATEMENT_SCORING: dict[str, Any] = {
    "minimum_monthly_closing_balance_usd": 4000,
    "full_balance_score_usd": 8000,
    "money_in_flag_threshold_usd": 2000,
    "money_out_flag_threshold_usd": 1000,
    "money_in_flag_deduction_percent": 0,
    "money_out_flag_deduction_percent": 0,
    "monthly_low_balance_flag_deduction_percent": 0,
    "minimum_statement_period_days": 90,
    "ending_balance_recent_withdrawal_days": 7,
    "historical_pattern_deviation_ratio": 2.0,
    "recent_large_deposit_days": 30,
    "rapid_funds_outflow_days": 7,
    "rapid_funds_outflow_ratio": 0.8,
    "unverified_source_penalty_points": SOURCE_UNVERIFIED_PENALTY_POINTS,
    "deduct_pending_source_review": False,
    "maximum_transaction_history_penalty": 9.0,
    "require_source_evidence_for_confirmation": True,
    "minimum_source_review_notes_length": 8,
    "staff_review_threshold_usd": 1000,
    "staff_review_exchange_rates_to_usd": {"USD": 1.0},
    "weights": {
        "ending_balance": 22.5,
        "starting_balance": 6.75,
        "transaction_history": 9.0,
        "transaction_period": 6.75,
    },
}


def calculate_bank_statement_score(
    components: dict[str, Any],
    *,
    weights: dict[str, Any] | None = None,
    ocr_complete: bool = True,
    score_available: bool = True,
) -> dict[str, Any]:
    """Normalize four category scores and calculate their single 45-point total."""
    score_weights = (
        DEFAULT_BANK_STATEMENT_SCORING["weights"] if weights is None else weights
    )
    if set(score_weights) != set(DEFAULT_BANK_STATEMENT_SCORING["weights"]):
        raise ValueError("Bank statement score weights must define all four components.")
    normalized_components: dict[str, dict[str, Any]] = {}
    all_components_available = True
    available_maximum = 0.0
    for key, configured_maximum in score_weights.items():
        maximum = float(configured_maximum)
        item = components.get(key)
        item = item if isinstance(item, dict) else {}
        raw_points = item.get("points")
        raw_maximum = item.get("maximum")
        try:
            points = (
                None
                if isinstance(raw_points, bool) or raw_points is None
                else float(raw_points)
            )
            stored_maximum = (
                None
                if isinstance(raw_maximum, bool) or raw_maximum is None
                else float(raw_maximum)
            )
        except (TypeError, ValueError):
            points = None
            stored_maximum = None
        category_available = (
            ocr_complete
            and bool(item.get("available", points is not None))
            and points is not None
            and math.isfinite(points)
            and stored_maximum == maximum
        )
        normalized_points = (
            round(min(max(points, 0.0), maximum), 2)
            if category_available and points is not None
            else None
        )
        normalized_components[key] = {
            **item,
            "points": normalized_points,
            "maximum": maximum,
            "available": category_available,
        }
        all_components_available = all_components_available and category_available
        if category_available:
            available_maximum += maximum

    maximum = sum(float(value) for value in score_weights.values())
    available = ocr_complete and score_available and all_components_available
    points = (
        round(
            min(
                maximum,
                max(
                    0.0,
                    sum(
                        float(item["points"])
                        for item in normalized_components.values()
                    ),
                ),
            ),
            2,
        )
        if available
        else None
    )
    return {
        "points": points,
        "maximum": maximum,
        "percentage": round(points / maximum * 100, 2) if points is not None else None,
        "available": available,
        "availableMaximumPoints": round(available_maximum, 2),
        "components": normalized_components,
    }


_BANK_FIELD_ALIASES = {
    "bankName": ("bank_name", "bank"),
    "accountHolderName": ("account_holder_name", "accountName", "holderName"),
    "accountNumber": ("account_number", "accountNo", "account_no"),
    "openingBalance": ("opening_balance", "beginningBalance", "beginning_balance"),
    "closingBalance": ("closing_balance", "endingBalance", "ending_balance"),
    "currency": ("currency_code", "currencyCode"),
    "statementStartDate": ("statement_start_date", "periodStartDate", "period_start_date"),
    "statementEndDate": ("statement_end_date", "periodEndDate", "period_end_date"),
    "deposits": ("totalCredits", "total_credits", "totalDeposits", "total_deposits"),
    "withdrawals": ("totalDebits", "total_debits", "totalWithdrawals", "total_withdrawals"),
    "transactionHistory": ("transaction_history", "transactions"),
    "averageBalance": ("average_balance",),
    "monthlyClosingBalances": ("monthly_closing_balances",),
    "monthlyBalanceHistory": ("monthly_balance_history", "bankMonthlySummaries"),
}


def normalize_bank_fields(fields: Any) -> dict[str, Any]:
    """Return a non-mutating field map with canonical names for legacy aliases."""
    normalized = dict(fields) if isinstance(fields, dict) else {}
    for canonical, aliases in _BANK_FIELD_ALIASES.items():
        if normalized.get(canonical) not in (None, "", []):
            continue
        for alias in aliases:
            if normalized.get(alias) not in (None, "", []):
                normalized[canonical] = normalized[alias]
                break
    period = normalized.get("statementPeriod") or normalized.get("statement_period")
    if isinstance(period, dict):
        normalized.setdefault(
            "statementStartDate", period.get("startDate") or period.get("start_date")
        )
        normalized.setdefault(
            "statementEndDate", period.get("endDate") or period.get("end_date")
        )
    return normalized


def calculate_document_verification_score(
    fields: dict[str, Any],
    required_fields: tuple[str, ...] = REQUIRED_BANK_FIELDS,
) -> dict[str, Any]:
    fields = normalize_bank_fields(fields)
    present = [
        name
        for name in required_fields
        if fields.get(name) not in (None, "", [])
    ]
    missing = [name for name in required_fields if name not in present]
    score = round(100 * len(present) / len(required_fields)) if required_fields else 0
    return {
        "document_verification_score": score,
        "score_type": "document_field_completeness",
        "required_fields": len(required_fields),
        "extracted_fields": len(present),
        "missing_fields": missing,
        "basis": (
            "Percentage of required bank-statement fields extracted. "
            "This is not a visa approval probability or authenticity result."
        ),
    }


_SOURCE_REVIEW_FLAG_CODES = {
    "LARGE_DEPOSIT_SOURCE_REVIEW",
    "RECENT_LARGE_DEPOSIT",
    "TRANSFER_REVIEW",
    "RAPID_FUNDS_OUTFLOW",
    "FREQUENT_UNEXPLAINED_DEPOSITS",
    "MISSING_COUNTERPARTY",
    "HISTORICAL_PATTERN_DEVIATION",
    "TRANSACTION_CURRENCY_MISMATCH",
}


def _transaction_reference(transaction: dict[str, Any]) -> str | None:
    for key in ("referenceNumber", "reference_number", "reference"):
        value = transaction.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _transaction_identity(transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": transaction.get("date"),
        "amount": transaction.get("amount"),
        "currency": transaction.get("currency"),
        "direction": transaction.get("direction"),
        "description": transaction.get("description"),
        "reference": _transaction_reference(transaction),
    }


def analyze_bank_statement(
    fields: dict[str, Any],
    *,
    raw_text: str = "",
    applicant_name: str = "",
    config: dict[str, Any] | None = None,
    financial_config: dict[str, Any] | None = None,
    ocr_complete: bool = True,
    transaction_source_reviews: dict[str, dict[str, Any]] | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Score the four bank-evidence components from available points and flags."""
    policy = {**DEFAULT_BANK_STATEMENT_SCORING, **(config or {})}
    weights = policy.get("weights", DEFAULT_BANK_STATEMENT_SCORING["weights"])
    if weights != DEFAULT_BANK_STATEMENT_SCORING["weights"]:
        raise ValueError(
            "Bank statement score weights must be Ending Balance 22.50, "
            "Starting Balance 6.75, Transaction History 9.00, and "
            "Transaction Period 6.75."
        )
    result = _analyze_bank_statement_base(
        fields,
        raw_text=raw_text,
        applicant_name=applicant_name,
        config=policy,
        financial_config=financial_config,
        ocr_complete=ocr_complete,
    )
    normalized_fields = normalize_bank_fields(fields)
    statement_start = _date(normalized_fields.get("statementStartDate"))
    end = _date(normalized_fields.get("statementEndDate"))
    statement_period = result.get("statementPeriod") or {}
    start = _date(statement_period.get("scoringStartDate")) or statement_start
    evaluated_months = set(statement_period.get("monthsEvaluated") or [])

    def transaction_date(item: dict[str, Any]) -> date | None:
        return _date(item.get("date") or item.get("transactionDate"))

    def transaction_in_period(item: dict[str, Any]) -> bool:
        tx_date = transaction_date(item)
        return tx_date is None or bool(start and end and start <= tx_date <= end)

    transactions = result.get("transactions") or []
    period_transactions = [
        item
        for item in transactions
        if isinstance(item, dict) and transaction_in_period(item)
    ]
    historical_source = (
        normalized_fields.get("historicalTransactionHistory")
        or normalized_fields.get("historical_transaction_history")
        or []
    )
    historical_transactions = normalize_transactions(
        historical_source,
        default_currency=normalized_fields.get("currency"),
    )["transactions"]
    historical_dates = [
        _date(item.get("date"))
        for item in historical_transactions
        if item.get("extraction_status") == "VALID" and item.get("date")
    ]
    current_valid_count = sum(
        item.get("extraction_status") == "VALID"
        and item.get("date") is not None
        and item.get("amount") is not None
        and item.get("direction") in {"credit", "debit"}
        and item.get("currency") == result.get("accountInformation", {}).get("currency")
        and transaction_in_period(item)
        for item in transactions
        if isinstance(item, dict)
    )
    current_month_count = (
        len(evaluated_months)
        if statement_period.get("completeThreeMonths") and evaluated_months
        else None
    )
    historical_month_count = (
        max(1, (max(historical_dates).year - min(historical_dates).year) * 12
            + max(historical_dates).month - min(historical_dates).month + 1)
        if historical_dates
        else None
    )
    historical_pattern_ratio = None
    if historical_month_count and current_month_count:
        historical_frequency = len(historical_dates) / historical_month_count
        current_frequency = current_valid_count / current_month_count
        if historical_frequency == 0:
            historical_pattern_ratio = float("inf") if current_frequency > 0 else 1.0
        elif current_frequency == 0:
            historical_pattern_ratio = float("inf")
        else:
            historical_pattern_ratio = max(
                historical_frequency / current_frequency,
                current_frequency / historical_frequency,
            )
        if historical_pattern_ratio >= float(policy["historical_pattern_deviation_ratio"]):
            for index, transaction in enumerate(transactions):
                if not isinstance(transaction, dict) or not transaction_in_period(transaction):
                    continue
                result.setdefault("riskFlags", []).append(
                    {
                        "code": "HISTORICAL_PATTERN_DEVIATION",
                        "reason": "Transaction frequency differs materially from the supplied historical transaction evidence; request context.",
                        "severity": "medium",
                        "manualReviewRequired": True,
                        "transactionIndex": index,
                        "date": transaction.get("date"),
                        "amount": transaction.get("amount"),
                        "currency": transaction.get("currency"),
                        "description": transaction.get("description"),
                        "page": transaction.get("page"),
                        "referenceNumber": transaction.get("referenceNumber"),
                    }
                )
    result["historicalPatternAssessment"] = {
        "status": "AVAILABLE" if historical_month_count else "INSUFFICIENT_DATA",
        "historicalTransactionCount": len(historical_dates),
        "historicalPeriodMonths": historical_month_count,
        "currentTransactionCount": current_valid_count,
        "currentPeriodMonths": current_month_count,
        "deviationRatio": (
            historical_pattern_ratio
            if historical_pattern_ratio is None or historical_pattern_ratio != float("inf")
            else None
        ),
        "significantDeviation": bool(
            historical_pattern_ratio is not None
            and historical_pattern_ratio >= float(policy["historical_pattern_deviation_ratio"])
        ),
        "thresholdRatio": float(policy["historical_pattern_deviation_ratio"]),
    }
    required_balance = float(policy["minimum_monthly_closing_balance_usd"])
    period_days = int(policy["minimum_statement_period_days"])
    scoring_currency = result.get("accountInformation", {}).get("currency")
    valid_transactions = [
        (index, item)
        for index, item in enumerate(transactions)
        if isinstance(item, dict)
        and item.get("extraction_status") == "VALID"
        and item.get("date")
        and item.get("running_balance") is not None
        and item.get("currency") == scoring_currency
        and (not start or item["date"] >= start.isoformat())
        and (not end or item["date"] <= end.isoformat())
    ]
    latest_monthly_check = next(
        (
            item
            for item in reversed(result.get("monthlyBalanceChecks") or [])
            if end and item.get("month") == end.strftime("%Y-%m")
        ),
        None,
    )
    latest_closing_verified = bool(
        statement_period.get("completeThreeMonths")
        and latest_monthly_check
        and not latest_monthly_check.get("ambiguous")
        and latest_monthly_check.get("closingBalance") is not None
        and latest_monthly_check.get("currency") == scoring_currency
    )
    period_opening = result.get("periodOpeningBalance")
    period_closing = (
        latest_monthly_check.get("closingBalance")
        if latest_closing_verified
        else None
    )
    result["accountInformation"]["openingBalance"] = period_opening
    result["accountInformation"]["closingBalance"] = period_closing
    dated_period_transactions = [
        item
        for item in transactions
        if isinstance(item, dict)
        and (tx_date := transaction_date(item)) is not None
        and start
        and end
        and start <= tx_date <= end
    ]
    period_financial_fields = {
        **normalized_fields,
        "statementStartDate": start.isoformat() if start else None,
        "statementEndDate": end.isoformat() if end else None,
        "openingBalance": period_opening,
        "closingBalance": period_closing,
        "averageBalance": None,
        "deposits": (
            normalized_fields.get("deposits")
            if statement_start == start
            else None
        ),
        "withdrawals": (
            normalized_fields.get("withdrawals")
            if statement_start == start
            else None
        ),
    }
    period_financial_analysis = calculate_financial_analysis(
        period_financial_fields,
        dated_period_transactions,
        financial_config,
    )
    if not ocr_complete:
        period_financial_analysis = {
            **period_financial_analysis,
            "financial_score": None,
            "status": "manual_review",
            "human_review_required": True,
        }
    period_reconciliation = period_financial_analysis.get("reconciliation") or {}
    has_undated_transactions = any(
        isinstance(item, dict) and transaction_date(item) is None
        for item in transactions
    )
    if (
        not statement_period.get("completeThreeMonths")
        or result.get("periodOpeningBalanceAmbiguous")
        or period_closing is None
        or has_undated_transactions
    ):
        period_reconciliation = {
            **period_reconciliation,
            "status": "not_available",
            "statusLabel": "NOT_AVAILABLE",
            "delta": None,
        }
    result["financialAnalysis"] = period_financial_analysis
    result["reconciliation"] = {
        **period_reconciliation,
        "status": period_reconciliation.get("statusLabel", "NOT_AVAILABLE"),
        "legacyStatus": period_reconciliation.get("status"),
    }
    scoped_risk_flags = []
    for flag in result.get("riskFlags", []):
        index = flag.get("transactionIndex")
        if isinstance(index, int) and (
            index >= len(transactions)
            or not isinstance(transactions[index], dict)
            or not transaction_in_period(transactions[index])
        ):
            continue
        flag_month = flag.get("month")
        if (
            flag_month
            and flag.get("code")
            in {
                "CONFLICTING_MONTHLY_BALANCES",
                "CONFLICTING_MONTHLY_OPENING_BALANCES",
                "MONTHLY_BALANCE_CONTINUITY_MISMATCH",
            }
            and flag_month not in evaluated_months
        ):
            continue
        scoped_risk_flags.append(flag)
    result["riskFlags"] = scoped_risk_flags
    reconciliation_flags = {
        "RECONCILIATION_MISMATCH",
        "RECONCILIATION_INSUFFICIENT_DATA",
    }
    result["riskFlags"] = [
        flag
        for flag in result.get("riskFlags", [])
        if flag.get("code") not in reconciliation_flags
    ]
    if result["reconciliation"]["status"] == "MISMATCHED":
        result["riskFlags"].append(
            {
                "code": "RECONCILIATION_MISMATCH",
                "reason": "The opening balance plus in-period credits minus in-period debits differs from the period-end closing balance.",
                "severity": "medium",
                "manualReviewRequired": True,
                "expectedClosingBalance": period_reconciliation.get(
                    "expected_closing_balance"
                ),
                "extractedClosingBalance": period_closing,
                "difference": period_reconciliation.get("delta"),
            }
        )
    elif result["reconciliation"]["status"] == "NOT_AVAILABLE":
        result["riskFlags"].append(
            {
                "code": "RECONCILIATION_INSUFFICIENT_DATA",
                "reason": "The complete three-month period cannot be reconciled from its boundary balances and dated transactions.",
                "severity": "low",
                "manualReviewRequired": True,
            }
        )
    result["reviewFlags"] = result["riskFlags"]
    result["reviewReasons"] = list(
        dict.fromkeys(flag["reason"] for flag in result["riskFlags"])
    )
    result["status"] = (
        "CONSISTENCY_CHECK_PASSED"
        if ocr_complete
        and result.get("financialRequirementStatus") == "PASS"
        and not result["riskFlags"]
        else "NEEDS_MANUAL_REVIEW"
    )

    def component(
        key: str,
        ratio: float | None,
        explanation: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        maximum = float(weights[key])
        bounded = max(0.0, min(1.0, ratio)) if ratio is not None else None
        return {
            "points": round(maximum * bounded, 2) if bounded is not None else None,
            "maximum": maximum,
            "available": bounded is not None,
            "evidence_ratio": round(bounded, 4) if bounded is not None else None,
            "explanation": explanation,
            "evidence": evidence,
        }

    ending_component: dict[str, Any]
    ending_evidence: dict[str, Any] = {
        "requiredMinimum": required_balance,
        "currency": scoring_currency,
        "basis": "closing balance at the end of the latest complete three-month period",
        "scoringStartDate": start.isoformat() if start else None,
        "scoringEndDate": end.isoformat() if end else None,
    }
    if latest_closing_verified and scoring_currency == "USD":
        final_balance = float(latest_monthly_check["closingBalance"])
        balance_ratio = max(0.0, min(1.0, final_balance / required_balance))
        recent_withdrawals = sum(
            abs(float(item.get("amount") or 0))
            for item in period_transactions
            if item.get("direction") == "debit"
            and item.get("date")
            and end
            and 0 <= (end - _date(item["date"])).days <= int(policy["ending_balance_recent_withdrawal_days"])
        )
        retention_ratio = (
            max(0.0, final_balance) / (max(0.0, final_balance) + recent_withdrawals)
            if final_balance > 0 or recent_withdrawals > 0
            else 0.0
        ) if valid_transactions else None
        reconciliation_status = str(result.get("reconciliation", {}).get("status") or "")
        reconciliation_ratio = (
            1.0 if reconciliation_status in {"RECONCILED", "MATCHED"}
            else 0.0 if reconciliation_status in {"MISMATCHED", "MISMATCH"}
            else None
        )
        supported_weight = 0.65
        ratio = 0.65 * balance_ratio
        if retention_ratio is not None:
            supported_weight += 0.2
            ratio += 0.2 * retention_ratio
        if reconciliation_ratio is not None:
            supported_weight += 0.15
            ratio += 0.15 * reconciliation_ratio
        ending_evidence.update(
            {
                "latestMonth": latest_monthly_check["month"],
                "latestVerifiedClosingBalance": final_balance,
                "closingBalanceSource": latest_monthly_check.get("source"),
                "closingBalanceSourcePage": latest_monthly_check.get("sourcePage"),
                "balanceSufficiencyRatio": round(balance_ratio, 4),
                "recentWithdrawalTotal": round(recent_withdrawals, 2),
                "balanceRetentionRatio": (
                    round(retention_ratio, 4)
                    if retention_ratio is not None
                    else None
                ),
                "reconciliationStatus": reconciliation_status or "INSUFFICIENT_DATA",
                "reconciliationRatio": reconciliation_ratio,
                "supportedWeightRatio": round(supported_weight, 4),
                "supportedMaximumPoints": round(
                    float(weights["ending_balance"]) * supported_weight, 2
                ),
            }
        )
        ending_component = component(
            "ending_balance",
            ratio,
            "Uses the actual closing balance at the end of the latest complete three-month period, with in-period withdrawal and reconciliation evidence. Earlier monthly balances are used only to detect conflicts.",
            ending_evidence,
        )
    else:
        ending_component = component(
            "ending_balance",
            None,
            (
                "INSUFFICIENT_DATA: the latest month's verified closing balance is missing, conflicting, or unavailable."
                if not latest_closing_verified
                else "INSUFFICIENT_DATA: a verified USD conversion is unavailable for this statement currency."
            ),
            ending_evidence,
        )

    opening = result.get("periodOpeningBalance")
    starting_balance_ratio = (
        max(0.0, min(1.0, opening / required_balance))
        if opening is not None
        and start is not None
        and not result.get("periodOpeningBalanceAmbiguous")
        and required_balance > 0
        and scoring_currency == "USD"
        else None
    )
    funding_source = normalized_fields.get("initialFundingSource")
    funding_evidence = normalized_fields.get("initialFundingSourceEvidence")
    if isinstance(funding_source, dict):
        funding_evidence = funding_source.get("evidence") or funding_evidence
        funding_source = funding_source.get("source") or funding_source.get("description")
    funding_source_available = bool(
        str(funding_source or "").strip() and str(funding_evidence or "").strip()
    )
    starting_supported_weight = 0.0
    starting_ratio = None
    if starting_balance_ratio is not None:
        starting_supported_weight = 0.8
        starting_ratio = 0.8 * starting_balance_ratio
        if funding_source_available:
            starting_supported_weight += 0.2
            starting_ratio += 0.2
    account_opening_date = _date(
        normalized_fields.get("accountOpeningDate")
        or normalized_fields.get("account_opening_date")
    )
    starting_component = component(
        "starting_balance",
        starting_ratio,
        (
            "Based on the actual opening balance at the beginning of the latest complete three-month period and the documented initial-funding evidence. "
            "Monthly balances are not averaged into this category."
            if starting_ratio is not None
            else "INSUFFICIENT_DATA: statement opening balance or start date is unavailable."
        ),
        {
            "statementStartDate": start.isoformat() if start else None,
            "openingBalance": opening,
            "requiredMinimum": required_balance,
            "scoringEndDate": end.isoformat() if end else None,
            "openingBalanceEvidence": result.get("periodOpeningBalanceEvidence") or [],
            "openingBalanceConflict": bool(
                result.get("periodOpeningBalanceAmbiguous")
            ),
            "initialFundingSource": funding_source if funding_source_available else None,
            "initialFundingSourceEvidence": funding_evidence if funding_source_available else None,
            "supportedWeightRatio": round(starting_supported_weight, 4),
            "supportedMaximumPoints": round(
                float(weights["starting_balance"]) * starting_supported_weight, 2
            ),
            "accountOpeningDate": account_opening_date.isoformat() if account_opening_date else None,
            "accountAgeStatus": "AVAILABLE" if account_opening_date else "INSUFFICIENT_DATA",
        },
    )

    total_rows = len(period_transactions)
    valid_history_transactions = [
        item
        for item in period_transactions
        if isinstance(item, dict)
        and item.get("extraction_status") == "VALID"
        and item.get("date") is not None
        and (not start or item["date"] >= start.isoformat())
        and (not end or item["date"] <= end.isoformat())
        and item.get("amount") is not None
        and item.get("direction") in {"credit", "debit"}
        and item.get("currency") == result.get("accountInformation", {}).get("currency")
    ]
    complete_rows = len(valid_history_transactions)
    history_ratio = complete_rows / total_rows if total_rows else None
    reconciliation_status = str(
        result.get("reconciliation", {}).get("status") or ""
    ).upper()
    period_transaction_indices = {
        index
        for index, item in enumerate(transactions)
        if isinstance(item, dict) and transaction_in_period(item)
    }
    duplicate_count = sum(
        duplicate.get("transactionIndex") in period_transaction_indices
        for duplicate in result.get("duplicateTransactions") or []
    )
    if history_ratio is not None:
        if reconciliation_status in {"MISMATCHED", "MISMATCH"}:
            history_ratio *= 0.5
        if duplicate_count:
            history_ratio *= max(
                0.0,
                1.0 - duplicate_count / total_rows,
            )

    review_map = transaction_source_reviews or {}
    source_reviews: dict[str, dict[str, Any]] = {}
    status_aliases = {
        "SOURCE_CONFIRMED": "CONFIRMED",
        "SOURCE_UNVERIFIED": "UNVERIFIED",
    }
    for key, review in review_map.items():
        if not isinstance(review, dict):
            continue
        status = str(review.get("status") or "PENDING_REVIEW").upper()
        status = status_aliases.get(status, status)
        if status in {"CONFIRMED", "UNVERIFIED", "PENDING_REVIEW"}:
            source_reviews[str(key)] = {**review, "status": status}
    risk_indicators: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for flag in result.get("riskFlags", []):
        index = flag.get("transactionIndex")
        if flag.get("code") not in _SOURCE_REVIEW_FLAG_CODES or not isinstance(index, int):
            continue
        if (
            index >= len(transactions)
            or not isinstance(transactions[index], dict)
            or not transaction_in_period(transactions[index])
        ):
            continue
        risk_indicators[index].append(
            {"code": flag["code"], "severity": flag.get("severity"), "reason": flag.get("reason")}
        )
    threshold_usd = float(policy.get("staff_review_threshold_usd", 1000))
    exchange_rates = policy.get("staff_review_exchange_rates_to_usd") or {"USD": 1.0}
    if not isinstance(exchange_rates, dict):
        exchange_rates = {"USD": 1.0}
    default_penalty = SOURCE_UNVERIFIED_PENALTY_POINTS
    minimum_balance_threshold = float(
        policy["minimum_monthly_closing_balance_usd"]
    )

    def transaction_balance_evidence(
        transaction: dict[str, Any],
    ) -> dict[str, Any] | None:
        balance_value = next(
            (
                transaction.get(key)
                for key in (
                    "running_balance",
                    "runningBalance",
                    "balance",
                    "availableBalance",
                    "available_balance",
                    "ledgerBalance",
                    "ledger_balance",
                )
                if transaction.get(key) not in (None, "")
            ),
            None,
        )
        amount = _number(balance_value)
        if amount is None:
            return None
        currency = _currency(
            transaction.get("balanceCurrency")
            or transaction.get("balance_currency")
            or transaction.get("currency")
            or scoring_currency
        )
        rate = _transaction_exchange_rate_to_usd(currency, exchange_rates)
        if rate is None:
            return None
        return {
            "amount": amount,
            "currency": currency,
            "amountUsd": round(amount * rate, 2),
        }

    review_candidates: dict[int, dict[str, Any]] = {}
    for index, transaction in enumerate(transactions):
        if not isinstance(transaction, dict):
            continue
        tx_date = transaction_date(transaction)
        if tx_date is None or not start or not end or not start <= tx_date <= end:
            continue
        amount = _number(transaction.get("amount"))
        currency = _currency(transaction.get("currency"))
        rate = _transaction_exchange_rate_to_usd(currency, exchange_rates)
        if amount is None or rate is None:
            continue
        amount_usd = round(abs(amount) * rate, 2)
        balance_evidence = transaction_balance_evidence(transaction)
        if (
            amount_usd < threshold_usd
            and not risk_indicators.get(index)
            and str(index) not in source_reviews
        ):
            continue
        review_candidates[index] = {
            "transactionIndex": index,
            "date": transaction.get("date"),
            "amount": abs(amount),
            "currency": currency,
            "amountUsd": amount_usd,
            "balance": (
                balance_evidence["amount"]
                if balance_evidence is not None
                else None
            ),
            "balanceCurrency": (
                balance_evidence["currency"] if balance_evidence is not None else None
            ),
            "balanceUsd": (
                balance_evidence["amountUsd"] if balance_evidence is not None else None
            ),
            "exchangeRateToUsd": rate,
            "staffReviewThresholdUsd": threshold_usd,
            "requiresStaffReview": True,
            "direction": transaction.get("direction"),
            "description": transaction.get("description"),
            "sender": transaction.get("sender"),
            "receiver": transaction.get("receiver"),
            "counterparty": transaction.get("counterparty"),
            "sourceEvidence": transaction.get("evidence") or transaction.get("sourceText"),
            "page": transaction.get("page"),
            "reference": _transaction_reference(transaction),
            "riskIndicators": risk_indicators.get(index, []),
        }
    for index, candidate in review_candidates.items():
        review = source_reviews.get(str(index), {})
        current_identity = _transaction_identity(transactions[index])
        if review.get("transactionIdentity") and review["transactionIdentity"] != current_identity:
            review = {}
        candidate.update(
            {
                "status": review.get("status", "PENDING_REVIEW"),
                "reviewStatus": {
                    "CONFIRMED": "SOURCE_CONFIRMED",
                    "UNVERIFIED": "SOURCE_UNVERIFIED",
                }.get(review.get("status", "PENDING_REVIEW"), "PENDING_REVIEW"),
                "sourceCategory": review.get("sourceCategory"),
                "sourceCategoryLabel": review.get("sourceCategoryLabel"),
                "reviewerId": review.get("reviewerId"),
                "reviewedAt": review.get("reviewedAt"),
                "evidenceReferences": review.get("evidenceReferences", []),
                "reviewNotes": review.get("notes"),
                "reviewHistory": review.get("decisionHistory", []),
                "requestedEvidence": review.get("requestedEvidence"),
            }
        )

    scoring_document_id = str(
        document_id
        or normalized_fields.get("documentId")
        or normalized_fields.get("document_id")
        or ""
    )
    dedup_document_id = scoring_document_id or "UNSPECIFIED_DOCUMENT"
    duplicate_aliases = {
        int(item["transactionIndex"]): int(item["duplicateOfIndex"])
        for item in result.get("duplicateTransactions") or []
        if isinstance(item, dict)
        and isinstance(item.get("transactionIndex"), int)
        and isinstance(item.get("duplicateOfIndex"), int)
    }

    def transaction_identifier(index: int, transaction: dict[str, Any]) -> str:
        reference = _transaction_reference(transaction)
        if reference:
            return f"reference:{reference}"
        explicit_id = (
            transaction.get("transactionId")
            or transaction.get("transaction_id")
            or transaction.get("id")
        )
        if explicit_id is not None and str(explicit_id).strip():
            return f"transaction:{str(explicit_id).strip()}"
        canonical_index = index
        visited: set[int] = set()
        while canonical_index in duplicate_aliases and canonical_index not in visited:
            visited.add(canonical_index)
            canonical_index = duplicate_aliases[canonical_index]
        return f"transaction-index:{canonical_index}"

    scoring_flags_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_scoring_flag(
        flag_type: str,
        component_key: str,
        transaction_id: str,
        reason: str,
        deduction_type: str,
        configured_deduction: float,
        **details: Any,
    ) -> None:
        deduplication_key = (
            dedup_document_id,
            transaction_id,
            flag_type,
        )
        if deduplication_key in scoring_flags_by_key:
            return
        scoring_flags_by_key[deduplication_key] = {
            "documentId": scoring_document_id or None,
            "transactionId": transaction_id,
            "flagType": flag_type,
            "componentKey": component_key,
            "reason": reason,
            "manualReviewRequired": True,
            "deductionType": deduction_type,
            "configuredDeduction": configured_deduction,
            "deductionPoints": 0.0,
            "deductionStatus": "PENDING_CALCULATION",
            **details,
        }

    money_in_threshold = float(policy["money_in_flag_threshold_usd"])
    money_out_threshold = float(policy["money_out_flag_threshold_usd"])
    valid_history_ids = {id(item) for item in valid_history_transactions}
    for index, transaction in enumerate(transactions):
        if not isinstance(transaction, dict) or id(transaction) not in valid_history_ids:
            continue
        amount = _number(transaction.get("amount"))
        currency = _currency(transaction.get("currency") or scoring_currency)
        rate = _transaction_exchange_rate_to_usd(currency, exchange_rates)
        direction = transaction.get("direction")
        if amount is None or rate is None or direction not in {"credit", "debit"}:
            continue
        amount_usd = round(abs(amount) * rate, 2)
        transaction_id = transaction_identifier(index, transaction)
        if direction == "credit" and amount_usd >= money_in_threshold:
            add_scoring_flag(
                "MONEY_IN_THRESHOLD",
                "transaction_history",
                transaction_id,
                f"Money In is at least USD {money_in_threshold:,.2f}.",
                "POINTS",
                0,
                transactionIndex=index,
                date=transaction.get("date"),
                amount=abs(amount),
                currency=currency,
                amountUsd=amount_usd,
                direction=direction,
                thresholdUsd=money_in_threshold,
                referenceNumber=_transaction_reference(transaction),
                transactionReference=_transaction_reference(transaction),
            )
        elif direction == "debit" and amount_usd >= money_out_threshold:
            add_scoring_flag(
                "MONEY_OUT_THRESHOLD",
                "transaction_history",
                transaction_id,
                f"Money Out is at least USD {money_out_threshold:,.2f}.",
                "POINTS",
                0,
                transactionIndex=index,
                date=transaction.get("date"),
                amount=abs(amount),
                currency=currency,
                amountUsd=amount_usd,
                direction=direction,
                thresholdUsd=money_out_threshold,
                referenceNumber=_transaction_reference(transaction),
                transactionReference=_transaction_reference(transaction),
            )

    for index, transaction in enumerate(transactions):
        if (
            not isinstance(transaction, dict)
            or not transaction_in_period(transaction)
        ):
            continue
        balance_evidence = transaction_balance_evidence(transaction)
        if (
            balance_evidence is None
            or balance_evidence["amountUsd"] >= minimum_balance_threshold
        ):
            continue
        transaction_id = transaction_identifier(index, transaction)
        transaction_amount = _number(transaction.get("amount"))
        transaction_currency = _currency(
            transaction.get("currency") or scoring_currency
        )
        transaction_rate = _transaction_exchange_rate_to_usd(
            transaction_currency, exchange_rates
        )
        add_scoring_flag(
            "TRANSACTION_BALANCE_BELOW_MINIMUM",
            "ending_balance",
            transaction_id,
            f"The transaction balance is below USD {minimum_balance_threshold:,.2f}.",
            "POINTS",
            0,
            transactionIndex=index,
            date=transaction.get("date"),
            amount=balance_evidence["amount"],
            currency=balance_evidence["currency"],
            amountUsd=balance_evidence["amountUsd"],
            transactionAmount=(
                abs(transaction_amount) if transaction_amount is not None else None
            ),
            transactionAmountUsd=(
                round(abs(transaction_amount) * transaction_rate, 2)
                if transaction_amount is not None and transaction_rate is not None
                else None
            ),
            direction=transaction.get("direction"),
            description=transaction.get("description"),
            page=transaction.get("page"),
            thresholdUsd=minimum_balance_threshold,
            referenceNumber=_transaction_reference(transaction),
            transactionReference=_transaction_reference(transaction),
            balanceUsd=balance_evidence["amountUsd"],
        )

    for check in result.get("monthlyBalanceChecks") or []:
        if (
            not isinstance(check, dict)
            or check.get("minimumBalanceStatus") != "BELOW_MINIMUM"
            or check.get("currency") != "USD"
        ):
            continue
        month = str(check.get("month") or "")
        source_transaction = check.get("sourceTransaction")
        transaction_id = (
            str(source_transaction)
            if source_transaction is not None
            else f"monthly-close:{month}"
        )
        add_scoring_flag(
            "MONTHLY_CLOSING_BALANCE_BELOW_MINIMUM",
            "ending_balance",
            transaction_id,
            f"The {month} monthly closing balance is below USD {required_balance:,.2f}.",
            "POINTS",
            0,
            month=month,
            amount=check.get("closingBalance"),
            currency=check.get("currency"),
            thresholdUsd=required_balance,
            shortfall=check.get("shortfall"),
            sourcePage=check.get("sourcePage"),
            sourceTransaction=source_transaction,
            sourceDate=check.get("sourceDate"),
        )

    for index, candidate in sorted(review_candidates.items()):
        if candidate.get("status") != "UNVERIFIED":
            continue
        transaction = transactions[index]
        add_scoring_flag(
            "SOURCE_OF_FUNDS_UNCONFIRMED",
            "transaction_history",
            transaction_identifier(index, transaction),
            "The transaction source has not been confirmed.",
            "POINTS",
            default_penalty,
            transactionIndex=index,
            date=candidate.get("date"),
            amount=candidate.get("amount"),
            currency=candidate.get("currency"),
            direction=candidate.get("direction"),
            amountUsd=candidate.get("amountUsd"),
            referenceNumber=candidate.get("reference"),
            transactionReference=_transaction_reference(transaction),
            sourceStatus=candidate.get("status"),
            reviewerId=candidate.get("reviewerId"),
            reviewedAt=candidate.get("reviewedAt"),
            reviewNotes=candidate.get("reviewNotes"),
        )

    scoring_flags = list(scoring_flags_by_key.values())

    unverified_reviews = [
        item
        for item in review_candidates.values()
        if item["status"] == "UNVERIFIED"
    ]
    monthly_activity: dict[str, dict[str, Any]] = {}
    for item in valid_history_transactions:
        tx_date = transaction_date(item)
        if tx_date is None:
            continue
        month = tx_date.strftime("%Y-%m")
        activity = monthly_activity.setdefault(
            month,
            {"month": month, "totalDeposits": 0.0, "totalWithdrawals": 0.0,
             "incomingCount": 0, "outgoingCount": 0, "transactionCount": 0},
        )
        amount = abs(float(item["amount"]))
        direction = item["direction"]
        if direction == "credit":
            activity["totalDeposits"] += amount
            activity["incomingCount"] += 1
        else:
            activity["totalWithdrawals"] += amount
            activity["outgoingCount"] += 1
        activity["transactionCount"] += 1
    base_monthly_summary = {
        str(item.get("month")): item
        for item in (result.get("transactionSummary") or {}).get("monthly", [])
        if isinstance(item, dict) and item.get("month")
    }
    monthly_checks_by_month = {
        str(item.get("month")): item
        for item in result.get("monthlyBalanceChecks") or []
        if isinstance(item, dict) and item.get("month")
    }
    monthly_activity_rows = []
    for month in sorted(evaluated_months | set(monthly_activity)):
        activity = monthly_activity.get(
            month,
            {
                "totalDeposits": 0.0,
                "totalWithdrawals": 0.0,
                "incomingCount": 0,
                "outgoingCount": 0,
                "transactionCount": 0,
            },
        )
        base_summary = base_monthly_summary.get(month, {})
        check = monthly_checks_by_month.get(month, {})
        closing_balance = (
            check.get("closingBalance")
            if check.get("closingBalance") is not None
            else base_summary.get("closingBalance")
        )
        monthly_activity_rows.append(
            {
                "month": month,
                "totalDeposits": round(activity["totalDeposits"], 2),
                "totalWithdrawals": round(activity["totalWithdrawals"], 2),
                "incomingCount": activity["incomingCount"],
                "outgoingCount": activity["outgoingCount"],
                "transactionCount": activity["transactionCount"],
                "closingBalance": closing_balance,
                "currency": check.get("currency")
                or base_summary.get("currency")
                or scoring_currency,
                "minimumRequired": (
                    check.get("minimumRequired")
                    if check.get("minimumRequired") is not None
                    else base_summary.get("minimumRequired")
                ),
                "differenceFromMinimum": (
                    check.get("differenceFromMinimum")
                    if check.get("differenceFromMinimum") is not None
                    else base_summary.get("differenceFromMinimum")
                ),
                "shortfall": (
                    check.get("shortfall")
                    if check.get("shortfall") is not None
                    else base_summary.get("shortfall")
                ),
                "minimumBalanceStatus": check.get("minimumBalanceStatus")
                or base_summary.get("minimumBalanceStatus")
                or "INSUFFICIENT_DATA",
                "closingBalanceEvidenceAvailable": closing_balance is not None,
                "closingBalanceStatus": check.get("status")
                or ("AVAILABLE" if closing_balance is not None else "INSUFFICIENT_DATA"),
                "closingBalanceSource": check.get("source")
                or base_summary.get("closingBalanceSource"),
                "sourcePage": check.get("sourcePage")
                or base_summary.get("sourcePage"),
                "sourceTransaction": check.get("sourceTransaction")
                or base_summary.get("sourceTransaction"),
                "sourceDate": check.get("sourceDate")
                or base_summary.get("sourceDate"),
            }
        )
    period_money_in = round(
        sum(
            abs(float(item["amount"]))
            for item in valid_history_transactions
            if item["direction"] == "credit"
        ),
        2,
    )
    period_money_out = round(
        sum(
            abs(float(item["amount"]))
            for item in valid_history_transactions
            if item["direction"] == "debit"
        ),
        2,
    )
    period_recurring_candidates = [
        candidate
        for candidate in result.get("sourceOfFundsAnalysis", {}).get(
            "recurringIncomeCandidates", []
        )
        if any(
            index in period_transaction_indices
            for index in candidate.get("transactionIndexes", [])
        )
    ]
    result["transactionSummary"] = {
        **(result.get("transactionSummary") or {}),
        "totalDeposits": period_money_in,
        "totalWithdrawals": period_money_out,
        "incomingCount": sum(row["incomingCount"] for row in monthly_activity_rows),
        "outgoingCount": sum(row["outgoingCount"] for row in monthly_activity_rows),
        "monthly": monthly_activity_rows,
    }
    history_component = component(
        "transaction_history",
        history_ratio,
        "Based on in-period transaction completeness, money-in/money-out reconciliation, duplicate consistency, and saved source-review outcomes. Threshold flags are informational; only an unverified source deducts points.",
        {
            "statementStartDate": start.isoformat() if start else None,
            "statementEndDate": end.isoformat() if end else None,
            "monthsEvaluated": sorted(evaluated_months),
            "validTransactionCount": complete_rows,
            "transactionCount": total_rows,
            "duplicateTransactionCount": duplicate_count,
            "moneyIn": period_money_in,
            "moneyOut": period_money_out,
            "reconciliationStatus": reconciliation_status or "INSUFFICIENT_DATA",
            "sourceEvidenceTransactionCount": sum(
                bool(
                    item.get("sourceText")
                    or item.get("source_text")
                    or item.get("evidence")
                    or item.get("sourceEvidence")
                )
                for item in period_transactions
                if isinstance(item, dict)
            ),
            "depositCount": sum(item.get("direction") == "credit" for item in valid_history_transactions),
            "withdrawalCount": sum(item.get("direction") == "debit" for item in valid_history_transactions),
            "transferCount": sum(
                any(term in str(item.get("description") or "").casefold() for term in ("transfer", "wire", "remittance"))
                for item in valid_history_transactions
            ),
            "recurringIncomeCandidates": len(period_recurring_candidates),
            "monthlyActivity": monthly_activity_rows,
            "sourceReviewCandidateCount": len(review_candidates),
            "unverifiedSourceCount": len(unverified_reviews),
        },
    )

    coverage_days = (end - start).days + 1 if start and end and end >= start else None
    transaction_dates = [
        _date(item.get("date")) for item in valid_history_transactions
    ]
    dated_in_period = [
        item for item in transaction_dates
        if item and (not start or item >= start) and (not end or item <= end)
    ]
    covered_months = {
        item.strftime("%Y-%m") for item in dated_in_period
    }.intersection(evaluated_months)
    missing_activity_months = sorted(evaluated_months - covered_months)
    coverage_ratio = (
        min(1.0, coverage_days / period_days)
        if coverage_days is not None and period_days > 0
        else None
    )
    continuity_ratio = (
        len(covered_months) / len(evaluated_months)
        if evaluated_months
        else None
    )
    period_ratio = (
        1.0
        if dated_in_period and statement_period.get("completeThreeMonths")
        else None
    )
    period_component = component(
        "transaction_period",
        period_ratio,
        "Based on complete three-month statement coverage, supported by dated transactions within the same period.",
        {
            "monthsEvaluated": sorted(evaluated_months),
            "coverageRatio": coverage_ratio,
            "statementStartDate": start.isoformat() if start else None,
            "statementEndDate": end.isoformat() if end else None,
            "coverageDays": coverage_days,
            "minimumRequiredDays": period_days,
            "transactionDateCount": len(dated_in_period),
            "monthsCovered": sorted(covered_months),
            "monthsMissingTransactionActivity": missing_activity_months,
            "continuityRatio": continuity_ratio,
            "completeThreeMonthCoverage": bool(
                statement_period.get("completeThreeMonths")
            ),
            "firstTransactionDate": min(dated_in_period).isoformat() if dated_in_period else None,
            "lastTransactionDate": max(dated_in_period).isoformat() if dated_in_period else None,
            "historicalPatternStatus": result["historicalPatternAssessment"]["status"],
            "historicalPatternAssessment": result["historicalPatternAssessment"],
        },
    )

    components = {
        "ending_balance": ending_component,
        "starting_balance": starting_component,
        "transaction_history": history_component,
        "transaction_period": period_component,
    }
    for item in components.values():
        evidence_points = item.get("points")
        item["evidenceBasedPoints"] = evidence_points
        item["basePoints"] = item["maximum"] if item.get("available") else None
        item["points"] = item["maximum"] if item.get("available") else None
        item["deductionPoints"] = 0.0
        item["flagDeductions"] = []

    history_deduction_limit = min(
        float(policy["maximum_transaction_history_penalty"]),
        float(weights["transaction_history"]),
    )
    history_deduction_total = 0.0
    for flag in scoring_flags:
        if flag["flagType"] != "SOURCE_OF_FUNDS_UNCONFIRMED":
            flag["deductionStatus"] = "INFORMATIONAL_ONLY"
            continue
        component_item = components[flag["componentKey"]]
        if not component_item.get("available") or component_item.get("points") is None:
            flag["deductionStatus"] = "COMPONENT_UNAVAILABLE"
            continue
        maximum = float(component_item["maximum"])
        configured_deduction = max(0.0, float(flag["configuredDeduction"]))
        requested_deduction = (
            maximum * configured_deduction / 100
            if flag["deductionType"] == "PERCENTAGE"
            else configured_deduction
        )
        remaining_deduction_capacity = float(component_item["points"])
        if flag["componentKey"] == "transaction_history":
            remaining_deduction_capacity = min(
                remaining_deduction_capacity,
                max(0.0, history_deduction_limit - history_deduction_total),
            )
        applied_deduction = round(
            min(requested_deduction, remaining_deduction_capacity), 2
        )
        component_item["points"] = round(
            max(0.0, float(component_item["points"]) - applied_deduction), 2
        )
        component_item["deductionPoints"] = round(
            float(component_item["deductionPoints"]) + applied_deduction, 2
        )
        component_item["flagDeductions"].append(flag)
        flag["deductionPoints"] = applied_deduction
        flag["deductionStatus"] = (
            "APPLIED"
            if applied_deduction > 0
            else "CAPPED"
            if requested_deduction > 0
            else "NO_DEDUCTION_CONFIGURED"
        )
        if flag["componentKey"] == "transaction_history":
            history_deduction_total = round(
                history_deduction_total + applied_deduction, 2
            )

    transaction_flag_types = {
        "MONEY_IN_THRESHOLD",
        "MONEY_OUT_THRESHOLD",
        "TRANSACTION_BALANCE_BELOW_MINIMUM",
    }
    for index, candidate in review_candidates.items():
        candidate_flags = [
            flag for flag in scoring_flags if flag.get("transactionIndex") == index
        ]
        candidate["transactionFlags"] = [
            flag
            for flag in candidate_flags
            if flag["flagType"] in transaction_flag_types
        ]
        source_deduction = next(
            (
                flag
                for flag in candidate_flags
                if flag["flagType"] == "SOURCE_OF_FUNDS_UNCONFIRMED"
            ),
            None,
        )
        candidate["sourceDeduction"] = (
            {
                "points": source_deduction["deductionPoints"],
                "status": source_deduction["deductionStatus"],
            }
            if source_deduction
            else {
                "points": 0.0,
                "status": (
                    "SOURCE_CONFIRMED"
                    if candidate["status"] == "CONFIRMED"
                    else "PENDING_REVIEW"
                    if candidate["status"] == "PENDING_REVIEW"
                    else "NO_DEDUCTION"
                ),
            }
        )
        candidate["penaltyPoints"] = candidate["sourceDeduction"]["points"]
        candidate["appliedDeductions"] = [
            {
                "flagType": flag["flagType"],
                "componentKey": flag["componentKey"],
                "points": flag["deductionPoints"],
                "status": flag["deductionStatus"],
            }
            for flag in candidate_flags
            if flag["deductionPoints"] > 0
        ]

    history_component["evidence"]["penaltyPoints"] = history_deduction_total
    history_component["evidence"]["flags"] = [
        flag
        for flag in scoring_flags
        if flag["componentKey"] == "transaction_history"
    ]
    history_component["evidence"]["deductions"] = [
        flag
        for flag in scoring_flags
        if flag["componentKey"] == "transaction_history"
        and flag["deductionPoints"] > 0
    ]
    score_result = calculate_bank_statement_score(
        components,
        weights=weights,
        ocr_complete=ocr_complete,
    )
    components = score_result["components"]
    def unique_review_key(item: dict[str, Any]) -> str:
        return str(
            item.get("transactionIdentity")
            or item.get("transactionReference")
            or f"transaction-index:{item.get('transactionIndex')}"
        )
    pending_count = len({
        unique_review_key(item)
        for item in review_candidates.values()
        if item["status"] == "PENDING_REVIEW"
    })
    unverified_transaction_count = sum(
        1
        for flag in scoring_flags
        if flag["flagType"] == "SOURCE_OF_FUNDS_UNCONFIRMED"
        and flag.get("sourceStatus") == "UNVERIFIED"
    )
    confirmed_transaction_count = len({
        unique_review_key(item)
        for item in review_candidates.values()
        if item["status"] == "CONFIRMED"
    })
    total_penalty_points = round(
        unverified_transaction_count * SOURCE_UNVERIFIED_PENALTY_POINTS,
        2,
    )
    result["bankStatementScore"] = {
        **score_result,
        "statementPeriod": statement_period,
        "assessment": "COMPUTED" if score_result["available"] else "INSUFFICIENT_DATA",
        "components": components,
        "confirmedPoints": None,
        "confirmedStatus": "NOT_YET_CONFIRMED",
        "pendingTransactionReviews": pending_count,
        "unverifiedTransactionCount": unverified_transaction_count,
        "confirmedTransactionCount": confirmed_transaction_count,
        "totalPenaltyPoints": total_penalty_points,
        "sourceReviewDeductionAudit": {
            "unverifiedTransactionCount": unverified_transaction_count,
            "confirmedTransactionCount": confirmed_transaction_count,
            "pendingTransactionCount": pending_count,
            "deductionPerTransaction": SOURCE_UNVERIFIED_PENALTY_POINTS,
            "totalDeductionPoints": total_penalty_points,
            "transactionHistoryBasePoints": components["transaction_history"].get("basePoints"),
            "transactionHistoryDeductedPoints": total_penalty_points,
            "transactionHistoryCurrentPoints": components["transaction_history"].get("points"),
        },
        "deductions": [
            flag for flag in scoring_flags if flag["deductionPoints"] > 0
        ],
        "triggeredFlags": scoring_flags,
        "transactionReviews": list(review_candidates.values()),
        "transactionReviewRequirements": {
            "sourceEvidenceRequiredForConfirmation": bool(
                policy["require_source_evidence_for_confirmation"]
            ),
            "minimumNotesLength": int(policy["minimum_source_review_notes_length"]),
            "staffReviewThresholdUsd": threshold_usd,
            "moneyInFlagThresholdUsd": money_in_threshold,
            "moneyOutFlagThresholdUsd": money_out_threshold,
            "minimumMonthlyClosingBalanceUsd": minimum_balance_threshold,
            "exchangeRatesToUsd": exchange_rates,
            "unverifiedSourcePenaltyPoints": default_penalty,
            "deductPendingSourceReview": False,
            "moneyInFlagDeductionPercent": 0.0,
            "moneyOutFlagDeductionPercent": 0.0,
            "monthlyLowBalanceFlagDeductionPercent": 0.0,
            "maximumTransactionHistoryPenalty": float(
                policy["maximum_transaction_history_penalty"]
            ),
            "maximumBankScore": score_result["maximum"],
        },
        "formula": (
            "Ending Balance 22.50 + Starting Balance 6.75 + "
            "Transaction History 9.00 + Transaction Period 6.75 = 45; "
            "only a completed unverified-source review deducts 0.50 points."
        ),
    }
    manual_review_required = bool(scoring_flags) or any(
        flag.get("manualReviewRequired") for flag in result.get("riskFlags") or []
    )
    result["bankStatementScore"]["manualReviewRequired"] = manual_review_required
    result["bankStatementScore"]["manualReviewStatus"] = (
        "NEEDS_MANUAL_REVIEW" if manual_review_required else "NOT_REQUIRED"
    )
    result["scoringFlags"] = scoring_flags
    result["manualReviewRequired"] = manual_review_required
    result["manualReviewStatus"] = (
        "NEEDS_MANUAL_REVIEW" if manual_review_required else "NOT_REQUIRED"
    )
    result["transactionSourceReviews"] = list(review_candidates.values())
    result["bankStatementScore"]["evidenceCoveragePercentage"] = round(
        score_result["availableMaximumPoints"] / score_result["maximum"] * 100,
        2,
    ) if score_result["maximum"] else 0.0
    if not ocr_complete:
        for item in components.values():
            item["points"] = None
            item["available"] = False
            item["evidence_ratio"] = None
        result["bankStatementScore"]["points"] = None
        result["bankStatementScore"]["percentage"] = None
        result["bankStatementScore"]["confirmedPoints"] = None
        result["bankStatementScore"]["available"] = False
        result["bankStatementScore"]["assessment"] = "INSUFFICIENT_DATA"
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    number, _, _ = parse_amount(value)
    return number


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
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


def _month_key(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}", value.strip()):
        return value.strip()
    parsed = _date(value)
    return parsed.strftime("%Y-%m") if parsed else None


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1


def _currency(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"$", "US DOLLAR", "US DOLLARS"}:
        return "USD"
    return normalized


def _transaction_exchange_rate_to_usd(
    currency: str, exchange_rates: dict[str, Any]
) -> float | None:
    if currency == "USD":
        return 1.0
    rate = exchange_rates.get(currency)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return None
    normalized_rate = float(rate)
    return normalized_rate if math.isfinite(normalized_rate) and normalized_rate > 0 else None


def _direction(transaction: dict[str, Any]) -> str | None:
    value = str(transaction.get("direction") or transaction.get("type") or "").strip().casefold()
    if value in {"credit", "deposit", "income", "salary", "cr"}:
        return "credit"
    if value in {"debit", "withdrawal", "expense", "payment", "dr"}:
        return "debit"
    amount = _number(transaction.get("amount"))
    if amount is not None and amount < 0:
        return "debit"
    return None


def _page_duplicates(raw_text: str) -> list[dict[str, Any]]:
    pages = re.split(r"(?m)^--- PAGE (\d+)\s*$", raw_text or "")
    seen: dict[str, int] = {}
    duplicates = []
    for index in range(1, len(pages), 2):
        page_number = int(pages[index])
        content = re.sub(r"[^a-z0-9]+", "", pages[index + 1].casefold())
        if len(content) < 40:
            continue
        previous = seen.get(content)
        if previous is not None:
            duplicates.append(
                {"page": page_number, "duplicate_of_page": previous}
            )
        else:
            seen[content] = page_number
    return duplicates


def _analyze_bank_statement_base(
    fields: dict[str, Any],
    *,
    raw_text: str = "",
    applicant_name: str = "",
    config: dict[str, Any] | None = None,
    financial_config: dict[str, Any] | None = None,
    ocr_complete: bool = True,
) -> dict[str, Any]:
    """Analyze extracted evidence without asserting that the statement is authentic."""
    policy = config or DEFAULT_BANK_STATEMENT_SCORING
    threshold = float(
        policy.get("minimum_monthly_closing_balance_usd", 4000)
    )
    recent_deposit_days = int(policy.get("recent_large_deposit_days", 30))
    rapid_days = int(policy.get("rapid_funds_outflow_days", 7))
    rapid_ratio = float(policy.get("rapid_funds_outflow_ratio", 0.8))
    fields = normalize_bank_fields(fields)
    source_transactions = (
        fields.get("transactionHistory")
        or fields.get("transaction_history")
        or fields.get("transactions")
        or []
    )
    normalized_result = normalize_transactions(
        source_transactions,
        default_currency=fields.get("currency") or fields.get("currency_code"),
    )
    transactions = normalized_result["transactions"]
    financial = calculate_financial_analysis(
        fields,
        transactions,
        financial_config,
    )
    period = fields.get("statementPeriod") or fields.get("statement_period") or {}
    period_start = _date(
        fields.get("statementStartDate")
        or fields.get("statement_start_date")
        or (period.get("startDate") or period.get("start_date") if isinstance(period, dict) else None)
    )
    period_end = _date(
        fields.get("statementEndDate")
        or fields.get("statement_end_date")
        or (period.get("endDate") or period.get("end_date") if isinstance(period, dict) else None)
    )
    statement_currency = _currency(fields.get("currency") or fields.get("currency_code"))

    month_keys: list[str] = []
    period_complete = False
    if period_start and period_end and period_end >= period_start:
        first_target_year, first_target_month = _shift_month(
            period_end.year, period_end.month, -2
        )
        first_target = date(first_target_year, first_target_month, 1)
        last_day = calendar.monthrange(period_end.year, period_end.month)[1]
        month_keys = [
            f"{_shift_month(period_end.year, period_end.month, offset)[0]:04d}-"
            f"{_shift_month(period_end.year, period_end.month, offset)[1]:02d}"
            for offset in (-2, -1, 0)
        ]
        period_complete = (
            period_start <= first_target
            and period_end.day == last_day
        )

    evidence_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    opening_evidence_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    monthly_balance_sources = (
        (
            fields.get("monthlyClosingBalances")
            or fields.get("monthly_closing_balances"),
            "extracted_monthly_closing_balance",
        ),
        (
            fields.get("monthlyBalanceHistory")
            or fields.get("monthly_balance_history")
            or fields.get("bankMonthlySummaries"),
            "extracted_monthly_balance_history",
        ),
    )
    for monthly_balances, source in monthly_balance_sources:
        if not isinstance(monthly_balances, list):
            continue
        for item in monthly_balances:
            if not isinstance(item, dict):
                continue
            key = _month_key(
                item.get("month")
                or item.get("date")
                or item.get("monthEndDate")
                or item.get("month_end_date")
            )
            opening_amount = _number(
                item.get("openingBalance")
                if item.get("openingBalance") is not None
                else item.get("opening_balance")
            )
            if key and opening_amount is not None:
                opening_evidence_by_month[key].append(
                    {
                        "amount": opening_amount,
                        "currency": _currency(
                            item.get("currency")
                            or item.get("currencyCode")
                            or item.get("currency_code")
                            or statement_currency
                        ),
                        "page": item.get("openingPage")
                        or item.get("opening_page"),
                        "sourceText": str(
                            item.get("openingSourceText")
                            or item.get("opening_source_text")
                            or ""
                        )[:500],
                        "source": "extracted_monthly_opening_balance",
                    }
                )
            amount = _number(
                item.get("closingBalance")
                if item.get("closingBalance") is not None
                else item.get("closing_balance")
                if item.get("closing_balance") is not None
                else item.get("balance")
                if item.get("balance") is not None
                else item.get("endingBalance")
                if item.get("endingBalance") is not None
                else item.get("ending_balance")
            )
            if key and amount is not None:
                evidence_by_month[key].append(
                    {
                        "amount": amount,
                        "currency": _currency(
                            item.get("currency")
                            or item.get("currencyCode")
                            or item.get("currency_code")
                            or statement_currency
                        ),
                        "date": item.get("date")
                        or item.get("monthEndDate")
                        or item.get("month_end_date"),
                        "page": item.get("closingPage")
                        or item.get("closing_page")
                        or item.get("sourcePage")
                        or item.get("source_page")
                        or item.get("page"),
                        "transaction": item.get("closingRow")
                        or item.get("closing_row")
                        or item.get("sourceTransaction")
                        or item.get("source_transaction")
                        or item.get("row")
                        or item.get("rowNumber")
                        or item.get("row_number"),
                        "sourceText": str(
                            item.get("closingSourceText")
                            or item.get("closing_source_text")
                            or item.get("sourceText")
                            or item.get("source_text")
                            or ""
                        )[:500],
                        "source": source,
                    }
                )

    transaction_month_end_evidence: dict[str, dict[str, Any]] = {}
    for transaction in transactions:
        tx_date = _date(transaction.get("date"))
        balance = transaction.get("running_balance")
        if (
            transaction.get("extraction_status") != "VALID"
            or tx_date is None
            or balance is None
            or tx_date.day != calendar.monthrange(tx_date.year, tx_date.month)[1]
        ):
            continue
        key = tx_date.strftime("%Y-%m")
        transaction_month_end_evidence[key] = {
            "amount": float(balance),
            "currency": _currency(transaction.get("currency") or statement_currency),
            "date": tx_date.isoformat(),
            "page": transaction.get("page"),
            "transaction": transaction.get("sourceRowId")
            or transaction.get("rowNumber")
            or transaction.get("row_number")
            or transaction.get("row"),
            "sourceText": str(
                transaction.get("sourceText") or transaction.get("evidence") or ""
            )[:500],
            "source": "transaction_running_balance",
        }
    for month, evidence in transaction_month_end_evidence.items():
        # The last source-order balance on month-end is the monthly close.
        evidence_by_month[month].append(evidence)

    if not statement_currency:
        explicit_currencies = {
            item["currency"]
            for entries in evidence_by_month.values()
            for item in entries
            if item["currency"]
        }
        if len(explicit_currencies) == 1:
            statement_currency = explicit_currencies.pop()
    if statement_currency:
        for candidates in evidence_by_month.values():
            for candidate in candidates:
                if not candidate["currency"]:
                    candidate["currency"] = statement_currency
        for candidates in opening_evidence_by_month.values():
            for candidate in candidates:
                if not candidate["currency"]:
                    candidate["currency"] = statement_currency

    statement_closing_balance = _number(fields.get("closingBalance"))
    if period_end and statement_closing_balance is not None:
        evidence_by_month[period_end.strftime("%Y-%m")].append(
            {
                "amount": statement_closing_balance,
                "currency": statement_currency,
                "date": period_end.isoformat(),
                "page": fields.get("closingBalancePage"),
                "sourceText": str(fields.get("closingBalanceSourceText") or "")[:500],
                "source": "extracted_statement_closing_balance",
            }
        )

    serialized_history_by_month: dict[str, dict[str, Any]] = {}
    for monthly_balances, _ in monthly_balance_sources:
        if not isinstance(monthly_balances, list):
            continue
        for item in monthly_balances:
            if not isinstance(item, dict):
                continue
            key = _month_key(
                item.get("month")
                or item.get("date")
                or item.get("monthEndDate")
                or item.get("month_end_date")
            )
            if not key:
                continue
            previous = serialized_history_by_month.get(key, {})
            opening_balance = _number(
                item.get("openingBalance")
                if item.get("openingBalance") is not None
                else item.get("opening_balance")
            )
            closing_balance = _number(
                item.get("closingBalance")
                if item.get("closingBalance") is not None
                else item.get("closing_balance")
                if item.get("closing_balance") is not None
                else item.get("balance")
                if item.get("balance") is not None
                else item.get("endingBalance")
                if item.get("endingBalance") is not None
                else item.get("ending_balance")
            )
            serialized_history_by_month[key] = {
                **previous,
                "month": key,
                "openingBalance": (
                    opening_balance
                    if opening_balance is not None
                    else previous.get("openingBalance")
                ),
                "closingBalance": (
                    closing_balance
                    if closing_balance is not None
                    else previous.get("closingBalance")
                ),
                "currency": _currency(
                    item.get("currency")
                    or item.get("currencyCode")
                    or item.get("currency_code")
                    or previous.get("currency")
                    or statement_currency
                )
                or None,
                "openingPage": item.get("openingPage")
                or item.get("opening_page")
                or previous.get("openingPage"),
                "closingPage": item.get("closingPage")
                or item.get("closing_page")
                or item.get("sourcePage")
                or item.get("source_page")
                or item.get("page")
                or previous.get("closingPage"),
                "openingSourceText": str(
                    item.get("openingSourceText")
                    or item.get("opening_source_text")
                    or previous.get("openingSourceText")
                    or ""
                )[:500],
                "closingSourceText": str(
                    item.get("closingSourceText")
                    or item.get("closing_source_text")
                    or item.get("sourceText")
                    or item.get("source_text")
                    or previous.get("closingSourceText")
                    or ""
                )[:500],
            }
    serialized_monthly_balance_history = [
        serialized_history_by_month[key]
        for key in sorted(serialized_history_by_month)
    ]

    period_opening_candidates = (
        list(opening_evidence_by_month.get(month_keys[0], []))
        if month_keys
        else []
    )
    if month_keys:
        first_month_year, first_month_number = (
            int(part) for part in month_keys[0].split("-")
        )
        previous_year, previous_month = _shift_month(
            first_month_year, first_month_number, -1
        )
        previous_month_key = f"{previous_year:04d}-{previous_month:02d}"
        previous_closings = evidence_by_month.get(previous_month_key, [])
        previous_amounts = {
            round(item["amount"], 2) for item in previous_closings
        }
        previous_currencies = {
            item["currency"]
            for item in previous_closings
            if item.get("currency")
        }
        if (
            len(previous_amounts) == 1
            and len(previous_currencies) <= 1
            and not any(
                item.get("ambiguous") for item in previous_closings
            )
        ):
            previous_close = next(iter(previous_closings))
            period_opening_candidates.append(
                {
                    "amount": previous_close["amount"],
                    "currency": previous_close["currency"],
                    "page": previous_close.get("page"),
                    "sourceText": previous_close.get("sourceText") or "",
                    "source": "previous_month_closing_balance",
                    "date": previous_close.get("date"),
                }
            )
    if period_start and month_keys:
        scoring_start = date.fromisoformat(f"{month_keys[0]}-01")
        statement_opening = _number(fields.get("openingBalance"))
        if period_start == scoring_start and statement_opening is not None:
            period_opening_candidates.append(
                {
                    "amount": statement_opening,
                    "currency": statement_currency,
                    "page": fields.get("openingBalancePage"),
                    "sourceText": str(fields.get("openingBalanceSourceText") or "")[:500],
                    "source": "extracted_statement_opening_balance",
                }
            )
    opening_amounts = {
        round(item["amount"], 2) for item in period_opening_candidates
    }
    opening_currencies = {
        item["currency"] for item in period_opening_candidates if item["currency"]
    }
    period_opening_ambiguous = (
        len(opening_amounts) > 1 or len(opening_currencies) > 1
    )
    period_opening_balance = (
        next(iter(opening_amounts))
        if len(opening_amounts) == 1 and not period_opening_ambiguous
        else None
    )

    monthly_checks = []
    if month_keys:
        for key in month_keys:
            candidates = evidence_by_month.get(key, [])
            unique_amounts = {
                round(item["amount"], 2)
                for item in candidates
            }
            unique_currencies = {item["currency"] for item in candidates}
            ambiguous = len(unique_amounts) > 1 or len(unique_currencies) > 1
            item = (
                max(
                    candidates,
                    key=lambda candidate: (
                        bool(candidate.get("page")),
                        bool(candidate.get("transaction")),
                        bool(candidate.get("sourceText")),
                        bool(candidate.get("date")),
                    ),
                )
                if candidates and not ambiguous
                else None
            )
            closing_balance = item["amount"] if item else None
            currency = item["currency"] if item else statement_currency
            status = (
                "CONFLICTING_DATA"
                if ambiguous
                else "AVAILABLE"
                if closing_balance is not None
                else "INSUFFICIENT_DATA"
            )
            minimum_balance_status = "INSUFFICIENT_DATA"
            minimum_required = None
            difference_from_minimum = None
            shortfall = None
            if not ambiguous and closing_balance is not None:
                if currency == "USD":
                    minimum_required = threshold
                    difference_from_minimum = round(
                        closing_balance - threshold, 2
                    )
                    shortfall = round(
                        max(0.0, threshold - closing_balance), 2
                    )
                    minimum_balance_status = (
                        "BELOW_MINIMUM"
                        if closing_balance < threshold
                        else "MEETS_MINIMUM"
                    )
                else:
                    minimum_balance_status = "NOT_ASSESSED"
            monthly_checks.append(
                {
                    "month": key,
                    "closingBalance": round(closing_balance, 2)
                    if closing_balance is not None
                    else None,
                    "currency": currency or None,
                    "minimumRequired": minimum_required,
                    "differenceFromMinimum": difference_from_minimum,
                    "shortfall": shortfall,
                    "minimumBalanceStatus": minimum_balance_status,
                    "status": status,
                    "evidencePurpose": "SUPPORTING_INFORMATION",
                    "source": item["source"] if item else None,
                    "sourcePage": item["page"] if item else None,
                    "sourceTransaction": item.get("transaction") if item else None,
                    "sourceDate": item.get("date") if item else None,
                    "sourceText": item["sourceText"] if item else "",
                    "ambiguous": ambiguous,
                }
            )

    latest_month_check = next(
        (
            item
            for item in reversed(monthly_checks)
            if period_end and item["month"] == period_end.strftime("%Y-%m")
        ),
        None,
    )
    if (
        not period_complete
        or latest_month_check is None
        or latest_month_check.get("ambiguous")
        or latest_month_check.get("closingBalance") is None
        or latest_month_check.get("currency") != "USD"
    ):
        financial_requirement_status = "INSUFFICIENT_DATA"
    else:
        latest_balance = float(latest_month_check["closingBalance"])
        financial_requirement_status = (
            "BELOW_MINIMUM" if latest_balance < threshold else "PASS"
        )
    minimum_balance_assessment = {
        "status": financial_requirement_status,
        "balance": (
            round(float(latest_month_check["closingBalance"]), 2)
            if latest_month_check
            and latest_month_check.get("closingBalance") is not None
            else None
        ),
        "currency": latest_month_check.get("currency") if latest_month_check else None,
        "minimum": threshold,
        "shortfall": (
            round(
                max(
                    0.0,
                    threshold - float(latest_month_check["closingBalance"]),
                ),
                2,
            )
            if financial_requirement_status == "BELOW_MINIMUM"
            and latest_month_check
            else 0.0
            if financial_requirement_status == "PASS"
            else None
        ),
    }
    flags: list[dict[str, Any]] = []

    def add_flag(
        code: str,
        reason: str,
        severity: str = "medium",
        transaction: dict[str, Any] | None = None,
        **details: Any,
    ) -> None:
        flag: dict[str, Any] = {
            "code": code,
            "reason": reason,
            "severity": severity,
            "manualReviewRequired": True,
        }
        if transaction:
            flag.update(
                {
                    "date": transaction.get("date")
                    or transaction.get("transactionDate")
                    or transaction.get("transaction_date"),
                    "amount": _number(transaction.get("amount")),
                    "currency": statement_currency or None,
                    "description": str(transaction.get("description") or "")[:160],
                    "page": transaction.get("page"),
                    "transactionIndex": transaction.get("transactionIndex"),
                    "referenceNumber": transaction.get("referenceNumber"),
                    "sender": transaction.get("sender"),
                    "receiver": transaction.get("receiver"),
                }
            )
        flag.update(details)
        flags.append(flag)

    if not period_complete:
        add_flag(
            "INCOMPLETE_STATEMENT_PERIOD",
            "The statement does not establish three complete calendar months.",
        )
    if statement_currency != "USD":
        add_flag(
            "CURRENCY_NOT_USD",
            "USD balance thresholds cannot be applied without a verified exchange rate.",
        )
    for item in monthly_checks:
        if item["ambiguous"]:
            add_flag(
                "CONFLICTING_MONTHLY_BALANCES",
                f"More than one closing balance was extracted for {item['month']}.",
                month=item["month"],
            )
    for month, candidates in opening_evidence_by_month.items():
        amounts = {round(item["amount"], 2) for item in candidates}
        currencies = {
            item["currency"] for item in candidates if item.get("currency")
        }
        if len(amounts) > 1 or len(currencies) > 1:
            add_flag(
                "CONFLICTING_MONTHLY_OPENING_BALANCES",
                f"More than one opening balance was extracted for {month}.",
                month=month,
            )
    if period_opening_ambiguous:
        add_flag(
            "CONFLICTING_PERIOD_OPENING_BALANCES",
            "Conflicting opening balances were extracted for the beginning of the complete three-month period.",
            month=month_keys[0] if month_keys else None,
        )
    monthly_history_by_month = {
        item["month"]: item for item in serialized_monthly_balance_history
    }
    reconciliation_tolerance = float(
        (financial_config or {}).get("targets", {}).get(
            "reconciliation_tolerance", 1
        )
    )
    for previous_month, next_month in zip(month_keys, month_keys[1:]):
        previous_close = monthly_history_by_month.get(previous_month, {}).get(
            "closingBalance"
        )
        next_open = monthly_history_by_month.get(next_month, {}).get(
            "openingBalance"
        )
        if (
            previous_close is not None
            and next_open is not None
            and abs(previous_close - next_open) > reconciliation_tolerance
        ):
            add_flag(
                "MONTHLY_BALANCE_CONTINUITY_MISMATCH",
                f"The {next_month} opening balance does not reconcile with the {previous_month} closing balance.",
                month=next_month,
                previousMonth=previous_month,
                previousClosingBalance=previous_close,
                openingBalance=next_open,
                tolerance=reconciliation_tolerance,
            )
    if financial_requirement_status == "BELOW_MINIMUM" and latest_month_check:
        add_flag(
            "PERIOD_END_BALANCE_BELOW_REQUIRED",
            "The closing balance at the end of the complete three-month period is below the configured USD minimum.",
            severity="low",
            closingBalance=latest_month_check["closingBalance"],
            minimumRequired=threshold,
            shortfall=minimum_balance_assessment["shortfall"],
        )

    holder_name = str(fields.get("accountHolderName") or fields.get("account_holder_name") or "")
    name_status = "NOT_ASSESSED"
    name_reason = (
        "The extracted account-holder name is retained as bank evidence and is not "
        "compared with the applicant name for financial eligibility. Legal ownership "
        "of the account is not asserted."
    )

    duplicate_pages = _page_duplicates(raw_text)
    for duplicate in duplicate_pages:
        add_flag(
            "DUPLICATE_PAGE_CANDIDATE",
            "OCR text for this page duplicates another page; inspect the original file.",
            page=duplicate["page"],
            duplicateOfPage=duplicate["duplicate_of_page"],
        )

    start_date = period_start
    end_date = period_end
    transaction_records = [
        item for item in transactions if isinstance(item, dict)
    ]
    if not transaction_records:
        add_flag(
            "TRANSACTION_HISTORY_UNAVAILABLE",
            "No transaction rows were extracted for source and reconciliation checks.",
        )
    duplicate_keys: dict[tuple[Any, ...], int] = {}
    duplicate_transactions = []
    monthly_summaries: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "deposits": 0.0,
            "withdrawals": 0.0,
            "incomingCount": 0,
            "outgoingCount": 0,
        }
    )
    source_terms = (
        "salary", "payroll", "wage", "pension", "business income", "invoice",
        "monthly income", "income", "dividend", "rental income",
    )
    source_patterns: dict[str, dict[str, Any]] = {}
    own_account_transfers: list[dict[str, Any]] = []
    parsed_transactions: list[tuple[date | None, float | None, str | None, dict[str, Any]]] = []
    for index, transaction in enumerate(transaction_records):
        tx_date = _date(transaction.get("date") or transaction.get("transactionDate"))
        amount = _number(transaction.get("amount"))
        direction = _direction(transaction)
        description = str(transaction.get("description") or "")
        month = tx_date.strftime("%Y-%m") if tx_date else None
        transaction_currency = _currency(transaction.get("currency"))
        if (
            transaction_currency
            and statement_currency
            and transaction_currency != statement_currency
        ):
            add_flag(
                "TRANSACTION_CURRENCY_MISMATCH",
                "Transaction currency differs from the statement currency; no conversion was inferred.",
                "medium",
                transaction,
            )
        if tx_date and start_date and end_date and not start_date <= tx_date <= end_date:
            add_flag(
                "TRANSACTION_OUTSIDE_STATEMENT_PERIOD",
                "Transaction date falls outside the stated period.",
                "low",
                transaction,
            )
        if tx_date and amount is not None and direction:
            summary = monthly_summaries[month]
            key = "deposits" if direction == "credit" else "withdrawals"
            count_key = "incomingCount" if direction == "credit" else "outgoingCount"
            summary[key] += abs(amount)
            summary[count_key] += 1
            if direction == "credit":
                description_folded = description.casefold()
                own_transfer = any(
                    term in description_folded
                    for term in ("own account", "self transfer", "internal transfer", "between my accounts")
                )
                if own_transfer:
                    own_account_transfers.append(
                        {
                            "transactionIndex": index,
                            "date": tx_date.isoformat() if tx_date else None,
                            "amount": round(abs(amount), 2),
                            "description": description,
                            "evidence": transaction.get("evidence") or "",
                            "note": "Description suggests an own-account transfer; verify its source evidence.",
                        }
                    )
                else:
                    labeled_income = any(
                        term in description_folded for term in source_terms
                    )
                    transfer_candidate = any(
                        term in description_folded
                        for term in ("transfer", "wire", "remittance", "standing order")
                    )
                    if labeled_income or transfer_candidate:
                        category = (
                            "salary_or_payroll"
                            if any(term in description_folded for term in ("salary", "payroll", "wage", "pension"))
                            else "business_income"
                            if any(term in description_folded for term in ("business income", "invoice"))
                            else "recurring_transfer_candidate"
                            if transfer_candidate and not labeled_income
                            else "other_labeled_income"
                        )
                        pattern = re.sub(r"\b\d+\b", " ", description_folded)
                        pattern = re.sub(r"[^\w]+", " ", pattern, flags=re.UNICODE)
                        pattern = " ".join(pattern.split()) or category
                        key = f"{category}:{pattern}"
                        source = source_patterns.setdefault(
                            key,
                            {
                                "category": category,
                                "descriptionPattern": pattern,
                                "months": set(),
                                "transactionIndexes": [],
                                "totalAmount": 0.0,
                                "evidence": [],
                            },
                        )
                        if month:
                            source["months"].add(month)
                        source["transactionIndexes"].append(index)
                        source["totalAmount"] += abs(amount)
                        if transaction.get("evidence"):
                            source["evidence"].append(str(transaction["evidence"])[:300])

        transaction_balance = transaction.get("balance")
        if transaction_balance is None:
            transaction_balance = transaction.get("running_balance")
        if transaction_balance is None:
            transaction_balance = transaction.get("runningBalance")
        fingerprint = (
            tx_date.isoformat() if tx_date else None,
            round(abs(amount), 2) if amount is not None else None,
            direction,
            re.sub(r"\s+", " ", description.casefold()).strip(),
            _number(transaction_balance),
        )
        previous_index = duplicate_keys.get(fingerprint)
        if previous_index is not None and fingerprint[3]:
            duplicate_transactions.append(
                {"transactionIndex": index, "duplicateOfIndex": previous_index}
            )
            add_flag(
                "DUPLICATE_TRANSACTION_CANDIDATE",
                "This transaction duplicates another extracted row; inspect the statement.",
                "low",
                transaction,
                duplicateOfIndex=previous_index,
            )
        else:
            duplicate_keys[fingerprint] = index
        parsed_transactions.append((tx_date, abs(amount) if amount is not None else None, direction, transaction))

    large_deposit_threshold = float(
        (financial_config or {}).get("targets", {}).get(
            "large_deposit_threshold_usd", 1000
        )
    )
    transfer_terms = ("transfer", "wire", "remittance", "cash out", "cash withdrawal")
    for tx_date, amount, direction, transaction in parsed_transactions:
        description = str(transaction.get("description") or "").casefold()
        if (
            amount is not None
            and direction == "credit"
            and statement_currency == "USD"
            and amount >= large_deposit_threshold
        ):
            explained = any(term in description for term in source_terms)
            if not explained:
                add_flag(
                    "LARGE_DEPOSIT_SOURCE_REVIEW",
                    "Large incoming deposit; confirm the source and supporting evidence. This is not a fraud finding.",
                    "medium",
                    transaction,
                )
            if tx_date and end_date and 0 <= (end_date - tx_date).days <= recent_deposit_days:
                add_flag(
                    "RECENT_LARGE_DEPOSIT",
                    "Large deposit occurred shortly before statement end; confirm its source.",
                    "medium",
                    transaction,
                )
        if (
            amount is not None
            and amount >= large_deposit_threshold
            and any(term in description for term in transfer_terms)
        ):
            add_flag(
                "TRANSFER_REVIEW",
                "Transfer transaction requires context; this is not a suspicious-activity finding.",
                "low",
                transaction,
            )

        if (
            amount is not None
            and direction in {"credit", "debit"}
            and any(term in description for term in transfer_terms)
            and not any(
                transaction.get(key)
                for key in ("sender", "receiver", "counterparty")
            )
        ):
            add_flag(
                "MISSING_COUNTERPARTY",
                "Transfer has no extracted sender or receiver information; staff source review may be needed.",
                "low",
                transaction,
            )

    unexplained_credits_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tx_date, amount, direction, transaction in parsed_transactions:
        description = str(transaction.get("description") or "").casefold()
        if (
            tx_date
            and direction == "credit"
            and amount is not None
            and not any(term in description for term in source_terms)
        ):
            unexplained_credits_by_month[tx_date.strftime("%Y-%m")].append(transaction)
    for month, deposits_in_month in unexplained_credits_by_month.items():
        if len(deposits_in_month) < 3:
            continue
        for transaction in deposits_in_month:
            add_flag(
                "FREQUENT_UNEXPLAINED_DEPOSITS",
                f"Multiple deposits in {month} lack an identifiable source description; request context without presuming wrongdoing.",
                "medium",
                transaction,
                month=month,
                monthlyUnexplainedDepositCount=len(deposits_in_month),
            )

    transfer_debits = sorted(
        [
            (tx_date, amount, transaction)
            for tx_date, amount, direction, transaction in parsed_transactions
            if tx_date
            and amount is not None
            and direction == "debit"
            and any(
                term in str(transaction.get("description") or "").casefold()
                for term in transfer_terms
            )
        ],
        key=lambda item: item[0],
    )
    credits = sorted(
        [
            (item[0], item[1])
            for item in parsed_transactions
            if item[0] and item[1] is not None and item[2] == "credit"
        ],
        key=lambda item: item[0],
    )
    transfer_index = 0
    available_transfers: list[tuple[float, int, date, dict[str, Any]]] = []
    for credit_date, credit_amount in credits:
        while (
            transfer_index < len(transfer_debits)
            and transfer_debits[transfer_index][0]
            <= credit_date + timedelta(days=rapid_days)
        ):
            debit_date, debit_amount, debit = transfer_debits[transfer_index]
            heapq.heappush(
                available_transfers,
                (-debit_amount, transfer_index, debit_date, debit),
            )
            transfer_index += 1
        while available_transfers and available_transfers[0][2] < credit_date:
            heapq.heappop(available_transfers)
        if (
            available_transfers
            and -available_transfers[0][0] >= credit_amount * rapid_ratio
        ):
            related_debit = available_transfers[0][3]
            add_flag(
                "RAPID_FUNDS_OUTFLOW",
                "A substantial transfer out followed an incoming deposit within the configured review window; verify context.",
                "medium",
                related_debit,
                relatedDepositDate=credit_date.isoformat(),
            )

    reconciliation_internal = financial["reconciliation"]
    reconciliation = {
        **reconciliation_internal,
        "status": reconciliation_internal.get("statusLabel", "NOT_AVAILABLE"),
        "legacyStatus": reconciliation_internal.get("status"),
    }
    if reconciliation_internal["status"] == "mismatch":
        add_flag(
            "RECONCILIATION_MISMATCH",
            "Opening balance plus credits minus debits differs from the extracted closing balance.",
            expectedClosingBalance=(
                round(
                    (_number(fields.get("openingBalance")) or 0)
                    + (financial["total_deposits"] or 0)
                    - (financial["total_withdrawals"] or 0),
                    2,
                )
                if _number(fields.get("openingBalance")) is not None
                and financial["total_deposits"] is not None
                and financial["total_withdrawals"] is not None
                else None
            ),
            extractedClosingBalance=financial.get("closing_balance"),
            difference=reconciliation.get("delta"),
        )
    elif reconciliation_internal["status"] != "reconciled":
        add_flag(
            "RECONCILIATION_INSUFFICIENT_DATA",
            "Opening balance, closing balance, or transaction totals are incomplete.",
            "low",
        )

    completeness = calculate_document_verification_score(fields)
    if not ocr_complete:
        financial = {
            **financial,
            "financial_score": None,
            "status": "manual_review",
            "human_review_required": True,
        }
        financial_requirement_status = "INSUFFICIENT_DATA"

    if not ocr_complete:
        overall_status = "NEEDS_MANUAL_REVIEW"
    elif financial_requirement_status == "FAIL":
        overall_status = "NEEDS_MANUAL_REVIEW"
    elif financial_requirement_status != "PASS" or flags:
        overall_status = "NEEDS_MANUAL_REVIEW"
    else:
        overall_status = "CONSISTENCY_CHECK_PASSED"
    monthly_transaction_summary = []
    for month_row in financial.get("monthly_summary", []):
        month = month_row["month"]
        check = next((item for item in monthly_checks if item["month"] == month), {})
        values = monthly_summaries.get(month, {})
        monthly_transaction_summary.append(
            {
                "month": month,
                "totalDeposits": month_row.get("credits") if month_row.get("credits") is not None else 0,
                "totalWithdrawals": month_row.get("debits") if month_row.get("debits") is not None else 0,
                "incomingCount": values.get("incomingCount", 0),
                "outgoingCount": values.get("outgoingCount", 0),
                "transactionCount": month_row.get("transaction_count", 0),
                "closingBalance": (
                    check.get("closingBalance")
                    if check.get("closingBalance") is not None
                    else month_row.get("closing_balance")
                ),
                "minimumRequired": check.get("minimumRequired"),
                "differenceFromMinimum": check.get("differenceFromMinimum"),
                "shortfall": check.get("shortfall"),
                "minimumBalanceStatus": check.get("minimumBalanceStatus")
                or "INSUFFICIENT_DATA",
                "minimumObservedBalance": month_row.get("minimum_observed_balance"),
                "currency": statement_currency or financial.get("currency"),
                "closingBalanceEvidenceAvailable": bool(
                    check.get("closingBalance") is not None
                    or month_row.get("closing_balance_evidence_available")
                ),
            }
        )

    missing_evidence = []
    if period_start is None:
        missing_evidence.append("statement_start_date")
    if period_end is None:
        missing_evidence.append("statement_end_date")
    if period_opening_balance is None:
        missing_evidence.append("opening_balance")
    if (
        latest_month_check is None
        or latest_month_check.get("closingBalance") is None
        or latest_month_check.get("ambiguous")
    ):
        missing_evidence.append("closing_balance")
    in_period_transaction_records = []
    if month_keys and period_end:
        scoring_start = date.fromisoformat(f"{month_keys[0]}-01")
        in_period_transaction_records = [
            item
            for item in transaction_records
            if (tx_date := _date(item.get("date") or item.get("transactionDate")))
            and scoring_start <= tx_date <= period_end
        ]
    if not in_period_transaction_records:
        missing_evidence.append("latest_three_month_transaction_history")
    if not applicant_name:
        missing_evidence.append("applicant_name_for_account_holder_comparison")
    if not statement_currency:
        missing_evidence.append("statement_currency")

    recurring_sources = [
        item for item in source_patterns.values() if len(item["months"]) >= 2
    ]
    recurring_source_output = [
        {
            "category": item["category"],
            "descriptionPattern": item["descriptionPattern"],
            "months": sorted(item["months"]),
            "transactionCount": len(item["transactionIndexes"]),
            "totalAmount": round(item["totalAmount"], 2),
            "currency": statement_currency or None,
            "transactionIndexes": item["transactionIndexes"],
            "evidence": item["evidence"],
            "status": "POSSIBLE_MATCH",
            "note": "Recurring description pattern only; verify source documents before treating as income.",
        }
        for item in recurring_sources
    ]
    source_of_funds = {
        "status": "POSSIBLE_MATCH" if recurring_source_output else "INSUFFICIENT_DATA",
        "recurringIncomeCandidates": recurring_source_output,
        "possibleOwnAccountTransfers": own_account_transfers,
        "unexplainedLargeDeposits": [
            item
            for item in financial.get("large_deposits", [])
            if item.get("review_required")
        ],
        "note": "Credits are not automatically classified as salary; transfer sources and income require human confirmation.",
    }

    return {
        "extractionStatus": "EXTRACTION_COMPLETED"
        if fields or raw_text
        else "NEEDS_MANUAL_REVIEW",
        "status": overall_status,
        "financialRequirementStatus": financial_requirement_status,
        "authenticityStatus": "AUTHENTICITY_NOT_CONFIRMED",
        "authenticityVerified": False,
        "accountInformation": {
            "bankName": fields.get("bankName") or fields.get("bank_name"),
            "accountHolderName": holder_name or None,
            "accountNumber": fields.get("accountNumber") or fields.get("account_number") or fields.get("accountNo"),
            "currency": statement_currency or None,
            "openingBalance": financial.get("opening_balance"),
            "closingBalance": financial.get("closing_balance"),
        },
        "statementPeriod": {
            "startDate": period_start.isoformat() if period_start else None,
            "endDate": period_end.isoformat() if period_end else None,
            "scoringStartDate": (
                f"{month_keys[0]}-01" if len(month_keys) == 3 else None
            ),
            "scoringEndDate": period_end.isoformat() if period_end else None,
            "completeThreeMonths": period_complete,
            "monthsEvaluated": month_keys,
            "status": "COMPLETE" if period_complete else "INSUFFICIENT_DATA",
            "evidenceSource": "explicit extracted statement-period fields",
        },
        "periodOpeningBalance": period_opening_balance,
        "periodOpeningBalanceAmbiguous": period_opening_ambiguous,
        "periodOpeningBalanceEvidence": period_opening_candidates,
        "monthlyBalanceChecks": monthly_checks,
        "monthlyBalanceHistory": serialized_monthly_balance_history,
        "minimumMonthlyClosingBalanceUsd": threshold,
        "minimumBalanceAssessment": minimum_balance_assessment,
        "transactionSummary": {
            "totalDeposits": financial.get("total_deposits"),
            "totalWithdrawals": financial.get("total_withdrawals"),
            "incomingCount": sum(
                item["incomingCount"] for item in monthly_transaction_summary
            ),
            "outgoingCount": sum(
                item["outgoingCount"] for item in monthly_transaction_summary
            ),
            "monthly": monthly_transaction_summary,
        },
        "transactions": transactions,
        "transactionCount": len(transactions),
        "invalidTransactions": normalized_result["invalid_transactions"],
        "invalidTransactionCount": len(normalized_result["invalid_transactions"]),
        "normalizationWarnings": normalized_result["warnings"],
        "reconciliation": reconciliation,
        "accountHolderCheck": {
            "status": name_status,
            "accountHolderName": holder_name or None,
            "applicantName": applicant_name or None,
            "reason": name_reason,
            "originalExtractedName": holder_name or None,
        },
        "duplicatePages": duplicate_pages,
        "duplicateTransactions": duplicate_transactions,
        "riskFlags": flags,
        "reviewFlags": flags,
        "sourceOfFundsAnalysis": source_of_funds,
        "missingEvidence": list(dict.fromkeys(missing_evidence)),
        "extractionWarnings": list(normalized_result["warnings"]),
        "financialAnalysis": financial,
        "documentVerificationScore": completeness,
        "reviewReasons": list(dict.fromkeys(flag["reason"] for flag in flags)),
        "disclaimer": (
            "Automated OCR and consistency checks only. This report does not "
            "verify authenticity, establish fraud, or predict visa approval."
        ),
    }
