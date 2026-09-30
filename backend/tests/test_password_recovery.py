from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import re

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import deps as deps_api
from app.api import employees as employees_api
from app.core import security
from app.core.rate_limit import limiter
from app.main import app
from app.services import password_recovery


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.next_id = 1

    def _matches(self, document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$gt" in expected and (actual is None or actual <= expected["$gt"]):
                    return False
                if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, _projection=None):
        return next((item for item in self.documents if self._matches(item, query)), None)

    def insert_one(self, document):
        saved = deepcopy(document)
        saved.setdefault("_id", f"memory-{self.next_id}")
        self.next_id += 1
        self.documents.append(saved)
        return SimpleNamespace(inserted_id=saved["_id"])

    def update_one(self, query, update):
        document = self.find_one(query)
        if not document:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for key, value in update.get("$set", {}).items():
            document[key] = value
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        return SimpleNamespace(matched_count=1, modified_count=1)

    def update_many(self, query, update):
        count = 0
        for document in self.documents:
            if self._matches(document, query):
                for key, value in update.get("$set", {}).items():
                    document[key] = value
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    def count_documents(self, query):
        return sum(self._matches(document, query) for document in self.documents)


class MockEmail:
    def __init__(self):
        self.sent_messages = []

    def send_otp(self, recovery_email, otp):
        self.sent_messages.append((recovery_email, otp))


class MemoryAudit:
    def __init__(self):
        self.events = []

    def insert_one(self, event):
        self.events.append(event)


def make_employee(
    employee_id="EMP-1",
    email="pushkar@aurelix.com",
    recovery_email="pushkar@gmail.com",
    recovery_email_verified=True,
    role="employee",
    password="OldPassword123!",
):
    return {
        "_id": employee_id,
        "employee_id": employee_id,
        "email": email,
        "recovery_email": recovery_email,
        "recovery_email_verified": recovery_email_verified,
        "full_name": "Test Employee",
        "department": "Operations",
        "role": role,
        "password_hash": security.hash_password(password),
        "auth_version": 0,
        "is_active": True,
    }


@pytest.fixture(autouse=True)
def clear_test_state():
    app.dependency_overrides.clear()
    limiter._storage.reset()
    yield
    app.dependency_overrides.clear()
    limiter._storage.reset()


def recovery_client(monkeypatch, employees=None):
    employee_collection = MemoryCollection(employees or [])
    sessions = MemoryCollection()
    audits = MemoryAudit()
    db = SimpleNamespace(employees=employee_collection, password_reset_sessions=sessions, audit_logs=audits)
    email = MockEmail()
    monkeypatch.setattr(auth_api, "get_db", lambda: db)
    monkeypatch.setattr(security, "get_db", lambda: db)
    monkeypatch.setattr(deps_api, "get_db", lambda: db)
    monkeypatch.setattr(password_recovery, "get_email_provider", lambda: email)
    app.dependency_overrides[security.require_admin] = lambda: {"sub": "ADM-1", "role": "admin"}
    return TestClient(app), db, email


def issue_otp(client, recovery_email="pushkar@gmail.com"):
    response = client.post("/api/auth/forgot-password/request", json={"recovery_email": recovery_email})
    return response, response.json().get("challenge_token")


def test_password_recovery_sends_otp_only_to_verified_recovery_email(monkeypatch, caplog):
    employee = make_employee()
    client, db, email = recovery_client(monkeypatch, [employee])
    response = client.post("/api/auth/forgot-password/request", json={"recovery_email": " Pushkar@Gmail.com "})

    assert response.status_code == 200
    assert response.json()["message"] == password_recovery.GENERIC_REQUEST_MESSAGE
    assert response.json()["masked_recovery_email"] == "p******@gmail.com"
    assert "otp" not in response.json()
    assert len(email.sent_messages) == 1
    destination, otp = email.sent_messages[0]
    assert destination == "pushkar@gmail.com"
    assert destination != employee["email"]
    assert re.fullmatch(r"\d{6}", otp)
    assert otp not in response.text
    session = db.password_reset_sessions.documents[0]
    assert session["otp_hash"] == password_recovery._digest(f"{session['challenge_hash']}:{otp}")
    assert session["otp_hash"] != otp
    assert session["expires_at"] - session["created_at"] == timedelta(minutes=5)
    assert otp not in caplog.text


@pytest.mark.parametrize("recovery_email", ["unknown@gmail.com", "pushkar@aurelix.com"])
def test_wrong_recovery_email_returns_generic_response_without_sending(monkeypatch, recovery_email):
    client, _db, email = recovery_client(monkeypatch, [make_employee()])

    response = client.post("/api/auth/forgot-password/request", json={"recovery_email": recovery_email})

    assert response.status_code == 200
    assert response.json()["message"] == password_recovery.GENERIC_REQUEST_MESSAGE
    assert email.sent_messages == []


def test_unverified_recovery_email_cannot_start_password_recovery(monkeypatch):
    employee = make_employee(recovery_email_verified=False)
    client, _db, email = recovery_client(monkeypatch, [employee])

    response, _challenge = issue_otp(client)

    assert response.status_code == 200
    assert response.json()["message"] == password_recovery.GENERIC_REQUEST_MESSAGE
    assert email.sent_messages == []


def test_legacy_employee_without_recovery_email_can_still_login(monkeypatch):
    employee = make_employee(recovery_email=None, recovery_email_verified=False)
    client, _db, email = recovery_client(monkeypatch, [employee])

    login = client.post("/api/auth/login", json={"email": employee["email"], "password": "OldPassword123!"})
    recovery, _challenge = issue_otp(client, employee["email"])

    assert login.status_code == 200
    assert login.json()["user"].get("recovery_email") is None
    assert recovery.status_code == 200
    assert recovery.json()["message"] == password_recovery.GENERIC_REQUEST_MESSAGE
    assert email.sent_messages == []


def test_recovery_email_verification_marks_only_matching_current_address_verified(monkeypatch):
    employee = make_employee(recovery_email="new.address@gmail.com", recovery_email_verified=False)
    client, db, email = recovery_client(monkeypatch, [employee])
    issued = password_recovery.request_recovery_email_verification(db, employee)
    otp = email.sent_messages[0][1]

    response = client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": otp})

    assert response.status_code == 200
    assert employee["recovery_email_verified"] is True
    assert response.json()["message"] == "Recovery email verified successfully."
    replay = client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": otp})
    assert replay.status_code == 400


def test_employee_creation_sends_verification_to_recovery_email_and_verifies_it(monkeypatch):
    administrator = make_employee(employee_id="ADM-1", email="admin@aurelix.com", recovery_email="admin@gmail.com", role="admin")
    client, db, email = recovery_client(monkeypatch, [administrator])
    monkeypatch.setattr(employees_api, "get_db", lambda: db)

    created = client.post("/api/employees", json={
        "employee_id": "EMP-2",
        "full_name": "New Employee",
        "email": "internal@aurelix.com",
        "recovery_email": "real.person@gmail.com",
        "department": "Operations",
        "role": "employee",
        "password": "TemporaryPassword123!",
    })
    new_employee = db.employees.find_one({"employee_id": "EMP-2"})

    assert created.status_code == 201
    assert created.json()["recovery_email_verified"] is False
    assert email.sent_messages[-1][0] == "real.person@gmail.com"
    assert email.sent_messages[-1][0] != "internal@aurelix.com"
    verified = client.post("/api/auth/recovery-email/verify", json={
        "challenge_token": created.json()["verification_challenge_token"],
        "otp": email.sent_messages[-1][1],
    })

    assert verified.status_code == 200
    assert new_employee["recovery_email_verified"] is True


def test_employee_recovery_verification_uses_configured_mock_email_provider(monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(email_service, "get_settings", lambda: SimpleNamespace(email_provider="mock"))
    monkeypatch.setattr(email_service, "SMTP", lambda *_args, **_kwargs: pytest.fail("Mock email provider attempted SMTP"))
    provider = email_service.get_email_provider()
    provider.sent_messages.clear()
    administrator = make_employee(employee_id="ADM-1", email="admin@aurelix.com", recovery_email="admin@gmail.com", role="admin")
    client, db, _unused_provider = recovery_client(monkeypatch, [administrator])
    monkeypatch.setattr(employees_api, "get_db", lambda: db)
    monkeypatch.setattr(password_recovery, "get_email_provider", email_service.get_email_provider)

    created = client.post("/api/employees", json={
        "employee_id": "EMP-2",
        "full_name": "New Employee",
        "email": "internal@aurelix.com",
        "recovery_email": "real.person@gmail.com",
        "department": "Operations",
        "role": "employee",
        "password": "TemporaryPassword123!",
    })

    assert created.status_code == 201
    assert created.json()["verification_delivery_mode"] == "mock"
    assert "no email was sent" in created.json()["verification_message"]
    assert provider.sent_messages[0][0] == "real.person@gmail.com"
    assert provider.sent_messages[0][0] != "internal@aurelix.com"
    verified = client.post("/api/auth/recovery-email/verify", json={
        "challenge_token": created.json()["verification_challenge_token"],
        "otp": provider.sent_messages[0][1],
    })

    assert verified.status_code == 200
    assert db.employees.find_one({"employee_id": "EMP-2"})["recovery_email_verified"] is True


def test_recovery_email_otp_expires_after_five_minutes(monkeypatch):
    employee = make_employee(recovery_email_verified=False)
    client, db, email = recovery_client(monkeypatch, [employee])
    issued = password_recovery.request_recovery_email_verification(db, employee)
    session = db.password_reset_sessions.documents[0]
    session["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": email.sent_messages[0][1]})

    assert response.status_code == 400
    assert employee["recovery_email_verified"] is False


def test_changing_recovery_email_invalidates_outstanding_verification_code(monkeypatch):
    employee = make_employee(recovery_email="first@gmail.com", recovery_email_verified=False)
    client, db, email = recovery_client(monkeypatch, [employee])
    issued = password_recovery.request_recovery_email_verification(db, employee)
    employee["recovery_email"] = "second@gmail.com"

    response = client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": email.sent_messages[0][1]})

    assert response.status_code == 400
    assert employee["recovery_email_verified"] is False


def test_recovery_email_verification_attempt_limit_invalidates_otp(monkeypatch):
    employee = make_employee(recovery_email_verified=False)
    client, db, email = recovery_client(monkeypatch, [employee])
    issued = password_recovery.request_recovery_email_verification(db, employee)
    correct_otp = email.sent_messages[0][1]
    wrong_otp = "000000" if correct_otp != "000000" else "000001"

    for _ in range(4):
        assert client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": wrong_otp}).status_code == 400
    fifth = client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": wrong_otp})
    after = client.post("/api/auth/recovery-email/verify", json={"challenge_token": issued["challenge_token"], "otp": correct_otp})

    assert fifth.status_code == 429
    assert fifth.json()["detail"] == password_recovery.TOO_MANY_ATTEMPTS_MESSAGE
    assert db.password_reset_sessions.documents[0]["attempts"] == 5
    assert after.status_code == 400
    assert employee["recovery_email_verified"] is False


def test_resend_invalidates_previous_recovery_email_otp(monkeypatch):
    employee = make_employee(recovery_email_verified=False)
    client, db, email = recovery_client(monkeypatch, [employee])
    previous = password_recovery.request_recovery_email_verification(db, employee)
    old_otp = email.sent_messages[-1][1]
    latest = password_recovery.request_recovery_email_verification(db, employee)
    new_otp = email.sent_messages[-1][1]

    old_result = client.post("/api/auth/recovery-email/verify", json={"challenge_token": previous["challenge_token"], "otp": old_otp})
    new_result = client.post("/api/auth/recovery-email/verify", json={"challenge_token": latest["challenge_token"], "otp": new_otp})

    assert old_result.status_code == 400
    assert new_result.status_code == 200


def test_password_reset_otp_is_single_use_and_yields_short_lived_reset_token(monkeypatch):
    employee = make_employee()
    client, db, email = recovery_client(monkeypatch, [employee])
    _response, challenge = issue_otp(client)
    otp = email.sent_messages[0][1]

    verified = client.post("/api/auth/forgot-password/verify", json={"challenge_token": challenge, "otp": otp})
    session = db.password_reset_sessions.documents[0]
    reset_token = verified.json()["reset_token"]

    assert verified.status_code == 200
    assert session["reset_expires_at"] - session["verified_at"] == timedelta(minutes=10)
    assert session["reset_token_hash"] == password_recovery._digest(reset_token)
    assert session["reset_token_hash"] != reset_token
    assert "reset_token" not in session or session["reset_token"] is None
    assert client.post("/api/auth/forgot-password/verify", json={"challenge_token": challenge, "otp": otp}).status_code == 400


def test_password_reset_changes_hash_audits_and_invalidates_old_jwt(monkeypatch):
    employee = make_employee()
    client, db, email = recovery_client(monkeypatch, [employee])
    old_token = security.create_access_token(employee["employee_id"], employee["role"], employee["auth_version"])
    _response, challenge = issue_otp(client)
    otp = email.sent_messages[0][1]
    verified = client.post("/api/auth/forgot-password/verify", json={"challenge_token": challenge, "otp": otp})
    reset_token = verified.json()["reset_token"]
    result = client.post("/api/auth/forgot-password/reset", json={"reset_token": reset_token, "new_password": "NewPassword456!"})

    old_login = client.post("/api/auth/login", json={"email": employee["email"], "password": "OldPassword123!"})
    new_login = client.post("/api/auth/login", json={"email": employee["email"], "password": "NewPassword456!"})
    old_token_result = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    new_token_result = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_login.json()['access_token']}"})

    assert result.status_code == 200
    assert security.verify_password("NewPassword456!", employee["password_hash"])
    assert employee["auth_version"] == 1
    assert old_login.status_code == 401
    assert old_login.json()["detail"] == "Incorrect email or password."
    assert new_login.status_code == 200
    assert old_token_result.status_code == 401
    assert new_token_result.status_code == 200
    reset_events = [event for event in db.audit_logs.events if event["event_type"] == "PASSWORD_RESET"]
    assert len(reset_events) == 1
    assert "password" not in str(reset_events[0]["metadata"]).lower()
    assert "otp" not in str(reset_events[0]["metadata"]).lower()


def test_expired_reset_token_and_invalid_token_cannot_reset(monkeypatch):
    employee = make_employee()
    client, db, email = recovery_client(monkeypatch, [employee])
    _response, challenge = issue_otp(client)
    verified = client.post("/api/auth/forgot-password/verify", json={"challenge_token": challenge, "otp": email.sent_messages[0][1]})
    db.password_reset_sessions.documents[0]["reset_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    expired = client.post("/api/auth/forgot-password/reset", json={"reset_token": verified.json()["reset_token"], "new_password": "NewPassword456!"})
    invalid = client.post("/api/auth/forgot-password/reset", json={"reset_token": "x" * 40, "new_password": "NewPassword456!"})
    short = client.post("/api/auth/forgot-password/reset", json={"reset_token": "x" * 40, "new_password": "short"})

    assert expired.status_code == 400
    assert expired.json()["detail"] == password_recovery.EXPIRED_SESSION_MESSAGE
    assert invalid.status_code == 400
    assert short.status_code == 422
    assert security.verify_password("OldPassword123!", employee["password_hash"])


def test_repeated_recovery_requests_are_allowed_and_invalidate_previous_otp(monkeypatch):
    client, db, email = recovery_client(monkeypatch, [make_employee()])
    responses = [issue_otp(client) for _ in range(5)]

    assert [response.status_code for response, _challenge in responses] == [200] * 5
    assert all(response.json()["message"] == password_recovery.GENERIC_REQUEST_MESSAGE for response, _challenge in responses)
    assert len(email.sent_messages) == 5
    assert all(re.fullmatch(r"\d{6}", otp) for _destination, otp in email.sent_messages)

    previous_challenge = responses[0][1]
    latest_challenge = responses[-1][1]
    previous_otp = email.sent_messages[0][1]
    latest_otp = email.sent_messages[-1][1]
    previous_result = client.post("/api/auth/forgot-password/verify", json={"challenge_token": previous_challenge, "otp": previous_otp})
    latest_result = client.post("/api/auth/forgot-password/verify", json={"challenge_token": latest_challenge, "otp": latest_otp})

    assert previous_result.status_code == 400
    assert latest_result.status_code == 200
    assert db.password_reset_sessions.documents[0]["used"] is True
    assert "request_key" not in db.password_reset_sessions.documents[-1]


def test_login_email_does_not_authorize_recovery_and_recovery_email_is_not_public(monkeypatch):
    employee = make_employee()
    employee["phone_number"] = "+919876543210"
    client, _db, email = recovery_client(monkeypatch, [employee])

    login = client.post("/api/auth/login", json={"email": employee["email"], "password": "OldPassword123!"})
    invalid_payload = client.post("/api/auth/forgot-password/request", json={"email": employee["email"]})

    assert login.status_code == 200
    assert "recovery_email" not in login.json()["user"]
    assert "phone_number" not in login.json()["user"]
    assert invalid_payload.status_code == 422
    assert email.sent_messages == []


def test_mock_email_provider_captures_without_network_and_smtp_credentials_are_backend_only(monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(email_service, "get_settings", lambda: SimpleNamespace(email_provider="mock"))
    monkeypatch.setattr(email_service, "SMTP", lambda *_args, **_kwargs: pytest.fail("Mock email provider attempted SMTP"))
    provider = email_service.get_email_provider()
    provider.sent_messages.clear()
    provider.send_otp("person@gmail.com", "483921")
    assert provider.sent_messages == [("person@gmail.com", "483921")]
    assert email_service.get_email_provider() is provider
    frontend = open("../frontend/src/App.jsx", encoding="utf-8").read()
    assert "SMTP_PASSWORD" not in frontend
    assert "SMS_PROVIDER" not in frontend


def test_smtp_email_message_uses_configured_sender_tls_and_required_copy(monkeypatch):
    from app.services import email_service

    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            captured.update(host=host, port=port, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            captured["tls"] = True

        def login(self, username, password):
            captured["credentials"] = (username, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(email_service, "SMTP", FakeSMTP)
    provider = email_service.SMTPEmailProvider("smtp.gmail.com", 587, "sender@gmail.com", "secret", "sender@gmail.com", "Aurelix Smart Attendance", True)
    provider.send_otp("person@gmail.com", "483921")

    message = captured["message"]
    body = message.get_content()
    assert captured["host"] == "smtp.gmail.com"
    assert captured["port"] == 587
    assert captured["tls"] is True
    assert captured["credentials"] == ("sender@gmail.com", "secret")
    assert message["To"] == "person@gmail.com"
    assert message["Subject"] == "Aurelix Smart Attendance - Password Reset Code"
    assert "483921" in body
    assert "This code expires in 5 minutes." in body
    assert "If you did not request a password reset, you can ignore this email." in body


def test_smtp_settings_load_all_expected_environment_variables(monkeypatch):
    from app.core.config import Settings

    values = {
        "EMAIL_PROVIDER": "smtp",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "sender@gmail.com",
        "SMTP_PASSWORD": "do-not-log-this",
        "SMTP_FROM_EMAIL": "sender@gmail.com",
        "SMTP_FROM_NAME": "Aurelix Smart Attendance",
        "SMTP_USE_TLS": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)

    assert settings.email_provider == "smtp"
    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.smtp_port == 587
    assert settings.smtp_username == "sender@gmail.com"
    assert settings.smtp_password == "do-not-log-this"
    assert settings.smtp_from_email == "sender@gmail.com"
    assert settings.smtp_from_name == "Aurelix Smart Attendance"
    assert settings.smtp_use_tls is True


def test_missing_smtp_configuration_returns_safe_server_error(monkeypatch, caplog):
    from app.services import email_service

    secret = "smtp-secret-must-not-leak"
    settings = SimpleNamespace(
        email_provider="smtp",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_username="sender@gmail.com",
        smtp_password=secret,
        smtp_from_email=None,
        smtp_from_name="Aurelix Smart Attendance",
        smtp_use_tls=True,
    )
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)
    monkeypatch.setattr(email_service, "SMTP", lambda *_args, **_kwargs: pytest.fail("SMTP connected without a complete configuration"))
    employee = make_employee()
    client, _db, _mock = recovery_client(monkeypatch, [employee])
    monkeypatch.setattr(password_recovery, "get_email_provider", email_service.get_email_provider)

    response = client.post("/api/auth/forgot-password/request", json={"recovery_email": employee["recovery_email"]})

    assert response.status_code == 503
    assert response.json()["detail"] == "Verification email delivery failed. Please try again later."
    assert secret not in response.text
    assert secret not in caplog.text
    assert "SMTP_FROM_EMAIL" in caplog.text


def test_gmail_sender_must_match_authenticated_account(monkeypatch, caplog):
    from app.services import email_service

    settings = SimpleNamespace(
        email_provider="smtp",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_username="sender@gmail.com",
        smtp_password="never-log-this-password",
        smtp_from_email="different@gmail.com",
        smtp_from_name="Aurelix Smart Attendance",
        smtp_use_tls=True,
    )
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="SMTP_FROM_EMAIL must match SMTP_USERNAME for Gmail"):
        email_service.get_email_provider()
    assert "never-log-this-password" not in caplog.text


def test_smtp_failure_logs_phase_but_redacts_credentials_otp_and_emails(monkeypatch, caplog):
    from app.services import email_service

    password = "password-marker"
    otp = "483921"
    sender = "sender@gmail.com"
    recipient = "person@gmail.com"

    class FailedAuthSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, _username, _password):
            raise RuntimeError(f"auth failed {sender} {recipient} {password} OTP={otp}")

    monkeypatch.setattr(email_service, "SMTP", FailedAuthSMTP)
    provider = email_service.SMTPEmailProvider("smtp.gmail.com", 587, sender, password, sender, "Aurelix Smart Attendance", True)

    with pytest.raises(RuntimeError):
        provider.send_otp(recipient, otp)

    logs = caplog.text
    assert "SMTP authentication failed" in logs
    assert "smtp.gmail.com" in logs
    assert "587" in logs
    assert "p******@gmail.com" in logs
    assert all(secret not in logs for secret in (password, otp, sender, recipient))
