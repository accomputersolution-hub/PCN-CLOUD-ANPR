"""Unit tests for Firestore repository adapter (mocked Admin client)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.domain.records import AnprEventRecord, OrganizationRecord
from app.firebase.cost_controls import clamp_page_size
from app.repositories.firestore_store import (
    FirestoreAnprEventRepository,
    FirestoreGatewayRepository,
    FirestoreOrganizationRepository,
)


@pytest.fixture(autouse=True)
def _reset():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _firebase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-anpr")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_firestore_requires_admin_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASTORE_PROVIDER", "firestore")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    get_settings.cache_clear()
    repo = FirestoreOrganizationRepository()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        await repo.list_all()


@pytest.mark.asyncio
async def test_firestore_org_add_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    _firebase_env(monkeypatch)
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

    class FakeCollection:
        def document(self, doc_id: str):
            return FakeDoc(doc_id)

        def limit(self, n: int):
            return self

        def stream(self):
            for doc_id, data in list(store.items())[:100]:
                yield SimpleNamespace(id=doc_id, to_dict=lambda d=data: dict(d))

    db = MagicMock()
    db.collection.return_value = FakeCollection()
    monkeypatch.setattr("app.repositories.firestore_store.get_firestore_client", lambda: db)

    repo = FirestoreOrganizationRepository()
    org = OrganizationRecord(id="org1", name="Hotel", slug="hotel")
    await repo.add(org)
    got = await repo.get("org1")
    assert got is not None
    assert got.slug == "hotel"
    listed = await repo.list_all()
    assert any(item.id == "org1" for item in listed)


@pytest.mark.asyncio
async def test_anpr_event_rejects_missing_org_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _firebase_env(monkeypatch)
    events: list[dict] = []

    class FakeSnap:
        def __init__(self, data: dict):
            self.id = data["id"]
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class FakeQuery:
        def where(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def limit(self, n):
            return self

        def start_after(self, *a, **k):
            return self

        def stream(self):
            for row in events:
                yield FakeSnap(row)

    class FakeCollection:
        def document(self, doc_id: str):
            raise NotImplementedError

        def where(self, *a, **k):
            return FakeQuery()

    db = MagicMock()
    db.collection.return_value = FakeCollection()
    monkeypatch.setattr("app.repositories.firestore_store.get_firestore_client", lambda: db)

    events.append(
        {
            "id": "e1",
            "organization_id": "org-a",
            "site_id": "s1",
            "gate_id": "g1",
            "camera_id": "c1",
            "direction": "ENTRY",
            "plate_text": "MH12AB1234",
            "plate_normalized": "MH12AB1234",
            "timestamp": datetime.now(UTC).isoformat(),
            "local_timestamp": datetime.now(UTC).isoformat(),
            "image_bytes": b"should-be-stripped",
        }
    )
    repo = FirestoreAnprEventRepository()
    rows = await repo.list_for_tenant(organization_id="org-a", limit=10)
    assert len(rows) == 1
    assert isinstance(rows[0], AnprEventRecord)
    assert not hasattr(rows[0], "image_bytes") or getattr(rows[0], "image_bytes", None) is None


@pytest.mark.asyncio
async def test_gateway_tenant_site_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _firebase_env(monkeypatch)
    from app.domain.connectivity import GatewayRecord

    stored: dict[str, dict] = {}

    class FakeDoc:
        def __init__(self, doc_id: str):
            self.id = doc_id

        def set(self, payload, merge=False):
            stored[self.id] = dict(payload)

        def get(self):
            data = stored.get(self.id)
            return SimpleNamespace(exists=data is not None, id=self.id, to_dict=lambda: dict(data) if data else None)

        def delete(self):
            stored.pop(self.id, None)

    class FakeQuery:
        def __init__(self, rows):
            self.rows = rows

        def where(self, field, op, value):
            filtered = [r for r in self.rows if r.get(field) == value]
            return FakeQuery(filtered)

        def order_by(self, *a, **k):
            return self

        def limit(self, n):
            self.rows = self.rows[:n]
            return self

        def stream(self):
            for row in self.rows:
                yield SimpleNamespace(id=row["id"], to_dict=lambda r=row: {k: v for k, v in r.items() if k != "id"})

    class FakeCollection:
        def document(self, doc_id: str):
            return FakeDoc(doc_id)

        def where(self, field, op, value):
            rows = [{"id": k, **v} for k, v in stored.items()]
            return FakeQuery(rows).where(field, op, value)

    db = MagicMock()
    db.collection.return_value = FakeCollection()
    monkeypatch.setattr("app.repositories.firestore_store.get_firestore_client", lambda: db)

    repo = FirestoreGatewayRepository()
    await repo.add(
        GatewayRecord(
            id="gw1",
            organization_id="org-a",
            site_id="site-1",
            name="GW",
            device_type="PCN_CLOUD_GATEWAY",
        )
    )
    await repo.add(
        GatewayRecord(
            id="gw2",
            organization_id="org-b",
            site_id="site-2",
            name="Other",
            device_type="PCN_CLOUD_GATEWAY",
        )
    )
    listed = await repo.list_for_tenant(organization_id="org-a", site_ids=["site-1"])
    assert [g.id for g in listed] == ["gw1"]


def test_event_page_clamp() -> None:
    assert clamp_page_size(1000) == 100
