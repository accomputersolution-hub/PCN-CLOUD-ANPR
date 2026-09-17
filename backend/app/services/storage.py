from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageBackend(ABC):
    """Object storage abstraction. Swap local for S3-compatible later."""

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
        safe = key.lstrip("/").replace("..", "")
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.info("storage.save", key=key, bytes=len(data), content_type=content_type)
        return key

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
        return f"/api/v1/storage/{quote(key)}"


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


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        provider = get_settings().storage_provider.lower()
        if provider == "local":
            _storage = LocalFilesystemStorage()
        elif provider in {"s3", "minio"}:
            raise RuntimeError("S3 storage provider is configured but not implemented in V1. Use STORAGE_PROVIDER=local.")
        else:
            raise RuntimeError(f"Unknown STORAGE_PROVIDER: {provider}")
    return _storage
