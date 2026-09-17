from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "PCN Cloud ANPR"
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    seed_demo_data: bool = True

    # Empty by default — not required when DATASTORE_PROVIDER=firestore.
    # Legacy SQLAlchemy path only: set DATABASE_URL when DATASTORE_PROVIDER=sqlalchemy.
    database_url: str = ""

    jwt_secret: str = "change-me-dev-jwt-secret-not-for-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    credentials_encryption_key: str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:8080"

    storage_provider: str = "firebase"
    storage_path: str = "./data/storage"

    redis_url: str | None = None

    # Auth: firebase (default). jwt = legacy PostgreSQL users + JWT.
    auth_provider: str = "firebase"

    # Persistence: firestore (default). sqlalchemy|postgres = legacy SQL path.
    datastore_provider: str = "firestore"

    # Firebase (server). Never commit real service-account JSON.
    firebase_project_id: str = ""
    firebase_storage_bucket: str = ""
    firebase_credentials_file: str = ""
    # Optional inline JSON for CI only; prefer FIREBASE_CREDENTIALS_FILE locally.
    firebase_credentials_json: str = ""

    # Firebase web client config (for SPA SDK). Safe to expose project public keys;
    # still do not put Admin credentials here.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_app_id: str = ""
    firebase_messaging_sender_id: str = ""
    firebase_measurement_id: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        if not (self.database_url or "").strip():
            return False
        return self.database_url.startswith("sqlite")

    @property
    def storage_dir(self) -> Path:
        return Path(self.storage_path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
