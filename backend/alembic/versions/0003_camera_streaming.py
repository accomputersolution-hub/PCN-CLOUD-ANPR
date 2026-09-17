"""Add camera.streaming flag for edge RTSP start/stop control."""

from alembic import op
import sqlalchemy as sa

revision = "0003_camera_streaming"
down_revision = "0002_demo_email_migration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("streaming", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("cameras", "streaming")
