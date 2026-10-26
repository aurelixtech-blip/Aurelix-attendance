import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from app.api.deps import current_employee
from app.core.security import require_admin
from app.db.mongodb import get_db
from app.models.attendance import public_attendance
from app.models.audit import audit_event
from app.models.employee import active_employee_accounts
from app.schemas.attendance import AttendanceResponse, VerificationRequest
from app.services.attendance_service import verify_and_record
from app.services.photo_service import PENDING_ATTENDANCE_ID, PhotoUnavailableError, bind_photo_to_attendance, delete_photo, open_photo, prepare_photo, store_photo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/attendance", tags=["attendance"])

@router.post("/verify", response_model=AttendanceResponse)
def verify(_payload: VerificationRequest, _employee: dict = Depends(current_employee)):
    raise HTTPException(status_code=400, detail="An attendance photo is required. Use /api/attendance/verify-with-photo.")


@router.post("/verify-with-photo", response_model=AttendanceResponse)
async def verify_with_photo(
    action: str = Form(...),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    accuracy: float | None = Form(default=None),
    source: str = Form(default="browser"),
    photo: UploadFile = File(...),
    employee: dict = Depends(current_employee),
):
    if action not in {"check_in", "check_out"}:
        raise HTTPException(status_code=422, detail="Invalid attendance action")
    data, content_type = await prepare_photo(photo)
    reference = None
    try:
        reference = store_photo(data, content_type, employee["employee_id"], PENDING_ATTENDANCE_ID, action)
        payload = VerificationRequest(action=action, latitude=latitude, longitude=longitude, accuracy=accuracy, source=source)
        result = verify_and_record(employee, payload, reference)
        if not result.get("success"):
            delete_photo(reference)
            return result
        try:
            bind_photo_to_attendance(reference, result.get("attendance_id"))
        except Exception:
            logger.exception("Attendance recorded but GridFS photo metadata could not be updated")
        return result
    except Exception:
        delete_photo(reference)
        raise

@router.get("/mine")
def mine(employee: dict = Depends(current_employee)):
    db = get_db()
    records = []
    for item in db.attendance.find({"employee_id": employee["employee_id"]}).sort("date", -1).limit(90):
        if not item.get("check_in_time") and item.get("check_out_time"):
            db.attendance.update_one(
                {"_id": item["_id"]},
                {"$set": {"check_out_time": None, "check_out_location": None, "check_out_photo_reference": None, "final_status": "ABSENT", "updated_at": datetime.now(timezone.utc)}},
            )
            item["check_out_time"] = None
            item["check_out_location"] = None
            item["check_out_photo_reference"] = None
            item["final_status"] = "ABSENT"
        records.append(public_attendance(item))
    return records

def _undo_attendance_event(attendance_id: str, event: str, claims: dict, employee_id: str | None = None) -> dict:
    db = get_db()
    record_query = {"attendance_id": attendance_id}
    if employee_id is not None:
        record_query["employee_id"] = employee_id
    record = db.attendance.find_one(record_query)
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    now = datetime.now(timezone.utc)
    if event == "check_in":
        if not record.get("check_in_time"):
            raise HTTPException(status_code=409, detail="No check-in time is recorded")
        if record.get("check_out_time"):
            raise HTTPException(status_code=409, detail="Undo Check Out first before undoing Check In.")
        changes = {
            "check_in_time": None,
            "check_in_location": None,
            "check_in_photo_reference": None,
            "check_out_location": None,
            "check_out_photo_reference": None,
            "final_status": "ABSENT",
            "updated_at": now,
        }
        guard = {"check_in_time": {"$ne": None}, "check_out_time": None}
        event_type = "UNDO_CHECK_IN"
    else:
        if not record.get("check_out_time"):
            raise HTTPException(status_code=409, detail="No check-out time is recorded")
        changes = {
            "check_out_time": None,
            "check_out_location": None,
            "check_out_photo_reference": None,
            "final_status": "PRESENT",
            "updated_at": now,
        }
        guard = {"check_out_time": {"$ne": None}, "check_in_time": {"$ne": None}}
        event_type = "UNDO_CHECK_OUT"

    update_query = {"_id": record["_id"], **guard}
    if employee_id is not None:
        update_query["employee_id"] = employee_id
    result = db.attendance.update_one(update_query, {"$set": changes})
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="Attendance changed while the undo was being applied. Refresh and try again.")

    db.audit_logs.insert_one(
        audit_event(
            event_type,
            claims["sub"],
            "ACCEPTED",
            {"attendance_id": attendance_id, "employee_id": record["employee_id"], "date": record["date"]},
        )
    )
    return {"message": f"Undo {event.replace('_', ' ').title()} completed", "attendance_id": attendance_id, "action": event}


def _undo_own_attendance_event(attendance_id: str, event: str, employee: dict) -> dict:
    if employee.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access required")
    employee_id = employee["employee_id"]
    return _undo_attendance_event(attendance_id, event, {"sub": employee_id}, employee_id=employee_id)


@router.post("/mine/{attendance_id}/undo-check-in")
def undo_my_check_in(attendance_id: str, employee: dict = Depends(current_employee)):
    return _undo_own_attendance_event(attendance_id, "check_in", employee)


@router.post("/mine/{attendance_id}/undo-check-out")
def undo_my_check_out(attendance_id: str, employee: dict = Depends(current_employee)):
    return _undo_own_attendance_event(attendance_id, "check_out", employee)


@router.get("/admin")
def all_attendance(_claims: dict = Depends(require_admin), date: str | None = Query(default=None), employee_id: str | None = Query(default=None), status: str | None = Query(default=None)):
    query = {key: value for key, value in (("date", date), ("employee_id", employee_id)) if value}
    records = []
    db = get_db()
    employees = active_employee_accounts(db.employees, {"password_hash": 0})
    employees.sort(key=lambda employee: employee.get("full_name", ""))
    employee_by_id = {employee["employee_id"]: employee for employee in employees}
    if employee_id:
        if employee_id not in employee_by_id:
            return []
    else:
        query["employee_id"] = {"$in": list(employee_by_id)}
    if status and not date:
        query["final_status"] = status
    attendance_records = db.attendance.find(query).sort("date", -1)
    if not date:
        attendance_records = attendance_records.limit(500)
    for item in attendance_records:
        employee = employee_by_id.get(item["employee_id"])
        if not employee:
            continue
        record = public_attendance(item)
        record["employee"] = {"full_name": employee.get("full_name"), "department": employee.get("department")}
        records.append(record)
    if date and not employee_id:
        existing = {item["employee_id"] for item in records}
        for employee in employees:
            if employee["employee_id"] in existing:
                continue
            records.append({"attendance_id": f"absent-{employee['employee_id']}-{date}", "employee_id": employee["employee_id"], "user_name": employee.get("full_name"), "date": date, "check_in_time": None, "check_in_location": None, "check_in_photo_available": False, "check_out_time": None, "check_out_location": None, "check_out_photo_available": False, "final_status": "ABSENT", "employee": {"full_name": employee["full_name"], "department": employee.get("department", "")}})
        records.sort(key=lambda item: (item.get("employee") or {}).get("full_name", ""))
    return [record for record in records if not status or record.get("final_status") == status]

@router.get("/admin/month")
def attendance_month(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), _claims: dict = Depends(require_admin)):
    year, month_number = month.split("-", 1)
    db = get_db()
    employee_ids = [
        employee["employee_id"]
        for employee in active_employee_accounts(db.employees, {"employee_id": 1})
    ]
    records = db.attendance.find(
        {"date": {"$regex": f"^{year}-{month_number}-"}, "employee_id": {"$in": employee_ids}},
        {"_id": 0, "employee_id": 1, "date": 1, "final_status": 1},
    )
    return list(records)

@router.post("/admin/{attendance_id}/undo-check-in")
def undo_check_in(attendance_id: str, claims: dict = Depends(require_admin)):
    return _undo_attendance_event(attendance_id, "check_in", claims)

@router.post("/admin/{attendance_id}/undo-check-out")
def undo_check_out(attendance_id: str, claims: dict = Depends(require_admin)):
    return _undo_attendance_event(attendance_id, "check_out", claims)

@router.get("/admin/{attendance_id}/photo")
def attendance_photo(attendance_id: str, event: str = Query(..., pattern=r"^(check_in|check_out)$"), _claims: dict = Depends(require_admin)):
    record = get_db().attendance.find_one({"attendance_id": attendance_id})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    reference = record.get(f"{event}_photo_reference")
    if not reference:
        raise HTTPException(status_code=404, detail="Photo unavailable")
    try:
        stream = open_photo(reference)
        return Response(content=stream.read(), media_type=reference.get("content_type", "image/jpeg"), headers={"Cache-Control": "private, no-store"})
    except PhotoUnavailableError as exc:
        raise HTTPException(status_code=404, detail="Photo unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Photo unavailable") from exc

@router.get("/dashboard")
def dashboard(_claims: dict = Depends(require_admin)):
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    db = get_db()
    employee_ids = {
        employee["employee_id"]
        for employee in active_employee_accounts(db.employees, {"employee_id": 1})
    }
    total = len(employee_ids)
    present_ids = {
        record["employee_id"]
        for record in db.attendance.find({
            "date": today,
            "final_status": "PRESENT",
            "employee_id": {"$in": list(employee_ids)},
        }, {"employee_id": 1})
    }
    present = len(present_ids)
    return {"total_employees": total, "present_today": present, "absent_today": max(total - present, 0), "late_today": 0, "rejected_attempts": 0}
