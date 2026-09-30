from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AssetKind(enum.StrEnum):
    SOURCE = "source"
    PREVIEW = "preview"
    FRAME = "frame"
    PROVIDER_OUTPUT = "provider_output"


class JobType(enum.StrEnum):
    RENDER = "render"
    GENERATE = "generate"


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobErrorCode(enum.StrEnum):
    WORKER_ERROR = "worker_error"
    LEASE_EXPIRED = "lease_expired"
    INVALID_OUTPUT = "invalid_output"
    INTERNAL_ERROR = "internal_error"


class ScheduleMode(enum.StrEnum):
    INTERVAL = "interval"
    CUSTOM = "custom"


class GenerationRunStatus(enum.StrEnum):
    PLANNED = "planned"
    QUEUED = "queued"
    GENERATING = "generating"
    RENDERING = "rendering"
    READY = "ready"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[AssetKind] = mapped_column(
        Enum(
            AssetKind,
            values_callable=lambda members: [item.value for item in members],
            native_enum=False,
        ),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claimable", "status", "available_at", "created_at"),
        Index("ix_jobs_expired_leases", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    job_type: Mapped[JobType] = mapped_column(
        Enum(
            JobType,
            values_callable=lambda members: [item.value for item in members],
            native_enum=False,
        ),
        nullable=False,
    )
    payload_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            values_callable=lambda members: [item.value for item in members],
            native_enum=False,
        ),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[JobErrorCode | None] = mapped_column(
        Enum(
            JobErrorCode,
            values_callable=lambda members: [item.value for item in members],
            native_enum=False,
        )
    )
    error_detail: Mapped[str | None] = mapped_column(String(2000))
    result_refs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingPreset(Base):
    __tablename__ = "processing_presets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessingPresetRevision(Base):
    __tablename__ = "processing_preset_revisions"

    preset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("processing_presets.id", ondelete="RESTRICT"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(primary_key=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScheduleConfig(Base):
    __tablename__ = "schedule_config"
    __table_args__ = (CheckConstraint("id = 1", name="ck_schedule_config_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mode: Mapped[ScheduleMode] = mapped_column(
        Enum(
            ScheduleMode,
            values_callable=lambda members: [item.value for item in members],
            native_enum=False,
        ),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    anchor_time: Mapped[str | None] = mapped_column(String(5))
    custom_times: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    quiet_start: Mapped[str | None] = mapped_column(String(5))
    quiet_end: Mapped[str | None] = mapped_column(String(5))
    lead_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (Index("ix_generation_runs_status_display", "status", "planned_display_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    planned_display_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), unique=True, nullable=False
    )
    generation_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[GenerationRunStatus] = mapped_column(
        Enum(
            GenerationRunStatus,
            values_callable=lambda members: [item.value for item in members],
            native_enum=False,
        ),
        nullable=False,
    )
    weather_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    prompt_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generate_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True
    )
    render_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True
    )
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="RESTRICT")
    )
    frame_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)
    generation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    render_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    render_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CurrentFrame(Base):
    __tablename__ = "current_frame"
    __table_args__ = (CheckConstraint("id = 1", name="ck_current_frame_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("generation_runs.id", ondelete="RESTRICT"), unique=True
    )
    frame_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)
    sequence: Mapped[int | None] = mapped_column(BigInteger)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceStatus(Base):
    __tablename__ = "device_status"
    __table_args__ = (CheckConstraint("id = 1", name="ck_device_status_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firmware_version: Mapped[str | None] = mapped_column(String(128))
    last_frame_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    wifi_rssi_dbm: Mapped[int | None] = mapped_column(Integer)
    battery_mv: Mapped[int | None] = mapped_column(Integer)
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(String(2000))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@event.listens_for(ProcessingPresetRevision, "before_update")
def _reject_preset_revision_update(
    mapper: object, connection: object, target: ProcessingPresetRevision
) -> None:
    del mapper, connection, target
    raise ValueError("processing preset revisions are immutable")


@event.listens_for(Asset, "before_update")
def _reject_asset_update(mapper: object, connection: object, target: Asset) -> None:
    del mapper, connection, target
    raise ValueError("asset metadata is immutable")
