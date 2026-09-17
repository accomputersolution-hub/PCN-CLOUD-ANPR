"""Connectivity + NVR + camera-source columns. Does not alter ANPR event pipeline."""

from alembic import op
import sqlalchemy as sa

revision = "0004_connectivity_gateways"
down_revision = "0003_camera_streaming"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateways",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("device_type", sa.String(length=32), nullable=False),
        sa.Column("vendor", sa.String(length=80), nullable=False, server_default="GENERIC"),
        sa.Column("model", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("firmware_version", sa.String(length=64), nullable=True),
        sa.Column("vpn_status", sa.String(length=24), nullable=False, server_default="UNKNOWN"),
        sa.Column("health_status", sa.String(length=24), nullable=False, server_default="UNKNOWN"),
        sa.Column("provisioning_status", sa.String(length=24), nullable=False, server_default="UNPROVISIONED"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lan_subnet", sa.String(length=64), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("device_key_hash", sa.String(length=255), nullable=True),
        sa.Column("key_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("cpu_usage", sa.Float(), nullable=True),
        sa.Column("memory_usage", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_gateways_organization_id", "gateways", ["organization_id"])
    op.create_index("ix_gateways_site_id", "gateways", ["site_id"])

    op.create_table(
        "nvrs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("gateway_id", sa.String(length=36), sa.ForeignKey("gateways.id"), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("vendor", sa.String(length=80), nullable=False, server_default="GENERIC"),
        sa.Column("model", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("host", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_nvrs_organization_id", "nvrs", ["organization_id"])
    op.create_index("ix_nvrs_site_id", "nvrs", ["site_id"])
    op.create_index("ix_nvrs_gateway_id", "nvrs", ["gateway_id"])

    op.add_column(
        "sites",
        sa.Column("connectivity_mode", sa.String(length=32), nullable=False, server_default="EXISTING_VPN_ROUTER"),
    )
    op.add_column(
        "sites",
        sa.Column("anpr_deployment_mode", sa.String(length=32), nullable=False, server_default="LOCAL_EDGE_AGENT"),
    )
    op.add_column("sites", sa.Column("primary_gateway_id", sa.String(length=36), nullable=True))
    op.create_index("ix_sites_primary_gateway_id", "sites", ["primary_gateway_id"])

    op.add_column(
        "cameras",
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="RTSP"),
    )
    op.add_column("cameras", sa.Column("nvr_id", sa.String(length=36), nullable=True))
    op.add_column("cameras", sa.Column("channel", sa.String(length=32), nullable=True))
    op.add_column("cameras", sa.Column("anpr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("cameras", sa.Column("gateway_id", sa.String(length=36), nullable=True))
    op.add_column("cameras", sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_cameras_nvr_id_nvrs", "cameras", "nvrs", ["nvr_id"], ["id"])
    op.create_foreign_key("fk_cameras_gateway_id_gateways", "cameras", "gateways", ["gateway_id"], ["id"])
    op.create_index("ix_cameras_nvr_id", "cameras", ["nvr_id"])
    op.create_index("ix_cameras_gateway_id", "cameras", ["gateway_id"])


def downgrade() -> None:
    op.drop_index("ix_cameras_gateway_id", table_name="cameras")
    op.drop_index("ix_cameras_nvr_id", table_name="cameras")
    op.drop_constraint("fk_cameras_gateway_id_gateways", "cameras", type_="foreignkey")
    op.drop_constraint("fk_cameras_nvr_id_nvrs", "cameras", type_="foreignkey")
    op.drop_column("cameras", "last_seen")
    op.drop_column("cameras", "gateway_id")
    op.drop_column("cameras", "anpr_enabled")
    op.drop_column("cameras", "channel")
    op.drop_column("cameras", "nvr_id")
    op.drop_column("cameras", "source_type")
    op.drop_index("ix_sites_primary_gateway_id", table_name="sites")
    op.drop_column("sites", "primary_gateway_id")
    op.drop_column("sites", "anpr_deployment_mode")
    op.drop_column("sites", "connectivity_mode")
    op.drop_index("ix_nvrs_gateway_id", table_name="nvrs")
    op.drop_index("ix_nvrs_site_id", table_name="nvrs")
    op.drop_index("ix_nvrs_organization_id", table_name="nvrs")
    op.drop_table("nvrs")
    op.drop_index("ix_gateways_site_id", table_name="gateways")
    op.drop_index("ix_gateways_organization_id", table_name="gateways")
    op.drop_table("gateways")
