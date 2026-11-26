"""Shared immutable assessment context and deterministic prediction invalidation.

Fingerprints contain hashes, not exposed source text. Volatile workflow locks and
prediction timestamps are intentionally excluded so writing a result cannot make
that same result stale.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LEGACY_FEATURE_SCHEMA = "visa-legacy-v1"
ABA_FEATURE_SCHEMA = "visa-aba-evidence-v1"


def _json_default(value: Any):
    if isinstance(value, datetime):
        normalized=value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return normalized.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def digest(value: Any) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def policy_snapshot(application: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = application.get("evidencePolicySnapshot")
    return snapshot if isinstance(snapshot, dict) and isinstance(snapshot.get("requirements"), list) else None


def application_requirements(application: dict[str, Any], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshot = policy_snapshot(application)
    application_snapshot = application.get("requirementSnapshot")
    rows = (
        snapshot["requirements"]
        if snapshot is not None
        else application_snapshot
        if isinstance(application_snapshot, list) and application_snapshot
        else current
    )
    return [{**row, "requirementId": str(row.get("requirementId") or row.get("_id") or "")} for row in rows]


def canonical_summary(application, requirements, documents, not_applicable=None, **kwargs):
    snapshot = policy_snapshot(application)
    if snapshot is None:
        from .document_metrics import summarize_documents
        return summarize_documents(requirements, documents, not_applicable, **kwargs)
    from .evidence_scoring import calculate_evidence_assessment
    return calculate_evidence_assessment(snapshot["requirements"], documents, policy=snapshot, application=application)


def prediction_fingerprint(application, customer, requirements, documents, model_version=None, financial_policy=None):
    app_fields = {key: application.get(key) for key in (
        "_id", "visaTypeId", "visaType", "countryId", "destination", "visaCategory", "category",
        "policyVersionId", "evidencePolicySnapshot", "requirementSnapshot", "selectedBankDocumentId", "selectedBankAnalysisRevision",
        "notApplicableRequirements", "applicabilityDecisions", "financialPolicySnapshot", "travelStartDate", "travelEndDate",
        "primaryDocumentSelections", "applicantContext", "assessmentTimezone",
        "verifiedPredictionInputs",
    )}
    # Document timestamps and all evidence/review revisions affect the result;
    # private content is represented only inside this non-reversible hash.
    evidence = [{k: v for k, v in d.items() if k not in {
        "ocrProgressPercent", "ocrProgress", "ocrPagesProcessed", "lastAccessedAt", "predictionStatus",
    }} for d in documents if str(d.get("status") or "").upper() not in {"DELETED", "REPLACED"}]
    evidence.sort(key=lambda d: str(d.get("_id") or ""))
    try:
        assessment_zone=ZoneInfo((policy_snapshot(application) or {}).get("timezone") or "Asia/Phnom_Penh")
    except ZoneInfoNotFoundError:
        assessment_zone=timezone.utc
    # Recency, expiry, and effective-date gates can change without any file edit.
    # Reuse is therefore scoped to the local policy assessment day.
    assessment_day=datetime.now(assessment_zone).date().isoformat()
    return digest({"application": app_fields, "customer": customer, "requirements": requirements, "assessmentDay":assessment_day,
                   "documents": evidence, "modelVersion": model_version, "financialPolicy": financial_policy})


def selected_bank_document(application, documents):
    candidates = [d for d in documents if str(d.get("requirementCode") or "").casefold() == "bank_statement"
                  or isinstance(d.get("abaStatementAnalysis"), dict)]
    selected = str(application.get("selectedBankDocumentId") or "")
    if selected:
        return next((d for d in candidates if str(d.get("_id") or "") == selected), None)
    # No arbitrary "first row" selection. A unique candidate is unambiguous,
    # while multiple candidates require a recorded primary choice.
    return candidates[0] if len(candidates) == 1 else None


def bank_verified_for_prediction(document):
    if not isinstance(document, dict) or str(document.get("status") or "").upper() != "VERIFIED":
        return False
    aba = document.get("abaStatementAnalysis")
    if not isinstance(aba, dict):
        return True  # legacy documents retain legacy-model compatibility
    return (aba.get("reconciliationStatus") == "RECONCILED" and aba.get("reviewStatus") == "VERIFIED"
            and aba.get("statementStrengthScore") is not None
            and not aba.get("issues"))
