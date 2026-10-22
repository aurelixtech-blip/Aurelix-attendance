from pydantic import BaseModel, Field, field_validator


class AttendanceNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Note cannot be empty or whitespace-only")
        return value
