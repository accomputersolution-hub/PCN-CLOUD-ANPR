from httpx import AsyncClient

from tests.conftest import login

SECRET_KEYS = {
    "device_key_hash",
    "password",
    "rtsp_url",
    "private_key",
    "vpn_key",
    "preshared_key",
    "wireguard_private_key",
}


def _assert_no_secrets(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in SECRET_KEYS, key
            if key == "device_key":
                continue
            _assert_no_secrets(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secrets(item)


async def _create_gateway(client: AsyncClient, token: str, site_id: str, name: str = "Site VPN") -> dict:
    res = await client.post(
        "/api/v1/gateways",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "site_id": site_id,
            "name": name,
            "device_type": "PCN_CLOUD_GATEWAY",
            "vendor": "TP-Link",
            "model": "ER605",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_org_isolation_on_gateways(client: AsyncClient, world: dict) -> None:
    a = await login(client, "orgadmin@pcncloud.in")
    b = await login(client, "societyadmin@pcncloud.in")
    created = await _create_gateway(client, a, world["site_a"].id, "Hotel GW")
    listed_b = await client.get("/api/v1/gateways", headers={"Authorization": f"Bearer {b}"})
    assert listed_b.status_code == 200
    assert all(item["id"] != created["id"] for item in listed_b.json())
    hidden = await client.get(f"/api/v1/gateways/{created['id']}", headers={"Authorization": f"Bearer {b}"})
    assert hidden.status_code == 403


async def test_gateway_create_returns_key_once_and_list_hides_it(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    created = await _create_gateway(client, token, world["site_a"].id)
    assert created["device_key"]
    assert "device_key_hash" not in created
    _assert_no_secrets({k: v for k, v in created.items() if k != "device_key"})
    listed = await client.get("/api/v1/gateways", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    body = listed.json()
    assert body
    for item in body:
        assert "device_key" not in item
        _assert_no_secrets(item)


async def test_gateway_heartbeat_and_revoked(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    created = await _create_gateway(client, token, world["site_a"].id, "Heartbeat GW")
    gw_id = created["id"]
    key = created["device_key"]
    ok = await client.post(
        f"/api/v1/gateways/{gw_id}/heartbeat",
        headers={"X-Gateway-Id": gw_id, "X-Gateway-Key": key},
        json={"vpn_status": "CONNECTED", "health_status": "HEALTHY", "lan_subnet": "192.168.1.0/24"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["vpn_status"] == "CONNECTED"
    assert body["health_status"] == "HEALTHY"
    assert body["last_seen"] is not None
    _assert_no_secrets(body)

    bad = await client.post(
        f"/api/v1/gateways/{gw_id}/heartbeat",
        headers={"X-Gateway-Id": gw_id, "X-Gateway-Key": "wrong-key-value-12"},
        json={"vpn_status": "CONNECTED"},
    )
    assert bad.status_code == 401

    revoked = await client.post(
        f"/api/v1/gateways/{gw_id}/revoke",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
    assert "device_key" not in revoked.json()

    after = await client.post(
        f"/api/v1/gateways/{gw_id}/heartbeat",
        headers={"X-Gateway-Id": gw_id, "X-Gateway-Key": key},
        json={"vpn_status": "CONNECTED"},
    )
    assert after.status_code in {401, 403}


async def test_gateway_provision_rotates_key(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    created = await _create_gateway(client, token, world["site_a"].id, "Rotate GW")
    old_key = created["device_key"]
    rotated = await client.post(
        f"/api/v1/gateways/{created['id']}/provision",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rotated.status_code == 200, rotated.text
    new_key = rotated.json()["device_key"]
    assert new_key != old_key
    stale = await client.post(
        f"/api/v1/gateways/{created['id']}/heartbeat",
        headers={"X-Gateway-Id": created["id"], "X-Gateway-Key": old_key},
        json={},
    )
    assert stale.status_code in {401, 403}
    fresh = await client.post(
        f"/api/v1/gateways/{created['id']}/heartbeat",
        headers={"X-Gateway-Id": created["id"], "X-Gateway-Key": new_key},
        json={"vpn_status": "CONNECTED"},
    )
    assert fresh.status_code == 200, fresh.text


async def test_connectivity_mode_and_camera_assignment(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    site_id = world["site_a"].id
    gw = await _create_gateway(client, token, site_id, "Path GW")
    nvr = await client.post(
        "/api/v1/nvrs",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "site_id": site_id,
            "name": "Lobby NVR",
            "vendor": "Hikvision",
            "host": "192.168.1.10",
            "channel_count": 16,
            "gateway_id": gw["id"],
        },
    )
    assert nvr.status_code == 201, nvr.text
    nvr_id = nvr.json()["id"]
    _assert_no_secrets(nvr.json())

    conn = await client.patch(
        f"/api/v1/sites/{site_id}/connectivity",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connectivity_mode": "PCN_CLOUD_GATEWAY",
            "anpr_deployment_mode": "LOCAL_EDGE_AGENT",
            "primary_gateway_id": gw["id"],
        },
    )
    assert conn.status_code == 200, conn.text
    assert conn.json()["connectivity_mode"] == "PCN_CLOUD_GATEWAY"
    assert conn.json()["gateway"]["id"] == gw["id"]
    _assert_no_secrets(conn.json())

    cam = await client.patch(
        f"/api/v1/cameras/{world['cam_in'].id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "source_type": "NVR_CHANNEL",
            "nvr_id": nvr_id,
            "channel": "101",
            "gateway_id": gw["id"],
            "anpr_enabled": True,
        },
    )
    assert cam.status_code == 200, cam.text
    body = cam.json()
    assert body["nvr_id"] == nvr_id
    assert body["gateway_id"] == gw["id"]
    assert body["channel"] == "101"
    assert body["anpr_enabled"] is True
    assert "password" not in body
    assert "rtsp_url" not in body
    _assert_no_secrets(body)

    other = await login(client, "societyadmin@pcncloud.in")
    steal = await client.patch(
        f"/api/v1/cameras/{world['cam_in'].id}",
        headers={"Authorization": f"Bearer {other}"},
        json={"gateway_id": gw["id"]},
    )
    assert steal.status_code == 403


async def test_guard_cannot_create_gateway(client: AsyncClient, world: dict) -> None:
    token = await login(client, "guard@pcncloud.in")
    res = await client.post(
        "/api/v1/gateways",
        headers={"Authorization": f"Bearer {token}"},
        json={"site_id": world["site_a"].id, "name": "Nope", "device_type": "PCN_CLOUD_GATEWAY"},
    )
    assert res.status_code == 403


async def test_cross_site_gateway_rejected_on_nvr(client: AsyncClient, world: dict) -> None:
    a = await login(client, "orgadmin@pcncloud.in")
    b = await login(client, "societyadmin@pcncloud.in")
    gw_a = await _create_gateway(client, a, world["site_a"].id, "A GW")
    site_b = world["cam_b"].site_id
    res = await client.post(
        "/api/v1/nvrs",
        headers={"Authorization": f"Bearer {b}"},
        json={"site_id": site_b, "name": "B NVR", "gateway_id": gw_a["id"]},
    )
    assert res.status_code in {403, 422}
