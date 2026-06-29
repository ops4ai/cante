"""Unit tests for cante.security — SSRF filter (S3), startup guard (S4), Fernet key (S9)."""
from __future__ import annotations

import pytest

from cante.security import (
    InsecureConfigurationError,
    assert_no_default_secrets,
    is_safe_url,
    validate_fernet_key,
)

VALID_FERNET = "piaHPpe_QOK_8VyqxXr6XNsM05WVKIHFR0TLeAIDHIA="


# ── S3: is_safe_url ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",                       # non-http scheme
        "gopher://127.0.0.1:6379/_INFO",            # non-http scheme
        "http://127.0.0.1:6379/",                   # loopback
        "http://localhost/",                        # loopback (resolves to 127.0.0.1/::1)
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.1/",                         # private
        "http://192.168.1.1/",                      # private
        "http://172.16.0.1/",                       # private
        "http://[::1]/",                            # ipv6 loopback
        "http://0.0.0.0/",                          # invalid/this-network
        "http://this-host-does-not-exist.invalid/",  # unresolvable -> fail closed
    ],
)
def test_is_safe_url_rejects_internal(url):
    assert is_safe_url(url) is False


def test_is_safe_url_rejects_non_string():
    assert is_safe_url(None) is False  # type: ignore[arg-type]
    assert is_safe_url("") is False


def test_is_safe_url_allows_public_ip():
    # 8.8.8.8 is a public IP literal — no DNS lookup needed, not in any blocked range.
    assert is_safe_url("http://8.8.8.8/") is True
    assert is_safe_url("https://8.8.8.8/path") is True


def test_is_safe_url_enforces_allowlist():
    assert is_safe_url("http://8.8.8.8/", allowed_hosts=["8.8.8.8"]) is True
    # Even a public host is refused when not on the allowlist.
    assert is_safe_url("http://8.8.8.8/", allowed_hosts=["api.partner.com"]) is False
    # Allowlist never overrides a blocked address.
    assert is_safe_url("http://169.254.169.254/", allowed_hosts=["169.254.169.254"]) is False


# ── S9: Fernet key validation ────────────────────────────────────────────────


def test_validate_fernet_key_accepts_valid():
    validate_fernet_key(VALID_FERNET)  # must not raise


def test_validate_fernet_key_rejects_passphrase():
    with pytest.raises(InsecureConfigurationError):
        validate_fernet_key("change-me-change-me-change-me!")


def test_validate_fernet_key_rejects_short():
    with pytest.raises(InsecureConfigurationError):
        validate_fernet_key("not-a-key")


def test_secrets_round_trip(fernet_key, monkeypatch):
    from cante import secrets as cante_secrets
    from cante.settings import settings

    monkeypatch.setattr(settings, "secret_encryption_key", fernet_key)
    cante_secrets._fernet = None  # reset cache
    token = cante_secrets.encrypt("super-secret-api-key")
    assert token != "super-secret-api-key"
    assert cante_secrets.decrypt(token) == "super-secret-api-key"


def test_secrets_rejects_weak_key(monkeypatch):
    from cante import secrets as cante_secrets
    from cante.settings import settings

    monkeypatch.setattr(settings, "secret_encryption_key", "change-me-change-me-change-me!")
    cante_secrets._fernet = None
    with pytest.raises(InsecureConfigurationError):
        cante_secrets.encrypt("x")


# ── S4: startup guard ────────────────────────────────────────────────────────


@pytest.fixture
def guard_enabled(monkeypatch):
    """Run with the CANTE_SKIP_STARTUP_GUARD escape hatch disabled."""
    monkeypatch.delenv("CANTE_SKIP_STARTUP_GUARD", raising=False)
    yield


def _strong(monkeypatch):
    from cante.settings import settings

    monkeypatch.setattr(settings, "jwt_secret", "a-real-strong-jwt-secret-0123456789")
    monkeypatch.setattr(settings, "admin_password", "a-real-admin-password")
    monkeypatch.setattr(settings, "trigger_api_key", "a-real-trigger-key")
    monkeypatch.setattr(settings, "secret_encryption_key", VALID_FERNET)
    monkeypatch.setattr(settings, "evolution_api_key", "")


def test_guard_rejects_default_jwt(guard_enabled, monkeypatch):
    from cante.settings import settings

    _strong(monkeypatch)
    monkeypatch.setattr(settings, "jwt_secret", "change-me-in-production")
    with pytest.raises(InsecureConfigurationError):
        assert_no_default_secrets()


def test_guard_rejects_empty_trigger_key(guard_enabled, monkeypatch):
    from cante.settings import settings

    _strong(monkeypatch)
    monkeypatch.setattr(settings, "trigger_api_key", "")
    with pytest.raises(InsecureConfigurationError):
        assert_no_default_secrets()


def test_guard_rejects_weak_encryption_key(guard_enabled, monkeypatch):
    from cante.settings import settings

    _strong(monkeypatch)
    monkeypatch.setattr(settings, "secret_encryption_key", "change-me-change-me-change-me!")
    with pytest.raises(InsecureConfigurationError):
        assert_no_default_secrets()


def test_guard_rejects_default_evolution_key(guard_enabled, monkeypatch):
    from cante.settings import settings

    _strong(monkeypatch)
    monkeypatch.setattr(settings, "evolution_api_key", "evolution-secret-key")
    with pytest.raises(InsecureConfigurationError):
        assert_no_default_secrets()


def test_guard_passes_with_strong_values(guard_enabled, monkeypatch):
    _strong(monkeypatch)
    assert_no_default_secrets()  # must not raise


def test_guard_skipped_when_env_set(monkeypatch):
    # Even with default secrets, the escape hatch short-circuits (test/CI use).
    monkeypatch.setenv("CANTE_SKIP_STARTUP_GUARD", "1")
    from cante.settings import settings

    monkeypatch.setattr(settings, "jwt_secret", "change-me-in-production")
    assert_no_default_secrets()  # must not raise
