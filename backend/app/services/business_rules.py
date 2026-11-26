from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import math
import re
from typing import Any

from .bank_statement_scoring import (
    DEFAULT_BANK_STATEMENT_SCORING,
    SOURCE_UNVERIFIED_PENALTY_POINTS,
)
from .financial_analysis import DEFAULT_SCORING_CONFIG

_EVIDENCE_KEYS = {
    "employment_letter",
    "business_evidence",
    "ties_to_cambodia",
    "fixed_deposit",
}


def _country_rule(
    citizen_balance: float | None = None,
    foreigner_balance: float | None = None,
    citizen_evidence: list[str] | None = None,
    foreigner_evidence: list[str] | None = None,
    cambodian_balance: float | None = None,
    ties_required: bool = False,
) -> dict[str, Any]:
    return {
        "cambodian_balance_usd": (
            cambodian_balance if cambodian_balance is not None else citizen_balance
        ),
        "foreigner_balance_usd": foreigner_balance,
        "cambodian_evidence": list(citizen_evidence or []),
        "foreigner_evidence": list(foreigner_evidence or []),
        "ties_to_cambodia_required": ties_required,
        "special_cases": {},
    }


DEFAULT_RULE_POLICY: dict[str, Any] = {
    "version": 1,
    "currency": "USD",
    "destination_rules": {
        "japan": _country_rule(
            cambodian_balance=4000,
            foreigner_balance=4000,
            foreigner_evidence=["employment_letter"],
        ),
        "australia": _country_rule(
            cambodian_balance=5000,
            foreigner_balance=4500,
            foreigner_evidence=["employment_letter", "business_evidence"],
        ),
        "hong kong": _country_rule(
            cambodian_balance=1000,
            foreigner_balance=3500,
            foreigner_evidence=["employment_letter", "business_evidence"],
        ),
        "south korea": _country_rule(
            cambodian_balance=4000,
            foreigner_balance=4000,
            foreigner_evidence=["employment_letter", "business_evidence"],
        ),
        "china": {
            **_country_rule(),
            "special_cases": {
                "first_time_female_18_30": {
                    "enabled": False,
                    "required_evidence": [],
                    "manual_review": True,
                },
                "married_applicant": {
                    "enabled": False,
                    "required_evidence": [],
                    "manual_review": True,
                },
            },
        },
        "usa": _country_rule(
            cambodian_balance=5000,
            foreigner_balance=5000,
            ties_required=True,
        ),
        "canada": {
            **_country_rule(cambodian_balance=4000, ties_required=True),
            "foreigner_balance_usd": None,
        },
        "malaysia": _country_rule(
            cambodian_balance=3000,
            foreigner_balance=3000,
            citizen_evidence=["employment_letter", "business_evidence"],
            foreigner_evidence=["employment_letter", "business_evidence"],
        ),
    },
    "universal_rules": {
        "no_overstay_history": True,
        "minimum_passport_validity_months": 6,
        "maximum_previous_visa_denials": 2,
    },
    "financial_scoring": deepcopy(DEFAULT_SCORING_CONFIG),
    "bank_statement_scoring": deepcopy(DEFAULT_BANK_STATEMENT_SCORING),
    "visa_risk_scoring": {
        "low_minimum": 65,
        "medium_minimum": 40,
        "very_low_balance_ratio": 0.5,
        "very_low_balance_penalty": 20,
        "low_balance_penalty": 10,
        "no_travel_history_penalty": 15,
        "limited_travel_history_maximum": 2,
        "limited_travel_history_penalty": 5,
        "no_employment_evidence_penalty": 10,
        "sponsor_support_bonus": 5,
        "fixed_deposit_bonus": 5,
    },
}


def _normalize_country(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def _reject_unknown_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} contains unsupported fields: {', '.join(sorted(unknown))}.")


def _is_cambodian(nationality: Any) -> bool:
    value = _normalize_country(nationality)
    return value in {"khm", "cambodian", "cambodia", "cambodian citizen"}


def configured_bank_balance_requirement(
    policy: dict[str, Any],
    destination: Any,
    nationality: Any,
) -> float | None:
    """Resolve the configured destination/nationality balance rule without guessing."""
    destination_rules = policy.get("destination_rules")
    if not isinstance(destination_rules, dict):
        return None
    rule = destination_rules.get(_normalize_country(destination))
    if not isinstance(rule, dict) or not str(nationality or "").strip():
        return None
    key = "cambodian_balance_usd" if _is_cambodian(nationality) else "foreigner_balance_usd"
    value = rule.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        return None
    return float(value)


def _bool_check(value: Any, title: str, true_is_pass: bool = True) -> dict[str, Any]:
    if not isinstance(value, bool):
        return {"code": title, "status": "manual_review", "reason": "Information not provided."}
    passed = value if true_is_pass else not value
    return {
        "code": title,
        "status": "pass" if passed else "fail",
        "reason": "Requirement met." if passed else "Requirement not met.",
    }


def _evidence_present(applicant: dict[str, Any], evidence: str) -> bool | None:
    direct = applicant.get(evidence)
    if isinstance(direct, bool):
        return direct
    documents = applicant.get("documents")
    if not isinstance(documents, list):
        return None
    synonyms = {
        "employment_letter": {"employment_letter", "employment letter"},
        "business_evidence": {"business_evidence", "business_license", "business document"},
        "ties_to_cambodia": {"ties_to_cambodia", "proof of ties", "family_book", "property"},
        "fixed_deposit": {"fixed_deposit", "fixed deposit"},
    }
    accepted = synonyms.get(evidence, {evidence})
    matching = [
        item
        for item in documents
        if isinstance(item, dict)
        and str(item.get("type") or item.get("code") or "").strip().casefold()
        in accepted
    ]
    if any(str(item.get("status") or "").strip().casefold() == "verified" for item in matching):
        return True
    if matching:
        return None
    return False


def _evidence_check(
    applicant: dict[str, Any], evidence_types: list[str], code: str
) -> dict[str, Any]:
    states = [_evidence_present(applicant, item) for item in evidence_types]
    if any(state is True for state in states):
        status = "pass"
        reason = "At least one required evidence type was verified."
    elif states and all(state is False for state in states):
        status = "fail"
        reason = "No accepted evidence type was provided."
    else:
        status = "manual_review"
        reason = "Evidence is missing or awaiting staff verification."
    return {
        "code": code,
        "status": status,
        "evidence_options": evidence_types,
        "reason": reason,
    }


def validate_rule_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Rule policy must be a JSON object.")
    _reject_unknown_keys(
        value,
        {
            "version",
            "currency",
            "destination_rules",
            "universal_rules",
            "financial_scoring",
            "bank_statement_scoring",
            "visa_risk_scoring",
        },
        "Rule policy",
    )
    version = value.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("Rule policy version must be a positive integer.")
    candidate = deepcopy(DEFAULT_RULE_POLICY)
    if "currency" in value and value["currency"] != "USD":
        raise ValueError("Configured thresholds must use USD; conversion rates are not inferred.")
    supplied_destinations = value.get("destination_rules", candidate["destination_rules"])
    if not isinstance(supplied_destinations, dict):
        raise ValueError("destination_rules must be an object.")
    for country, supplied in supplied_destinations.items():
        key = _normalize_country(country)
        if key not in candidate["destination_rules"] or not isinstance(supplied, dict):
            raise ValueError(f"Unsupported destination rule: {country}.")
        _reject_unknown_keys(
            supplied,
            {
                "cambodian_balance_usd",
                "foreigner_balance_usd",
                "cambodian_evidence",
                "foreigner_evidence",
                "ties_to_cambodia_required",
                "special_cases",
            },
            f"{key} rule",
        )
        current = candidate["destination_rules"][key]
        for field in (
            "cambodian_balance_usd",
            "foreigner_balance_usd",
        ):
            if field in supplied:
                item = supplied[field]
                if item is not None and (
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not math.isfinite(float(item))
                    or item < 0
                ):
                    raise ValueError(f"{key}.{field} must be a non-negative number or null.")
                current[field] = float(item) if item is not None else None
        for field in ("cambodian_evidence", "foreigner_evidence"):
            if field in supplied:
                entries = supplied[field]
                if (
                    not isinstance(entries, list)
                    or any(item not in _EVIDENCE_KEYS for item in entries)
                ):
                    raise ValueError(f"{key}.{field} contains unsupported evidence types.")
                current[field] = list(dict.fromkeys(entries))
        if "ties_to_cambodia_required" in supplied:
            if not isinstance(supplied["ties_to_cambodia_required"], bool):
                raise ValueError(f"{key}.ties_to_cambodia_required must be boolean.")
            current["ties_to_cambodia_required"] = supplied["ties_to_cambodia_required"]
        if "special_cases" in supplied:
            cases = supplied["special_cases"]
            if key != "china":
                if cases not in ({}, None):
                    raise ValueError("special_cases can only be configured for China.")
                continue
            if not isinstance(cases, dict):
                raise ValueError("special_cases can only be configured for China.")
            for case_key, case_value in cases.items():
                if case_key not in current["special_cases"] or not isinstance(case_value, dict):
                    raise ValueError(f"Unsupported China special case: {case_key}.")
                _reject_unknown_keys(
                    case_value,
                    {"enabled", "required_evidence", "manual_review"},
                    f"{case_key}",
                )
                if not isinstance(case_value.get("enabled"), bool):
                    raise ValueError(f"{case_key}.enabled must be boolean.")
                required = case_value.get("required_evidence", [])
                if not isinstance(required, list) or any(
                    item not in _EVIDENCE_KEYS for item in required
                ):
                    raise ValueError(f"{case_key}.required_evidence is invalid.")
                if not isinstance(case_value.get("manual_review", True), bool):
                    raise ValueError(f"{case_key}.manual_review must be boolean.")
                current["special_cases"][case_key] = {
                    "enabled": case_value["enabled"],
                    "required_evidence": list(dict.fromkeys(required)),
                    "manual_review": case_value.get("manual_review", True),
                }

    universal = value.get("universal_rules", candidate["universal_rules"])
    if not isinstance(universal, dict):
        raise ValueError("universal_rules must be an object.")
    _reject_unknown_keys(
        universal,
        {
            "no_overstay_history",
            "minimum_passport_validity_months",
            "maximum_previous_visa_denials",
        },
        "universal_rules",
    )
    for field in ("no_overstay_history",):
        if field in universal and not isinstance(universal[field], bool):
            raise ValueError(f"universal_rules.{field} must be boolean.")
        if field in universal:
            candidate["universal_rules"][field] = universal[field]
    for field in ("minimum_passport_validity_months", "maximum_previous_visa_denials"):
        if field in universal:
            number = universal[field]
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise ValueError(f"universal_rules.{field} must be a non-negative integer.")
            candidate["universal_rules"][field] = number

    scoring = value.get("financial_scoring", candidate["financial_scoring"])
    if not isinstance(scoring, dict):
        raise ValueError("financial_scoring must be an object.")
    _reject_unknown_keys(
        scoring,
        {"weights", "targets", "risk_thresholds", "unexplained_deposit_penalty_points"},
        "financial_scoring",
    )
    weights = scoring.get("weights", candidate["financial_scoring"]["weights"])
    if not isinstance(weights, dict) or set(weights) != set(candidate["financial_scoring"]["weights"]):
        raise ValueError("financial_scoring.weights must define all seven scoring components.")
    normalized_weights: dict[str, float] = {}
    for key, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or weight < 0:
            raise ValueError(f"Financial score weight {key} must be non-negative.")
        normalized_weights[key] = float(weight)
    if abs(sum(normalized_weights.values()) - 100) > 0.01:
        raise ValueError("Financial score weights must total 100.")
    candidate["financial_scoring"]["weights"] = normalized_weights
    targets = scoring.get("targets", candidate["financial_scoring"]["targets"])
    if not isinstance(targets, dict) or set(targets) != set(candidate["financial_scoring"]["targets"]):
        raise ValueError("financial_scoring.targets must define every target.")
    normalized_targets: dict[str, float] = {}
    for key, target in targets.items():
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(float(target)) or target <= 0:
            raise ValueError(f"Financial score target {key} must be greater than zero.")
        normalized_targets[key] = float(target)
    candidate["financial_scoring"]["targets"] = normalized_targets
    risk_thresholds = scoring.get(
        "risk_thresholds", candidate["financial_scoring"]["risk_thresholds"]
    )
    if not isinstance(risk_thresholds, dict):
        raise ValueError("financial_scoring.risk_thresholds must be an object.")
    _reject_unknown_keys(
        risk_thresholds,
        {"low_minimum", "medium_minimum"},
        "financial_scoring.risk_thresholds",
    )
    low = risk_thresholds.get("low_minimum", 65)
    medium = risk_thresholds.get("medium_minimum", 40)
    if (
        isinstance(low, bool)
        or isinstance(medium, bool)
        or not isinstance(low, (int, float))
        or not isinstance(medium, (int, float))
        or not 0 <= medium <= low <= 100
    ):
        raise ValueError("Risk thresholds must satisfy 0 <= medium <= low <= 100.")
    candidate["financial_scoring"]["risk_thresholds"] = {
        "low_minimum": float(low),
        "medium_minimum": float(medium),
    }
    penalty = scoring.get(
        "unexplained_deposit_penalty_points",
        candidate["financial_scoring"]["unexplained_deposit_penalty_points"],
    )
    if isinstance(penalty, bool) or not isinstance(penalty, (int, float)) or not 0 <= penalty <= 100:
        raise ValueError("unexplained_deposit_penalty_points must be between 0 and 100.")
    candidate["financial_scoring"]["unexplained_deposit_penalty_points"] = float(penalty)

    bank_scoring = value.get(
        "bank_statement_scoring", candidate["bank_statement_scoring"]
    )
    if not isinstance(bank_scoring, dict):
        raise ValueError("bank_statement_scoring must be an object.")
    if set(bank_scoring.get("weights", {})) == {
        "three_month_balance_stability",
        "transaction_history_quality",
        "source_of_funds_consistency",
        "document_completeness_consistency",
    }:
        bank_scoring = {
            **bank_scoring,
            "weights": deepcopy(candidate["bank_statement_scoring"]["weights"]),
        }
    if bank_scoring.get("weights") == {
        "ending_balance": 15.75,
        "starting_balance": 15.75,
        "transaction_history": 6.75,
        "transaction_period": 6.75,
    }:
        bank_scoring = {
            **bank_scoring,
            "weights": deepcopy(candidate["bank_statement_scoring"]["weights"]),
        }
    _reject_unknown_keys(
        bank_scoring,
        {
            "minimum_monthly_closing_balance_usd",
            "full_balance_score_usd",
            "money_in_flag_threshold_usd",
            "money_out_flag_threshold_usd",
            "money_in_flag_deduction_percent",
            "money_out_flag_deduction_percent",
            "monthly_low_balance_flag_deduction_percent",
            "minimum_statement_period_days",
            "ending_balance_recent_withdrawal_days",
            "historical_pattern_deviation_ratio",
            "recent_large_deposit_days",
            "rapid_funds_outflow_days",
            "rapid_funds_outflow_ratio",
            "unverified_source_penalty_points",
            "deduct_pending_source_review",
            "maximum_transaction_history_penalty",
            "require_source_evidence_for_confirmation",
            "minimum_source_review_notes_length",
            "staff_review_threshold_usd",
            "staff_review_exchange_rates_to_usd",
            "weights",
        },
        "bank_statement_scoring",
    )
    bank_config = candidate["bank_statement_scoring"]
    for field in (
        "minimum_monthly_closing_balance_usd",
        "full_balance_score_usd",
        "money_in_flag_threshold_usd",
        "money_out_flag_threshold_usd",
    ):
        number = bank_scoring.get(field, bank_config[field])
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            or number <= 0
        ):
            raise ValueError(f"bank_statement_scoring.{field} must be greater than zero.")
        bank_config[field] = float(number)
    for field, maximum in (
        ("recent_large_deposit_days", 365),
        ("rapid_funds_outflow_days", 30),
        ("minimum_statement_period_days", 366),
        ("ending_balance_recent_withdrawal_days", 30),
        ("minimum_source_review_notes_length", 500),
    ):
        number = bank_scoring.get(field, bank_config[field])
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not (0 <= number <= maximum if field == "minimum_source_review_notes_length" else 1 <= number <= maximum)
        ):
            raise ValueError(
                f"bank_statement_scoring.{field} is outside its allowed range."
            )
        bank_config[field] = number
    for field in (
        "unverified_source_penalty_points",
        "maximum_transaction_history_penalty",
    ):
        number = bank_scoring.get(field, bank_config[field])
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            or number < 0
        ):
            raise ValueError(f"bank_statement_scoring.{field} must be non-negative.")
        bank_config[field] = float(number)
    if bank_config["unverified_source_penalty_points"] > 9.0:
        raise ValueError(
            "bank_statement_scoring.unverified_source_penalty_points cannot exceed the transaction-history category maximum."
        )
    bank_config["unverified_source_penalty_points"] = (
        SOURCE_UNVERIFIED_PENALTY_POINTS
    )
    if bank_config["maximum_transaction_history_penalty"] > 9.0:
        raise ValueError(
            "bank_statement_scoring.maximum_transaction_history_penalty cannot exceed the transaction-history category maximum."
        )
    for field in (
        "money_in_flag_deduction_percent",
        "money_out_flag_deduction_percent",
        "monthly_low_balance_flag_deduction_percent",
    ):
        number = bank_scoring.get(field, bank_config[field])
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            or not 0 <= number <= 100
        ):
            raise ValueError(
                f"bank_statement_scoring.{field} must be between 0 and 100."
            )
        bank_config[field] = 0.0
    require_evidence = bank_scoring.get(
        "require_source_evidence_for_confirmation",
        bank_config["require_source_evidence_for_confirmation"],
    )
    if not isinstance(require_evidence, bool):
        raise ValueError(
            "bank_statement_scoring.require_source_evidence_for_confirmation must be boolean."
        )
    bank_config["require_source_evidence_for_confirmation"] = require_evidence
    deduct_pending_source_review = bank_scoring.get(
        "deduct_pending_source_review",
        bank_config["deduct_pending_source_review"],
    )
    if not isinstance(deduct_pending_source_review, bool):
        raise ValueError(
            "bank_statement_scoring.deduct_pending_source_review must be boolean."
        )
    bank_config["deduct_pending_source_review"] = False
    staff_review_threshold = bank_scoring.get(
        "staff_review_threshold_usd", bank_config["staff_review_threshold_usd"]
    )
    if (
        isinstance(staff_review_threshold, bool)
        or not isinstance(staff_review_threshold, (int, float))
        or not math.isfinite(float(staff_review_threshold))
        or staff_review_threshold <= 0
    ):
        raise ValueError(
            "bank_statement_scoring.staff_review_threshold_usd must be greater than zero."
        )
    bank_config["staff_review_threshold_usd"] = float(staff_review_threshold)
    configured_rates = bank_scoring.get(
        "staff_review_exchange_rates_to_usd",
        bank_config["staff_review_exchange_rates_to_usd"],
    )
    if not isinstance(configured_rates, dict):
        raise ValueError(
            "bank_statement_scoring.staff_review_exchange_rates_to_usd must be an object."
        )
    normalized_rates = {"USD": 1.0}
    for currency, rate in configured_rates.items():
        code = str(currency).strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError(
                "bank_statement_scoring exchange-rate keys must be three-letter currency codes."
            )
        if (
            isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or not math.isfinite(float(rate))
            or rate <= 0
        ):
            raise ValueError(
                f"bank_statement_scoring exchange rate for {code} must be greater than zero."
            )
        if code == "USD" and not math.isclose(float(rate), 1.0):
            raise ValueError(
                "bank_statement_scoring USD exchange rate must equal 1."
            )
        normalized_rates[code] = float(rate)
    bank_config["staff_review_exchange_rates_to_usd"] = normalized_rates
    ratio = bank_scoring.get(
        "rapid_funds_outflow_ratio", bank_config["rapid_funds_outflow_ratio"]
    )
    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not math.isfinite(float(ratio))
        or not 0 < ratio <= 1
    ):
        raise ValueError(
            "bank_statement_scoring.rapid_funds_outflow_ratio must be greater than zero and at most one."
        )
    bank_config["rapid_funds_outflow_ratio"] = float(ratio)
    historical_ratio = bank_scoring.get(
        "historical_pattern_deviation_ratio",
        bank_config["historical_pattern_deviation_ratio"],
    )
    if (
        isinstance(historical_ratio, bool)
        or not isinstance(historical_ratio, (int, float))
        or not math.isfinite(float(historical_ratio))
        or historical_ratio < 1
    ):
        raise ValueError(
            "bank_statement_scoring.historical_pattern_deviation_ratio must be at least one."
        )
    bank_config["historical_pattern_deviation_ratio"] = float(historical_ratio)
    bank_weights = bank_scoring.get("weights", bank_config["weights"])
    if (
        not isinstance(bank_weights, dict)
        or set(bank_weights) != set(bank_config["weights"])
    ):
        raise ValueError(
            "bank_statement_scoring.weights must define all four bank scoring components."
        )
    normalized_bank_weights: dict[str, float] = {}
    for key, weight in bank_weights.items():
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(float(weight))
            or weight < 0
        ):
            raise ValueError(
                f"Bank statement score weight {key} must be non-negative."
            )
        normalized_bank_weights[key] = float(weight)
    if abs(sum(normalized_bank_weights.values()) - 45) > 0.01:
        raise ValueError("Bank statement score weights must total 45.")
    required_bank_weights = {
        key: float(weight)
        for key, weight in candidate["bank_statement_scoring"]["weights"].items()
    }
    if normalized_bank_weights != required_bank_weights:
        raise ValueError(
            "Bank statement score weights must be Ending Balance 22.50, "
            "Starting Balance 6.75, Transaction History 9.00, and "
            "Transaction Period 6.75."
        )
    bank_config["weights"] = normalized_bank_weights

    visa_risk = value.get("visa_risk_scoring", candidate["visa_risk_scoring"])
    if not isinstance(visa_risk, dict):
        raise ValueError("visa_risk_scoring must be an object.")
    _reject_unknown_keys(
        visa_risk,
        {
            "low_minimum",
            "medium_minimum",
            "very_low_balance_ratio",
            "very_low_balance_penalty",
            "low_balance_penalty",
            "no_travel_history_penalty",
            "limited_travel_history_maximum",
            "limited_travel_history_penalty",
            "no_employment_evidence_penalty",
            "sponsor_support_bonus",
            "fixed_deposit_bonus",
        },
        "visa_risk_scoring",
    )
    risk_config = candidate["visa_risk_scoring"]
    for field in ("low_minimum", "medium_minimum"):
        number = visa_risk.get(field, risk_config[field])
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            or not 0 <= number <= 100
        ):
            raise ValueError(f"visa_risk_scoring.{field} must be between 0 and 100.")
        risk_config[field] = float(number)
    if risk_config["medium_minimum"] > risk_config["low_minimum"]:
        raise ValueError("visa_risk_scoring medium_minimum cannot exceed low_minimum.")
    for field in (
        "very_low_balance_ratio",
        "very_low_balance_penalty",
        "low_balance_penalty",
        "no_travel_history_penalty",
        "limited_travel_history_penalty",
        "no_employment_evidence_penalty",
        "sponsor_support_bonus",
        "fixed_deposit_bonus",
    ):
        number = visa_risk.get(field, risk_config[field])
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            or not 0 <= number <= 100
        ):
            raise ValueError(f"visa_risk_scoring.{field} must be between 0 and 100.")
        risk_config[field] = float(number)
    history_maximum = visa_risk.get(
        "limited_travel_history_maximum",
        risk_config["limited_travel_history_maximum"],
    )
    if (
        isinstance(history_maximum, bool)
        or not isinstance(history_maximum, int)
        or history_maximum < 1
    ):
        raise ValueError("visa_risk_scoring.limited_travel_history_maximum must be a positive integer.")
    risk_config["limited_travel_history_maximum"] = history_maximum
    candidate["version"] = version
    candidate["currency"] = "USD"
    return candidate


def evaluate_business_rules(
    applicant: dict[str, Any],
    financial_analysis: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configuration = validate_rule_policy(policy or DEFAULT_RULE_POLICY)
    checks: list[dict[str, Any]] = []
    universal = configuration["universal_rules"]
    if universal["no_overstay_history"]:
        check = _bool_check(
            applicant.get("has_overstay_history"),
            "no_overstay_history",
            true_is_pass=False,
        )
        check["reason"] = (
            "No overstay history reported."
            if check["status"] == "pass"
            else "Overstay history reported."
            if check["status"] == "fail"
            else "Overstay history not provided."
        )
        checks.append(check)

    expiry = applicant.get("passport_expiry_date") or applicant.get("passportExpiryDate")
    expiry_date = None
    if isinstance(expiry, str):
        try:
            expiry_date = date.fromisoformat(expiry[:10])
        except ValueError:
            expiry_date = None
    if expiry_date is None:
        checks.append(
            {
                "code": "passport_validity",
                "status": "manual_review",
                "reason": "Passport expiry date is missing or invalid.",
                "minimum_months": universal["minimum_passport_validity_months"],
            }
        )
    else:
        required_expiry = date.today() + timedelta(
            days=round(universal["minimum_passport_validity_months"] * 30.4375)
        )
        passed = expiry_date >= required_expiry
        checks.append(
            {
                "code": "passport_validity",
                "status": "pass" if passed else "fail",
                "reason": (
                    "Passport meets the configured validity period."
                    if passed
                    else "Passport does not meet the configured validity period."
                ),
                "minimum_months": universal["minimum_passport_validity_months"],
            }
        )

    denials = applicant.get("previous_visa_denials")
    if isinstance(denials, bool) or not isinstance(denials, int) or denials < 0:
        checks.append(
            {
                "code": "previous_visa_denials",
                "status": "manual_review",
                "reason": "Previous visa denial count is missing or invalid.",
                "maximum": universal["maximum_previous_visa_denials"],
            }
        )
    else:
        passed = denials <= universal["maximum_previous_visa_denials"]
        checks.append(
            {
                "code": "previous_visa_denials",
                "status": "pass" if passed else "fail",
                "reason": (
                    "Denial count is within the configured limit."
                    if passed
                    else "Denial count exceeds the configured limit."
                ),
                "maximum": universal["maximum_previous_visa_denials"],
            }
        )

    destination = _normalize_country(
        applicant.get("destination_country") or applicant.get("destinationCountry")
    )
    rule = configuration["destination_rules"].get(destination)
    if not rule:
        checks.append(
            {
                "code": "destination_policy",
                "status": "manual_review",
                "reason": "No internal rules are configured for this destination.",
            }
        )
    else:
        nationality = applicant.get("nationality") or applicant.get("citizenship")
        nationality_known = bool(str(nationality or "").strip())
        if not nationality_known:
            checks.append(
                {
                    "code": "applicant_nationality",
                    "status": "manual_review",
                    "reason": "Nationality is required to select the applicable internal balance rule.",
                }
            )
            cambodian = False
        else:
            cambodian = _is_cambodian(nationality)
        if nationality_known:
            balance_key = (
                "cambodian_balance_usd" if cambodian else "foreigner_balance_usd"
            )
            evidence_key = (
                "cambodian_evidence" if cambodian else "foreigner_evidence"
            )
            balance_minimum = rule.get(balance_key)
            balance = _safe_float(financial_analysis.get("closing_balance"))
            currency = str(financial_analysis.get("currency") or "").upper()
            if balance_minimum is None:
                checks.append(
                    {
                        "code": "destination_balance",
                        "status": "manual_review",
                        "reason": "Balance requirement for this applicant group is not configured.",
                    }
                )
            elif currency != configuration["currency"]:
                checks.append(
                    {
                        "code": "destination_balance",
                        "status": "manual_review",
                        "reason": "A verified USD balance is required; no exchange rate was inferred.",
                        "required_usd": balance_minimum,
                    }
                )
            elif balance is None:
                checks.append(
                    {
                        "code": "destination_balance",
                        "status": "manual_review",
                        "reason": "Closing balance is missing or invalid.",
                        "required_usd": balance_minimum,
                    }
                )
            else:
                passed = balance >= balance_minimum
                checks.append(
                    {
                        "code": "destination_balance",
                        "status": "pass" if passed else "fail",
                        "reason": "Configured balance threshold met." if passed else "Configured balance threshold not met.",
                        "required_usd": balance_minimum,
                        "observed_usd": balance,
                    }
                )

            evidence_types = rule.get(evidence_key, [])
            if evidence_types:
                checks.append(
                    _evidence_check(
                        applicant,
                        evidence_types,
                        (
                            "employment_or_business_evidence"
                            if set(evidence_types).issubset(
                                {"employment_letter", "business_evidence"}
                            )
                            and len(evidence_types) > 1
                            else evidence_types[0]
                        ),
                    )
                )
        if rule.get("ties_to_cambodia_required"):
            present = _evidence_present(applicant, "ties_to_cambodia")
            checks.append(
                {
                    "code": "ties_to_cambodia",
                    "status": "pass" if present else "fail" if present is False else "manual_review",
                    "reason": "Proof of ties provided." if present else "Proof of ties not provided." if present is False else "Proof of ties status not provided.",
                }
            )

        if destination == "china":
            age = _safe_float(applicant.get("age"))
            is_female = str(applicant.get("gender") or "").strip().casefold() in {
                "female",
                "f",
                "woman",
            }
            known_first_travel_case = (
                isinstance(applicant.get("first_time_traveller"), bool)
                and bool(str(applicant.get("gender") or "").strip())
                and age is not None
            )
            if not known_first_travel_case:
                checks.append(
                    {
                        "code": "first_time_female_18_30",
                        "status": "manual_review",
                        "reason": "Age, gender, and first-time travel history must be confirmed for configured China-specific screening.",
                    }
                )
            elif (
                applicant.get("first_time_traveller") is True
                and is_female
                and 18 <= age <= 30
            ):
                checks.extend(
                    _evaluate_special_case(
                        "first_time_female_18_30",
                        rule["special_cases"].get("first_time_female_18_30", {}),
                        applicant,
                    )
                )
            marital_status = applicant.get("marital_status")
            if not isinstance(marital_status, str) or not marital_status.strip():
                checks.append(
                    {
                        "code": "married_applicant",
                        "status": "manual_review",
                        "reason": "Marital status must be confirmed for configured China-specific screening.",
                    }
                )
            elif marital_status.strip().casefold() == "married":
                checks.extend(
                    _evaluate_special_case(
                        "married_applicant",
                        rule["special_cases"].get("married_applicant", {}),
                        applicant,
                    )
                )

    risk_assessment = _evaluate_visa_risk(
        applicant,
        financial_analysis,
        rule if rule else None,
        configuration["visa_risk_scoring"],
        configuration["currency"],
    )
    statuses = {check["status"] for check in checks}
    result_status = (
        "manual_review"
        if "manual_review" in statuses
        else "requirements_not_met"
        if "fail" in statuses
        else "requirements_met"
    )
    return {
        "status": result_status,
        "checks": checks,
        "risk_assessment": risk_assessment,
        "disclaimer": (
            "Internal configurable business rules only; not official embassy "
            "requirements or a visa decision."
        ),
    }


def _evaluate_visa_risk(
    applicant: dict[str, Any],
    financial_analysis: dict[str, Any],
    country_rule: dict[str, Any] | None,
    config: dict[str, Any],
    policy_currency: str,
) -> dict[str, Any]:
    score = 100.0
    adjustments: list[dict[str, Any]] = []
    evaluated_factors: list[str] = []
    unknown_factors: list[str] = []
    balance = _safe_float(financial_analysis.get("closing_balance"))
    currency = str(financial_analysis.get("currency") or "").upper()
    nationality = applicant.get("nationality") or applicant.get("citizenship")
    if country_rule and str(nationality or "").strip() and currency == policy_currency:
        balance_key = (
            "cambodian_balance_usd"
            if _is_cambodian(nationality)
            else "foreigner_balance_usd"
        )
        required_balance = country_rule.get(balance_key)
        if balance is not None and required_balance:
            evaluated_factors.append("balance")
            if balance < required_balance * float(config["very_low_balance_ratio"]):
                points = float(config["very_low_balance_penalty"])
                score -= points
                adjustments.append(
                    {"factor": "very_low_balance", "points": -points}
                )
            elif balance < required_balance:
                points = float(config["low_balance_penalty"])
                score -= points
                adjustments.append({"factor": "low_balance", "points": -points})
        else:
            unknown_factors.append("balance")
    else:
        unknown_factors.append("balance")

    travel_count = applicant.get("travel_history_count")
    if (
        isinstance(travel_count, int)
        and not isinstance(travel_count, bool)
        and travel_count >= 0
    ):
        evaluated_factors.append("travel_history")
        if travel_count == 0:
            points = float(config["no_travel_history_penalty"])
            score -= points
            adjustments.append({"factor": "no_travel_history", "points": -points})
        elif travel_count <= int(config["limited_travel_history_maximum"]):
            points = float(config["limited_travel_history_penalty"])
            score -= points
            adjustments.append({"factor": "limited_travel_history", "points": -points})
    else:
        unknown_factors.append("travel_history")

    employment_present = _evidence_present(applicant, "employment_letter")
    business_present = _evidence_present(applicant, "business_evidence")
    if employment_present is False and business_present is False:
        evaluated_factors.append("employment_or_business_evidence")
        points = float(config["no_employment_evidence_penalty"])
        score -= points
        adjustments.append({"factor": "no_employment_or_business_evidence", "points": -points})
    elif employment_present is True or business_present is True:
        evaluated_factors.append("employment_or_business_evidence")
    else:
        unknown_factors.append("employment_or_business_evidence")

    for field, key in (
        ("sponsor_support", "sponsor_support_bonus"),
        ("fixed_deposit", "fixed_deposit_bonus"),
    ):
        evidence = _evidence_present(applicant, field)
        if evidence is not None:
            evaluated_factors.append(field)
        else:
            unknown_factors.append(field)
        if evidence is True:
            points = float(config[key])
            score += points
            adjustments.append({"factor": field, "points": points})

    score = max(0.0, min(100.0, score))
    low_minimum = float(config["low_minimum"])
    medium_minimum = float(config["medium_minimum"])
    sufficient_data = bool(evaluated_factors)
    return {
        "score": round(score, 2) if sufficient_data else None,
        "risk_category": (
            "INSUFFICIENT_DATA"
            if not sufficient_data
            else "LOW"
            if score >= low_minimum
            else "MEDIUM"
            if score >= medium_minimum
            else "HIGH"
        ),
        "status": "evaluated" if not unknown_factors else "partial",
        "adjustments": adjustments,
        "evaluated_factors": evaluated_factors,
        "unknown_factors": unknown_factors,
        "scope": "Internal rules-based readiness score; separate from financial score and model probability.",
        "model_overlap_policy": "Not combined with ML estimates; shown as a separate internal score.",
        "disclaimer": "Not a visa approval probability or official decision.",
    }


def _evaluate_special_case(
    case_name: str, config: dict[str, Any], applicant: dict[str, Any]
) -> list[dict[str, Any]]:
    if not config.get("enabled"):
        return [
            {
                "code": f"china_{case_name}",
                "status": "manual_review",
                "reason": "This China-specific rule is not configured; verify requirements with an administrator.",
            }
        ]
    checks = [
        {
            "code": f"china_{case_name}",
            "status": "manual_review" if config.get("manual_review", True) else "pass",
            "reason": "Configured special-case review applies.",
        }
    ]
    for evidence in config.get("required_evidence", []):
        present = _evidence_present(applicant, evidence)
        checks.append(
            {
                "code": f"china_{case_name}_{evidence}",
                "status": "pass" if present else "fail" if present is False else "manual_review",
                "reason": "Evidence provided." if present else "Required evidence not provided." if present is False else "Evidence status not provided.",
            }
        )
    return checks


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
