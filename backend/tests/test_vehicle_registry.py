"""Feature 3: site vehicle registry — exact match, isolation, redaction."""

from __future__ import annotations

import pytest

from app.models.enums import UserRole, VehicleRegistryCategory
from app.services.vehicle_registry import (
    can_lookup_plate,
    match_from_registration,
    redact_registration,
)
from app.services.plate import matches_indian_plate, normalize_plate


def test_can_lookup_requires_pattern_and_confidence():
    assert can_lookup_plate(matches_pattern=True, ocr_confidence=0.9, min_confidence=0.7)
    assert not can_lookup_plate(matches_pattern=True, ocr_confidence=0.5, min_confidence=0.7)
    assert not can_lookup_plate(matches_pattern=False, ocr_confidence=0.99, min_confidence=0.7)


def test_garbage_plate_rejected_by_normalizer():
    # Short / invalid RTO patterns must not be treated as matchable plates
    assert not matches_indian_plate("MH2F22")
    assert not matches_indian_plate("MN2F2")
    norm = normalize_plate("MH2F22")
    assert not norm.matches_known_pattern


def test_valid_plate_normalizes_for_exact_match():
    norm = normalize_plate("mh12 ab 5687")
    assert norm.normalized == "MH12AB5687"
    assert norm.matches_known_pattern


def test_unknown_when_no_registration():
    m = match_from_registration(None, plate_normalized="MH12AB5687", role=UserRole.SITE_MANAGER)
    assert m["known"] is False
    assert m["status"] == "unknown"


def test_known_resident_match():
    reg = {
        "id": "r1",
        "organization_id": "o1",
        "site_id": "s1",
        "plate_normalized": "MH12AB5687",
        "category": VehicleRegistryCategory.RESIDENT,
        "person_name": "Mohammed",
        "flat_room_unit": "B-204",
        "mobile_number": "9999999999",
        "notes": "vip",
        "active": True,
    }
    m = match_from_registration(reg, plate_normalized="MH12AB5687", role=UserRole.SITE_MANAGER)
    assert m["known"] is True
    assert m["status"] == "resident"
    assert m["person_name"] == "Mohammed"
    assert m["flat_room_unit"] == "B-204"
    assert m["mobile_number"] == "9999999999"


def test_known_guest_and_staff():
    for cat in (VehicleRegistryCategory.GUEST, VehicleRegistryCategory.STAFF):
        reg = {
            "id": "r2",
            "organization_id": "o1",
            "site_id": "s1",
            "plate_normalized": "MH14XY1234",
            "category": cat,
            "person_name": "A",
            "active": True,
        }
        m = match_from_registration(reg, plate_normalized="MH14XY1234", role=UserRole.ORG_ADMIN)
        assert m["known"] is True
        assert m["status"] == cat


def test_disabled_registration_is_unknown():
    reg = {
        "id": "r3",
        "organization_id": "o1",
        "site_id": "s1",
        "plate_normalized": "MH12AB5687",
        "category": "resident",
        "person_name": "X",
        "active": False,
    }
    m = match_from_registration(reg, plate_normalized="MH12AB5687", role=UserRole.SITE_MANAGER)
    assert m["known"] is False
    assert m["status"] == "unknown"
    assert m["registry_status"] == "unknown"


def test_event_display_shows_inactive_with_details():
    from app.services.vehicle_registry import match_for_event_display

    reg = {
        "id": "r3",
        "organization_id": "o1",
        "site_id": "s1",
        "plate_normalized": "MH12AB5687",
        "category": "resident",
        "person_name": "Priya",
        "flat_room_unit": "A-101",
        "mobile_number": "999",
        "active": False,
    }
    m = match_for_event_display(reg, plate_normalized="MH12AB5687", role=UserRole.SITE_MANAGER)
    assert m["known"] is True
    assert m["registry_status"] == "inactive"
    assert m["person_name"] == "Priya"
    assert m["flat_room_unit"] == "A-101"
    assert m["category"] == "resident"
    assert m["active"] is False


def test_event_display_unknown_has_no_invented_pii():
    from app.services.vehicle_registry import match_for_event_display

    m = match_for_event_display(None, plate_normalized="MH12AB5687", role=UserRole.SITE_MANAGER)
    assert m["registry_status"] == "unknown"
    assert m["person_name"] is None
    assert m["flat_room_unit"] is None
    assert m["category"] is None


def test_event_display_active_resident():
    from app.services.vehicle_registry import match_for_event_display

    reg = {
        "id": "r1",
        "organization_id": "o1",
        "site_id": "s1",
        "plate_normalized": "MH12AB5687",
        "category": "resident",
        "person_name": "Mohammed",
        "flat_room_unit": "B-204",
        "active": True,
    }
    m = match_for_event_display(reg, plate_normalized="MH12AB5687", role=UserRole.SITE_MANAGER)
    assert m["registry_status"] == "active"
    assert m["known"] is True
    assert m["person_name"] == "Mohammed"
    assert m["status"] == "resident"


def test_viewer_redaction_hides_pii():
    reg = {
        "id": "r4",
        "organization_id": "o1",
        "site_id": "s1",
        "plate_normalized": "MH12AB5687",
        "category": "resident",
        "person_name": "Secret",
        "mobile_number": "111",
        "flat_room_unit": "A-1",
        "notes": "private",
        "active": True,
    }
    out = redact_registration(reg, UserRole.VIEWER)
    assert out["plate_normalized"] == "MH12AB5687"
    assert out["category"] == "resident"
    assert out["person_name"] is None
    assert out["mobile_number"] is None
    assert out["flat_room_unit"] is None
    assert out["notes"] is None


def test_guard_redaction_hides_mobile_and_notes():
    reg = {
        "id": "r5",
        "organization_id": "o1",
        "site_id": "s1",
        "plate_normalized": "MH12AB5687",
        "category": "guest",
        "person_name": "Guest",
        "mobile_number": "111",
        "flat_room_unit": "Lobby",
        "notes": "private",
        "active": True,
    }
    out = redact_registration(reg, UserRole.SECURITY_GUARD)
    assert out["person_name"] == "Guest"
    assert out["flat_room_unit"] == "Lobby"
    assert out["mobile_number"] is None
    assert out["notes"] is None


def test_rbac_registry_write_roles():
    from app.core.rbac import Permission, has_permission

    assert has_permission(UserRole.ORG_ADMIN, Permission.VEHICLE_REGISTRY_WRITE)
    assert has_permission(UserRole.SITE_MANAGER, Permission.VEHICLE_REGISTRY_WRITE)
    assert has_permission(UserRole.SECURITY_GUARD, Permission.VEHICLE_REGISTRY_WRITE)
    assert not has_permission(UserRole.VIEWER, Permission.VEHICLE_REGISTRY_WRITE)


@pytest.mark.asyncio
async def test_registry_sql_site_isolation_and_categories(session, world, monkeypatch):
    """SQL path: site isolation, categories, disable, garbage plate (no HTTP/Firebase)."""
    monkeypatch.setenv("DATASTORE_PROVIDER", "sqlalchemy")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.services.vehicle_registry.is_firestore", lambda: False)

    from app.core.exceptions import ForbiddenError
    from app.models.site import Site, DEFAULT_SITE_SETTINGS
    from app.services import vehicle_registry as reg_svc
    from app.schemas.vehicle_registry import VehicleRegistryCreate, VehicleRegistryUpdate
    from app.services.tenant import TenantContext

    org_admin = world["org_admin"]
    guard = world["guard"]
    site_a = world["site_a"]
    site_a2 = Site(
        organization_id=world["org_a"].id,
        name="Wing B",
        address="B",
        timezone="Asia/Kolkata",
        settings={**DEFAULT_SITE_SETTINGS},
    )
    session.add(site_a2)
    await session.commit()
    await session.refresh(site_a2)

    ctx = TenantContext(org_admin, site_ids=[])
    created = await reg_svc.create_registration(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a.id,
        body=VehicleRegistryCreate(
            plate="MH12AB5687",
            category=VehicleRegistryCategory.RESIDENT,
            person_name="Mohammed",
            flat_room_unit="B-204",
            mobile_number="9999999999",
        ),
    )
    await session.commit()
    assert created["plate_normalized"] == "MH12AB5687"
    assert created["category"] == "resident"
    reg_id = created["id"]

    for plate, cat, name in (
        ("KA05KP7941", VehicleRegistryCategory.STAFF, "Staffer"),
        ("TN51Y6552", VehicleRegistryCategory.GUEST, "Visitor"),
    ):
        await reg_svc.create_registration(
            db=session,
            ctx=ctx,
            user=org_admin,
            site_id=site_a.id,
            body=VehicleRegistryCreate(plate=plate, category=cat, person_name=name),
        )
    await session.commit()

    hit = await reg_svc.lookup_plate(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a.id,
        plate="MH12AB5687",
        ocr_confidence=0.95,
        matches_pattern=True,
    )
    assert hit["known"] is True
    assert hit["status"] == "resident"
    assert hit["person_name"] == "Mohammed"

    miss_site = await reg_svc.lookup_plate(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a2.id,
        plate="MH12AB5687",
        ocr_confidence=0.95,
        matches_pattern=True,
    )
    assert miss_site["known"] is False

    unk = await reg_svc.lookup_plate(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a.id,
        plate="MH14XY1234",
        ocr_confidence=0.95,
        matches_pattern=True,
    )
    assert unk["known"] is False

    garbage = await reg_svc.lookup_plate(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a.id,
        plate="MH2F22",
        ocr_confidence=0.99,
        matches_pattern=False,
    )
    assert garbage["known"] is False

    _, disabled = await reg_svc.update_registration(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a.id,
        registration_id=reg_id,
        body=VehicleRegistryUpdate(active=False),
    )
    await session.commit()
    assert disabled is True
    after = await reg_svc.lookup_plate(
        db=session,
        ctx=ctx,
        user=org_admin,
        site_id=site_a.id,
        plate="MH12AB5687",
        ocr_confidence=0.95,
        matches_pattern=True,
    )
    assert after["known"] is False

    gctx = TenantContext(guard, site_ids=[site_a.id])
    with pytest.raises(ForbiddenError):
        await reg_svc.create_registration(
            db=session,
            ctx=gctx,
            user=guard,
            site_id=site_a.id,
            body=VehicleRegistryCreate(
                plate="MH20DV2366",
                category=VehicleRegistryCategory.RESIDENT,
                person_name="Nope",
            ),
        )
    guest = await reg_svc.create_registration(
        db=session,
        ctx=gctx,
        user=guard,
        site_id=site_a.id,
        body=VehicleRegistryCreate(
            plate="MH20DV2366",
            category=VehicleRegistryCategory.GUEST,
            person_name="Temp Guest",
        ),
    )
    await session.commit()
    assert guest["category"] == "guest"
