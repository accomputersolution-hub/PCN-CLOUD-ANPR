"""Integration: Manual ANPR service path must match pipeline on night motorcycle fixture.

Exercises ``run_anpr_on_bytes`` + ``analysis_response`` — the same code path as
``POST /api/v1/manual-anpr/analyze`` (without mocking the engine).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import manual_anpr as manual_svc

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "anpr-engine"
    / "testdata"
    / "MH12AB5687_night_primary.jpg"
)


def test_manual_anpr_service_recovers_night_primary_mh12ab5687() -> None:
    if not FIXTURE.is_file():
        pytest.skip(f"fixture missing: {FIXTURE}")

    data = FIXTURE.read_bytes()
    assert len(data) > 1000

    manual_svc.reset_shared_pipeline()
    result = manual_svc.run_anpr_on_bytes(data, suffix=".jpg")
    debug = result.get("_anpr_debug") or manual_svc.build_anpr_debug(result)

    assert debug.get("has_detect_plates_in_primary_rois") is True, debug
    assert debug.get("pipeline_has_primary_roi_symbol") is True, debug
    assert debug.get("primary_roi_invoked") is True, (
        f"second-stage ROI detector not invoked; debug={debug}"
    )

    chosen = manual_svc.best_plate(result)
    assert chosen is not None, f"best_plate None; debug={debug}"
    assert chosen.get("normalized_text") == "MH12AB5687"

    resp = manual_svc.analysis_response(
        capture_id="integration-night-primary",
        organization_id="org",
        site_id="site",
        camera_id="cam",
        result=result,
        plate_crop_bytes=result.get("_plate_crop_bytes"),
    )
    assert resp["plate_detected"] is True
    assert resp["detected_plate"] == "MH12AB5687"
    assert resp["matches_indian_pattern"] is True
    assert resp.get("anpr_debug", {}).get("primary_roi_invoked") is True

    manual_svc.reset_shared_pipeline()


@pytest.mark.asyncio
async def test_manual_anpr_analyze_endpoint_night_primary(
    client, world: dict
) -> None:
    """HTTP analyze endpoint — same multipart path as the Manual ANPR UI."""
    if not FIXTURE.is_file():
        pytest.skip(f"fixture missing: {FIXTURE}")

    from tests.conftest import login

    token = await login(client, world["guard"].email)
    cam_id = world["cam_in"].id
    data = FIXTURE.read_bytes()

    manual_svc.reset_shared_pipeline()
    res = await client.post(
        "/api/v1/manual-anpr/analyze",
        headers={"Authorization": f"Bearer {token}"},
        data={"camera_id": cam_id},
        files={"file": ("MH12AB5687_night_primary.jpg", data, "image/jpeg")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("plate_detected") is True, body
    assert body.get("detected_plate") == "MH12AB5687", body
    debug = body.get("anpr_debug") or {}
    assert debug.get("primary_roi_invoked") is True, debug
    assert debug.get("has_detect_plates_in_primary_rois") is True, debug

    capture_id = body.get("capture_id")
    if capture_id:
        await client.post(
            "/api/v1/manual-anpr/cancel",
            headers={"Authorization": f"Bearer {token}"},
            data={"capture_id": capture_id},
        )
    manual_svc.reset_shared_pipeline()
