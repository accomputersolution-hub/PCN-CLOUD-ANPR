from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().credentials_encryption_key.encode("utf-8"))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt stored credential") from exc
