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
    assert "expires_at" not in created.json()
    assert client.get(f"/api/attendance/notes/{DATE}").json()["content"] == "Doctor appointment"

    assert client.post(f"/api/attendance/notes/{DATE}", json={"content": "Duplicate"}).status_code == 409
    updated = client.patch(f"/api/attendance/notes/{DATE}", json={"content": "Updated note"})
    assert updated.status_code == 200
    assert updated.json()["content"] == "Updated note"
    assert updated.json()["created_at"] == created.json()["created_at"]
    assert "expires_at" not in updated.json()
    assert db.attendance_notes.documents[0]["employee_id"] == "EMP-1"
    assert db.attendance_notes.documents[0]["date"] == DATE
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


def test_legacy_note_with_expired_timestamp_remains_readable_editable_and_deletable(api_client):
    client, db = api_client
    legacy_note = {
        "employee_id": "EMP-1",
        "date": DATE,
        "content": "Legacy note",
        "created_at": NOW - timedelta(days=3650),
        "expires_at": NOW - timedelta(days=3649),
        "updated_at": NOW - timedelta(days=3650),
    }
    db.attendance_notes.documents.append(legacy_note.copy())

    response = client.get(f"/api/attendance/notes/{DATE}")
    assert response.status_code == 200
    assert response.json()["content"] == "Legacy note"
    assert client.get("/api/attendance/notes").json()[0]["content"] == "Legacy note"
    admin_note = client.get("/api/attendance/admin/att-1/note")
    assert admin_note.status_code == 200
    assert admin_note.json()["content"] == "Legacy note"
    assert "expires_at" not in response.json()

    updated = client.patch(f"/api/attendance/notes/{DATE}", json={"content": "Edited legacy note"})
    assert updated.status_code == 200
    stored = db.attendance_notes.documents[0]
    assert stored["content"] == "Edited legacy note"
    assert stored["employee_id"] == "EMP-1"
    assert stored["date"] == DATE
    assert stored["created_at"] == legacy_note["created_at"]
    assert stored["expires_at"] == legacy_note["expires_at"]

    assert client.delete(f"/api/attendance/notes/{DATE}").status_code == 204
    assert db.attendance_notes.documents == []


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


def test_mongodb_removes_only_the_attendance_note_expiry_index_and_preserves_data(monkeypatch):
    from app.db import mongodb

    note_indexes = [
        {"name": "_id_", "key": {"_id": 1}},
        {"name": "employee_date_unique", "key": {"employee_id": 1, "date": 1}, "unique": True},
        {"name": "expires_at_1", "key": {"expires_at": 1}, "expireAfterSeconds": 0},
        {"name": "archive_at_ttl", "key": {"archive_at": 1}, "expireAfterSeconds": 0},
    ]
    dropped_indexes = []
    created_indexes = []
    notes = [{"employee_id": "EMP-1", "date": DATE, "content": "Keep this note"}]
    attendance = [{"employee_id": "EMP-1", "date": DATE, "check_in_time": NOW}]

    class Collection:
        def __init__(self, name, indexes=None, documents=None):
            self.name = name
            self.indexes = indexes if indexes is not None else []
            self.documents = documents if documents is not None else []

        def create_index(self, keys, **options):
            created_indexes.append((self.name, keys, options))

        def list_indexes(self):
            return list(self.indexes)

        def drop_index(self, name):
            dropped_indexes.append((self.name, name))
            self.indexes[:] = [index for index in self.indexes if index["name"] != name]

    class FakeDb:
        def __init__(self):
            self.collections = {
                "attendance_notes": Collection("attendance_notes", note_indexes, notes),
                "attendance": Collection("attendance", documents=attendance),
            }

        def list_collection_names(self, filter=None):
            return ["attendance_notes"] if filter else list(self.collections)

        def collection(self, name):
            if name not in self.collections:
                self.collections[name] = Collection(name)
            return self.collections[name]

        def __getattr__(self, name):
            if name == "fs":
                return SimpleNamespace(files=self.collection("fs.files"))
            return self.collection(name)

    db = FakeDb()

    monkeypatch.setattr(mongodb, "get_db", lambda: db)
    mongodb.init_indexes()
    mongodb.init_indexes()

    assert dropped_indexes == [("attendance_notes", "expires_at_1")]
    assert [index["name"] for index in db.attendance_notes.indexes] == [
        "_id_",
        "employee_date_unique",
        "archive_at_ttl",
    ]
    assert ("attendance_notes", [("employee_id", 1), ("date", 1)], {"unique": True}) in created_indexes
    assert not any(
        collection == "attendance_notes" and options.get("expireAfterSeconds") is not None
        for collection, _keys, options in created_indexes
    )
    assert notes == [{"employee_id": "EMP-1", "date": DATE, "content": "Keep this note"}]
    assert attendance == [{"employee_id": "EMP-1", "date": DATE, "check_in_time": NOW}]


def test_database_startup_aborts_if_note_ttl_index_cleanup_fails(monkeypatch):
    from app import main
    from app.db.mongodb import AttendanceNoteTtlIndexMigrationError

    monkeypatch.setattr(
        main,
        "init_indexes",
        lambda: (_ for _ in ()).throw(AttendanceNoteTtlIndexMigrationError("cleanup failed")),
    )
    with pytest.raises(AttendanceNoteTtlIndexMigrationError):
        main.initialize_database()
