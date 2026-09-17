"""Firebase package: Admin bootstrap + cost-control helpers. Not enabled by default."""

from app.firebase.admin import firebase_admin_ready, get_firebase_admin_app, reset_firebase_admin_cache
from app.firebase.cost_controls import (
    CAMERA_HEARTBEAT_MIN_INTERVAL,
    DASHBOARD_RECENT_EVENTS_LIMIT,
    DEFAULT_EVENT_PAGE_SIZE,
    MAX_EVENT_PAGE_SIZE,
    clamp_page_size,
    event_storage_keys,
    should_write_heartbeat,
)

__all__ = [
    "CAMERA_HEARTBEAT_MIN_INTERVAL",
    "DASHBOARD_RECENT_EVENTS_LIMIT",
    "DEFAULT_EVENT_PAGE_SIZE",
    "MAX_EVENT_PAGE_SIZE",
    "clamp_page_size",
    "event_storage_keys",
    "firebase_admin_ready",
    "get_firebase_admin_app",
    "reset_firebase_admin_cache",
    "should_write_heartbeat",
]
