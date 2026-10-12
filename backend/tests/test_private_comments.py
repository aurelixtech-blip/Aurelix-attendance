from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_employee
from app.core.security import current_claims
from app.main import app


class MemoryCursor(list):
    def sort(self, field, direction):
        super().sort(key=lambda item: item[field], reverse=direction < 0)
        return self


class MemoryComments:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        self.documents.append(deepcopy(document))

    def find(self, _query, _projection):
        return MemoryCursor(deepcopy(self.documents))


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def comment_client(monkeypatch):
    comments = MemoryComments()
    employees = {
        "EMP-1": {"employee_id": "EMP-1", "full_name": "Employee One", "role": "employee"},
        "ADM-1": {"employee_id": "ADM-1", "full_name": "Admin One", "role": "admin"},
    }
    database = SimpleNamespace(
        employee_comments=comments,
        employees=SimpleNamespace(find_one=lambda query: employees.get(query["employee_id"])),
    )
    monkeypatch.setattr("app.api.private_comments.get_db", lambda: database)
    monkeypatch.setattr("app.api.deps.get_db", lambda: database)
    return TestClient(app), comments


def test_employee_can_submit_without_attendance_and_server_owns_identity_and_time(comment_client):
    client, comments = comment_client
    app.dependency_overrides[current_claims] = lambda: {"sub": "EMP-1", "role": "employee"}

    response = client.post("/api/comments", json={
        "comment": "Please update my department.",
        "employee_id": "EMP-OTHER",
        "submitted_at": "2000-01-01T00:00:00Z",
    })

    assert response.status_code == 201
    assert response.json() == {"message": "Comment submitted successfully"}
    assert len(comments.documents) == 1
    saved = comments.documents[0]
    assert saved["employee_id"] == "EMP-1"
    assert saved["employee_name"] == "Employee One"
    assert saved["comment"] == "Please update my department."
    assert isinstance(saved["submitted_at"], datetime)
    assert saved["submitted_at"].tzinfo == timezone.utc
    assert "comment" not in response.json()


def test_submission_rejects_admin_role(comment_client):
    client, comments = comment_client
    app.dependency_overrides[current_employee] = lambda: {
        "employee_id": "ADM-1",
        "full_name": "Admin One",
        "role": "admin",
    }

    response = client.post("/api/comments", json={"comment": "Not an employee submission"})

    assert response.status_code == 403
    assert comments.documents == []


def test_submission_accepts_seven_lines_and_rejects_eight_lines(comment_client):
    client, comments = comment_client
    app.dependency_overrides[current_employee] = lambda: {
        "employee_id": "EMP-1",
        "full_name": "Employee One",
        "role": "employee",
    }

    accepted = client.post("/api/comments", json={"comment": "\n".join(["line"] * 7)})
    rejected = client.post("/api/comments", json={"comment": "\n".join(["line"] * 8)})

    assert accepted.status_code == 201
    assert rejected.status_code == 422
    assert len(comments.documents) == 1


def test_submission_accepts_exactly_500_characters(comment_client):
    client, comments = comment_client
    app.dependency_overrides[current_employee] = lambda: {
        "employee_id": "EMP-1",
        "full_name": "Employee One",
        "role": "employee",
    }

    response = client.post("/api/comments", json={"comment": "x" * 500})

    assert response.status_code == 201
    assert len(comments.documents[0]["comment"]) == 500


@pytest.mark.parametrize("comment", ["", "   ", "x" * 501])
def test_submission_rejects_blank_or_overlong_comments(comment_client, comment):
    client, comments = comment_client
    app.dependency_overrides[current_employee] = lambda: {
        "employee_id": "EMP-1",
        "full_name": "Employee One",
        "role": "employee",
    }

    response = client.post("/api/comments", json={"comment": comment})

    assert response.status_code == 422
    assert comments.documents == []


def test_each_employee_submission_is_persisted_separately(comment_client):
    client, comments = comment_client
    app.dependency_overrides[current_employee] = lambda: {
        "employee_id": "EMP-1",
        "full_name": "Employee One",
        "role": "employee",
    }

    first = client.post("/api/comments", json={"comment": "First note"})
    second = client.post("/api/comments", json={"comment": "Second note"})

    assert first.status_code == second.status_code == 201
    assert [item["comment"] for item in comments.documents] == ["First note", "Second note"]


def test_only_admin_can_retrieve_private_comments(comment_client):
    client, comments = comment_client
    comments.documents.append({
        "employee_id": "EMP-1",
        "employee_name": "Employee One",
        "comment": "Private note",
        "submitted_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    })
    app.dependency_overrides[current_claims] = lambda: {"sub": "EMP-1", "role": "employee"}

    employee_response = client.get("/api/comments")

    assert employee_response.status_code == 403

    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-1", "role": "admin"}
    admin_response = client.get("/api/comments")

    assert admin_response.status_code == 200
    assert admin_response.json()[0]["comment"] == "Private note"
    assert admin_response.json()[0]["employee_id"] == "EMP-1"


def test_unauthenticated_user_cannot_retrieve_private_comments(comment_client):
    client, _comments = comment_client

    response = client.get("/api/comments")

    assert response.status_code == 401
