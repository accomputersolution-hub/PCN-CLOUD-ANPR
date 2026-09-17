"""Phase 2 Firebase Storage — mocked Admin SDK; no live credentials required."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.firebase.cost_controls import event_storage_keys
from app.services.storage import (
    FirebaseStorage,
    LocalFilesystemStorage,
    get_storage,
    normalize_storage_key,
    reset_storage_cache,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    reset_storage_cache()
    yield
    get_settings.cache_clear()
    reset_storage_cache()


def _firebase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-demo")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "pcn-demo.appspot.com")
    get_settings.cache_clear()
    reset_storage_cache()


def test_normalize_storage_key_rejects_traversal() -> None:
    assert normalize_storage_key("/a/b/c.jpg") == "a/b/c.jpg"
    with pytest.raises(ValueError):
        normalize_storage_key("../secret")
    with pytest.raises(ValueError):
        normalize_storage_key("")


def test_firebase_storage_save_get_delete_with_mock_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    _firebase_env(monkeypatch)
    blob = MagicMock()
    blob.exists.return_value = True
    blob.download_as_bytes.return_value = b"jpeg-bytes"
    bucket = MagicMock()
    bucket.blob.return_value = blob

    store = FirebaseStorage(bucket=bucket)
    key = store.save("org1/events/e1/snapshot.jpg", b"jpeg-bytes", "image/jpeg")
    assert key == "org1/events/e1/snapshot.jpg"
    blob.upload_from_string.assert_called_once_with(b"jpeg-bytes", content_type="image/jpeg")

    assert store.get_bytes(key) == b"jpeg-bytes"
    store.delete(key)
    blob.delete.assert_called_once()
    assert store.public_url(key).startswith("/api/v1/storage/")


def test_firebase_storage_missing_object_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _firebase_env(monkeypatch)
    blob = MagicMock()
    blob.exists.return_value = False
    bucket = MagicMock()
    bucket.blob.return_value = blob
    store = FirebaseStorage(bucket=bucket)
    assert store.get_bytes("org/missing.jpg") is None
    store.delete("org/missing.jpg")
    blob.delete.assert_not_called()


def test_firebase_storage_requires_bucket_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "pcn-demo")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "/tmp/sa.json")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "")
    get_settings.cache_clear()
    with pytest.raises(ValidationAppError, match="FIREBASE_STORAGE_BUCKET"):
        FirebaseStorage(bucket=MagicMock())


def test_firebase_storage_requires_admin_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_FILE", "")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "bucket")
    get_settings.cache_clear()
    reset_storage_cache()
    with pytest.raises(ValidationAppError, match="Firebase Admin is not configured"):
        get_storage()


def test_get_storage_firebase_opens_bucket_via_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    _firebase_env(monkeypatch)
    fake_bucket = MagicMock()
    monkeypatch.setattr(
        "app.services.storage.FirebaseStorage._open_bucket",
        staticmethod(lambda _name: fake_bucket),
    )
    store = get_storage()
    assert isinstance(store, FirebaseStorage)
    assert store._bucket is fake_bucket


def test_event_storage_keys_match_firebase_paths() -> None:
    keys = event_storage_keys("org-a", "evt-1")
    assert keys == {
        "snapshot": "org-a/events/evt-1/snapshot.jpg",
        "plate_crop": "org-a/events/evt-1/plate_crop.jpg",
        "vehicle_crop": "org-a/events/evt-1/vehicle_crop.jpg",
    }


def test_local_storage_still_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    reset_storage_cache()
    store = get_storage()
    assert isinstance(store, LocalFilesystemStorage)
    key = store.save("o/events/e/snapshot.jpg", b"x", "image/jpeg")
    assert store.get_bytes(key) == b"x"
