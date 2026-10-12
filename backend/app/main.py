import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api import auth, employees, attendance, admin, audit, attendance_reminders
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.mongodb import init_indexes

logging.basicConfig(level=logging.INFO)

def initialize_database():
    try:
        init_indexes()
    except Exception:
        logging.exception("MongoDB unavailable; API will start but database operations will fail")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Aurelix Smart Attendance API", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.exception_handler(Exception)
async def unhandled_error(_request: Request, _exc: Exception):
    logging.exception("Unhandled API error")
    return JSONResponse(status_code=500, content={"detail": "An internal server error occurred"})

@app.get("/health")
def health():
    return {"status": "ok", "service": "aurelix-attendance-api"}

app.include_router(auth.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(admin.router)
app.include_router(audit.router)
app.include_router(attendance_reminders.router)
app.include_router(attendance_reminders.development_router)
