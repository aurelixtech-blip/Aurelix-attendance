from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from fastapi import HTTPException
from app.core.config import get_settings
from app.core.security import hash_password
from app.models.audit import audit_event
from app.services.email_service import get_email_provider, mask_email_address

OTP_LIFETIME = timedelta(minutes=5)
RESET_LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5
GENERIC_REQUEST_MESSAGE = "If the account details are eligible, a verification code has been sent."
INVALID_OTP_MESSAGE = "Invalid or expired verification code."
TOO_MANY_ATTEMPTS_MESSAGE = "Too many attempts. Please request a new code."
EXPIRED_SESSION_MESSAGE = "Your password reset session has expired. Please start again."


def _digest(value: str) -> str:
    secret = get_settings().jwt_secret.encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _issue_otp(db, email: str, purpose: str, employee_id: str | None) -> dict:
    now = _now()
    email = email.strip().lower()
    challenge_key = _digest(f"{purpose}:{email}")
    sessions = db.password_reset_sessions

    challenge_token = secrets.token_urlsafe(32)
    challenge_hash = _digest(challenge_token)
    otp = f"{secrets.randbelow(1_000_000):06d}"
    sessions.update_many(
        {"challenge_key": challenge_key},
        {"$set": {"used": True, "reset_used": True}},
    )
    session = {
        "challenge_hash": challenge_hash,
        "challenge_key": challenge_key,
        "user_id": employee_id,
        "recovery_email": email,
        "purpose": purpose,
        "otp_hash": _digest(f"{challenge_hash}:{otp}" if employee_id else secrets.token_urlsafe(24)),
        "created_at": now,
        "expires_at": now + OTP_LIFETIME,
        "cleanup_at": now + OTP_LIFETIME,
        "attempts": 0,
        "used": False,
        "reset_token_hash": None,
        "reset_expires_at": None,
        "reset_used": False,
    }
    sessions.insert_one(session)

    delivery_mode = get_settings().email_provider.lower()
    if employee_id:
        try:
            provider = get_email_provider()
            delivery_mode = getattr(provider, "delivery_mode", "smtp")
            provider.send_otp(email, otp)
        except Exception as exc:
            sessions.update_one({"challenge_hash": challenge_hash}, {"$set": {"used": True}})
            raise HTTPException(status_code=503, detail="Verification email delivery failed. Please try again later.") from exc

    if purpose == "password_reset":
        message = GENERIC_REQUEST_MESSAGE
    elif delivery_mode == "mock":
        message = "Development mock mode: verification code captured locally; no email was sent."
    else:
        message = "Recovery email verification code sent."
    return {
        "message": message,
        "challenge_token": challenge_token,
        "masked_recovery_email": mask_email_address(email),
        "delivery_mode": delivery_mode,
    }


def request_otp(db, recovery_email: str) -> dict:
    recovery_email = recovery_email.strip().lower()
    employee = db.employees.find_one({
        "recovery_email": recovery_email,
        "recovery_email_verified": True,
        "is_active": True,
    })
    return _issue_otp(
        db,
        recovery_email,
        "password_reset",
        employee.get("employee_id") if employee else None,
    )


def request_recovery_email_verification(db, employee: dict) -> dict:
    recovery_email = employee.get("recovery_email")
    if not recovery_email:
        raise HTTPException(status_code=422, detail="Recovery email is required")
    return _issue_otp(
        db,
        recovery_email,
        "recovery_email_verification",
        employee.get("employee_id"),
    )


def verify_otp(db, challenge_token: str, otp: str, purpose: str = "password_reset") -> dict:
    challenge_hash = _digest(challenge_token)
    sessions = db.password_reset_sessions
    session = sessions.find_one({"challenge_hash": challenge_hash, "purpose": purpose})
    now = _now()
    if not session or session.get("used") or session.get("expires_at") <= now:
        raise HTTPException(status_code=400, detail=INVALID_OTP_MESSAGE)
    if session.get("attempts", 0) >= MAX_ATTEMPTS:
        sessions.update_one({"_id": session.get("_id")}, {"$set": {"used": True}})
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS_MESSAGE)

    valid_otp = len(otp) == 6 and otp.isdigit() and hmac.compare_digest(session["otp_hash"], _digest(f"{challenge_hash}:{otp}"))
    if not valid_otp:
        sessions.update_one(
            {"_id": session.get("_id"), "used": False, "attempts": {"$lt": MAX_ATTEMPTS}},
            {"$inc": {"attempts": 1}},
        )
        latest = sessions.find_one({"_id": session.get("_id")})
        if latest and latest.get("attempts", 0) >= MAX_ATTEMPTS:
            sessions.update_one({"_id": session.get("_id"), "used": False}, {"$set": {"used": True}})
            raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS_MESSAGE)
        raise HTTPException(status_code=400, detail=INVALID_OTP_MESSAGE)

    if purpose == "recovery_email_verification":
        result = sessions.update_one({"_id": session.get("_id"), "used": False}, {"$set": {"used": True, "verified_at": now}})
        if getattr(result, "matched_count", 1) == 0:
            raise HTTPException(status_code=400, detail=INVALID_OTP_MESSAGE)
        employee_result = db.employees.update_one(
            {"employee_id": session.get("user_id"), "recovery_email": session.get("recovery_email"), "is_active": True},
            {"$set": {"recovery_email_verified": True, "updated_at": now}},
        )
        if getattr(employee_result, "matched_count", 1) == 0:
            raise HTTPException(status_code=400, detail=INVALID_OTP_MESSAGE)
        return {"message": "Recovery email verified successfully."}

    reset_token = secrets.token_urlsafe(32)
    reset_expires_at = now + RESET_LIFETIME
    result = sessions.update_one(
        {"_id": session.get("_id"), "used": False},
        {"$set": {
            "used": True,
            "verified_at": now,
            "reset_token_hash": _digest(reset_token),
            "reset_expires_at": reset_expires_at,
            "cleanup_at": reset_expires_at,
        }},
    )
    if getattr(result, "matched_count", 1) == 0:
        raise HTTPException(status_code=400, detail=INVALID_OTP_MESSAGE)
    return {"reset_token": reset_token}


def reset_password(db, reset_token: str, new_password: str) -> dict:
    now = _now()
    token_hash = _digest(reset_token)
    sessions = db.password_reset_sessions
    session = sessions.find_one({"reset_token_hash": token_hash, "reset_used": False, "purpose": "password_reset"})
    if not session or not session.get("reset_expires_at") or session["reset_expires_at"] <= now:
        raise HTTPException(status_code=400, detail=EXPIRED_SESSION_MESSAGE)

    result = sessions.update_one(
        {"_id": session.get("_id"), "reset_token_hash": token_hash, "reset_used": False, "reset_expires_at": {"$gt": now}},
        {"$set": {"reset_used": True}},
    )
    if getattr(result, "matched_count", 1) == 0:
        raise HTTPException(status_code=400, detail=EXPIRED_SESSION_MESSAGE)

    employee_id = session.get("user_id")
    if not employee_id:
        raise HTTPException(status_code=400, detail=EXPIRED_SESSION_MESSAGE)
    employee_result = db.employees.update_one(
        {
            "employee_id": employee_id,
            "is_active": {"$ne": False},
            "recovery_email": session.get("recovery_email"),
            "recovery_email_verified": True,
        },
        {"$set": {"password_hash": hash_password(new_password), "updated_at": now}, "$inc": {"auth_version": 1}},
    )
    if getattr(employee_result, "matched_count", 1) == 0:
        raise HTTPException(status_code=400, detail=EXPIRED_SESSION_MESSAGE)
    db.audit_logs.insert_one(audit_event("PASSWORD_RESET", employee_id, "ACCEPTED"))
    return {"message": "Password reset successfully. You can now sign in with your new password."}
