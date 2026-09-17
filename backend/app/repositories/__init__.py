"""Persistence factory. PostgreSQL/SQLite default; Firestore when explicitly selected."""

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


def _is_firestore() -> bool:
    return normalize_datastore_provider(get_settings().datastore_provider) == "firestore"


def gateway_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreGatewayRepository()
    require_sqlalchemy_datastore()
    if db is None:
        raise ValidationAppError("SQLAlchemy gateway repository requires a database session")
    return SqlAlchemyGatewayRepository(db)


def nvr_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreNvrRepository()
    require_sqlalchemy_datastore()
    if db is None:
        raise ValidationAppError("SQLAlchemy NVR repository requires a database session")
    return SqlAlchemyNvrRepository(db)


def site_connectivity_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreSiteConnectivityRepository()
    require_sqlalchemy_datastore()
    if db is None:
        raise ValidationAppError("SQLAlchemy site connectivity repository requires a database session")
    return SqlAlchemySiteConnectivityRepository(db)


def organization_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreOrganizationRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "organization_repo is Firestore-backed; use SQLAlchemy Organization models when DATASTORE_PROVIDER=sqlalchemy",
    )


def site_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreSiteRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "site_repo is Firestore-backed; use SQLAlchemy Site models when DATASTORE_PROVIDER=sqlalchemy",
    )


def gate_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreGateRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "gate_repo is Firestore-backed; use SQLAlchemy Gate models when DATASTORE_PROVIDER=sqlalchemy",
    )


def camera_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreCameraRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "camera_repo is Firestore-backed; use SQLAlchemy Camera models when DATASTORE_PROVIDER=sqlalchemy",
    )


def vehicle_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreVehicleRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "vehicle_repo is Firestore-backed; use SQLAlchemy Vehicle models when DATASTORE_PROVIDER=sqlalchemy",
    )


def anpr_event_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreAnprEventRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "anpr_event_repo is Firestore-backed; use SQLAlchemy AnprEvent models when DATASTORE_PROVIDER=sqlalchemy",
    )


def user_profile_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreUserProfileRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "user_profile_repo is Firestore-backed; use SQLAlchemy User models when DATASTORE_PROVIDER=sqlalchemy",
    )


def edge_agent_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreEdgeAgentRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "edge_agent_repo is Firestore-backed; use SQLAlchemy EdgeAgent models when DATASTORE_PROVIDER=sqlalchemy",
    )


def visit_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreVisitRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "visit_repo is Firestore-backed; use SQLAlchemy Visit models when DATASTORE_PROVIDER=sqlalchemy",
    )


def refresh_token_repo(db: AsyncSession | None = None):
    if _is_firestore():
        return firestore_store.FirestoreRefreshTokenRepository()
    require_sqlalchemy_datastore()
    raise ValidationAppError(
        "refresh_token_repo is Firestore-backed; use SQLAlchemy RefreshToken models when DATASTORE_PROVIDER=sqlalchemy",
    )
