from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import ensure_utc
from app.models.refresh_token import RefreshToken
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


def test_ensure_utc_treats_naive_as_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = ensure_utc(naive)
    assert aware.tzinfo is not None
    assert aware == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    already = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert ensure_utc(already) == already


async def test_refresh_rejects_expired_token_even_if_expires_at_naive(
    client: AsyncClient, session: AsyncSession, world: dict
) -> None:
    """SQLite often returns naive expires_at; expiry must still be enforced vs aware UTC now."""
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "orgadmin@pcncloud.in", "password": "ChangeMe@12345"},
    )
    assert login_res.status_code == 200
    refresh_token = login_res.json()["refresh_token"]

    stored = (await session.execute(select(RefreshToken))).scalars().all()
    assert stored
    for row in stored:
        # Force a naive past expiry (simulates SQLite round-trip + expired token).
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    await session.commit()

    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh.status_code == 401
