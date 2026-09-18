from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from httpx import AsyncClient

from app.core.rbac import Permission, has_permission
from app.models.enums import UserRole
from app.services import manual_anpr as manual_svc
from tests.conftest import login


def _jpeg_bytes(w: int = 96, h: int = 64) -> bytes:
    import cv2

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_shared_pipeline_built_once() -> None:
    manual_svc.reset_shared_pipeline()
    fake = MagicMock()
    fake.warm_up.return_value = {"warmup_ms": 1, "ocr_init_ms": 1, "ocr_instance": 1}
    fake.process_image.return_value = {
        "vehicle_detected": False,
        "plate_detected": False,
        "vehicles": [],
        "plates": [],
        "processing_ms": 5,
        "error": None,
    }
    with patch("pcn_anpr.factory.build_pipeline", return_value=fake) as build:
        a = manual_svc.run_anpr_on_bytes(_jpeg_bytes())
        b = manual_svc.run_anpr_on_bytes(_jpeg_bytes())
    assert build.call_count == 1
    assert fake.warm_up.call_count == 1
    assert fake.process_image.call_count == 2
    assert a["processing_ms"] == 5
    assert b["processing_ms"] == 5
    manual_svc.reset_shared_pipeline()


def test_reset_shared_pipeline_allows_rebuild() -> None:
    manual_svc.reset_shared_pipeline()
    fake1 = MagicMock()
    fake1.warm_up.return_value = {}
    fake1.process_image.return_value = {
        "vehicle_detected": False,
        "plate_detected": False,
        "vehicles": [],
        "plates": [],
        "processing_ms": 1,
        "error": None,
    }
    fake2 = MagicMock()
    fake2.warm_up.return_value = {}
    fake2.process_image.return_value = {
        "vehicle_detected": False,
        "plate_detected": False,
        "vehicles": [],
        "plates": [],
        "processing_ms": 2,
        "error": None,
    }
    with patch("pcn_anpr.factory.build_pipeline", side_effect=[fake1, fake2]) as build:
        manual_svc.run_anpr_on_bytes(_jpeg_bytes())
        manual_svc.reset_shared_pipeline()
        manual_svc.run_anpr_on_bytes(_jpeg_bytes())
    assert build.call_count == 2
    manual_svc.reset_shared_pipeline()


def _fake_anpr_result(**overrides):
    base = {
        "vehicle_detected": True,
        "plate_detected": True,
        "vehicles": [{"label": "car", "confidence": 0.9, "bbox": [10, 10, 80, 50]}],
        "plates": [
            {
                "raw_text": "MH20DV2366",
                "normalized_text": "MH20DV2366",
                "normalized_plate": "MH20DV2366",
                "ocr_confident": True,
                "confidence": 0.86,
                "ocr_confidence": 0.96,
                "plate_confidence": 0.68,
                "matches_pattern": True,
                "bbox": [20, 30, 70, 45],
                "padded_bbox": [15, 25, 75, 50],
            }
        ],
        "processing_ms": 42,
        "error": None,
        "_plate_crop_bytes": b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9",
    }
    base.update(overrides)
    return base


async def test_manual_anpr_permission_matrix() -> None:
    assert has_permission(UserRole.SECURITY_GUARD, Permission.MANUAL_ANPR)
    assert has_permission(UserRole.ORG_ADMIN, Permission.MANUAL_ANPR)
    assert not has_permission(UserRole.VIEWER, Permission.MANUAL_ANPR)


async def test_manual_analyze_requires_auth(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/manual-anpr/analyze",
        data={"camera_id": "x"},
        files={"file": ("x.jpg", b"abc", "image/jpeg")},
    )
    assert res.status_code in {401, 403}


async def test_viewer_cannot_manual_anpr(client: AsyncClient, world: dict, session) -> None:
    from app.core.security import hash_password
    from app.models.enums import UserRole
    from app.models.user import User

    viewer = User(
        email="viewer@pcncloud.in",
        hashed_password=hash_password("ChangeMe@12345"),
        full_name="Viewer",
        role=UserRole.VIEWER,
        organization_id=world["org_a"].id,
    )
    session.add(viewer)
    await session.commit()
    token = await login(client, "viewer@pcncloud.in")
    res = await client.post(
        "/api/v1/manual-anpr/analyze",
        headers={"Authorization": f"Bearer {token}"},
        data={"camera_id": world["cam_in"].id},
        files={"file": ("x.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert res.status_code == 403


async def test_guard_can_analyze_and_confirm(client: AsyncClient, world: dict) -> None:
    token = await login(client, "guard@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        analyze = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers=headers,
            data={"camera_id": world["cam_in"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    assert analyze.status_code == 200, analyze.text
    body = analyze.json()
    assert body["event_created"] is False
    assert body["detected_plate"] == "MH20DV2366"
    assert body["ocr_confidence"] == pytest.approx(0.96)
    assert body["plate_confidence"] == pytest.approx(0.68)
    assert body["combined_confidence"] == pytest.approx(0.86)
    assert body["matches_indian_pattern"] is True
    assert body["plate_crop_jpeg_base64"]
    assert "rtsp" not in analyze.text.lower()
    capture_id = body["capture_id"]

    # Edit plate + ENTRY
    confirm = await client.post(
        "/api/v1/manual-anpr/confirm",
        headers=headers,
        json={
            "capture_id": capture_id,
            "plate_text": "MH20DV9999",
            "direction": "ENTRY",
            "ocr_confidence": body["ocr_confidence"],
            "plate_confidence": body["plate_confidence"],
            "combined_confidence": body["combined_confidence"],
        },
    )
    assert confirm.status_code == 200, confirm.text
    ev = confirm.json()
    assert ev["plate_normalized"] == "MH20DV9999"
    assert ev["direction"] == "ENTRY"
    assert ev["source_type"] == "MANUAL"
    assert ev["operator_user_id"] == world["guard"].id
    assert ev["organization_id"] == world["org_a"].id
    assert ev["site_id"] == world["site_a"].id
    assert ev["snapshot_path"]
    assert ev["plate_crop_path"]
    # Storage keys follow convention
    assert f"{world['org_a'].id}/events/" in ev["snapshot_path"]
    assert ev["snapshot_path"].endswith("snapshot.jpg")
    assert "password" not in confirm.text.lower()


async def test_cross_org_camera_rejected(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        res = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers={"Authorization": f"Bearer {token}"},
            data={"camera_id": world["cam_b"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    assert res.status_code in {403, 404}


async def test_invalid_image_rejected(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    res = await client.post(
        "/api/v1/manual-anpr/analyze",
        headers={"Authorization": f"Bearer {token}"},
        data={"camera_id": world["cam_in"].id},
        files={"file": ("bad.txt", b"not-an-image", "text/plain")},
    )
    assert res.status_code in {400, 422}


async def test_confirm_exit_and_cancel(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        analyze = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers=headers,
            data={"camera_id": world["cam_out"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    assert analyze.status_code == 200
    capture_id = analyze.json()["capture_id"]

    cancel = await client.post(
        "/api/v1/manual-anpr/cancel",
        headers=headers,
        data={"capture_id": capture_id},
    )
    assert cancel.status_code == 200

    # Confirm after cancel should fail
    confirm = await client.post(
        "/api/v1/manual-anpr/confirm",
        headers=headers,
        json={"capture_id": capture_id, "plate_text": "MH12AB1234", "direction": "EXIT"},
    )
    assert confirm.status_code in {404, 400}

    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        analyze2 = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers=headers,
            data={"camera_id": world["cam_out"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    confirm2 = await client.post(
        "/api/v1/manual-anpr/confirm",
        headers=headers,
        json={
            "capture_id": analyze2.json()["capture_id"],
            "plate_text": "MH12AB1234",
            "direction": "EXIT",
        },
    )
    assert confirm2.status_code == 200
    assert confirm2.json()["direction"] == "EXIT"
    assert confirm2.json()["source_type"] == "MANUAL"


async def test_confirmed_manual_event_appears_in_events_and_dashboard(client: AsyncClient, world: dict) -> None:
    """Manual confirm must land in the normal events list + dashboard recent feed."""
    token = await login(client, "orgadmin@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        analyze = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers=headers,
            data={"camera_id": world["cam_in"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    assert analyze.status_code == 200
    confirm = await client.post(
        "/api/v1/manual-anpr/confirm",
        headers=headers,
        json={
            "capture_id": analyze.json()["capture_id"],
            "plate_text": "MH20DV2366",
            "direction": "ENTRY",
        },
    )
    assert confirm.status_code == 200, confirm.text
    created = confirm.json()
    assert created["source_type"] == "MANUAL"
    event_id = created["id"]

    listed = await client.get("/api/v1/events", headers=headers)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(e["id"] == event_id for e in items)
    match = next(e for e in items if e["id"] == event_id)
    assert match["plate_normalized"] == "MH20DV2366"
    assert match["source_type"] == "MANUAL"
    assert match["organization_id"] == world["org_a"].id
    assert match["site_id"] == world["site_a"].id
    assert any(e["source_type"] == "MANUAL" for e in items)

    by_plate = await client.get("/api/v1/events?plate=MH20DV2366", headers=headers)
    assert by_plate.status_code == 200
    assert any(e["id"] == event_id for e in by_plate.json()["items"])

    dash = await client.get("/api/v1/dashboard/summary", headers=headers)
    assert dash.status_code == 200, dash.text
    recent = dash.json()["recent_events"]
    assert any(e["id"] == event_id for e in recent)
    assert any(e["plate_normalized"] == "MH20DV2366" and e["source_type"] == "MANUAL" for e in recent)


async def test_manual_event_tenant_isolation_on_list(client: AsyncClient, world: dict) -> None:
    token_a = await login(client, "orgadmin@pcncloud.in")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        analyze = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers=headers_a,
            data={"camera_id": world["cam_in"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    confirm = await client.post(
        "/api/v1/manual-anpr/confirm",
        headers=headers_a,
        json={
            "capture_id": analyze.json()["capture_id"],
            "plate_text": "MH20ISO9999",
            "direction": "ENTRY",
        },
    )
    assert confirm.status_code == 200
    event_id = confirm.json()["id"]

    listed_a = await client.get("/api/v1/events?plate=MH20ISO9999", headers=headers_a)
    assert any(e["id"] == event_id for e in listed_a.json()["items"])

    # Org A admin must not pierce into org B via organization_id filter.
    listed_scoped = await client.get(
        f"/api/v1/events?organization_id={world['org_b'].id}",
        headers=headers_a,
    )
    assert listed_scoped.status_code in {200, 403}
    if listed_scoped.status_code == 200:
        assert not any(e["id"] == event_id for e in listed_scoped.json()["items"])
        assert not any(e.get("organization_id") == world["org_b"].id for e in listed_scoped.json()["items"])


async def test_manual_confirm_publishes_realtime_event(client: AsyncClient, world: dict) -> None:
    from unittest.mock import AsyncMock

    token = await login(client, "guard@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    with (
        patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()),
        patch("app.services.realtime.hub.publish", new_callable=AsyncMock) as pub,
    ):
        analyze = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers=headers,
            data={"camera_id": world["cam_in"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
        confirm = await client.post(
            "/api/v1/manual-anpr/confirm",
            headers=headers,
            json={
                "capture_id": analyze.json()["capture_id"],
                "plate_text": "MH20DV2366",
                "direction": "ENTRY",
            },
        )
    assert confirm.status_code == 200
    assert pub.await_count >= 1
    assert pub.await_args.args[1] == "anpr.event"
    assert pub.await_args.args[2]["plate"] == "MH20DV2366"


async def test_manual_anpr_no_postgres_env(client: AsyncClient, world: dict, monkeypatch) -> None:
    """Confirm path works with empty DATABASE_URL semantics for firestore helpers unused in SQL tests."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    token = await login(client, "orgadmin@pcncloud.in")
    with patch("app.services.manual_anpr.run_anpr_on_bytes", return_value=_fake_anpr_result()):
        res = await client.post(
            "/api/v1/manual-anpr/analyze",
            headers={"Authorization": f"Bearer {token}"},
            data={"camera_id": world["cam_in"].id},
            files={"file": ("car.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    assert res.status_code == 200
    # No sqlalchemy/postgres connection string required for analyze
    assert res.json()["capture_id"]
