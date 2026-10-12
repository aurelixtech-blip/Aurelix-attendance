from pydantic import BaseModel, Field, field_validator


class PrivateCommentCreate(BaseModel):
    comment: str = Field(min_length=1, max_length=500)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        comment = value.strip()
        if not comment:
            raise ValueError("Comment must not be blank")
        if len(comment.splitlines()) > 7:
            raise ValueError("Comment must not exceed 7 lines")
        return comment


class PrivateCommentResponse(BaseModel):
    message: str


class AdminPrivateComment(BaseModel):
    employee_id: str
    employee_name: str
    comment: str
    submitted_at: str
