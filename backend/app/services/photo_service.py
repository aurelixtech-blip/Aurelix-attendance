from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException, UploadFile
from gridfs import GridFSBucket
from gridfs.errors import NoFile
from PIL import Image, UnidentifiedImageError

from app.db.mongodb import get_db


MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_EDGE = 1920
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
PENDING_ATTENDANCE_ID = "pending"

class PhotoUnavailableError(Exception):
    """Raised when a referenced attendance photo cannot be read."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def prepare_photo(upload: UploadFile) -> tuple[bytes, str]:
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Photo must be a JPEG, PNG, or WebP image")
    data = await upload.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Photo must be 8 MB or smaller")
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            normalized = image.convert("RGB")
            if max(normalized.size) > MAX_PHOTO_EDGE:
                normalized.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE), Image.Resampling.LANCZOS)
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid image") from exc


def store_photo(data: bytes, content_type: str, employee_id: str, attendance_id: str, event: str) -> dict:
    uploaded_at = _utc_now()
    bucket = GridFSBucket(get_db())
    photo_id = bucket.upload_from_stream(
        f"attendance-{event}-{uuid4()}.jpg",
        BytesIO(data),
        metadata={
            "employee_id": employee_id,
            "attendance_id": attendance_id,
            "event": event,
            "content_type": content_type,
            "uploaded_at": uploaded_at,
        },
    )
    return {"file_id": str(photo_id), "content_type": content_type}


def bind_photo_to_attendance(reference: dict | None, attendance_id: str) -> None:
    if not reference or not reference.get("file_id") or not attendance_id:
        return
    get_db().fs.files.update_one(
        {"_id": ObjectId(reference["file_id"])},
        {"$set": {"metadata.attendance_id": attendance_id}},
    )


def _purge_gridfs_file(db, file_id: ObjectId) -> None:
    try:
        GridFSBucket(db).delete(file_id)
    except Exception:
        db.fs.files.delete_one({"_id": file_id})
        db.fs.chunks.delete_many({"files_id": file_id})


def delete_photo(reference: dict | None) -> None:
    if not reference or not reference.get("file_id"):
        return
    try:
        file_id = ObjectId(reference["file_id"])
    except Exception:
        return
    _purge_gridfs_file(get_db(), file_id)


def open_photo(reference: dict):
    file_id = ObjectId(reference["file_id"])
    try:
        stream = GridFSBucket(get_db()).open_download_stream(file_id)
    except NoFile as exc:
        raise PhotoUnavailableError("Photo unavailable") from exc
    return stream
