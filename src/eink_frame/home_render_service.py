from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy.orm import Session, sessionmaker

from .assets import read_asset_bytes
from .converter import DitheringAlgorithm, ProcessingPreset
from .home_worker import HardwareProfile, RenderPayload, render_home_artifacts
from .jobs import CompletionResult, JobRepository
from .orchestration import GenerationRunRepository
from .persistence import Asset, AssetKind, GenerationRun, JobErrorCode, JobType
from .presets import PresetRepository


class HomeRenderWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        artifact_root: Path,
        hardware_profile: HardwareProfile,
        worker_id: str = "home-render",
    ) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root
        self._profile = hardware_profile
        self._worker_id = worker_id

    def run_once(self, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("render worker now must be timezone-aware")
        with self._session_factory() as session:
            job = JobRepository(session).claim_next(
                worker_id=self._worker_id,
                now=now,
                lease_duration=timedelta(minutes=10),
                job_types=(JobType.RENDER,),
            )
            if job is None:
                return False
            job_id = job.id
            payload = dict(job.payload)
            session.commit()
        try:
            frame_id = self._render(job_id=job_id, payload=payload, now=now)
            with self._session_factory() as session:
                completion = JobRepository(session).complete(
                    job_id=job_id,
                    worker_id=self._worker_id,
                    result_refs={"frame_id": str(frame_id)},
                    now=now,
                )
                if completion is CompletionResult.CONFLICT:
                    raise RuntimeError("render lease expired before completion")
                if completion is CompletionResult.REPLAY:
                    return True
                run_id = uuid.UUID(str(payload["generation_run_id"]))
                GenerationRunRepository(session).render_succeeded(
                    run_id=run_id, frame_id=frame_id, now=now
                )
                session.commit()
        except (OSError, ValueError, LookupError, RuntimeError, KeyError) as error:
            with self._session_factory() as session:
                JobRepository(session).fail(
                    job_id=job_id,
                    worker_id=self._worker_id,
                    error_code=JobErrorCode.INVALID_OUTPUT,
                    error_detail=f"{type(error).__name__}: {error}"[:2000],
                    retryable=True,
                    now=now,
                    retry_delay=timedelta(seconds=30),
                )
                try:
                    run_id = uuid.UUID(str(payload["generation_run_id"]))
                    run = session.get(GenerationRun, run_id)
                    if run is not None:
                        run.error_code = "render_failed"
                        run.error_detail = f"{type(error).__name__}: {error}"[:2000]
                        run.updated_at = now
                except (KeyError, ValueError):
                    pass
                session.commit()
        return True

    def _render(self, *, job_id: uuid.UUID, payload: dict[str, Any], now: datetime) -> uuid.UUID:
        del job_id
        run_id = uuid.UUID(str(payload["generation_run_id"]))
        source_asset_id = uuid.UUID(str(payload["source_asset_id"]))
        with self._session_factory() as session:
            run = session.get(GenerationRun, run_id)
            asset = session.get(Asset, source_asset_id)
            if run is None:
                raise LookupError("generation run not found")
            if asset is None or asset.kind is not AssetKind.PROVIDER_OUTPUT:
                raise LookupError("provider output asset not found")
            presets = PresetRepository(session).list_latest()
            selected = next(
                ((preset, revision) for preset, revision in presets if preset.is_default), None
            )
            if selected is None:
                raise LookupError("default processing preset not found")
            preset_record, preset_revision = selected
            settings = dict(preset_revision.settings)
            dithering = DitheringAlgorithm(settings.pop("dithering"))
            processing = ProcessingPreset(**settings)
            planned_display_at = _aware_utc(run.planned_display_at)
            storage_key = asset.storage_key
            source_sha256 = asset.sha256
            source_length = asset.byte_length
            preset_id = preset_record.id
            revision = preset_revision.revision
        assets_root = self._artifact_root / "assets"
        try:
            source_bytes = read_asset_bytes(assets_root, storage_key)
        except OSError as error:
            raise OSError("provider asset path is invalid or missing") from error
        source_path = assets_root / storage_key
        if len(source_bytes) != source_length:
            raise OSError("provider asset byte length mismatch")
        if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
            raise OSError("provider asset checksum mismatch")
        frame_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"eink-frame:{run_id}:{source_sha256}:{preset_id}:{revision}:"
            f"{self._profile.profile_id}:{self._profile.rotation}",
        )
        final_dir = self._artifact_root / str(frame_id)
        if final_dir.is_dir():
            _validate_existing_frame(final_dir)
            return frame_id
        staging_dir = self._artifact_root / f".{frame_id}.tmp"
        shutil.rmtree(staging_dir, ignore_errors=True)
        render_home_artifacts(
            source_bytes,
            RenderPayload(
                source_asset_id=str(source_asset_id),
                source_asset_path=source_path,
                planned_display_at=planned_display_at,
                generation_run_id=str(run_id),
                preset_id=str(preset_id),
                preset_revision=revision,
                preset_settings=processing,
                dithering=dithering,
                hardware_profile=self._profile,
            ),
            staging_dir,
        )
        frame_bytes = (staging_dir / "frame.bin").read_bytes()
        manifest = {
            "schema_version": 1,
            "frame_id": str(frame_id),
            "created_at": now.isoformat(),
            "width": 1200,
            "height": 1600,
            "pixel_format": "spectra6-nibbles",
            "palette_encoding": self._profile.palette_encoding,
            "byte_length": len(frame_bytes),
            "sha256": hashlib.sha256(frame_bytes).hexdigest(),
            "url": f"/api/v1/frames/{frame_id}/frame.bin",
            "source_asset_id": str(source_asset_id),
            "preset": {"id": str(preset_id), "revision": revision},
            "processing": {
                "pipeline_version": "python-rgb-v1",
                "dithering": dithering.value,
                **settings,
            },
        }
        _replace_manifest(staging_dir, manifest)
        if final_dir.exists():
            raise FileExistsError("frame artifact target already exists")
        os.replace(staging_dir, final_dir)
        _fsync_directory(self._artifact_root)
        _validate_existing_frame(final_dir)
        return frame_id


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


def _replace_manifest(staging_dir: Path, manifest: dict[str, Any]) -> None:
    path = staging_dir / "manifest.json"
    temporary = staging_dir / ".manifest.json.tmp"
    content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with temporary.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    _fsync_directory(staging_dir)


def _validate_existing_frame(directory: Path) -> None:
    manifest = json.loads((directory / "manifest.json").read_text())
    frame = (directory / "frame.bin").read_bytes()
    if len(frame) != 960000 or manifest["byte_length"] != 960000:
        raise OSError("published frame byte length mismatch")
    if hashlib.sha256(frame).hexdigest() != manifest["sha256"]:
        raise OSError("published frame checksum mismatch")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_home_render_forever() -> None:
    from sqlalchemy import create_engine

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    artifact_root = Path(
        os.environ.get(
            "EINK_ARTIFACT_ROOT",
            str(Path.home() / ".local/share/eink-weather-frame/artifacts"),
        )
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    rotation_value = os.environ.get("EINK_PANEL_ROTATION", "clockwise")
    if rotation_value not in ("clockwise", "counterclockwise", "transverse"):
        raise ValueError("EINK_PANEL_ROTATION must be clockwise, counterclockwise or transverse")
    rotation = cast(Literal["clockwise", "counterclockwise", "transverse"], rotation_value)
    worker = HomeRenderWorker(
        session_factory=factory,
        artifact_root=artifact_root,
        hardware_profile=HardwareProfile(
            profile_id=os.environ.get("EINK_HARDWARE_PROFILE", "gdep133c02-native-transverse-v1"),
            width=1200,
            height=1600,
            palette_codes=(0, 1, 2, 3, 5, 6),
            palette_encoding=os.environ.get(
                "EINK_PALETTE_ENCODING", "gdep133c02-native-transverse-v1"
            ),
            rotation=rotation,
        ),
    )
    while True:
        worked = worker.run_once()
        time.sleep(0.25 if worked else 2)


def main() -> None:
    run_home_render_forever()
