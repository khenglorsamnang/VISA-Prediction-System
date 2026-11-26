"""Single, native-first ABA integration boundary. Currency conversion happens here only.

The canonical ABA result retains integer minor units. Legacy fields are explicitly
major-unit compatibility diagnostics and never authorize the ABA strength score.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any, Callable
from uuid import uuid4

import fitz

from .aba_reader.parser import ParseError, parse_pdf
from .aba_reader.analysis import analyze
from .aba_reader.scoring import DEFAULT_CONFIG

SCHEMA_VERSION = "aba-statement-v1"
MAPPING_VERSION = "bank-linear-1-to-10-v1"


def minor_to_major(value: Any) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)) / Decimal(100))


def _review_blockers(
    result: dict[str, Any], statement: dict[str, Any]
) -> list[dict[str, Any]]:
    currency = statement.get("currency")
    blockers = []
    for issue in result.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        blocker = {
            "code": issue.get("code") or "unresolved_evidence",
            "message": issue.get("message") or "An extraction or financial check requires review.",
        }
        for key in ("field", "page", "row", "pages"):
            if issue.get(key) is not None:
                blocker[key] = issue[key]
        if issue.get("code") == "summary_field_conflict" and issue.get("field"):
            candidates = (
                (statement.get("metadata_sources") or {})
                .get(issue["field"], {})
                .get("candidates", [])
            )
            blocker["candidates"] = [
                {
                    "page": candidate.get("page"),
                    "raw": candidate.get("raw"),
                    "value": minor_to_major(candidate.get("value")),
                    "currency": currency,
                }
                for candidate in candidates
            ]
        for key in ("expected", "actual", "difference"):
            if isinstance(issue.get(key), (int, float)):
                blocker[key] = minor_to_major(issue[key])
                blocker[f"{key}Units"] = "major"
        if any(key in blocker for key in ("expected", "actual", "difference")):
            blocker["currency"] = currency
        blockers.append(blocker)

    score = result.get("score") or {}
    metrics = result.get("metrics") or {}
    config = score.get("config") or DEFAULT_CONFIG
    if score.get("provisional"):
        days = metrics.get("days")
        final_days = config.get("final_days")
        blockers.append({
            "code": "provisional_history",
            "message": (
                f"The statement score is provisional: {days} days are available; "
                f"{final_days} days are required for a final score."
            ),
        })
    elif score.get("value") is None and not blockers and score.get("label"):
        blockers.append({
            "code": "score_unavailable",
            "message": str(score["label"]),
        })
    return blockers


def normalized_bank_quality(score: Any) -> str | None:
    if score is None or isinstance(score, bool):
        return None
    try:
        value = Decimal(str(score))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or not Decimal(1) <= value <= Decimal(10):
        return None
    return str((value - 1) / 9)


def detect_aba_candidate(content: bytes, password: str = "") -> bool:
    """Native non-ABA PDFs fall through to the existing multi-bank OCR pipeline.

    Image-only and encrypted PDFs are attempted locally: their bank cannot yet be
    established from native text. They are never sent externally by this function.
    """
    if b"%PDF-" not in content[:1024]:
        return False
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            if doc.needs_pass and not doc.authenticate(password):
                return True
            if not len(doc):
                return False
            text = doc[0].get_text().upper()
            if len(text.strip()) < 30:
                return True
            return "ABA" in text and "ACCOUNT STATEMENT" in text
    except Exception:
        return False


def analysis_from_statement(statement: dict[str, Any], *, source: dict[str, Any],
                            config: dict[str, Any] | None = None,
                            previous: dict[str, Any] | None = None) -> dict[str, Any]:
    result = analyze(statement, config or (previous or {}).get("scoringConfig") or DEFAULT_CONFIG)
    final_score = result["score"].get("value")
    previous = previous or {}
    # Re-analysis always requires fresh review; neither an old manual override nor
    # a legacy one-dollar tolerance releases exact ABA reconciliation blockers.
    return {
        "schemaVersion": SCHEMA_VERSION,
        "analysisRevision": str(uuid4()),
        "documentRevision": source["sha256"],
        "documentId": source.get("documentId"),
        "applicationId": source.get("applicationId"),
        "requirementId": source.get("requirementId"),
        "ocrStatus": "OCR_COMPLETED",
        "ocrConfidence": None,
        "statementStrengthScore": final_score,
        "normalizedQuality": normalized_bank_quality(final_score),
        "extractionStatus": (
            "NEEDS_REVIEW"
            if statement.get("extraction_issues")
            or any(
                issue.get("code")
                in {"missing_fields", "unreadable_row", "ordering", "row_date"}
                for issue in result.get("issues", [])
            )
            else "EXTRACTED"
        ),
        "financialEvidenceStatus": (
            "RECONCILED" if result.get("status") == "Reconciled" else "NEEDS_REVIEW"
        ),
        "monthlyBalanceVerification": {
            "status": (
                "INSUFFICIENT_DATA"
                if not result.get("monthly")
                else "PASS"
                if result.get("status") == "Reconciled"
                else "NEEDS_REVIEW"
            ),
            "months": [
                {
                    "month": item["month"],
                    "closingBalance": minor_to_major(item.get("ending")),
                    "currency": statement.get("currency"),
                    "sourceDate": item.get("source_date"),
                    "sourcePage": item.get("source_page"),
                    "sourceTransaction": item.get("source_transaction"),
                    "evidenceType": item.get("evidence_type"),
                }
                for item in result.get("monthly", [])
            ],
        },
        "accountHolderExtractionStatus": (
            "EXTRACTED" if statement.get("holder") else "MISSING"
        ),
        "fieldEvidence": deepcopy(statement.get("metadata_sources") or {}),
        "reviewBlockers": _review_blockers(result, statement),
        "mappingVersion": MAPPING_VERSION,
        "modelVersion": result["score"]["config"]["version"],
        "scoringConfig": result["score"]["config"],
        "reconciliationStatus": "RECONCILED" if result["status"] == "Reconciled" else "NEEDS_REVIEW",
        "reviewStatus": "PENDING_REVIEW",
        "ownerRelationship": previous.get("ownerRelationship", "UNASSESSED"),
        "ownerRelationshipConfirmed": False,
        "authenticityStatus": "NOT_VERIFIED",
        "statement": statement,
        "metrics": result.get("metrics"),
        "daily": result.get("daily", []),
        "monthly": result.get("monthly", []),
        "transactions": statement.get("transactions", []),
        "checks": result.get("checks", []),
        "issues": result.get("issues", []),
        "score": result["score"],
        "audit": list(previous.get("audit") or []),
        "source": {**source, "pages": statement.get("pages"), "units": "minor", "currency": statement.get("currency")},
        "calculatedAt": datetime.now(timezone.utc).isoformat(),
        "availableBalance": "Unknown: posted balances may exclude holds and pending withdrawals.",
    }


def analyze_aba_pdf(content: bytes, filename: str, *, password: str = "", document_id: str | None = None,
                    application_id: str | None = None, requirement_id: str | None = None,
                    progress: Callable[[int, int], None] | None = None,
                    cancelled: Callable[[], bool] | None = None,
                    force_ocr: bool = False) -> dict[str, Any] | None:
    if not detect_aba_candidate(content, password):
        return None
    source = {"sha256": hashlib.sha256(content).hexdigest(), "filename": filename,
              "documentId": document_id, "applicationId": application_id, "requirementId": requirement_id}
    try:
        statement = parse_pdf(content, filename, password=password, progress=progress,
                              cancelled=cancelled, force_ocr=force_ocr)
    except ParseError as exc:
        message = str(exc)
        return {
            "schemaVersion": SCHEMA_VERSION, "analysisRevision": str(uuid4()), "documentRevision": source["sha256"],
            "documentId": document_id, "applicationId": application_id, "requirementId": requirement_id,
            "statementStrengthScore": None, "normalizedQuality": None, "modelVersion": DEFAULT_CONFIG["version"],
            "scoringConfig": deepcopy(DEFAULT_CONFIG), "mappingVersion": MAPPING_VERSION,
            "reconciliationStatus": "NEEDS_REVIEW", "reviewStatus": "PENDING_REVIEW",
            "ocrStatus": "OCR_FAILED", "ocrConfidence": None,
            "ownerRelationship": "UNASSESSED", "ownerRelationshipConfirmed": False,
            "extractionStatus": "NEEDS_REVIEW", "financialEvidenceStatus": "NEEDS_REVIEW",
            "monthlyBalanceVerification": {"status": "INSUFFICIENT_DATA", "months": []},
            "accountHolderExtractionStatus": "MISSING",
            "fieldEvidence": {}, "reviewBlockers": [{"code": "extraction_failed", "message": message}],
            "authenticityStatus": "NOT_VERIFIED", "source": {**source, "units": "minor"},
            "statement": None, "metrics": None, "daily": [], "monthly": [], "transactions": [], "checks": [],
            "issues": [{"code": "password_required" if message.startswith("PASSWORD_REQUIRED") else "extraction_failed", "message": message}],
            "score": {"value": None, "label": "Needs review", "components": [], "config": deepcopy(DEFAULT_CONFIG)}, "audit": [],
            "calculatedAt": datetime.now(timezone.utc).isoformat(),
        }
    if any(issue.get("code") == "unsupported_bank" for issue in statement.get("extraction_issues", [])):
        return None
    return analysis_from_statement(statement, source=source)


def aba_to_legacy_ocr(analysis: dict[str, Any]) -> dict[str, Any]:
    statement = analysis.get("statement") or {}
    metrics = analysis.get("metrics") or {}
    currency = statement.get("currency")
    rows = []
    for t in statement.get("transactions", []):
        incoming, outgoing = t.get("money_in"), t.get("money_out")
        rows.append({
            "date": t["date"], "description": t.get("description", ""),
            "credit": minor_to_major(incoming), "debit": minor_to_major(outgoing),
            "amount": minor_to_major(incoming if incoming else outgoing),
            "direction": "credit" if incoming else "debit", "currency": currency,
            "balance": minor_to_major(t.get("balance")), "page": t["page"],
            "reference": t.get("reference"), "sourceRowId": t["id"], "bbox": t.get("bbox"),
            "sourceUnits": "minor", "units": "major", "sourceAnalysisRevision": analysis["analysisRevision"],
        })
    fields = {
        "bankName": statement.get("bank"), "accountHolderName": statement.get("holder"),
        "accountNumber": statement.get("account"), "currency": currency,
        "openingBalance": minor_to_major(statement.get("opening")),
        "closingBalance": minor_to_major(statement.get("ending")),
        "statementStartDate": statement.get("start"), "statementEndDate": statement.get("end"),
        "deposits": minor_to_major(statement.get("reported_in")), "withdrawals": minor_to_major(statement.get("reported_out")),
        "averageBalance": minor_to_major(metrics.get("average")), "transactionHistory": rows,
        "abaEvidenceConvention": "Inclusive calendar-day closing posted balances; ABA v1",
        # These are derived, never represented as explicit printed monthly closings.
        "derivedMonthlyBalances": [{**m, "evidenceType": "DERIVED_DAILY_CLOSING", "units": "minor"} for m in analysis.get("monthly", [])],
    }
    pages = statement.get("pages") or 0
    return {"fields": fields, "extracted_text": "", "confidence": None,
            "status": "success" if statement else "failed", "filename": analysis["source"]["filename"],
            "pages": [{"page": n+1, "method": "embedded_text" if method == "Text" else "ocr", "status": "OCR_COMPLETED"} for n,method in enumerate(statement.get("methods", []))],
            "metrics": {"page_count": pages, "pages_processed": pages, "source": "ABA_NATIVE_LOCAL", "units": "major"},
            "warnings": ["Legacy diagnostics are separate from the exact ABA 1–10 strength score; no numeric OCR confidence was invented."],
            "failed_pages": []}


def apply_correction(analysis: dict[str, Any], payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if not analysis.get("statement"):
        raise ValueError("Retry extraction before correcting fields.")
    if payload.get("analysisRevision") != analysis["analysisRevision"]:
        raise ValueError("Analysis changed. Reload before applying a correction.")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 8:
        raise ValueError("A source-evidence reason of at least 8 characters is required.")
    statement = deepcopy(analysis["statement"])
    row_id, field = payload.get("row"), str(payload.get("field") or "")
    if row_id is None:
        target = statement
        allowed = {"opening", "ending", "reported_in", "reported_out", "start", "end", "currency"}
    else:
        target = next((t for t in statement["transactions"] if t["id"] == row_id), None)
        allowed = {"money_in", "money_out", "balance", "date"}
    if target is None or field not in allowed:
        raise ValueError("Choose a supported field or transaction.")
    value = str(payload.get("value") or "")
    original = target.get(field)
    if field in {"date", "start", "end"}:
        from datetime import date
        corrected = date.fromisoformat(value).isoformat()
    elif field == "currency":
        corrected = value.upper()
        if corrected not in {"USD", "KHR"}:
            raise ValueError("Supported source currencies are USD and KHR.")
    else:
        amount = Decimal(value.replace(",", ""))
        if not amount.is_finite() or amount.as_tuple().exponent < -2:
            raise ValueError("Enter a finite amount with at most two decimal places, in major units.")
        corrected = int(amount * 100)
    if original == corrected:
        raise ValueError("The new value matches the current value.")
    target[field] = corrected
    event = {"type": "EXTRACTION_CORRECTION", "row": row_id, "field": field,
             "original": original, "corrected": corrected, "reason": reason,
             "actorId": actor_id, "at": datetime.now(timezone.utc).isoformat(),
             "sourceResolutionConfirmed": payload.get("sourceResolutionConfirmed", payload.get("sourceResolution")) is True}
    statement.setdefault("audit", []).append(event)
    if not event["sourceResolutionConfirmed"]:
        statement.setdefault("extraction_issues", []).append({"code": "correction_requires_resolution", "message": "A corrected financial field requires a documented source resolution."})
    result = analysis_from_statement(statement, source=analysis["source"], previous=analysis)
    result["audit"].append(event)
    return result


def reviewed_analysis(analysis: dict[str, Any], payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if payload.get("analysisRevision") != analysis.get("analysisRevision"):
        raise ValueError("Analysis changed. Reload before reviewing.")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 8:
        raise ValueError("A review reason of at least 8 characters is required.")
    status = str(payload.get("status") or "").upper()
    if status not in {"VERIFIED", "REJECTED", "NEEDS_REVIEW"}:
        raise ValueError("Choose VERIFIED, REJECTED, or NEEDS_REVIEW.")
    result = deepcopy(analysis)
    if payload.get("resolveCorrections") is True:
        if not payload.get("sourceResolutionConfirmed"):
            raise ValueError("Confirm the documented source resolution first.")
        statement = deepcopy(result.get("statement"))
        if not statement:
            raise ValueError("Extraction must complete before source resolution.")
        statement["extraction_issues"] = [i for i in statement.get("extraction_issues", []) if i["code"] != "correction_requires_resolution"]
        result = analysis_from_statement(statement, source=analysis["source"], previous=analysis)
    if status == "VERIFIED":
        if result.get("statementStrengthScore") is None or result.get("reconciliationStatus") != "RECONCILED" or result.get("score", {}).get("provisional"):
            raise ValueError("Exact reconciliation and sufficient non-provisional history must pass before verification.")
    result["reviewStatus"] = status
    result["analysisRevision"] = str(uuid4())
    result["audit"].append({"type": "STAFF_REVIEW", "status": status, "reason": reason, "actorId": actor_id,
                            "at": datetime.now(timezone.utc).isoformat()})
    return result


def decrypted_render_bytes(content: bytes, password: str) -> bytes | None:
    with fitz.open(stream=content, filetype="pdf") as doc:
        if doc.needs_pass and doc.authenticate(password):
            return doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
    return None
