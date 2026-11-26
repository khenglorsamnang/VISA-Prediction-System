from backend.app.services.document_metrics import (
    bank_requirement_points,
    requirement_awarded_points,
    summarize_documents,
)
from backend.app.services.automated_document_verification import evaluate_automated_document_verification


REQS = [
    {"requirementId": "passport", "name": "Passport", "required": True, "weight": 5},
    {"requirementId": "photo", "name": "Applicant Photo", "required": True, "weight": 5},
    {"requirementId": "id", "name": "National ID Card", "required": True, "weight": 5},
    {"requirementId": "bank", "name": "Bank Statement", "required": True, "weight": 60},
]


def doc(requirement_id, status="VERIFIED", points=None, file_id=None, **extra):
    return {
        "_id": file_id or f"{requirement_id}-file",
        "requirementId": requirement_id,
        "status": status,
        "ocrStatus": "OCR_COMPLETED",
        "ocrTotalPages": 1,
        "ocrPagesProcessed": 1,
        "ocrProgressPercent": 100,
        "ocrPageNumbers": [1],
        "awardedPoints": points,
        **extra,
    }


def test_configured_four_requirements_total_75_and_no_uploads_are_zero():
    result = summarize_documents(REQS, [])
    assert result["maximumPoints"] == 75
    assert result["earnedPoints"] == 0
    assert result["documentAssessmentScore"] == 0
    assert len(result["missingRequiredDocuments"]) == 4


def test_uploaded_unreadable_passport_is_submitted_but_not_verified():
    result = summarize_documents(REQS, [doc("passport", status="INSUFFICIENT_DATA", points=0)])
    assert result["submissionCompleteness"] == 25
    assert result["documentAssessmentScore"] == 0
    assert not any(item["requirementId"] == "passport" for item in result["missingRequiredDocuments"])


def test_applicant_photo_does_not_require_ocr_text_fields():
    result = evaluate_automated_document_verification(
        {
            "requirementCode": "photo",
            "mimeType": "image/jpeg",
            "size": 1000,
            "ocrStatus": "OCR_FAILED",
            "ocrConfidence": None,
            "extractedFields": {},
        },
        {},
        {"code": "photo"},
    )
    assert result["status"] == "NEEDS_REVIEW"
    assert any("image checks have not completed" in reason.lower() for reason in result["reasons"])
    assert any(check["code"] == "PHOTO_OCR_NOT_REQUIRED" for check in result["checks"])


def test_bank_diagnostic_score_is_not_requirement_points_without_reliable_coverage():
    analysis = {
        "financialRequirementStatus": "INSUFFICIENT_DATA",
        "bankStatementScore": {"available": True, "points": 1.24, "maximum": 45},
    }
    assert bank_requirement_points(analysis, 60) is None
    analysis["financialRequirementStatus"] = "PASS"
    assert bank_requirement_points(analysis, 60) == 1.65
    analysis["financialRequirementStatus"] = "BELOW_MINIMUM"
    assert bank_requirement_points(analysis, 60) == 1.65
    analysis["financialRequirementStatus"] = "FAIL"
    assert bank_requirement_points(analysis, 60) == 0
    analysis.pop("financialRequirementStatus")
    assert bank_requirement_points(analysis, 60) is None


def test_passport_and_photo_scores_are_dynamic_percentages():
    result = summarize_documents(REQS, [doc("passport", points=5), doc("photo", points=5)])
    assert result["earnedPoints"] == 10
    assert result["documentAssessmentScore"] == 13.33


def test_partial_and_rejected_points_are_evidence_based():
    result = summarize_documents(
        REQS,
        [doc("passport", points=5), doc("photo", points=5), doc("bank", status="PARTIAL", points=30), doc("id", status="REJECTED", points=5)],
    )
    assert result["earnedPoints"] == 10
    assert result["documentAssessmentScore"] == 13.33
    assert next(item for item in result["requirementScores"] if item["requirementId"] == "id")["status"] == "REJECTED"


def test_needs_review_and_insufficient_data_only_receive_confirmed_points():
    result = summarize_documents(REQS, [doc("passport", status="NEEDS_REVIEW", points=2), doc("photo", status="INSUFFICIENT_DATA", points=1)])
    assert result["earnedPoints"] == 0


def test_multiple_files_are_capped_and_single_file_uses_latest():
    multi = summarize_documents(
        [{**REQS[0], "allowMultiple": True}],
        [doc("passport", points=5, file_id="a"), doc("passport", points=5, file_id="b")],
    )
    assert multi["earnedPoints"] == 5
    single = summarize_documents(
        [REQS[0]],
        [doc("passport", points=5, file_id="old", updatedAt="2026-01-01"), doc("passport", status="REJECTED", points=0, file_id="new", updatedAt="2026-02-01")],
    )
    assert single["earnedPoints"] == 0


def test_optional_requirements_are_included_in_the_configured_maximum():
    requirements = [*REQS, {"requirementId": "optional", "name": "Optional", "required": False, "weight": 25}]
    excluded = summarize_documents(requirements, [])
    included = summarize_documents(requirements, [doc("optional", status="REJECTED", points=0)])
    assert excluded["maximumPoints"] == 100
    assert included["maximumPoints"] == 100


def test_optional_maximum_is_stable_when_its_file_is_deleted():
    requirements = [*REQS, {"requirementId": "optional", "required": False, "weight": 25}]
    active = summarize_documents(requirements, [doc("optional", status="REJECTED", points=0)])
    deleted = summarize_documents(requirements, [{**doc("optional", status="DELETED", points=0)}])
    assert active["maximumPoints"] == 100
    assert deleted["maximumPoints"] == 100


def test_disabled_requirements_are_excluded_when_not_in_active_configuration():
    active = [item for item in REQS if item["requirementId"] != "id"]
    assert summarize_documents(active, [doc("id", points=5)])["maximumPoints"] == 70


def test_different_visa_requirement_sets_are_isolated_and_recalculation_is_idempotent():
    visa_a = summarize_documents([REQS[0]], [doc("passport", points=5)])
    visa_b = summarize_documents([REQS[3]], [doc("passport", points=5)])
    assert visa_a["documentAssessmentScore"] == 100
    assert visa_b["earnedPoints"] == 0
    assert summarize_documents([REQS[0]], [doc("passport", points=5)])["earnedPoints"] == visa_a["earnedPoints"]


def test_requirement_points_are_dynamic_and_bank_contribution_uses_configured_maximum():
    requirements = [
        {"requirementId": "passport", "name": "Passport", "required": True, "weight": 15},
        {"requirementId": "photo", "name": "Photo", "required": True, "weight": 15},
        {"requirementId": "id", "name": "National ID Card", "required": True, "weight": 20},
        {"requirementId": "bank", "code": "bank_statement", "name": "Bank Statement", "required": True, "weight": 50},
    ]
    documents = [
        doc("passport"),
        doc("photo"),
        doc("id"),
        doc(
            "bank",
            bankStatementAnalysis={
                "financialRequirementStatus": "PASS",
                "computedBankScore": {"available": True, "points": 30.04, "maximum": 45},
            },
        ),
    ]
    initial = summarize_documents(requirements, documents)
    assert initial["maximumPoints"] == 100
    assert initial["earnedPoints"] == 83.38
    assert next(item for item in initial["requirementScores"] if item["requirementId"] == "bank")["earnedPoints"] == 33.38

    changed = [
        {**item, "weight": 60, "maximumEarnedPoints": 60}
        if item["requirementId"] == "bank"
        else item
        for item in requirements
    ]
    updated = summarize_documents(changed, documents)
    assert updated["maximumPoints"] == 110
    assert next(item for item in updated["requirementScores"] if item["requirementId"] == "bank")["earnedPoints"] == 40.05

    added = [*changed, {"requirementId": "new", "name": "Additional", "required": True, "weight": 10}]
    assert summarize_documents(added, documents)["maximumPoints"] == 120

    deactivated = [item for item in added if item["requirementId"] != "id"]
    assert summarize_documents(deactivated, documents)["maximumPoints"] == 100


def test_unverified_upload_never_earns_document_points():
    requirement = {"requirementId": "passport", "name": "Passport", "required": True, "weight": 15}
    uploaded = doc("passport", status="NEEDS_REVIEW", points=15)
    assert requirement_awarded_points(requirement, uploaded) == 0
    assert summarize_documents([requirement], [uploaded])["earnedPoints"] == 0
