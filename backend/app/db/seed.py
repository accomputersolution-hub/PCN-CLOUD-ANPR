from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.camera import Camera
from app.models.enums import CameraStatus, Direction, GateMode, SourceType, StreamType, UserRole
from app.models.gate import Gate
from app.models.organization import Organization
from app.models.site import DEFAULT_SITE_SETTINGS, Site, UserSiteAccess
from app.models.user import User
from app.services.event import ingest_event

logger = get_logger(__name__)

DEMO_PASSWORD = "ChangeMe@12345"

# Old reserved-TLD demo emails → EmailStr-valid @pcncloud.in addresses.
DEMO_EMAIL_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("admin@pcncloud.local", "admin@pcncloud.in"),
    ("orgadmin@hotela.local", "orgadmin@pcncloud.in"),
    ("manager@hotela.local", "manager@pcncloud.in"),
    ("guard@hotela.local", "guard@pcncloud.in"),
    ("viewer@hotela.local", "viewer@pcncloud.in"),
    ("admin@societyb.local", "societyadmin@pcncloud.in"),
)


async def ensure_demo_users(db: AsyncSession) -> dict[str, list[str]]:
    """Idempotent demo-user email/password upsert for existing development databases.

    - Renames known .local demo emails to @pcncloud.in when the target is free.
    - Always resets mapped demo passwords to DEMO_PASSWORD via the project hasher.
    - Preserves user id, role, organization_id, and site access rows.
    - Safe to run on every startup; does not create duplicates or wipe data.
    """
    updated: list[str] = []
    password_reset: list[str] = []
    skipped: list[str] = []

    for old_email, new_email in DEMO_EMAIL_MIGRATIONS:
        pwd_hash = hash_password(DEMO_PASSWORD)
        old_user = (await db.execute(select(User).where(User.email == old_email))).scalar_one_or_none()
        new_user = (await db.execute(select(User).where(User.email == new_email))).scalar_one_or_none()

        if old_user is not None and new_user is None:
            old_user.email = new_email
            old_user.hashed_password = pwd_hash
            old_user.is_active = True
            updated.append(f"{old_email}->{new_email}")
            password_reset.append(new_email)
            continue

        if old_user is not None and new_user is not None and old_user.id != new_user.id:
            # Target already exists; keep both rows but ensure the new address can log in.
            new_user.hashed_password = pwd_hash
            new_user.is_active = True
            password_reset.append(new_email)
            skipped.append(f"kept both {old_email} and {new_email}")
            continue

        target = new_user or old_user
        if target is None:
            skipped.append(f"missing {old_email}/{new_email}")
            continue

        if target.email != new_email:
            previous = target.email
            target.email = new_email
            updated.append(f"{previous}->{new_email}")

        target.hashed_password = pwd_hash
        target.is_active = True
        password_reset.append(new_email)

    await db.commit()
    result = {"updated": updated, "password_reset": password_reset, "skipped": skipped}
    logger.info("demo_users.ensured", **result)
    return result


async def seed_if_empty(db: AsyncSession) -> None:
    existing = (await db.execute(select(User).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    logger.info("seeding.demo_data")

    hotel = Organization(name="Hotel A", slug="hotel-a", retention_days=90)
    society = Organization(name="Society B", slug="society-b", retention_days=30)
    db.add_all([hotel, society])
    await db.flush()

    lonavala = Site(
        organization_id=hotel.id,
        name="Lonavala Site",
        address="Old Mumbai-Pune Hwy, Lonavala, Maharashtra",
        timezone="Asia/Kolkata",
        settings={**DEFAULT_SITE_SETTINGS},
    )
    pune = Site(
        organization_id=society.id,
        name="Pune Site",
        address="Baner, Pune, Maharashtra",
        timezone="Asia/Kolkata",
        settings={**DEFAULT_SITE_SETTINGS},
    )
    db.add_all([lonavala, pune])
    await db.flush()

    hotel_entry = Gate(organization_id=hotel.id, site_id=lonavala.id, name="Entry Gate", mode=GateMode.ENTRY)
    hotel_exit = Gate(organization_id=hotel.id, site_id=lonavala.id, name="Exit Gate", mode=GateMode.EXIT)
    society_g1 = Gate(organization_id=society.id, site_id=pune.id, name="Gate 1", mode=GateMode.MIXED)
    society_g2 = Gate(organization_id=society.id, site_id=pune.id, name="Gate 2", mode=GateMode.MIXED)
    db.add_all([hotel_entry, hotel_exit, society_g1, society_g2])
    await db.flush()

    cam1 = Camera(
        organization_id=hotel.id,
        site_id=lonavala.id,
        gate_id=hotel_entry.id,
        name="Camera 1",
        camera_code="HOTEL-A-ENTRY-01",
        direction=Direction.ENTRY,
        rtsp_url_encrypted=encrypt_secret("rtsp://demo.invalid/entry"),
        onvif_ip="10.10.1.11",
        username_encrypted=encrypt_secret("demo"),
        password_encrypted=encrypt_secret("demo-password"),
        stream_type=StreamType.RTSP,
        resolution="1920x1080",
        enabled=True,
        status=CameraStatus.ONLINE,
        last_heartbeat=datetime.now(UTC),
        fps=12.0,
        last_frame_at=datetime.now(UTC),
    )
    cam2 = Camera(
        organization_id=hotel.id,
        site_id=lonavala.id,
        gate_id=hotel_exit.id,
        name="Camera 2",
        camera_code="HOTEL-A-EXIT-01",
        direction=Direction.EXIT,
        rtsp_url_encrypted=encrypt_secret("rtsp://demo.invalid/exit"),
        onvif_ip="10.10.1.12",
        username_encrypted=encrypt_secret("demo"),
        password_encrypted=encrypt_secret("demo-password"),
        stream_type=StreamType.RTSP,
        resolution="1920x1080",
        enabled=True,
        status=CameraStatus.OFFLINE,
        connection_error="Waiting for edge agent heartbeat",
        retry_count=2,
    )
    cam3 = Camera(
        organization_id=society.id,
        site_id=pune.id,
        gate_id=society_g1.id,
        name="Gate 1 Camera",
        camera_code="SOC-B-G1",
        direction=Direction.BOTH,
        rtsp_url_encrypted=encrypt_secret("rtsp://demo.invalid/g1"),
        stream_type=StreamType.RTSP,
        enabled=True,
        status=CameraStatus.UNKNOWN,
    )
    db.add_all([cam1, cam2, cam3])
    await db.flush()

    pwd = hash_password(DEMO_PASSWORD)
    super_admin = User(
        email="admin@pcncloud.in",
        hashed_password=pwd,
        full_name="PCN Super Admin",
        role=UserRole.SUPER_ADMIN,
        organization_id=None,
    )
    org_admin = User(
        email="orgadmin@pcncloud.in",
        hashed_password=pwd,
        full_name="Hotel A Admin",
        role=UserRole.ORG_ADMIN,
        organization_id=hotel.id,
    )
    manager = User(
        email="manager@pcncloud.in",
        hashed_password=pwd,
        full_name="Lonavala Site Manager",
        role=UserRole.SITE_MANAGER,
        organization_id=hotel.id,
    )
    guard = User(
        email="guard@pcncloud.in",
        hashed_password=pwd,
        full_name="Entry Gate Guard",
        role=UserRole.SECURITY_GUARD,
        organization_id=hotel.id,
    )
    viewer = User(
        email="viewer@pcncloud.in",
        hashed_password=pwd,
        full_name="Hotel Viewer",
        role=UserRole.VIEWER,
        organization_id=hotel.id,
    )
    society_admin = User(
        email="societyadmin@pcncloud.in",
        hashed_password=pwd,
        full_name="Society B Admin",
        role=UserRole.ORG_ADMIN,
        organization_id=society.id,
    )
    db.add_all([super_admin, org_admin, manager, guard, viewer, society_admin])
    await db.flush()
    db.add_all(
        [
            UserSiteAccess(user_id=manager.id, site_id=lonavala.id),
            UserSiteAccess(user_id=guard.id, site_id=lonavala.id),
            UserSiteAccess(user_id=viewer.id, site_id=lonavala.id),
        ]
    )

    tz = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz)
    samples = [
        ("MH12AB1234", Direction.ENTRY, cam1, timedelta(hours=3, minutes=10)),
        ("MH12AB1234", Direction.EXIT, cam2, timedelta(minutes=20)),
        ("MH14CD5678", Direction.ENTRY, cam1, timedelta(hours=1, minutes=5)),
        ("MH04EF9012", Direction.ENTRY, cam1, timedelta(minutes=42)),
        ("MH12GH3456", Direction.EXIT, cam2, timedelta(hours=2)),
        ("MH20JK7788", Direction.ENTRY, cam1, timedelta(minutes=8)),
        ("MH12AB1234", Direction.ENTRY, cam1, timedelta(days=1, hours=2)),
        ("MH12AB1234", Direction.EXIT, cam2, timedelta(days=1, hours=1)),
    ]
    await db.flush()
    for plate, direction, camera, delta in samples:
        ts = (now - delta).astimezone(UTC)
        await ingest_event(
            db,
            camera=camera,
            site=lonavala,
            plate_text=plate,
            direction=direction,
            ocr_confidence=0.94,
            plate_detection_confidence=0.91,
            vehicle_detection_confidence=0.88,
            timestamp=ts,
            source_type=SourceType.MOCK,
            event_id=str(uuid4()),
            force=True,
        )
    await db.commit()
    logger.info("seeding.complete")
