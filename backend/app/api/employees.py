from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError
from app.core.security import require_admin, hash_password
from app.db.mongodb import get_db
from app.models.employee import admin_employee, employee_document
from app.models.audit import audit_event
from app.schemas.employee import EmployeeCreate, EmployeeUpdate
from app.services.password_recovery import request_recovery_email_verification

router = APIRouter(prefix="/api/employees", tags=["employees"])

@router.get("")
def list_employees(_claims: dict = Depends(require_admin)):
    return [admin_employee(item) for item in get_db().employees.find({"is_active": True}, {"password_hash": 0}).sort("full_name", 1)]

@router.post("", status_code=201)
def create_employee(payload: EmployeeCreate, claims: dict = Depends(require_admin)):
    db = get_db()
    document = employee_document(payload.employee_id, payload.full_name, payload.email, payload.department, payload.role, hash_password(payload.password), str(payload.recovery_email))
    existing_by_id = db.employees.find_one({"employee_id": payload.employee_id})
    existing_by_email = db.employees.find_one({"email": payload.email.lower()})
    existing_by_recovery_email = db.employees.find_one({"recovery_email": str(payload.recovery_email).lower(), "recovery_email_verified": True, "is_active": True})
    if existing_by_recovery_email and (not existing_by_id or existing_by_recovery_email.get("_id") != existing_by_id.get("_id")):
        raise HTTPException(status_code=409, detail="Recovery email is already registered")
    if existing_by_email and (not existing_by_id or existing_by_email.get("_id") != existing_by_id.get("_id")):
        raise HTTPException(status_code=409, detail="Employee ID or email already exists")
    if existing_by_id and existing_by_id.get("is_active", True):
        raise HTTPException(status_code=409, detail="Employee ID or email already exists")
    if existing_by_id:
        db.employees.update_one({"_id": existing_by_id["_id"]}, {"$set": document})
        document["_id"] = existing_by_id["_id"]
        db.audit_logs.insert_one(audit_event("EMPLOYEE_REACTIVATED", claims["sub"], "ACCEPTED", {"employee_id": payload.employee_id}))
        response = admin_employee(document)
        try:
            verification = request_recovery_email_verification(db, document)
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            response["verification_message"] = "Employee created, but recovery email verification could not be sent. Select the employee and save the recovery email to retry."
            return response
        response["verification_challenge_token"] = verification["challenge_token"]
        response["verification_message"] = verification["message"]
        response["verification_delivery_mode"] = verification.get("delivery_mode", "smtp")
        return response
    try:
        inserted = db.employees.insert_one(document)
        document["_id"] = inserted.inserted_id
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Employee ID or email already exists") from exc
    db.audit_logs.insert_one(audit_event("EMPLOYEE_CREATED", claims["sub"], "ACCEPTED", {"employee_id": payload.employee_id}))
    response = admin_employee(document)
    try:
        verification = request_recovery_email_verification(db, document)
    except HTTPException as exc:
        if exc.status_code != 429:
            raise
        response["verification_message"] = "Employee created, but recovery email verification could not be sent. Select the employee and save the recovery email to retry."
        return response
    response["verification_challenge_token"] = verification["challenge_token"]
    response["verification_message"] = verification["message"]
    response["verification_delivery_mode"] = verification.get("delivery_mode", "smtp")
    return response

@router.put("/{employee_id}")
def update_employee(employee_id: str, payload: EmployeeUpdate, claims: dict = Depends(require_admin)):
    db = get_db()
    employee = db.employees.find_one({"employee_id": employee_id})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    if employee.get("employee_id") == "ADM-001" and payload.employee_id != "ADM-001":
        raise HTTPException(status_code=400, detail="The original System Admin employee ID cannot be changed.")
    if employee.get("employee_id") == "ADM-001" and payload.role != "admin":
        raise HTTPException(status_code=400, detail="The original System Admin role cannot be changed.")
    id_conflict = db.employees.find_one({"employee_id": payload.employee_id, "_id": {"$ne": employee["_id"]}})
    email_conflict = db.employees.find_one({"email": payload.email.lower(), "_id": {"$ne": employee["_id"]}})
    if id_conflict or email_conflict:
        raise HTTPException(status_code=409, detail="Employee ID or email already exists")
    changes = {"employee_id": payload.employee_id, "full_name": payload.full_name, "email": payload.email.lower(), "department": payload.department, "role": payload.role}
    if payload.role != employee.get("role"):
        changes["auth_version"] = employee.get("auth_version", 0) + 1
    should_verify_recovery_email = False
    if payload.recovery_email is not None:
        recovery_email = str(payload.recovery_email).lower()
        recovery_conflict = db.employees.find_one({
            "recovery_email": recovery_email,
            "recovery_email_verified": True,
            "is_active": True,
            "_id": {"$ne": employee["_id"]},
        })
        if recovery_conflict:
            raise HTTPException(status_code=409, detail="Recovery email is already registered")
        if recovery_email != employee.get("recovery_email"):
            changes["recovery_email"] = recovery_email
            changes["recovery_email_verified"] = False
            should_verify_recovery_email = True
        elif not employee.get("recovery_email_verified", False):
            should_verify_recovery_email = True
    if payload.password is not None:
        changes["password_hash"] = hash_password(payload.password)
        changes["auth_version"] = employee.get("auth_version", 0) + 1
    db.employees.update_one({"_id": employee["_id"]}, {"$set": changes})
    updated = db.employees.find_one({"_id": employee["_id"]})
    db.audit_logs.insert_one(audit_event("EMPLOYEE_UPDATED", claims["sub"], "ACCEPTED", {"employee_id": employee_id, "updated_employee_id": payload.employee_id}))
    response = admin_employee(updated)
    if should_verify_recovery_email:
        verification = request_recovery_email_verification(db, updated)
        response["verification_challenge_token"] = verification["challenge_token"]
        response["verification_message"] = verification["message"]
        response["verification_delivery_mode"] = verification.get("delivery_mode", "smtp")
    return response

@router.delete("/{employee_id}")
def remove_employee(employee_id: str, claims: dict = Depends(require_admin)):
    db = get_db()
    employee = db.employees.find_one({"employee_id": employee_id})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    if employee.get("employee_id") == "ADM-001":
        raise HTTPException(status_code=400, detail="The original System Admin account cannot be removed.")
    if claims["sub"] == employee.get("employee_id"):
        raise HTTPException(status_code=400, detail="You cannot remove your own account.")
    db.employees.delete_one({"employee_id": employee_id})
    db.audit_logs.insert_one(audit_event("EMPLOYEE_DELETED", claims["sub"], "ACCEPTED", {"employee_id": employee_id}))
    return {"message": "Employee removed", "employee_id": employee_id}
