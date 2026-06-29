"""Security primitives for Cante.

- :func:`assert_no_default_secrets` — startup guard that refuses to boot while
  any shipped-default / empty secret is still present (S4).
- :func:`is_safe_url` — SSRF egress filter for model-invoked HTTP tools (S3).
- :func:`validate_fernet_key` — used by ``cante.secrets`` and the startup guard
  to reject weak at-rest encryption keys (S9).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

from cante.settings import settings

# ── S4: default-secret startup guard ────────────────────────────────────────

# Values that are shipped in the repo / .env.example and must never reach prod.
_DEFAULT_SECRET_VALUES: set[str] = {
    "change-me-in-production",
    "change-me-change-me-change-me!",
    "change-me",
    "change-me-to-a-random-string",
    "evolution-secret-key",
}


class InsecureConfigurationError(RuntimeError):
    """Raised at startup when the deployment is not safe to run."""


def validate_fernet_key(key: str) -> None:
    """Raise :class:`InsecureConfigurationError` unless *key* is a valid Fernet key.

    A Fernet key is exactly 44 urlsafe-base64 characters (32 bytes). We validate
    via ``Fernet`` itself rather than a regex so we catch any malformed key.
    """
    from cryptography.fernet import Fernet  # local import to keep import cost low

    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:  # InvalidToken / ValueError / TypeError
        raise InsecureConfigurationError(
            "SECRET_ENCRYPTION_KEY must be a valid Fernet key (run: "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`). "
            f"Got: {exc.__class__.__name__}"
        ) from exc


def assert_no_default_secrets() -> None:
    """Refuse to boot if any secret is still a shipped default or empty.

    Cante ships as open-source; the default deploy must not be trivially
    admin-compromisable. This guard is called at API and worker startup. Tests
    set ``CANTE_SKIP_STARTUP_GUARD=1`` only for fixtures that deliberately
    exercise the guard itself.
    """
    import os

    if os.environ.get("CANTE_SKIP_STARTUP_GUARD") == "1":
        return

    problems: list[str] = []

    def _check(name: str, env: str, value: str, *, allow_default: bool = False) -> None:
        if not value:
            problems.append(f"{env} is empty — set a strong value in .env")
            return
        if not allow_default and value in _DEFAULT_SECRET_VALUES:
            problems.append(f"{env} is still a shipped default — rotate it")

    _check("jwt_secret", "JWT_SECRET", settings.jwt_secret)
    _check("admin_password", "ADMIN_PASSWORD", settings.admin_password)
    _check("trigger_api_key", "TRIGGER_API_KEY", settings.trigger_api_key)

    # S9: at-rest encryption key must be a real Fernet key (not a passphrase).
    if not settings.secret_encryption_key:
        problems.append("SECRET_ENCRYPTION_KEY is empty — generate a Fernet key")
    elif settings.secret_encryption_key in _DEFAULT_SECRET_VALUES:
        problems.append("SECRET_ENCRYPTION_KEY is still a shipped default — generate a Fernet key")
    else:
        try:
            validate_fernet_key(settings.secret_encryption_key)
        except InsecureConfigurationError as exc:
            problems.append(str(exc))

    # Evolution API key is optional but must not be the shipped default if set.
    if settings.evolution_api_key and settings.evolution_api_key in _DEFAULT_SECRET_VALUES:
        problems.append("EVOLUTION_API_KEY is still a shipped default — rotate it")

    if problems:
        raise InsecureConfigurationError(
            "Refusing to start — insecure configuration:\n  - "
            + "\n  - ".join(problems)
            + "\nFix these in your .env (see .env.example) and restart."
        )


# ── S3: SSRF egress filter ──────────────────────────────────────────────────

# Networks a model-invoked HTTP tool must never reach.
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "0.0.0.0/8",        # "this network" / invalid
    "10.0.0.0/8",       # private
    "100.64.0.0/10",    # CGNAT
    "127.0.0.0/8",      # loopback
    "169.254.0.0/16",   # link-local + cloud metadata (169.254.169.254)
    "172.16.0.0/12",    # private
    "192.0.0.0/24",     # IETF protocol assignments
    "192.0.2.0/24",     # TEST-NET-1
    "192.168.0.0/16",   # private
    "198.18.0.0/15",    # benchmarking
    "198.51.100.0/24",  # TEST-NET-2
    "203.0.113.0/24",   # TEST-NET-3
    "224.0.0.0/4",      # multicast
    "240.0.0.0/4",      # reserved
    "::1/128",          # loopback
    "fc00::/7",         # ULA private
    "fe80::/10",        # link-local
)


class UnsafeUrlError(ValueError):
    """Raised when a URL is refused by the SSRF egress filter."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    for blocked in _BLOCKED_PREFIXES:
        net = ipaddress.ip_network(blocked)
        # `ip in net` is version-aware (False across v4/v6); subnet_of is not.
        if ip.version == net.version and ip in net:
            return True
    return False


def is_safe_url(url: str, allowed_hosts: Iterable[str] | None = None) -> bool:
    """Return True iff *url* is an http(s) URL to a public, non-internal host.

    Checks:
    * scheme is http or https (rejects ``file://``, ``gopher://``, …);
    * host (after DNS resolution) is not in loopback / private / link-local /
      cloud-metadata / reserved space — unresolvable hosts are rejected
      (fail-closed);
    * if *allowed_hosts* is non-empty, the URL host must match one of them
      (case-insensitive; port-agnostic).
    """
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    # Allowlist check first — explicit deny always wins.
    if allowed_hosts:
        wanted = {h.lower().split(":")[0] for h in allowed_hosts}
        if host.lower() not in wanted:
            return False

    # Resolve every A/AAAA record; if *any* address is internal, reject.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Cannot prove the host is public → fail closed.
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if _ip_is_blocked(ip):
            return False
    return True


__all__ = [
    "InsecureConfigurationError",
    "UnsafeUrlError",
    "assert_no_default_secrets",
    "is_safe_url",
    "validate_fernet_key",
]
