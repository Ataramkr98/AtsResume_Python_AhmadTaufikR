import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    configured = settings.FIELD_ENCRYPTION_KEY.strip()
    if configured:
        key = configured.encode()
    else:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.strip().encode()).decode() if value else ""


def decrypt_value(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def lookup_hash(value: str) -> str:
    normalized = value.strip().casefold()
    return hmac.new(settings.PII_HASH_KEY.encode(), normalized.encode(), hashlib.sha256).hexdigest()
