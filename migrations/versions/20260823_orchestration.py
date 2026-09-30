"""add schedule generation runs and single device state

Revision ID: 20260823_orchestration
Revises: 20260817_default_preset
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_orchestration"
down_revision: str | None = "20260817_default_preset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("interval", "custom", name="schedulemode", native_enum=False),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("anchor_time", sa.String(length=5), nullable=True),
        sa.Column("custom_times", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.String(length=5), nullable=True),
        sa.Column("quiet_end", sa.String(length=5), nullable=True),
        sa.Column("lead_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_schedule_config_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("planned_display_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "generating",
                "rendering",
                "ready",
                "published",
                "failed",
                name="generationrunstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("weather_snapshot", sa.JSON(), nullable=True),
        sa.Column("prompt_snapshot", sa.JSON(), nullable=True),
        sa.Column("generate_job_id", sa.Uuid(), nullable=True),
        sa.Column("render_job_id", sa.Uuid(), nullable=True),
        sa.Column("source_asset_id", sa.Uuid(), nullable=True),
        sa.Column("frame_id", sa.Uuid(), nullable=True),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("render_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("render_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["generate_job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["render_job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("frame_id"),
        sa.UniqueConstraint("generate_job_id"),
        sa.UniqueConstraint("planned_display_at"),
        sa.UniqueConstraint("render_job_id"),
    )
    op.create_index(
        "ix_generation_runs_status_display",
        "generation_runs",
        ["status", "planned_display_at"],
        unique=False,
    )
    op.create_table(
        "current_frame",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("generation_run_id", sa.Uuid(), nullable=True),
        sa.Column("frame_id", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_current_frame_singleton"),
        sa.ForeignKeyConstraint(["generation_run_id"], ["generation_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("frame_id"),
        sa.UniqueConstraint("generation_run_id"),
    )
    op.create_table(
        "device_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("firmware_version", sa.String(length=128), nullable=True),
        sa.Column("last_frame_id", sa.Uuid(), nullable=True),
        sa.Column("wifi_rssi_dbm", sa.Integer(), nullable=True),
        sa.Column("battery_mv", sa.Integer(), nullable=True),
        sa.Column("battery_pct", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.String(length=2000), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_device_status_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("device_status")
    op.drop_table("current_frame")
    op.drop_index("ix_generation_runs_status_display", table_name="generation_runs")
    op.drop_table("generation_runs")
    op.drop_table("schedule_config")
