from httpx import AsyncClient

from tests.conftest import login


async def test_org_admin_cannot_see_other_org_events(client: AsyncClient, world: dict) -> None:
    a_token = await login(client, "orgadmin@pcncloud.in")
    b_token = await login(client, "societyadmin@pcncloud.in")
    created = await client.post(
        "/api/v1/mock/events",
        headers={"Authorization": f"Bearer {b_token}"},
        json={"camera_id": world["cam_b"].id, "plate_text": "MH14SECRET1"},
    )
    assert created.status_code == 200, created.text
    listed = await client.get("/api/v1/events?plate=MH14SECRET1", headers={"Authorization": f"Bearer {a_token}"})
    assert listed.status_code == 200
    assert listed.json()["items"] == []


async def test_super_admin_can_see_both_orgs(client: AsyncClient, world: dict) -> None:
    b_token = await login(client, "societyadmin@pcncloud.in")
    await client.post(
        "/api/v1/mock/events",
        headers={"Authorization": f"Bearer {b_token}"},
        json={"camera_id": world["cam_b"].id, "plate_text": "MH14SECRET1"},
    )
    super_token = await login(client, "admin@pcncloud.in")
    listed = await client.get("/api/v1/events?plate=MH14SECRET1", headers={"Authorization": f"Bearer {super_token}"})
    assert listed.json()["meta"]["total"] >= 1
