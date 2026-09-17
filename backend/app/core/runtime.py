"""Datastore mode helpers."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.providers import (
    normalize_auth_provider,
    normalize_datastore_provider,
    normalize_storage_provider,
    require_firebase_admin_for,
)


def is_firestore() -> bool:
    return normalize_datastore_provider(get_settings().datastore_provider) == "firestore"


def is_sqlalchemy() -> bool:
    return normalize_datastore_provider(get_settings().datastore_provider) == "sqlalchemy"


def require_firebase_stack() -> None:
    """Fail clearly when Firebase-first defaults are selected without Admin config."""
    settings = get_settings()
    auth = normalize_auth_provider(settings.auth_provider)
    datastore = normalize_datastore_provider(settings.datastore_provider)
    storage = normalize_storage_provider(settings.storage_provider)
    if auth == "firebase" or datastore == "firestore" or storage == "firebase":
        require_firebase_admin_for("Firebase-first runtime")
    if auth == "firebase" and not (settings.firebase_api_key or "").strip():
        from app.core.exceptions import ValidationAppError

        raise ValidationAppError(
            "FIREBASE_API_KEY is required when AUTH_PROVIDER=firebase "
            "(web API key from Firebase console; not an Admin private key).",
        )
