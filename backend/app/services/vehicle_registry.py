"""Site vehicle registry (Feature 3) — exact plate match, site-scoped."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.rbac import Permission, has_permission
from app.core.runtime import is_firestore
from app.domain.records import SiteVehicleRegistrationRecord
from app.models.enums import UserRole, VehicleRegistryCategory
from app.models.site import DEFAULT_SITE_SETTINGS, Site
from app.models.site_vehicle_registration import SiteVehicleRegistration
from app.models.vehicle import Vehicle
from app.services.plate import matches_indian_plate, normalize_plate
from app.services.tenant import TenantContext


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _role(user: Any) -> UserRole:
    raw = getattr(user, "role", None)
    if isinstance(raw, UserRole):
        return raw
    return UserRole(str(raw))


def _is_manager_plus(role: UserRole) -> bool:
    return role in {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN, UserRole.SITE_MANAGER}


def _is_viewer(role: UserRole) -> bool:
    return role == UserRole.VIEWER


def redact_registration(record: dict[str, Any] | SiteVehicleRegistrationRecord, role: UserRole) -> dict[str, Any]:
    """PII redaction by role — VIEWER sees plate/category/active only."""
    if hasattr(record, "model_dump"):
        data = record.model_dump()
    else:
        data = dict(record)
    if _is_viewer(role):
        return {
            "id": data.get("id"),
            "organization_id": data.get("organization_id"),
            "site_id": data.get("site_id"),
            "plate_normalized": data.get("plate_normalized"),
            "vehicle_id": data.get("vehicle_id"),
            "category": data.get("category"),
            "person_name": None,
            "mobile_number": None,
            "flat_room_unit": None,
            "notes": None,
            "active": data.get("active", True),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }
    if not _is_manager_plus(role):
        # Security guard: name + flat + category, no mobile/notes
        data["mobile_number"] = None
        data["notes"] = None
    return data


def match_from_registration(
    reg: SiteVehicleRegistrationRecord | dict[str, Any] | None,
    *,
    plate_normalized: str,
    role: UserRole,
) -> dict[str, Any]:
    """Access / ANPR semantics: inactive registrations collapse to unknown."""
    if reg is None:
        return {
            "known": False,
            "status": "unknown",
            "registry_status": "unknown",
            "plate_normalized": plate_normalized,
            "person_name": None,
            "flat_room_unit": None,
            "mobile_number": None,
            "registration_id": None,
            "active": None,
            "category": None,
        }
    data = redact_registration(reg, role)
    active = bool(data.get("active", True))
    if not active:
        return {
            "known": False,
            "status": "unknown",
            "registry_status": "unknown",
            "plate_normalized": plate_normalized,
            "person_name": None,
            "flat_room_unit": None,
            "mobile_number": None,
            "registration_id": data.get("id"),
            "active": False,
            "category": None,
        }
    cat = str(data.get("category") or "unknown")
    return {
        "known": True,
        "status": cat,
        "registry_status": "active",
        "plate_normalized": data.get("plate_normalized") or plate_normalized,
        "person_name": data.get("person_name"),
        "flat_room_unit": data.get("flat_room_unit"),
        "mobile_number": data.get("mobile_number"),
        "registration_id": data.get("id"),
        "active": True,
        "category": cat,
    }


def match_for_event_display(
    reg: SiteVehicleRegistrationRecord | dict[str, Any] | None,
    *,
    plate_normalized: str,
    role: UserRole,
) -> dict[str, Any]:
    """Events UI semantics: live resolve including inactive rows (name/category/unit + badge).

    Does not snapshot onto AnprEvent — callers attach this at read time so later
    disable/edit of a registration updates historical event display.
    """
    if reg is None:
        return {
            "known": False,
            "status": "unknown",
            "registry_status": "unknown",
            "plate_normalized": plate_normalized,
            "person_name": None,
            "flat_room_unit": None,
            "mobile_number": None,
            "registration_id": None,
            "active": None,
            "category": None,
        }
    data = redact_registration(reg, role)
    active = bool(data.get("active", True))
    cat = str(data.get("category") or "unknown")
    return {
        "known": True,
        "status": cat,
        "registry_status": "active" if active else "inactive",
        "plate_normalized": data.get("plate_normalized") or plate_normalized,
        "person_name": data.get("person_name"),
        "flat_room_unit": data.get("flat_room_unit"),
        "mobile_number": data.get("mobile_number"),
        "registration_id": data.get("id"),
        "active": active,
        "category": cat,
    }


def _reg_key(organization_id: str, site_id: str, plate_normalized: str) -> tuple[str, str, str]:
    return (organization_id, site_id, plate_normalized)


async def load_registrations_for_keys(
    *,
    db: AsyncSession | None,
    keys: set[tuple[str, str, str]],
) -> dict[tuple[str, str, str], SiteVehicleRegistrationRecord]:
    """Batch-load registry rows for (organization_id, site_id, plate_normalized) keys."""
    if not keys:
        return {}
    if is_firestore():
        from app.repositories import vehicle_registry_repo

        out: dict[tuple[str, str, str], SiteVehicleRegistrationRecord] = {}
        # Group by site to reuse list_for_site where possible
        by_site: dict[tuple[str, str], set[str]] = {}
        for org_id, site_id, plate in keys:
            by_site.setdefault((org_id, site_id), set()).add(plate)
        for (org_id, site_id), plates in by_site.items():
            rows = await vehicle_registry_repo().list_for_site(
                organization_id=org_id,
                site_id=site_id,
                limit=max(200, len(plates) + 50),
            )
            for row in rows:
                k = _reg_key(row.organization_id, row.site_id, row.plate_normalized)
                if k in keys:
                    out[k] = row
            # Fallback exact get for any plate still missing (list limit / filter edge)
            for plate in plates:
                k = _reg_key(org_id, site_id, plate)
                if k not in out:
                    found = await vehicle_registry_repo().get_by_plate(
                        organization_id=org_id,
                        site_id=site_id,
                        plate_normalized=plate,
                        active_only=False,
                    )
                    if found:
                        out[k] = found
        return out

    assert db is not None
    org_ids = {k[0] for k in keys}
    site_ids = {k[1] for k in keys}
    plates = {k[2] for k in keys}
    stmt = select(SiteVehicleRegistration).where(
        SiteVehicleRegistration.organization_id.in_(org_ids),
        SiteVehicleRegistration.site_id.in_(site_ids),
        SiteVehicleRegistration.plate_normalized.in_(plates),
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: dict[tuple[str, str, str], SiteVehicleRegistrationRecord] = {}
    wanted = keys
    for row in rows:
        k = _reg_key(row.organization_id, row.site_id, row.plate_normalized)
        if k not in wanted:
            continue
        out[k] = SiteVehicleRegistrationRecord(
            id=row.id,
            organization_id=row.organization_id,
            site_id=row.site_id,
            plate_normalized=row.plate_normalized,
            vehicle_id=row.vehicle_id,
            category=row.category,
            person_name=row.person_name,
            mobile_number=row.mobile_number,
            flat_room_unit=row.flat_room_unit,
            notes=row.notes,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    return out


async def attach_registry_matches_to_event_dicts(
    *,
    db: AsyncSession | None,
    user: Any,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach live registry_match (display semantics) onto serialized event dicts."""
    role = _role(user)
    keys: set[tuple[str, str, str]] = set()
    for item in items:
        org_id = item.get("organization_id")
        site_id = item.get("site_id")
        plate = item.get("plate_normalized")
        if org_id and site_id and plate:
            keys.add(_reg_key(str(org_id), str(site_id), str(plate)))
    regs = await load_registrations_for_keys(db=db, keys=keys)
    for item in items:
        org_id = str(item.get("organization_id") or "")
        site_id = str(item.get("site_id") or "")
        plate = str(item.get("plate_normalized") or "")
        reg = regs.get(_reg_key(org_id, site_id, plate))
        item["registry_match"] = match_for_event_display(reg, plate_normalized=plate, role=role)
    return items


async def plates_matching_registry_filters(
    *,
    db: AsyncSession | None,
    ctx: TenantContext,
    person_name: str | None = None,
    flat_room_unit: str | None = None,
    site_id: str | None = None,
    organization_id: str | None = None,
) -> set[tuple[str, str]] | None:
    """Return (site_id, plate_normalized) pairs matching name/unit, or None if no filters."""
    if not person_name and not flat_room_unit:
        return None
    name_l = (person_name or "").strip()
    flat_l = (flat_room_unit or "").strip()
    if is_firestore():
        from app.repositories import vehicle_registry_repo
        from app.services import firestore_domain as fs

        sites = await fs.list_sites(ctx, organization_id=organization_id)
        pairs: set[tuple[str, str]] = set()
        for site in sites:
            if site_id and site.id != site_id:
                continue
            rows = await vehicle_registry_repo().list_for_site(
                organization_id=site.organization_id,
                site_id=site.id,
                name=name_l or None,
                flat_room_unit=flat_l or None,
                limit=500,
            )
            for r in rows:
                pairs.add((r.site_id, r.plate_normalized))
        return pairs

    assert db is not None
    stmt = select(
        SiteVehicleRegistration.site_id,
        SiteVehicleRegistration.plate_normalized,
    )
    stmt = ctx.apply_org(stmt, SiteVehicleRegistration.organization_id)
    stmt = ctx.apply_site(stmt, SiteVehicleRegistration.site_id)
    if site_id:
        ctx.ensure_site(site_id)
        stmt = stmt.where(SiteVehicleRegistration.site_id == site_id)
    if organization_id:
        ctx.ensure_org(organization_id)
        stmt = stmt.where(SiteVehicleRegistration.organization_id == organization_id)
    if name_l:
        stmt = stmt.where(SiteVehicleRegistration.person_name.ilike(f"%{name_l}%"))
    if flat_l:
        stmt = stmt.where(SiteVehicleRegistration.flat_room_unit.ilike(f"%{flat_l}%"))
    rows = (await db.execute(stmt)).all()
    return {(str(r[0]), str(r[1])) for r in rows}


def can_lookup_plate(*, matches_pattern: bool, ocr_confidence: float, min_confidence: float) -> bool:
    """Exact-match gate: valid Indian pattern + high enough OCR confidence."""
    return bool(matches_pattern) and float(ocr_confidence) >= float(min_confidence)


async def _site_min_confidence(db: AsyncSession | None, site_id: str) -> float:
    if is_firestore():
        from app.repositories import site_repo

        site = await site_repo().get(site_id)
        settings = {**DEFAULT_SITE_SETTINGS, **((site.settings if site else None) or {})}
        return float(settings.get("min_confidence", 0.7))
    assert db is not None
    site = await db.get(Site, site_id)
    settings = {**DEFAULT_SITE_SETTINGS, **((site.settings if site else None) or {})}
    return float(settings.get("min_confidence", 0.7))


def _normalize_for_registry(plate: str) -> str:
    norm = normalize_plate(plate)
    if not norm.normalized or not matches_indian_plate(norm.normalized):
        raise ValidationAppError("Plate must be a valid Indian registration number")
    return norm.normalized


async def get_by_plate_exact(
    *,
    db: AsyncSession | None,
    organization_id: str,
    site_id: str,
    plate_normalized: str,
    active_only: bool = True,
) -> SiteVehicleRegistrationRecord | None:
    if is_firestore():
        from app.repositories import vehicle_registry_repo

        return await vehicle_registry_repo().get_by_plate(
            organization_id=organization_id,
            site_id=site_id,
            plate_normalized=plate_normalized,
            active_only=active_only,
        )
    assert db is not None
    stmt = select(SiteVehicleRegistration).where(
        SiteVehicleRegistration.organization_id == organization_id,
        SiteVehicleRegistration.site_id == site_id,
        SiteVehicleRegistration.plate_normalized == plate_normalized,
    )
    if active_only:
        stmt = stmt.where(SiteVehicleRegistration.active.is_(True))
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        return None
    return SiteVehicleRegistrationRecord(
        id=row.id,
        organization_id=row.organization_id,
        site_id=row.site_id,
        plate_normalized=row.plate_normalized,
        vehicle_id=row.vehicle_id,
        category=row.category,
        person_name=row.person_name,
        mobile_number=row.mobile_number,
        flat_room_unit=row.flat_room_unit,
        notes=row.notes,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def lookup_plate(
    *,
    db: AsyncSession | None,
    ctx: TenantContext,
    user: Any,
    site_id: str,
    plate: str,
    ocr_confidence: float | None = None,
    matches_pattern: bool | None = None,
) -> dict[str, Any]:
    ctx.ensure_site(site_id)
    norm = normalize_plate(plate)
    plate_n = norm.normalized
    pattern_ok = matches_pattern if matches_pattern is not None else bool(norm.matches_known_pattern)
    if not pattern_ok and plate_n:
        pattern_ok = matches_indian_plate(plate_n)
    min_conf = await _site_min_confidence(db, site_id)
    conf = float(ocr_confidence) if ocr_confidence is not None else 1.0
    if not plate_n or not can_lookup_plate(matches_pattern=pattern_ok, ocr_confidence=conf, min_confidence=min_conf):
        return match_from_registration(None, plate_normalized=plate_n or plate.upper(), role=_role(user))

    org_id = ctx.organization_id
    if not org_id:
        # SUPER_ADMIN: resolve org from site
        if is_firestore():
            from app.repositories import site_repo

            site = await site_repo().get(site_id)
            org_id = site.organization_id if site else None
        else:
            assert db is not None
            site = await db.get(Site, site_id)
            org_id = site.organization_id if site else None
    if not org_id:
        raise NotFoundError("Site not found")
    ctx.ensure_org(org_id)

    reg = await get_by_plate_exact(
        db=db,
        organization_id=org_id,
        site_id=site_id,
        plate_normalized=plate_n,
        active_only=False,
    )
    # Inactive → unknown
    if reg and not reg.active:
        return match_from_registration(reg, plate_normalized=plate_n, role=_role(user))
    if reg and reg.active:
        return match_from_registration(reg, plate_normalized=plate_n, role=_role(user))
    return match_from_registration(None, plate_normalized=plate_n, role=_role(user))


async def list_registrations(
    *,
    db: AsyncSession | None,
    ctx: TenantContext,
    user: Any,
    site_id: str,
    q: str | None = None,
    plate: str | None = None,
    name: str | None = None,
    flat_room_unit: str | None = None,
    category: str | None = None,
    active: bool | None = None,
) -> list[dict[str, Any]]:
    ctx.ensure_site(site_id)
    role = _role(user)
    if is_firestore():
        from app.repositories import site_repo, vehicle_registry_repo

        site = await site_repo().get(site_id)
        if not site:
            raise NotFoundError("Site not found")
        ctx.ensure_org(site.organization_id)
        rows = await vehicle_registry_repo().list_for_site(
            organization_id=site.organization_id,
            site_id=site_id,
            q=q,
            plate=plate,
            name=name,
            flat_room_unit=flat_room_unit,
            category=category,
            active=active,
        )
        return [redact_registration(r, role) for r in rows]

    assert db is not None
    site = await db.get(Site, site_id)
    if not site:
        raise NotFoundError("Site not found")
    ctx.ensure_org(site.organization_id)
    stmt = select(SiteVehicleRegistration).where(
        SiteVehicleRegistration.organization_id == site.organization_id,
        SiteVehicleRegistration.site_id == site_id,
    )
    if active is not None:
        stmt = stmt.where(SiteVehicleRegistration.active.is_(active))
    if category:
        stmt = stmt.where(SiteVehicleRegistration.category == category)
    if plate:
        compact = normalize_plate(plate).normalized
        stmt = stmt.where(SiteVehicleRegistration.plate_normalized.contains(compact))
    if name:
        stmt = stmt.where(SiteVehicleRegistration.person_name.ilike(f"%{name.strip()}%"))
    if flat_room_unit:
        stmt = stmt.where(SiteVehicleRegistration.flat_room_unit.ilike(f"%{flat_room_unit.strip()}%"))
    if q:
        compact = normalize_plate(q).normalized
        like = f"%{q.strip()}%"
        from sqlalchemy import or_

        stmt = stmt.where(
            or_(
                SiteVehicleRegistration.plate_normalized.contains(compact),
                SiteVehicleRegistration.person_name.ilike(like),
                SiteVehicleRegistration.flat_room_unit.ilike(like),
            )
        )
    stmt = stmt.order_by(SiteVehicleRegistration.plate_normalized).limit(200)
    rows = (await db.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = SiteVehicleRegistrationRecord(
            id=row.id,
            organization_id=row.organization_id,
            site_id=row.site_id,
            plate_normalized=row.plate_normalized,
            vehicle_id=row.vehicle_id,
            category=row.category,
            person_name=row.person_name,
            mobile_number=row.mobile_number,
            flat_room_unit=row.flat_room_unit,
            notes=row.notes,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        out.append(redact_registration(rec, role))
    return out


async def create_registration(
    *,
    db: AsyncSession | None,
    ctx: TenantContext,
    user: Any,
    site_id: str,
    body: Any,
) -> dict[str, Any]:
    ctx.ensure_site(site_id)
    role = _role(user)
    if not has_permission(role, Permission.VEHICLE_REGISTRY_WRITE):
        raise ForbiddenError("Missing permission: vehicle:registry_write")

    category = body.category if isinstance(body.category, str) else str(body.category)
    # Guards may only create guests
    if role == UserRole.SECURITY_GUARD and category != VehicleRegistryCategory.GUEST:
        raise ForbiddenError("Security guards may only register guest vehicles")

    plate_n = _normalize_for_registry(body.plate)

    if is_firestore():
        from app.repositories import site_repo, vehicle_repo, vehicle_registry_repo

        site = await site_repo().get(site_id)
        if not site:
            raise NotFoundError("Site not found")
        ctx.ensure_org(site.organization_id)
        existing = await vehicle_registry_repo().get_by_plate(
            organization_id=site.organization_id,
            site_id=site_id,
            plate_normalized=plate_n,
            active_only=False,
        )
        if existing:
            raise ConflictError("Vehicle already registered at this site")
        veh = await vehicle_repo().get_by_plate(site.organization_id, plate_n)
        now = _utcnow()
        rec = SiteVehicleRegistrationRecord(
            id=str(uuid4()),
            organization_id=site.organization_id,
            site_id=site_id,
            plate_normalized=plate_n,
            vehicle_id=veh.id if veh else None,
            category=category,
            person_name=body.person_name.strip(),
            mobile_number=(body.mobile_number or None),
            flat_room_unit=(body.flat_room_unit or None),
            notes=(body.notes or None),
            active=bool(getattr(body, "active", True)),
            created_at=now,
            updated_at=now,
        )
        saved = await vehicle_registry_repo().add(rec)
        return redact_registration(saved, role)

    assert db is not None
    site = await db.get(Site, site_id)
    if not site:
        raise NotFoundError("Site not found")
    ctx.ensure_org(site.organization_id)
    existing = (
        await db.execute(
            select(SiteVehicleRegistration).where(
                SiteVehicleRegistration.organization_id == site.organization_id,
                SiteVehicleRegistration.site_id == site_id,
                SiteVehicleRegistration.plate_normalized == plate_n,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise ConflictError("Vehicle already registered at this site")
    veh = (
        await db.execute(
            select(Vehicle).where(
                Vehicle.organization_id == site.organization_id,
                Vehicle.plate_normalized == plate_n,
            )
        )
    ).scalar_one_or_none()
    row = SiteVehicleRegistration(
        organization_id=site.organization_id,
        site_id=site_id,
        plate_normalized=plate_n,
        vehicle_id=veh.id if veh else None,
        category=category,
        person_name=body.person_name.strip(),
        mobile_number=(body.mobile_number or None),
        flat_room_unit=(body.flat_room_unit or None),
        notes=(body.notes or None),
        active=bool(getattr(body, "active", True)),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    rec = SiteVehicleRegistrationRecord(
        id=row.id,
        organization_id=row.organization_id,
        site_id=row.site_id,
        plate_normalized=row.plate_normalized,
        vehicle_id=row.vehicle_id,
        category=row.category,
        person_name=row.person_name,
        mobile_number=row.mobile_number,
        flat_room_unit=row.flat_room_unit,
        notes=row.notes,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    return redact_registration(rec, role)


async def get_registration(
    *,
    db: AsyncSession | None,
    ctx: TenantContext,
    user: Any,
    site_id: str,
    registration_id: str,
) -> dict[str, Any]:
    ctx.ensure_site(site_id)
    role = _role(user)
    if is_firestore():
        from app.repositories import vehicle_registry_repo

        rec = await vehicle_registry_repo().get(registration_id)
        if not rec or rec.site_id != site_id:
            raise NotFoundError("Registration not found")
        ctx.ensure_org(rec.organization_id)
        return redact_registration(rec, role)

    assert db is not None
    row = await db.get(SiteVehicleRegistration, registration_id)
    if not row or row.site_id != site_id:
        raise NotFoundError("Registration not found")
    ctx.ensure_org(row.organization_id)
    rec = SiteVehicleRegistrationRecord(
        id=row.id,
        organization_id=row.organization_id,
        site_id=row.site_id,
        plate_normalized=row.plate_normalized,
        vehicle_id=row.vehicle_id,
        category=row.category,
        person_name=row.person_name,
        mobile_number=row.mobile_number,
        flat_room_unit=row.flat_room_unit,
        notes=row.notes,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    return redact_registration(rec, role)


async def update_registration(
    *,
    db: AsyncSession | None,
    ctx: TenantContext,
    user: Any,
    site_id: str,
    registration_id: str,
    body: Any,
) -> dict[str, Any]:
    ctx.ensure_site(site_id)
    role = _role(user)
    if not has_permission(role, Permission.VEHICLE_REGISTRY_WRITE):
        raise ForbiddenError("Missing permission: vehicle:registry_write")

    data = body.model_dump(exclude_unset=True)

    if is_firestore():
        from app.repositories import vehicle_registry_repo

        rec = await vehicle_registry_repo().get(registration_id)
        if not rec or rec.site_id != site_id:
            raise NotFoundError("Registration not found")
        ctx.ensure_org(rec.organization_id)
        if role == UserRole.SECURITY_GUARD:
            # Guards: only edit guest entries; cannot disable non-guest
            if rec.category != VehicleRegistryCategory.GUEST:
                raise ForbiddenError("Security guards may only edit guest registrations")
            if "category" in data and data["category"] != VehicleRegistryCategory.GUEST:
                raise ForbiddenError("Security guards may only keep category=guest")
            if data.get("active") is False:
                raise ForbiddenError("Security guards cannot disable registrations")
        was_active = rec.active
        for k, v in data.items():
            if k == "category" and v is not None:
                setattr(rec, k, str(v))
            else:
                setattr(rec, k, v)
        rec.updated_at = _utcnow()
        saved = await vehicle_registry_repo().save(rec)
        return redact_registration(saved, role), (was_active and saved.active is False)

    assert db is not None
    row = await db.get(SiteVehicleRegistration, registration_id)
    if not row or row.site_id != site_id:
        raise NotFoundError("Registration not found")
    ctx.ensure_org(row.organization_id)
    if role == UserRole.SECURITY_GUARD:
        if row.category != VehicleRegistryCategory.GUEST:
            raise ForbiddenError("Security guards may only edit guest registrations")
        if "category" in data and str(data["category"]) != VehicleRegistryCategory.GUEST:
            raise ForbiddenError("Security guards may only keep category=guest")
        if data.get("active") is False:
            raise ForbiddenError("Security guards cannot disable registrations")
    was_active = row.active
    for k, v in data.items():
        if k == "category" and v is not None:
            setattr(row, k, str(v))
        else:
            setattr(row, k, v)
    await db.flush()
    await db.refresh(row)
    disabled = was_active and row.active is False
    rec = SiteVehicleRegistrationRecord(
        id=row.id,
        organization_id=row.organization_id,
        site_id=row.site_id,
        plate_normalized=row.plate_normalized,
        vehicle_id=row.vehicle_id,
        category=row.category,
        person_name=row.person_name,
        mobile_number=row.mobile_number,
        flat_room_unit=row.flat_room_unit,
        notes=row.notes,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    return redact_registration(rec, role), disabled
