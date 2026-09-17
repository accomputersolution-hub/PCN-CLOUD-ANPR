"""Provider name normalization and Firebase readiness checks.

Production code never returns fake success when Firebase is selected
but misconfigured or not yet wired.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationAppError

# Accepted aliases → canonical name
DATASTORE_ALIASES = {
    "sqlalchemy": "sqlalchemy",
    "postgres": "sqlalchemy",
    "postgresql": "sqlalchemy",
    "sqlite": "sqlalchemy",
    "firestore": "firestore",
}

STORAGE_ALIASES = {
    "local": "local",
    "filesystem": "local",
    "firebase": "firebase",
    "gcs": "firebase",
    "s3": "s3",
    "minio": "s3",
}

AUTH_ALIASES = {
    "jwt": "jwt",
    "local": "jwt",
    "postgres": "jwt",
    "firebase": "firebase",
}


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    auth: str
    datastore: str
    storage: str


def normalize_auth_provider(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key not in AUTH_ALIASES:
        raise ValidationAppError(f"Unknown AUTH_PROVIDER: {raw}")
    return AUTH_ALIASES[key]


def normalize_datastore_provider(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key not in DATASTORE_ALIASES:
        raise ValidationAppError(f"Unknown DATASTORE_PROVIDER: {raw}")
    return DATASTORE_ALIASES[key]


def normalize_storage_provider(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key not in STORAGE_ALIASES:
        raise ValidationAppError(f"Unknown STORAGE_PROVIDER: {raw}")
    return STORAGE_ALIASES[key]


def current_providers(settings: Settings | None = None) -> ProviderSelection:
    s = settings or get_settings()
    return ProviderSelection(
        auth=normalize_auth_provider(s.auth_provider),
        datastore=normalize_datastore_provider(s.datastore_provider),
        storage=normalize_storage_provider(s.storage_provider),
    )


def firebase_admin_configured(settings: Settings | None = None) -> bool:
    """True when enough server-side Firebase Admin settings are present."""
    s = settings or get_settings()
    if not (s.firebase_project_id or "").strip():
        return False
    if (s.firebase_credentials_json or "").strip():
        return True
    if (s.firebase_credentials_file or "").strip():
        return True
    return False


def firebase_client_configured(settings: Settings | None = None) -> bool:
    """True when browser/client Firebase web config fields are present."""
    s = settings or get_settings()
    return bool(
        (s.firebase_project_id or "").strip()
        and (s.firebase_api_key or "").strip()
        and (s.firebase_auth_domain or "").strip()
        and (s.firebase_app_id or "").strip()
    )


def require_sqlalchemy_datastore(settings: Settings | None = None) -> None:
    provider = normalize_datastore_provider((settings or get_settings()).datastore_provider)
    if provider == "sqlalchemy":
        return
    if provider == "firestore":
        raise ValidationAppError(
            "Firestore datastore is not enabled yet. Keep DATASTORE_PROVIDER=sqlalchemy "
            "(or postgres). Configure FIREBASE_* and implement the adapter before switching.",
        )
    raise ValidationAppError(f"Unknown DATASTORE_PROVIDER: {provider}")


def require_firebase_admin_for(purpose: str, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    if not firebase_admin_configured(s):
        raise ValidationAppError(
            f"Firebase Admin is not configured for {purpose}. "
            "Set FIREBASE_PROJECT_ID and either FIREBASE_CREDENTIALS_FILE or "
            "FIREBASE_CREDENTIALS_JSON. Never commit service-account keys.",
        )
