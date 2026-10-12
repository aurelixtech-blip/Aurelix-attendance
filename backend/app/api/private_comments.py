from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from app.api.deps import current_employee
from app.core.security import require_admin
from app.db.mongodb import get_db
from app.schemas.private_comment import AdminPrivateComment, PrivateCommentCreate, PrivateCommentResponse

router = APIRouter(prefix="/api/comments", tags=["private comments"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PrivateCommentResponse)
def submit_private_comment(payload: PrivateCommentCreate, employee: dict = Depends(current_employee)):
    if employee.get("role") != "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Employee access required")

    get_db().employee_comments.insert_one({
        "employee_id": employee["employee_id"],
        "employee_name": employee["full_name"],
        "comment": payload.comment,
        "submitted_at": datetime.now(timezone.utc),
    })
    return {"message": "Comment submitted successfully"}


@router.get("", response_model=list[AdminPrivateComment])
def list_private_comments(_claims: dict = Depends(require_admin)):
    comments = get_db().employee_comments.find(
        {},
        {"_id": 0, "employee_id": 1, "employee_name": 1, "comment": 1, "submitted_at": 1},
    ).sort("submitted_at", -1)
    return [
        {
            **item,
            "submitted_at": item["submitted_at"].isoformat(),
        }
        for item in comments
    ]
