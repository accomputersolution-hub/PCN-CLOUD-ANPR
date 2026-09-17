from __future__ import annotations

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.camera import Camera
from app.models.enums import CameraStatus
from app.services.rtsp_probe import probe_rtsp
from app.services.rtsp_url import build_rtsp_url, redact_rtsp_url, validate_rtsp_url_shape


def encrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    return encrypt_secret(value)


def decrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    return decrypt_secret(value)


def credentials_configured(camera: Camera) -> bool:
    return bool(camera.username_encrypted or camera.password_encrypted)


def rtsp_configured(camera: Camera) -> bool:
    return bool(camera.rtsp_url_encrypted)


def camera_to_out(camera: Camera) -> dict:
    return {
        "id": camera.id,
        "organization_id": camera.organization_id,
        "site_id": camera.site_id,
        "gate_id": camera.gate_id,
        "name": camera.name,
        "camera_code": camera.camera_code,
        "direction": camera.direction,
        "onvif_ip": camera.onvif_ip,
        "stream_type": camera.stream_type,
        "resolution": camera.resolution,
        "enabled": camera.enabled,
        "streaming": bool(getattr(camera, "streaming", False)),
        "status": camera.status,
        "last_heartbeat": camera.last_heartbeat,
        "fps": camera.fps,
        "connection_error": camera.connection_error,
        "last_frame_at": camera.last_frame_at,
        "retry_count": camera.retry_count,
        "credentials_configured": credentials_configured(camera),
        "rtsp_configured": rtsp_configured(camera),
        "site_name": camera.site.name if getattr(camera, "site", None) is not None else None,
        "gate_name": camera.gate.name if getattr(camera, "gate", None) is not None else None,
    }


def validate_rtsp_url(url: str) -> tuple[bool, str]:
    return validate_rtsp_url_shape(url)


def resolve_camera_rtsp_url(camera: Camera) -> str | None:
    """Build a usable RTSP URL from encrypted fields. Caller must not log the result."""
    base = decrypt_optional(camera.rtsp_url_encrypted)
    if not base:
        return None
    username = decrypt_optional(camera.username_encrypted)
    password = decrypt_optional(camera.password_encrypted)
    return build_rtsp_url(base, username, password)


def test_camera_connection(rtsp_url: str | None) -> dict:
    """Backward-compatible wrapper used by legacy /cameras/test."""
    result = probe_rtsp(rtsp_url)
    return {
        "ok": result.ok,
        "message": result.message,
        "probe": result.probe,
        "resolution": result.resolution,
        "fps": result.fps,
        "first_frame_received": result.first_frame_received,
        "redacted_url": result.redacted_url,
    }


def test_rtsp_for_camera(camera: Camera) -> dict:
    url = resolve_camera_rtsp_url(camera)
    result = probe_rtsp(url)
    payload = {
        "ok": result.ok,
        "message": result.message,
        "probe": result.probe,
        "resolution": result.resolution,
        "fps": result.fps,
        "first_frame_received": result.first_frame_received,
        "redacted_url": result.redacted_url or (redact_rtsp_url(url) if url else None),
    }
    if result.ok and result.resolution:
        camera.resolution = result.resolution
    if result.ok:
        camera.status = CameraStatus.ONLINE if result.first_frame_received else camera.status
        camera.connection_error = None
    else:
        camera.status = CameraStatus.ERROR
        camera.connection_error = result.message[:500]
    return payload


def mark_offline_if_stale(camera: Camera) -> None:
    if camera.enabled and camera.status == CameraStatus.UNKNOWN:
        camera.status = CameraStatus.OFFLINE
