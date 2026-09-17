from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from pcn_edge.config import EdgeSettings
from pcn_edge.queue import EventQueue


class CloudSync:
    def __init__(self, settings: EdgeSettings, queue: EventQueue) -> None:
        self.settings = settings
        self.queue = queue

    def _headers(self) -> dict[str, str]:
        return {
            "X-Edge-Id": self.settings.edge_agent_id,
            "X-Edge-Key": self.settings.edge_agent_key,
        }

    def push_pending(self) -> dict[str, Any]:
        pending = self.queue.pending()
        if not pending:
            return {"accepted": 0, "duplicates": 0, "rejected": 0}
        url = f"{self.settings.api_base_url.rstrip('/')}/api/v1/edge/sync"
        try:
            response = httpx.post(url, json={"events": pending}, headers=self._headers(), timeout=30)
            response.raise_for_status()
            body = response.json()
            self.queue.mark_synced(body.get("event_ids", [p["id"] for p in pending]))
            return body
        except Exception as exc:  # offline — keep queue
            for item in pending:
                self.queue.mark_failed(item["id"], str(exc))
            raise

    def heartbeat(self, extra: dict[str, Any] | None = None) -> None:
        url = f"{self.settings.api_base_url.rstrip('/')}/api/v1/edge/heartbeat"
        payload = {"queue_size": self.queue.size(), **(extra or {})}
        response = httpx.post(url, json=payload, headers=self._headers(), timeout=10)
        response.raise_for_status()

    def fetch_camera_configs(self) -> list[dict[str, Any]]:
        """Fetch streaming camera configs (includes RTSP URL). Edge-auth only."""
        url = f"{self.settings.api_base_url.rstrip('/')}/api/v1/edge/cameras"
        response = httpx.get(url, headers=self._headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else data.get("cameras", [])

    def enqueue_detection(
        self,
        camera_id: str,
        plate: str,
        direction: str,
        confidence: float,
        *,
        raw_ocr_text: str | None = None,
        plate_detection_confidence: float = 0.0,
        vehicle_detection_confidence: float = 0.0,
        processing_duration_ms: int = 0,
        snapshot_bytes: bytes | None = None,
        plate_crop_bytes: bytes | None = None,
        vehicle_crop_bytes: bytes | None = None,
        timestamp: datetime | None = None,
        source_type: str = "EDGE",
    ) -> str:
        ts = timestamp or datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "camera_id": camera_id,
            "direction": direction,
            "plate_text": plate,
            "raw_ocr_text": raw_ocr_text or plate,
            "ocr_confidence": confidence,
            "plate_detection_confidence": plate_detection_confidence,
            "vehicle_detection_confidence": vehicle_detection_confidence,
            "processing_duration_ms": processing_duration_ms,
            "timestamp": ts.isoformat(),
            "source_type": source_type,
        }
        if snapshot_bytes:
            payload["snapshot_b64"] = base64.b64encode(snapshot_bytes).decode("ascii")
        if plate_crop_bytes:
            payload["plate_crop_b64"] = base64.b64encode(plate_crop_bytes).decode("ascii")
        if vehicle_crop_bytes:
            payload["vehicle_crop_b64"] = base64.b64encode(vehicle_crop_bytes).decode("ascii")
        return self.queue.enqueue(payload)
