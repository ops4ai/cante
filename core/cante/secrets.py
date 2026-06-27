"""App-level symmetric encryption for secrets at rest. Uses Fernet (AES-128-CBC)."""
from cryptography.fernet import Fernet
from cante.settings import settings

def _get_fernet() -> Fernet:
    import base64, hashlib
    key = hashlib.sha256(settings.secret_encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
