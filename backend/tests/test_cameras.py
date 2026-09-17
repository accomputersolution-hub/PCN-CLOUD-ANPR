from httpx import AsyncClient

from tests.conftest import login


async def test_list_cameras_hides_secrets(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    res = await client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    for cam in res.json():
        assert "password" not in cam
        assert "rtsp_url" not in cam
        assert "password_encrypted" not in cam
        assert "streaming" in cam


async def test_test_connection_validates_url(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    bad = await client.post(
        "/api/v1/cameras/test",
        headers={"Authorization": f"Bearer {token}"},
        json={"rtsp_url": "http://not-rtsp"},
    )
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    good = await client.post(
        "/api/v1/cameras/test",
        headers={"Authorization": f"Bearer {token}"},
        json={"rtsp_url": "rtsp://10.0.0.8/stream1"},
    )
    body = good.json()
    assert "ok" in body
    assert "message" in body
    # Without FFmpeg (or when probe is skipped): format-valid → ok True.
    # With FFmpeg: unreachable lab hosts may fail the live probe (ok False) — still not a crash.
    if body.get("probe") == "url_validation":
        assert body["ok"] is True
    else:
        assert body["ok"] in {True, False}


async def test_test_rtsp_endpoint(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    cam_id = world["cam_in"].id
    res = await client.post(
        f"/api/v1/cameras/{cam_id}/test-rtsp",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "ok" in body
    assert "message" in body
    assert "password" not in body.get("message", "").lower() or "pass" not in body["message"].lower()
    # redacted URL must not include credentials
    if body.get("redacted_url"):
        assert "@" not in body["redacted_url"] or "***" in body["redacted_url"]


async def test_camera_health(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    cam_id = world["cam_in"].id
    res = await client.get(
        f"/api/v1/cameras/{cam_id}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == cam_id
    assert "status" in body
    assert "rtsp_configured" in body
    assert "password" not in body
    assert "rtsp_url" not in body


async def test_start_stop_streaming(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    cam_id = world["cam_in"].id
    start = await client.post(
        f"/api/v1/edge/cameras/{cam_id}/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start.status_code == 200, start.text
    assert start.json()["streaming"] is True
    assert start.json()["status"] == "CONNECTING"
    assert "rtsp_url" not in start.json()

    health = await client.get(
        f"/api/v1/cameras/{cam_id}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert health.json()["streaming"] is True

    stop = await client.post(
        f"/api/v1/edge/cameras/{cam_id}/stop",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert stop.status_code == 200
    assert stop.json()["streaming"] is False


async def test_start_stop_tenant_isolation(client: AsyncClient, world: dict) -> None:
    # Org B admin cannot start Org A camera
    token_b = await login(client, "societyadmin@pcncloud.in")
    cam_a = world["cam_in"].id
    res = await client.post(
        f"/api/v1/edge/cameras/{cam_a}/start",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res.status_code in {403, 404}


async def test_guard_cannot_start_camera(client: AsyncClient, world: dict) -> None:
    token = await login(client, "guard@pcncloud.in")
    res = await client.post(
        f"/api/v1/edge/cameras/{world['cam_in'].id}/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
