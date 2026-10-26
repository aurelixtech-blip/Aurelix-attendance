from datetime import datetime
import re
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import attendance as attendance_api
from app.api import deps as deps_api
from app.core import security
from app.core.security import current_claims
from app.main import app


class MemoryCursor(list):
    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, order in reversed(key):
                list.sort(self, key=lambda item: item.get(field), reverse=order == -1)
            return self
        list.sort(self, key=lambda item: item.get(key), reverse=direction == -1)
        return self

    def limit(self, count):
        return MemoryCursor(self[:count])


class MemoryCollection:
    def __init__(self, documents):
        self.documents = documents

    @staticmethod
    def matches(document, query):
        for key, condition in query.items():
            value = document.get(key)
            if isinstance(condition, dict):
                if "$in" in condition and value not in condition["$in"]:
                    return False
                if "$regex" in condition and not re.search(condition["$regex"], value or ""):
                    return False
                if "$gte" in condition and value < condition["$gte"]:
                    return False
                if "$lte" in condition and value > condition["$lte"]:
                    return False
            elif value != condition:
                return False
        return True

    def find(self, query, _projection=None):
        return MemoryCursor([item for item in self.documents if self.matches(item, query)])

    def find_one(self, query, _projection=None):
        return next((item for item in self.documents if self.matches(item, query)), None)


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def account(employee_id, role="employee", active=True, full_name=None):
    return {
        "employee_id": employee_id,
        "full_name": full_name or employee_id,
        "email": f"{employee_id.lower()}@example.com",
        "department": "Operations",
        "role": role,
        "is_active": active,
        "auth_version": 0,
    }


def api_client(monkeypatch, employees, records):
    db = SimpleNamespace(
        employees=MemoryCollection(employees),
        attendance=MemoryCollection(records),
    )
    monkeypatch.setattr(deps_api, "get_db", lambda: db)
    monkeypatch.setattr(attendance_api, "get_db", lambda: db)
    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-001", "role": "admin"}
    return TestClient(app), db


def test_system_admin_cannot_use_employee_attendance_endpoints(monkeypatch):
    admin = account("ADM-001", role="admin", full_name="System Admin")
    client, _db = api_client(monkeypatch, [admin], [])

    for action in ("check_in", "check_out"):
        payload = {"action": action}
        assert client.post("/api/attendance/verify", json=payload).status_code == 403
        response = client.post(
            "/api/attendance/verify-with-photo",
            data=payload,
            files={"photo": ("capture.jpg", b"photo", "image/jpeg")},
        )
        assert response.status_code == 403


def test_employee_can_check_in_and_check_out(monkeypatch):
    employee = account("EMP-1")
    client, _db = api_client(monkeypatch, [employee], [])
    app.dependency_overrides[current_claims] = lambda: {"sub": "EMP-1", "role": "employee"}
    async def prepare_photo(_photo):
        return b"image", "image/jpeg"

    monkeypatch.setattr(attendance_api, "prepare_photo", prepare_photo)
    monkeypatch.setattr(attendance_api, "store_photo", lambda *_args: {"file_id": "photo"})
    monkeypatch.setattr(attendance_api, "verify_and_record", lambda _employee, payload, _reference: {
        "success": True,
        "status": "PRESENT",
        "message": payload.action,
    })
    monkeypatch.setattr(attendance_api, "bind_photo_to_attendance", lambda *_args: None)

    for action in ("check_in", "check_out"):
        response = client.post(
            "/api/attendance/verify-with-photo",
            data={"action": action},
            files={"photo": ("capture.jpg", b"photo", "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.json()["message"] == action


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "EMP-1"},
        {"sub": "EMP-1", "role": "unknown"},
        {"sub": "EMP-1", "role": "admin"},
    ],
)
def test_missing_invalid_or_inconsistent_role_claims_are_rejected(monkeypatch, claims):
    monkeypatch.setattr(security, "decode_access_token", lambda _token: claims)
    monkeypatch.setattr(
        security,
        "get_db",
        lambda: SimpleNamespace(employees=MemoryCollection([account("EMP-1")])),
    )

    with pytest.raises(HTTPException) as error:
        security.current_claims(token="test-token")

    assert error.value.status_code == 401
    assert "role" in error.value.detail


def test_system_admin_with_employee_role_is_rejected_with_diagnostic(monkeypatch):
    employee = account("ADM-001", role="employee", full_name="System Admin")
    monkeypatch.setattr(security, "decode_access_token", lambda _token: {"sub": "ADM-001", "role": "employee"})
    monkeypatch.setattr(
        security,
        "get_db",
        lambda: SimpleNamespace(employees=MemoryCollection([employee])),
    )

    with pytest.raises(HTTPException) as error:
        security.current_claims(token="test-token")

    assert error.value.status_code == 403
    assert "System Admin account must have the admin role" in error.value.detail


def test_reporting_uses_only_active_employee_accounts_consistently(monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    employees = [
        account("ADM-001", role="employee", full_name="System Admin"),
        account("EMP-1", full_name="Present Employee"),
        account("EMP-2", full_name="Absent Employee"),
        account("EMP-3", active=False, full_name="Inactive Employee"),
    ]
    records = [
        {"attendance_id": "present-1", "employee_id": "EMP-1", "date": today, "final_status": "PRESENT", "user_name": "Present Employee"},
        {"attendance_id": "admin-1", "employee_id": "ADM-001", "date": today, "final_status": "PRESENT", "user_name": "System Admin"},
        {"attendance_id": "inactive-1", "employee_id": "EMP-3", "date": today, "final_status": "PRESENT", "user_name": "Inactive Employee"},
    ]
    client, _db = api_client(monkeypatch, employees, records)

    dashboard = client.get("/api/attendance/dashboard")
    daily = client.get("/api/attendance/admin", params={"date": today})
    month = client.get("/api/attendance/admin/month", params={"month": today[:7]})

    assert dashboard.status_code == 200
    assert dashboard.json()["total_employees"] == 2
    assert dashboard.json()["present_today"] == 1
    assert dashboard.json()["absent_today"] == 1
    assert {item["employee_id"] for item in daily.json()} == {"EMP-1", "EMP-2"}
    assert next(item for item in daily.json() if item["employee_id"] == "EMP-2")["final_status"] == "ABSENT"
    present_only = client.get("/api/attendance/admin", params={"date": today, "status": "PRESENT"})
    absent_only = client.get("/api/attendance/admin", params={"date": today, "status": "ABSENT"})
    assert [item["employee_id"] for item in present_only.json()] == ["EMP-1"]
    assert [item["employee_id"] for item in absent_only.json()] == ["EMP-2"]
    assert {item["employee_id"] for item in month.json()} == {"EMP-1"}
    assert all(item["employee_id"] != "ADM-001" for item in daily.json() + month.json())
