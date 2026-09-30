from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core.config import get_settings
from app.db.mongodb import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, role: str, auth_version: int = 0) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": subject, "role": role, "auth_version": auth_version, "exp": expires}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


def current_claims(token: str = Depends(oauth2_scheme)) -> dict:
    claims = decode_access_token(token)
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    employee = get_db().employees.find_one({"employee_id": claims["sub"]}, {"auth_version": 1})
    if not employee or claims.get("auth_version", 0) != employee.get("auth_version", 0):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims


def require_admin(claims: dict = Depends(current_claims)) -> dict:
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return claims
