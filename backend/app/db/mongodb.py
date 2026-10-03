from datetime import timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from app.core.config import get_settings

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(get_settings().mongodb_uri, serverSelectionTimeoutMS=3000, tz_aware=True, tzinfo=timezone.utc)
    return _client


def get_db():
    return get_client()[get_settings().database_name]


def init_indexes() -> None:
    db = get_db()
    db.employees.create_index("employee_id", unique=True)
    db.employees.create_index("email", unique=True)
    db.employees.create_index("recovery_email", unique=True, partialFilterExpression={"is_active": True, "recovery_email_verified": True, "recovery_email": {"$type": "string"}})
    db.password_reset_sessions.create_index("cleanup_at", expireAfterSeconds=0)
    db.attendance.create_index([("employee_id", ASCENDING), ("date", DESCENDING)])
    db.attendance.create_index([("employee_id", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_reminders.create_index(
        [("employee_id", ASCENDING), ("date", ASCENDING), ("reminder_type", ASCENDING)],
        unique=True,
    )
    db.audit_logs.create_index([("created_at", DESCENDING)])
    db.fs.files.create_index("metadata.event")
