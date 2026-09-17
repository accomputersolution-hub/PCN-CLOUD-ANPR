from httpx import AsyncClient

from tests.conftest import login


async def test_guard_cannot_create_camera(client: AsyncClient, world: dict) -> None:
    token = await login(client, "guard@pcncloud.in")
    res = await client.post(
        "/api/v1/cameras",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "site_id": world["site_a"].id,
            "gate_id": world["cam_in"].gate_id,
            "name": "Nope",
            "camera_code": "X",
            "direction": "ENTRY",
        },
    )
    assert res.status_code == 403


async def test_org_admin_can_create_camera(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    res = await client.post(
        "/api/v1/cameras",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "site_id": world["site_a"].id,
            "gate_id": world["cam_in"].gate_id,
            "name": "Cam Extra",
            "camera_code": "A-EXTRA",
            "direction": "ENTRY",
            "rtsp_url": "rtsp://10.0.0.5/stream",
            "password": "secret-cam-pass",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert "password" not in body
    assert "rtsp_url" not in body
    assert body["credentials_configured"] is True
    assert body["rtsp_configured"] is True


async def test_viewer_cannot_mock_event(client: AsyncClient, world: dict) -> None:
    # promote nothing — viewer not in world; guard lacks MOCK_WRITE
    token = await login(client, "guard@pcncloud.in")
    res = await client.post(
        "/api/v1/mock/events",
        headers={"Authorization": f"Bearer {token}"},
        json={"camera_id": world["cam_in"].id, "plate_text": "MH12AB1234"},
    )
    assert res.status_code == 403
