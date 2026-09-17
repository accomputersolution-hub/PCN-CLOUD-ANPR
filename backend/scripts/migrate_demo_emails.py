"""Apply demo email upsert against the configured database and verify login."""
from __future__ import annotations

import asyncio
import os

os.environ.pop("DATABASE_URL", None)

from app.core.config import get_settings

get_settings.cache_clear()

import app.db.session as sess

sess._engine = None
sess.SessionLocal = None

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.seed import DEMO_PASSWORD, ensure_demo_users
from app.db.session import get_engine, get_session_factory
from app.main import app
from app.models.user import User


async def main() -> None:
    settings = get_settings()
    print("DATABASE_URL", settings.database_url)
    factory = get_session_factory()

    async with factory() as db:
        before = (await db.execute(select(User.email, User.role).order_by(User.email))).all()
        print("before", [dict(r._mapping) for r in before])
        result = await ensure_demo_users(db)
        print("ensure_result", result)

    async with factory() as db:
        after = (
            await db.execute(select(User.id, User.email, User.role, User.organization_id).order_by(User.email))
        ).all()
        print("after", [dict(r._mapping) for r in after])
        count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        print("user_count", count)

    # Login against the SAME PostgreSQL via the real app + DB dependency.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@pcncloud.in", "password": DEMO_PASSWORD},
        )
        print("login_status", res.status_code)
        body = res.json()
        if res.status_code == 200:
            print("login_email", body["user"]["email"])
            print("login_role", body["user"]["role"])
            print("has_access", bool(body.get("access_token")))
            print("has_refresh", bool(body.get("refresh_token")))
        else:
            print("login_body", body)

    # Idempotency: run ensure again; user count must stay the same.
    async with factory() as db:
        again = await ensure_demo_users(db)
        count2 = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        print("ensure_again", again)
        print("user_count_after_second_run", count2)

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
