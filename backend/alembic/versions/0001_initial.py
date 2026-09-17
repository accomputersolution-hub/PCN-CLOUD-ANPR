"""Initial schema for PCN Cloud ANPR."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_unique_constraint("uq_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])

    op.create_table(
        "sites",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sites_organization_id", "sites", ["organization_id"])

    op.create_table(
        "user_site_access",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
    )
    op.create_index("ix_user_site_access_user_id", "user_site_access", ["user_id"])
    op.create_index("ix_user_site_access_site_id", "user_site_access", ["site_id"])
    op.create_unique_constraint("uq_user_site_access", "user_site_access", ["user_id", "site_id"])

    op.create_table(
        "gates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_gates_organization_id", "gates", ["organization_id"])
    op.create_index("ix_gates_site_id", "gates", ["site_id"])

    op.create_table(
        "cameras",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("gate_id", sa.String(length=36), sa.ForeignKey("gates.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("camera_code", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("rtsp_url_encrypted", sa.Text(), nullable=True),
        sa.Column("onvif_ip", sa.String(length=128), nullable=True),
        sa.Column("username_encrypted", sa.Text(), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("stream_type", sa.String(length=16), nullable=False),
        sa.Column("resolution", sa.String(length=32), nullable=False, server_default="1920x1080"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("connection_error", sa.String(length=500), nullable=True),
        sa.Column("last_frame_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cameras_organization_id", "cameras", ["organization_id"])
    op.create_index("ix_cameras_site_id", "cameras", ["site_id"])
    op.create_index("ix_cameras_gate_id", "cameras", ["gate_id"])

    op.create_table(
        "edge_agents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("agent_key_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cpu_usage", sa.Float(), nullable=True),
        sa.Column("memory_usage", sa.Float(), nullable=True),
        sa.Column("queue_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="0.1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_edge_agents_organization_id", "edge_agents", ["organization_id"])
    op.create_index("ix_edge_agents_site_id", "edge_agents", ["site_id"])

    op.create_table(
        "vehicles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("plate_normalized", sa.String(length=32), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_visits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currently_inside", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("visitor_note", sa.String(length=500), nullable=True),
        sa.Column("classification", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_vehicles_organization_id", "vehicles", ["organization_id"])
    op.create_index("ix_vehicles_plate_normalized", "vehicles", ["plate_normalized"])
    op.create_unique_constraint("uq_vehicles_org_plate", "vehicles", ["organization_id", "plate_normalized"])

    op.create_table(
        "vehicle_visits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("vehicle_id", sa.String(length=36), sa.ForeignKey("vehicles.id"), nullable=False),
        sa.Column("plate_normalized", sa.String(length=32), nullable=False),
        sa.Column("entry_event_id", sa.String(length=36), nullable=True),
        sa.Column("exit_event_id", sa.String(length=36), nullable=True),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gate_id", sa.String(length=36), sa.ForeignKey("gates.id"), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_vehicle_visits_organization_id", "vehicle_visits", ["organization_id"])
    op.create_index("ix_vehicle_visits_site_id", "vehicle_visits", ["site_id"])
    op.create_index("ix_vehicle_visits_vehicle_id", "vehicle_visits", ["vehicle_id"])
    op.create_index("ix_vehicle_visits_plate_normalized", "vehicle_visits", ["plate_normalized"])
    op.create_index("ix_vehicle_visits_status", "vehicle_visits", ["status"])

    op.create_table(
        "anpr_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("site_id", sa.String(length=36), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("gate_id", sa.String(length=36), sa.ForeignKey("gates.id"), nullable=False),
        sa.Column("camera_id", sa.String(length=36), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("vehicle_id", sa.String(length=36), sa.ForeignKey("vehicles.id"), nullable=True),
        sa.Column("visit_id", sa.String(length=36), sa.ForeignKey("vehicle_visits.id"), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("plate_text", sa.String(length=64), nullable=False),
        sa.Column("raw_ocr_text", sa.String(length=64), nullable=False),
        sa.Column("plate_normalized", sa.String(length=32), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=False),
        sa.Column("plate_detection_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vehicle_detection_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_path", sa.String(length=500), nullable=True),
        sa.Column("plate_crop_path", sa.String(length=500), nullable=True),
        sa.Column("vehicle_crop_path", sa.String(length=500), nullable=True),
        sa.Column("processing_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="EDGE"),
        sa.Column("sync_status", sa.String(length=16), nullable=False, server_default="SYNCED"),
        sa.Column("classification", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_anpr_events_organization_id", "anpr_events", ["organization_id"])
    op.create_index("ix_anpr_events_site_id", "anpr_events", ["site_id"])
    op.create_index("ix_anpr_events_camera_id", "anpr_events", ["camera_id"])
    op.create_index("ix_anpr_events_plate_normalized", "anpr_events", ["plate_normalized"])
    op.create_index("ix_anpr_events_timestamp", "anpr_events", ["timestamp"])
    op.create_index("ix_anpr_events_gate_id", "anpr_events", ["gate_id"])

    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("anpr_events.id"), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=80), nullable=False, server_default="image/jpeg"),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_snapshots_organization_id", "snapshots", ["organization_id"])
    op.create_index("ix_snapshots_event_id", "snapshots", ["event_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    for table in [
        "audit_logs",
        "snapshots",
        "anpr_events",
        "vehicle_visits",
        "vehicles",
        "edge_agents",
        "cameras",
        "gates",
        "user_site_access",
        "sites",
        "refresh_tokens",
        "users",
        "organizations",
    ]:
        op.drop_table(table)
