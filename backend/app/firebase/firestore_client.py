"""Firestore Admin client helpers (Phase 3 adapter support)."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ValidationAppError
from app.core.providers import require_firebase_admin_for
from app.firebase.admin import get_firebase_admin_app
from app.firebase.cost_controls import clamp_page_size


def get_firestore_client() -> Any:
    """Return Admin Firestore client or raise a clear configuration error."""
    require_firebase_admin_for("Firestore")
    get_firebase_admin_app()
    try:
        from firebase_admin import firestore
    except ImportError as exc:  # pragma: no cover
        raise ValidationAppError(
            "firebase-admin is not installed. pip install -r requirements-firebase.txt "
            "when enabling DATASTORE_PROVIDER=firestore.",
        ) from exc
    return firestore.client()


def tenant_filter_ok(
    data: dict[str, Any],
    *,
    organization_id: str | None,
    site_ids: list[str] | None = None,
    site_id: str | None = None,
) -> bool:
    if organization_id and data.get("organization_id") != organization_id:
        return False
    doc_site = data.get("site_id")
    if site_id and doc_site != site_id:
        return False
    if site_ids is not None and len(site_ids) > 0 and doc_site and doc_site not in site_ids:
        return False
    return True


__all__ = ["clamp_page_size", "get_firestore_client", "tenant_filter_ok"]
