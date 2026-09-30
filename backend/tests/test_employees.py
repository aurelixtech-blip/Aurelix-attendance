from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import employees as employees_api
from app.core.security import current_claims
from app.main import app


class MemoryEmployees:
    def __init__(self, employees):
        self.employees = employees

    def find_one(self, query):
        for employee in self.employees:
            if all(self._matches(employee, key, value) for key, value in query.items()):
                return employee
        return None

    def _matches(self, employee, key, value):
        if isinstance(value, dict) and "$ne" in value:
            return employee.get(key) != value["$ne"]
        return employee.get(key) == value

    def update_one(self, query, update):
        employee = self.find_one(query)
        if employee:
            employee.update(update["$set"])

    def insert_one(self, document):
        document.setdefault("_id", f"db-{document['employee_id']}")
        self.employees.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    def delete_one(self, query):
        employee = self.find_one(query)
        if employee:
            self.employees.remove(employee)


class MemoryAuditLogs:
    def insert_one(self, _event):
        pass


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def employee(employee_id, role="employee", **overrides):
    return {
        "_id": f"db-{employee_id}",
        "employee_id": employee_id,
        "full_name": "Test Person",
        "email": f"{employee_id.lower()}@example.com",
        "department": "Operations",
        "role": role,
        "password_hash": "old-hash",
        "is_active": True,
        **overrides,
    }


def employee_client(monkeypatch, employees, subject="ADM-1"):
    collection = MemoryEmployees(employees)
    db = SimpleNamespace(employees=collection, audit_logs=MemoryAuditLogs())
    monkeypatch.setattr(employees_api, "get_db", lambda: db)
    monkeypatch.setattr(employees_api, "request_recovery_email_verification", lambda _db, _employee: {"challenge_token": "v" * 43, "message": "Verification code sent."})
    app.dependency_overrides[current_claims] = lambda: {"sub": subject, "role": "admin"}
    return TestClient(app), collection


def test_admin_can_edit_own_account(monkeypatch):
    administrator = employee("ADM-1", role="admin")
    client, collection = employee_client(monkeypatch, [administrator])
    monkeypatch.setattr(employees_api, "hash_password", lambda password: f"hashed:{password}")

    response = client.put("/api/employees/ADM-1", json={
        "employee_id": "ADM-2",
        "full_name": "Updated Admin",
        "email": "updated@example.com",
        "department": "Security",
        "role": "admin",
        "password": "new-password",
        "recovery_email": " Admin.Recovery@Example.com ",
    })

    assert response.status_code == 200
    assert response.json()["employee_id"] == "ADM-2"
    assert response.json()["full_name"] == "Updated Admin"
    assert response.json()["email"] == "updated@example.com"
    assert response.json()["department"] == "Security"
    assert "phone_number" not in response.json()
    assert administrator["recovery_email"] == "admin.recovery@example.com"
    assert administrator["recovery_email_verified"] is False
    assert response.json()["verification_challenge_token"] == "v" * 43
    assert administrator["password_hash"] == "hashed:new-password"
    removal = client.delete("/api/employees/ADM-2")
    assert removal.status_code == 400
    assert removal.json()["detail"] == "Administrator accounts cannot be removed"
    assert collection.employees == [administrator]


def test_admin_cannot_delete_own_account(monkeypatch):
    administrator = employee("ADM-1", role="admin")
    client, collection = employee_client(monkeypatch, [administrator])

    response = client.delete("/api/employees/ADM-1")

    assert response.status_code == 400
    assert response.json()["detail"] == "You cannot remove your own administrator account."
    assert collection.employees == [administrator]


def test_admin_cannot_delete_another_administrator(monkeypatch):
    own_account = employee("ADM-1", role="admin")
    other_admin = employee("ADM-2", role="admin")
    client, collection = employee_client(monkeypatch, [own_account, other_admin])

    response = client.delete("/api/employees/ADM-2")

    assert response.status_code == 400
    assert response.json()["detail"] == "Administrator accounts cannot be removed"
    assert collection.employees == [own_account, other_admin]


def test_admin_can_update_and_delete_normal_employee(monkeypatch):
    administrator = employee("ADM-1", role="admin")
    staff_member = employee("EMP-1")
    client, collection = employee_client(monkeypatch, [administrator, staff_member])

    update = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-2",
        "full_name": "Updated Employee",
        "email": "employee@example.com",
        "department": "Finance",
        "role": "employee",
        "password": None,
    })
    removal = client.delete("/api/employees/EMP-2")

    assert update.status_code == 200
    assert update.json()["employee_id"] == "EMP-2"
    assert removal.status_code == 200
    assert removal.json()["employee_id"] == "EMP-2"
    assert collection.employees == [administrator]


def test_admin_can_create_employee_without_phone_number(monkeypatch):
    administrator = employee("ADM-1", role="admin")
    client, collection = employee_client(monkeypatch, [administrator])
    monkeypatch.setattr(employees_api, "hash_password", lambda password: f"hashed:{password}")

    response = client.post("/api/employees", json={
        "employee_id": "EMP-2",
        "full_name": "New Employee",
        "email": "new@example.com",
        "department": "Operations",
        "role": "employee",
        "password": "temporary-password",
        "recovery_email": " New.Employee@Example.com ",
    })

    assert response.status_code == 201
    assert "phone_number" not in response.json()
    created = next(item for item in collection.employees if item["employee_id"] == "EMP-2")
    assert "phone_number" not in created
    assert created["recovery_email"] == "new.employee@example.com"
    assert created["recovery_email_verified"] is False
    assert response.json()["verification_challenge_token"] == "v" * 43


def test_employee_create_and_edit_reject_removed_phone_field(monkeypatch):
    administrator = employee("ADM-1", role="admin")
    staff_member = employee("EMP-1")
    client, _collection = employee_client(monkeypatch, [administrator, staff_member])

    create = client.post("/api/employees", json={
        "employee_id": "EMP-2",
        "full_name": "New Employee",
        "email": "new@example.com",
        "department": "Operations",
        "role": "employee",
        "password": "temporary-password",
        "recovery_email": "new@example.com",
        "phone_number": "+919876543210",
    })
    update = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-1",
        "full_name": "Test Person",
        "email": "emp-1@example.com",
        "department": "Operations",
        "role": "employee",
        "phone_number": "+919876543210",
    })

    assert create.status_code == 422
    assert update.status_code == 422


def test_new_employee_requires_valid_recovery_email(monkeypatch):
    client, _collection = employee_client(monkeypatch, [employee("ADM-1", role="admin")])
    payload = {
        "employee_id": "EMP-2",
        "full_name": "New Employee",
        "email": "new@example.com",
        "department": "Operations",
        "role": "employee",
        "password": "temporary-password",
    }

    missing = client.post("/api/employees", json=payload)
    invalid = client.post("/api/employees", json={**payload, "recovery_email": "not-an-email"})

    assert missing.status_code == 422
    assert invalid.status_code == 422


def test_admin_changing_recovery_email_requires_new_verification(monkeypatch):
    administrator = employee("ADM-1", role="admin", recovery_email="old@example.com", recovery_email_verified=True)
    staff_member = employee("EMP-1", recovery_email="old.staff@example.com", recovery_email_verified=True)
    client, _collection = employee_client(monkeypatch, [administrator, staff_member])

    response = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-1",
        "full_name": "Test Person",
        "email": "emp-1@example.com",
        "department": "Operations",
        "role": "employee",
        "recovery_email": "New.Staff@Example.com",
    })

    assert response.status_code == 200
    assert response.json()["recovery_email"] == "new.staff@example.com"
    assert response.json()["recovery_email_verified"] is False
    assert response.json()["verification_challenge_token"] == "v" * 43