from functools import lru_cache
import json
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Aurelix Smart Attendance"
    environment: str = "development"
    mongodb_uri: str = Field(default="mongodb://localhost:27017", validation_alias="MONGODB_URI")
    database_name: str = Field(default="aurelix_attendance", validation_alias="DATABASE_NAME")
    jwt_secret: str = Field(default="change-me-in-env", validation_alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=480, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    cors_origins: list[str] = Field(default=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"], validation_alias="CORS_ORIGINS")
    reverse_geocode_url: str = Field(default="https://nominatim.openstreetmap.org/reverse", validation_alias="REVERSE_GEOCODE_URL")
    reverse_geocode_user_agent: str = Field(default="AurelixSmartAttendance/1.0", validation_alias="REVERSE_GEOCODE_USER_AGENT")
    reverse_geocode_timeout_seconds: float = Field(default=3.0, validation_alias="REVERSE_GEOCODE_TIMEOUT_SECONDS")
    google_maps_api_key: str | None = Field(default=None, validation_alias="GOOGLE_MAPS_API_KEY")
    google_geocode_url: str = Field(default="https://maps.googleapis.com/maps/api/geocode/json", validation_alias="GOOGLE_GEOCODE_URL")
    cron_secret: str | None = Field(default=None, validation_alias="CRON_SECRET")
    attendance_reminder_test_email: str | None = Field(default=None, validation_alias="ATTENDANCE_REMINDER_TEST_EMAIL")
    email_provider: str = Field(default="mock", validation_alias="EMAIL_PROVIDER")
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, validation_alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_from_email: str | None = Field(default=None, validation_alias="SMTP_FROM_EMAIL")
    smtp_from_name: str = Field(default="Aurelix Smart Attendance", validation_alias="SMTP_FROM_NAME")
    smtp_use_tls: bool = Field(default=True, validation_alias="SMTP_USE_TLS")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(origin).strip() for origin in parsed if str(origin).strip()]
            except json.JSONDecodeError:
                pass
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
