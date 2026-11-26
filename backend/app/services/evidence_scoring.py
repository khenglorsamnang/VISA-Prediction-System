"""Canonical evidence-v3 ledger. Policy points are not approval probabilities.

Pure calculation: no database writes, model inference, external calls or OCR.
All policy arithmetic is Decimal and all diagnostic money stays in reader units.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from copy import deepcopy
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ..api.document_workflow import has_completed_ocr
from .photo_validation import is_photo_document

VERSION = "evidence-v3"
SCORING_FIELDS = (
    "scoringMode", "minimumEarnedPoints", "maximumEarnedPoints", "qualityScorer",
    "qualityScorerVersion", "mappingVersion", "qualityRubric", "alternativeGroupId",
    "applicabilityRule", "requiredForFinalAssessment", "documentSelectionStrategy",
    "policyVersionId", "sourceKind", "sourceReviewedAt", "sourceReviewedBy",
    "evidenceRules",
)
INACTIVE = {"DELETED", "REPLACED"}
D = Decimal


def number(value: Any, *, name: str = "Points") -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return result


def displayed(value: Decimal) -> float:
    return float(value.quantize(D(".01"), rounding=ROUND_HALF_UP))


def requirement_id(requirement: dict[str, Any]) -> str:
    return str(requirement.get("requirementId") or requirement.get("_id") or requirement.get("code") or "")


def normalize_scoring_fields(requirement: dict[str, Any]) -> dict[str, Any]:
    """Validate a single editable requirement, retaining legacy fields."""
    result = deepcopy(requirement)
    maximum = number(result.get("maximumEarnedPoints", result.get("weight", 0)), name="Maximum points")
    minimum = number(result.get("minimumEarnedPoints", 0), name="Minimum points")
    if not D(0) <= minimum <= maximum or maximum > D(100):
        raise ValueError("Points must be between 0 and 100.")
    if "weight" in result and "maximumEarnedPoints" in result and number(result["weight"]) != maximum:
        raise ValueError("weight is an alias of maximumEarnedPoints and cannot differ")
    mode = str(result.get("scoringMode", "FIXED")).upper()
    if mode not in {"FIXED", "DYNAMIC"}:
        raise ValueError("Scoring mode must be FIXED or DYNAMIC")
    result.update(scoringMode=mode, minimumEarnedPoints=str(minimum), maximumEarnedPoints=str(maximum), weight=float(maximum))
    if "requiredForFinalAssessment" in result and not isinstance(result["requiredForFinalAssessment"], bool):
        raise ValueError("requiredForFinalAssessment must be true or false")
    strategy = result.get("documentSelectionStrategy", "REVIEWER_SELECTED_PRIMARY")
    if strategy not in {"REVIEWER_SELECTED_PRIMARY", "LATEST_REVISION"}:
        raise ValueError("Unsupported document selection strategy")
    result["documentSelectionStrategy"] = strategy
    for flag in ("required", "conditional", "allowMultiple"):
        if flag in result and not isinstance(result[flag], bool):
            raise ValueError(f"{flag} must be true or false")
    if result.get("sourceKind", "INTERNAL") not in {"INTERNAL", "OFFICIAL"}:
        raise ValueError("sourceKind must be INTERNAL or OFFICIAL")
    for key in ("qualityScorerVersion", "mappingVersion", "alternativeGroupId"):
        if result.get(key) is not None and not isinstance(result[key], str):
            raise ValueError(f"{key} must be a string")
    rules = result.get("evidenceRules") or {}
    if not isinstance(rules, dict) or set(rules) - {"minimumDays", "maximumAgeDays"}:
        raise ValueError("Supported evidenceRules are minimumDays and maximumAgeDays")
    for key, value in rules.items():
        amount = number(value, name=key)
        if amount < 0 or amount != amount.to_integral_value():
            raise ValueError(f"{key} must be a nonnegative whole number")
    rule = result.get("applicabilityRule")
    if rule is not None:
        if not isinstance(rule, dict) or not isinstance(rule.get("field"), str) or not rule.get("field") or rule.get("operator", "eq") not in {"eq", "in"} or "value" not in rule:
            raise ValueError("Applicability must contain field and operator eq/in")
        if rule.get("operator") == "in" and not isinstance(rule.get("value"), list):
            raise ValueError("An in applicability rule requires a value list")
    return result


def validate_profile_requirements(requirements: Any) -> list[dict[str, Any]]:
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("A profile needs at least one requirement")
    normalized = [normalize_scoring_fields(r) for r in requirements if isinstance(r, dict)]
    if len(normalized) != len(requirements):
        raise ValueError("Requirements must be objects")
    seen = set()
    seen_names = set()
    seen_codes = set()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in normalized:
        identity = requirement_id(r)
        if not identity or identity in seen:
            raise ValueError("Every requirement needs a unique requirementId or code")
        seen.add(identity)
        name = str(r.get("name") or "").strip().casefold()
        code = str(r.get("code") or "").strip().casefold()
        if name and name in seen_names:
            raise ValueError("Requirement names must be unique")
        if code and code in seen_codes:
            raise ValueError("Requirement codes must be unique")
        if name:
            seen_names.add(name)
        if code:
            seen_codes.add(code)
        r["requirementId"] = identity
        groups[str(r.get("alternativeGroupId") or identity)].append(r)
        if r["scoringMode"] == "DYNAMIC":
            scorer = r.get("qualityScorer")
            if scorer not in {"ABA_STATEMENT_V1", "MANUAL_RUBRIC"}:
                raise ValueError("Dynamic requirements need a supported quality scorer")
            r.setdefault("qualityScorerVersion", "1.0")
            if not r["qualityScorerVersion"].strip():
                raise ValueError("A quality scorer version is required")
            if scorer == "ABA_STATEMENT_V1":
                r.setdefault("mappingVersion", "bank-linear-1-to-10-v1")
                if r["mappingVersion"] != "bank-linear-1-to-10-v1":
                    raise ValueError("Unsupported bank normalization mapping version")
            if scorer == "MANUAL_RUBRIC":
                rubric = r.get("qualityRubric")
                if not isinstance(rubric, dict) or not rubric.get("version"):
                    raise ValueError("Manual dynamic requirements need a versioned rubric")
                criteria = rubric.get("criteria", [])
                levels = rubric.get("levels", [])
                if not isinstance(criteria, list) or not isinstance(levels, list) or any(not isinstance(item, dict) for item in criteria + levels):
                    raise ValueError("Rubric criteria and levels must be lists of objects")
                if bool(criteria) == bool(levels):
                    raise ValueError("A manual rubric needs explicit criteria or quality levels")
                if criteria:
                    ids = [str(c.get("id") or "") for c in criteria]
                    if len(set(ids)) != len(ids) or any(not i for i in ids) or any(number(c.get("maximumPoints", 0)) <= 0 for c in criteria):
                        raise ValueError("Rubric criteria need unique IDs and positive maximum points")
                if levels:
                    ids = [str(level.get("id") or "") for level in levels]
                    if len(set(ids)) != len(ids) or any(not identity for identity in ids) or any(not 0 <= number(level.get("quality")) <= 1 for level in levels):
                        raise ValueError("Rubric levels need unique IDs and quality between zero and one")
        if r.get("sourceKind") == "OFFICIAL" and (r.get("sourceStatus") != "VERIFIED" or not r.get("source") or not r.get("sourceReviewedBy") or not r.get("sourceReviewedAt")):
            raise ValueError("Official requirements require a source and recorded source review")
        if r.get("sourceKind") == "OFFICIAL":
            url = urlsplit(str(r["source"]))
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
                raise ValueError("Official source must be an HTTP(S) URL without embedded credentials")
            try:
                reviewed_at = datetime.fromisoformat(str(r["sourceReviewedAt"]).replace("Z", "+00:00"))
                reviewed_at = reviewed_at.replace(tzinfo=timezone.utc) if reviewed_at.tzinfo is None else reviewed_at
                if reviewed_at > datetime.now(timezone.utc):
                    raise ValueError("Source review cannot be future dated")
            except (ValueError, TypeError) as exc:
                raise ValueError("Official source needs a valid past sourceReviewedAt timestamp") from exc
    maximum = D(0)
    for members in groups.values():
        budgets = {number(r["maximumEarnedPoints"]) for r in members}
        if len(budgets) != 1:
            raise ValueError("Alternative requirements must share the same slot maximum")
        if len(members) == 1 and members[0].get("applicabilityRule"):
            raise ValueError("Conditional requirements need a budget-preserving alternative group/profile variant")
        # A stable slot stays in the denominator even when a branch lacks evidence.
        maximum += next(iter(budgets))
    if maximum <= 0 or not maximum.is_finite():
        raise ValueError("The total maximum points must be greater than zero and finite")
    return normalized


def _applicable(requirement: dict[str, Any], application: dict[str, Any]) -> bool | None:
    rule = requirement.get("applicabilityRule")
    if not rule:
        return True
    value: Any = application.get("applicantContext", application)
    for field in str(rule["field"]).split("."):
        if not isinstance(value, dict) or field not in value:
            return None
        value = value[field]
    return value in rule["value"] if rule.get("operator") == "in" else value == rule.get("value")


def _manual_quality(requirement: dict[str, Any], document: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    rubric = requirement.get("qualityRubric") or {}
    review = document.get("qualityAssessment") or {}
    if not review.get("reviewedBy") or not str(review.get("reason") or "").strip() or review.get("rubricVersion") != rubric.get("version"):
        return None, "A version-matched staff rubric assessment and evidence reason are required"
    try:
        if rubric.get("criteria"):
            scores = review.get("criterionScores") or {}
            if not isinstance(scores, dict):
                return None, "Rubric criterion scores must be an object"
            total = earned = D(0)
            for criterion in rubric["criteria"]:
                maximum = number(criterion["maximumPoints"])
                value = number(scores.get(str(criterion["id"])))
                if not 0 <= value <= maximum:
                    return None, "Rubric criterion points are outside their configured range"
                earned += value
                total += maximum
            return earned / total, None
        level = next((level for level in rubric.get("levels", []) if str(level.get("id")) == str(review.get("levelId"))), None)
        if level is None:
            return None, "Select a configured rubric level"
        return number(level["quality"]), None
    except ValueError:
        return None, "The rubric assessment is incomplete or invalid"


def _quality(requirement: dict[str, Any], document: dict[str, Any], as_of: date) -> tuple[Decimal | None, list[str], dict[str, Any]]:
    if requirement["scoringMode"] == "FIXED":
        return D(1), [], {}
    if requirement.get("qualityScorer") == "MANUAL_RUBRIC":
        quality, error = _manual_quality(requirement, document)
        return quality, [error] if error else [], {"rubricVersion": (requirement.get("qualityRubric") or {}).get("version")}
    analysis = document.get("abaStatementAnalysis")
    if not isinstance(analysis, dict):
        return None, ["ABA statement analysis is not available"], {}
    reasons = []
    if analysis.get("reconciliationStatus") != "RECONCILED":
        reasons.append("Statement has unresolved extraction or reconciliation checks")
    if analysis.get("reviewStatus") != "VERIFIED":
        reasons.append("Statement review must be verified")
    if str(analysis.get("modelVersion")) != str(requirement.get("qualityScorerVersion", "1.0")):
        reasons.append("Statement scorer version differs from this policy's version")
    if analysis.get("acceptedForPolicy") is False:
        reasons.append("Statement does not meet the selected policy's evidence rules")
    if (analysis.get("score") or {}).get("provisional") or analysis.get("provisional"):
        reasons.append("Statement history is provisional and requires a complete accepted period")
    rules = requirement.get("evidenceRules") or {}
    metrics = analysis.get("metrics") or (analysis.get("analysis") or {}).get("metrics") or {}
    period = analysis.get("statement") or {}
    try:
        if rules.get("minimumDays") is not None and number(metrics.get("days", 0)) < number(rules["minimumDays"]):
            reasons.append("Statement duration is below this policy's minimum")
        if rules.get("maximumAgeDays") is not None:
            end = period.get("end") or analysis.get("periodEnd")
            if not end or (as_of-date.fromisoformat(str(end)[:10])).days > int(rules["maximumAgeDays"]):
                reasons.append("Statement freshness does not meet this policy")
        value = number(analysis.get("statementStrengthScore"))
        if not 1 <= value <= 10:
            reasons.append("Statement strength is outside the 1–10 contract")
    except (ValueError, TypeError):
        value = None
        reasons.append("Final statement strength or required coverage data is unavailable")
    provenance = {"statementStrengthScore": None if value is None else float(value), "bankAnalysisRevision": analysis.get("analysisRevision"), "bankModelVersion": analysis.get("modelVersion"), "mappingVersion": requirement.get("mappingVersion", "bank-linear-1-to-10-v1")}
    return (value-D(1))/D(9) if value is not None and not reasons else None, reasons, provenance


def calculate_evidence_assessment(requirements: list[dict[str, Any]], documents: list[dict[str, Any]], *, policy: dict[str, Any] | None = None, application: dict[str, Any] | None = None, as_of: datetime | date | None = None) -> dict[str, Any]:
    """Return confirmed evidence points against the configured requirement budget."""
    policy, application = policy or {}, application or {}
    calculated = as_of or datetime.now(timezone.utc)
    if isinstance(calculated, datetime):
        aware = calculated.replace(tzinfo=timezone.utc) if calculated.tzinfo is None else calculated
        day = aware.astimezone(ZoneInfo(policy.get("timezone") or "Asia/Phnom_Penh")).date()
    else:
        day = calculated
    try:
        requirements = validate_profile_requirements(requirements)
    except ValueError as exc:
        return {"scoringVersion":VERSION,"status":"NEEDS_REVIEW","complete":False,"gates":[str(exc)],"confirmedReadinessPoints":0.0,"confirmedReadinessNormalized":0.0,"documentAssessmentScore":0.0,"documentReadiness":0.0,"earnedPoints":0.0,"maximumPoints":0.0,"requirementScores":[],"approvalProbability":None,"configurationValid":False}
    current = [d for d in documents if str(d.get("status", "")).upper() not in INACTIVE]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in requirements:
        grouped[str(r.get("alternativeGroupId") or r["requirementId"])].append(r)
    total = D(0)
    total_maximum = sum(
        (number(members[0]["maximumEarnedPoints"]) for members in grouped.values()),
        D(0),
    )
    upper = D(0)
    slots = []
    gates = []
    required_count = submitted_required = verified_required = 0
    all_document_ids = set()
    for slot_id, members in grouped.items():
        maximum = number(members[0]["maximumEarnedPoints"])
        applicable = [r for r in members if _applicable(r, application) is True]
        unknown = any(_applicable(r, application) is None for r in members)
        required = any(r.get("requiredForFinalAssessment", r.get("required", True)) for r in (applicable or members))
        attached = [d for d in current if str(d.get("requirementId") or d.get("requirement") or "") in {r["requirementId"] for r in applicable}]
        reasons = []
        if not applicable:
            reasons.append("Applicability needs a reviewed budget-preserving profile resolution")
        if unknown:
            reasons.append("Applicant context for a conditional alternative is missing")
        chosen = None
        choices = application.get("primaryDocumentSelections") or {}
        selected_id = choices.get(slot_id)
        bank_members = [r for r in applicable if r.get("qualityScorer") == "ABA_STATEMENT_V1"]
        if bank_members:
            selected_id = application.get("selectedBankDocumentId") or selected_id
            if not selected_id:
                reasons.append("Choose a reviewed primary bank statement explicitly")
        if selected_id:
            chosen = next((d for d in attached if str(d.get("_id") or d.get("id")) == str(selected_id)), None)
            if chosen is None:
                reasons.append("The selected primary document is missing or superseded")
        elif len(attached) == 1:
            chosen = attached[0]
        elif len(attached) > 1:
            strategies = {r.get("documentSelectionStrategy") for r in applicable}
            if strategies == {"LATEST_REVISION"} and not bank_members:
                chosen = max(attached, key=lambda d: (str(d.get("updatedAt") or d.get("createdAt") or ""), str(d.get("_id") or "")))
            else:
                reasons.append("Choose and review a primary document; uploads are not added together")
        earned = D(0)
        quality = None
        provenance = {}
        chosen_requirement = next((r for r in applicable if chosen and r["requirementId"] == str(chosen.get("requirementId") or chosen.get("requirement"))), applicable[0] if applicable else members[0])
        if required:
            required_count += 1
            submitted_required += bool(attached)
        if chosen:
            document_id = str(chosen.get("_id") or chosen.get("id") or "")
            if document_id in all_document_ids:
                reasons.append("A document cannot receive points in more than one slot")
            all_document_ids.add(document_id)
            if str(chosen.get("status", "")).upper() != "VERIFIED":
                reasons.append("Document verification is not confirmed")
            if bank_members and application.get("selectedBankAnalysisRevision") != (chosen.get("abaStatementAnalysis") or {}).get("analysisRevision"):
                reasons.append("Primary bank selection is stale; review and select the current analysis revision")
            if not has_completed_ocr(chosen) and not is_photo_document(chosen, chosen_requirement):
                # Explicit reviewed manual evidence may cover supported non-OCR documents.
                manual = chosen.get("qualityAssessment") or chosen.get("manualReview") or {}
                if not manual.get("reviewedBy") or not manual.get("reason"):
                    reasons.append("OCR/source checks are incomplete")
            if not reasons:
                quality, quality_reasons, provenance = _quality(chosen_requirement, chosen, day)
                reasons.extend(quality_reasons)
                if quality is not None and not reasons:
                    minimum = number(chosen_requirement["minimumEarnedPoints"])
                    earned = maximum if chosen_requirement["scoringMode"] == "FIXED" else minimum+(maximum-minimum)*quality
                    if required:
                        verified_required += 1
        elif not reasons:
            reasons.append("No evidence has been selected/uploaded for this slot")
        total += earned
        upper += earned if quality is not None and not reasons else maximum
        if required and reasons:
            gates.append({"slotId":slot_id,"name":chosen_requirement.get("name", slot_id),"reasons":reasons})
        slots.append({"slotId":slot_id,"requirementId":chosen_requirement["requirementId"],"alternativeRequirementIds":[r["requirementId"] for r in members],"name":chosen_requirement.get("name",slot_id),"required":required,"optional":not required,"scoringMode":chosen_requirement["scoringMode"],"configuredPoints":displayed(maximum),"minimumEarnedPoints":chosen_requirement["minimumEarnedPoints"],"maximumEarnedPoints":str(maximum),"earnedPoints":displayed(earned),"exactEarnedPoints":str(earned),"quality":None if quality is None else str(quality),"status":"VERIFIED" if not reasons else "NOT_UPLOADED" if not attached else "NEEDS_REVIEW","documentIds":[str(d.get("_id") or d.get("id")) for d in attached],"selectedDocumentId":str(chosen.get("_id") or chosen.get("id")) if chosen else None,"reasons":reasons,"provenance":provenance})
    fingerprint_payload = {"policy":policy.get("contentHash") or policy.get("id") or policy.get("_id"),"version":policy.get("version"),"requirements":requirements,"context":application.get("applicantContext",{}),"selectedBankDocumentId":application.get("selectedBankDocumentId"),"primaryDocumentSelections":application.get("primaryDocumentSelections",{}),"documents":[{k:d.get(k) for k in ("_id","requirementId","status","ocrStatus","contentHash","documentRevision","updatedAt","qualityAssessment","abaStatementAnalysis")} for d in sorted(current,key=lambda d:str(d.get("_id")))]}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()
    statuses = [str(d.get("status", "")).upper() for d in current]
    requirement_by_id = {r["requirementId"]: r for r in requirements}
    processing = sum(
        s == "PROCESSING"
        or (
            not is_photo_document(
                d,
                requirement_by_id.get(
                    str(d.get("requirementId") or d.get("requirement") or "")
                ),
            )
            and str(d.get("ocrStatus", "")).upper() in {"OCR_PENDING", "OCR_PROCESSING"}
        )
        for d, s in zip(current, statuses)
    )
    failed = sum(s == "FAILED" for s in statuses)
    status = "PROCESSING" if processing else "NEEDS_REVIEW" if gates else "READY_FOR_STAFF_REVIEW"
    score_value = displayed(total)
    score_percentage = displayed(D(100) * total / total_maximum) if total_maximum else 0.0
    pages = sum(int(d.get("ocrTotalPages") or 0) for d in current)
    processed = sum(int(d.get("ocrPagesProcessed") or 0) for d in current)
    return {"scoringVersion":VERSION,"policyVersionId":str(policy.get("id") or policy.get("_id") or ""),"policyVersion":policy.get("version"),"configurationValid":True,"status":status,"complete":not gates and not processing,"gates":gates,"confirmedReadinessPoints":score_value,"confirmedReadinessNormalized":float(total/total_maximum) if total_maximum else 0.0,"exactPoints":str(total),"documentAssessmentScore":score_percentage,"documentReadiness":score_percentage,"earnedPoints":score_value,"verifiedPoints":score_value,"maximumPoints":displayed(total_maximum),"totalAvailablePoints":displayed(total_maximum),"requirementScores":slots,"approvalProbability":None,"completionRange":{"lower":score_value,"upper":displayed(upper)},"inputFingerprint":fingerprint,"totalDocuments":len(current),"totalRequiredDocuments":required_count,"submittedRequiredDocuments":submitted_required,"verifiedRequiredDocuments":verified_required,"submissionCompleteness":displayed(D(100)*submitted_required/required_count) if required_count else 100.0,"verificationPercentage":displayed(D(100)*verified_required/required_count) if required_count else 100.0,"verifiedDocuments":sum(s=="VERIFIED" and (is_photo_document(d, requirement_by_id.get(str(d.get("requirementId") or d.get("requirement") or ""))) or has_completed_ocr(d)) for d,s in zip(current,statuses)),"ocrCompletedDocuments":sum(has_completed_ocr(d) for d in current),"processingDocuments":processing,"failedDocuments":failed,"rejectedDocuments":sum(s=="REJECTED" for s in statuses),"needsReviewDocuments":sum(s in {"PARTIAL","NEEDS_REVIEW","SUSPICIOUS"} for s in statuses),"insufficientDataDocuments":sum(s=="INSUFFICIENT_DATA" for s in statuses),"missingRequiredDocuments":[{"requirementId":s["requirementId"],"name":s["name"]} for s in slots if s["required"] and not s["documentIds"]],"documentProcessingStatus":status,"lastCalculationAt":calculated,"ocrProgress":{"pagesProcessed":processed,"totalPages":pages,"percentage":round(100*processed/pages,2) if pages else 0},"disclaimer":"Evidence strength is not visa approval probability or proof of document authenticity."}
