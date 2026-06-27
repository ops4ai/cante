"""JWT auth utilities for the API."""
from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext
from cante.settings import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(user_id: str, role: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    access = jwt.encode(
        {"sub": user_id, "role": role, "exp": now + timedelta(minutes=settings.jwt_expire_minutes), "type": "access"},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    refresh = jwt.encode(
        {"sub": user_id, "role": role, "exp": now + timedelta(days=7), "type": "refresh"},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    return access, refresh

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
