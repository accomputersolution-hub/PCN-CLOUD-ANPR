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
