"""Cost-control helpers for future Firestore usage.

These are design-time constants and throttle utilities. They do not open
Firestore listeners or write documents by themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Event list pages must always cap reads.
DEFAULT_EVENT_PAGE_SIZE = 50
MAX_EVENT_PAGE_SIZE = 100

# Camera / gateway status writes — avoid per-frame or sub-second spam.
CAMERA_HEARTBEAT_MIN_INTERVAL = timedelta(seconds=30)
GATEWAY_HEARTBEAT_MIN_INTERVAL = timedelta(seconds=30)

# Dashboard should fetch summary aggregates, not full event history.
DASHBOARD_RECENT_EVENTS_LIMIT = 20


def clamp_page_size(requested: int | None, *, default: int = DEFAULT_EVENT_PAGE_SIZE) -> int:
    if requested is None or requested <= 0:
        return default
    return min(int(requested), MAX_EVENT_PAGE_SIZE)


def should_write_heartbeat(
    last_written: datetime | None,
    *,
    now: datetime | None = None,
    min_interval: timedelta = CAMERA_HEARTBEAT_MIN_INTERVAL,
) -> bool:
    """Return True only when enough time has passed since the last status write."""
    current = now or datetime.now(UTC)
    if last_written is None:
        return True
    previous = last_written if last_written.tzinfo else last_written.replace(tzinfo=UTC)
    if previous.tzinfo is not UTC:
        previous = previous.astimezone(UTC)
    return (current - previous) >= min_interval


def event_storage_keys(
    organization_id: str,
    event_id: str,
) -> dict[str, str]:
    """Canonical object-storage paths for confirmed ANPR evidence (not every frame)."""
    base = f"{organization_id}/events/{event_id}"
    return {
        "snapshot": f"{base}/snapshot.jpg",
        "plate_crop": f"{base}/plate_crop.jpg",
        "vehicle_crop": f"{base}/vehicle_crop.jpg",
    }
