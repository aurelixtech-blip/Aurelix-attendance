from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(min_length=2, max_length=32)
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    department: str = Field(min_length=2, max_length=80)
    role: Literal["employee", "admin"] = "employee"
    password: str
    recovery_email: EmailStr

    @field_validator("recovery_email", mode="before")
    @classmethod
    def normalize_recovery_email(cls, value):
        return str(value).strip().lower() if value is not None else value

class EmployeeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(min_length=2, max_length=32)
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    department: str = Field(min_length=2, max_length=80)
    role: Literal["employee", "admin"] = "employee"
    password: str | None = None
    recovery_email: EmailStr | None = None

    @field_validator("recovery_email", mode="before")
    @classmethod
    def normalize_recovery_email(cls, value):
        return str(value).strip().lower() if value is not None else value

class EmployeeResponse(BaseModel):
    id: str
    employee_id: str
    full_name: str
    email: str
    department: str
    role: str
    is_active: bool
    recovery_email_verified: bool = False
