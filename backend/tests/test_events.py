from httpx import AsyncClient

from tests.conftest import login


async def test_mock_event_and_search(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/v1/mock/events",
        headers=headers,
        json={"camera_id": world["cam_in"].id, "plate_text": "mh 12 ab 1234", "direction": "ENTRY"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["plate_normalized"] == "MH12AB1234"
    listed = await client.get("/api/v1/events?plate=MH12AB1234", headers=headers)
    assert listed.json()["meta"]["total"] >= 1
    vehicle = await client.get("/api/v1/vehicles/MH12AB1234", headers=headers)
    assert vehicle.status_code == 200
    assert vehicle.json()["vehicle"]["plate_normalized"] == "MH12AB1234"


async def test_super_admin_can_lookup_vehicle_by_plate(client: AsyncClient, world: dict) -> None:
    """SUPER_ADMIN has null organization_id — plate detail must still resolve (SQL path)."""
    org_token = await login(client, "orgadmin@pcncloud.in")
    org_headers = {"Authorization": f"Bearer {org_token}"}
    created = await client.post(
        "/api/v1/mock/events",
        headers=org_headers,
        json={"camera_id": world["cam_in"].id, "plate_text": "MH20DV2366", "direction": "ENTRY"},
    )
    assert created.status_code == 200, created.text

    admin_token = await login(client, "admin@pcncloud.in")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    vehicle = await client.get("/api/v1/vehicles/MH20DV2366", headers=admin_headers)
    assert vehicle.status_code == 200, vehicle.text
    assert vehicle.json()["vehicle"]["plate_normalized"] == "MH20DV2366"

    missing = await client.get("/api/v1/vehicles/ZZ99ZZ9999", headers=admin_headers)
    assert missing.status_code == 404


async def test_idempotent_event_id(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "camera_id": world["cam_in"].id,
        "plate_text": "MH04EF9012",
        "event_id": "11111111-1111-1111-1111-111111111111",
    }
    first = await client.post("/api/v1/mock/events", headers=headers, json=payload)
    second = await client.post("/api/v1/mock/events", headers=headers, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
