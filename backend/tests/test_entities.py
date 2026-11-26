from collections.abc import Iterator
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

import jwt
import joblib
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from backend.app import main
from backend.app.api import (
    bank_statements as bank_statements_api,
    dependencies,
    documents as documents_api,
    document_workflow as document_workflow_api,
    ml as ml_api,
    verification_reports as verification_reports_api,
)
from backend.app.services.bank_statement_scoring import calculate_bank_statement_score

TEST_JWT_SECRET = "fastapi-entity-test-secret-with-32-bytes"


def test_legacy_ocr_job_response_is_normalized_to_canonical_contract() -> None:
    job = documents_api._parse_ocr_job(
        {
            "job_id": "legacy-job-123",
            "status": "completed",
            "result": {
                "full_text": "Page one text\nPage two text",
                "total_pages": 2,
                "pages": [
                    {
                        "page": 1,
                        "results": [{"text": "Page one text", "confidence": 0.9}],
                    },
                    {
                        "page": 2,
                        "results": [{"text": "Page two text", "confidence": 0.8}],
                    },
                ],
            },
        },
        "job response",
    )

    assert job["id"] == "legacy-job-123"
    assert job["status"] == "OCR_COMPLETED"
    assert job["pagesProcessed"] == 2
    assert job["totalPages"] == 2
    assert job["progressPercent"] == 100
    assert job["result"]["extracted_text"] == "Page one text\nPage two text"
    assert [page["page"] for page in job["result"]["pages"]] == [1, 2]
    assert job["result"]["pages"][0]["text"] == "Page one text"
    assert "fields" not in job["result"]
    assert (
        documents_api._bank_statement_field_score("bank_statement", job["result"])
        is None
    )
    is_valid, total_pages, _ = documents_api._valid_ocr_result(job)
    assert is_valid is True
    assert total_pages == 2


def test_ocr_job_parser_rejects_response_without_a_job_id() -> None:
    with pytest.raises(ValueError, match="missing job ID"):
        documents_api._parse_ocr_job(
            {"status": "queued", "result": None},
            "job response",
        )


@pytest.mark.parametrize(
    "requirement",
    [
        {"code": "national_id"},
        {"code": "National ID Card"},
        {"code": "national_id_card"},
        {"name": "National Id Card"},
        {"name": "Identity Card"},
        {"name": "ID Card"},
    ],
)
def test_document_type_normalizes_national_id_aliases(requirement):
    assert documents_api._document_type(requirement) == "national_id"


def test_partial_ocr_pages_are_preserved_but_never_count_as_completed() -> None:
    job = documents_api._parse_ocr_job(
        {
            "job": {
                "id": "partial-job",
                "status": "OCR_PARTIAL",
                "pagesProcessed": 2,
                "totalPages": 2,
                "progressPercent": 100,
                "failedPages": [{"page": 2, "error": "OCR timed out"}],
                "result": {
                    "status": "partial",
                    "fields": {"accountNumber": "123456789"},
                    "pages": [
                        {
                            "page": 1,
                            "text": "Account statement",
                            "method": "embedded_text",
                            "status": "OCR_COMPLETED",
                        },
                        {
                            "page": 2,
                            "text": "",
                            "method": "ocr",
                            "status": "OCR_FAILED",
                            "error": "OCR timed out",
                        },
                    ],
                    "failed_pages": [
                        {"page": 2, "error": "OCR timed out"}
                    ],
                    "metrics": {"page_count": 2, "status": "OCR_PARTIAL"},
                },
                "error": {
                    "code": "OCR_PARTIAL",
                    "message": "OCR failed on page 2",
                },
            }
        },
        "job status",
    )

    assert job["status"] == "OCR_PARTIAL"
    assert job["pagesProcessed"] == 2
    assert job["failedPages"] == [{"page": 2, "error": "OCR timed out"}]
    assert documents_api._valid_ocr_result(
        job,
        require_all_pages_success=False,
    )[0]
    assert not documents_api._valid_ocr_result(job)[0]
    assert documents_api._bank_statement_field_score(
        "bank_statement",
        job["result"],
    ) is None
    partial_document = {
        "ocrStatus": "OCR_PARTIAL",
        "ocrTotalPages": 2,
        "ocrPagesProcessed": 2,
        "ocrProgressPercent": 100,
        "ocrPageNumbers": [1, 2],
    }
    assert not document_workflow_api.has_completed_ocr(partial_document)
    assert (
        document_workflow_api.document_workflow_status(partial_document)
        == "OCR_PARTIAL"
    )


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [document.copy() for document in self.documents]


class FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.documents: list[dict[str, Any]] = []

    def find(self, _query: dict[str, Any]) -> FakeCursor:
        return FakeCursor(self.documents)

    @staticmethod
    def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            if key == "$or":
                if not any(FakeCollection._matches(document, item) for item in expected):
                    return False
                continue
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$exists" in expected and (key in document) != expected["$exists"]:
                    return False
                if "$lte" in expected and (
                    actual is None or actual > expected["$lte"]
                ):
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self.documents:
            if self._matches(document, query):
                return document.copy()
        return None

    async def insert_one(self, document: dict[str, Any]) -> None:
        unique_fields = {
            "countries": ("isoCode",),
            "customers": ("email", "passportNumber"),
        }.get(self.name, ())
        if any(
            existing.get("_id") == document.get("_id")
            or any(
                document.get(field) is not None
                and existing.get(field) == document.get(field)
                for field in unique_fields
            )
            for existing in self.documents
        ):
            raise DuplicateKeyError("duplicate key")
        self.documents.append(document.copy())

    async def replace_one(
        self, query: dict[str, Any], replacement: dict[str, Any]
    ) -> SimpleNamespace:
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                self.documents[index] = replacement.copy()
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query: dict[str, Any]) -> SimpleNamespace:
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> SimpleNamespace:
        for document in self.documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))
                for field in update.get("$unset", {}):
                    document.pop(field, None)
                for field, value in update.get("$push", {}).items():
                    document.setdefault(field, []).append(value)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection(name)
        return self.collections[name]


def test_bank_statement_check_is_saved_when_ocr_job_completes(
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    class FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return self.payload

    completed_job = {
        "job": {
            "id": "bank-statement-job",
            "status": "OCR_COMPLETED",
            "progressPercent": 100,
            "pagesProcessed": 1,
            "totalPages": 1,
            "result": {
                "fields": {
                    "bankName": "Example Bank",
                    "accountHolderName": "Sample Applicant",
                    "accountNumber": "123456789",
                    "openingBalance": 1000,
                    "closingBalance": 1200,
                    "currency": "USD",
                },
                "pages": [
                    {
                        "page": 1,
                        "text": "Bank statement",
                        "method": "ocr",
                        "boxes": [],
                    }
                ],
                "metrics": {"page_count": 1},
            },
        }
    }

    class FakeOCRClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse(completed_job)

        async def get(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse(completed_job)

    monkeypatch.setattr(
        documents_api,
        "_PRIVATE_DIR",
        temporary_model_dir,
    )
    monkeypatch.setattr(
        documents_api.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeOCRClient(),
    )
    stored_path = temporary_model_dir / "bank-statement.pdf"
    stored_path.write_bytes(b"%PDF-1.7\nsample")
    document = {
        "_id": "bank-document",
        "storagePath": str(stored_path),
        "fileName": "bank-statement.pdf",
        "requirementCode": "bank_statement",
        "applicationId": "application-1",
        "requirementId": "bank-requirement",
        "customerId": "customer-1",
    }
    database = FakeDatabase()
    database["documents"].documents.append(document.copy())
    database["visaapplications"].documents.append(
        {
            "_id": "application-1",
            "customerId": "customer-1",
            "requirementSnapshot": [
                {
                    "requirementId": "bank-requirement",
                    "code": "bank_statement",
                    "weight": 20,
                }
            ],
        }
    )
    database["customers"].documents.append(
        {"_id": "customer-1", "fullName": "Sample Applicant"}
    )

    asyncio.run(documents_api._process_document_ocr(document, database, 1))

    processed = database["documents"].documents[0]
    assert processed["ocrStatus"] == "OCR_COMPLETED"
    assert processed["bankStatementCheckPercent"] == 50
    assert len(processed["bankStatementMissingFields"]) == 6
    assert processed["status"] == "NEEDS_REVIEW"
    assert processed["automatedVerificationStatus"] == "NEEDS_REVIEW"
    assert processed["automatedVerification"]["authenticityVerified"] is False
    assert processed["automatedVerification"]["documentScore"] < 100


def test_partial_bank_statement_pages_are_stored_without_a_final_score(
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {
                "job": {
                    "id": "partial-bank-statement-job",
                    "status": "OCR_PARTIAL",
                    "progressPercent": 100,
                    "pagesProcessed": 2,
                    "totalPages": 2,
                    "failedPages": [
                        {"page": 2, "error": "OCR timed out"}
                    ],
                    "message": "OCR partially completed. Failed page(s): 2.",
                    "error": {
                        "code": "OCR_PARTIAL",
                        "message": "OCR partially completed. Failed page(s): 2.",
                    },
                    "result": {
                        "status": "partial",
                        "fields": {
                            "bankName": "Example Bank",
                            "accountHolderName": "Sample Applicant",
                            "accountNumber": "123456789",
                            "openingBalance": 1000,
                            "closingBalance": 1200,
                            "currency": "USD",
                        },
                        "extracted_text": "--- PAGE 1 ---\nBank statement page one",
                        "failed_pages": [
                            {"page": 2, "error": "OCR timed out"}
                        ],
                        "pages": [
                            {
                                "page": 1,
                                "text": "Bank statement page one",
                                "method": "embedded_text",
                                "status": "OCR_COMPLETED",
                            },
                            {
                                "page": 2,
                                "text": "",
                                "method": "ocr",
                                "status": "OCR_FAILED",
                                "error": "OCR timed out",
                            },
                        ],
                        "metrics": {
                            "page_count": 2,
                            "pages_processed": 2,
                            "status": "OCR_PARTIAL",
                        },
                    },
                }
            }

    class FakeOCRClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse()

        async def get(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(
        documents_api,
        "_PRIVATE_DIR",
        temporary_model_dir,
    )
    monkeypatch.setattr(
        documents_api.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeOCRClient(),
    )
    stored_path = temporary_model_dir / "partial-bank-statement.pdf"
    stored_path.write_bytes(b"%PDF-1.7\nsample")
    document = {
        "_id": "partial-bank-document",
        "storagePath": str(stored_path),
        "fileName": "partial-bank-statement.pdf",
        "requirementCode": "bank_statement",
    }
    database = FakeDatabase()
    database["documents"].documents.append(document.copy())

    asyncio.run(documents_api._process_document_ocr(document, database, 1))

    processed = database["documents"].documents[0]
    assert processed["ocrStatus"] == "OCR_PARTIAL"
    assert processed["workflowStatus"] == "NEEDS_REVIEW"
    assert processed["ocrPagesProcessed"] == 2
    assert processed["ocrTotalPages"] == 2
    assert processed["ocrFailedPages"] == [
        {"page": 2, "error": "OCR timed out"}
    ]
    assert "Bank statement page one" in processed["ocrText"]
    assert processed["bankStatementCheckPercent"] is None
    assert processed["bankStatementMissingFields"] == []


def test_ocr_service_timeout_is_persisted_as_a_technical_failure(
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    class TimeoutOCRClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            raise documents_api.httpx.TimeoutException("OCR request timed out")

    monkeypatch.setattr(documents_api, "_PRIVATE_DIR", temporary_model_dir)
    monkeypatch.setattr(
        documents_api.httpx,
        "AsyncClient",
        lambda **_kwargs: TimeoutOCRClient(),
    )
    stored_path = temporary_model_dir / "passport.pdf"
    stored_path.write_bytes(b"%PDF-1.7\nsample")
    document = {
        "_id": "timeout-document",
        "storagePath": str(stored_path),
        "fileName": "passport.pdf",
        "requirementCode": "passport",
        "applicationId": "timeout-application",
    }
    database = FakeDatabase()
    database["documents"].documents.append(document.copy())

    asyncio.run(documents_api._process_document_ocr(document, database, 1))

    processed = database["documents"].documents[0]
    assert processed["ocrStatus"] == "OCR_FAILED"
    assert processed["status"] == "FAILED"
    assert processed["automatedVerificationStatus"] == "FAILED"
    assert "timed out" in processed["ocrError"]
    assert "ocrLeaseOwner" not in processed


def test_active_ocr_lease_prevents_duplicate_document_processing(monkeypatch) -> None:
    async def unexpected_client(**_kwargs):
        pytest.fail("A leased document must not start a second OCR request")

    monkeypatch.setattr(
        documents_api.httpx,
        "AsyncClient",
        unexpected_client,
    )
    document = {
        "_id": "leased-document",
        "ocrLeaseOwner": "active-worker",
        "ocrLeaseExpiresAt": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    database = FakeDatabase()
    database["documents"].documents.append(document.copy())

    asyncio.run(documents_api._process_document_ocr(document, database, 1))

    assert database["documents"].documents[0]["ocrLeaseOwner"] == "active-worker"


class FakeMongoClient:
    def __init__(self) -> None:
        self.database = FakeDatabase()
        self.admin = SimpleNamespace(command=self._ping)

    async def _ping(self, _command: str) -> dict[str, int]:
        return {"ok": 1}

    def get_database(self, _database_name: str) -> FakeDatabase:
        return self.database

    async def close(self) -> None:
        pass


@pytest.fixture
def entity_client(monkeypatch) -> Iterator[tuple[TestClient, FakeDatabase]]:
    mongo_client = FakeMongoClient()
    monkeypatch.setattr(main, "create_mongo_client", lambda: mongo_client)
    monkeypatch.setattr(
        dependencies,
        "settings",
        dependencies.settings.__class__(
            **{
                **dependencies.settings.__dict__,
                "jwt_secret": TEST_JWT_SECRET,
            }
        ),
    )
    with TestClient(main.app) as client:
        yield client, mongo_client.database


@pytest.fixture
def temporary_model_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(
        prefix=".test-model-storage-",
        dir=Path(__file__).resolve().parent,
    ) as directory:
        yield Path(directory)


@pytest.fixture
def temporary_receipt_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(
        prefix=".test-receipts-",
        dir=Path(__file__).resolve().parent,
    ) as directory:
        yield Path(directory)


def _headers(role: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "id": "test-user",
            "email": "test@example.com",
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_country_routes_enforce_admin_and_preserve_use_restrictions(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    payload = {"name": "  Japan ", "isoCode": "jp", "nationality": "jpn"}

    denied = client.post(
        "/api/countries", json=payload, headers=_headers("CUSTOMER")
    )
    assert denied.status_code == 403

    created = client.post("/api/countries", json=payload, headers=_headers("ADMIN"))
    assert created.status_code == 201
    country = created.json()["country"]
    assert country["name"] == "Japan"
    assert country["isoCode"] == "JP"
    assert country["nationality"] == "JPN"
    assert country["active"] is True

    duplicate = client.post(
        "/api/countries",
        json={"name": "Japan Duplicate", "isoCode": "JP"},
        headers=_headers("ADMIN"),
    )
    assert duplicate.status_code == 409

    database["visatypes"].documents.append(
        {"_id": ObjectId(), "countryId": ObjectId(country["_id"])}
    )
    deleted = client.delete(
        f"/api/countries/{country['_id']}", headers=_headers("ADMIN")
    )
    assert deleted.status_code == 200
    assert deleted.json()["message"] == "Country is in use and has been deactivated"
    assert database["countries"].documents[0]["active"] is False


def test_customer_routes_validate_duplicates_updates_and_references(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    headers = _headers("STAFF")
    invalid = client.post(
        "/api/customers",
        json={"fullName": "Test", "email": "not-an-email"},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"message": "Customer email address is invalid"}

    created = client.post(
        "/api/customers",
        json={
            "fullName": "Test Customer",
            "email": "test@example.com",
            "passportNumber": "P123456",
        },
        headers=headers,
    )
    assert created.status_code == 201
    customer = created.json()["customer"]
    assert customer["email"] == "test@example.com"

    duplicate = client.post(
        "/api/customers",
        json={"fullName": "Duplicate", "email": " TEST@example.com "},
        headers=headers,
    )
    assert duplicate.status_code == 409

    updated = client.put(
        f"/api/customers/{customer['_id']}",
        json={"fullName": "Updated Customer"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["customer"]["fullName"] == "Updated Customer"

    database["visaapplications"].documents.append(
        {"_id": "app-1", "customer": customer["_id"]}
    )
    retained = client.delete(
        f"/api/customers/{customer['_id']}", headers=headers
    )
    assert retained.status_code == 409
    assert database["customers"].documents


def test_customer_boolean_profile_values_are_normalized_and_validated(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    headers = _headers("STAFF")

    created = client.post(
        "/api/customers",
        json={
            "fullName": "Boolean Customer",
            "passportNumber": "BOOL123",
            "hasOverstayHistory": "false",
            "proofOfTiesToCambodia": "TRUE",
        },
        headers=headers,
    )

    assert created.status_code == 201
    assert created.json()["customer"]["hasOverstayHistory"] is False
    assert created.json()["customer"]["proofOfTiesToCambodia"] is True
    assert database["customers"].documents[0]["hasOverstayHistory"] is False

    invalid = client.put(
        f"/api/customers/{created.json()['customer']['_id']}",
        json={"hasOverstayHistory": "sometimes"},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["message"] == "hasOverstayHistory must be true or false"


def test_business_routes_register_static_application_path_before_id_route(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client

    unauthorized = client.get("/api/visa-types/applications")
    assert unauthorized.status_code == 401

    visa_type_id = ObjectId()
    database["visatypes"].documents.append(
        {"_id": visa_type_id, "name": "Test visa", "active": True}
    )
    visa_type = client.get(
        f"/api/visa-types/{visa_type_id}",
        headers=_headers("STAFF"),
    )
    assert visa_type.status_code == 200
    assert visa_type.json()["visaType"]["_id"] == str(visa_type_id)

    customer_visa_type = client.get(
        f"/api/visa-types/{visa_type_id}",
        headers=_headers("CUSTOMER"),
    )
    assert customer_visa_type.status_code == 200

    denied_update = client.put(
        f"/api/visa-types/{visa_type_id}",
        json={"name": "Unauthorized change"},
        headers=_headers("CUSTOMER"),
    )
    assert denied_update.status_code == 403
    assert database["visatypes"].documents[0]["name"] == "Test visa"

    applications = client.get(
        "/api/visa-types/applications",
        headers=_headers("STAFF"),
    )
    assert applications.status_code == 200
    assert applications.json() == {"applications": [], "count": 0}

    requirements = client.get(
        "/api/requirements?includeArchived=true",
        headers=_headers("STAFF"),
    )
    assert requirements.status_code == 200
    assert requirements.json()["count"] == 0


def test_visa_type_create_and_update_preserve_the_previewed_code(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = entity_client
    headers = _headers("ADMIN")
    country_response = client.post(
        "/api/countries",
        json={"name": "Japan", "isoCode": "JP", "nationality": "JPN"},
        headers=headers,
    )
    assert country_response.status_code == 201
    country_id = country_response.json()["country"]["_id"]
    visa_code = "VISA-JP-TOU-SIN-V90D-15D-S-KHM"
    payload = {
        "name": "Japan Tourist Visa",
        "countryId": country_id,
        "category": "Tourist",
        "entryType": "Single",
        "validity": "90 Days",
        "allowedStayDays": 15,
        "visaClass": "S",
        "applicantNationality": "KHM",
        "visaTypeCode": visa_code,
    }

    created = client.post("/api/visa-types", json=payload, headers=headers)
    assert created.status_code == 201
    visa_id = created.json()["visaType"]["_id"]
    assert created.json()["visaType"]["visaTypeCode"] == visa_code

    other_payload = {**payload, "visaTypeCode": f"{visa_code}-OTHER"}
    other = client.post("/api/visa-types", json=other_payload, headers=headers)
    assert other.status_code == 201
    duplicate_code = client.put(
        f"/api/visa-types/{visa_id}",
        json={**payload, "visaTypeCode": other_payload["visaTypeCode"]},
        headers=headers,
    )
    assert duplicate_code.status_code == 409

    updated_code = f"{visa_code}-V2"
    updated = client.put(
        f"/api/visa-types/{visa_id}",
        json={**payload, "visaTypeCode": updated_code},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["visaType"]["visaTypeCode"] == updated_code


def test_visa_type_and_requirements_save_together_or_roll_back(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
) -> None:
    client, database = entity_client
    headers = _headers("ADMIN")
    country = client.post(
        "/api/countries",
        json={"name": "Japan", "isoCode": "JP", "nationality": "JPN"},
        headers=headers,
    ).json()["country"]
    payload = {
        "name": "Japan Tourist Visa",
        "countryId": country["_id"],
        "visaTypeCode": "VISA-JP-TOU-SIN-V90D-15D-S-KHM",
        "category": "Tourist",
        "requirements": [
            {
                "name": "Applicant Photo",
                "code": "applicant_photo",
                "required": True,
                "acceptedFormats": ["jpg", "jpeg", "png"],
                "maxFileSize": 5_000_000,
                "status": "ACTIVE",
                "weight": 100,
                "displayOrder": 0,
            },
            {
                "name": "Travel Itinerary",
                "code": "travel_itinerary",
                "required": False,
                "acceptedFormats": ["pdf", "jpg", "jpeg", "png"],
                "maxFileSize": 10_000_000,
                "status": "INACTIVE",
                "weight": 10,
                "displayOrder": 1,
            },
        ],
    }

    requirements = database["visarequirements"]
    insert_requirement = requirements.insert_one

    invalid_formats = {
        **payload,
        "requirements": [
            {**payload["requirements"][0], "acceptedFormats": ["pdf", "jpg", "jpeg", "png"]},
            payload["requirements"][1],
        ],
    }
    rejected = client.post(
        "/api/visa-types/with-requirements",
        json=invalid_formats,
        headers=headers,
    )
    assert rejected.status_code == 400
    assert rejected.json()["failedRequirement"] == "Applicant Photo"
    assert "JPG, JPEG, and PNG only" in rejected.json()["message"]
    assert database["visatypes"].documents == []

    async def fail_on_itinerary(document):
        if document.get("name") == "Travel Itinerary":
            raise RuntimeError("simulated storage failure")
        await insert_requirement(document)

    with monkeypatch.context() as patch:
        patch.setattr(requirements, "insert_one", fail_on_itinerary)
        failed = client.post(
            "/api/visa-types/with-requirements",
            json=payload,
            headers=headers,
        )

    assert failed.status_code == 503
    assert failed.json()["failedRequirement"] == "Travel Itinerary"
    assert "simulated storage failure" in failed.json()["message"]
    assert database["visatypes"].documents == []
    assert database["visarequirements"].documents == []
    assert database["requirementversions"].documents == []

    saved = client.post(
        "/api/visa-types/with-requirements",
        json=payload,
        headers=headers,
    )
    assert saved.status_code == 201
    saved_requirements = saved.json()["requirements"]
    assert len(saved_requirements) == 2
    assert set(saved_requirements[0]["acceptedFormats"]) == {"jpg", "jpeg", "png"}
    assert saved_requirements[0]["weight"] == 100
    assert set(saved_requirements[1]["acceptedFormats"]) == {"pdf", "jpg", "jpeg", "png"}
    assert saved_requirements[1]["required"] is False
    assert saved_requirements[1]["maxFileSize"] == 10_000_000
    assert saved_requirements[1]["status"] == "INACTIVE"


def test_requirement_versions_preserve_application_snapshot_until_admin_recalculation(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    headers = _headers("ADMIN")
    database["visatypes"].documents.append(
        {
            "_id": "requirements-visa",
            "name": "Visitor",
            "active": True,
            "requirementsConfigurationVersion": 0,
        }
    )
    database["customers"].documents.append(
        {"_id": "requirements-customer", "fullName": "Snapshot Applicant"}
    )

    created = client.post(
        "/api/requirements",
        headers=headers,
        json={
            "visaTypeId": "requirements-visa",
            "name": "Passport",
            "code": "passport",
            "weight": 50,
            "required": True,
            "acceptedFormats": ["pdf", "jpg", "jpeg", "png"],
        },
    )
    assert created.status_code == 201
    requirement_id = created.json()["requirement"]["_id"]
    assert created.json()["requirement"]["configurationVersion"] == 1

    duplicate_name = client.post(
        "/api/requirements",
        headers=headers,
        json={
            "visaTypeId": "requirements-visa",
            "name": "Passport",
            "code": "travel_document",
            "weight": 10,
        },
    )
    duplicate_code = client.post(
        "/api/requirements",
        headers=headers,
        json={
            "visaTypeId": "requirements-visa",
            "name": "Travel document",
            "code": "passport",
            "weight": 10,
        },
    )
    assert duplicate_name.status_code == 409
    assert duplicate_code.status_code == 409

    application_response = client.post(
        "/api/visa-types/applications",
        headers=headers,
        json={
            "customerId": "requirements-customer",
            "visaTypeId": "requirements-visa",
        },
    )
    assert application_response.status_code == 201
    application = application_response.json()["application"]
    application_id = application["_id"]
    assert application["requirementSnapshot"][0]["weight"] == 50
    assert application["requirementsConfigurationVersion"] == 1

    database["documents"].documents.append(
        {
            "_id": "requirements-passport",
            "applicationId": application_id,
            "requirementId": requirement_id,
            "status": "VERIFIED",
            "ocrStatus": "success",
        }
    )
    changed = client.put(
        f"/api/requirements/{requirement_id}",
        headers=headers,
        json={"weight": 60},
    )
    assert changed.status_code == 200
    assert changed.json()["requirement"]["configurationVersion"] == 2
    assert database["visaapplications"].documents[0]["requirementSnapshot"][0][
        "weight"
    ] == 50

    history = client.get(
        f"/api/requirements/{requirement_id}/history",
        headers=headers,
    )
    assert history.status_code == 200
    versions = history.json()["history"]
    assert [item["action"] for item in reversed(versions)] == ["CREATE", "UPDATE"]
    assert versions[0]["adminId"] == "test-user"
    assert versions[0]["previous"]["weight"] == 50
    assert versions[0]["current"]["weight"] == 60

    blocked = client.post(
        f"/api/requirements/applications/{application_id}/recalculate",
        headers=_headers("STAFF"),
        json={"reason": "Recalculate updated requirements"},
    )
    assert blocked.status_code == 403
    recalculated = client.post(
        f"/api/requirements/applications/{application_id}/recalculate",
        headers=headers,
        json={"reason": "Recalculate updated requirements"},
    )
    assert recalculated.status_code == 200
    updated = database["visaapplications"].documents[0]
    assert updated["requirementSnapshot"][0]["weight"] == 60
    assert updated["requirementsConfigurationVersion"] == 2
    assert updated["maximumPoints"] == 60
    assert updated["earnedPoints"] == 60
    audit = updated["requirementRecalculationHistory"][0]
    assert audit["reviewedBy"] == "test-user"
    assert audit["previousRequirementSnapshot"][0]["weight"] == 50
    assert audit["requirementSnapshot"][0]["weight"] == 60


def test_application_list_shows_metrics_only_after_verification(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["customers"].documents.append(
        {"_id": "metrics-customer", "fullName": "Metrics Applicant"}
    )
    database["visatypes"].documents.append(
        {
            "_id": "metrics-visa",
            "name": "Visitor",
            "destination": "Japan",
            "visaTypeCode": "VISA-JP-TOU-SIN-V90D-15D-S-KHM",
        }
    )
    database["visaapplications"].documents.append(
        {
            "_id": "metrics-application",
            "customer": "metrics-customer",
            "visaTypeId": "metrics-visa",
            "status": "DRAFT",
            "createdAt": datetime.now(timezone.utc),
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 100,
                },
                {
                    "requirementId": "bank",
                    "name": "Bank statement",
                    "required": True,
                    "weight": 60,
                },
            ],
        }
    )
    database["documents"].documents.extend(
        [
            {
                "_id": "verified-passport",
                "applicationId": "metrics-application",
                "requirementId": "passport",
                "status": "VERIFIED",
                "awardedPoints": 60,
                "ocrStatus": "OCR_COMPLETED",
                "ocrTotalPages": 1,
                "ocrPagesProcessed": 1,
                "ocrProgressPercent": 100,
                "ocrPageNumbers": [1],
            },
            {
                "_id": "pending-bank",
                "applicationId": "metrics-application",
                "requirementId": "bank",
                "status": "PENDING",
                "ocrStatus": "OCR_COMPLETED",
                "ocrTotalPages": 2,
                "ocrPagesProcessed": 2,
                "ocrProgressPercent": 100,
                "ocrPageNumbers": [1, 2],
            },
        ]
    )

    response = client.get(
        "/api/visa-types/applications",
        headers=_headers("STAFF"),
    )

    assert response.status_code == 200
    application = response.json()["applications"][0]
    assert application["hasVerificationResult"] is False
    assert "documentReadiness" not in application
    assert "verificationPercentage" not in application

    verification = client.post(
        "/api/verification/run",
        json={"applicationId": "metrics-application"},
        headers=_headers("STAFF"),
    )
    assert verification.status_code == 200

    updated_response = client.get(
        "/api/visa-types/applications",
        headers=_headers("STAFF"),
    )
    updated_application = updated_response.json()["applications"][0]
    assert updated_application["hasVerificationResult"] is True
    assert updated_application["documentReadiness"] == 62.5
    assert updated_application["verificationPercentage"] == 50
    assert "provisionalStatus" not in updated_application
    dashboard = client.get("/api/dashboard", headers=_headers("STAFF"))
    assert dashboard.status_code == 200
    assert dashboard.json()["recentApplications"][0]["visaTypeCode"] == (
        "VISA-JP-TOU-SIN-V90D-15D-S-KHM"
    )
    trend = dashboard.json()["analytics"]["applicationTrend"]
    assert len(trend) == 30
    assert sum(day["total"] for day in trend) == 1


def test_deleted_applications_are_hidden_from_list_without_deleting_records(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    collection = database["visaapplications"]
    collection.documents.extend(
        [
            {"_id": "active-app", "status": "DRAFT"},
            {"_id": "deleted-app", "status": "DRAFT"},
        ]
    )
    database["documents"].documents.extend(
        [
            {
                "_id": "active-verified",
                "applicationId": "active-app",
                "status": "VERIFIED",
            },
            {
                "_id": "active-pending",
                "applicationId": "active-app",
                "status": "PENDING",
            },
            {
                "_id": "active-rejected",
                "applicationId": "active-app",
                "status": "REJECTED",
            },
            {
                "_id": "deleted-app-pending",
                "applicationId": "deleted-app",
                "status": "PENDING",
            },
            {
                "_id": "removed-document",
                "applicationId": "active-app",
                "status": "DELETED",
            },
        ]
    )
    deleted = client.delete(
        "/api/visa-types/applications/deleted-app",
        headers=_headers("ADMIN"),
    )

    response = client.get(
        "/api/visa-types/applications",
        headers=_headers("STAFF"),
    )
    dashboard = client.get(
        "/api/dashboard",
        headers=_headers("STAFF"),
    )

    assert response.status_code == 200
    assert [item["_id"] for item in response.json()["applications"]] == [
        "active-app"
    ]
    assert dashboard.status_code == 200
    assert dashboard.json()["metrics"]["totalVisaApplications"] == 1
    assert dashboard.json()["metrics"]["verifiedDocuments"] == 1
    assert dashboard.json()["metrics"]["pendingDocuments"] == 1
    assert dashboard.json()["metrics"]["rejectedDocuments"] == 1
    assert dashboard.json()["metrics"]["failedDocuments"] == 0
    assert [
        item["_id"] for item in dashboard.json()["recentApplications"]
    ] == ["active-app"]
    assert len(collection.documents) == 2
    deleted_record = next(
        item for item in collection.documents if item["_id"] == "deleted-app"
    )
    assert deleted.status_code == 200
    assert deleted_record["status"] == "CANCELLED"
    assert deleted_record["deletedAt"] is not None


def test_dashboard_resolves_customer_id_and_visa_details_from_live_records(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["customers"].documents.append(
        {"_id": "dashboard-customer", "fullName": "Real Applicant"}
    )
    database["visatypes"].documents.append(
        {
            "_id": "dashboard-visa",
            "name": "Tourist Visa",
            "visaTypeCode": "VISA-TOURIST",
        }
    )
    database["visaapplications"].documents.append(
        {
            "_id": "dashboard-application",
            "customerId": "dashboard-customer",
            "visaTypeId": "dashboard-visa",
            "status": "DRAFT",
        }
    )

    response = client.get("/api/dashboard", headers=_headers("STAFF"))

    assert response.status_code == 200
    application = response.json()["recentApplications"][0]
    assert application["applicant"] == "Real Applicant"
    assert application["visaTypeName"] == "Tourist Visa"
    assert application["visaTypeCode"] == "VISA-TOURIST"


def test_dashboard_bank_score_uses_canonical_computed_score_not_awarded_points(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    application_id = "dashboard-bank-score-application"
    components = {
        "ending_balance": {"points": 7.88, "maximum": 22.5, "available": True},
        "starting_balance": {"points": 6.66, "maximum": 6.75, "available": True},
        "transaction_history": {"points": 8.75, "maximum": 9.0, "available": True},
        "transaction_period": {"points": 6.75, "maximum": 6.75, "available": True},
    }
    bank_score = calculate_bank_statement_score(components)
    bank_score["points"] = 0
    database["visaapplications"].documents.append(
        {
            "_id": application_id,
            "status": "UNDER_VERIFICATION",
            "requirementSnapshot": [
                {
                    "requirementId": "dashboard-bank-requirement",
                    "code": "bank_statement",
                    "name": "Bank Statement",
                    "weight": 45,
                }
            ],
        }
    )
    database["documents"].documents.append(
        {
            "_id": "dashboard-bank-document",
            "applicationId": application_id,
            "requirementId": "dashboard-bank-requirement",
            "requirementCode": "bank_statement",
            "status": "VERIFIED",
            "awardedPoints": 0,
            "bankStatementAnalysis": {
                "financialRequirementStatus": "PASS",
                "bankStatementScore": bank_score,
            },
        }
    )

    response = client.get("/api/dashboard", headers=_headers("STAFF"))

    assert response.status_code == 200
    score = response.json()["recentApplications"][0]["documentVerification"][0][
        "score"
    ]
    assert score == {"points": 30.04, "maximum": 45}


def test_admin_user_list_returns_real_accounts_without_password_fields(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["users"].documents.extend(
        [
            {
                "_id": "admin-user",
                "name": "Real Admin",
                "email": "admin@example.test",
                "role": "ADMIN",
                "password": "hashed-secret",
            },
            {
                "_id": "customer-user",
                "name": "Real Customer",
                "email": "customer@example.test",
                "role": "CUSTOMER",
                "passwordHash": "hashed-secret",
            },
        ]
    )

    response = client.get("/api/users", headers=_headers("ADMIN"))

    assert response.status_code == 200
    users = response.json()["users"]
    assert [user["name"] for user in users] == ["Real Admin", "Real Customer"]
    assert all("password" not in user for user in users)
    assert all("passwordHash" not in user for user in users)


def test_verification_honors_justified_conditional_exceptions(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "application-1",
            "requirementSnapshot": [
                {
                    "requirementId": "required-1",
                    "name": "Passport",
                    "required": True,
                    "conditional": False,
                    "weight": 70,
                },
                {
                    "requirementId": "conditional-1",
                    "name": "Employment letter",
                    "required": True,
                    "conditional": True,
                    "weight": 30,
                },
            ],
        }
    )
    database["documents"].documents.append(
        {
            "_id": "document-1",
            "applicationId": "application-1",
            "requirementId": "required-1",
            "status": "VERIFIED",
            "ocrStatus": "success",
            "awardedPoints": 70,
        }
    )

    response = client.post(
        "/api/verification/run",
        json={
            "applicationId": "application-1",
            "notApplicableRequirements": {
                "conditional-1": "Applicant is currently unemployed"
            },
        },
        headers=_headers("STAFF"),
    )

    assert response.status_code == 200
    verification = response.json()["verification"]
    assert verification["status"] == "READY_FOR_STAFF_REVIEW"
    assert verification["completenessScore"] == 100
    assert verification["totalPoints"] == 70
    assert verification["earnedPoints"] == 70
    exception = next(
        item
        for item in verification["results"]
        if item["requirementId"] == "conditional-1"
    )
    assert exception["status"] == "NOT_APPLICABLE"
    assert exception["explanationEn"] == "Applicant is currently unemployed"


def test_verification_recalculates_and_saves_score_from_current_document_statuses(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "application-1",
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 60,
                },
                {
                    "requirementId": "income-letter",
                    "name": "Income letter",
                    "required": True,
                    "weight": 40,
                },
            ],
        }
    )
    database["documents"].documents.extend(
        [
            {
                "_id": "passport-document",
                "applicationId": "application-1",
                "requirementId": "passport",
                "status": "VERIFIED",
                "ocrStatus": "success",
                "awardedPoints": 60,
            },
            {
                "_id": "statement-document",
                "applicationId": "application-1",
                "requirementId": "income-letter",
                "status": "PENDING",
                "ocrStatus": "manual_review",
            },
        ]
    )

    first_response = client.post(
        "/api/verification/run",
        json={"applicationId": "application-1"},
        headers=_headers("STAFF"),
    )
    assert first_response.status_code == 200
    first_verification = first_response.json()["verification"]
    assert first_verification["completenessScore"] == 60
    assert first_verification["totalPoints"] == 100
    assert first_verification["earnedPoints"] == 60
    assert first_verification["status"] == "NEEDS_REVIEW"
    assert first_verification["verificationMethod"] == "OCR_COMPLETENESS"
    assert "provisionalStatus" not in first_verification
    assert database["visaapplications"].documents[0]["completenessScore"] == 60

    database["documents"].documents[1]["status"] = "VERIFIED"
    database["documents"].documents[1]["awardedPoints"] = 40
    second_response = client.post(
        "/api/verification/run",
        json={"applicationId": "application-1"},
        headers=_headers("STAFF"),
    )

    assert second_response.status_code == 200
    second_verification = second_response.json()["verification"]
    assert second_verification["completenessScore"] == 100
    assert second_verification["totalPoints"] == 100
    assert second_verification["earnedPoints"] == 100
    assert second_verification["status"] == "READY_FOR_STAFF_REVIEW"
    assert database["visaapplications"].documents[0]["completenessScore"] == 100
    assert len(database["verifications"].documents) == 2


def test_ocr_completed_documents_get_immediate_check_without_staff_review(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "application-pending",
            "customer": "test-user",
            "completenessScore": 0,
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 60,
                },
                {
                    "requirementId": "photo",
                    "name": "Applicant photo",
                    "required": True,
                    "weight": 40,
                },
            ],
        }
    )
    database["documents"].documents.extend(
        [
            {
                "_id": "pending-passport",
                "applicationId": "application-pending",
                "requirementId": "passport",
                "status": "PENDING",
                "ocrStatus": "success",
            },
            {
                "_id": "pending-photo",
                "applicationId": "application-pending",
                "requirementId": "photo",
                "status": "PENDING",
                "ocrStatus": "manual_review",
            },
        ]
    )

    response = client.post(
        "/api/verification/run",
        json={"applicationId": "application-pending"},
        headers=_headers("CUSTOMER"),
    )

    assert response.status_code == 200
    verification = response.json()["verification"]
    assert verification["status"] == "NEEDS_REVIEW"
    assert verification["completenessScore"] == 0
    assert verification["verificationPercentage"] == 0
    assert all(result["status"] == "INSUFFICIENT_DATA" for result in verification["results"])
    assert database["visaapplications"].documents[0]["completenessScore"] == 0
    assert "provisionalStatus" not in database["visaapplications"].documents[0]
    assert all(
        document["status"] == "PENDING"
        for document in database["documents"].documents
    )


def test_rejected_document_fails_verification_instead_of_appearing_missing(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "application-1",
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 100,
                }
            ],
        }
    )
    database["documents"].documents.append(
        {
            "_id": "rejected-passport",
            "applicationId": "application-1",
            "requirementId": "passport",
            "status": "REJECTED",
            "ocrStatus": "success",
        }
    )

    response = client.post(
        "/api/verification/run",
        json={"applicationId": "application-1"},
        headers=_headers("STAFF"),
    )

    assert response.status_code == 200
    verification = response.json()["verification"]
    assert verification["status"] == "NEEDS_REVIEW"
    assert verification["results"][0]["status"] == "FAIL"


def test_verification_is_blocked_until_all_ocr_pages_are_complete(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "application-ocr",
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 100,
                }
            ],
        }
    )
    document = {
        "_id": "passport-ocr",
        "applicationId": "application-ocr",
        "requirementId": "passport",
        "fileName": "passport.pdf",
        "status": "PENDING",
        "workflowStatus": "OCR_PROCESSING",
        "ocrStatus": "OCR_PROCESSING",
        "ocrPagesProcessed": 2,
        "ocrTotalPages": 3,
        "ocrProgressPercent": 67,
    }
    database["documents"].documents.append(document)

    processing = client.post(
        "/api/verification/run",
        json={"applicationId": "application-ocr"},
        headers=_headers("STAFF"),
    )
    assert processing.status_code == 409
    assert "passport.pdf" in processing.json()["message"]
    assert not database["verifications"].documents

    document.update(
        {
            "workflowStatus": "OCR_COMPLETED",
            "ocrStatus": "OCR_COMPLETED",
            "ocrPagesProcessed": 2,
            "ocrPageNumbers": [1, 2, 3],
            "ocrProgressPercent": 100,
        }
    )
    incomplete = client.post(
        "/api/verification/run",
        json={"applicationId": "application-ocr"},
        headers=_headers("STAFF"),
    )
    assert incomplete.status_code == 409
    assert not database["verifications"].documents

    document["ocrPagesProcessed"] = 3
    completed = client.post(
        "/api/verification/run",
        json={"applicationId": "application-ocr"},
        headers=_headers("STAFF"),
    )
    assert completed.status_code == 200


@pytest.mark.parametrize(
    ("filename", "content", "mime_type"),
    [
        ("passport.png", b"\x89PNG\r\n\x1a\nimage", "image/png"),
        ("passport.pdf", b"%PDF-1.7\nfile", "application/pdf"),
    ],
)
def test_upload_starts_ocr_instead_of_waiting_for_it(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
    temporary_model_dir: Path,
    filename: str,
    content: bytes,
    mime_type: str,
) -> None:
    client, database = entity_client
    application = {
        "_id": "application-upload",
        "customer": "test-user",
        "visaTypeId": "visa-type",
        "verificationMetricsCurrent": True,
        "documentReadiness": 100,
        "verificationPercentage": 100,
    }
    requirement = {
        "_id": "passport-requirement",
        "visaTypeId": "visa-type",
        "code": "passport",
        "acceptedFormats": ["png", "pdf"],
        "maxFileSize": 10 * 1024 * 1024,
        "allowMultiple": False,
    }
    database["visaapplications"].documents.append(application)
    database["visarequirements"].documents.append(requirement)
    monkeypatch.setattr(
        documents_api, "_PRIVATE_DIR", temporary_model_dir
    )
    monkeypatch.setattr(
        documents_api,
        "_schedule_document_ocr",
        lambda _document, _database, _attempt: True,
    )

    response = client.post(
        "/api/documents/upload",
        data={
            "applicationId": "application-upload",
            "requirementId": "passport-requirement",
        },
        files={"file": (filename, content, mime_type)},
        headers=_headers("CUSTOMER"),
    )

    assert response.status_code == 201
    document = response.json()["document"]
    assert document["workflowStatus"] == "PROCESSING"
    assert document["ocrStatus"] == "OCR_PENDING"
    assert document["status"] == "PROCESSING"
    assert document["ocrProgressPercent"] == 0
    assert Path(document["fileName"]).name == filename
    stored_document = database["documents"].documents[0]
    stored_application = database["visaapplications"].documents[0]
    assert stored_application["verificationMetricsCurrent"] is False
    stored_document.update(
        {
            "workflowStatus": "OCR_PROCESSING",
            "ocrStatus": "OCR_PROCESSING",
            "ocrPagesProcessed": 2,
            "ocrTotalPages": 5,
            "ocrProgressPercent": 40,
        }
    )
    progress = client.get(
        f"/api/documents/{document['_id']}/processing",
        headers=_headers("CUSTOMER"),
    )
    assert progress.status_code == 200
    assert progress.json()["pagesProcessed"] == 2
    assert progress.json()["totalPages"] == 5
    assert progress.json()["progressPercent"] == 40


def test_photo_upload_uses_photo_checks_without_starting_ocr(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    client, database = entity_client
    application = {
        "_id": "application-photo-upload",
        "customer": "test-user",
        "visaTypeId": "visa-type",
    }
    requirement = {
        "_id": "photo-requirement",
        "visaTypeId": "visa-type",
        "code": "applicant_photo",
        "name": "Photo",
        "acceptedFormats": ["png"],
        "maxFileSize": 10 * 1024 * 1024,
        "allowMultiple": False,
    }
    database["visaapplications"].documents.append(application)
    database["visarequirements"].documents.append(requirement)
    monkeypatch.setattr(documents_api, "_PRIVATE_DIR", temporary_model_dir)
    scheduled = []
    monkeypatch.setattr(
        documents_api,
        "_schedule_document_ocr",
        lambda *_args: scheduled.append(True) or True,
    )

    async def verify_photo(document, db, *, ocr_status, content_hash):
        assert ocr_status == "NOT_REQUIRED"
        await db["documents"].update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "status": "NEEDS_REVIEW",
                    "workflowStatus": "NEEDS_REVIEW",
                    "ocrStatus": "NOT_REQUIRED",
                    "ocrMessage": "OCR is not required for applicant photos.",
                    "contentHash": content_hash,
                }
            },
        )
        return {"status": "NEEDS_REVIEW"}

    monkeypatch.setattr(
        documents_api, "_run_automated_document_verification", verify_photo
    )
    response = client.post(
        "/api/documents/upload",
        data={
            "applicationId": "application-photo-upload",
            "requirementId": "photo-requirement",
        },
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\nphoto", "image/png")},
        headers=_headers("CUSTOMER"),
    )

    assert response.status_code == 201
    document = response.json()["document"]
    assert document["requirementCode"] == "photo"
    assert document["ocrStatus"] == "NOT_REQUIRED"
    assert document["status"] == "NEEDS_REVIEW"
    assert scheduled == []
    assert documents_api._document_type({"code": "applicant_photo"}) == "photo"


def test_retrying_photo_revalidates_image_without_starting_ocr(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
) -> None:
    client, database = entity_client
    database["documents"].documents.append(
        {
            "_id": "failed-photo",
            "applicationId": "application-photo",
            "customerId": "test-user",
            "requirementId": "photo-requirement",
            "requirementCode": "applicant_photo",
            "status": "FAILED",
            "workflowStatus": "OCR_FAILED",
            "ocrStatus": "OCR_FAILED",
            "ocrAttempt": 1,
        }
    )
    scheduled = []
    monkeypatch.setattr(
        documents_api,
        "_schedule_document_ocr",
        lambda *_args: scheduled.append(True) or True,
    )

    async def verify_photo(document, db, *, ocr_status, content_hash):
        assert ocr_status == "NOT_REQUIRED"
        await db["documents"].update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "status": "NEEDS_REVIEW",
                    "workflowStatus": "NEEDS_REVIEW",
                    "ocrStatus": "NOT_REQUIRED",
                    "ocrMessage": "OCR is not required for applicant photos.",
                }
            },
        )
        return {"status": "NEEDS_REVIEW"}

    monkeypatch.setattr(
        documents_api, "_run_automated_document_verification", verify_photo
    )
    response = client.post(
        "/api/documents/failed-photo/ocr/retry",
        headers=_headers("CUSTOMER"),
    )

    assert response.status_code == 200
    assert response.json()["document"]["ocrStatus"] == "NOT_REQUIRED"
    assert response.json()["document"]["status"] == "NEEDS_REVIEW"
    assert scheduled == []


def test_verification_rejects_a_duplicate_in_progress_request(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "application-running",
            "verificationInProgress": True,
            "requirementSnapshot": [],
        }
    )

    response = client.post(
        "/api/verification/run",
        json={"applicationId": "application-running"},
        headers=_headers("STAFF"),
    )

    assert response.status_code == 409
    assert response.json()["message"] == "Verification is already in progress"
    assert not database["verifications"].documents


def test_ocr_retry_clears_stale_results_and_returns_to_processing(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
) -> None:
    client, database = entity_client
    document = {
        "_id": "retry-document",
        "applicationId": "retry-application",
        "customerId": "test-user",
        "status": "FAILED",
        "workflowStatus": "FAILED",
        "ocrStatus": "OCR_FAILED",
        "ocrAttempt": 1,
        "extractedFields": {"fullName": "Old OCR text"},
        "automatedVerification": {"status": "FAILED"},
        "automatedVerificationStatus": "FAILED",
        "bankStatementAnalysis": {"financialRequirementStatus": "FAIL"},
        "rejectionReason": "Previous OCR attempt failed",
        "awardedPoints": 0,
    }
    database["documents"].documents.append(document)
    monkeypatch.setattr(
        documents_api,
        "_schedule_document_ocr",
        lambda _document, _database, _attempt: True,
    )

    response = client.post(
        "/api/documents/retry-document/ocr/retry",
        headers=_headers("CUSTOMER"),
    )

    assert response.status_code == 200
    retried = response.json()["document"]
    assert retried["status"] == "PROCESSING"
    assert retried["ocrStatus"] == "OCR_PENDING"
    assert retried["extractedFields"] == {}
    assert retried["automatedVerification"] is None
    assert retried["bankStatementAnalysis"] is None
    assert retried["rejectionReason"] == ""


def test_manual_document_review_requires_a_reason_and_records_overrides(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    document = {
        "_id": "document-review",
        "applicationId": "application-review",
        "customerId": "test-user",
        "status": "PENDING",
        "ocrStatus": "OCR_PROCESSING",
        "ocrPagesProcessed": 0,
        "ocrTotalPages": 2,
    }
    database["documents"].documents.append(document)
    blocked_ocr = client.patch(
        "/api/documents/document-review/review",
        json={
            "status": "VERIFIED",
            "reason": "The original document is not available",
        },
        headers=_headers("STAFF"),
    )
    assert blocked_ocr.status_code == 409
    assert "complete OCR evidence" in blocked_ocr.json()["message"]

    document.update(
        {
            "ocrStatus": "OCR_COMPLETED",
            "ocrPagesProcessed": 2,
            "ocrTotalPages": 2,
            "ocrPageNumbers": [1, 2],
            "ocrProgressPercent": 100,
        }
    )
    manual_review = client.patch(
        "/api/documents/document-review/review",
        json={
            "status": "REJECTED",
            "rejectionReason": "Supporting evidence is inconsistent",
        },
        headers=_headers("STAFF"),
    )
    assert manual_review.status_code == 200
    assert document["status"] == "REJECTED"
    assert document["manualReviewHistory"][0]["actorRole"] == "STAFF"
    assert document["manualReviewHistory"][0]["reason"] == (
        "Supporting evidence is inconsistent"
    )

    database["verifications"].documents.append(
        {
            "_id": "verification-review",
            "applicationId": "application-review",
            "results": [{"documentIds": ["document-review"]}],
        }
    )
    manual_override = client.patch(
        "/api/documents/document-review/review",
        json={
            "status": "VERIFIED",
            "reason": "Additional original evidence confirmed",
        },
        headers=_headers("STAFF"),
    )
    assert manual_override.status_code == 200
    assert document["status"] == "VERIFIED"
    assert len(document["manualReviewHistory"]) == 2


def test_source_review_report_reload_uses_current_persisted_bank_score(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
) -> None:
    client, database = entity_client
    application_id = "score-application"
    document_id = "DOC-7D04FFD1525F"
    requirement = {
        "_id": "score-bank",
        "requirementId": "score-bank",
        "visaTypeId": "score-visa",
        "name": "Bank statement",
        "code": "bank_statement",
        "status": "ACTIVE",
        "required": True,
        "weight": 45,
    }
    database["visaapplications"].documents.append(
        {
            "_id": application_id,
            "customer": "test-user",
            "visaType": "score-visa",
            "applicationNumber": "VD-SCORE",
            "requirementSnapshot": [requirement],
        }
    )
    database["customers"].documents.append(
        {"_id": "test-user", "fullName": "Test Applicant"}
    )
    database["visatypes"].documents.append(
        {"_id": "score-visa", "name": "Visitor", "destination": "Australia"}
    )
    database["visarequirements"].documents.append(requirement)
    transactions = [
        {
            "date": "2026-03-20",
            "description": f"Wire transfer {index}",
            "amount": 2000,
            "type": "credit",
            "direction": "credit",
            "currency": "USD",
            "balance": 6000 + index,
            "page": 2,
            "referenceNumber": f"WIRE-{index}",
        }
        for index in range(40)
    ]
    fields = {
        "currency": "USD",
        "statementStartDate": "2026-01-01",
        "statementEndDate": "2026-03-31",
        "transactionHistory": transactions,
    }

    def score_with_configured_source_penalty(_fields, **kwargs):
        source_reviews = kwargs.get("transaction_source_reviews") or {}
        configured_penalty = float(
            kwargs["config"]["unverified_source_penalty_points"]
        )
        penalty = (
            configured_penalty
            if source_reviews.get("0", {}).get("status") == "UNVERIFIED"
            else 0
        )
        components = {
            "ending_balance": {"points": 7.88, "maximum": 22.5, "available": True},
            "starting_balance": {"points": 6.66, "maximum": 6.75, "available": True},
            "transaction_history": {
                "points": 8.75 - penalty,
                "maximum": 9.0,
                "available": True,
            },
            "transaction_period": {"points": 6.75, "maximum": 6.75, "available": True},
        }
        score = calculate_bank_statement_score(components)
        score.update(
            {
                "totalPenaltyPoints": penalty,
                "transactionReviewRequirements": {
                    "unverifiedSourcePenaltyPoints": configured_penalty,
                    "maximumBankScore": 45,
                },
                "transactionReviews": [
                    {
                        "transactionIndex": index,
                        "requiresStaffReview": True,
                        "status": (
                            "UNVERIFIED"
                            if index == 0 and penalty
                            else "PENDING_REVIEW"
                        ),
                        "amount": transaction["amount"],
                        "currency": "USD",
                        "amountUsd": 2000,
                        "exchangeRateToUsd": 1,
                        "staffReviewThresholdUsd": 1000,
                        "reference": transaction["referenceNumber"],
                        "sourceEvidence": "Statement transaction row",
                        "evidenceReferences": [],
                    }
                    for index, transaction in enumerate(transactions)
                ],
            }
        )
        return {
            "transactions": transactions,
            "bankStatementScore": score,
            "financialRequirementStatus": "PASS",
            "reconciliation": {"status": "RECONCILED"},
        }

    monkeypatch.setattr(
        bank_statements_api, "score_bank_statement", score_with_configured_source_penalty
    )

    async def skip_aba_invalidation(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bank_statements_api, "_invalidate_aba_case", skip_aba_invalidation)
    pre_review_analysis = score_with_configured_source_penalty(
        fields,
        config={"unverified_source_penalty_points": 0.5},
    )
    database["documents"].documents.append(
        {
            "_id": document_id,
            "applicationId": application_id,
            "requirementId": "score-bank",
            "requirementCode": "bank_statement",
            "status": "NEEDS_REVIEW",
            "ocrStatus": "OCR_COMPLETED",
            "extractedFields": fields,
            "bankStatementAnalysis": pre_review_analysis,
        }
    )

    saved = client.post(
        f"/api/bank-statements/documents/{document_id}/transactions/0/source-review",
        headers=_headers("STAFF"),
        json={"action": "SOURCE_UNVERIFIED", "sourceCategory": "UNKNOWN"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["bankStatementAnalysis"]["computedBankScore"]["points"] == 29.54
    assert saved.json()["bankStatementAnalysis"]["bankStatementScore"]["pendingTransactionReviews"] == 39
    assert saved.json()["bankStatementAnalysis"]["bankStatementScore"]["totalPenaltyPoints"] == 0.5
    assert saved.json()["bankStatementAnalysis"]["sourceReviewStatus"] == "UNVERIFIED"

    database["verifications"].documents.append(
        {
            "_id": "old-score-assessment",
            "applicationId": application_id,
            "createdAt": "2026-11-20T12:00:00Z",
            "results": [
                {
                    "bankStatements": [
                        {
                            "documentId": document_id,
                            "analysis": pre_review_analysis,
                        }
                    ]
                }
            ],
        }
    )
    reloaded = client.get(
        f"/api/verification/results/{application_id}",
        headers=_headers("STAFF"),
    )
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.headers["cache-control"] == "no-store"
    report_score = next(
        item
        for item in reloaded.json()["summary"]["bankStatementScore"]
        if item["documentId"] == document_id
    )
    report_document = next(
        item
        for item in reloaded.json()["documents"]
        if item["documentId"] == document_id
    )
    detailed_score = report_document["bankStatementAnalysis"]
    history = next(
        item
        for item in detailed_score["computedBankScore"]["components"]
        if item["key"] == "transaction_history"
    )
    displayed_categories = {
        item["key"]: item["points"]
        for item in detailed_score["computedBankScore"]["components"]
    }

    assert report_score["computedBankScore"]["points"] == 29.54
    assert report_score["requirementContribution"]["points"] == 29.54
    assert report_score["computedBankScore"]["maximum"] == 45
    assert report_score["score"]["points"] == report_score["computedBankScore"]["points"]
    assert (
        report_score["bankDiagnosticScore"]["points"]
        == report_score["computedBankScore"]["points"]
    )
    assert displayed_categories == {
        "ending_balance": 7.88,
        "starting_balance": 6.66,
        "transaction_history": 8.25,
        "transaction_period": 6.75,
    }
    assert history["points"] == 8.25
    assert sum(displayed_categories.values()) == 29.54
    assert detailed_score["bankStatementScore"]["points"] == 29.54
    assert detailed_score["computedBankScore"]["points"] == 29.54
    assert detailed_score["requirementContribution"]["points"] == 29.54
    assert detailed_score["bankStatementScore"]["pendingTransactionReviews"] == 39
    assert detailed_score["confirmedBankScore"]["status"] == "NOT_YET_CONFIRMED"
    assert detailed_score["staffReviewStatus"] == "PENDING_REVIEW"
    assert detailed_score["documentReviewStatus"] == "PENDING_REVIEW"
    assert detailed_score["authenticityStatus"] == "NOT_VERIFIED"


def test_verification_results_are_customer_scoped_and_mask_passport(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "report-application",
            "customer": "test-user",
            "visaType": "report-visa",
            "applicationNumber": "VD-REPORT",
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 100,
                }
            ],
        }
    )
    database["customers"].documents.append(
        {
            "_id": "test-user",
            "fullName": "Test Applicant",
            "passportNumber": "AB1234567",
            "nationality": "Cambodian",
        }
    )
    database["visatypes"].documents.append(
        {"_id": "report-visa", "name": "Visitor", "destination": "Australia"}
    )

    response = client.get(
        "/api/verification/results/report-application",
        headers=_headers("CUSTOMER"),
    )

    assert response.status_code == 200
    assert response.json()["applicant"]["passportNumber"] == "*****4567"
    assert response.json()["summary"]["verificationPercentage"] == 0
    assert response.json()["prediction"]["status"] == "UNAVAILABLE"

    database["documents"].documents.append(
        {
            "_id": "report-passport",
            "applicationId": "report-application",
            "requirementId": "passport",
            "status": "PENDING",
            "ocrStatus": "OCR_COMPLETED",
            "ocrTotalPages": 1,
            "ocrPagesProcessed": 1,
            "ocrProgressPercent": 100,
            "ocrPageNumbers": [1],
        }
    )
    database["verifications"].documents.append(
        {
            "_id": "report-automated-check",
            "applicationId": "report-application",
            "verificationMethod": "OCR_COMPLETENESS",
            "verificationPercentage": 100,
            "documentReadiness": 100,
        }
    )
    checked = client.get(
        "/api/verification/results/report-application",
        headers=_headers("CUSTOMER"),
    )
    assert checked.status_code == 200
    assert checked.json()["summary"]["verificationPercentage"] == 0
    assert checked.json()["summary"]["submissionCompleteness"] == 100
    assert checked.json()["summary"]["documentReadiness"] == 0
    assert "staffVerificationPercentage" not in checked.json()["summary"]
    assert "staffDocumentReadiness" not in checked.json()["summary"]

    database["visaapplications"].documents[0]["customer"] = "another-customer"
    hidden = client.get(
        "/api/verification/results/report-application",
        headers=_headers("CUSTOMER"),
    )
    assert hidden.status_code == 404


def test_prediction_and_finalization_are_blocked_while_ocr_is_pending(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.append(
        {
            "_id": "pending-application",
            "customer": "test-user",
            "requirementSnapshot": [],
        }
    )
    database["documents"].documents.append(
        {
            "_id": "pending-document",
            "applicationId": "pending-application",
            "status": "PENDING",
            "ocrStatus": "OCR_PROCESSING",
            "ocrPagesProcessed": 1,
            "ocrTotalPages": 2,
        }
    )

    prediction = client.post(
        "/api/verification/pending-application/predict",
        headers=_headers("STAFF"),
    )
    finalization = client.post(
        "/api/verification/pending-application/finalize",
        json={"status": "VERIFIED"},
        headers=_headers("STAFF"),
    )

    assert prediction.status_code == 409
    assert "OCR" in prediction.json()["message"]
    assert finalization.status_code == 409
    assert "OCR" in finalization.json()["message"]


def test_finalization_generates_one_receipt_and_public_qr_verifies_it(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
    temporary_receipt_dir: Path,
) -> None:
    client, database = entity_client
    monkeypatch.setattr(
        verification_reports_api, "_RECEIPT_DIR", temporary_receipt_dir
    )
    database["visaapplications"].documents.append(
        {
            "_id": "final-application",
            "customer": "test-user",
            "visaType": "final-visa",
            "applicationNumber": "VD-FINAL",
            "requirementSnapshot": [
                {
                    "requirementId": "passport",
                    "name": "Passport",
                    "required": True,
                    "weight": 100,
                }
            ],
            "predictionStatus": "UNAVAILABLE",
        }
    )
    database["customers"].documents.append(
        {"_id": "test-user", "fullName": "អ្នកសាកល្បង"}
    )
    database["visatypes"].documents.append(
        {"_id": "final-visa", "name": "Visitor", "destination": "Japan"}
    )
    database["documents"].documents.append(
        {
            "_id": "final-passport",
            "applicationId": "final-application",
            "requirementId": "passport",
            "fileName": "passport.png",
            "mimeType": "image/png",
            "status": "VERIFIED",
            "ocrStatus": "OCR_COMPLETED",
            "ocrPagesProcessed": 1,
            "ocrTotalPages": 1,
            "ocrProgressPercent": 100,
            "ocrPageNumbers": [1],
            "awardedPoints": 100,
        }
    )
    database["verifications"].documents.append(
        {
            "_id": "final-run",
            "applicationId": "final-application",
            "results": [],
        }
    )

    finalized = client.post(
        "/api/verification/final-application/finalize",
        json={"status": "VERIFIED", "comments": "Reviewed"},
        headers=_headers("STAFF"),
    )
    receipt_number = finalized.json()["receiptNumber"]
    repeated = client.post(
        "/api/verification/final-application/finalize",
        json={"status": "VERIFIED", "comments": "Reviewed"},
        headers=_headers("STAFF"),
    )
    token = database["visaapplications"].documents[0]["receiptVerificationToken"]
    verified = client.get(f"/api/verification/receipts/verify/{token}")

    assert finalized.status_code == 200
    assert (temporary_receipt_dir / f"{receipt_number}.pdf").is_file()
    assert repeated.status_code == 200
    assert repeated.json()["receiptNumber"] == receipt_number
    assert verified.status_code == 200
    assert verified.json()["valid"] is True
    assert verified.json()["receiptNumber"] == receipt_number


def test_ml_training_is_admin_only_and_document_listing_requires_authentication(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = entity_client

    unauthorized_documents = client.get("/api/documents")
    assert unauthorized_documents.status_code == 401
    status = client.get("/api/ml/model-status", headers=_headers("STAFF"))
    assert status.status_code == 200
    assert status.json()["prediction_available"] is False

    unavailable_prediction = client.post(
        "/api/ml/predict",
        json={"model_type": "visa", "features": {}},
        headers=_headers("STAFF"),
    )
    assert unavailable_prediction.status_code == 400
    assert "application_id" in unavailable_prediction.json()["message"]

    denied_training = client.post(
        "/api/ml/train",
        data={
            "model_type": "visa",
            "verified_outcomes": "true",
            "privacy_reviewed": "true",
            "source_description": "Authorized historical decisions",
            "label_mapping": '{"APPROVED":"APPROVED","REJECTED":"REJECTED"}',
        },
        files={"file": ("dataset.csv", b"group_id,visa_outcome\n", "text/csv")},
        headers=_headers("STAFF"),
    )
    assert denied_training.status_code == 403


def test_ml_status_metrics_and_rules_encode_nested_database_values(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    object_id = ObjectId()
    nested_object_id = ObjectId()
    trained_at = datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc)
    record = {
        "_id": object_id,
        "model_version": "visa-test-version",
        "model_type": "visa",
        "model_status": "trained_pending_independent_review",
        "row_count": 500,
        "trained_at": trained_at,
        "approved_at": None,
        "metrics": {
            "accuracy": 0.91,
            "confusion_matrix": [[120, 5], [8, 117]],
            "details": {
                "evaluated_date": date(2026, 11, 12),
                "legacy_reference": nested_object_id,
                "optional_note": None,
            },
        },
    }
    database["ml_model_versions"].documents.append(record)
    database["ml_active_models"].documents.append(
        {"_id": "visa", "model_version": "visa-test-version"}
    )
    headers = _headers("STAFF")

    status_response = client.get("/api/ml/model-status", headers=headers)
    metrics_response = client.get("/api/ml/metrics", headers=headers)
    rules_response = client.get("/api/rules", headers=headers)

    assert status_response.status_code == 200
    assert metrics_response.status_code == 200
    assert rules_response.status_code == 200
    status_model = status_response.json()["models"]["visa"]["latest"]
    metric_model = metrics_response.json()["models"][0]
    for model in (status_model, metric_model):
        assert model["trained_at"] == trained_at.isoformat()
        assert model["approved_at"] is None
        assert model["metrics"]["accuracy"] == 0.91
        assert model["metrics"]["confusion_matrix"] == [[120, 5], [8, 117]]
        assert model["metrics"]["details"] == {
            "evaluated_date": "2026-11-12",
            "legacy_reference": str(nested_object_id),
            "optional_note": None,
        }
    assert rules_response.json()["rules"]
    assert database["ml_model_versions"].documents[0]["_id"] is object_id
    assert database["ml_model_versions"].documents[0]["metrics"]["details"][
        "legacy_reference"
    ] is nested_object_id


def test_ml_template_download_and_dataset_preflight_accept_application_ids(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = entity_client
    headers = _headers("ADMIN")
    template = client.get("/api/ml/dataset-template", headers=headers)

    assert template.status_code == 200
    assert "attachment" in template.headers["content-disposition"]
    assert "application_id" in template.text.splitlines()[0]

    rows = ["Application ID,Visa Status,Travel History Count"]
    for index in range(500):
        label = "Visa Granted" if index % 2 == 0 else "Visa Refused"
        rows.append(f"application-{index},{label},{index % 5}")
    response = client.post(
        "/api/ml/validate-dataset",
        data={
            "model_type": "visa",
            "label_mapping": '{"APPROVED":"APPROVED","REJECTED":"REJECTED"}',
        },
        files={"file": ("historical.csv", "\n".join(rows).encode(), "text/csv")},
        headers=headers,
    )

    assert response.status_code == 200
    report = response.json()
    assert report["validation_status"] == "valid"
    assert report["ready_to_train"] is True
    assert report["total_records"] == 500
    assert report["valid_records"] == 500
    assert report["invalid_record_count"] == 0
    assert report["approved_count"] == 250
    assert report["rejected_count"] == 250
    assert report["group_id_source_columns"] == ["application_id"]
    assert report["feature_columns"] == ["travel_history_count"]
    assert report["group_counts"] == {"APPROVED": 250, "REJECTED": 250}


def test_ml_preflight_enforces_minimum_500_valid_rows_even_if_env_is_lower(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
) -> None:
    client, _database = entity_client
    monkeypatch.setenv("ML_MINIMUM_TRAINING_ROWS", "1")
    monkeypatch.setenv("ML_MINIMUM_PER_CLASS", "1")
    rows = ["group_id,visa_outcome,travel_history_count"]
    for index in range(499):
        label = "APPROVED" if index % 2 == 0 else "REJECTED"
        rows.append(f"group-{index},{label},{index % 5}")

    response = client.post(
        "/api/ml/validate-dataset",
        data={
            "model_type": "visa",
            "label_mapping": '{"APPROVED":"APPROVED","REJECTED":"REJECTED"}',
        },
        files={"file": ("historical.csv", "\n".join(rows).encode(), "text/csv")},
        headers=_headers("ADMIN"),
    )

    assert response.status_code == 200
    report = response.json()
    assert report["valid_records"] == 499
    assert report["validation_status"] == "insufficient_data"
    assert any("at least 500 are required" in message for message in report["messages"])


def test_ml_train_returns_validation_report_for_missing_visa_outcome(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = entity_client
    response = client.post(
        "/api/ml/train",
        data={
            "model_type": "visa",
            "algorithm": "lightgbm",
            "verified_outcomes": "true",
            "privacy_reviewed": "true",
            "source_description": "Authorized historical visa outcomes",
            "label_mapping": '{"APPROVED":"APPROVED","REJECTED":"REJECTED"}',
        },
        files={
            "file": (
                "missing-outcome.csv",
                b"application_id,travel_history_count\napp-1,2\n",
                "text/csv",
            )
        },
        headers=_headers("ADMIN"),
    )

    assert response.status_code == 409
    assert "visa_outcome" in response.json()["message"]
    assert response.json()["validation_report"]["missing_columns"] == [
        "visa_outcome"
    ]


def _store_test_model(
    database: FakeDatabase,
    model_dir,
    version: str,
    status: str,
) -> dict[str, Any]:
    artifact_dir = model_dir / "visa"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{version}.joblib"
    joblib.dump(
        {
            "model_type": "visa",
            "model_version": version,
            "pipeline": {"loaded": True},
        },
        artifact_path,
    )
    record = {
        "_id": version,
        "model_version": version,
        "model_type": "visa",
        "model_status": status,
        "validation_passed": True,
        "artifact_path": str(artifact_path.resolve()),
        "artifact_sha256": ml_api._sha256_file(artifact_path),
        "metrics": {"accuracy": 0.8},
        "row_count": 500,
        "trained_by": "test-user",
        "approved_by": "test-user",
        "approved_at": datetime.now(timezone.utc),
        "validation_report": "Reviewed the model metrics and grouped validation results.",
    }
    database["ml_model_versions"].documents.append(record)
    return record


def test_one_admin_can_train_review_approve_activate_and_rollback(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    client, database = entity_client
    monkeypatch.setattr(ml_api, "_MODEL_DIR", temporary_model_dir)
    previous = _store_test_model(
        database, temporary_model_dir, "visa-previous", "ACTIVE"
    )
    database["ml_active_models"].documents.append(
        {"_id": "visa", "model_version": previous["model_version"]}
    )

    rows = ["application_id,visa_outcome,travel_history_count"]
    for index in range(500):
        outcome = "APPROVED" if index % 2 == 0 else "REJECTED"
        rows.append(f"application-{index},{outcome},{index % 5}")
    training = client.post(
        "/api/ml/train",
        data={
            "model_type": "visa",
            "algorithm": "random_forest",
            "verified_outcomes": "true",
            "privacy_reviewed": "true",
            "source_description": "Authorized historical visa outcomes",
            "label_mapping": '{"APPROVED":"APPROVED","REJECTED":"REJECTED"}',
        },
        files={"file": ("historical.csv", "\n".join(rows).encode(), "text/csv")},
        headers=_headers("ADMIN"),
    )

    assert training.status_code == 201
    candidate = training.json()["model"]
    version = candidate["model_version"]
    assert candidate["model_status"] == "PENDING_REVIEW"
    assert candidate["trained_by"] == "test-user"
    assert candidate["artifact_sha256"]

    review = client.post(
        f"/api/ml/models/{version}/review",
        json={
            "validation_report": "Reviewed grouped metrics, class balance, and the authorized data source.",
            "review_comment": "Checked the validation results and documented limitations.",
        },
        headers=_headers("ADMIN"),
    )
    assert review.status_code == 200
    assert review.json()["model_status"] == "UNDER_REVIEW"
    assert review.json()["review_type"] == "Admin Self-Review"

    approval = client.post(
        f"/api/ml/models/{version}/approve",
        json={"approval_comment": "Approved after reviewing the holdout results."},
        headers=_headers("ADMIN"),
    )
    assert approval.status_code == 200
    assert approval.json()["model_status"] == "APPROVED"

    unconfirmed = client.post(
        f"/api/ml/models/{version}/activate",
        json={"activation_comment": "Activate the reviewed candidate."},
        headers=_headers("ADMIN"),
    )
    assert unconfirmed.status_code == 400
    assert database["ml_active_models"].documents[0]["model_version"] == "visa-previous"

    activation = client.post(
        f"/api/ml/models/{version}/activate",
        json={
            "activation_confirmation": True,
            "activation_comment": "Activate the reviewed candidate.",
        },
        headers=_headers("ADMIN"),
    )
    assert activation.status_code == 200
    assert activation.json()["model_status"] == "ACTIVE"
    assert database["ml_active_models"].documents[0]["model_version"] == version
    assert previous["model_status"] == "ROLLED_BACK"
    previous_after_activation = next(
        item
        for item in database["ml_model_versions"].documents
        if item["model_version"] == "visa-previous"
    )
    assert previous_after_activation["model_status"] == "ROLLED_BACK"

    rollback = client.post(
        "/api/ml/models/visa-previous/rollback",
        json={
            "rollback_confirmation": True,
            "rollback_comment": "Restore the prior approved production model.",
        },
        headers=_headers("ADMIN"),
    )
    assert rollback.status_code == 200
    assert rollback.json()["model_version"] == "visa-previous"
    assert database["ml_active_models"].documents[0]["model_version"] == "visa-previous"

    actions = [item["action"] for item in database["ml_business_audit"].documents]
    assert "model_review_started" in actions
    assert "model_approved" in actions
    assert "model_activated" in actions
    assert "model_rolled_back" in actions
    review_audit = next(
        item
        for item in database["ml_business_audit"].documents
        if item["action"] == "model_review_started"
    )
    assert review_audit["trained_by"] == review_audit["reviewed_by"] == "test-user"


def test_admin_model_actions_are_denied_to_staff(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = entity_client
    headers = _headers("STAFF")
    actions = (
        ("review", {"validation_report": "A complete report with enough detail.", "review_comment": "Reviewed all reported checks."}),
        ("approve", {"approval_comment": "Approved after reviewing the report."}),
        ("reject", {"rejection_comment": "Rejected after reviewing the report."}),
        ("activate", {"activation_confirmation": True, "activation_comment": "Activate the approved candidate."}),
        ("rollback", {"rollback_confirmation": True, "rollback_comment": "Restore the previous approved model."}),
    )

    for action, payload in actions:
        response = client.post(
            f"/api/ml/models/visa-not-found/{action}",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 403


def test_failed_model_validation_or_checksum_does_not_replace_active_model(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    client, database = entity_client
    monkeypatch.setattr(ml_api, "_MODEL_DIR", temporary_model_dir)
    candidate = _store_test_model(
        database, temporary_model_dir, "visa-candidate", "APPROVED"
    )
    candidate.update(
        {
            "validation_passed": False,
            "reviewed_by": "test-user",
            "approved_by": "test-user",
        }
    )
    database["ml_active_models"].documents.append(
        {"_id": "visa", "model_version": "visa-current"}
    )
    payload = {
        "activation_confirmation": True,
        "activation_comment": "Activate the reviewed candidate.",
    }

    invalid_validation = client.post(
        "/api/ml/models/visa-candidate/activate",
        json=payload,
        headers=_headers("ADMIN"),
    )
    assert invalid_validation.status_code == 409
    assert database["ml_active_models"].documents[0]["model_version"] == "visa-current"

    candidate["validation_passed"] = True
    candidate["artifact_sha256"] = "0" * 64
    invalid_checksum = client.post(
        "/api/ml/models/visa-candidate/activate",
        json=payload,
        headers=_headers("ADMIN"),
    )
    assert invalid_checksum.status_code == 409
    assert database["ml_active_models"].documents[0]["model_version"] == "visa-current"

    artifact_path = Path(candidate["artifact_path"])
    joblib.dump(
        {
            "model_type": "visa",
            "model_version": "visa-another-version",
            "pipeline": {"loaded": True},
        },
        artifact_path,
    )
    candidate["artifact_sha256"] = ml_api._sha256_file(artifact_path)
    version_mismatch = client.post(
        "/api/ml/models/visa-candidate/activate",
        json=payload,
        headers=_headers("ADMIN"),
    )
    assert version_mismatch.status_code == 409
    assert database["ml_active_models"].documents[0]["model_version"] == "visa-current"

    artifact_path.write_bytes(b"not a serialized model")
    candidate["artifact_sha256"] = ml_api._sha256_file(artifact_path)
    unloadable_artifact = client.post(
        "/api/ml/models/visa-candidate/activate",
        json=payload,
        headers=_headers("ADMIN"),
    )
    assert unloadable_artifact.status_code == 409
    assert database["ml_active_models"].documents[0]["model_version"] == "visa-current"


def test_rejected_model_cannot_be_activated(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    version = "visa-rejected"
    database["ml_model_versions"].documents.append(
        {
            "_id": version,
            "model_version": version,
            "model_type": "visa",
            "model_status": "PENDING_REVIEW",
            "validation_passed": True,
            "trained_by": "test-user",
            "metrics": {"accuracy": 0.8},
            "row_count": 500,
        }
    )
    rejection = client.post(
        f"/api/ml/models/{version}/reject",
        json={"rejection_comment": "Rejected because review found a material issue."},
        headers=_headers("ADMIN"),
    )
    assert rejection.status_code == 200
    assert rejection.json()["model_status"] == "REJECTED"

    activation = client.post(
        f"/api/ml/models/{version}/activate",
        json={
            "activation_confirmation": True,
            "activation_comment": "Activate the reviewed candidate.",
        },
        headers=_headers("ADMIN"),
    )
    assert activation.status_code == 409
    assert database["ml_model_versions"].documents[0]["model_status"] == "REJECTED"


def test_failed_active_pointer_write_preserves_current_model(
    entity_client: tuple[TestClient, FakeDatabase],
    monkeypatch,
    temporary_model_dir: Path,
) -> None:
    client, database = entity_client
    monkeypatch.setattr(ml_api, "_MODEL_DIR", temporary_model_dir)
    current = _store_test_model(
        database, temporary_model_dir, "visa-current", "ACTIVE"
    )
    candidate = _store_test_model(
        database, temporary_model_dir, "visa-approved", "APPROVED"
    )
    candidate.update(
        {
            "reviewed_by": "test-user",
            "approved_by": "test-user",
        }
    )

    class FailingActiveCollection(FakeCollection):
        async def update_one(
            self, query: dict[str, Any], update: dict[str, Any]
        ) -> SimpleNamespace:
            raise PyMongoError("simulated active-pointer write failure")

    active_collection = FailingActiveCollection("ml_active_models")
    active_collection.documents.append(
        {"_id": "visa", "model_version": current["model_version"]}
    )
    database.collections["ml_active_models"] = active_collection

    response = client.post(
        "/api/ml/models/visa-approved/activate",
        json={
            "activation_confirmation": True,
            "activation_comment": "Activate the reviewed candidate.",
        },
        headers=_headers("ADMIN"),
    )

    assert response.status_code == 503
    assert active_collection.documents[0]["model_version"] == "visa-current"
    assert candidate["model_status"] == "APPROVED"
    assert current["model_status"] == "ACTIVE"


def test_customer_verification_and_history_are_limited_to_owned_applications(
    entity_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = entity_client
    database["visaapplications"].documents.extend(
        [
            {"_id": "owned-application", "customer": "test-user"},
            {"_id": "other-application", "customer": "another-customer"},
        ]
    )
    database["verifications"].documents.extend(
        [
            {"_id": "owned-verification", "applicationId": "owned-application"},
            {"_id": "other-verification", "applicationId": "other-application"},
        ]
    )

    denied = client.post(
        "/api/verification/run",
        json={"applicationId": "other-application"},
        headers=_headers("CUSTOMER"),
    )
    history = client.get(
        "/api/verification",
        headers=_headers("CUSTOMER"),
    )

    assert denied.status_code == 404
    assert len(database["verifications"].documents) == 2
    assert history.status_code == 200
    assert [
        item["_id"] for item in history.json()["verifications"]
    ] == ["owned-verification"]
