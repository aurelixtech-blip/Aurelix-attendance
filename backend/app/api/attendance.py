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
from app.schemas.attendance import AttendanceResponse, VerificationRequest
from app.services.attendance_service import verify_and_record
from app.services.photo_service import PENDING_ATTENDANCE_ID, PhotoExpiredError, bind_photo_to_attendance, delete_photo, open_photo, prepare_photo, store_photo

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
                {"$set": {"check_out_time": None, "check_out_location": None, "final_status": "ABSENT", "updated_at": datetime.now(timezone.utc)}},
            )
            item["check_out_time"] = None
            item["check_out_location"] = None
            item["final_status"] = "ABSENT"
        records.append(public_attendance(item))
    return records

@router.delete("/mine/{attendance_id}")
def undo_mine(attendance_id: str, action: str = Query("record", pattern=r"^(check_in|check_out|record)$"), employee: dict = Depends(current_employee)):
    db = get_db()
    record = db.attendance.find_one({"attendance_id": attendance_id, "employee_id": employee["employee_id"]})
    if not record:
        raise HTTPException(status_code=404, detail="Your attendance record was not found")
    if action == "record":
        delete_photo(record.get("check_in_photo_reference"))
        delete_photo(record.get("check_out_photo_reference"))
        db.attendance.delete_one({"_id": record["_id"]})
    else:
        changes = {"updated_at": datetime.now(timezone.utc)}
        if action == "check_in":
            delete_photo(record.get("check_in_photo_reference"))
            delete_photo(record.get("check_out_photo_reference"))
            changes.update({"check_in_time": None, "check_in_location": None, "check_out_time": None, "check_out_location": None, "final_status": "ABSENT"})
            changes.update({"check_in_photo_reference": None, "check_out_photo_reference": None})
        elif not record.get("check_out_time"):
            raise HTTPException(status_code=400, detail="No check-out time is recorded")
        else:
            delete_photo(record.get("check_out_photo_reference"))
            changes.update({"check_out_time": None, "check_out_location": None, "final_status": "PRESENT"})
            changes.update({"check_out_photo_reference": None})
        db.attendance.update_one({"_id": record["_id"]}, {"$set": changes})
    db.audit_logs.insert_one(audit_event("EMPLOYEE_ATTENDANCE_UNDO", employee["employee_id"], "ACCEPTED", {"attendance_id": attendance_id, "date": record["date"], "action": action}))
    return {"message": f"Your {action.replace('_', '-')} was undone", "attendance_id": attendance_id, "action": action}

@router.get("/admin")
def all_attendance(_claims: dict = Depends(require_admin), date: str | None = Query(default=None), employee_id: str | None = Query(default=None), status: str | None = Query(default=None)):
    query = {key: value for key, value in (("date", date), ("employee_id", employee_id), ("final_status", status)) if value}
    records = []
    db = get_db()
    for item in db.attendance.find(query).sort("date", -1).limit(500):
        record = public_attendance(item)
        employee = db.employees.find_one({"employee_id": item["employee_id"]}, {"password_hash": 0})
        record["employee"] = (
            {"full_name": employee.get("full_name"), "department": employee.get("department")}
            if employee
            else {"full_name": record.get("user_name") or record.get("employee_id"), "department": ""}
        )
        records.append(record)
    if date and not employee_id:
        existing = {item["employee_id"] for item in records}
        for employee in db.employees.find({"role": "employee", "is_active": True}, {"password_hash": 0}).sort("full_name", 1):
            if employee["employee_id"] in existing:
                continue
            records.append({"attendance_id": f"absent-{employee['employee_id']}-{date}", "employee_id": employee["employee_id"], "user_name": employee.get("full_name"), "date": date, "check_in_time": None, "check_in_location": None, "check_in_photo_available": False, "check_out_time": None, "check_out_location": None, "check_out_photo_available": False, "final_status": "ABSENT", "employee": {"full_name": employee["full_name"], "department": employee.get("department", "")}})
        records.sort(key=lambda item: (item.get("employee") or {}).get("full_name", ""))
    return records

@router.get("/admin/month")
def attendance_month(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), _claims: dict = Depends(require_admin)):
    year, month_number = month.split("-", 1)
    records = get_db().attendance.find({"date": {"$regex": f"^{year}-{month_number}-"}}, {"_id": 0, "employee_id": 1, "date": 1, "final_status": 1})
    return list(records)

@router.delete("/admin/{attendance_id}")
def clear_attendance(attendance_id: str, claims: dict = Depends(require_admin)):
    db = get_db()
    record = db.attendance.find_one({"attendance_id": attendance_id})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    delete_photo(record.get("check_in_photo_reference"))
    delete_photo(record.get("check_out_photo_reference"))
    db.attendance.delete_one({"_id": record["_id"]})
    db.audit_logs.insert_one(audit_event("ATTENDANCE_RECORD_CLEARED", claims["sub"], "ACCEPTED", {"attendance_id": attendance_id, "employee_id": record["employee_id"], "date": record["date"]}))
    return {"message": "Attendance record cleared", "attendance_id": attendance_id}


@router.get("/admin/{attendance_id}/photo")
def attendance_photo(attendance_id: str, event: str = Query(..., pattern=r"^(check_in|check_out)$"), _claims: dict = Depends(require_admin)):
    record = get_db().attendance.find_one({"attendance_id": attendance_id})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    reference = record.get(f"{event}_photo_reference")
    if not reference:
        raise HTTPException(status_code=404, detail="Photo expired or unavailable")
    try:
        stream = open_photo(reference)
        return Response(content=stream.read(), media_type=reference.get("content_type", "image/jpeg"), headers={"Cache-Control": "private, no-store"})
    except PhotoExpiredError as exc:
        raise HTTPException(status_code=410, detail="Photo expired or unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Photo expired or unavailable") from exc

@router.get("/dashboard")
def dashboard(_claims: dict = Depends(require_admin)):
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    db = get_db()
    total = db.employees.count_documents({"role": "employee", "is_active": True})
    present = db.attendance.count_documents({"date": today, "final_status": "PRESENT"})
    return {"total_employees": total, "present_today": present, "absent_today": max(total - present, 0), "late_today": 0, "rejected_attempts": 0}
