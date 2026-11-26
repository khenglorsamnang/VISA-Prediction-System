from backend.app.services.document_metrics import summarize_documents


def _completed_document(**values):
    return {
        "status": "VERIFIED",
        "ocrStatus": "OCR_COMPLETED",
        "ocrTotalPages": 2,
        "ocrPagesProcessed": 2,
        "ocrProgressPercent": 100,
        "ocrPageNumbers": [1, 2],
        **values,
    }


def test_document_metrics_include_optional_maximum_and_only_verified_points():
    summary = summarize_documents(
        [
            {"requirementId": "passport", "name": "Passport", "required": True, "weight": 60},
            {"requirementId": "bank", "name": "Bank statement", "required": True, "weight": 40},
            {"requirementId": "photo", "name": "Photo", "required": False, "weight": 10},
        ],
        [
            _completed_document(
                _id="passport-file",
                requirementId="passport",
                awardedPoints=45,
            ),
            {
                "_id": "unread-file",
                "requirementId": "bank",
                "status": "PENDING",
                "ocrStatus": "OCR_FAILED",
                "ocrTotalPages": 3,
                "ocrPagesProcessed": 1,
            },
        ],
    )

    assert summary["totalDocuments"] == 2
    assert summary["ocrCompletedDocuments"] == 1
    assert summary["verifiedDocuments"] == 1
    assert summary["failedDocuments"] == 1
    assert summary["verificationPercentage"] == 50
    assert summary["totalAvailablePoints"] == 110
    assert summary["verifiedPoints"] == 60
    assert summary["documentReadiness"] == 54.55
    assert summary["missingRequiredDocuments"] == []
    assert summary["ocrProgress"] == {
        "pagesProcessed": 3,
        "totalPages": 5,
        "percentage": 60,
    }


def test_document_metrics_ignore_incomplete_and_replaced_documents():
    summary = summarize_documents(
        [
            {"requirementId": "passport", "required": True, "weight": 100},
        ],
        [
            {
                "_id": "unread-passport",
                "requirementId": "passport",
                "status": "VERIFIED",
                "ocrStatus": "OCR_COMPLETED",
                "ocrTotalPages": 2,
                "ocrPagesProcessed": 1,
                "ocrProgressPercent": 50,
                "ocrPageNumbers": [1],
            },
            {
                "_id": "replaced-passport",
                "requirementId": "passport",
                "status": "REPLACED",
                "ocrStatus": "OCR_FAILED",
            },
        ],
    )

    assert summary["totalDocuments"] == 1
    assert summary["verifiedDocuments"] == 0
    assert summary["verificationPercentage"] == 0
    assert summary["documentReadiness"] == 0


def test_verified_photo_does_not_need_completed_ocr_for_document_metrics():
    summary = summarize_documents(
        [
            {
                "requirementId": "photo",
                "code": "photo",
                "name": "Applicant photo",
                "required": True,
                "weight": 10,
            }
        ],
        [
            {
                "_id": "photo-file",
                "requirementId": "photo",
                "requirementCode": "photo",
                "status": "VERIFIED",
                "ocrStatus": "OCR_FAILED",
                "awardedPoints": 10,
            }
        ],
    )
    assert summary["verifiedDocuments"] == 1
    assert summary["verifiedRequiredDocuments"] == 1
    assert summary["verificationPercentage"] == 100
    assert summary["failedDocuments"] == 0
    assert summary["earnedPoints"] == 10
    assert summary["documentAssessmentScore"] == 100
