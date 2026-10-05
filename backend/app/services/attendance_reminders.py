import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import get_db
from app.services.email_service import get_email_provider

logger = logging.getLogger(__name__)
KOLKATA_TIME_ZONE = ZoneInfo("Asia/Kolkata")
REMINDER_LEASE = timedelta(minutes=5)
REMINDER_TYPES = {"check_in", "check_out"}


def _usable_recovery_email(employee: dict) -> str | None:
    if employee.get("recovery_email_verified") is not True:
        return None
    address = employee.get("recovery_email")
    if not isinstance(address, str):
        return None
    address = address.strip().lower()
    try:
        return validate_email(address, check_deliverability=False).normalized
    except EmailNotValidError:
        return None


def _claim_reminder(db, identity: dict, now: datetime, claim_id: str) -> bool:
    reservation = {
        **identity,
        "status": "sending",
        "claim_id": claim_id,
        "lease_expires_at": now + REMINDER_LEASE,
        "created_at": now,
    }
    try:
        db.attendance_reminders.insert_one(reservation)
        return True
    except DuplicateKeyError:
        existing = db.attendance_reminders.find_one(identity)
        if not existing or existing.get("status") == "sent":
            return False
        lease_expires_at = existing.get("lease_expires_at")
        if lease_expires_at is None or lease_expires_at > now:
            return False
        result = db.attendance_reminders.update_one(
            {**identity, "status": "sending", "claim_id": existing.get("claim_id")},
            {"$set": {"claim_id": claim_id, "lease_expires_at": now + REMINDER_LEASE, "created_at": now}},
        )
        return result.matched_count == 1


def run_attendance_reminder(reminder_type: str, db=None, email_provider=None, now: datetime | None = None) -> dict:
    if reminder_type not in REMINDER_TYPES:
        raise ValueError("Unsupported attendance reminder type")
    db = db if db is not None else get_db()
    email_provider = email_provider if email_provider is not None else get_email_provider()
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    today = current_time.astimezone(KOLKATA_TIME_ZONE).date().isoformat()
    summary = {"success": True, "eligible": 0, "sent": 0, "skipped": 0, "failed": 0}

    for employee in db.employees.find({"role": "employee", "is_active": True}):
        recovery_email = _usable_recovery_email(employee)
        if not recovery_email:
            continue
        attendance = db.attendance.find_one({"employee_id": employee.get("employee_id"), "date": today})
        checked_in = bool(attendance and attendance.get("check_in_time"))
        checked_out = bool(attendance and attendance.get("check_out_time"))
        if reminder_type == "check_in" and checked_in:
            continue
        if reminder_type == "check_out" and (not checked_in or checked_out):
            continue

        summary["eligible"] += 1
        identity = {
            "employee_id": employee.get("employee_id"),
            "date": today,
            "reminder_type": reminder_type,
        }
        claim_id = str(uuid4())
        if not _claim_reminder(db, identity, current_time, claim_id):
            summary["skipped"] += 1
            continue

        try:
            email_provider.send_attendance_reminder(
                recovery_email,
                employee.get("full_name") or employee.get("employee_id") or "there",
                reminder_type,
            )
        except Exception as exc:
            db.attendance_reminders.delete_one({**identity, "claim_id": claim_id})
            summary["failed"] += 1
            logger.error(
                "Attendance reminder delivery failed type=%s employee_id=%s exception=%s",
                reminder_type,
                employee.get("employee_id"),
                type(exc).__name__,
            )
            continue

        try:
            result = db.attendance_reminders.update_one(
                {**identity, "claim_id": claim_id, "status": "sending"},
                {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc)}},
            )
            if result.matched_count != 1:
                raise RuntimeError("Reminder claim was not available to mark sent")
            summary["sent"] += 1
        except Exception as exc:
            summary["failed"] += 1
            logger.error(
                "Attendance reminder sent but idempotency record update failed type=%s employee_id=%s exception=%s",
                reminder_type,
                employee.get("employee_id"),
                type(exc).__name__,
            )

    return summary


def send_attendance_reminder_samples(recipient: str, email_provider) -> dict:
    try:
        recipient = validate_email(recipient.strip(), check_deliverability=False).normalized
    except (AttributeError, EmailNotValidError) as exc:
        raise ValueError("A valid test email address is required") from exc

    summary = {"success": True, "attempted": 2, "sent": 0, "failed": 0}
    for reminder_type in ("check_in", "check_out"):
        try:
            email_provider.send_attendance_reminder(recipient, "Test Employee", reminder_type)
            summary["sent"] += 1
        except Exception as exc:
            summary["success"] = False
            summary["failed"] += 1
            logger.error(
                "Attendance reminder sample delivery failed type=%s exception=%s",
                reminder_type,
                type(exc).__name__,
            )
    return summary
