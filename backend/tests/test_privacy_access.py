"""Case access checks use synthetic records and never connect to MongoDB."""
from copy import deepcopy

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.api import visa_types
from backend.app.api.dependencies import get_current_principal, get_mongo_database
from backend.app.core.errors import APIError


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))

    @staticmethod
    def matches(row, query):
        return all(
            row.get(key) in value["$in"] if isinstance(value, dict) and "$in" in value
            else row.get(key) == value
            for key, value in query.items()
        )

    async def find_one(self, query):
        return next((deepcopy(row) for row in self.rows if self.matches(row, query)), None)

    def find(self, query):
        matches = [deepcopy(row) for row in self.rows if self.matches(row, query)]

        class Cursor:
            async def to_list(self, length=None):
                return matches

        return Cursor()

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))

    async def replace_one(self, query, replacement):
        for index, row in enumerate(self.rows):
            if self.matches(row, query):
                self.rows[index] = deepcopy(replacement)
                return


@pytest.fixture
def case_client():
    database = {
        "visaapplications": Collection([
            {"_id": "case-own", "customer": "customer-a", "visaTypeId": "visa-1", "status": "DRAFT"},
            {"_id": "case-other", "customer": "customer-b", "visaTypeId": "visa-1", "status": "DRAFT"},
            {"_id": "case-legacy", "customerId": "customer-a", "visaTypeId": "visa-1", "status": "DRAFT"},
            {"_id": "case-deleted", "customer": "customer-a", "visaTypeId": "visa-1", "deletedAt": "2026-01-01"},
        ]),
        "customers": Collection([
            {"_id": "customer-a", "fullName": "Applicant A"},
            {"_id": "customer-b", "fullName": "Applicant B"},
        ]),
        "visatypes": Collection([{"_id": "visa-1", "name": "Synthetic visa", "active": True}]),
        "visarequirements": Collection([{"_id": "requirement-1", "visaTypeId": "visa-1", "status": "ACTIVE", "weight": 100}]),
    }
    principal = {"id": "customer-a", "email": "a@example.test", "role": "CUSTOMER"}
    app = FastAPI()
    app.include_router(visa_types.applications_router, prefix="/api/applications", dependencies=[Depends(get_current_principal)])
    app.include_router(visa_types.router, prefix="/api/visa-types", dependencies=[Depends(get_current_principal)])
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_mongo_database] = lambda: database

    @app.exception_handler(APIError)
    async def handle_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"message": error.message})

    with TestClient(app) as client:
        yield client, database, principal


@pytest.mark.parametrize("application_id", ["case-own", "case-legacy"])
def test_customer_can_read_own_case(case_client, application_id):
    client, _, _ = case_client
    response = client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200
    assert response.json()["application"]["applicant"] == "Applicant A"


def test_customer_cannot_distinguish_other_case_from_missing_case(case_client):
    client, _, _ = case_client
    other = client.get("/api/applications/case-other")
    missing = client.get("/api/applications/case-missing")
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json() == {"message": "Application not found"}


@pytest.mark.parametrize("role", ["ADMIN", "STAFF", "CUSTOMER"])
def test_deleted_case_is_not_readable(case_client, role):
    client, _, principal = case_client
    principal["role"] = role
    assert client.get("/api/applications/case-deleted").status_code == 404


@pytest.mark.parametrize("role", ["ADMIN", "STAFF"])
def test_internal_roles_can_read_other_cases(case_client, role):
    client, _, principal = case_client
    principal["role"] = role
    response = client.get("/api/applications/case-other")
    assert response.status_code == 200
    assert response.json()["application"]["applicant"] == "Applicant B"


@pytest.mark.parametrize("path", ["/api/applications", "/api/applications/", "/api/visa-types/applications"])
def test_case_list_aliases_keep_customer_scope(case_client, path):
    client, _, _ = case_client
    response = client.get(path)
    assert response.status_code == 200
    assert {row["_id"] for row in response.json()["applications"]} == {"case-own", "case-legacy"}
    assert client.get(path, params={"customerId": "customer-b"}).json()["applications"] == []


@pytest.mark.parametrize("path", ["/api/applications", "/api/applications/", "/api/visa-types/applications"])
def test_customer_cannot_create_case_through_any_alias(case_client, path):
    client, database, _ = case_client
    before = deepcopy(database["visaapplications"].rows)
    response = client.post(path, json={"customerId": "customer-b", "visaTypeId": "visa-1"})
    assert response.status_code == 403
    assert database["visaapplications"].rows == before


@pytest.mark.parametrize("application_id", ["case-own", "case-other"])
def test_customer_cannot_change_case_status_or_dates(case_client, application_id):
    client, database, _ = case_client
    before = deepcopy(database["visaapplications"].rows)
    response = client.put(f"/api/applications/{application_id}", json={"status": "FINAL_PASS", "travelStartDate": "2026-12-01"})
    assert response.status_code == 403
    assert database["visaapplications"].rows == before


@pytest.mark.parametrize("role", ["ADMIN", "STAFF"])
@pytest.mark.parametrize("path", ["/api/applications", "/api/visa-types/applications"])
def test_internal_roles_can_create_and_update_cases(case_client, role, path):
    client, _, principal = case_client
    principal["role"] = role
    created = client.post(path, json={"customerId": "customer-b", "visaTypeId": "visa-1"})
    assert created.status_code == 201
    application_id = created.json()["application"]["_id"]
    updated = client.put(f"/api/applications/{application_id}", json={"status": "UNDER_VERIFICATION"})
    assert updated.status_code == 200
    assert updated.json()["application"]["status"] == "UNDER_VERIFICATION"


@pytest.mark.parametrize("role", ["ADMIN", "STAFF"])
def test_deleted_case_cannot_be_updated(case_client, role):
    client, database, principal = case_client
    principal["role"] = role
    before = deepcopy(database["visaapplications"].rows)
    assert client.put("/api/applications/case-deleted", json={"status": "DRAFT"}).status_code == 404
    assert database["visaapplications"].rows == before
