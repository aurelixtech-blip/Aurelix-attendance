from datetime import datetime, timezone
from typing import Any
from bson import ObjectId


def employee_document(employee_id: str, full_name: str, email: str, department: str, role: str, password_hash: str, recovery_email: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {"employee_id": employee_id, "full_name": full_name, "email": email.lower(), "department": department, "role": role, "password_hash": password_hash, "recovery_email": recovery_email.lower() if recovery_email else None, "recovery_email_verified": False, "auth_version": 0, "is_active": True, "created_at": now, "updated_at": now}


def public_employee(document: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(document.get("_id", ObjectId())), "employee_id": document["employee_id"], "full_name": document["full_name"], "email": document["email"], "department": document["department"], "role": document["role"], "is_active": document.get("is_active", True)}


def admin_employee(document: dict[str, Any]) -> dict[str, Any]:
    result = public_employee(document)
    result["recovery_email"] = document.get("recovery_email")
    result["recovery_email_verified"] = document.get("recovery_email_verified", False)
    return result
