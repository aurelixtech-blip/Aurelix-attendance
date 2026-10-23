from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import current_employee
from app.core.security import require_admin
from app.db.mongodb import get_db
from app.models.attendance_note import attendance_note_document, public_attendance_note
from app.schemas.attendance_note import AttendanceNoteRequest

router = APIRouter(prefix="/api/attendance/notes", tags=["attendance-notes"])
admin_router = APIRouter(prefix="/api/attendance/admin", tags=["attendance-notes"])


def _validated_date(value: str) -> str:
    try:
        if datetime.strptime(value, "%Y-%m-%d").date().isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Attendance date must use YYYY-MM-DD format") from exc
    return value


def _require_employee(employee: dict) -> str:
    if employee.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access required")
    return employee["employee_id"]


def _require_own_attendance(employee_id: str, attendance_date: str) -> None:
    if not get_db().attendance.find_one({"employee_id": employee_id, "date": attendance_date}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Attendance record not found")


def _note_query(employee_id: str, attendance_date: str) -> dict:
    return {"employee_id": employee_id, "date": attendance_date}


def _note_response(document: dict) -> dict:
    return public_attendance_note(document)


def _mark_private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@admin_router.get("/{attendance_id}/note")
def admin_attendance_note(attendance_id: str, response: Response, _claims: dict = Depends(require_admin)):
    _mark_private(response)
    db = get_db()
    attendance = db.attendance.find_one({"attendance_id": attendance_id})
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    note = db.attendance_notes.find_one(
        _note_query(attendance["employee_id"], attendance["date"]),
        {"_id": 0},
    )
    if not note or not isinstance(note.get("content"), str) or not note["content"].strip():
        raise HTTPException(status_code=404, detail="No note found")
    employee = db.employees.find_one({"employee_id": attendance["employee_id"]}, {"full_name": 1})
    return {
        "content": note["content"],
        "employee_name": (employee or {}).get("full_name") or attendance.get("user_name") or attendance["employee_id"],
        "date": attendance["date"],
        "created_at": note["created_at"],
    }


@router.get("")
def list_my_attendance_notes(response: Response, employee: dict = Depends(current_employee)):
    _mark_private(response)
    employee_id = _require_employee(employee)
    notes = get_db().attendance_notes.find(
        {"employee_id": employee_id},
        {"_id": 0},
    ).sort("date", -1)
    return [_note_response(note) for note in notes if isinstance(note.get("content"), str) and note["content"].strip()]


@router.get("/{attendance_date}")
def get_my_attendance_note(attendance_date: str, response: Response, employee: dict = Depends(current_employee)):
    _mark_private(response)
    employee_id = _require_employee(employee)
    attendance_date = _validated_date(attendance_date)
    _require_own_attendance(employee_id, attendance_date)
    note = get_db().attendance_notes.find_one(
        _note_query(employee_id, attendance_date),
        {"_id": 0},
    )
    if not note or not isinstance(note.get("content"), str) or not note["content"].strip():
        raise HTTPException(status_code=404, detail="No note found")
    return _note_response(note)


@router.post("/{attendance_date}")
def create_my_attendance_note(
    attendance_date: str,
    payload: AttendanceNoteRequest,
    response: Response,
    employee: dict = Depends(current_employee),
):
    _mark_private(response)
    employee_id = _require_employee(employee)
    attendance_date = _validated_date(attendance_date)
    _require_own_attendance(employee_id, attendance_date)
    db = get_db()
    identity = {"employee_id": employee_id, "date": attendance_date}
    if db.attendance_notes.find_one(identity, {"_id": 1}):
        raise HTTPException(status_code=409, detail="A note already exists for this attendance date")

    note = attendance_note_document(employee_id, attendance_date, payload.content, datetime.now(timezone.utc))
    try:
        db.attendance_notes.insert_one(note)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A note already exists for this attendance date")
    return _note_response(note)


@router.patch("/{attendance_date}")
def edit_my_attendance_note(
    attendance_date: str,
    payload: AttendanceNoteRequest,
    response: Response,
    employee: dict = Depends(current_employee),
):
    _mark_private(response)
    employee_id = _require_employee(employee)
    attendance_date = _validated_date(attendance_date)
    _require_own_attendance(employee_id, attendance_date)
    db = get_db()
    now = datetime.now(timezone.utc)
    note_query = _note_query(employee_id, attendance_date)
    result = db.attendance_notes.update_one(
        note_query,
        {"$set": {"content": payload.content, "updated_at": now}},
    )
    if result.matched_count:
        return _note_response(db.attendance_notes.find_one(note_query, {"_id": 0}))
    raise HTTPException(status_code=404, detail="No note found")


@router.delete("/{attendance_date}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_attendance_note(
    attendance_date: str,
    response: Response,
    employee: dict = Depends(current_employee),
):
    _mark_private(response)
    employee_id = _require_employee(employee)
    attendance_date = _validated_date(attendance_date)
    _require_own_attendance(employee_id, attendance_date)
    result = get_db().attendance_notes.delete_one({"employee_id": employee_id, "date": attendance_date})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="No note found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
