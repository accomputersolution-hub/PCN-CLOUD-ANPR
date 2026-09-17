from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Direction, SourceType, VisitStatus
from app.models.site import Site
from app.services.event import ingest_event


async def test_entry_exit_matching(session: AsyncSession, world: dict) -> None:
    site: Site = world["site_a"]
    now = datetime.now(UTC)
    entry, reason = await ingest_event(
        session,
        camera=world["cam_in"],
        site=site,
        plate_text="MH12AB1234",
        direction=Direction.ENTRY,
        ocr_confidence=0.95,
        timestamp=now - timedelta(hours=3),
        source_type=SourceType.MOCK,
        force=True,
    )
    assert reason == "created"
    assert entry is not None
    exit_event, _ = await ingest_event(
        session,
        camera=world["cam_out"],
        site=site,
        plate_text="MH12AB1234",
        direction=Direction.EXIT,
        ocr_confidence=0.95,
        timestamp=now,
        source_type=SourceType.MOCK,
        force=True,
    )
    await session.commit()
    assert exit_event is not None
    assert exit_event.visit_id == entry.visit_id


async def test_exit_without_match(session: AsyncSession, world: dict) -> None:
    event, _ = await ingest_event(
        session,
        camera=world["cam_out"],
        site=world["site_a"],
        plate_text="MH99UNMATCH",
        direction=Direction.EXIT,
        ocr_confidence=0.9,
        timestamp=datetime.now(UTC),
        source_type=SourceType.MOCK,
        force=True,
    )
    await session.commit()
    assert event is not None
    from app.models.vehicle_visit import VehicleVisit

    visit = await session.get(VehicleVisit, event.visit_id)
    assert visit is not None
    assert visit.status == VisitStatus.EXIT_WITHOUT_MATCH
    assert visit.entry_event_id is None
