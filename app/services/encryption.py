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


# Phase 18 (D-04): Bring-Your-Own LLM key encryption reuses the exact same Fernet
# (one ENCRYPTION_KEY, one code path) as Telegram-session encryption — no second
# key to manage. These are thin semantic aliases so call-sites read intent.
def encrypt_api_key(api_key: str) -> str:
    """Encrypt a BYO LLM provider API key before storing in llm_settings."""
    return encrypt_session(api_key)


def decrypt_api_key(encrypted: str) -> str:
    """Decrypt a BYO LLM provider API key when building the provider client."""
    return decrypt_session(encrypted)
