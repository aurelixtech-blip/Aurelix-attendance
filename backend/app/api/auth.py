from fastapi import APIRouter, Depends, HTTPException, Request
from app.api.deps import current_employee
from app.core.rate_limit import limiter
from app.core.security import create_access_token, require_admin, verify_password
from app.db.mongodb import get_db
from app.models.employee import public_employee
from app.models.audit import audit_event
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.password_recovery import PasswordRecoveryRequest, PasswordRecoveryReset, PasswordRecoveryVerify, RecoveryEmailVerification
from app.services.password_recovery import request_otp, request_recovery_email_verification, reset_password, verify_otp

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    db = get_db()
    employee = db.employees.find_one({"email": payload.email.lower()})
    if not employee or not verify_password(payload.password, employee.get("password_hash", "")):
        db.audit_logs.insert_one(audit_event("LOGIN_FAILURE", employee.get("employee_id") if employee else None, "REJECTED"))
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if not employee.get("is_active", True):
        raise HTTPException(status_code=403, detail="Employee account is inactive")
    db.audit_logs.insert_one(audit_event("LOGIN_SUCCESS", employee["employee_id"], "ACCEPTED"))
    return {"access_token": create_access_token(employee["employee_id"], employee["role"], employee.get("auth_version", 0)), "user": public_employee(employee)}


@router.post("/forgot-password/request")
@limiter.limit("20/minute")
def forgot_password_request(request: Request, payload: PasswordRecoveryRequest):
    result = request_otp(get_db(), str(payload.recovery_email).lower())
    return result


@router.post("/forgot-password/verify")
@limiter.limit("30/minute")
def forgot_password_verify(request: Request, payload: PasswordRecoveryVerify):
    return verify_otp(get_db(), payload.challenge_token, payload.otp)


@router.post("/recovery-email/verify")
@limiter.limit("20/minute")
def verify_recovery_email(request: Request, payload: RecoveryEmailVerification, claims: dict = Depends(require_admin)):
    return verify_otp(get_db(), payload.challenge_token, payload.otp, purpose="recovery_email_verification")


@router.post("/forgot-password/reset")
@limiter.limit("10/minute")
def forgot_password_reset(request: Request, payload: PasswordRecoveryReset):
    return reset_password(get_db(), payload.reset_token, payload.new_password)

@router.get("/me")
def me(employee: dict = Depends(current_employee)):
    return public_employee(employee)
