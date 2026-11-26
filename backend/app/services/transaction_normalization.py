from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_DATE_KEYS = (
    "date",
    "transactionDate",
    "transaction_date",
    "transaction_date_time",
    "postingDate",
    "posting_date",
    "postedDate",
    "valueDate",
    "value_date",
    "txnDate",
    "txn_date",
)
_AMOUNT_KEYS = ("amount", "transactionAmount", "transaction_amount", "value", "total")
_DIRECTION_KEYS = (
    "direction",
    "type",
    "transactionType",
    "transaction_type",
    "entryType",
    "entry_type",
    "debitCredit",
    "debit_credit",
    "drCr",
    "dr_cr",
)
_BALANCE_KEYS = (
    "running_balance",
    "runningBalance",
    "balance",
    "availableBalance",
    "available_balance",
    "ledgerBalance",
    "ledger_balance",
)
_PAGE_KEYS = ("page", "pageNumber", "page_number", "pageIndex")
_EVIDENCE_KEYS = (
    "evidence",
    "sourceText",
    "source_text",
    "sourceLine",
    "source_line",
    "rawText",
    "raw_text",
    "ocrText",
)
_CREDIT_WORDS = {"credit", "deposit", "income", "salary", "payroll", "cr", "money in"}
_DEBIT_WORDS = {
    "debit",
    "withdrawal",
    "withdraw",
    "expense",
    "payment",
    "purchase",
    "fee",
    "charge",
    "dr",
    "money out",
}
_CURRENCY_PATTERN = re.compile(r"\b(USD|US\s+DOLLARS?|KHR|RIEL|EUR|THB|GBP|CAD|AUD)\b|[$€£¥៛]", re.I)


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _currency(value: Any, amount_text: Any = None, fallback: Any = None) -> str | None:
    raw = str(value or "").strip().upper()
    if raw in {"$", "US DOLLAR", "US DOLLARS"}:
        return "USD"
    if raw in {"៛", "RIEL"}:
        return "KHR"
    if re.fullmatch(r"[A-Z]{3}", raw):
        return raw
    for candidate in (amount_text, fallback):
        match = _CURRENCY_PATTERN.search(str(candidate or ""))
        if not match:
            continue
        token = (match.group(1) or match.group(0)).upper()
        if token in {"$", "US DOLLAR", "US DOLLARS"}:
            return "USD"
        if token in {"៛", "RIEL"}:
            return "KHR"
        return re.sub(r"\s+", " ", token)
    return None


def parse_amount(value: Any) -> tuple[float | None, str | None, bool]:
    """Return signed amount, detected currency, and whether parsing was ambiguous."""
    if isinstance(value, bool) or value is None:
        return None, None, False
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return (number if math.isfinite(number) else None), None, False

    raw = str(value).strip().replace("−", "-")
    currency = _currency(None, raw)
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    normalized = re.sub(r"[^0-9,.+\-]", "", raw)
    if not normalized or normalized in {"+", "-", ".", ","}:
        return None, currency, False
    if negative_parentheses and not normalized.startswith("-"):
        normalized = f"-{normalized}"

    comma = normalized.rfind(",")
    dot = normalized.rfind(".")
    ambiguous = False
    if comma >= 0 and dot >= 0:
        decimal_separator = "," if comma > dot else "."
        grouping_separator = "." if decimal_separator == "," else ","
        normalized = normalized.replace(grouping_separator, "")
        normalized = normalized.replace(decimal_separator, ".")
    elif comma >= 0:
        places = len(normalized) - comma - 1
        if places in (1, 2):
            normalized = normalized.replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif dot >= 0 and len(normalized) - dot - 1 > 2:
        normalized = normalized.replace(".", "")

    try:
        amount = float(Decimal(normalized))
    except (InvalidOperation, ValueError, OverflowError):
        return None, currency, ambiguous
    if not math.isfinite(amount):
        return None, currency, ambiguous
    return amount, currency, ambiguous


def parse_transaction_date(value: Any) -> tuple[date | None, bool]:
    if isinstance(value, datetime):
        return value.date(), False
    if isinstance(value, date):
        return value, False
    if not isinstance(value, str):
        return None, False
    raw = value.strip()
    if not raw:
        return None, False
    normalized = re.sub(r"(?i)(\d)(st|nd|rd|th)\b", r"\1", raw)
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date(), False
    except ValueError:
        pass

    numeric = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", normalized)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        if first <= 12 and second <= 12 and first != second:
            return None, True

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %y",
        "%d %B %y",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%m.%d.%Y",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d/%m/%y",
        "%m/%d/%y",
    )
    normalized = normalized.replace(",", "")
    for pattern in formats:
        try:
            return datetime.strptime(normalized, pattern).date(), False
        except ValueError:
            continue

    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", normalized)
    if not match:
        return None, False
    first, second, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000 if year < 70 else 1900
    if first > 12 and second <= 12:
        return _valid_date(year, second, first), False
    if second > 12 and first <= 12:
        return _valid_date(year, first, second), False
    if first == second and 1 <= first <= 12:
        return _valid_date(year, first, second), False
    if 1 <= first <= 12 and 1 <= second <= 12:
        return None, True
    return None, False


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _direction(record: dict[str, Any], amount: float | None, description: str) -> str | None:
    raw = _first(record, _DIRECTION_KEYS)
    value = re.sub(r"\s+", " ", str(raw or "").strip().casefold())
    if value in _CREDIT_WORDS or value.endswith(" credit") or value.endswith(" cr"):
        return "credit"
    if value in _DEBIT_WORDS or value.endswith(" debit") or value.endswith(" dr"):
        return "debit"
    if amount is not None and amount < 0:
        return "debit"
    text = description.casefold()
    if re.search(r"\b(?:salary|payroll|wage|pension|deposit|credit|money\s+in|transfer\s+in|received)\b", text):
        return "credit"
    if re.search(r"\b(?:withdrawal|payment|purchase|fee|charge|debit|money\s+out|transfer\s+out|sent)\b", text):
        return "debit"
    return None


def normalize_transactions(
    transactions: Any,
    *,
    default_currency: Any = None,
) -> dict[str, Any]:
    """Normalize transaction aliases without dropping malformed or ambiguous rows."""
    source = transactions if isinstance(transactions, list) else []
    normalized: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, item in enumerate(source):
        record = item if isinstance(item, dict) else {}
        errors: list[str] = []
        amount_value = None
        date_value = _first(record, _DATE_KEYS)
        transaction_date, ambiguous_date = parse_transaction_date(date_value)
        if transaction_date is None:
            errors.append("ambiguous_date" if ambiguous_date else "missing_or_invalid_date")

        description_value = _first(record, ("description", "details", "narrative", "memo", "transactionDescription", "transaction_description"))
        description = str(description_value or "").strip()
        evidence_value = _first(record, _EVIDENCE_KEYS)
        evidence = str(evidence_value if evidence_value is not None else (item if isinstance(item, str) else ""))[:1000]

        debit_value = _first(record, ("debit", "debitAmount", "debit_amount", "withdrawalAmount", "withdrawal_amount"))
        credit_value = _first(record, ("credit", "creditAmount", "credit_amount", "depositAmount", "deposit_amount"))
        debit_amount, debit_currency, _ = parse_amount(debit_value)
        credit_amount, credit_currency, _ = parse_amount(credit_value)
        has_debit = debit_amount is not None and debit_amount != 0
        has_credit = credit_amount is not None and credit_amount != 0
        amount_currency = None
        direction = None
        if has_debit and has_credit:
            amount = None
            errors.append("ambiguous_debit_and_credit")
        elif has_debit:
            amount = abs(debit_amount)
            direction = "debit"
            amount_currency = debit_currency
        elif has_credit:
            amount = abs(credit_amount)
            direction = "credit"
            amount_currency = credit_currency
        else:
            amount_value = _first(record, _AMOUNT_KEYS)
            amount_signed, amount_currency, amount_ambiguous = parse_amount(amount_value)
            amount = abs(amount_signed) if amount_signed is not None else None
            direction = _direction(record, amount_signed, description)
            declared_direction = str(_first(record, _DIRECTION_KEYS) or "").strip().casefold()
            if amount_signed is not None and amount_signed < 0 and declared_direction in _CREDIT_WORDS:
                direction = None
                errors.append("conflicting_amount_sign_and_direction")
            if amount_ambiguous:
                errors.append("ambiguous_amount_format")

        if amount is None:
            errors.append("missing_or_invalid_amount")
        if direction is None:
            errors.append("ambiguous_or_missing_direction")

        currency = _currency(
            _first(record, ("currency", "currencyCode", "currency_code")),
            amount_value,
            default_currency,
        ) or _currency(None, debit_value) or _currency(None, credit_value)
        running_balance_value = _first(record, _BALANCE_KEYS)
        running_balance, balance_currency, _ = parse_amount(running_balance_value)
        if currency is None:
            currency = balance_currency or _currency(None, evidence) or _currency(None, default_currency)

        page_value = _first(record, _PAGE_KEYS)
        try:
            page = int(page_value) if page_value is not None else None
            if page is not None and page < 1:
                page = None
        except (TypeError, ValueError):
            page = None

        canonical = {
            "transactionIndex": index,
            "date": transaction_date.isoformat() if transaction_date else None,
            "description": description,
            "amount": round(amount, 2) if amount is not None else None,
            "direction": direction,
            "running_balance": round(running_balance, 2) if running_balance is not None else None,
            "currency": currency,
            "page": page,
            "evidence": evidence,
            "extraction_status": "VALID" if not errors else "NEEDS_REVIEW",
            "normalization_errors": list(dict.fromkeys(errors)),
            # Legacy response aliases retained for existing API clients.
            "type": direction or "unknown",
            "runningBalance": round(running_balance, 2) if running_balance is not None else None,
            "sourceText": evidence,
            "referenceNumber": _first(record, ("referenceNumber", "reference_number", "reference", "transactionId", "transaction_id")),
            "sender": _first(record, ("sender", "senderName", "sender_name", "originator")),
            "receiver": _first(record, ("receiver", "receiverName", "receiver_name", "beneficiary")),
            "counterparty": _first(record, ("counterparty", "counterpartyName", "counterparty_name")),
            # Preserve ABA evidence provenance without reinterpreting minor-unit
            # source amounts as this legacy major-unit transaction contract.
            **{key: record[key] for key in ("sourceRowId", "bbox", "sourceAnalysisRevision", "sourceUnits", "units") if key in record},
        }
        normalized.append(canonical)
        if errors:
            invalid.append({"index": index, "transaction": canonical, "issues": canonical["normalization_errors"]})

    if invalid:
        warnings.append(f"{len(invalid)} transaction row(s) require review because date, amount, or direction evidence is incomplete or ambiguous.")
    return {
        "transactions": normalized,
        "invalid_transactions": invalid,
        "warnings": warnings,
        "source_count": len(source),
        "valid_count": len(normalized) - len(invalid),
    }
