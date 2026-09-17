from httpx import AsyncClient

from tests.conftest import login


async def test_login_success(client: AsyncClient, world: dict) -> None:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@pcncloud.in", "password": "ChangeMe@12345"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "SUPER_ADMIN"


async def test_login_rejects_bad_password(client: AsyncClient, world: dict) -> None:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@pcncloud.in", "password": "wrong-password"},
    )
    assert res.status_code == 401


async def test_me_requires_auth(client: AsyncClient) -> None:
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401


async def test_refresh_and_logout(client: AsyncClient, world: dict) -> None:
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "orgadmin@pcncloud.in", "password": "ChangeMe@12345"},
    )
    tokens = login_res.json()
    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 200
    headers = {"Authorization": f"Bearer {refresh.json()['access_token']}"}
    out = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh.json()["refresh_token"]},
        headers=headers,
    )
    assert out.status_code == 200
