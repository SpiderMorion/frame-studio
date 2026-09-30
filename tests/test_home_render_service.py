from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from eink_frame.home_render_service import HomeRenderWorker
from eink_frame.home_worker import HardwareProfile
from eink_frame.persistence import (
    Asset,
    AssetKind,
    Base,
    GenerationRun,
    GenerationRunStatus,
    Job,
    JobStatus,
    JobType,
    ProcessingPreset,
    ProcessingPresetRevision,
)


def test_home_render_worker_claims_asset_and_publishes_ready_frame(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    image = Image.new("RGB", (64, 48), (20, 80, 160))
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    source = encoded.getvalue()
    source_sha = hashlib.sha256(source).hexdigest()
    storage_key = f"provider-output/{source_sha[:2]}/{source_sha}"
    source_path = tmp_path / "assets" / storage_key
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source)
    with Session(engine) as session:
        session.add(
            Asset(
                id=asset_id,
                kind=AssetKind.PROVIDER_OUTPUT,
                storage_key=storage_key,
                media_type="image/png",
                byte_length=len(source),
                sha256=source_sha,
                metadata_json={"width": 64, "height": 48},
                created_at=now,
            )
        )
        session.add(ProcessingPreset(id=preset_id, name="Default", is_default=True, created_at=now))
        session.add(
            ProcessingPresetRevision(
                preset_id=preset_id,
                revision=1,
                settings={
                    "brightness": 0,
                    "contrast": 0,
                    "saturation": 0,
                    "gamma": 1,
                    "sharpness": 0,
                    "dithering_strength": 1,
                    "dithering": "bayer-8x8",
                },
                created_at=now,
            )
        )
        session.add(
            Job(
                id=job_id,
                job_type=JobType.RENDER,
                payload_schema_version=1,
                payload={
                    "generation_run_id": str(run_id),
                    "source_asset_id": str(asset_id),
                    "planned_display_at": (now + timedelta(minutes=5)).isoformat(),
                },
                idempotency_key=f"render:{run_id}",
                status=JobStatus.QUEUED,
                attempt=0,
                max_attempts=3,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            GenerationRun(
                id=run_id,
                planned_display_at=now + timedelta(minutes=5),
                generation_due_at=now - timedelta(minutes=10),
                provider="comfyui",
                model="qwen-image-2512",
                status=GenerationRunStatus.RENDERING,
                render_job_id=job_id,
                source_asset_id=asset_id,
                created_at=now - timedelta(minutes=15),
                updated_at=now,
            )
        )
        session.commit()

    worker = HomeRenderWorker(
        session_factory=factory,
        artifact_root=tmp_path,
        hardware_profile=HardwareProfile(
            profile_id="gdep133c02-native-transverse-v1",
            width=1200,
            height=1600,
            palette_codes=(0, 1, 2, 3, 5, 6),
            palette_encoding="gdep133c02-native-transverse-v1",
            rotation="transverse",
        ),
    )
    assert worker.run_once(now=now) is True

    with Session(engine) as session:
        job = session.get(Job, job_id)
        run = session.get(GenerationRun, run_id)
        assert job is not None and job.status == JobStatus.SUCCEEDED
        assert run is not None and run.status == GenerationRunStatus.READY
        assert run.frame_id is not None
        artifact_dir = tmp_path / str(run.frame_id)
        assert (artifact_dir / "frame.bin").stat().st_size == 960000
        manifest = json.loads((artifact_dir / "manifest.json").read_text())
        schema = json.loads(
            (Path(__file__).parents[1] / "contracts/frame-manifest.schema.json").read_text()
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
        assert manifest["source_asset_id"] == str(asset_id)
        assert manifest["preset"] == {"id": str(preset_id), "revision": 1}
