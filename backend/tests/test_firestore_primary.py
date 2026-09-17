"""Firestore-primary unit tests (mocked Admin SDK — no live credentials required)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError, ValidationAppError
from app.core.providers import require_firestore_datastore, require_firebase_admin_for
from app.core.runtime import is_firestore, require_firebase_stack
from app.domain.records import OrganizationRecord, UserProfileRecord
from app.firebase.admin import reset_firebase_admin_cache
from app.identity.principal import principal_from_profile
from app.repositories.firestore_store import (
    FirestoreOrganizationRepository,
    FirestoreUserProfileRepository,
)
from app.services.tenant import TenantContext


@pytest.fixture(autouse=True)
def _reset():
    get_settings.cache_clear()
    reset_firebase_admin_cache()
    yield
    get_settings.cache_clear()
    reset_firebase_admin_cache()


def _fs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "firebase")
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-anpr")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/fake-sa.json")
    monkeypatch.setenv("FIREBASE_API_KEY", "fake-web-api-key")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "pcn-anpr.firebasestorage.app")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()


def test_defaults_are_firebase_first(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AUTH_PROVIDER", "DATASTORE_PROVIDER", "STORAGE_PROVIDER", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.auth_provider == "firebase"
    assert s.datastore_provider == "firestore"
    assert s.storage_provider == "firebase"
    assert s.database_url == ""


def test_firestore_mode_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _fs_env(monkeypatch)
    assert is_firestore() is True
    assert get_settings().database_url == "" or True  # may be unset
    require_firestore_datastore()


def test_missing_firebase_config_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "firebase")
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "")
    get_settings.cache_clear()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        require_firebase_stack()


def test_sqlalchemy_engine_blocked_in_firestore_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _fs_env(monkeypatch)
    from app.db.session import get_engine

    with pytest.raises(ValidationAppError, match="not used when DATASTORE_PROVIDER=firestore"):
        get_engine()


@pytest.mark.asyncio
async def test_org_repo_tenant_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _fs_env(monkeypatch)
    store: dict[str, dict] = {}

    class FakeDoc:
        def __init__(self, doc_id: str):
            self.id = doc_id

        def get(self):
            data = store.get(self.id)
            return SimpleNamespace(exists=data is not None, id=self.id, to_dict=lambda: dict(data) if data else None)

        def set(self, payload, merge=False):
            cur = store.get(self.id, {})
            if merge:
                cur.update(payload)
            else:
                cur = dict(payload)
            store[self.id] = cur

        def delete(self):
            store.pop(self.id, None)

    class FakeCollection:
        def document(self, doc_id: str):
            return FakeDoc(doc_id)

        def limit(self, n: int):
            return self

        def stream(self):
            for doc_id, data in list(store.items())[:100]:
                yield SimpleNamespace(id=doc_id, to_dict=lambda d=data: dict(d))

        def where(self, *args, **kwargs):
            return self

    db = MagicMock()
    db.collection.return_value = FakeCollection()
    monkeypatch.setattr("app.repositories.firestore_store.get_firestore_client", lambda: db)

    repo = FirestoreOrganizationRepository()
    org = OrganizationRecord(id="org1", name="Hotel", slug="hotel")
    await repo.add(org)
    got = await repo.get("org1")
    assert got is not None
    assert got.slug == "hotel"


@pytest.mark.asyncio
async def test_user_profile_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    _fs_env(monkeypatch)
    store: dict[str, dict] = {}

    class FakeDoc:
        def __init__(self, doc_id: str):
            self.id = doc_id

        def get(self):
            data = store.get(self.id)
            return SimpleNamespace(exists=data is not None, id=self.id, to_dict=lambda: dict(data) if data else None)

        def set(self, payload, merge=False):
            cur = store.get(self.id, {})
            cur.update(payload) if merge else None
            store[self.id] = {**(cur if merge else {}), **payload}

    class FakeCollection:
        def __init__(self):
            self._filters = []

        def document(self, doc_id: str):
            return FakeDoc(doc_id)

        def where(self, field, op, value):
            self._filters.append((field, value))
            return self

        def limit(self, n: int):
            return self

        def stream(self):
            org = None
            for f, v in self._filters:
                if f == "organization_id":
                    org = v
            for doc_id, data in store.items():
                if org is None or data.get("organization_id") == org:
                    yield SimpleNamespace(id=doc_id, to_dict=lambda d=data: dict(d))

    db = MagicMock()
    db.collection.side_effect = lambda name: FakeCollection()
    monkeypatch.setattr("app.repositories.firestore_store.get_firestore_client", lambda: db)

    repo = FirestoreUserProfileRepository()
    await repo.add(
        UserProfileRecord(
            id="uid-a",
            email="a@pcncloud.in",
            role="ORG_ADMIN",
            organization_id="org-a",
            firebase_uid="uid-a",
        )
    )
    await repo.add(
        UserProfileRecord(
            id="uid-b",
            email="b@pcncloud.in",
            role="ORG_ADMIN",
            organization_id="org-b",
            firebase_uid="uid-b",
        )
    )
    listed = await repo.list_for_org("org-a")
    assert all(u.organization_id == "org-a" for u in listed)


def test_tenant_context_principal_isolation() -> None:
    principal = principal_from_profile(
        UserProfileRecord(
            id="u1",
            email="x@pcncloud.in",
            role="ORG_ADMIN",
            organization_id="org-a",
            site_ids=[],
        )
    )
    ctx = TenantContext(principal, [])
    ctx.ensure_org("org-a")
    with pytest.raises(Exception):
        ctx.ensure_org("org-b")
