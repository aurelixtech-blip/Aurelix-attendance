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


class AttendanceNoteTtlIndexMigrationError(RuntimeError):
    pass


def remove_attendance_note_ttl_index(db) -> None:
    try:
        if "attendance_notes" not in db.list_collection_names(filter={"name": "attendance_notes"}):
            return

        collection = db.attendance_notes

        def is_note_expiry_index(index: dict) -> bool:
            return dict(index.get("key", {})) == {"expires_at": ASCENDING} and "expireAfterSeconds" in index

        for index in collection.list_indexes():
            if is_note_expiry_index(index):
                collection.drop_index(index["name"])

        if any(is_note_expiry_index(index) for index in collection.list_indexes()):
            raise RuntimeError("Attendance-note expiry TTL index still exists after removal")
    except Exception as exc:
        raise AttendanceNoteTtlIndexMigrationError(
            "Could not safely remove the attendance-note expiry TTL index"
        ) from exc


def init_indexes() -> None:
    db = get_db()
    remove_attendance_note_ttl_index(db)
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
    db.attendance_notes.create_index([("employee_id", ASCENDING), ("date", ASCENDING)], unique=True)
    db.audit_logs.create_index([("created_at", DESCENDING)])
    db.fs.files.create_index("metadata.event")
