from httpx import AsyncClient

from tests.conftest import login


async def test_dashboard_summary(client: AsyncClient, world: dict) -> None:
    token = await login(client, "orgadmin@pcncloud.in")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/api/v1/mock/events",
        headers=headers,
        json={"camera_id": world["cam_in"].id, "plate_text": "MH12AB9999", "direction": "ENTRY"},
    )
    res = await client.get("/api/v1/dashboard/summary", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["entries_today"] >= 1
    assert "recent_events" in body
