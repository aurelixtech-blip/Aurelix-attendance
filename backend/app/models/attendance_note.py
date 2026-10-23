from datetime import datetime, timezone


def attendance_note_document(employee_id: str, attendance_date: str, content: str, now: datetime | None = None) -> dict:
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return {
        "employee_id": employee_id,
        "date": attendance_date,
        "content": content,
        "created_at": created_at,
        "updated_at": created_at,
    }


def public_attendance_note(document: dict) -> dict:
    return {
        "date": document["date"],
        "content": document["content"],
        "created_at": document["created_at"],
    }
