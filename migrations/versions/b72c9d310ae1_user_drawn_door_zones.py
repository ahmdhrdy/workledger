"""Versioned user-drawn door zones and immutable crossing logs."""

import sqlalchemy as sa
from alembic import op

revision = "b72c9d310ae1"
down_revision = "a91f7302c114"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "camera_zones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "camera_id", sa.String(36), sa.ForeignKey("cameras.id"), nullable=False, unique=True
        ),
        sa.Column(
            "company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False, index=True
        ),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("sites.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "camera_zone_versions",
        sa.Column("zone_id", sa.String(36), sa.ForeignKey("camera_zones.id"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "door_crossings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "zone_id", sa.String(36), sa.ForeignKey("camera_zones.id"), nullable=False, index=True
        ),
        sa.Column("zone_version", sa.Integer(), nullable=False),
        sa.Column(
            "camera_id", sa.String(36), sa.ForeignKey("cameras.id"), nullable=False, index=True
        ),
        sa.Column(
            "company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False, index=True
        ),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("sites.id"), nullable=False, index=True),
        sa.Column("track_id", sa.String(100), nullable=False),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employees.id")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_event_id", sa.String(36), sa.ForeignKey("source_events.id"), unique=True),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("door_crossings")
    op.drop_table("camera_zone_versions")
    op.drop_table("camera_zones")
