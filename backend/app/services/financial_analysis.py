from __future__ import annotations

import math
import re
import calendar
from collections import defaultdict
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any

from .transaction_normalization import normalize_transactions, parse_amount


DEFAULT_SCORING_CONFIG: dict[str, Any] = {
    "weights": {
        "average_daily_balance": 25,
        "monthly_income_consistency": 20,
        "balance_stability": 15,
        "transaction_consistency": 15,
        "savings_growth": 10,
        "employment_income_evidence": 10,
        "account_history": 5,
    },
    "targets": {
        "average_daily_balance_usd": 5000,
        "income_consistency_cv": 0.3,
        "balance_stability_cv": 0.5,
        "monthly_transaction_count": 8,
        "savings_growth_rate": 0.1,
        "account_history_months": 12,
        "large_deposit_threshold_usd": 1000,
        "reconciliation_tolerance": 1,
    },
    "risk_thresholds": {"low_minimum": 65, "medium_minimum": 40},
    "unexplained_deposit_penalty_points": 10,
}

_CREDITS = {"credit", "deposit", "income", "salary", "cr"}
_DEBITS = {"debit", "withdrawal", "expense", "payment", "dr"}
_EXPLAINED_DEPOSIT_TERMS = (
    "salary",
    "payroll",
    "wage",
    "pension",
    "business income",
    "invoice",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        result, _, _ = parse_amount(value)
        if result is None:
            return None
    return result if math.isfinite(result) else None


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


def _round(value: float | None) -> float | None:
    return round(value, 2) if value is not None and math.isfinite(value) else None


def _coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = mean(values)
    if abs(average) < 1e-9:
        return 0.0 if all(abs(value) < 1e-9 for value in values) else None
    return pstdev(values) / abs(average)


def _transaction_direction(transaction: dict[str, Any]) -> str | None:
    normalized = str(transaction.get("direction") or "").strip().casefold()
    if normalized in {"credit", "debit"}:
        return normalized
    direction = str(transaction.get("type") or "").strip().casefold()
    if direction in _CREDITS:
        return "credit"
    if direction in _DEBITS:
        return "debit"
    amount = _number(transaction.get("amount"))
    if amount is not None and amount < 0:
        return "debit"
    return None


def _month_delta(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + end.month - start.month + 1)


def _time_weighted_average(
    start: date,
    end: date,
    opening_balance: float,
    closing_balance: float,
    dated_balances: list[tuple[date, float]],
) -> float | None:
    if end <= start or not dated_balances:
        return None
    points = sorted(dated_balances, key=lambda item: item[0])
    total = 0.0
    cursor = start
    balance = opening_balance
    for point_date, next_balance in points:
        bounded_date = min(max(point_date, start), end)
        total += balance * max(0, (bounded_date - cursor).days)
        cursor = max(cursor, bounded_date)
        balance = next_balance
    total += balance * max(0, (end - cursor).days)
    days = (end - start).days
    return total / days if days else closing_balance


def calculate_financial_analysis(
    fields: dict[str, Any],
    transactions: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = config or DEFAULT_SCORING_CONFIG
    weights = policy.get("weights", DEFAULT_SCORING_CONFIG["weights"])
    targets = policy.get("targets", DEFAULT_SCORING_CONFIG["targets"])
    thresholds = policy.get("risk_thresholds", DEFAULT_SCORING_CONFIG["risk_thresholds"])
    source_transactions = transactions
    if source_transactions is None:
        source_transactions = (
            fields.get("transactionHistory")
            or fields.get("transaction_history")
            or fields.get("transactions")
        )
    if not isinstance(source_transactions, list):
        source_transactions = []

    normalized_result = normalize_transactions(
        source_transactions,
        default_currency=fields.get("currency"),
    )
    normalized_transactions: list[dict[str, Any]] = []
    for item in normalized_result["transactions"]:
        amount = _number(item.get("amount"))
        direction = item.get("direction")
        transaction_date = _date(item.get("date"))
        balance = _number(item.get("running_balance"))
        normalized_transactions.append(
            {
                "transactionIndex": item.get("transactionIndex"),
                "date": transaction_date,
                "amount": abs(amount) if amount is not None else None,
                "direction": direction,
                "balance": balance,
                "description": str(item.get("description") or "")[:160],
                "currency": item.get("currency"),
                "page": item.get("page"),
                "evidence": item.get("evidence") or "",
                "extraction_status": item.get("extraction_status"),
                "normalization_errors": item.get("normalization_errors") or [],
                "type": item.get("type") or direction or "unknown",
                "runningBalance": _round(balance),
                "sourceText": item.get("sourceText") or item.get("evidence") or "",
                "referenceNumber": item.get("referenceNumber"),
                "sender": item.get("sender"),
                "receiver": item.get("receiver"),
                "counterparty": item.get("counterparty"),
            }
        )

    period = fields.get("statementPeriod") or fields.get("statement_period") or {}
    start_date = _date(
        fields.get("statementStartDate")
        or fields.get("statement_start_date")
        or (period.get("startDate") or period.get("start_date") if isinstance(period, dict) else None)
    )
    end_date = _date(
        fields.get("statementEndDate")
        or fields.get("statement_end_date")
        or (period.get("endDate") or period.get("end_date") if isinstance(period, dict) else None)
    )
    opening = _number(fields.get("openingBalance", fields.get("opening_balance")))
    closing = _number(fields.get("closingBalance", fields.get("closing_balance")))
    currency = str(fields.get("currency") or fields.get("currency_code") or "").strip().upper() or None

    monthly_income: dict[str, float] = defaultdict(float)
    monthly_expenses: dict[str, float] = defaultdict(float)
    monthly_transactions: dict[str, int] = defaultdict(int)
    credits: list[float] = []
    debits: list[float] = []
    dated_balances: list[tuple[date, float]] = []
    all_directions_known = bool(normalized_transactions)
    for transaction in normalized_transactions:
        amount = transaction["amount"]
        direction = transaction["direction"]
        transaction_date = transaction["date"]
        if amount is None or direction is None:
            all_directions_known = False
        elif direction == "credit":
            credits.append(amount)
            if transaction_date:
                monthly_income[transaction_date.strftime("%Y-%m")] += amount
        elif direction == "debit":
            debits.append(amount)
            if transaction_date:
                monthly_expenses[transaction_date.strftime("%Y-%m")] += amount
        if transaction_date:
            monthly_transactions[transaction_date.strftime("%Y-%m")] += 1
        if transaction_date and transaction["balance"] is not None:
            dated_balances.append((transaction_date, transaction["balance"]))

    total_deposits = _number(fields.get("deposits", fields.get("totalCredits", fields.get("total_credits"))))
    if total_deposits is None and all_directions_known:
        total_deposits = sum(credits)
    total_withdrawals = _number(fields.get("withdrawals", fields.get("totalDebits", fields.get("total_debits"))))
    if total_withdrawals is None and all_directions_known:
        total_withdrawals = sum(debits)

    average_daily_balance = _number(fields.get("averageBalance", fields.get("average_balance")))
    average_balance_source = "document_extracted" if average_daily_balance is not None else None
    if (
        start_date
        and end_date
        and opening is not None
        and closing is not None
        and dated_balances
    ):
        computed_average = _time_weighted_average(
            start_date, end_date, opening, closing, dated_balances
        )
        if computed_average is not None:
            average_daily_balance = computed_average
            average_balance_source = "computed_from_running_balances"

    monthly_income_values = list(monthly_income.values())
    income_cv = _coefficient_of_variation(monthly_income_values)
    monthly_balance_values = [balance for _, balance in dated_balances]
    balance_cv = _coefficient_of_variation(monthly_balance_values)
    transaction_count_cv = _coefficient_of_variation(
        list(monthly_transactions.values())
    )
    statement_months = (
        _month_delta(start_date, end_date)
        if start_date and end_date and end_date >= start_date
        else None
    )
    transaction_history_months = (
        (end_date - start_date).days / 30.4375
        if start_date and end_date and end_date >= start_date
        else None
    )
    savings_growth_rate = (
        (closing - opening) / abs(opening)
        if opening is not None and closing is not None and abs(opening) > 1e-9
        else None
    )

    observed_balances = [balance for _, balance in dated_balances]
    if opening is not None:
        observed_balances.append(opening)
    minimum_balance = min(observed_balances) if observed_balances else None

    reconciliation_delta = None
    if opening is not None and closing is not None:
        if all_directions_known:
            expected_closing = opening + sum(credits) - sum(debits)
            reconciliation_delta = closing - expected_closing
        elif total_deposits is not None and total_withdrawals is not None:
            reconciliation_delta = closing - (opening + total_deposits - total_withdrawals)

    threshold = float(
        targets.get("large_deposit_threshold_usd", 1000)
    )
    large_deposits = []
    for index, transaction in enumerate(normalized_transactions):
        amount = transaction["amount"]
        if (
            currency not in {"USD", "US DOLLAR", "US DOLLARS", "$"}
            or transaction["direction"] != "credit"
            or amount is None
            or amount < threshold
        ):
            continue
        description = transaction["description"]
        explanation_present = any(
            term in description.casefold() for term in _EXPLAINED_DEPOSIT_TERMS
        )
        large_deposits.append(
            {
                "transaction_index": index,
                "date": transaction["date"].isoformat() if transaction["date"] else None,
                "amount": _round(amount),
                "currency": currency,
                "description": description,
                "source_indicated_by_description": explanation_present,
                "review_required": not explanation_present,
                "note": (
                    "Large deposit candidate; confirm source and supporting evidence. "
                    "This is not a fraud finding."
                ),
            }
        )

    warnings: list[str] = []
    monthly_closings: dict[str, list[float]] = defaultdict(list)
    monthly_minimums: dict[str, list[float]] = defaultdict(list)
    for collection_name in (
        "monthlyClosingBalances",
        "monthly_closing_balances",
        "monthlyBalanceHistory",
        "monthly_balance_history",
    ):
        entries = fields.get(collection_name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            month_value = entry.get("month") or entry.get("date") or entry.get("monthEndDate")
            month_date = _date(month_value)
            month_key = (
                str(month_value).strip()[:7]
                if isinstance(month_value, str) and re.fullmatch(r"\d{4}-\d{2}", month_value.strip())
                else month_date.strftime("%Y-%m") if month_date else None
            )
            closing_value = entry.get(
                "closingBalance",
                entry.get("closing_balance", entry.get("balance")),
            )
            parsed_closing = _number(closing_value)
            if month_key and parsed_closing is not None:
                monthly_closings[month_key].append(parsed_closing)
            minimum_value = entry.get("minimumBalance", entry.get("minimum_balance"))
            parsed_minimum = _number(minimum_value)
            if month_key and parsed_minimum is not None:
                monthly_minimums[month_key].append(parsed_minimum)

    for transaction in normalized_transactions:
        if transaction["date"] and transaction["balance"] is not None:
            transaction_date = transaction["date"]
            last_day = calendar.monthrange(transaction_date.year, transaction_date.month)[1]
            if transaction_date.day == last_day:
                monthly_closings[transaction_date.strftime("%Y-%m")].append(
                    transaction["balance"]
                )
            monthly_minimums[transaction_date.strftime("%Y-%m")].append(
                transaction["balance"]
            )
    if opening is not None and start_date:
        monthly_minimums[start_date.strftime("%Y-%m")].append(opening)

    month_keys = set(monthly_income) | set(monthly_expenses) | set(monthly_transactions)
    if start_date and end_date and end_date >= start_date:
        year, month = start_date.year, start_date.month
        while (year, month) <= (end_date.year, end_date.month):
            month_keys.add(f"{year:04d}-{month:02d}")
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
    monthly_summary = [
        {
            "month": key,
            "credits": _round(monthly_income.get(key, 0.0)),
            "debits": _round(monthly_expenses.get(key, 0.0)),
            "transaction_count": monthly_transactions.get(key, 0),
            "closing_balance": (
                _round(monthly_closings[key][-1])
                if monthly_closings.get(key)
                and len({round(value, 2) for value in monthly_closings[key]}) == 1
                else None
            ),
            "minimum_observed_balance": (
                _round(min(monthly_minimums[key])) if monthly_minimums.get(key) else None
            ),
            "closing_balance_evidence_available": bool(monthly_closings.get(key)),
            "minimum_balance_evidence_available": bool(monthly_minimums.get(key)),
        }
        for key in sorted(month_keys)
    ]

    if currency not in {"USD", "US DOLLAR", "US DOLLARS", "$"}:
        warnings.append("USD-based thresholds cannot be applied without a verified currency conversion.")
    if normalized_transactions and not all_directions_known:
        warnings.append("Some transaction directions or amounts are unclear; totals and reconciliation may be incomplete.")
    if start_date is None or end_date is None:
        warnings.append("Statement duration is incomplete because one or both period dates are missing.")
    if large_deposits:
        warnings.append("One or more large deposits require source verification; this is not a fraud classification.")
    if reconciliation_delta is not None and abs(reconciliation_delta) > float(
        targets.get("reconciliation_tolerance", 1)
    ):
        warnings.append("Statement totals do not reconcile within the configured tolerance.")

    score_components: dict[str, dict[str, Any]] = {}

    def add_component(name: str, ratio: float | None, detail: str) -> None:
        maximum = float(weights.get(name, 0))
        value = max(0.0, min(maximum, maximum * ratio)) if ratio is not None else 0.0
        score_components[name] = {
            "points": round(value, 2),
            "maximum": maximum,
            "available": ratio is not None,
            "explanation": detail,
        }

    adb_target = float(targets.get("average_daily_balance_usd", 5000))
    add_component(
        "average_daily_balance",
        min(1.0, average_daily_balance / adb_target)
        if average_daily_balance is not None
        and currency in {"USD", "US DOLLAR", "US DOLLARS", "$"}
        and adb_target > 0
        else None,
        "Compares average daily balance to the configured target; USD is required for USD targets.",
    )
    income_limit = float(targets.get("income_consistency_cv", 0.3))
    add_component(
        "monthly_income_consistency",
        max(0.0, 1.0 - income_cv / income_limit)
        if income_cv is not None and income_limit > 0
        else None,
        "Lower month-to-month income variation earns more points; at least two dated income months are needed.",
    )
    balance_limit = float(targets.get("balance_stability_cv", 0.5))
    add_component(
        "balance_stability",
        max(0.0, 1.0 - balance_cv / balance_limit)
        if balance_cv is not None and balance_limit > 0
        else None,
        "Based on the variation of transaction running balances; this is a descriptive heuristic.",
    )
    transaction_target = float(targets.get("monthly_transaction_count", 8))
    frequency_ratio = (
        min(1.0, mean(monthly_transactions.values()) / transaction_target)
        if monthly_transactions and transaction_target > 0
        else None
    )
    if frequency_ratio is not None and transaction_count_cv is not None:
        frequency_ratio *= max(0.0, 1.0 - transaction_count_cv)
    add_component(
        "transaction_consistency",
        frequency_ratio,
        "Considers monthly transaction frequency and variation; dated transactions are required.",
    )
    growth_target = float(targets.get("savings_growth_rate", 0.1))
    add_component(
        "savings_growth",
        min(1.0, max(0.0, savings_growth_rate / growth_target))
        if savings_growth_rate is not None and growth_target > 0
        else None,
        "Compares opening and closing balances; it does not infer a future savings trend.",
    )
    evidence = fields.get("employmentIncomeEvidence")
    if evidence is None:
        evidence = fields.get("employment_income_evidence")
    add_component(
        "employment_income_evidence",
        1.0 if evidence is True else (0.0 if evidence is False else None),
        "Requires a separately verified employment or income evidence flag.",
    )
    history_target = float(targets.get("account_history_months", 12))
    add_component(
        "account_history",
        min(1.0, transaction_history_months / history_target)
        if transaction_history_months is not None and history_target > 0
        else None,
        "Based on the observed statement period, not the age of the bank account.",
    )

    raw_score = sum(component["points"] for component in score_components.values())
    penalty_points = (
        float(policy.get("unexplained_deposit_penalty_points", 0))
        if any(deposit["review_required"] for deposit in large_deposits)
        else 0.0
    )
    available_components = sum(
        component["available"] for component in score_components.values()
    )
    financial_score = (
        round(max(0.0, min(100.0, raw_score - penalty_points)), 2)
        if available_components >= 3
        else None
    )
    low_minimum = float(thresholds.get("low_minimum", 65))
    medium_minimum = float(thresholds.get("medium_minimum", 40))
    risk_category = (
        "INSUFFICIENT_DATA"
        if financial_score is None
        else "LOW"
        if financial_score >= low_minimum
        else "MEDIUM"
        if financial_score >= medium_minimum
        else "HIGH"
    )

    return {
        "status": "manual_review" if warnings or large_deposits else "calculated",
        "financial_score": financial_score,
        "risk_category": risk_category,
        "score_type": "configurable_rule_based_financial_assessment",
        "disclaimer": (
            "Financial review support only. Not a visa approval probability, "
            "fraud finding, or guarantee."
        ),
        "currency": currency,
        "opening_balance": _round(opening),
        "closing_balance": _round(closing),
        "average_daily_balance": _round(average_daily_balance),
        "average_daily_balance_source": average_balance_source,
        "minimum_balance": _round(minimum_balance),
        "monthly_income": {
            month: _round(amount) for month, amount in sorted(monthly_income.items())
        },
        "monthly_expenses": {
            month: _round(amount) for month, amount in sorted(monthly_expenses.items())
        },
        "monthly_summary": monthly_summary,
        "total_deposits": _round(total_deposits),
        "total_withdrawals": _round(total_withdrawals),
        "transaction_frequency": len(normalized_transactions),
        "transaction_count_by_month": dict(sorted(monthly_transactions.items())),
        "large_deposits": large_deposits,
        "balance_stability_cv": _round(balance_cv),
        "income_consistency_cv": _round(income_cv),
        "statement_duration_months": statement_months,
        "account_history_months_observed": _round(transaction_history_months),
        "savings_growth_rate": _round(savings_growth_rate),
        "reconciliation": {
            "delta": _round(reconciliation_delta),
            "tolerance": float(targets.get("reconciliation_tolerance", 1)),
            "status": (
                "not_available"
                if reconciliation_delta is None
                else "reconciled"
                if abs(reconciliation_delta)
                <= float(targets.get("reconciliation_tolerance", 1))
                else "mismatch"
            ),
            "statusLabel": (
                "NOT_AVAILABLE"
                if reconciliation_delta is None
                else "MATCHED"
                if abs(reconciliation_delta)
                <= float(targets.get("reconciliation_tolerance", 1))
                else "MISMATCHED"
            ),
            "expected_closing_balance": _round(
                opening + sum(credits) - sum(debits)
                if opening is not None and all_directions_known
                else opening + total_deposits - total_withdrawals
                if opening is not None and total_deposits is not None and total_withdrawals is not None
                else None
            ),
            "extracted_closing_balance": _round(closing),
            "calculation": "opening balance + total credits - total debits",
            "evidence": {
                "opening_balance": _round(opening),
                "total_credits": _round(total_deposits),
                "total_debits": _round(total_withdrawals),
                "closing_balance": _round(closing),
                "transaction_count": len(normalized_transactions),
                "fully_interpretable_transaction_count": sum(
                    item["amount"] is not None and item["direction"] is not None
                    for item in normalized_transactions
                ),
            },
        },
        "normalized_transactions": [
            {
                "transactionIndex": item["transactionIndex"],
                "date": item["date"].isoformat() if item["date"] else None,
                "description": item["description"],
                "amount": _round(item["amount"]),
                "direction": item["direction"],
                "running_balance": _round(item["balance"]),
                "currency": item["currency"] or currency,
                "page": item["page"],
                "evidence": item["evidence"],
                "extraction_status": item["extraction_status"],
                "normalization_errors": item["normalization_errors"],
                "type": item["type"],
                "runningBalance": item["runningBalance"],
                "sourceText": item["sourceText"],
                "referenceNumber": item["referenceNumber"],
                "sender": item["sender"],
                "receiver": item["receiver"],
                "counterparty": item["counterparty"],
            }
            for item in normalized_transactions
        ],
        "invalid_transactions": normalized_result["invalid_transactions"],
        "transaction_normalization_warnings": normalized_result["warnings"],
        "score_components": score_components,
        "penalty_points": penalty_points,
        "warnings": warnings,
        "human_review_required": bool(warnings or large_deposits),
    }
