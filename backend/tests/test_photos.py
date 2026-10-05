import pytest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient
from PIL import Image

from app.api import attendance as attendance_api
from app.api.deps import current_employee
from app.core.security import current_claims
from app.main import app
from app.models.attendance import attendance_document, public_attendance
from app.services import photo_service
from app.services.photo_service import PENDING_ATTENDANCE_ID, MAX_PHOTO_EDGE


EMPLOYEE = {"employee_id": "EMP-1", "full_name": "Test Employee", "role": "employee", "is_active": True}


def jpeg_bytes(size=(24, 24)):
    buffer = BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format="JPEG")
    buffer.seek(0)
    return buffer.getvalue()


def png_upload(size=(24, 24)):
    buffer = BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class MemoryAttendance:
    def __init__(self, record):
        self.record = record
        self.deleted = False
        self.updated = None

    def find_one(self, query):
        if self.deleted:
            return None
        if query.get("attendance_id") != self.record["attendance_id"]:
            return None
        if query.get("employee_id") and query["employee_id"] != self.record["employee_id"]:
            return None
        return self.record

    def delete_one(self, _query):
        self.deleted = True

    def update_one(self, _query, update):
        for key, condition in _query.items():
            if key == "_id":
                continue
            if isinstance(condition, dict) and "$ne" in condition:
                if self.record.get(key) == condition["$ne"]:
                    return SimpleNamespace(matched_count=0, modified_count=0)
            elif self.record.get(key) != condition:
                return SimpleNamespace(matched_count=0, modified_count=0)
        self.updated = update["$set"]
        self.record.update(self.updated)
        return SimpleNamespace(matched_count=1, modified_count=1)


class MemoryAudit:
    def __init__(self):
        self.events = []

    def insert_one(self, _document):
        self.events.append(_document)


def override_employee():
    app.dependency_overrides[current_employee] = lambda: EMPLOYEE
    app.dependency_overrides[current_claims] = lambda: {"sub": "EMP-1", "role": "employee"}


def override_admin():
    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-1", "role": "admin"}


def clear_overrides():
    app.dependency_overrides.clear()


def test_attendance_document_includes_photo_fields():
    document = attendance_document("EMP-1", "2026-09-24", "Test Employee")
    assert document["check_in_photo_reference"] is None
    assert document["check_out_photo_reference"] is None


def test_old_attendance_records_without_photos_still_work():
    record = public_attendance({
        "attendance_id": "attendance-legacy",
        "employee_id": "EMP-1",
        "date": "2026-01-01",
        "check_in_time": datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        "check_out_time": None,
        "check_in_location": None,
        "check_out_location": None,
        "final_status": "PRESENT",
    })
    assert record["check_in_photo_available"] is False
    assert record["check_out_photo_available"] is False
    assert "check_in_photo_reference" not in record
    assert "file_id" not in record
    assert record["check_in_time"] is not None


def test_public_attendance_does_not_expose_gridfs_ids():
    record = public_attendance({
        "attendance_id": "attendance-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": None,
        "check_out_time": None,
        "check_in_location": None,
        "check_out_location": None,
        "final_status": "PRESENT",
        "check_in_photo_reference": {"file_id": "secret-file-id", "content_type": "image/jpeg"},
        "check_out_photo_reference": {"file_id": "other-file-id", "content_type": "image/jpeg"},
    })
    assert record["check_in_photo_available"] is True
    assert record["check_out_photo_available"] is True
    assert "secret-file-id" not in str(record)
    assert "check_in_photo_reference" not in record


def test_bind_photo_updates_attendance_id_metadata(monkeypatch):
    updates = []

    class FakeFiles:
        def update_one(self, query, update):
            updates.append((query, update))

    monkeypatch.setattr(photo_service, "get_db", lambda: SimpleNamespace(fs=SimpleNamespace(files=FakeFiles())))
    file_id = ObjectId()
    photo_service.bind_photo_to_attendance({"file_id": str(file_id), "content_type": "image/jpeg"}, "attendance-real")
    assert updates[0][0]["_id"] == file_id
    assert updates[0][1]["$set"]["metadata.attendance_id"] == "attendance-real"


def test_store_photo_writes_gridfs_with_metadata(monkeypatch):
    uploads = []

    class FakeBucket:
        def upload_from_stream(self, filename, _stream, metadata=None):
            uploads.append((filename, metadata))
            return ObjectId()

    monkeypatch.setattr(photo_service, "GridFSBucket", lambda _db: FakeBucket())
    monkeypatch.setattr(photo_service, "get_db", lambda: object())
    reference = photo_service.store_photo(b"jpeg-bytes", "image/jpeg", "EMP-1", PENDING_ATTENDANCE_ID, "check_in")
    assert "file_id" in reference
    assert uploads[0][1]["attendance_id"] == PENDING_ATTENDANCE_ID
    assert uploads[0][1]["employee_id"] == "EMP-1"
    assert uploads[0][1]["event"] == "check_in"
    assert "expires_at" not in uploads[0][1]
    assert "expires_at" not in reference


def test_photo_preparation_resizes_oversized_dimensions():
    import asyncio
    from starlette.datastructures import UploadFile

    source = png_upload((2400, 1800))
    data, content_type = asyncio.run(photo_service.prepare_photo(UploadFile(filename="capture.png", file=source, headers={"content-type": "image/png"})))
    with Image.open(BytesIO(data)) as image:
        assert max(image.size) <= MAX_PHOTO_EDGE
        assert content_type == "image/jpeg"


def test_legacy_json_verify_cannot_record_without_photo():
    override_employee()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/verify", json={"action": "check_in"})
        assert response.status_code == 400
        assert "photo" in response.json()["detail"].lower()
    finally:
        clear_overrides()


def test_check_in_with_photo_binds_real_attendance_id(monkeypatch):
    stored = []
    deleted = []
    bound = []

    async def fake_prepare(_upload):
        return b"jpeg", "image/jpeg"

    monkeypatch.setattr(attendance_api, "prepare_photo", fake_prepare)
    monkeypatch.setattr(attendance_api, "store_photo", lambda *args, **kwargs: stored.append(args) or {"file_id": "file-1", "content_type": "image/jpeg"})
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: deleted.append(reference))
    monkeypatch.setattr(attendance_api, "bind_photo_to_attendance", lambda reference, attendance_id: bound.append((reference, attendance_id)))
    monkeypatch.setattr(attendance_api, "verify_and_record", lambda employee, payload, photo_reference=None: {
        "success": True,
        "status": "PRESENT",
        "reason": None,
        "message": "Attendance recorded.",
        "timestamp": datetime.now(timezone.utc),
        "attendance_id": "attendance-real",
    })
    override_employee()
    client = TestClient(app)
    try:
        response = client.post(
            "/api/attendance/verify-with-photo",
            data={"action": "check_in"},
            files={"photo": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert stored[0][3] == PENDING_ATTENDANCE_ID
        assert bound == [({"file_id": "file-1", "content_type": "image/jpeg"}, "attendance-real")]
        assert deleted == []
        assert len(stored) == 1
    finally:
        clear_overrides()


def test_check_out_with_photo_uses_same_required_upload(monkeypatch):
    async def fake_prepare(_upload):
        return b"jpeg", "image/jpeg"

    monkeypatch.setattr(attendance_api, "prepare_photo", fake_prepare)
    monkeypatch.setattr(attendance_api, "store_photo", lambda *args, **kwargs: {"file_id": "file-2", "content_type": "image/jpeg"})
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: None)
    monkeypatch.setattr(attendance_api, "bind_photo_to_attendance", lambda reference, attendance_id: None)
    monkeypatch.setattr(attendance_api, "verify_and_record", lambda employee, payload, photo_reference=None: {
        "success": True,
        "status": "PRESENT",
        "reason": None,
        "message": "Attendance recorded.",
        "timestamp": datetime.now(timezone.utc),
        "attendance_id": "attendance-real",
    } if payload.action == "check_out" else {"success": False})
    override_employee()
    client = TestClient(app)
    try:
        response = client.post(
            "/api/attendance/verify-with-photo",
            data={"action": "check_out"},
            files={"photo": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
    finally:
        clear_overrides()


def test_retake_equivalent_is_one_store_per_confirmed_request(monkeypatch):
    stored = []

    async def fake_prepare(_upload):
        return b"jpeg", "image/jpeg"

    monkeypatch.setattr(attendance_api, "prepare_photo", fake_prepare)
    monkeypatch.setattr(attendance_api, "store_photo", lambda *args, **kwargs: stored.append(1) or {"file_id": "file-1", "content_type": "image/jpeg"})
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: None)
    monkeypatch.setattr(attendance_api, "bind_photo_to_attendance", lambda reference, attendance_id: None)
    monkeypatch.setattr(attendance_api, "verify_and_record", lambda *args, **kwargs: {
        "success": True, "status": "PRESENT", "reason": None, "message": "ok",
        "timestamp": datetime.now(timezone.utc), "attendance_id": "attendance-real",
    })
    override_employee()
    client = TestClient(app)
    try:
        client.post("/api/attendance/verify-with-photo", data={"action": "check_in"}, files={"photo": ("shot.jpg", jpeg_bytes(), "image/jpeg")})
        assert stored == [1]
    finally:
        clear_overrides()


def test_failed_attendance_deletes_temporary_photo(monkeypatch):
    deleted = []

    async def fake_prepare(_upload):
        return b"jpeg", "image/jpeg"

    monkeypatch.setattr(attendance_api, "prepare_photo", fake_prepare)
    monkeypatch.setattr(attendance_api, "store_photo", lambda *args, **kwargs: {"file_id": "temp-file", "content_type": "image/jpeg"})
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: deleted.append(reference))
    monkeypatch.setattr(attendance_api, "bind_photo_to_attendance", lambda reference, attendance_id: None)
    monkeypatch.setattr(attendance_api, "verify_and_record", lambda *args, **kwargs: {
        "success": False, "status": "REJECTED", "reason": "ALREADY_CHECKED_IN",
        "message": "You have already checked in today.", "timestamp": datetime.now(timezone.utc),
    })
    override_employee()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/verify-with-photo", data={"action": "check_in"}, files={"photo": ("shot.jpg", jpeg_bytes(), "image/jpeg")})
        assert response.json()["success"] is False
        assert deleted == [{"file_id": "temp-file", "content_type": "image/jpeg"}]
    finally:
        clear_overrides()


def test_attendance_exception_deletes_temporary_photo(monkeypatch):
    deleted = []

    async def fake_prepare(_upload):
        return b"jpeg", "image/jpeg"

    monkeypatch.setattr(attendance_api, "prepare_photo", fake_prepare)
    monkeypatch.setattr(attendance_api, "store_photo", lambda *args, **kwargs: {"file_id": "temp-file", "content_type": "image/jpeg"})
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: deleted.append(reference))
    monkeypatch.setattr(attendance_api, "bind_photo_to_attendance", lambda reference, attendance_id: None)
    def boom(*_args, **_kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(attendance_api, "verify_and_record", boom)
    override_employee()
    client = TestClient(app)
    try:
     with pytest.raises(RuntimeError, match="write failed"):
        client.post(
            "/api/attendance/verify-with-photo",
            data={"action": "check_in"},
            files={"photo": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        )

     assert deleted == [{"file_id": "temp-file", "content_type": "image/jpeg"}]
    finally:
     clear_overrides()

def test_admin_can_retrieve_photo(monkeypatch):
    class Stream:
        def read(self):
            return b"jpeg-bytes"

    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=SimpleNamespace(find_one=lambda query: {
        "attendance_id": "att-1",
        "check_in_photo_reference": {"file_id": str(ObjectId()), "content_type": "image/jpeg"},
    })))
    monkeypatch.setattr(attendance_api, "open_photo", lambda reference: Stream())
    override_admin()
    client = TestClient(app)
    try:
        response = client.get("/api/attendance/admin/att-1/photo?event=check_in")
        assert response.status_code == 200
        assert response.content == b"jpeg-bytes"
        assert response.headers["cache-control"] == "private, no-store"
    finally:
        clear_overrides()


def test_admin_attendance_includes_records_with_missing_employees(monkeypatch):
    attendance_record = {
        "attendance_id": "att-missing-employee",
        "employee_id": "EMP-REMOVED",
        "user_name": "Former Employee",
        "date": "2026-09-24",
        "final_status": "PRESENT",
    }

    class Cursor:
        def __init__(self, records):
            self.records = records

        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def __iter__(self):
            return iter(self.records)

    class AttendanceCollection:
        def find(self, _query):
            return Cursor([attendance_record])

    class EmployeeCollection:
        def find_one(self, *_args):
            return None

        def find(self, *_args):
            return Cursor([])

    db = SimpleNamespace(attendance=AttendanceCollection(), employees=EmployeeCollection())
    monkeypatch.setattr(attendance_api, "get_db", lambda: db)
    override_admin()
    client = TestClient(app)
    try:
        response = client.get("/api/attendance/admin?date=2026-09-24")

        assert response.status_code == 200
        records = response.json()
        assert len(records) == 1
        assert records[0]["attendance_id"] == "att-missing-employee"
        assert records[0]["employee"] == {"full_name": "Former Employee", "department": ""}
    finally:
        clear_overrides()


def test_employee_receives_403_for_admin_photo_retrieval():
    override_employee()
    client = TestClient(app)
    try:
        response = client.get("/api/attendance/admin/att-1/photo?event=check_in")
        assert response.status_code == 403
    finally:
        clear_overrides()


def test_admin_undo_check_in_removes_only_check_in_and_audits(monkeypatch):
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "user_name": "Test Employee",
        "check_in_time": datetime.now(timezone.utc),
        "check_in_location": {"latitude": 1.0, "longitude": 2.0},
        "check_in_photo_reference": {"file_id": "in-file"},
        "check_out_time": None,
        "check_out_location": None,
        "check_out_photo_reference": None,
        "final_status": "PRESENT",
        "unrelated": "preserved",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    deleted = []
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: deleted.append(reference))
    override_admin()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/admin/att-1/undo-check-in")
        assert response.status_code == 200
        assert record["check_in_time"] is None
        assert record["check_in_location"] is None
        assert record["check_in_photo_reference"] is None
        assert record["check_out_time"] is None
        assert record["check_out_location"] is None
        assert record["check_out_photo_reference"] is None
        assert record["final_status"] == "ABSENT"
        assert record["employee_id"] == "EMP-1"
        assert record["date"] == "2026-09-24"
        assert record["user_name"] == "Test Employee"
        assert record["unrelated"] == "preserved"
        assert deleted == []
        assert audit.events[0]["event_type"] == "UNDO_CHECK_IN"
        assert audit.events[0]["actor_id"] == "ADM-1"
        assert audit.events[0]["metadata"] == {"attendance_id": "att-1", "employee_id": "EMP-1", "date": "2026-09-24"}
        assert isinstance(audit.events[0]["created_at"], datetime)
    finally:
        clear_overrides()


def test_admin_undo_check_in_is_blocked_if_checkout_exists(monkeypatch):
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": datetime.now(timezone.utc),
        "check_in_location": {"latitude": 1.0, "longitude": 2.0},
        "check_in_photo_reference": {"file_id": "in-file"},
        "check_out_photo_reference": {"file_id": "out-file"},
        "check_out_time": datetime.now(timezone.utc),
        "check_out_location": {"latitude": 3.0, "longitude": 4.0},
        "final_status": "PRESENT",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    override_admin()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/admin/att-1/undo-check-in")
        assert response.status_code == 409
        assert response.json()["detail"] == "Undo Check Out first before undoing Check In."
        assert record["check_in_time"] is not None
        assert record["check_out_time"] is not None
        assert record["check_in_photo_reference"] == {"file_id": "in-file"}
        assert record["check_out_photo_reference"] == {"file_id": "out-file"}
        assert audit.events == []
    finally:
        clear_overrides()


def test_admin_undo_check_out_removes_only_checkout_and_audits(monkeypatch):
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": datetime.now(timezone.utc),
        "check_in_location": {"latitude": 1.0, "longitude": 2.0},
        "check_in_photo_reference": {"file_id": "in-file"},
        "check_out_photo_reference": {"file_id": "out-file"},
        "check_out_time": datetime.now(timezone.utc),
        "check_out_location": {"latitude": 3.0, "longitude": 4.0},
        "final_status": "PRESENT",
        "unrelated": "preserved",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    deleted = []
    monkeypatch.setattr(attendance_api, "delete_photo", lambda reference: deleted.append(reference))
    override_admin()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/admin/att-1/undo-check-out")
        assert response.status_code == 200
        assert record["check_out_time"] is None
        assert record["check_out_location"] is None
        assert record["check_out_photo_reference"] is None
        assert record["check_in_time"] is not None
        assert record["check_in_location"] == {"latitude": 1.0, "longitude": 2.0}
        assert record["check_in_photo_reference"] == {"file_id": "in-file"}
        assert record["final_status"] == "PRESENT"
        assert record["unrelated"] == "preserved"
        assert deleted == []
        assert audit.events[0]["event_type"] == "UNDO_CHECK_OUT"
        assert audit.events[0]["actor_id"] == "ADM-1"
        assert audit.events[0]["metadata"]["employee_id"] == "EMP-1"
        assert audit.events[0]["metadata"]["attendance_id"] == "att-1"
        assert isinstance(audit.events[0]["created_at"], datetime)
    finally:
        clear_overrides()


def test_employee_can_undo_own_check_out_and_preserves_check_in(monkeypatch):
    check_in_time = datetime.now(timezone.utc)
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": check_in_time,
        "check_in_location": {"latitude": 1.0, "longitude": 2.0},
        "check_in_photo_reference": {"file_id": "in-file"},
        "check_out_time": datetime.now(timezone.utc),
        "check_out_location": {"latitude": 3.0, "longitude": 4.0},
        "check_out_photo_reference": {"file_id": "out-file"},
        "final_status": "PRESENT",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    override_employee()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/mine/att-1/undo-check-out")

        assert response.status_code == 200
        assert record["check_out_time"] is None
        assert record["check_out_location"] is None
        assert record["check_out_photo_reference"] is None
        assert record["check_in_time"] == check_in_time
        assert record["check_in_location"] == {"latitude": 1.0, "longitude": 2.0}
        assert record["check_in_photo_reference"] == {"file_id": "in-file"}
        assert record["final_status"] == "PRESENT"
        assert audit.events[0]["event_type"] == "UNDO_CHECK_OUT"
        assert audit.events[0]["actor_id"] == "EMP-1"
        assert audit.events[0]["metadata"] == {"attendance_id": "att-1", "employee_id": "EMP-1", "date": "2026-09-24"}
        assert isinstance(audit.events[0]["created_at"], datetime)
    finally:
        clear_overrides()


def test_employee_can_undo_own_check_in_after_undoing_checkout(monkeypatch):
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": datetime.now(timezone.utc),
        "check_in_location": {"latitude": 1.0, "longitude": 2.0},
        "check_in_photo_reference": {"file_id": "in-file"},
        "check_out_time": datetime.now(timezone.utc),
        "check_out_location": {"latitude": 3.0, "longitude": 4.0},
        "check_out_photo_reference": {"file_id": "out-file"},
        "final_status": "PRESENT",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    override_employee()
    client = TestClient(app)
    try:
        check_out_response = client.post("/api/attendance/mine/att-1/undo-check-out")
        assert check_out_response.status_code == 200

        response = client.post("/api/attendance/mine/att-1/undo-check-in")

        assert response.status_code == 200
        assert record["check_in_time"] is None
        assert record["check_in_location"] is None
        assert record["check_in_photo_reference"] is None
        assert record["check_out_time"] is None
        assert record["check_out_location"] is None
        assert record["check_out_photo_reference"] is None
        assert record["final_status"] == "ABSENT"
        assert [event["event_type"] for event in audit.events] == ["UNDO_CHECK_OUT", "UNDO_CHECK_IN"]
        assert all(event["actor_id"] == "EMP-1" for event in audit.events)
        assert all(event["metadata"] == {"attendance_id": "att-1", "employee_id": "EMP-1", "date": "2026-09-24"} for event in audit.events)
        assert all(isinstance(event["created_at"], datetime) for event in audit.events)
    finally:
        clear_overrides()


def test_employee_cannot_undo_check_in_while_checkout_exists(monkeypatch):
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": datetime.now(timezone.utc),
        "check_out_time": datetime.now(timezone.utc),
        "check_in_photo_reference": {"file_id": "in-file"},
        "check_out_photo_reference": {"file_id": "out-file"},
        "final_status": "PRESENT",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    override_employee()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/mine/att-1/undo-check-in")

        assert response.status_code == 409
        assert response.json()["detail"] == "Undo Check Out first before undoing Check In."
        assert record["check_in_time"] is not None
        assert record["check_out_time"] is not None
        assert audit.events == []
    finally:
        clear_overrides()


def test_employee_cannot_undo_another_employees_attendance(monkeypatch):
    record = {
        "_id": "mongo-1",
        "attendance_id": "att-other",
        "employee_id": "EMP-2",
        "date": "2026-09-24",
        "check_in_time": datetime.now(timezone.utc),
        "check_out_time": datetime.now(timezone.utc),
        "final_status": "PRESENT",
    }
    attendance = MemoryAttendance(record)
    audit = MemoryAudit()
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=attendance, audit_logs=audit))
    override_employee()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/mine/att-other/undo-check-out")

        assert response.status_code == 404
        assert record["check_out_time"] is not None
        assert audit.events == []
    finally:
        clear_overrides()


@pytest.mark.parametrize("action", ["undo-check-in", "undo-check-out"])
def test_admin_cannot_use_employee_self_undo_routes(action, monkeypatch):
    monkeypatch.setattr(attendance_api, "get_db", lambda: pytest.fail("A non-employee must be rejected before attendance lookup"))
    app.dependency_overrides[current_employee] = lambda: {"employee_id": "ADM-1", "role": "admin", "is_active": True}
    client = TestClient(app)
    try:
        response = client.post(f"/api/attendance/mine/att-1/{action}")
        assert response.status_code == 403
    finally:
        clear_overrides()


@pytest.mark.parametrize("action", ["undo-check-in", "undo-check-out"])
def test_employee_cannot_call_admin_undo_operations(action, monkeypatch):
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace())
    override_employee()
    client = TestClient(app)
    try:
        response = client.post(f"/api/attendance/admin/att-1/{action}")
        assert response.status_code == 403
    finally:
        clear_overrides()


def test_old_generic_employee_undo_endpoint_is_removed():
    override_employee()
    client = TestClient(app)
    try:
        assert client.delete("/api/attendance/mine/att-1?action=record").status_code == 404
    finally:
        clear_overrides()


def test_photo_must_be_present_on_verify_with_photo():
    override_employee()
    client = TestClient(app)
    try:
        response = client.post("/api/attendance/verify-with-photo", data={"action": "check_in"})
        assert response.status_code == 422
    finally:
        clear_overrides()


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_frontend_camera_flow_uses_inline_getusermedia():
    source = (REPO_ROOT / "frontend" / "src" / "LocationAttendance.jsx").read_text(encoding="utf-8")
    assert "getUserMedia" in source
    assert "useState('environment')" in source
    assert "facingMode: { exact: cameraFacingMode }" in source
    assert "setCameraFacingMode(mode => mode === 'environment' ? 'user' : 'environment')" in source
    assert "Take Photo" in source
    assert "Retake" in source
    assert "Use Photo & ${actionLabel}" in source
    assert "Cancel" in source
    assert 'type="file"' not in source
    assert "openPhotoCapture" not in source


def test_admin_dashboard_photo_viewer_is_in_react():
    source = (REPO_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "View Check-In Photo" in source
    assert "View Check-Out Photo" in source
    assert "Photo unavailable" in source
    assert "Undo Check In" in source
    assert "Undo Check Out" in source
    assert "This will remove the employee's check-in time, location, and check-in photo reference." in source
    assert "This will remove the employee's check-out time, location, and check-out photo reference." in source
    assert "function AdminPhotoModal" in source
    assert "function AdminDashboard" in source


def test_admin_can_retrieve_checkout_photo(monkeypatch):
    class Stream:
        def read(self):
            return b"checkout-jpeg"

    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=SimpleNamespace(find_one=lambda query: {
        "attendance_id": "att-1",
        "check_out_photo_reference": {"file_id": str(ObjectId()), "content_type": "image/jpeg"},
    })))
    monkeypatch.setattr(attendance_api, "open_photo", lambda reference: Stream())
    override_admin()
    client = TestClient(app)
    try:
        response = client.get("/api/attendance/admin/att-1/photo?event=check_out")
        assert response.status_code == 200
        assert response.content == b"checkout-jpeg"
        assert response.headers["cache-control"] == "private, no-store"
    finally:
        clear_overrides()


def test_photo_retrieval_returns_unavailable_if_gridfs_file_is_missing(monkeypatch):
    monkeypatch.setattr(attendance_api, "get_db", lambda: SimpleNamespace(attendance=SimpleNamespace(find_one=lambda query: {
        "attendance_id": "att-1",
        "check_in_photo_reference": {"file_id": str(ObjectId()), "content_type": "image/jpeg"},
    })))

    def unavailable(_reference):
        raise photo_service.PhotoUnavailableError("Photo unavailable")

    monkeypatch.setattr(attendance_api, "open_photo", unavailable)
    override_admin()
    client = TestClient(app)
    try:
        response = client.get("/api/attendance/admin/att-1/photo?event=check_in")
        assert response.status_code == 404
        assert response.json()["detail"] == "Photo unavailable"
    finally:
        clear_overrides()


def test_public_attendance_shows_old_referenced_photo_regardless_of_legacy_expiry():
    expired = datetime.now(timezone.utc) - timedelta(hours=1)
    record = public_attendance({
        "attendance_id": "attendance-1",
        "employee_id": "EMP-1",
        "date": "2026-09-24",
        "check_in_time": datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc),
        "check_out_time": None,
        "check_in_location": {"latitude": 12.9, "longitude": 77.6},
        "check_out_location": None,
        "final_status": "PRESENT",
        "check_in_photo_reference": {"file_id": "secret-file-id", "content_type": "image/jpeg", "expires_at": expired},
    })
    assert record["check_in_photo_available"] is True
    assert record["check_in_time"] is not None
    assert record["employee_id"] == "EMP-1"
    assert "secret-file-id" not in str(record)


def test_open_photo_ignores_legacy_expiry_metadata(monkeypatch):
    old_expiry = datetime.now(timezone.utc) - timedelta(days=1000)

    class Stream:
        metadata = {"expires_at": old_expiry}

        def read(self):
            return b"jpeg-bytes"

    monkeypatch.setattr(photo_service, "GridFSBucket", lambda _db: SimpleNamespace(open_download_stream=lambda _id: Stream()))
    monkeypatch.setattr(photo_service, "get_db", lambda: object())
    stream = photo_service.open_photo({"file_id": str(ObjectId()), "content_type": "image/jpeg", "expires_at": old_expiry})
    assert stream.read() == b"jpeg-bytes"


@pytest.mark.parametrize("event", ["check_in", "check_out"])
def test_new_attendance_photo_uploads_never_receive_expiry(monkeypatch, event):
    uploads = []

    class FakeBucket:
        def upload_from_stream(self, _filename, _stream, metadata=None):
            uploads.append(metadata)
            return ObjectId()

    monkeypatch.setattr(photo_service, "GridFSBucket", lambda _db: FakeBucket())
    monkeypatch.setattr(photo_service, "get_db", lambda: object())
    reference = photo_service.store_photo(b"jpeg", "image/jpeg", "EMP-1", "att-1", event)
    assert "expires_at" not in uploads[0]
    assert "expires_at" not in reference


def test_attendance_photo_cleanup_job_and_endpoint_are_removed():
    assert not hasattr(photo_service, "cleanup_expired_photos")
    client = TestClient(app)
    assert client.get("/api/admin/photos/cleanup").status_code == 404
    assert client.post("/api/admin/photos/cleanup").status_code == 404
