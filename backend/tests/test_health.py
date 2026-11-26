from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from backend.app import main


class FakeAdmin:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.ping_count = 0

    async def command(self, command: str) -> dict[str, int]:
        assert command == "ping"
        self.ping_count += 1
        if self.error:
            raise self.error
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.admin = FakeAdmin(error)
        self.closed = False

    def get_database(self, _database_name: str) -> object:
        return object()

    async def close(self) -> None:
        self.closed = True


def make_test_client(monkeypatch, mongo_client: FakeMongoClient) -> TestClient:
    monkeypatch.setattr(main, "create_mongo_client", lambda: mongo_client)
    return TestClient(main.app)


def test_health_does_not_depend_on_mongodb(monkeypatch) -> None:
    mongo_client = FakeMongoClient()
    with make_test_client(monkeypatch, mongo_client) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "timestamp" in response.json()
    assert mongo_client.admin.ping_count == 1
    assert mongo_client.closed


def test_readiness_checks_mongodb(monkeypatch) -> None:
    mongo_client = FakeMongoClient()
    with make_test_client(monkeypatch, mongo_client) as client:
        response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"success": True, "status": "ready"}
    assert mongo_client.admin.ping_count == 2


def test_readiness_reports_mongodb_failure(monkeypatch) -> None:
    mongo_client = FakeMongoClient(ServerSelectionTimeoutError("unavailable"))
    with make_test_client(monkeypatch, mongo_client) as client:
        response = client.get("/api/ready")
        health_response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["message"] == "Service unavailable"
    assert health_response.status_code == 200
    assert health_response.json()["success"] is False
    assert health_response.json()["status"] == "degraded"


def test_cors_preflight_allows_frontend_origin(monkeypatch) -> None:
    mongo_client = FakeMongoClient()
    with make_test_client(monkeypatch, mongo_client) as client:
        response = client.options(
            "/api/dashboard",
            headers={
                "Origin": "http://localhost:5174",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"


def test_bank_statement_analysis_routes_are_registered() -> None:
    paths = {
        path
        for path in main.app.openapi()["paths"]
        if path.startswith("/api/bank-statements")
    }

    assert paths == {
        "/api/bank-statements/analyze",
        "/api/bank-statements/{statement_id}",
        "/api/bank-statements/{statement_id}/result",
        "/api/bank-statements/{statement_id}/score",
        "/api/bank-statements/{statement_id}/recalculate",
        "/api/bank-statements/{statement_id}/transactions",
        "/api/bank-statements/documents/{document_id}/aba",
        "/api/bank-statements/documents/{document_id}/aba/page/{page}",
        "/api/bank-statements/documents/{document_id}/aba/corrections",
        "/api/bank-statements/documents/{document_id}/aba/review",
        "/api/bank-statements/documents/{document_id}/transactions/{transaction_index}/source-review",
        "/api/bank-statements/documents/{document_id}/aba/select-primary",
        "/api/bank-statements/documents/{document_id}/aba/verify",
    }


def test_ml_and_document_ocr_routes_are_registered() -> None:
    paths = set(main.app.openapi()["paths"])

    assert {
        "/api/documents/ocr",
        "/api/rules",
        "/api/rules/evaluate",
        "/api/ml/train",
        "/api/ml/predict",
        "/api/ml/model-status",
        "/api/ml/metrics",
        "/api/ml/models/{model_version}/activate",
        "/api/predictions/{prediction_id}",
    } <= paths
