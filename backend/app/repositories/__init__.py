"""Persistence factory. PostgreSQL/SQLite today; Firestore adapter structure ready."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.core.providers import normalize_datastore_provider, require_sqlalchemy_datastore
from app.repositories import firestore_store
from app.repositories.sqlalchemy_store import (
    SqlAlchemyGatewayRepository,
    SqlAlchemyNvrRepository,
    SqlAlchemySiteConnectivityRepository,
)


def gateway_repo(db: AsyncSession | None = None) -> SqlAlchemyGatewayRepository | firestore_store.FirestoreGatewayRepository:
    provider = normalize_datastore_provider(get_settings().datastore_provider)
    if provider == "firestore":
        return firestore_store.FirestoreGatewayRepository()
    require_sqlalchemy_datastore()
    if db is None:
        raise ValidationAppError("SQLAlchemy gateway repository requires a database session")
    return SqlAlchemyGatewayRepository(db)


def nvr_repo(db: AsyncSession | None = None) -> SqlAlchemyNvrRepository | firestore_store.FirestoreNvrRepository:
    provider = normalize_datastore_provider(get_settings().datastore_provider)
    if provider == "firestore":
        return firestore_store.FirestoreNvrRepository()
    require_sqlalchemy_datastore()
    if db is None:
        raise ValidationAppError("SQLAlchemy NVR repository requires a database session")
    return SqlAlchemyNvrRepository(db)


def site_connectivity_repo(
    db: AsyncSession | None = None,
) -> SqlAlchemySiteConnectivityRepository | firestore_store.FirestoreSiteConnectivityRepository:
    provider = normalize_datastore_provider(get_settings().datastore_provider)
    if provider == "firestore":
        return firestore_store.FirestoreSiteConnectivityRepository()
    require_sqlalchemy_datastore()
    if db is None:
        raise ValidationAppError("SQLAlchemy site connectivity repository requires a database session")
    return SqlAlchemySiteConnectivityRepository(db)
