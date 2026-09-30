from pydantic import BaseModel, EmailStr, Field, field_validator


class PasswordRecoveryRequest(BaseModel):
    recovery_email: EmailStr

    @field_validator("recovery_email", mode="before")
    @classmethod
    def normalize_recovery_email(cls, value):
        return str(value).strip().lower() if value is not None else value


class PasswordRecoveryVerify(BaseModel):
    challenge_token: str = Field(min_length=32, max_length=128)
    otp: str = Field(min_length=1, max_length=32)


class RecoveryEmailVerification(BaseModel):
    challenge_token: str = Field(min_length=32, max_length=128)
    otp: str = Field(min_length=1, max_length=32)


class PasswordRecoveryReset(BaseModel):
    reset_token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
