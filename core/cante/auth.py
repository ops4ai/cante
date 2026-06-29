"""JWT auth utilities for the API.

Tokens carry a ``tenant_id`` claim (S1) and a ``type`` (access|refresh) plus
``jti``/``iat``/``nbf`` (S10). :func:`decode_token` enforces the expected token
type so a refresh token cannot be used on an access-only endpoint.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from cante.settings import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, resolved from the JWT at request time."""

    user_id: str
    tenant_id: str
    role: str


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(user_id: str, tenant_id: str, role: str, token_type: str, expires_in: int) -> tuple[str, str]:
    now = _now()
    jti = uuid.uuid4().hex
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "type": token_type,
        "jti": jti,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti


def create_access_token(user_id: str, tenant_id: str, role: str) -> tuple[str, str]:
    """Return (access_token, jti)."""
    return _encode(user_id, tenant_id, role, "access", settings.jwt_expire_minutes * 60)


def create_refresh_token(user_id: str, tenant_id: str, role: str) -> tuple[str, str]:
    """Return (refresh_token, jti)."""
    return _encode(user_id, tenant_id, role, "refresh", settings.jwt_refresh_expire_days * 86_400)


def create_token_pair(user_id: str, tenant_id: str, role: str) -> tuple[str, str]:
    """Return (access_token, refresh_token) — convenience for login/refresh."""
    access, _ = create_access_token(user_id, tenant_id, role)
    refresh, _ = create_refresh_token(user_id, tenant_id, role)
    return access, refresh


def decode_token(token: str, expected_type: str | None = None) -> dict:
    """Decode and validate *token*.

    Raises :class:`jose.JWTError` if the signature is invalid, expired, not-yet-
    valid, or (when *expected_type* is given) of the wrong type.
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub", "type"]},
    )
    if expected_type is not None and payload.get("type") != expected_type:
        raise JWTError(f"Expected {expected_type} token, got {payload.get('type')}")
    return payload


def principal_from_token(token: str, expected_type: str = "access") -> Principal:
    """Decode *token* and build a :class:`Principal`."""
    payload = decode_token(token, expected_type=expected_type)
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise JWTError("Token missing tenant_id claim")
    return Principal(user_id=payload["sub"], tenant_id=tenant_id, role=payload.get("role", "operator"))


__all__ = [
    "Principal",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "create_token_pair",
    "decode_token",
    "principal_from_token",
]
