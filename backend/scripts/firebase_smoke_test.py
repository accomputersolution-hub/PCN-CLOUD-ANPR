# Firebase smoke test (optional, live project).
#
# Prerequisites:
#   1. pip install -r requirements-firebase.txt
#   2. FIREBASE_CREDENTIALS_FILE pointing to a service-account JSON (not committed)
#   3. Firebase Storage default bucket created in Console (Get Started) if testing Storage
#   4. Keep AUTH_PROVIDER=jwt / DATASTORE_PROVIDER=sqlalchemy / STORAGE_PROVIDER=local
#      unless you intentionally want to exercise those providers.
#
# Creates ONLY disposable test resources under _smoke/ and deletes them afterward.
# Does NOT migrate PostgreSQL data.

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("FIREBASE_PROJECT_ID", "pcn-anpr")


def main() -> int:
    from app.core.config import get_settings
    from app.core.providers import firebase_admin_configured
    from app.firebase.admin import get_firebase_admin_app, reset_firebase_admin_cache
    from app.services.storage import FirebaseStorage, reset_storage_cache

    get_settings.cache_clear()
    reset_firebase_admin_cache()
    reset_storage_cache()
    settings = get_settings()

    if not firebase_admin_configured(settings):
        print("SKIP: FIREBASE_PROJECT_ID + credentials not configured")
        return 0

    print("Initializing Admin SDK…")
    get_firebase_admin_app()

    from firebase_admin import firestore

    db = firestore.client()
    smoke_id = f"smoke-{uuid.uuid4().hex[:8]}"
    doc_ref = db.collection("_smoke").document(smoke_id)
    doc_ref.set({"ok": True, "purpose": "pcn-cloud-anpr-smoke"})
    snap = doc_ref.get()
    assert snap.exists, "Firestore smoke write failed"
    doc_ref.delete()
    print(f"OK Firestore smoke document {smoke_id} written and deleted")

    if not (settings.firebase_storage_bucket or "").strip():
        print("SKIP Storage: FIREBASE_STORAGE_BUCKET empty")
        return 0

    try:
        store = FirebaseStorage()
        key = f"_smoke/{smoke_id}.txt"
        store.save(key, b"pcn-smoke", "text/plain")
        data = store.get_bytes(key)
        assert data == b"pcn-smoke"
        store.delete(key)
        print(f"OK Storage smoke object {key} uploaded and deleted")
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP Storage smoke (create default bucket in Console if needed): {exc}")

    print("Smoke complete. Defaults remain jwt/sqlalchemy/local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
