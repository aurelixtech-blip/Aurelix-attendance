import hmac

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import get_settings
from app.core.security import require_admin
from app.services.attendance_reminders import run_attendance_reminder, send_attendance_reminder_samples
from app.services.email_service import get_email_provider

router = APIRouter(prefix="/api/cron/attendance-reminders", tags=["attendance-reminders"])
development_router = APIRouter(prefix="/api/development/attendance-reminders", tags=["attendance-reminders"])


def _authorize_cron(request: Request) -> None:
    secret = get_settings().cron_secret
    cron_secret = request.headers.get("x-cron-secret", "")
    authorization = request.headers.get("authorization", "")
    valid_cron_secret = bool(secret) and hmac.compare_digest(cron_secret, secret)
    valid_bearer_secret = bool(secret) and hmac.compare_digest(authorization, f"Bearer {secret}")
    if not valid_cron_secret and not valid_bearer_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/check-in")
def check_in_reminder(request: Request):
    _authorize_cron(request)
    return run_attendance_reminder("check_in")


@router.post("/check-out")
def check_out_reminder(request: Request):
    _authorize_cron(request)
    return run_attendance_reminder("check_out")


@development_router.post("/samples")
def send_sample_reminders(_claims: dict = Depends(require_admin)):
    settings = get_settings()
    if settings.environment.strip().lower() not in {"development", "test"}:
        raise HTTPException(status_code=404, detail="Not found")
    if not settings.attendance_reminder_test_email:
        raise HTTPException(status_code=400, detail="ATTENDANCE_REMINDER_TEST_EMAIL is not configured")
    try:
        email_provider = get_email_provider()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Gmail SMTP is not configured") from exc
    if getattr(email_provider, "delivery_mode", None) != "smtp":
        raise HTTPException(status_code=503, detail="Gmail SMTP must be enabled to send samples")
    try:
        return send_attendance_reminder_samples(settings.attendance_reminder_test_email, email_provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
