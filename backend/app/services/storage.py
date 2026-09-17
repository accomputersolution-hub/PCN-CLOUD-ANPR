"""Object storage abstraction.

StorageProvider
  ├── LocalFilesystemStorage  (default)
  └── FirebaseStorage         (Phase 2 — Admin SDK; evidence blobs only)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.core.logging import get_logger
from app.core.providers import normalize_storage_provider, require_firebase_admin_for

logger = get_logger(__name__)


def normalize_storage_key(key: str) -> str:
    """Reject path traversal and normalize object keys."""
    safe = key.lstrip("/").replace("\\", "/")
    parts = [p for p in safe.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("Invalid storage key")
    if not parts:
        raise ValueError("Invalid storage key")
    return "/".join(parts)


class StorageBackend(ABC):
    """Object storage abstraction. Swap local for Firebase Storage without changing ANPR logic."""

    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    @abstractmethod
    def get_bytes(self, key: str) -> bytes | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def public_url(self, key: str) -> str:
        raise NotImplementedError


class LocalFilesystemStorage(StorageBackend):
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = normalize_storage_key(key)
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        safe = normalize_storage_key(key)
        path = self._path(safe)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.info("storage.save", provider="local", key=safe, bytes=len(data), content_type=content_type)
        return safe

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def public_url(self, key: str) -> str:
        return f"/api/v1/storage/{quote(normalize_storage_key(key))}"


class S3CompatibleStorage(StorageBackend):
    """Extension point for later. Not wired in V1."""

    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError("S3 storage is an extension point and is not enabled in V1.")

    def get_bytes(self, key: str) -> bytes | None:
        raise NotImplementedError("S3 storage is an extension point and is not enabled in V1.")

    def delete(self, key: str) -> None:
        raise NotImplementedError("S3 storage is an extension point and is not enabled in V1.")

    def public_url(self, key: str) -> str:
        raise NotImplementedError("S3 storage is an extension point and is not enabled in V1.")


class FirebaseStorage(StorageBackend):
    """Firebase / GCS object storage via Admin SDK (Phase 2).

    Stores only confirmed ANPR evidence (event snapshot + plate/vehicle crops).
    Binary blobs must NOT be written into Firestore documents.
    Browser access stays via authenticated `/api/v1/storage/{key}` (no public bucket ACL).
    """

    def __init__(self, bucket: Any | None = None) -> None:
        require_firebase_admin_for("Firebase Storage")
        settings = get_settings()
        bucket_name = (settings.firebase_storage_bucket or "").strip()
        if not bucket_name:
            raise ValidationAppError(
                "FIREBASE_STORAGE_BUCKET is required when STORAGE_PROVIDER=firebase.",
            )
        self._bucket_name = bucket_name
        if bucket is not None:
            self._bucket = bucket
        else:
            self._bucket = self._open_bucket(bucket_name)

    @staticmethod
    def _open_bucket(bucket_name: str) -> Any:
        from app.firebase.admin import get_firebase_admin_app

        get_firebase_admin_app()
        try:
            from firebase_admin import storage as fb_storage
        except ImportError as exc:  # pragma: no cover
            raise ValidationAppError(
                "firebase-admin is not installed. "
                "pip install -r requirements-firebase.txt when enabling STORAGE_PROVIDER=firebase.",
            ) from exc
        return fb_storage.bucket(bucket_name)

    def _blob(self, key: str) -> Any:
        return self._bucket.blob(normalize_storage_key(key))

    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        safe = normalize_storage_key(key)
        blob = self._blob(safe)
        blob.upload_from_string(data, content_type=content_type)
        logger.info(
            "storage.save",
            provider="firebase",
            bucket=self._bucket_name,
            key=safe,
            bytes=len(data),
            content_type=content_type,
        )
        return safe

    def get_bytes(self, key: str) -> bytes | None:
        safe = normalize_storage_key(key)
        blob = self._blob(safe)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def delete(self, key: str) -> None:
        safe = normalize_storage_key(key)
        blob = self._blob(safe)
        if blob.exists():
            blob.delete()
            logger.info("storage.delete", provider="firebase", key=safe)

    def public_url(self, key: str) -> str:
        # Keep org-scoped auth on the API; do not expose world-readable GCS URLs by default.
        return f"/api/v1/storage/{quote(normalize_storage_key(key))}"


_storage: StorageBackend | None = None


def reset_storage_cache() -> None:
    """Test helper."""
    global _storage
    _storage = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        provider = normalize_storage_provider(get_settings().storage_provider)
        if provider == "local":
            _storage = LocalFilesystemStorage()
        elif provider == "s3":
            raise RuntimeError(
                "S3 storage provider is configured but not implemented in V1. Use STORAGE_PROVIDER=local.",
            )
        elif provider == "firebase":
            _storage = FirebaseStorage()
        else:
            raise RuntimeError(f"Unknown STORAGE_PROVIDER: {provider}")
    return _storage
