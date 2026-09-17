"""Phase 1 Firebase provider abstractions — no live Firebase credentials required."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.core.providers import (
    current_providers,
    firebase_admin_configured,
    firebase_client_configured,
    normalize_auth_provider,
    normalize_datastore_provider,
    normalize_storage_provider,
    require_firebase_admin_for,
    require_sqlalchemy_datastore,
)
from app.domain.records import AnprEventRecord, CameraRecord, OrganizationRecord
from app.firebase.cost_controls import (
    MAX_EVENT_PAGE_SIZE,
    clamp_page_size,
    event_storage_keys,
    should_write_heartbeat,
)
from app.repositories.firestore_store import FirestoreAnprEventRepository, FirestoreGatewayRepository
from app.services.storage import FirebaseStorage, get_storage, reset_storage_cache


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    reset_storage_cache()
    yield
    get_settings.cache_clear()
    reset_storage_cache()


def test_default_providers_are_firebase_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("DATASTORE_PROVIDER", raising=False)
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    get_settings.cache_clear()
    selected = current_providers()
    assert selected.auth == "firebase"
    assert selected.datastore == "firestore"
    assert selected.storage == "firebase"


def test_postgres_alias_maps_to_sqlalchemy() -> None:
    assert normalize_datastore_provider("postgres") == "sqlalchemy"
    assert normalize_datastore_provider("postgresql") == "sqlalchemy"
    assert normalize_auth_provider("local") == "jwt"
    assert normalize_storage_provider("gcs") == "firebase"


def test_unknown_providers_rejected() -> None:
    with pytest.raises(ValidationAppError):
        normalize_datastore_provider("mysql")
    with pytest.raises(ValidationAppError):
        normalize_auth_provider("ldap")
    with pytest.raises(ValidationAppError):
        normalize_storage_provider("ftp")


def test_firebase_config_validation_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "")
    get_settings.cache_clear()
    assert firebase_admin_configured() is False
    assert firebase_client_configured() is False
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        require_firebase_admin_for("unit-test")


def test_firebase_admin_configured_with_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-demo")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "")
    get_settings.cache_clear()
    assert firebase_admin_configured() is True


def test_firebase_client_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-demo")
    monkeypatch.setenv("FIREBASE_API_KEY", "web-key")
    monkeypatch.setenv("FIREBASE_AUTH_DOMAIN", "pcn-demo.firebaseapp.com")
    monkeypatch.setenv("FIREBASE_APP_ID", "1:123:web:abc")
    get_settings.cache_clear()
    assert firebase_client_configured() is True


def test_sqlalchemy_datastore_required_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASTORE_PROVIDER", "sqlalchemy")
    get_settings.cache_clear()
    require_sqlalchemy_datastore()


def test_sqlalchemy_rejected_when_firestore_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    get_settings.cache_clear()
    with pytest.raises(ValidationAppError, match="SQLAlchemy datastore is legacy"):
        require_sqlalchemy_datastore()


def test_jwt_auth_provider_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth.providers import JwtAuthProvider, get_auth_provider

    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    get_settings.cache_clear()
    provider = get_auth_provider()
    assert isinstance(provider, JwtAuthProvider)
    assert provider.name == "jwt"


def test_firebase_auth_provider_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth.providers import get_auth_provider

    monkeypatch.setenv("AUTH_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "")
    get_settings.cache_clear()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        get_auth_provider()


def test_firebase_auth_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth.providers import FirebaseAuthProvider

    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-anpr")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    monkeypatch.setenv("FIREBASE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ValidationAppError, match="FIREBASE_API_KEY"):
        FirebaseAuthProvider()


def test_firebase_auth_verify_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth.providers import FirebaseAuthProvider
    from app.core.exceptions import UnauthorizedError

    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-anpr")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    monkeypatch.setenv("FIREBASE_API_KEY", "web-api-key")
    get_settings.cache_clear()

    monkeypatch.setattr("app.auth.providers.get_firebase_admin_app", lambda: object())

    class _Auth:
        @staticmethod
        def verify_id_token(_token: str, app=None, clock_skew_seconds=0, **_kwargs):
            raise ValueError("bad token")

    import sys
    from types import ModuleType

    fake_fb = ModuleType("firebase_admin")
    fake_auth = ModuleType("firebase_admin.auth")
    fake_auth.verify_id_token = _Auth.verify_id_token
    fake_fb.auth = fake_auth
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_fb)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", fake_auth)

    provider = FirebaseAuthProvider()
    with pytest.raises(UnauthorizedError, match="Invalid Firebase ID token"):
        provider.verify_id_token("fake-token")


@pytest.mark.asyncio
async def test_firestore_repo_fails_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    get_settings.cache_clear()
    repo = FirestoreGatewayRepository()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        await repo.list_for_tenant(organization_id="org-1", site_ids=None)


@pytest.mark.asyncio
async def test_firestore_repo_requires_credentials_even_when_provider_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    get_settings.cache_clear()
    repo = FirestoreAnprEventRepository()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        await repo.list_for_tenant(organization_id="org-1", limit=10)


def test_domain_event_record_has_storage_keys_not_bytes() -> None:
    now = datetime.now(UTC)
    event = AnprEventRecord(
        id="e1",
        organization_id="o1",
        site_id="s1",
        gate_id="g1",
        camera_id="c1",
        direction="ENTRY",
        plate_text="MH12AB1234",
        plate_normalized="MH12AB1234",
        timestamp=now,
        local_timestamp=now,
        snapshot_storage_key="o1/events/e1/snapshot.jpg",
        plate_crop_storage_key="o1/events/e1/plate_crop.jpg",
    )
    dumped = event.model_dump()
    assert "snapshot_storage_key" in dumped
    assert b"JPEG" not in str(dumped).encode()
    org = OrganizationRecord(id="o1", name="Hotel", slug="hotel")
    cam = CameraRecord(
        id="c1",
        organization_id="o1",
        site_id="s1",
        name="Entry",
        has_credentials=True,
    )
    assert org.slug == "hotel"
    assert cam.has_credentials is True


def test_tenant_scoping_fields_required_on_camera() -> None:
    cam = CameraRecord(id="c1", organization_id="org-a", site_id="site-a", name="Cam")
    assert cam.organization_id == "org-a"
    assert cam.site_id == "site-a"


def test_storage_local_provider(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    reset_storage_cache()
    store = get_storage()
    key = store.save("org/test.bin", b"abc", "application/octet-stream")
    assert store.get_bytes(key) == b"abc"


def test_storage_firebase_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "")
    get_settings.cache_clear()
    reset_storage_cache()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        get_storage()


def test_storage_firebase_factory_uses_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 2: configured Firebase Storage constructs the adapter (bucket open mocked)."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-demo")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "pcn-demo.appspot.com")
    get_settings.cache_clear()
    reset_storage_cache()
    monkeypatch.setattr(
        "app.services.storage.FirebaseStorage._open_bucket",
        staticmethod(lambda _name: MagicMock()),
    )
    store = get_storage()
    assert isinstance(store, FirebaseStorage)


def test_cost_controls_pagination_and_throttle() -> None:
    assert clamp_page_size(None) == 50
    assert clamp_page_size(999) == MAX_EVENT_PAGE_SIZE
    keys = event_storage_keys("org1", "evt1")
    assert keys["snapshot"].startswith("org1/events/evt1/")
    assert "frame" not in keys
    now = datetime.now(UTC)
    assert should_write_heartbeat(None, now=now) is True
    assert should_write_heartbeat(now, now=now) is False
    assert should_write_heartbeat(now - timedelta(seconds=31), now=now) is True
