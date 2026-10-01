from email.message import EmailMessage
import base64
import logging
from smtplib import SMTP
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailProvider(Protocol):
    def send_otp(self, recovery_email: str, otp: str) -> None: ...
    def send_attendance_reminder(self, recovery_email: str, employee_name: str, reminder_type: str) -> None: ...


class MockEmailProvider:
    delivery_mode = "mock"

    def __init__(self):
        self.sent_messages = []
        self.sent_reminders = []

    def send_otp(self, recovery_email: str, otp: str) -> None:
        self.sent_messages.append((recovery_email, otp))

    def send_attendance_reminder(self, recovery_email: str, employee_name: str, reminder_type: str) -> None:
        subject, body = _attendance_reminder_content(employee_name, reminder_type)
        self.sent_reminders.append((recovery_email, subject, body))


_mock_email_provider = MockEmailProvider()


class SMTPEmailProvider:
    delivery_mode = "smtp"

    def __init__(self, host: str, port: int, username: str, password: str, from_email: str, from_name: str, use_tls: bool):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.from_name = from_name
        self.use_tls = use_tls

    def send_otp(self, recovery_email: str, otp: str) -> None:
        recipient = mask_email_address(recovery_email) or "<invalid>"
        logger.info("SMTP delivery starting host=%s port=%s recipient=%s", self.host, self.port, recipient)
        message = EmailMessage()
        message["Subject"] = "Aurelix Smart Attendance - Password Reset Code"
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = recovery_email
        message.set_content(
            "Hello,\n\n"
            "Your Aurelix Smart Attendance password reset code is:\n\n"
            f"{otp}\n\n"
            "This code expires in 5 minutes.\n\n"
            "If you did not request a password reset, you can ignore this email."
        )
        phase = "connection"
        try:
            with SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.ehlo()
                if self.use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                logger.info("SMTP connection established host=%s port=%s", self.host, self.port)
                phase = "authentication"
                smtp.login(self.username, self.password)
                logger.info("SMTP authentication succeeded host=%s port=%s", self.host, self.port)
                phase = "message send"
                smtp.send_message(message)
                logger.info("SMTP message accepted host=%s port=%s recipient=%s", self.host, self.port, recipient)
        except Exception as exc:
            safe_error = _redact_smtp_error(str(exc), self.username, self.password, self.from_email, recovery_email, otp)
            logger.error(
                "SMTP %s failed host=%s port=%s recipient=%s exception=%s reason=%s",
                phase,
                self.host,
                self.port,
                recipient,
                type(exc).__name__,
                safe_error,
            )
            raise

    def send_attendance_reminder(self, recovery_email: str, employee_name: str, reminder_type: str) -> None:
        subject, body = _attendance_reminder_content(employee_name, reminder_type)
        recipient = mask_email_address(recovery_email) or "<invalid>"
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = recovery_email
        message.set_content(body)
        phase = "connection"
        try:
            with SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.ehlo()
                if self.use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                phase = "authentication"
                smtp.login(self.username, self.password)
                phase = "message send"
                smtp.send_message(message)
        except Exception as exc:
            logger.error(
                "Attendance reminder SMTP %s failed host=%s port=%s recipient=%s exception=%s",
                phase,
                self.host,
                self.port,
                recipient,
                type(exc).__name__,
            )
            raise


def _attendance_reminder_content(employee_name: str, reminder_type: str) -> tuple[str, str]:
    if reminder_type == "check_in":
        subject = "Aurelix Smart Attendance \u2014 Check-In Reminder"
        message = "This is a reminder that you have not checked in for today.\n\nPlease complete your attendance check-in."
    elif reminder_type == "check_out":
        subject = "Aurelix Smart Attendance \u2014 Check-Out Reminder"
        message = "This is a reminder that you have checked in today but have not checked out.\n\nPlease complete your attendance check-out."
    else:
        raise ValueError("Unsupported attendance reminder type")
    return subject, f"Hello {employee_name},\n\n{message}\n\nAurelix Smart Attendance"


def _redact_smtp_error(message: str, username: str, password: str, from_email: str, recipient: str, otp: str) -> str:
    sensitive_values = (
        username,
        password,
        otp,
        from_email,
        recipient,
        base64.b64encode(password.encode()).decode(),
        base64.b64encode(f"\0{username}\0{password}".encode()).decode(),
    )
    for sensitive_value in sensitive_values:
        if sensitive_value:
            message = message.replace(sensitive_value, "[REDACTED]")
    return message


def get_email_provider() -> EmailProvider:
    settings = get_settings()
    provider = settings.email_provider.lower()
    logger.info("Email provider selected: %s", provider)
    if provider == "mock":
        return _mock_email_provider
    if provider == "smtp":
        missing = [
            name for name, value in (
                ("SMTP_HOST", settings.smtp_host),
                ("SMTP_USERNAME", settings.smtp_username),
                ("SMTP_PASSWORD", settings.smtp_password),
                ("SMTP_FROM_EMAIL", settings.smtp_from_email),
            ) if not value or not value.strip()
        ]
        if missing:
            logger.error("SMTP configuration incomplete; missing=%s", ",".join(missing))
            raise RuntimeError("SMTP email provider is not configured")
        if settings.smtp_host.strip().lower() != "smtp.gmail.com":
            logger.error("SMTP_HOST must be smtp.gmail.com for the configured Gmail email provider")
            raise RuntimeError("SMTP_HOST must be smtp.gmail.com")
        if settings.smtp_port != 587 or not settings.smtp_use_tls:
            logger.error("Gmail SMTP requires SMTP_PORT=587 and SMTP_USE_TLS=true")
            raise RuntimeError("Gmail SMTP requires TLS on port 587")
        if settings.smtp_from_email.strip().lower() != settings.smtp_username.strip().lower():
            logger.error("Gmail SMTP sender address must match the authenticated username")
            raise RuntimeError("SMTP_FROM_EMAIL must match SMTP_USERNAME for Gmail")
        logger.info("SMTP provider configured host=%s port=%s tls=%s", settings.smtp_host, settings.smtp_port, settings.smtp_use_tls)
        return SMTPEmailProvider(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            settings.smtp_password,
            settings.smtp_from_email,
            settings.smtp_from_name,
            settings.smtp_use_tls,
        )
    raise RuntimeError("Unsupported email provider")


def mask_email_address(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local_part, domain = value.rsplit("@", 1)
    return f"{local_part[:1]}******@{domain}"
