from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import attendance_notes as notes_api
from app.api.deps import current_employee
from app.core.security import current_claims
from app.main import app


DATE = "2026-10-20"
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])

    @staticmethod
    def matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$gt" in expected and (actual is None or actual <= expected["$gt"]):
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, _projection=None):
        return next((item for item in self.documents if self.matches(item, query)), None)

    def find(self, query, _projection=None):
        return MemoryCursor([item for item in self.documents if self.matches(item, query)])

    def insert_one(self, document):
        self.documents.append(deepcopy(document))

    def update_one(self, query, update):
        document = self.find_one(query)
        if document is None:
            return SimpleNamespace(matched_count=0)
        document.update(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=1)

    def replace_one(self, query, replacement):
        document = self.find_one(query)
        if document is None:
            return SimpleNamespace(matched_count=0)
        self.documents[self.documents.index(document)] = deepcopy(replacement)
        return SimpleNamespace(matched_count=1)

    def delete_one(self, query):
        document = self.find_one(query)
        if document is None:
            return SimpleNamespace(deleted_count=0)
        self.documents.remove(document)
        return SimpleNamespace(deleted_count=1)


class MemoryCursor(list):
    def sort(self, key, direction):
        return MemoryCursor(sorted(self, key=lambda item: item.get(key), reverse=direction < 0))


@pytest.fixture
def api_client(monkeypatch):
    db = SimpleNamespace(
        attendance=MemoryCollection([
            {"attendance_id": "att-1", "employee_id": "EMP-1", "date": DATE, "user_name": "Employee One"},
            {"attendance_id": "att-2", "employee_id": "EMP-2", "date": DATE, "user_name": "Employee Two"},
            {"attendance_id": "att-3", "employee_id": "EMP-1", "date": "2026-10-19", "user_name": "Employee One"},
        ]),
        attendance_notes=MemoryCollection(),
        employees=MemoryCollection([
            {"employee_id": "EMP-1", "full_name": "Employee One"},
            {"employee_id": "EMP-2", "full_name": "Employee Two"},
        ]),
    )
    monkeypatch.setattr(notes_api, "get_db", lambda: db)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(notes_api, "datetime", FrozenDateTime)
    app.dependency_overrides[current_employee] = lambda: {"employee_id": "EMP-1", "role": "employee"}
    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-1", "role": "admin"}
    yield TestClient(app), db
    app.dependency_overrides.clear()


def test_employee_creates_reads_edits_and_deletes_own_note(api_client):
    client, db = api_client
    created = client.post(f"/api/attendance/notes/{DATE}", json={"content": "Doctor appointment"})
    assert created.status_code == 200
    assert created.json()["content"] == "Doctor appointment"
    assert created.json()["expires_at"] == "2026-10-21T12:00:00+00:00"
    assert client.get(f"/api/attendance/notes/{DATE}").json()["content"] == "Doctor appointment"

    assert client.post(f"/api/attendance/notes/{DATE}", json={"content": "Duplicate"}).status_code == 409
    updated = client.patch(f"/api/attendance/notes/{DATE}", json={"content": "Updated note"})
    assert updated.status_code == 200
    assert updated.json()["content"] == "Updated note"
    assert updated.json()["created_at"] == created.json()["created_at"]
    assert updated.json()["expires_at"] == created.json()["expires_at"]
    assert len(db.attendance_notes.documents) == 1

    deleted = client.delete(f"/api/attendance/notes/{DATE}")
    assert deleted.status_code == 204
    assert client.get(f"/api/attendance/notes/{DATE}").status_code == 404


def test_employee_cannot_read_edit_or_delete_another_employees_note(api_client):
    client, db = api_client
    client.post("/api/attendance/notes/2026-10-19", json={"content": "Private"})
    app.dependency_overrides[current_employee] = lambda: {"employee_id": "EMP-2", "role": "employee"}

    assert client.get("/api/attendance/notes/2026-10-19").status_code == 404
    assert client.patch("/api/attendance/notes/2026-10-19", json={"content": "Changed"}).status_code == 404
    assert client.post("/api/attendance/notes/2026-10-19", json={"content": "Wrong owner"}).status_code == 404
    assert client.delete("/api/attendance/notes/2026-10-19").status_code == 404

    assert next(item for item in db.attendance_notes.documents if item["employee_id"] == "EMP-1")["content"] == "Private"


def test_note_identity_comes_from_authenticated_employee_not_request_body(api_client):
    client, db = api_client
    response = client.post(f"/api/attendance/notes/{DATE}", json={"content": "Mine", "employee_id": "EMP-2"})
    assert response.status_code == 200
    assert db.attendance_notes.documents[0]["employee_id"] == "EMP-1"


@pytest.mark.parametrize("content", ["", "  ", "x" * 1001])
def test_empty_or_too_long_notes_are_rejected(api_client, content):
    client, _db = api_client
    assert client.post(f"/api/attendance/notes/{DATE}", json={"content": content}).status_code == 422


def test_employee_cannot_create_note_without_their_attendance_record(api_client):
    client, _db = api_client
    app.dependency_overrides[current_employee] = lambda: {"employee_id": "EMP-OTHER", "role": "employee"}
    assert client.post(f"/api/attendance/notes/{DATE}", json={"content": "No record"}).status_code == 404


def test_expired_notes_are_hidden_and_cannot_be_edited(api_client, monkeypatch):
    client, db = api_client
    db.attendance_notes.documents.append({
        "employee_id": "EMP-1",
        "date": DATE,
        "content": "Expired",
        "created_at": NOW - timedelta(hours=24),
        "expires_at": NOW,
        "updated_at": NOW - timedelta(hours=24),
    })
    assert client.get(f"/api/attendance/notes/{DATE}").status_code == 404
    assert client.get("/api/attendance/notes").json() == []
    assert client.get("/api/attendance/admin/att-1/note").status_code == 404
    assert client.patch(f"/api/attendance/notes/{DATE}", json={"content": "Edit expired"}).status_code == 410

    class LaterDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=1)

    monkeypatch.setattr(notes_api, "datetime", LaterDateTime)
    replacement = client.post(f"/api/attendance/notes/{DATE}", json={"content": "New note"})
    assert replacement.status_code == 200
    assert replacement.json()["created_at"] == "2026-10-20T12:00:01+00:00"
    assert len(db.attendance_notes.documents) == 1


def test_admin_note_is_derived_from_matching_attendance_employee_and_date(api_client):
    client, db = api_client
    client.post(f"/api/attendance/notes/{DATE}", json={"content": "For employee one"})
    assert client.get("/api/attendance/admin/att-1/note").json()["content"] == "For employee one"
    assert client.get("/api/attendance/admin/att-1/note").json()["employee_name"] == "Employee One"
    assert client.get("/api/attendance/admin/att-2/note").status_code == 404
    assert client.get("/api/attendance/admin/att-3/note").status_code == 404


def test_employee_role_cannot_use_admin_note_api_or_employee_write_as_admin(api_client):
    client, _db = api_client
    app.dependency_overrides[current_employee] = lambda: {"employee_id": "ADM-1", "role": "admin"}
    assert client.post(f"/api/attendance/notes/{DATE}", json={"content": "Not an employee"}).status_code == 403
    assert client.patch(f"/api/attendance/notes/{DATE}", json={"content": "Not an employee"}).status_code == 403


def test_note_endpoints_require_authentication():
    app.dependency_overrides.clear()
    client = TestClient(app)
    try:
        assert client.get("/api/attendance/notes").status_code == 401
        assert client.get("/api/attendance/admin/att-1/note").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_mongodb_initializes_unique_note_and_expiry_indexes(monkeypatch):
    from app.db import mongodb

    indexes = {}

    class Collection:
        def __init__(self, name):
            self.name = name

        def create_index(self, keys, **options):
            indexes[(self.name, str(keys))] = options

    class FakeDb:
        def __getattr__(self, name):
            if name == "fs":
                return SimpleNamespace(files=Collection("fs.files"))
            return Collection(name)

    monkeypatch.setattr(mongodb, "get_db", FakeDb)
    mongodb.init_indexes()
    assert indexes[("attendance_notes", "[('employee_id', 1), ('date', 1)]")]["unique"] is True
    assert indexes[("attendance_notes", "expires_at")]["expireAfterSeconds"] == 0
