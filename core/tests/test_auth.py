"""JWT auth tests — tenant_id claim (S1), token-type enforcement + jti (S10)."""
from __future__ import annotations

import pytest
from jose import JWTError

from cante.auth import (
    Principal,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    principal_from_token,
)

TENANT_A = "00000000-0000-0000-0000-000000000001"
USER = "00000000-0000-0000-0000-0000000000aa"


def test_access_token_carries_tenant_and_type():
    token, jti = create_access_token(USER, TENANT_A, "admin")
    payload = decode_token(token)
    assert payload["tenant_id"] == TENANT_A
    assert payload["type"] == "access"
    assert payload["role"] == "admin"
    assert payload["jti"] == jti
    assert "iat" in payload and "exp" in payload


def test_refresh_token_rejected_on_access_endpoint():
    """S10: a refresh token must not authenticate an access-only endpoint."""
    refresh, _ = create_refresh_token(USER, TENANT_A, "operator")
    with pytest.raises(JWTError):
        decode_token(refresh, expected_type="access")


def test_access_token_rejected_on_refresh_endpoint():
    access, _ = create_access_token(USER, TENANT_A, "operator")
    with pytest.raises(JWTError):
        decode_token(access, expected_type="refresh")


def test_principal_from_access_token():
    token, _ = create_access_token(USER, TENANT_A, "admin")
    p = principal_from_token(token, expected_type="access")
    assert isinstance(p, Principal)
    assert p.user_id == USER
    assert p.tenant_id == TENANT_A
    assert p.role == "admin"


def test_principal_rejects_refresh_token():
    refresh, _ = create_refresh_token(USER, TENANT_A, "operator")
    with pytest.raises(JWTError):
        principal_from_token(refresh, expected_type="access")


def test_token_pair_roundtrip():
    access, refresh = create_token_pair(USER, TENANT_A, "operator")
    assert decode_token(access, expected_type="access")["tenant_id"] == TENANT_A
    assert decode_token(refresh, expected_type="refresh")["tenant_id"] == TENANT_A


def test_bad_signature_rejected():
    token, _ = create_access_token(USER, TENANT_A, "operator")
    tampered = token + "x"
    with pytest.raises(JWTError):
        decode_token(tampered, expected_type="access")
