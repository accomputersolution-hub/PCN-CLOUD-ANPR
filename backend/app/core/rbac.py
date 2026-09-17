from __future__ import annotations

from enum import StrEnum

from app.models.enums import UserRole


class Permission(StrEnum):
    PLATFORM_ADMIN = "platform:admin"
    ORG_READ = "org:read"
    ORG_WRITE = "org:write"
    SITE_READ = "site:read"
    SITE_WRITE = "site:write"
    GATE_READ = "gate:read"
    GATE_WRITE = "gate:write"
    CAMERA_READ = "camera:read"
    CAMERA_WRITE = "camera:write"
    CAMERA_TEST = "camera:test"
    EVENT_READ = "event:read"
    EVENT_WRITE = "event:write"
    EVENT_DELETE = "event:delete"
    EVENT_CORRECT = "event:correct"
    EVENT_CLASSIFY = "event:classify"
    VEHICLE_READ = "vehicle:read"
    VEHICLE_VISITOR_WRITE = "vehicle:visitor_write"
    VISIT_RESOLVE = "visit:resolve"
    REPORT_READ = "report:read"
    REPORT_EXPORT = "report:export"
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    EDGE_INGEST = "edge:ingest"
    AUDIT_READ = "audit:read"
    DASHBOARD_READ = "dashboard:read"
    MOCK_WRITE = "mock:write"
    ANPR_TEST = "anpr:test"


ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.SUPER_ADMIN: set(Permission),
    UserRole.ORG_ADMIN: {
        Permission.ORG_READ,
        Permission.ORG_WRITE,
        Permission.SITE_READ,
        Permission.SITE_WRITE,
        Permission.GATE_READ,
        Permission.GATE_WRITE,
        Permission.CAMERA_READ,
        Permission.CAMERA_WRITE,
        Permission.CAMERA_TEST,
        Permission.EVENT_READ,
        Permission.EVENT_WRITE,
        Permission.EVENT_DELETE,
        Permission.EVENT_CORRECT,
        Permission.EVENT_CLASSIFY,
        Permission.VEHICLE_READ,
        Permission.VEHICLE_VISITOR_WRITE,
        Permission.VISIT_RESOLVE,
        Permission.REPORT_READ,
        Permission.REPORT_EXPORT,
        Permission.USER_READ,
        Permission.USER_WRITE,
        Permission.AUDIT_READ,
        Permission.DASHBOARD_READ,
        Permission.MOCK_WRITE,
        Permission.ANPR_TEST,
    },
    UserRole.SITE_MANAGER: {
        Permission.ORG_READ,
        Permission.SITE_READ,
        Permission.SITE_WRITE,
        Permission.GATE_READ,
        Permission.GATE_WRITE,
        Permission.CAMERA_READ,
        Permission.CAMERA_WRITE,
        Permission.CAMERA_TEST,
        Permission.EVENT_READ,
        Permission.EVENT_WRITE,
        Permission.EVENT_CORRECT,
        Permission.EVENT_CLASSIFY,
        Permission.VEHICLE_READ,
        Permission.VEHICLE_VISITOR_WRITE,
        Permission.VISIT_RESOLVE,
        Permission.REPORT_READ,
        Permission.REPORT_EXPORT,
        Permission.DASHBOARD_READ,
        Permission.MOCK_WRITE,
        Permission.ANPR_TEST,
    },
    UserRole.SECURITY_GUARD: {
        Permission.SITE_READ,
        Permission.GATE_READ,
        Permission.CAMERA_READ,
        Permission.EVENT_READ,
        Permission.EVENT_CLASSIFY,
        Permission.VEHICLE_READ,
        Permission.VEHICLE_VISITOR_WRITE,
        Permission.DASHBOARD_READ,
    },
    UserRole.VIEWER: {
        Permission.SITE_READ,
        Permission.GATE_READ,
        Permission.CAMERA_READ,
        Permission.EVENT_READ,
        Permission.VEHICLE_READ,
        Permission.DASHBOARD_READ,
        Permission.REPORT_READ,
    },
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())
