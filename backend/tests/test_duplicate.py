from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Direction, SourceType
from app.services.event import ingest_event


async def test_duplicate_window_suppresses(session: AsyncSession, world: dict) -> None:
    now = datetime.now(UTC)
    first, reason1 = await ingest_event(
        session,
        camera=world["cam_in"],
        site=world["site_a"],
        plate_text="MH12DUP001",
        direction=Direction.ENTRY,
        ocr_confidence=0.9,
        timestamp=now,
        source_type=SourceType.MOCK,
    )
    second, reason2 = await ingest_event(
        session,
        camera=world["cam_in"],
        site=world["site_a"],
        plate_text="MH12DUP001",
        direction=Direction.ENTRY,
        ocr_confidence=0.9,
        timestamp=now + timedelta(seconds=10),
        source_type=SourceType.MOCK,
    )
    assert reason1 == "created"
    assert reason2 == "duplicate"
    assert first is not None and second is not None
    assert first.id == second.id


async def test_low_confidence_rejected(session: AsyncSession, world: dict) -> None:
    event, reason = await ingest_event(
        session,
        camera=world["cam_in"],
        site=world["site_a"],
        plate_text="MH12LOW001",
        direction=Direction.ENTRY,
        ocr_confidence=0.2,
        timestamp=datetime.now(UTC),
        source_type=SourceType.MOCK,
    )
    assert event is None
    assert reason == "low_confidence"
