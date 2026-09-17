"""Data migration: rename reserved-TLD demo emails to @pcncloud.in and reset demo passwords.

This mirrors app.db.seed.ensure_demo_users so existing development databases can be
upgraded without wiping tenant data. Safe / idempotent.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column

revision = "0002_demo_email_migration"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# bcrypt hash for ChangeMe@12345 is generated at upgrade time via the app hasher
# when available; fallback uses a one-time hash placeholder only if import fails.


MAPPINGS = (
    ("admin@pcncloud.local", "admin@pcncloud.in"),
    ("orgadmin@hotela.local", "orgadmin@pcncloud.in"),
    ("manager@hotela.local", "manager@pcncloud.in"),
    ("guard@hotela.local", "guard@pcncloud.in"),
    ("viewer@hotela.local", "viewer@pcncloud.in"),
    ("admin@societyb.local", "societyadmin@pcncloud.in"),
)


def upgrade() -> None:
    try:
        from app.core.security import hash_password

        password_hash = hash_password("ChangeMe@12345")
    except Exception:
        # Alembic environments without app import still rename emails; password
        # is corrected on next app startup by ensure_demo_users().
        password_hash = None

    users = table(
        "users",
        column("email", sa.String),
        column("hashed_password", sa.String),
        column("is_active", sa.Boolean),
    )
    conn = op.get_bind()
    for old_email, new_email in MAPPINGS:
        existing_new = conn.execute(sa.select(users.c.email).where(users.c.email == new_email)).first()
        existing_old = conn.execute(sa.select(users.c.email).where(users.c.email == old_email)).first()
        if existing_old and not existing_new:
            values = {"email": new_email, "is_active": True}
            if password_hash is not None:
                values["hashed_password"] = password_hash
            conn.execute(users.update().where(users.c.email == old_email).values(**values))
        elif existing_new and password_hash is not None:
            conn.execute(
                users.update()
                .where(users.c.email == new_email)
                .values(hashed_password=password_hash, is_active=True)
            )


def downgrade() -> None:
    users = table(
        "users",
        column("email", sa.String),
    )
    conn = op.get_bind()
    for old_email, new_email in MAPPINGS:
        existing_old = conn.execute(sa.select(users.c.email).where(users.c.email == old_email)).first()
        existing_new = conn.execute(sa.select(users.c.email).where(users.c.email == new_email)).first()
        if existing_new and not existing_old:
            conn.execute(users.update().where(users.c.email == new_email).values(email=old_email))
