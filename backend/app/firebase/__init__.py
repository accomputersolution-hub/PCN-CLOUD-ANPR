"""Firebase package: Admin bootstrap + cost-control helpers."""

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
from app.firebase.firestore_client import get_firestore_client, tenant_filter_ok

__all__ = [
    "CAMERA_HEARTBEAT_MIN_INTERVAL",
    "DASHBOARD_RECENT_EVENTS_LIMIT",
    "DEFAULT_EVENT_PAGE_SIZE",
    "MAX_EVENT_PAGE_SIZE",
    "clamp_page_size",
    "event_storage_keys",
    "firebase_admin_ready",
    "get_firebase_admin_app",
    "get_firestore_client",
    "reset_firebase_admin_cache",
    "should_write_heartbeat",
    "tenant_filter_ok",
]
