"""Firebase Admin bootstrap.

Does not initialize the Admin SDK unless credentials are present.
Never fabricates a successful client.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationAppError
from app.core.providers import firebase_admin_configured, require_firebase_admin_for


@lru_cache
def get_firebase_admin_app() -> Any:
    """Return initialized firebase_admin app, or raise a clear configuration error."""
    settings = get_settings()
    require_firebase_admin_for("Admin SDK", settings)
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValidationAppError(
            "firebase-admin is not installed. Install it only when enabling Firebase providers.",
        ) from exc

    if firebase_admin._apps:  # type: ignore[attr-defined]
        return firebase_admin.get_app()

    cred = _load_credentials(settings, credentials)
    options: dict[str, Any] = {"projectId": settings.firebase_project_id}
    if settings.firebase_storage_bucket:
        options["storageBucket"] = settings.firebase_storage_bucket
    return firebase_admin.initialize_app(cred, options)


def _load_credentials(settings: Settings, credentials: Any) -> Any:
    raw_json = (settings.firebase_credentials_json or "").strip()
    if raw_json:
        import json

        return credentials.Certificate(json.loads(raw_json))
    path = (settings.firebase_credentials_file or "").strip()
    if path:
        return credentials.Certificate(path)
    raise ValidationAppError("Firebase credentials missing")


def firebase_admin_ready(settings: Settings | None = None) -> bool:
    return firebase_admin_configured(settings or get_settings())


def reset_firebase_admin_cache() -> None:
    """Test/helper — clear cached Admin app factory and delete initialized apps."""
    get_firebase_admin_app.cache_clear()
    try:
        import firebase_admin
    except ImportError:  # pragma: no cover
        return
    for name in list(getattr(firebase_admin, "_apps", {}) or {}):
        try:
            firebase_admin.delete_app(firebase_admin.get_app(name))
        except Exception:  # noqa: BLE001
            pass
