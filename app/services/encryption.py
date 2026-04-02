from cryptography.fernet import Fernet
from app.config import get_settings
import base64
import hashlib


def get_fernet() -> Fernet:
    """Create Fernet instance from encryption key."""
    settings = get_settings()
    # Ensure key is 32 bytes for Fernet
    key = hashlib.sha256(settings.encryption_key.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key)
    return Fernet(key_b64)


def encrypt_session(session_string: str) -> str:
    """Encrypt session string before storing in DB."""
    fernet = get_fernet()
    encrypted = fernet.encrypt(session_string.encode())
    return encrypted.decode()


def decrypt_session(encrypted: str) -> str:
    """Decrypt session string when using."""
    fernet = get_fernet()
    decrypted = fernet.decrypt(encrypted.encode())
    return decrypted.decode()
