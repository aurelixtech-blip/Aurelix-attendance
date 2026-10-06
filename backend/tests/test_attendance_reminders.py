from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.api import attendance_reminders as reminder_api
from app.core.security import require_admin
from app.main import app
from app.services.email_service import MockEmailProvider as TemplateEmailProvider
from app.services import attendance_reminders as reminder_service
from app.services.attendance_reminders import run_attendance_reminder


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query):
        return [item for item in self.documents if self._matches(item, query)]

    def find_one(self, query):
        return next((item for item in self.documents if self._matches(item, query)), None)

    def insert_one(self, document):
        identity = {key: document[key] for key in ("employee_id", "date", "reminder_type") if key in document}
        if identity and any(self._matches(item, identity) for item in self.documents):
            raise DuplicateKeyError("duplicate reminder key")
        self.documents.append(deepcopy(document))
        return SimpleNamespace(inserted_id=len(self.documents))

    def update_one(self, query, update):
        document = self.find_one(query)
        if not document:
            return SimpleNamespace(matched_count=0, modified_count=0)
        document.update(deepcopy(update.get("$set", {})))
        return SimpleNamespace(matched_count=1, modified_count=1)

    def delete_one(self, query):
        document = self.find_one(query)
        if document:
            self.documents.remove(document)
            return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class MockEmailProvider:
    def __init__(self, fail_count=0):
        self.fail_count = fail_count
        self.sent = []

    def send_attendance_reminder(self, recovery_email, employee_name, reminder_type):
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("simulated delivery failure")
        self.sent.append((recovery_email, employee_name, reminder_type))


def make_employee(**overrides):
    employee = {
        "employee_id": "EMP-1",
        "full_name": "Test Employee",
        "email": "login@example.com",
        "role": "employee",
        "recovery_email": "verified@example.net",
        "recovery_email_verified": True,
        "is_active": True,
    }
    employee.update(overrides)
    return employee


def make_db(employees=None, attendance=None):
    return SimpleNamespace(
        employees=MemoryCollection(employees),
        attendance=MemoryCollection(attendance),
        attendance_reminders=MemoryCollection(),
    )


def test_employee_without_check_in_receives_check_in_reminder():
    db = make_db([make_employee()])
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email)

    assert result == {"success": True, "eligible": 1, "sent": 1, "skipped": 0, "failed": 0}
    assert email.sent == [("verified@example.net", "Test Employee", "check_in")]


def test_admin_without_check_in_does_not_receive_check_in_reminder():
    db = make_db([make_employee(employee_id="ADM-001", role="admin")])
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email)

    assert result["eligible"] == 0
    assert email.sent == []


def test_admin_checked_in_without_check_out_does_not_receive_check_out_reminder():
    now = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    db = make_db(
        [make_employee(employee_id="ADM-002", role="admin")],
        [{"employee_id": "ADM-002", "date": "2026-10-01", "check_in_time": now, "check_out_time": None}],
    )
    email = MockEmailProvider()

    result = run_attendance_reminder("check_out", db=db, email_provider=email, now=now)

    assert result["eligible"] == 0
    assert email.sent == []


def test_employee_who_checked_in_does_not_receive_check_in_reminder():
    db = make_db(
        [make_employee()],
        [{"employee_id": "EMP-1", "date": "2026-10-01", "check_in_time": datetime.now(timezone.utc)}],
    )
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email, now=datetime(2026, 10, 1, 8, tzinfo=timezone.utc))

    assert result["eligible"] == 0
    assert email.sent == []


def test_inactive_employee_does_not_receive_reminder():
    db = make_db([make_employee(is_active=False)])
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email)

    assert result["eligible"] == 0
    assert email.sent == []


def test_employee_without_verified_recovery_email_does_not_receive_reminder():
    db = make_db([make_employee(recovery_email_verified=False)])
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email)

    assert result["eligible"] == 0
    assert email.sent == []


def test_employee_with_unusable_verified_recovery_email_does_not_receive_reminder():
    db = make_db([make_employee(recovery_email="not-an-email")])
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email)

    assert result["eligible"] == 0
    assert email.sent == []


def test_employee_checked_in_without_check_out_receives_check_out_reminder():
    db = make_db(
        [make_employee()],
        [{"employee_id": "EMP-1", "date": "2026-10-01", "check_in_time": datetime.now(timezone.utc), "check_out_time": None}],
    )
    email = MockEmailProvider()

    result = run_attendance_reminder("check_out", db=db, email_provider=email, now=datetime(2026, 10, 1, 8, tzinfo=timezone.utc))

    assert result["sent"] == 1
    assert email.sent == [("verified@example.net", "Test Employee", "check_out")]


def test_employee_who_checked_out_does_not_receive_check_out_reminder():
    db = make_db(
        [make_employee()],
        [{"employee_id": "EMP-1", "date": "2026-10-01", "check_in_time": datetime.now(timezone.utc), "check_out_time": datetime.now(timezone.utc)}],
    )
    email = MockEmailProvider()

    result = run_attendance_reminder("check_out", db=db, email_provider=email, now=datetime(2026, 10, 1, 8, tzinfo=timezone.utc))

    assert result["eligible"] == 0
    assert email.sent == []


def test_employee_who_never_checked_in_does_not_receive_check_out_reminder():
    db = make_db(
        [make_employee()],
        [{"employee_id": "EMP-1", "date": "2026-10-01", "check_in_time": None, "check_out_time": None}],
    )
    email = MockEmailProvider()

    result = run_attendance_reminder("check_out", db=db, email_provider=email, now=datetime(2026, 10, 1, 8, tzinfo=timezone.utc))

    assert result["eligible"] == 0
    assert email.sent == []


def test_reminder_uses_verified_recovery_email_instead_of_login_email():
    employee = make_employee(email="login@example.com", recovery_email="Verified@Example.net")
    db = make_db([employee])
    email = MockEmailProvider()

    run_attendance_reminder("check_in", db=db, email_provider=email)

    assert email.sent[0][0] == "verified@example.net"
    assert email.sent[0][0] != employee["email"]


def test_reminder_templates_are_separate_and_match_requested_subjects():
    email = TemplateEmailProvider()

    email.send_attendance_reminder("employee@example.net", "Test Employee", "check_in")
    email.send_attendance_reminder("employee@example.net", "Test Employee", "check_out")

    assert email.sent_reminders[0][1] == "Aurelix Smart Attendance \u2014 Check-In Reminder"
    assert "have not checked in for today" in email.sent_reminders[0][2]
    assert email.sent_reminders[1][1] == "Aurelix Smart Attendance \u2014 Check-Out Reminder"
    assert "have checked in today but have not checked out" in email.sent_reminders[1][2]


def test_reminder_date_uses_asia_kolkata_at_utc_day_boundary():
    instant = datetime(2026, 10, 1, 18, 45, tzinfo=timezone.utc)
    previous_india_date = "2026-10-01"
    db = make_db(
        [make_employee()],
        [{"employee_id": "EMP-1", "date": previous_india_date, "check_in_time": datetime(2026, 10, 1, 8, tzinfo=timezone.utc)}],
    )
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email, now=instant)

    assert result["sent"] == 1
    assert db.attendance_reminders.documents[0]["date"] == "2026-10-02"


def test_previously_sent_reminder_is_not_sent_twice():
    db = make_db([make_employee()])
    email = MockEmailProvider()

    first = run_attendance_reminder("check_in", db=db, email_provider=email)
    second = run_attendance_reminder("check_in", db=db, email_provider=email)

    assert first["sent"] == 1
    assert second["skipped"] == 1
    assert len(email.sent) == 1


def test_failed_email_is_not_marked_sent_and_successful_retry_works():
    db = make_db([make_employee()])
    email = MockEmailProvider(fail_count=1)

    first = run_attendance_reminder("check_in", db=db, email_provider=email)
    assert first["failed"] == 1
    assert db.attendance_reminders.documents == []

    second = run_attendance_reminder("check_in", db=db, email_provider=email)
    assert second["sent"] == 1
    assert db.attendance_reminders.documents[0]["status"] == "sent"
    assert len(email.sent) == 1


def test_unauthorized_cron_requests_are_rejected(monkeypatch):
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(cron_secret="cron-test-secret"))
    client = TestClient(app)

    check_in = client.post("/api/cron/attendance-reminders/check-in")
    check_out = client.post("/api/cron/attendance-reminders/check-out", headers={"Authorization": "Bearer wrong"})

    assert check_in.status_code == 401
    assert check_out.status_code == 401


@pytest.mark.parametrize("endpoint", [
    "/api/cron/attendance-reminders/check-in",
    "/api/cron/attendance-reminders/check-out",
])
def test_cron_endpoint_accepts_x_cron_secret(monkeypatch, endpoint):
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(cron_secret="test-cron-secret"))
    monkeypatch.setattr(reminder_api, "run_attendance_reminder", lambda _reminder_type: {"success": True})
    response = TestClient(app).post(endpoint, headers={"X-Cron-Secret": "test-cron-secret"})

    assert response.status_code == 200


@pytest.mark.parametrize("endpoint", [
    "/api/cron/attendance-reminders/check-in",
    "/api/cron/attendance-reminders/check-out",
])
def test_cron_endpoint_rejects_invalid_x_cron_secret(monkeypatch, endpoint):
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(cron_secret="test-cron-secret"))
    response = TestClient(app).post(endpoint, headers={"X-Cron-Secret": "incorrect-secret"})

    assert response.status_code == 401


@pytest.mark.parametrize("endpoint", [
    "/api/cron/attendance-reminders/check-in",
    "/api/cron/attendance-reminders/check-out",
])
def test_cron_endpoint_rejects_missing_x_cron_secret(monkeypatch, endpoint):
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(cron_secret="test-cron-secret"))
    response = TestClient(app).post(endpoint)

    assert response.status_code == 401


@pytest.mark.parametrize("endpoint", [
    "/api/cron/attendance-reminders/check-in",
    "/api/cron/attendance-reminders/check-out",
])
def test_cron_endpoint_still_accepts_bearer_secret(monkeypatch, endpoint):
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(cron_secret="test-cron-secret"))
    monkeypatch.setattr(reminder_api, "run_attendance_reminder", lambda _reminder_type: {"success": True})
    response = TestClient(app).post(endpoint, headers={"Authorization": "Bearer test-cron-secret"})

    assert response.status_code == 200


def test_check_in_reminder_does_not_modify_attendance():
    attendance = [{"employee_id": "EMP-1", "date": "2026-10-01", "check_in_time": None, "check_out_time": None, "final_status": "PENDING"}]
    original = deepcopy(attendance)
    db = make_db([make_employee()], attendance)

    run_attendance_reminder("check_in", db=db, email_provider=MockEmailProvider(), now=datetime(2026, 10, 1, 8, tzinfo=timezone.utc))

    assert db.attendance.documents == original


def test_check_out_reminder_does_not_modify_attendance():
    attendance = [{"employee_id": "EMP-1", "date": "2026-10-01", "check_in_time": datetime(2026, 10, 1, 8, tzinfo=timezone.utc), "check_out_time": None, "final_status": "PRESENT"}]
    original = deepcopy(attendance)
    db = make_db([make_employee()], attendance)

    run_attendance_reminder("check_out", db=db, email_provider=MockEmailProvider(), now=datetime(2026, 10, 1, 14, tzinfo=timezone.utc))

    assert db.attendance.documents == original


def test_expired_in_flight_claim_can_be_retried():
    now = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    db = make_db([make_employee()])
    db.attendance_reminders.documents.append({
        "employee_id": "EMP-1",
        "date": "2026-10-01",
        "reminder_type": "check_in",
        "status": "sending",
        "claim_id": "expired-claim",
        "lease_expires_at": now - timedelta(seconds=1),
    })
    email = MockEmailProvider()

    result = run_attendance_reminder("check_in", db=db, email_provider=email, now=now)

    assert result["sent"] == 1
    assert db.attendance_reminders.documents[0]["status"] == "sent"
    assert len(email.sent) == 1


@pytest.fixture
def authorized_sample_client():
    app.dependency_overrides[require_admin] = lambda: {"sub": "ADM-1", "role": "admin"}
    yield TestClient(app)
    app.dependency_overrides.pop(require_admin, None)


def test_development_sample_trigger_sends_both_templates_to_configured_email(monkeypatch, authorized_sample_client):
    provider = TemplateEmailProvider()
    provider.delivery_mode = "smtp"
    db = make_db()
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(
        environment="development",
        attendance_reminder_test_email="pushkar.test@example.com",
    ))
    monkeypatch.setattr(reminder_api, "get_email_provider", lambda: provider)
    monkeypatch.setattr(reminder_service, "get_db", lambda: db)

    response = authorized_sample_client.post("/api/development/attendance-reminders/samples")

    assert response.status_code == 200
    assert response.json() == {"success": True, "attempted": 2, "sent": 2, "failed": 0}
    assert [item[0] for item in provider.sent_reminders] == ["pushkar.test@example.com"] * 2
    assert provider.sent_reminders[0][1] == "Aurelix Smart Attendance \u2014 Check-In Reminder"
    assert provider.sent_reminders[0][2] == (
        "Hello Test Employee,\n\nThis is a reminder that you have not checked in for today.\n\n"
        "Please complete your attendance check-in.\n\nAurelix Smart Attendance"
    )
    assert provider.sent_reminders[1][1] == "Aurelix Smart Attendance \u2014 Check-Out Reminder"
    assert provider.sent_reminders[1][2] == (
        "Hello Test Employee,\n\nThis is a reminder that you have checked in today but have not checked out.\n\n"
        "Please complete your attendance check-out.\n\nAurelix Smart Attendance"
    )
    assert db.attendance.documents == []
    assert db.attendance_reminders.documents == []


def test_development_sample_trigger_is_unavailable_in_production(monkeypatch, authorized_sample_client):
    provider = TemplateEmailProvider()
    provider.delivery_mode = "smtp"
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(
        environment="production",
        attendance_reminder_test_email="pushkar.test@example.com",
    ))
    monkeypatch.setattr(reminder_api, "get_email_provider", lambda: provider)

    response = authorized_sample_client.post("/api/development/attendance-reminders/samples")

    assert response.status_code == 404
    assert provider.sent_reminders == []


def test_development_sample_trigger_requires_admin_authorization(monkeypatch):
    monkeypatch.setattr(reminder_api, "get_settings", lambda: SimpleNamespace(
        environment="development",
        attendance_reminder_test_email="pushkar.test@example.com",
    ))

    response = TestClient(app).post("/api/development/attendance-reminders/samples")

    assert response.status_code == 401
