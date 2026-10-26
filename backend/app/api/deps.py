from fastapi import Depends, HTTPException
from app.core.security import current_claims
from app.db.mongodb import get_db


def current_employee(claims: dict = Depends(current_claims)) -> dict:
    employee = get_db().employees.find_one({"employee_id": claims["sub"]})
    if not employee or not employee.get("is_active", True):
        raise HTTPException(status_code=401, detail="Employee account is inactive")
    if employee.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access required")
    return employee
