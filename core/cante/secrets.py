"""App-level symmetric encryption for secrets at rest. Uses Fernet (AES-128-CBC).

The encryption key MUST be a valid Fernet key (44 urlsafe-base64 chars, 32 bytes)
generated with ``Fernet.generate_key()`` — never a passphrase. The startup guard
(:func:`cante.security.assert_no_default_secrets`) and :func:`validate_fernet_key`
reject weak keys before the app boots.
"""
from cryptography.fernet import Fernet

from cante.security import validate_fernet_key
from cante.settings import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        validate_fernet_key(settings.secret_encryption_key)  # fail-closed on weak key
        _fernet = Fernet(settings.secret_encryption_key.encode())
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
