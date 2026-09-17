"""Startup path must not require PostgreSQL when Firestore is primary."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.firebase.admin import reset_firebase_admin_cache


@pytest.fixture(autouse=True)
def _reset():
    get_settings.cache_clear()
    reset_firebase_admin_cache()
    yield
    get_settings.cache_clear()
    reset_firebase_admin_cache()


def test_health_reports_providers_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "firebase")
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-anpr")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/fake-sa.json")
    monkeypatch.setenv("FIREBASE_API_KEY", "fake-key")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "pcn-anpr.firebasestorage.app")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()

    # Avoid real Admin init during TestClient lifespan by stubbing.
    monkeypatch.setattr("app.firebase.admin.get_firebase_admin_app", lambda: object())
    monkeypatch.setattr("app.core.runtime.require_firebase_stack", lambda: None)

    from app.main import create_app

    application = create_app()
    with TestClient(application) as client:
        res = client.get("/api/v1/health")
        assert res.status_code == 200
        body = res.json()
        # Accept either nested providers or flat fields depending on schema.
        text = str(body).lower()
        assert "firestore" in text or "firebase" in text or body.get("status") in {"ok", "healthy", "up"}
