from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .assets import ensure_assets_root, publish_asset_bytes, read_asset_bytes
from .converter import DitheringAlgorithm, ProcessingPreset, convert_panel_image
from .drafts import (
    MAX_DRAFT_REVISION,
    DraftBusyError,
    DraftCorruptError,
    DraftLimitError,
    DraftNotFoundError,
    DraftStore,
    DraftSupersededError,
)
from .jobs import CompletionResult, JobRepository
from .orchestration import GenerationRunRepository, build_current_frame_lock_statement
from .persistence import (
    Asset,
    AssetKind,
    CurrentFrame,
    DeviceStatus,
    GenerationRun,
    Job,
    JobErrorCode,
    JobStatus,
    JobType,
    ProcessingPresetRevision,
    ScheduleConfig,
)
from .persistence import ProcessingPreset as ProcessingPresetRecord
from .persistence import ScheduleMode as DbScheduleMode
from .presets import PresetRepository
from .providers import PROVIDER_CATALOG
from .schedule import ScheduleMode, ScheduleSpec, compute_display_slots

DEFAULT_PALETTE_CODES = (0x0, 0x1, 0x2, 0x3, 0x5, 0x6)
_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eink-render")
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eink-draft-io")


class CurrentFrameInvariantError(RuntimeError):
    """Raised when the migration-seeded current-frame singleton is absent."""


class JobCreateRequest(BaseModel):
    job_type: JobType
    payload: dict[str, Any]
    max_attempts: int = Field(ge=1, le=20)


class WorkerClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=255)
    lease_seconds: int = Field(ge=10, le=3600)
    job_types: list[JobType] = Field(
        default_factory=lambda: [JobType.RENDER, JobType.GENERATE], min_length=1
    )


class WorkerCompleteRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=255)
    result_refs: dict[str, Any]


class WorkerFailRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=255)
    error_code: JobErrorCode
    error_detail: str = Field(max_length=2000)
    retryable: bool
    retry_delay_seconds: int = Field(ge=0, le=86400)


class ProcessingSettingsRequest(BaseModel):
    brightness: float = Field(ge=-100, le=100)
    contrast: float = Field(ge=-100, le=100)
    saturation: float = Field(ge=-100, le=100)
    gamma: float = Field(ge=0.1, le=5)
    sharpness: float = Field(ge=-100, le=100)
    dithering: DitheringAlgorithm
    dithering_strength: float = Field(ge=0, le=1)


class DraftPreviewRequest(ProcessingSettingsRequest):
    revision: int = Field(ge=2, le=MAX_DRAFT_REVISION)


class PresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    settings: ProcessingSettingsRequest
    make_default: bool = False


class PresetRevisionRequest(BaseModel):
    settings: ProcessingSettingsRequest


class DeviceStatusRequest(BaseModel):
    firmware_version: str | None = Field(default=None, max_length=128)
    last_frame_id: uuid.UUID | None = None
    wifi_rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    battery_mv: int | None = Field(default=None, ge=0, le=20000)
    battery_pct: int | None = Field(default=None, ge=0, le=100)
    error_code: str | None = Field(default=None, max_length=128)
    error_detail: str | None = Field(default=None, max_length=2000)


class ScheduleRequest(BaseModel):
    enabled: bool
    mode: DbScheduleMode
    interval_minutes: int = Field(ge=30, le=1440)
    anchor_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    custom_times: list[str]
    quiet_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_schedule(self) -> ScheduleRequest:
        self.custom_times = sorted(set(self.custom_times))
        self.to_spec()
        return self

    def to_spec(self) -> ScheduleSpec:
        return ScheduleSpec(
            enabled=self.enabled,
            mode=ScheduleMode(self.mode.value),
            interval_minutes=self.interval_minutes,
            anchor_time=time.fromisoformat(self.anchor_time),
            custom_times=tuple(time.fromisoformat(value) for value in self.custom_times),
            quiet_start=time.fromisoformat(self.quiet_start) if self.quiet_start else None,
            quiet_end=time.fromisoformat(self.quiet_end) if self.quiet_end else None,
        )


def _job_response(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "job_type": job.job_type.value,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "result_refs": job.result_refs,
        "error_code": job.error_code.value if job.error_code else None,
    }


def _worker_job_response(job: Job) -> dict[str, Any]:
    response = _job_response(job)
    response.update(
        {
            "payload_schema_version": job.payload_schema_version,
            "payload": job.payload,
            "lease_owner": job.lease_owner,
            "lease_expires_at": job.lease_expires_at,
        }
    )
    return response


def _asset_response(asset: Asset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "kind": asset.kind.value,
        "media_type": asset.media_type,
        "byte_length": asset.byte_length,
        "sha256": asset.sha256,
        "width": asset.metadata_json.get("width"),
        "height": asset.metadata_json.get("height"),
        "url": f"/api/v1/assets/{asset.id}",
    }


def _preset_response(
    preset: ProcessingPresetRecord, revision: ProcessingPresetRevision
) -> dict[str, Any]:
    return {
        "id": str(preset.id),
        "name": preset.name,
        "is_default": preset.is_default,
        "revision": revision.revision,
        "settings": revision.settings,
        "created_at": revision.created_at,
    }


def _duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _generation_source_response(asset: Asset | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    return {
        "id": str(asset.id),
        "url": f"/api/v1/assets/{asset.id}",
        "download_url": f"/api/v1/assets/{asset.id}/download",
        "media_type": asset.media_type,
        "byte_length": asset.byte_length,
        "sha256": asset.sha256,
        "width": asset.metadata_json.get("width"),
        "height": asset.metadata_json.get("height"),
    }


def _generation_response(run: GenerationRun, source: Asset | None = None) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "planned_display_at": run.planned_display_at,
        "generation_due_at": run.generation_due_at,
        "provider": run.provider,
        "model": run.model,
        "status": run.status.value,
        "weather_snapshot": run.weather_snapshot,
        "prompt_snapshot": run.prompt_snapshot,
        "generation_duration_seconds": _duration_seconds(
            run.generation_started_at, run.generation_completed_at
        ),
        "render_duration_seconds": _duration_seconds(
            run.render_started_at, run.render_completed_at
        ),
        "frame_id": str(run.frame_id) if run.frame_id else None,
        "preview_url": (f"/api/v1/frames/{run.frame_id}/preview.png" if run.frame_id else None),
        "source": _generation_source_response(source),
        "error_code": run.error_code,
        "error_detail": run.error_detail,
    }


def _schedule_request(config: ScheduleConfig | None) -> ScheduleRequest:
    if config is None:
        return ScheduleRequest(
            enabled=False,
            mode=DbScheduleMode.INTERVAL,
            interval_minutes=120,
            anchor_time="08:00",
            custom_times=["08:00"],
            quiet_start=None,
            quiet_end=None,
        )
    return ScheduleRequest(
        enabled=config.enabled,
        mode=config.mode,
        interval_minutes=config.interval_minutes or 120,
        anchor_time=config.anchor_time or "08:00",
        custom_times=config.custom_times,
        quiet_start=config.quiet_start,
        quiet_end=config.quiet_end,
    )


def _schedule_response(request: ScheduleRequest, *, now: datetime) -> dict[str, Any]:
    spec = request.to_spec()
    next_runs = compute_display_slots(spec, now, now + timedelta(hours=48))[:20]
    return {
        **request.model_dump(mode="json"),
        "timezone": "Europe/Moscow",
        "lead_minutes": 15,
        "next_runs": [value.isoformat() for value in next_runs],
    }


def _fsync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_app(
    *,
    artifact_root: Path,
    max_upload_bytes: int = 25 * 1024 * 1024,
    session_factory: sessionmaker[Session] | None = None,
    worker_token: str | None = None,
    device_token: str | None = None,
    panel_rotation: Literal["clockwise", "counterclockwise", "transverse"] = "clockwise",
    frontend_dist: Path | None = None,
    draft_limit: int = 16,
    asset_write_hook: Callable[[], None] | None = None,
) -> FastAPI:
    app = FastAPI(title="E-Ink Weather Frame")
    artifact_root.mkdir(parents=True, exist_ok=True)
    draft_store = DraftStore(artifact_root, limit=draft_limit)
    render_slots = asyncio.Semaphore(2)
    activation_lock = threading.Lock()

    async def acquire_render_slot() -> None:
        try:
            await asyncio.wait_for(render_slots.acquire(), timeout=0.01)
        except TimeoutError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="render queue is full",
            ) from error

    async def run_io(function: Any, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(_IO_EXECUTOR, function, *args)

    def render_draft_preview(image: Image.Image, settings_data: Any) -> Image.Image:
        settings = ProcessingSettingsRequest.model_validate(settings_data)
        preset = ProcessingPreset(
            brightness=settings.brightness,
            contrast=settings.contrast,
            saturation=settings.saturation,
            gamma=settings.gamma,
            sharpness=settings.sharpness,
            dithering_strength=settings.dithering_strength,
        )
        return convert_panel_image(
            image,
            palette_codes=DEFAULT_PALETTE_CODES,
            preset=preset,
            dithering=settings.dithering,
            rotation=panel_rotation,
        ).preview

    async def require_worker_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if worker_token is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="worker authentication is not configured",
            )
        expected = f"Bearer {worker_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid worker credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def require_device_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if device_token is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="device authentication is not configured",
            )
        expected = f"Bearer {device_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid device credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/api/v1/health")
    async def get_health() -> dict[str, str]:
        if not os.access(artifact_root, os.W_OK):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="artifact store is not writable",
            )
        return {"status": "ok", "artifact_store": "writable"}

    @app.get("/api/v1/provider-kinds")
    async def get_provider_kinds() -> list[dict[str, Any]]:
        return [provider.public_dict() for provider in PROVIDER_CATALOG]

    @app.get("/api/v1/schedule")
    async def get_schedule() -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="schedule store is not configured",
            )
        with session_factory() as session:
            request = _schedule_request(session.get(ScheduleConfig, 1))
        return _schedule_response(request, now=datetime.now(UTC))

    @app.put("/api/v1/schedule")
    async def update_schedule(request: ScheduleRequest) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="schedule store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            config = session.get(ScheduleConfig, 1)
            if config is None:
                config = ScheduleConfig(id=1)
                session.add(config)
            config.enabled = request.enabled
            config.mode = request.mode
            config.timezone = "Europe/Moscow"
            config.interval_minutes = request.interval_minutes
            config.anchor_time = request.anchor_time
            config.custom_times = request.custom_times
            config.quiet_start = request.quiet_start
            config.quiet_end = request.quiet_end
            config.lead_minutes = 15
            config.updated_at = now
            session.commit()
        return _schedule_response(request, now=now)

    @app.get("/api/v1/generations")
    async def list_generations(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="generation history is not configured",
            )
        with session_factory() as session:
            runs = session.scalars(
                select(GenerationRun)
                .order_by(GenerationRun.planned_display_at.desc(), GenerationRun.id.desc())
                .limit(limit)
            )
            run_list = list(runs)
            source_ids = {run.source_asset_id for run in run_list if run.source_asset_id is not None}
            sources = {
                asset.id: asset
                for asset in session.scalars(select(Asset).where(Asset.id.in_(source_ids)))
            }
            return [
                _generation_response(
                    run,
                    sources.get(run.source_asset_id) if run.source_asset_id is not None else None,
                )
                for run in run_list
            ]

    @app.get("/api/v1/device/manifest")
    async def get_device_manifest(
        if_none_match: Annotated[str | None, Header()] = None,
        _authorized: None = Depends(require_device_token),
    ) -> Response:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="device state is not configured",
            )
        with session_factory() as session:
            current = session.get(CurrentFrame, 1)
            if current is None or current.frame_id is None:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            run = (
                session.get(GenerationRun, current.generation_run_id)
                if current.generation_run_id is not None
                else None
            )
            if current.generation_run_id is not None and run is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="current frame references a missing generation run",
                )
            manifest_path = artifact_root / str(current.frame_id) / "manifest.json"
            if not manifest_path.is_file():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="current frame artifact is missing",
                )
            frame_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            planned_display_at = (
                run.planned_display_at
                if run is not None
                else current.activated_at or datetime.now(UTC)
            )
            if planned_display_at.utcoffset() is None:
                planned_display_at = planned_display_at.replace(tzinfo=UTC)
            sequence = current.sequence or int(planned_display_at.timestamp())
            response_manifest = {
                "schema_version": 1,
                "target": "weather-frame",
                "panel": "GDEP133C02",
                "sequence": sequence,
                "frame_id": str(current.frame_id),
                "planned_display_at": planned_display_at,
                "byte_length": frame_manifest["byte_length"],
                "sha256": frame_manifest["sha256"],
                "profile": frame_manifest["palette_encoding"],
                "url": frame_manifest["url"],
            }
            validator = f"{sequence}:{current.frame_id}:{frame_manifest['sha256']}".encode()
            etag = f'"{hashlib.sha256(validator).hexdigest()}"'
            if if_none_match == etag:
                return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
            return JSONResponse(
                jsonable_encoder(response_manifest),
                headers={"ETag": etag, "Cache-Control": "no-cache"},
            )

    @app.get("/api/v1/device/status")
    async def get_device_status() -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="device state is not configured",
            )
        with session_factory() as session:
            current = session.get(CurrentFrame, 1)
            reported = session.get(DeviceStatus, 1)
            current_frame_id = current.frame_id if current is not None else None
            last_frame_id = reported.last_frame_id if reported is not None else None
            reported_at = reported.reported_at if reported is not None else None
            if reported_at is not None and reported_at.utcoffset() is None:
                reported_at = reported_at.replace(tzinfo=UTC)
            return {
                "firmware_version": reported.firmware_version if reported is not None else None,
                "current_frame_id": str(current_frame_id) if current_frame_id else None,
                "last_frame_id": str(last_frame_id) if last_frame_id else None,
                "is_current": current_frame_id is not None and current_frame_id == last_frame_id,
                "wifi_rssi_dbm": reported.wifi_rssi_dbm if reported is not None else None,
                "error_code": reported.error_code if reported is not None else None,
                "error_detail": reported.error_detail if reported is not None else None,
                "reported_at": reported_at,
            }

    @app.post("/api/v1/device/status", status_code=status.HTTP_204_NO_CONTENT)
    async def report_device_status(
        request: DeviceStatusRequest,
        _authorized: None = Depends(require_device_token),
    ) -> Response:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="device state is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            device_status = session.get(DeviceStatus, 1)
            if device_status is None:
                device_status = DeviceStatus(id=1, reported_at=now)
                session.add(device_status)
            for field, value in request.model_dump().items():
                setattr(device_status, field, value)
            device_status.reported_at = now
            session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/presets", status_code=status.HTTP_201_CREATED)
    async def create_preset(request: PresetCreateRequest) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="preset store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            preset, revision = PresetRepository(session).create(
                name=request.name,
                settings=request.settings.model_dump(mode="json"),
                make_default=request.make_default,
                now=now,
            )
            session.commit()
            return _preset_response(preset, revision)

    @app.get("/api/v1/presets")
    async def list_presets() -> list[dict[str, Any]]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="preset store is not configured",
            )
        with session_factory() as session:
            return [
                _preset_response(preset, revision)
                for preset, revision in PresetRepository(session).list_latest()
            ]

    @app.post("/api/v1/presets/{preset_id}/revisions", status_code=status.HTTP_201_CREATED)
    async def append_preset_revision(
        preset_id: uuid.UUID, request: PresetRevisionRequest
    ) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="preset store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            preset = session.get(ProcessingPresetRecord, preset_id)
            if preset is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="preset not found"
                )
            revision = PresetRepository(session).append_revision(
                preset_id=preset_id,
                settings=request.settings.model_dump(mode="json"),
                now=now,
            )
            session.commit()
            return _preset_response(preset, revision)

    @app.post("/api/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        request: JobCreateRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            job = JobRepository(session).enqueue(
                job_type=request.job_type,
                payload=request.payload,
                idempotency_key=idempotency_key,
                max_attempts=request.max_attempts,
                available_at=now,
                now=now,
            )
            session.commit()
            return _job_response(job)

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: uuid.UUID) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            return _job_response(job)

    @app.post("/api/v1/workers/claim")
    async def claim_job(
        request: WorkerClaimRequest,
        _authorized: None = Depends(require_worker_token),
    ) -> Response:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            job = JobRepository(session).claim_next(
                worker_id=request.worker_id,
                now=now,
                lease_duration=timedelta(seconds=request.lease_seconds),
                job_types=tuple(request.job_types),
            )
            if job is None:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            if job.job_type == JobType.GENERATE and "generation_run_id" in job.payload:
                try:
                    run_id = uuid.UUID(str(job.payload["generation_run_id"]))
                    GenerationRunRepository(session).generation_started(run_id=run_id, now=now)
                except (ValueError, LookupError) as error:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="generate job references an invalid generation run",
                    ) from error
            session.commit()
            return JSONResponse(jsonable_encoder(_worker_job_response(job)))

    @app.post("/api/v1/jobs/{job_id}/heartbeat")
    async def heartbeat_job(
        job_id: uuid.UUID,
        request: WorkerClaimRequest,
        _authorized: None = Depends(require_worker_token),
    ) -> Response:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            accepted = JobRepository(session).heartbeat(
                job_id=job_id,
                worker_id=request.worker_id,
                now=now,
                lease_duration=timedelta(seconds=request.lease_seconds),
            )
            if not accepted:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="job lease is not owned or has expired",
                )
            session.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/jobs/{job_id}/complete")
    async def complete_job(
        job_id: uuid.UUID,
        request: WorkerCompleteRequest,
        _authorized: None = Depends(require_worker_token),
    ) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            generation_context: tuple[uuid.UUID, uuid.UUID, GenerationRun] | None = None
            if job.job_type == JobType.GENERATE and "generation_run_id" in job.payload:
                try:
                    run_id = uuid.UUID(str(job.payload["generation_run_id"]))
                    source_asset_id = uuid.UUID(str(request.result_refs["provider_asset_id"]))
                except (KeyError, ValueError) as error:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="generate completion requires valid generation_run_id and provider_asset_id",
                    ) from error
                run = session.get(GenerationRun, run_id)
                asset = session.get(Asset, source_asset_id)
                if run is None or asset is None or asset.kind != AssetKind.PROVIDER_OUTPUT:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="generate completion references missing run or provider asset",
                    )
                generation_context = (run_id, source_asset_id, run)
            completion = JobRepository(session).complete(
                job_id=job_id,
                worker_id=request.worker_id,
                result_refs=request.result_refs,
                now=now,
            )
            if completion is CompletionResult.CONFLICT:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="job lease is not owned or has expired",
                )
            if completion is CompletionResult.REPLAY:
                return _job_response(job)
            if generation_context is not None:
                run_id, source_asset_id, run = generation_context
                render_job = JobRepository(session).enqueue(
                    job_type=JobType.RENDER,
                    payload={
                        "generation_run_id": str(run_id),
                        "source_asset_id": str(source_asset_id),
                        "planned_display_at": run.planned_display_at.isoformat(),
                    },
                    idempotency_key=f"render:{run_id}",
                    max_attempts=3,
                    available_at=now,
                    now=now,
                )
                run.generate_job_id = job_id
                GenerationRunRepository(session).generation_succeeded(
                    run_id=run_id,
                    source_asset_id=source_asset_id,
                    render_job_id=render_job.id,
                    weather_snapshot=run.weather_snapshot or {},
                    prompt_snapshot=run.prompt_snapshot or {},
                    now=now,
                )
            session.commit()
            session.refresh(job)
            return _job_response(job)

    @app.post("/api/v1/jobs/{job_id}/fail")
    async def fail_job(
        job_id: uuid.UUID,
        request: WorkerFailRequest,
        _authorized: None = Depends(require_worker_token),
    ) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            accepted = JobRepository(session).fail(
                job_id=job_id,
                worker_id=request.worker_id,
                error_code=request.error_code,
                error_detail=request.error_detail,
                retryable=request.retryable,
                now=now,
                retry_delay=timedelta(seconds=request.retry_delay_seconds),
            )
            if not accepted:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="job lease is not owned or has expired",
                )
            session.commit()
            job = session.get(Job, job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            return _job_response(job)

    @app.post("/api/v1/jobs/{job_id}/outputs", status_code=status.HTTP_201_CREATED)
    async def upload_job_output(
        job_id: uuid.UUID,
        worker_id: Annotated[str, Form(min_length=1, max_length=255)],
        output: Annotated[UploadFile, File()],
        _authorized: None = Depends(require_worker_token),
    ) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        now = datetime.now(UTC)
        with session_factory() as session:
            job = session.get(Job, job_id)
            expires_at = job.lease_expires_at if job is not None else None
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if (
                job is None
                or job.status is not JobStatus.LEASED
                or job.lease_owner != worker_id
                or expires_at is None
                or expires_at <= now
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="job lease is not owned or has expired",
                )
            content = await output.read(max_upload_bytes + 1)
            if len(content) > max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"upload exceeds {max_upload_bytes} bytes",
                )
            try:
                image = Image.open(BytesIO(content))
                image.load()
            except (OSError, UnidentifiedImageError) as error:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="unsupported or corrupt image",
                ) from error
            sha256 = hashlib.sha256(content).hexdigest()
            storage_key = f"provider-output/{sha256[:2]}/{sha256}"
            asset = session.scalar(select(Asset).where(Asset.storage_key == storage_key))
            try:
                publish_asset_bytes(
                    ensure_assets_root(artifact_root),
                    storage_key,
                    content,
                    after_destination_open=asset_write_hook,
                )
            except OSError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="existing asset failed integrity validation",
                ) from error
            if asset is None:
                candidate = Asset(
                    id=uuid.uuid4(),
                    kind=AssetKind.PROVIDER_OUTPUT,
                    storage_key=storage_key,
                    media_type=output.content_type or "application/octet-stream",
                    byte_length=len(content),
                    sha256=sha256,
                    metadata_json={
                        "width": image.width,
                        "height": image.height,
                        "source_job_id": str(job_id),
                    },
                    created_at=now,
                )
                try:
                    with session.begin_nested():
                        session.add(candidate)
                        session.flush()
                    asset = candidate
                except IntegrityError:
                    asset = session.scalar(select(Asset).where(Asset.storage_key == storage_key))
                    if asset is None:
                        raise
                session.commit()
            return _asset_response(asset)

    def get_asset_response(asset_id: uuid.UUID, *, download: bool) -> Response:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="job store is not configured",
            )
        with session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
            try:
                content = read_asset_bytes(artifact_root / "assets", asset.storage_key)
            except OSError as error:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="asset file not found"
                ) from error
            if len(content) != asset.byte_length or not hmac.compare_digest(
                hashlib.sha256(content).hexdigest(), asset.sha256
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="asset failed integrity validation",
                )
            headers = {"ETag": f'"{asset.sha256}"', "Cache-Control": "public, immutable"}
            if download:
                extension = {"image/png": ".png", "image/jpeg": ".jpg"}.get(
                    asset.media_type, ""
                )
                headers["Content-Disposition"] = (
                    f'attachment; filename="generation-source-{asset.id}{extension}"'
                )
            return Response(
                content=content,
                media_type=asset.media_type,
                headers=headers,
            )

    @app.get("/api/v1/assets/{asset_id}")
    async def get_asset(asset_id: uuid.UUID) -> Response:
        return get_asset_response(asset_id, download=False)

    @app.get("/api/v1/assets/{asset_id}/download")
    async def download_asset(asset_id: uuid.UUID) -> Response:
        return get_asset_response(asset_id, download=True)

    def persist_conversion(
        image: Image.Image,
        selected_settings: ProcessingSettingsRequest,
        preset_reference: dict[str, Any],
        include_source: bool = True,
    ) -> dict[str, Any]:
        artifact_id = str(uuid.uuid4())
        artifact_dir = artifact_root / artifact_id
        staging_dir = artifact_root / f".{artifact_id}.tmp"
        staging_dir.mkdir()
        try:
            if include_source:
                image.save(staging_dir / "source.png", format="PNG")
            preset = ProcessingPreset(
                brightness=selected_settings.brightness,
                contrast=selected_settings.contrast,
                saturation=selected_settings.saturation,
                gamma=selected_settings.gamma,
                sharpness=selected_settings.sharpness,
                dithering_strength=selected_settings.dithering_strength,
            )
            result = convert_panel_image(
                image,
                palette_codes=DEFAULT_PALETTE_CODES,
                preset=preset,
                dithering=selected_settings.dithering,
                rotation=panel_rotation,
            )
            result.preview.save(staging_dir / "preview.png", format="PNG")
            (staging_dir / "frame.bin").write_bytes(result.packed)
            frame_sha256 = hashlib.sha256(result.packed).hexdigest()
            manifest: dict[str, Any] = {
                "schema_version": 1,
                "frame_id": artifact_id,
                "created_at": datetime.now(UTC).isoformat(),
                "width": 1200,
                "height": 1600,
                "pixel_format": "spectra6-nibbles",
                "palette_encoding": "gdep133c02-native-transverse-v1",
                "byte_length": len(result.packed),
                "sha256": frame_sha256,
                "url": f"/api/v1/frames/{artifact_id}/frame.bin",
                "source_asset_id": None,
                "preset": preset_reference,
                "processing": {
                    "pipeline_version": "python-rgb-v1",
                    "dithering": selected_settings.dithering.value,
                    "brightness": selected_settings.brightness,
                    "contrast": selected_settings.contrast,
                    "saturation": selected_settings.saturation,
                    "gamma": selected_settings.gamma,
                    "sharpness": selected_settings.sharpness,
                    "dithering_strength": selected_settings.dithering_strength,
                },
            }
            (staging_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            filenames = ["preview.png", "frame.bin", "manifest.json"]
            if include_source:
                filenames.insert(0, "source.png")
            for filename in filenames:
                _fsync_path(staging_dir / filename)
            _fsync_path(staging_dir)
            os.replace(staging_dir, artifact_dir)
            _fsync_path(artifact_root)
            return manifest
        except BaseException:
            shutil.rmtree(staging_dir, ignore_errors=True)
            shutil.rmtree(artifact_dir, ignore_errors=True)
            raise

    @app.post("/api/v1/drafts", status_code=status.HTTP_201_CREATED)
    async def create_draft(
        source: Annotated[UploadFile, File()],
        dithering: Annotated[DitheringAlgorithm, Form()] = DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1,
        brightness: Annotated[float, Form(ge=-100, le=100)] = 0,
        contrast: Annotated[float, Form(ge=-100, le=100)] = 0,
        saturation: Annotated[float, Form(ge=-100, le=100)] = 0,
        gamma: Annotated[float, Form(ge=0.1, le=5)] = 1,
        sharpness: Annotated[float, Form(ge=-100, le=100)] = 0,
        dithering_strength: Annotated[float, Form(ge=0, le=1)] = 1,
    ) -> dict[str, Any]:
        content = await source.read(max_upload_bytes + 1)
        if len(content) > max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"upload exceeds {max_upload_bytes} bytes",
            )
        settings = ProcessingSettingsRequest(
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            gamma=gamma,
            sharpness=sharpness,
            dithering=dithering,
            dithering_strength=dithering_strength,
        )
        await acquire_render_slot()
        try:
            return await asyncio.get_running_loop().run_in_executor(
                _RENDER_EXECUTOR,
                draft_store.create,
                content,
                settings.model_dump(mode="json"),
                render_draft_preview,
            )
        except DraftLimitError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="draft session limit reached",
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="unsupported or corrupt image",
            ) from error
        finally:
            render_slots.release()

    @app.get("/api/v1/drafts/{draft_id}/revisions/{revision}/preview.png")
    async def get_draft_preview(draft_id: uuid.UUID, revision: int) -> FileResponse:
        preview_path = (
            artifact_root / "drafts" / str(draft_id) / "revisions" / str(revision) / "preview.png"
        )
        if revision < 1 or not preview_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
        return FileResponse(
            preview_path, media_type="image/png", headers={"Cache-Control": "no-store"}
        )

    @app.delete("/api/v1/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_draft(draft_id: uuid.UUID) -> Response:
        await run_io(draft_store.delete, str(draft_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/drafts/{draft_id}/preview")
    async def update_draft_preview(
        draft_id: uuid.UUID, request: DraftPreviewRequest
    ) -> dict[str, Any]:
        draft_key = str(draft_id)
        settings = request.model_dump(mode="json", exclude={"revision"})
        await acquire_render_slot()
        try:
            await run_io(draft_store.request_revision, draft_key, request.revision, settings)
            return await asyncio.get_running_loop().run_in_executor(
                _RENDER_EXECUTOR,
                draft_store.render_revision,
                draft_key,
                request.revision,
                settings,
                render_draft_preview,
            )
        except DraftSupersededError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "preview_superseded",
                    "current_revision": error.current_revision,
                    "requested_revision": error.requested_revision,
                },
            ) from error
        except DraftNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="draft not found"
            ) from error
        except DraftCorruptError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="draft state is corrupt",
            ) from error
        finally:
            render_slots.release()

    @app.post("/api/v1/drafts/{draft_id}/publish")
    async def publish_draft(draft_id: uuid.UUID) -> dict[str, Any]:
        draft_key = str(draft_id)
        await acquire_render_slot()
        manifest: dict[str, Any] | None = None
        try:
            snapshot = await run_io(draft_store.snapshot, draft_key)

            def finalize() -> dict[str, Any]:
                try:
                    opened = Image.open(snapshot.source_path)
                    opened.load()
                except (OSError, UnidentifiedImageError) as error:
                    raise DraftCorruptError from error
                settings = ProcessingSettingsRequest.model_validate(snapshot.settings)
                return persist_conversion(
                    opened.convert("RGB"),
                    settings,
                    {"id": "ad-hoc", "revision": snapshot.revision},
                    False,
                )

            manifest = await asyncio.get_running_loop().run_in_executor(_RENDER_EXECUTOR, finalize)
            frame_id = uuid.UUID(manifest["frame_id"])
            published = await run_io(
                draft_store.commit_if_current,
                draft_key,
                snapshot.revision,
                lambda: activate_frame(frame_id),
            )
            return {"draft_id": draft_key, "revision": snapshot.revision, **published}
        except DraftBusyError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="preview is still rendering",
            ) from error
        except DraftSupersededError as error:
            if manifest is not None:
                shutil.rmtree(artifact_root / manifest["frame_id"], ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="draft changed during final render",
            ) from error
        except DraftNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="draft not found"
            ) from error
        except DraftCorruptError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="draft state is corrupt",
            ) from error
        except BaseException:
            if manifest is not None:
                shutil.rmtree(artifact_root / manifest["frame_id"], ignore_errors=True)
            raise
        finally:
            render_slots.release()

    @app.post("/api/v1/conversions/{source_frame_id}", status_code=status.HTTP_201_CREATED)
    async def reprocess_conversion(
        source_frame_id: uuid.UUID,
        selected_settings: ProcessingSettingsRequest,
    ) -> dict[str, Any]:
        source_path = artifact_root / str(source_frame_id) / "source.png"
        if not source_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="source frame not found"
            )

        def reprocess() -> dict[str, Any]:
            try:
                opened = Image.open(source_path)
                opened.load()
            except (OSError, UnidentifiedImageError) as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="stored source image is corrupt",
                ) from error
            return persist_conversion(
                opened.convert("RGB"),
                selected_settings,
                {"id": "ad-hoc", "revision": 1},
            )

        await acquire_render_slot()
        try:
            return await asyncio.get_running_loop().run_in_executor(_RENDER_EXECUTOR, reprocess)
        finally:
            render_slots.release()

    @app.post("/api/v1/conversions", status_code=status.HTTP_201_CREATED)
    async def create_conversion(
        source: Annotated[UploadFile, File()],
        processing_mode: Annotated[Literal["default", "ad-hoc"], Form()] = "default",
        dithering: Annotated[DitheringAlgorithm, Form()] = DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1,
        brightness: Annotated[float, Form(ge=-100, le=100)] = 0,
        contrast: Annotated[float, Form(ge=-100, le=100)] = 0,
        saturation: Annotated[float, Form(ge=-100, le=100)] = 0,
        gamma: Annotated[float, Form(ge=0.1, le=5)] = 1,
        sharpness: Annotated[float, Form(ge=-100, le=100)] = 0,
        dithering_strength: Annotated[float, Form(ge=0, le=1)] = 1,
    ) -> dict[str, Any]:
        content = await source.read(max_upload_bytes + 1)
        if len(content) > max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"upload exceeds {max_upload_bytes} bytes",
            )

        def convert_upload() -> dict[str, Any]:
            try:
                opened = Image.open(BytesIO(content))
                opened.load()
            except (OSError, UnidentifiedImageError) as error:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="unsupported or corrupt image",
                ) from error
            image: Image.Image = opened.convert("RGB")
            selected_settings = ProcessingSettingsRequest(
                brightness=brightness,
                contrast=contrast,
                saturation=saturation,
                gamma=gamma,
                sharpness=sharpness,
                dithering=dithering,
                dithering_strength=dithering_strength,
            )
            preset_reference: dict[str, Any] = {"id": "ad-hoc", "revision": 1}
            if processing_mode == "default" and session_factory is not None:
                with session_factory() as session:
                    default = next(
                        (
                            (preset_record, revision)
                            for preset_record, revision in PresetRepository(session).list_latest()
                            if preset_record.is_default
                        ),
                        None,
                    )
                    if default is not None:
                        preset_record, revision = default
                        selected_settings = ProcessingSettingsRequest.model_validate(
                            revision.settings
                        )
                        preset_reference = {
                            "id": str(preset_record.id),
                            "revision": revision.revision,
                        }
            return persist_conversion(image, selected_settings, preset_reference)

        await acquire_render_slot()
        try:
            return await asyncio.get_running_loop().run_in_executor(
                _RENDER_EXECUTOR, convert_upload
            )
        finally:
            render_slots.release()

    def _activate_frame(
        frame_id: uuid.UUID, generation_run_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="device state is not configured",
            )
        artifact_dir = artifact_root / str(frame_id)
        frame_path = artifact_dir / "frame.bin"
        manifest_path = artifact_dir / "manifest.json"
        if not frame_path.is_file() or not manifest_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="frame not found")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actual_bytes = frame_path.read_bytes()
            valid = (
                manifest["frame_id"] == str(frame_id)
                and manifest["byte_length"] == 960_000
                and len(actual_bytes) == 960_000
                and manifest["palette_encoding"] == "gdep133c02-native-transverse-v1"
                and hmac.compare_digest(
                    manifest["sha256"], hashlib.sha256(actual_bytes).hexdigest()
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="frame artifact is invalid",
            ) from error
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="frame artifact failed integrity validation",
            )

        now = datetime.now(UTC)
        with session_factory() as session:
            current = session.scalar(build_current_frame_lock_statement())
            if current is None:
                raise CurrentFrameInvariantError("current frame singleton is missing")
            if generation_run_id is not None and current.frame_id == frame_id:
                activated_at = current.activated_at
                if activated_at is not None and activated_at.utcoffset() is None:
                    activated_at = activated_at.replace(tzinfo=UTC)
                return {
                    "frame_id": str(frame_id),
                    "sequence": current.sequence,
                    "activated_at": activated_at,
                    "sha256": manifest["sha256"],
                    "already_current": True,
                }
            sequence = max((current.sequence or 0) + 1, int(now.timestamp()))
            current.generation_run_id = generation_run_id
            current.frame_id = frame_id
            current.sequence = sequence
            current.activated_at = now
            session.commit()
        return {
            "frame_id": str(frame_id),
            "sequence": sequence,
            "activated_at": now,
            "sha256": manifest["sha256"],
            "already_current": False,
        }

    def activate_frame(
        frame_id: uuid.UUID, generation_run_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        with activation_lock:
            try:
                return _activate_frame(frame_id, generation_run_id)
            except CurrentFrameInvariantError as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(error),
                ) from error

    @app.post("/api/v1/frames/{frame_id}/publish")
    async def publish_frame_now(frame_id: uuid.UUID) -> dict[str, Any]:
        published: dict[str, Any] = await run_io(activate_frame, frame_id)
        return published

    @app.post("/api/v1/generations/{run_id}/publish")
    async def publish_generation_now(run_id: uuid.UUID) -> dict[str, Any]:
        if session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="generation history is not configured",
            )
        with session_factory() as session:
            run = session.get(GenerationRun, run_id)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="generation run not found"
                )
            if run.frame_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="frame not found")
            frame_id = run.frame_id
        published: dict[str, Any] = await run_io(activate_frame, frame_id, run_id)
        return published

    @app.get("/api/v1/frames/{frame_id}/frame.bin")
    async def get_frame(
        frame_id: uuid.UUID,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        artifact_dir = artifact_root / str(frame_id)
        frame_path = artifact_dir / "frame.bin"
        manifest_path = artifact_dir / "manifest.json"
        if not frame_path.is_file() or not manifest_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="frame not found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        etag = f'"{manifest["sha256"]}"'
        if if_none_match == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        return FileResponse(
            frame_path,
            media_type="application/octet-stream",
            headers={
                "ETag": etag,
                "Cache-Control": "public, immutable",
            },
        )

    @app.get("/api/v1/frames/{frame_id}/preview.png")
    async def get_preview(frame_id: uuid.UUID) -> FileResponse:
        preview_path = artifact_root / str(frame_id) / "preview.png"
        if not preview_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="preview not found")
        return FileResponse(
            preview_path,
            media_type="image/png",
            headers={"Cache-Control": "public, immutable"},
        )

    if frontend_dist is not None:
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app
