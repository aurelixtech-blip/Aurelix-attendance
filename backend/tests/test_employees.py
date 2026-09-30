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
            for key, value in update.get("$inc", {}).items():
                employee[key] = employee.get(key, 0) + value

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
    app.dependency_overrides[current_claims] = lambda: {"sub": "ADM-2", "role": "admin"}
    removal = client.delete("/api/employees/ADM-2")
    assert removal.status_code == 400
    assert removal.json()["detail"] == "You cannot remove your own account."
    assert collection.employees == [administrator]


def test_admin_cannot_delete_own_account(monkeypatch):
    administrator = employee("ADM-1", role="admin")
    client, collection = employee_client(monkeypatch, [administrator])

    response = client.delete("/api/employees/ADM-1")

    assert response.status_code == 400
    assert response.json()["detail"] == "You cannot remove your own account."
    assert collection.employees == [administrator]


def test_admin_can_delete_another_administrator(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    additional_admin = employee("ADM-002", role="admin")
    another_admin = employee("ADM-003", role="admin")
    client, collection = employee_client(monkeypatch, [system_admin, additional_admin, another_admin], subject="ADM-001")

    response = client.delete("/api/employees/ADM-002")

    assert response.status_code == 200
    assert response.json()["employee_id"] == "ADM-002"
    assert collection.employees == [system_admin, another_admin]


def test_original_system_admin_cannot_be_deleted_by_another_admin(monkeypatch):
    administrator = employee("ADM-002", role="admin")
    system_admin = employee("ADM-001", role="admin")
    client, collection = employee_client(monkeypatch, [administrator, system_admin])

    response = client.delete("/api/employees/ADM-001")

    assert response.status_code == 400
    assert "cannot be removed" in response.json()["detail"]
    assert collection.employees == [administrator, system_admin]


def test_original_system_admin_employee_id_cannot_be_changed(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    client, _collection = employee_client(monkeypatch, [system_admin])

    response = client.put("/api/employees/ADM-001", json={
        "employee_id": "ADM-002",
        "full_name": system_admin["full_name"],
        "email": system_admin["email"],
        "department": system_admin["department"],
        "role": "admin",
        "password": None,
    })

    assert response.status_code == 400
    assert system_admin["employee_id"] == "ADM-001"


def test_original_system_admin_cannot_change_own_role(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    client, collection = employee_client(monkeypatch, [system_admin], subject="ADM-001")

    response = client.put("/api/employees/ADM-001", json={
        "employee_id": "ADM-001",
        "full_name": system_admin["full_name"],
        "email": system_admin["email"],
        "department": system_admin["department"],
        "role": "employee",
        "password": None,
    })

    assert response.status_code == 400
    assert system_admin["role"] == "admin"
    assert system_admin.get("auth_version", 0) == 0
    assert collection.employees == [system_admin]


def test_original_system_admin_can_edit_details_and_remains_admin(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    client, _collection = employee_client(monkeypatch, [system_admin], subject="ADM-001")

    response = client.put("/api/employees/ADM-001", json={
        "employee_id": "ADM-001",
        "full_name": "Updated System Admin",
        "email": system_admin["email"],
        "department": "Security",
        "role": "admin",
        "password": None,
    })

    assert response.status_code == 200
    assert system_admin["full_name"] == "Updated System Admin"
    assert system_admin["department"] == "Security"
    assert system_admin["role"] == "admin"


def test_original_system_admin_can_change_another_users_role(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    target = employee("EMP-1", role="employee")
    client, _collection = employee_client(monkeypatch, [system_admin, target], subject="ADM-001")

    response = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-1",
        "full_name": target["full_name"],
        "email": target["email"],
        "department": target["department"],
        "role": "admin",
        "password": None,
    })

    assert response.status_code == 200
    assert target["role"] == "admin"
    assert target["auth_version"] == 1


def test_additional_admin_can_change_own_role(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    additional_admin = employee("ADM-002", role="admin")
    client, _collection = employee_client(monkeypatch, [system_admin, additional_admin], subject="ADM-002")

    response = client.put("/api/employees/ADM-002", json={
        "employee_id": "ADM-002",
        "full_name": additional_admin["full_name"],
        "email": additional_admin["email"],
        "department": additional_admin["department"],
        "role": "employee",
        "password": None,
    })

    assert response.status_code == 200
    assert additional_admin["role"] == "employee"
    assert additional_admin["auth_version"] == 1


def test_additional_admin_can_change_another_users_role(monkeypatch):
    additional_admin = employee("ADM-002", role="admin")
    target = employee("EMP-1", role="employee")
    client, _collection = employee_client(monkeypatch, [additional_admin, target], subject="ADM-002")

    response = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-1",
        "full_name": target["full_name"],
        "email": target["email"],
        "department": target["department"],
        "role": "admin",
        "password": None,
    })

    assert response.status_code == 200
    assert target["role"] == "admin"
    assert target["auth_version"] == 1


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


@pytest.mark.parametrize("role", ["employee", "admin"])
def test_admin_can_create_person_with_selected_role(monkeypatch, role):
    administrator = employee("ADM-001", role="admin")
    client, collection = employee_client(monkeypatch, [administrator])
    monkeypatch.setattr(employees_api, "hash_password", lambda password: f"hashed:{password}")

    response = client.post("/api/employees", json={
        "employee_id": f"NEW-{role}",
        "full_name": "New Person",
        "email": f"new-{role}@example.com",
        "department": "Operations",
        "role": role,
        "password": "temporary-password",
        "recovery_email": f"recovery-{role}@example.com",
    })

    assert response.status_code == 201
    created = next(item for item in collection.employees if item["employee_id"] == f"NEW-{role}")
    assert created["role"] == role


def test_admin_creation_with_invalid_role_is_rejected(monkeypatch):
    client, collection = employee_client(monkeypatch, [employee("ADM-001", role="admin")])
    payload = {
        "employee_id": "NEW-INVALID",
        "full_name": "New Person",
        "email": "new-invalid@example.com",
        "department": "Operations",
        "role": "superadmin",
        "password": "temporary-password",
        "recovery_email": "recovery-invalid@example.com",
    }

    response = client.post("/api/employees", json=payload)

    assert response.status_code == 422
    assert len(collection.employees) == 1


def test_create_rejects_email_owned_by_a_different_inactive_account(monkeypatch):
    inactive_account = employee("ADM-OLD", role="admin", is_active=False)
    client, collection = employee_client(monkeypatch, [employee("ADM-001", role="admin"), inactive_account])

    response = client.post("/api/employees", json={
        "employee_id": "ADM-002",
        "full_name": "Additional Admin",
        "email": inactive_account["email"],
        "department": "Operations",
        "role": "admin",
        "password": "a-distinct-password",
        "recovery_email": "additional-admin-recovery@example.com",
    })

    assert response.status_code == 409
    assert inactive_account["employee_id"] == "ADM-OLD"
    assert inactive_account["is_active"] is False
    assert len(collection.employees) == 2


def test_admin_edit_with_invalid_role_is_rejected(monkeypatch):
    person = employee("EMP-1", role="employee")
    client, _collection = employee_client(monkeypatch, [employee("ADM-001", role="admin"), person])

    response = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-1",
        "full_name": person["full_name"],
        "email": person["email"],
        "department": person["department"],
        "role": "superadmin",
        "password": None,
    })

    assert response.status_code == 422
    assert person["role"] == "employee"


def test_multiple_admin_accounts_can_be_created(monkeypatch):
    system_admin = employee("ADM-001", role="admin", email="system-admin@example.com", recovery_email="system-recovery@example.com")
    client, collection = employee_client(monkeypatch, [system_admin])

    for employee_id, email, recovery_email, password in (
        ("ADM-002", "admin-two@example.com", "admin-two-recovery@example.com", "second-admin-password"),
        ("ADM-003", "admin-three@example.com", "admin-three-recovery@example.com", "third-admin-password"),
    ):
        response = client.post("/api/employees", json={
            "employee_id": employee_id,
            "full_name": "Additional Admin",
            "email": email,
            "department": "Operations",
            "role": "admin",
            "password": password,
            "recovery_email": recovery_email,
        })
        assert response.status_code == 201

    assert [item["employee_id"] for item in collection.employees] == ["ADM-001", "ADM-002", "ADM-003"]
    assert len({item["_id"] for item in collection.employees}) == 3
    assert len({item["email"] for item in collection.employees}) == 3
    assert len({item["recovery_email"] for item in collection.employees}) == 3
    assert len({item["password_hash"] for item in collection.employees}) == 3
    assert all(item["role"] == "admin" and item.get("recovery_email_verified", False) is False for item in collection.employees)


def test_admin_create_rejects_duplicate_login_email(monkeypatch):
    system_admin = employee("ADM-001", role="admin", email="system-admin@example.com")
    client, collection = employee_client(monkeypatch, [system_admin])

    response = client.post("/api/employees", json={
        "employee_id": "ADM-002",
        "full_name": "Additional Admin",
        "email": "SYSTEM-ADMIN@example.com",
        "department": "Operations",
        "role": "admin",
        "password": "different-password",
        "recovery_email": "additional-recovery@example.com",
    })

    assert response.status_code == 409
    assert collection.employees == [system_admin]


def test_admin_cannot_delete_their_own_additional_admin_account(monkeypatch):
    system_admin = employee("ADM-001", role="admin")
    additional_admin = employee("ADM-002", role="admin")
    client, collection = employee_client(monkeypatch, [system_admin, additional_admin], subject="ADM-002")

    response = client.delete("/api/employees/ADM-002")

    assert response.status_code == 400
    assert response.json()["detail"] == "You cannot remove your own account."
    assert collection.employees == [system_admin, additional_admin]


@pytest.mark.parametrize(("initial_role", "updated_role"), [("employee", "admin"), ("admin", "employee")])
def test_admin_can_change_roles_and_invalidates_existing_sessions(monkeypatch, initial_role, updated_role):
    administrator = employee("ADM-001", role="admin")
    person = employee("EMP-1", role=initial_role)
    client, _collection = employee_client(monkeypatch, [administrator, person])

    response = client.put("/api/employees/EMP-1", json={
        "employee_id": "EMP-1",
        "full_name": person["full_name"],
        "email": person["email"],
        "department": person["department"],
        "role": updated_role,
        "password": None,
    })

    assert response.status_code == 200
    assert response.json()["role"] == updated_role
    assert person["role"] == updated_role
    assert person["auth_version"] == 1


def test_employee_cannot_access_admin_people_api(monkeypatch):
    person = employee("EMP-1", role="employee")
    collection = MemoryEmployees([person])
    monkeypatch.setattr(employees_api, "get_db", lambda: SimpleNamespace(employees=collection))
    app.dependency_overrides[current_claims] = lambda: {"sub": "EMP-1", "role": "employee"}
    client = TestClient(app)

    response = client.get("/api/employees")

    assert response.status_code == 403


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