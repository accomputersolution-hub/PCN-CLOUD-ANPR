from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SEED_DEMO_DATA", "false")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-use")
os.environ.setdefault("CREDENTIALS_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("STORAGE_PATH", str(Path("./data/test-storage").resolve()))

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.camera import Camera
from app.models.enums import CameraStatus, Direction, GateMode, StreamType, UserRole
from app.models.gate import Gate
from app.models.organization import Organization
from app.models.site import DEFAULT_SITE_SETTINGS, Site
from app.models.user import User

get_settings.cache_clear()


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def world(session: AsyncSession) -> dict:
    org_a = Organization(name="Hotel A", slug="hotel-a", retention_days=90)
    org_b = Organization(name="Society B", slug="society-b", retention_days=30)
    session.add_all([org_a, org_b])
    await session.flush()
    site_a = Site(
        organization_id=org_a.id,
        name="Lonavala",
        address="Lonavala",
        timezone="Asia/Kolkata",
        settings={**DEFAULT_SITE_SETTINGS, "duplicate_window_seconds": 30, "event_cooldown_seconds": 60},
    )
    site_b = Site(
        organization_id=org_b.id,
        name="Pune",
        address="Pune",
        timezone="Asia/Kolkata",
        settings={**DEFAULT_SITE_SETTINGS},
    )
    session.add_all([site_a, site_b])
    await session.flush()
    gate_a = Gate(organization_id=org_a.id, site_id=site_a.id, name="Entry Gate", mode=GateMode.ENTRY)
    gate_ax = Gate(organization_id=org_a.id, site_id=site_a.id, name="Exit Gate", mode=GateMode.EXIT)
    gate_b = Gate(organization_id=org_b.id, site_id=site_b.id, name="Gate 1", mode=GateMode.MIXED)
    session.add_all([gate_a, gate_ax, gate_b])
    await session.flush()
    cam_in = Camera(
        organization_id=org_a.id,
        site_id=site_a.id,
        gate_id=gate_a.id,
        name="Entry Cam",
        camera_code="A-IN",
        direction=Direction.ENTRY,
        stream_type=StreamType.RTSP,
        status=CameraStatus.ONLINE,
        enabled=True,
        rtsp_url_encrypted=encrypt_secret("rtsp://10.0.0.10/stream1"),
    )
    cam_out = Camera(
        organization_id=org_a.id,
        site_id=site_a.id,
        gate_id=gate_ax.id,
        name="Exit Cam",
        camera_code="A-OUT",
        direction=Direction.EXIT,
        stream_type=StreamType.RTSP,
        status=CameraStatus.ONLINE,
        enabled=True,
        rtsp_url_encrypted=encrypt_secret("rtsp://10.0.0.11/stream1"),
    )
    cam_b = Camera(
        organization_id=org_b.id,
        site_id=site_b.id,
        gate_id=gate_b.id,
        name="Society Cam",
        camera_code="B-1",
        direction=Direction.BOTH,
        stream_type=StreamType.RTSP,
        status=CameraStatus.ONLINE,
        enabled=True,
        rtsp_url_encrypted=encrypt_secret("rtsp://10.0.0.20/stream1"),
    )
    session.add_all([cam_in, cam_out, cam_b])
    pwd = hash_password("ChangeMe@12345")
    super_admin = User(email="admin@pcncloud.in", hashed_password=pwd, full_name="Super", role=UserRole.SUPER_ADMIN)
    org_admin = User(
        email="orgadmin@pcncloud.in",
        hashed_password=pwd,
        full_name="Org Admin",
        role=UserRole.ORG_ADMIN,
        organization_id=org_a.id,
    )
    guard = User(
        email="guard@pcncloud.in",
        hashed_password=pwd,
        full_name="Guard",
        role=UserRole.SECURITY_GUARD,
        organization_id=org_a.id,
    )
    other_admin = User(
        email="societyadmin@pcncloud.in",
        hashed_password=pwd,
        full_name="Other",
        role=UserRole.ORG_ADMIN,
        organization_id=org_b.id,
    )
    session.add_all([super_admin, org_admin, guard, other_admin])
    await session.commit()
    return {
        "org_a": org_a,
        "org_b": org_b,
        "site_a": site_a,
        "cam_in": cam_in,
        "cam_out": cam_out,
        "cam_b": cam_b,
        "super": super_admin,
        "org_admin": org_admin,
        "guard": guard,
        "other_admin": other_admin,
    }


async def login(client: AsyncClient, email: str, password: str = "ChangeMe@12345") -> str:
    res = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]
