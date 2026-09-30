from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from eink_frame.api import create_app
from eink_frame.orchestration import GenerationRunRepository
from eink_frame.persistence import (
    Asset,
    AssetKind,
    Base,
    CurrentFrame,
    GenerationRun,
    GenerationRunStatus,
    Job,
    JobStatus,
    JobType,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_job_api_enqueues_idempotently_and_returns_status(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(
        artifact_root=tmp_path,
        session_factory=factory,
        worker_token="test-worker-token",
    )
    transport = httpx.ASGITransport(app=app)
    request = {
        "job_type": "render",
        "payload": {"source_asset_id": "asset-1", "preset_id": "default"},
        "max_attempts": 3,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/jobs", json=request, headers={"Idempotency-Key": "render-asset-1"}
        )
        second = await client.post(
            "/api/v1/jobs", json=request, headers={"Idempotency-Key": "render-asset-1"}
        )
        status_response = await client.get(f"/api/v1/jobs/{first.json()['id']}")
        unauthorized_claim = await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300},
        )
        worker_headers = {"Authorization": "Bearer test-worker-token"}
        claim = await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300},
            headers=worker_headers,
        )
        empty_claim = await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-b", "lease_seconds": 300},
            headers=worker_headers,
        )
        wrong_heartbeat = await client.post(
            f"/api/v1/jobs/{first.json()['id']}/heartbeat",
            json={"worker_id": "wrong-worker", "lease_seconds": 300},
            headers=worker_headers,
        )
        heartbeat = await client.post(
            f"/api/v1/jobs/{first.json()['id']}/heartbeat",
            json={"worker_id": "spark-a", "lease_seconds": 300},
            headers=worker_headers,
        )
        completed = await client.post(
            f"/api/v1/jobs/{first.json()['id']}/complete",
            json={"worker_id": "spark-a", "result_refs": {"frame_asset_id": "frame-1"}},
            headers=worker_headers,
        )
        idempotent_complete = await client.post(
            f"/api/v1/jobs/{first.json()['id']}/complete",
            json={"worker_id": "spark-a", "result_refs": {"frame_asset_id": "frame-1"}},
            headers=worker_headers,
        )
        conflicting_complete = await client.post(
            f"/api/v1/jobs/{first.json()['id']}/complete",
            json={"worker_id": "spark-a", "result_refs": {"frame_asset_id": "frame-2"}},
            headers=worker_headers,
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "queued"
    assert status_response.json()["attempt"] == 0
    assert unauthorized_claim.status_code == 401
    assert claim.status_code == 200
    assert claim.json()["id"] == first.json()["id"]
    assert claim.json()["status"] == "leased"
    assert claim.json()["payload"] == request["payload"]
    assert empty_claim.status_code == 204
    assert wrong_heartbeat.status_code == 409
    assert heartbeat.status_code == 204
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["result_refs"] == {"frame_asset_id": "frame-1"}
    assert idempotent_complete.status_code == 200
    assert idempotent_complete.json()["status"] == "succeeded"
    assert conflicting_complete.status_code == 409


@pytest.mark.anyio
async def test_worker_can_fail_job_into_retry_wait(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory, worker_token="token")
    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            "/api/v1/jobs",
            json={"job_type": "generate", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "retry-job"},
        )
        await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300},
            headers=auth,
        )
        failed = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/fail",
            json={
                "worker_id": "spark-a",
                "error_code": "worker_error",
                "error_detail": "provider unavailable",
                "retryable": True,
                "retry_delay_seconds": 30,
            },
            headers=auth,
        )

    assert failed.status_code == 200
    assert failed.json()["status"] == "retry_wait"
    assert failed.json()["error_code"] == "worker_error"


@pytest.mark.anyio
async def test_current_lease_owner_can_upload_provider_output(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory, worker_token="token")
    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer token"}
    image = Image.new("RGB", (64, 48), (20, 80, 160))
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            "/api/v1/jobs",
            json={"job_type": "generate", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "output-job"},
        )
        await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300},
            headers=auth,
        )
        uploaded = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/outputs",
            data={"worker_id": "spark-a"},
            files={"output": ("generated.png", encoded.getvalue(), "image/png")},
            headers=auth,
        )
        downloaded = await client.get(uploaded.json()["url"])

    assert uploaded.status_code == 201
    assert uploaded.json()["kind"] == "provider_output"
    assert uploaded.json()["width"] == 64
    assert uploaded.json()["height"] == 48
    assert len(uploaded.json()["sha256"]) == 64
    assert downloaded.status_code == 200
    assert downloaded.content == encoded.getvalue()


@pytest.mark.anyio
@pytest.mark.parametrize("existing_record", [False, True])
async def test_upload_refuses_corrupt_existing_content_addressed_file(
    tmp_path: Path, existing_record: bool
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory, worker_token="token")
    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer token"}
    encoded = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 80, 160)).save(encoded, format="PNG")
    content = encoded.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    existing = tmp_path / "assets" / "provider-output" / digest[:2] / digest
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"corrupt")
    if existing_record:
        with Session(engine) as session:
            session.add(
                Asset(
                    id=uuid.uuid4(),
                    kind=AssetKind.PROVIDER_OUTPUT,
                    storage_key=f"provider-output/{digest[:2]}/{digest}",
                    media_type="image/png",
                    byte_length=len(content),
                    sha256=digest,
                    metadata_json={},
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            "/api/v1/jobs",
            json={"job_type": "generate", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "corrupt-existing-output"},
        )
        await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300, "job_types": ["generate"]},
            headers=auth,
        )
        uploaded = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/outputs",
            data={"worker_id": "spark-a"},
            files={"output": ("generated.png", content, "image/png")},
            headers=auth,
        )

    assert uploaded.status_code == 409
    assert uploaded.json() == {"detail": "existing asset failed integrity validation"}
    assert existing.read_bytes() == b"corrupt"


@pytest.mark.anyio
async def test_concurrent_identical_uploads_reuse_one_asset(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path / "artifacts", session_factory=factory, worker_token="token")
    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer token"}
    encoded = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 80, 160)).save(encoded, format="PNG")
    content = encoded.getvalue()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        jobs = []
        for index, worker in enumerate(("spark-a", "spark-b")):
            queued = await client.post(
                "/api/v1/jobs",
                json={"job_type": "generate", "payload": {}, "max_attempts": 2},
                headers={"Idempotency-Key": f"concurrent-output-{index}"},
            )
            claimed = await client.post(
                "/api/v1/workers/claim",
                json={"worker_id": worker, "lease_seconds": 300, "job_types": ["generate"]},
                headers=auth,
            )
            assert claimed.json()["id"] == queued.json()["id"]
            jobs.append((queued.json()["id"], worker))

        uploads = await asyncio.gather(
            *(
                client.post(
                    f"/api/v1/jobs/{job_id}/outputs",
                    data={"worker_id": worker},
                    files={"output": ("generated.png", content, "image/png")},
                    headers=auth,
                )
                for job_id, worker in jobs
            )
        )

    assert [response.status_code for response in uploads] == [201, 201]
    assert uploads[0].json()["id"] == uploads[1].json()["id"]
    with Session(engine) as session:
        assert len(list(session.scalars(select(Asset)))) == 1


@pytest.mark.anyio
async def test_upload_never_writes_through_swapped_provider_output_symlink(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    auth = {"Authorization": "Bearer token"}
    encoded = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 80, 160)).save(encoded, format="PNG")
    content = encoded.getvalue()
    swapped = False

    def swap_provider_output() -> None:
        nonlocal swapped
        assert not swapped
        swapped = True
        provider_output = artifact_root / "assets" / "provider-output"
        provider_output.rename(provider_output.with_name("provider-output-swapped"))
        provider_output.symlink_to(outside, target_is_directory=True)

    app = create_app(
        artifact_root=artifact_root,
        session_factory=factory,
        worker_token="token",
        asset_write_hook=swap_provider_output,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            "/api/v1/jobs",
            json={"job_type": "generate", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "swapped-provider-output"},
        )
        await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300, "job_types": ["generate"]},
            headers=auth,
        )
        uploaded = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/outputs",
            data={"worker_id": "spark-a"},
            files={"output": ("generated.png", content, "image/png")},
            headers=auth,
        )

    assert swapped
    assert uploaded.status_code == 409
    assert uploaded.json() == {"detail": "existing asset failed integrity validation"}
    assert list(outside.iterdir()) == []
    assert not list(artifact_root.rglob("*.tmp"))


@pytest.mark.anyio
async def test_worker_claim_filters_by_supported_job_types(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory, worker_token="token")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        render = await client.post(
            "/api/v1/jobs",
            json={"job_type": "render", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "render-first"},
        )
        generate = await client.post(
            "/api/v1/jobs",
            json={"job_type": "generate", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "generate-second"},
        )
        claim = await client.post(
            "/api/v1/workers/claim",
            json={
                "worker_id": "spark-a",
                "lease_seconds": 300,
                "job_types": ["generate"],
            },
            headers={"Authorization": "Bearer token"},
        )

    assert render.status_code == 202
    assert generate.status_code == 202
    assert claim.status_code == 200
    assert claim.json()["id"] == generate.json()["id"]
    assert claim.json()["job_type"] == "generate"


@pytest.mark.anyio
async def test_generate_completion_enqueues_one_render_job(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    planned = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    run_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            GenerationRun(
                id=run_id,
                planned_display_at=planned,
                generation_due_at=planned - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                status=GenerationRunStatus.PLANNED,
                weather_snapshot={"temperature_c": 21},
                prompt_snapshot={"final_prompt": "weather"},
                created_at=planned - timedelta(minutes=15),
                updated_at=planned - timedelta(minutes=15),
            )
        )
        session.commit()
    app = create_app(artifact_root=tmp_path, session_factory=factory, worker_token="token")
    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer token"}
    image = Image.new("RGB", (64, 48), (20, 80, 160))
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            "/api/v1/jobs",
            json={
                "job_type": "generate",
                "payload": {
                    "generation_run_id": str(run_id),
                    "prompt": "weather",
                    "seed": 7,
                    "width": 1600,
                    "height": 1200,
                },
                "max_attempts": 2,
            },
            headers={"Idempotency-Key": f"generate:{run_id}"},
        )
        claim = await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 300, "job_types": ["generate"]},
            headers=auth,
        )
        uploaded = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/outputs",
            data={"worker_id": "spark-a"},
            files={"output": ("generated.png", encoded.getvalue(), "image/png")},
            headers=auth,
        )
        completed = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/complete",
            json={
                "worker_id": "spark-a",
                "result_refs": {"provider_asset_id": uploaded.json()["id"]},
            },
            headers=auth,
        )
        frame_id = uuid.uuid4()
        rendered_at = planned - timedelta(minutes=1)
        with Session(engine) as session:
            repository = GenerationRunRepository(session)
            repository.render_succeeded(run_id=run_id, frame_id=frame_id, now=rendered_at)
            session.add(CurrentFrame(id=1))
            assert repository.publish_due(now=planned) == run_id
            session.commit()
            run = session.get(GenerationRun, run_id)
            current = session.get(CurrentFrame, 1)
            assert run is not None and current is not None
            prior_run_state = (
                run.status,
                run.frame_id,
                run.generation_completed_at,
                run.render_started_at,
                run.render_completed_at,
                run.published_at,
                run.updated_at,
            )
            prior_current_state = (
                current.generation_run_id,
                current.frame_id,
                current.sequence,
                current.activated_at,
            )

        repeated = await client.post(
            f"/api/v1/jobs/{queued.json()['id']}/complete",
            json={
                "worker_id": "spark-a",
                "result_refs": {"provider_asset_id": uploaded.json()["id"]},
            },
            headers=auth,
        )

    assert claim.status_code == 200
    assert completed.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "succeeded"
    with Session(engine) as session:
        run = session.get(GenerationRun, run_id)
        current = session.get(CurrentFrame, 1)
        assert run is not None
        assert current is not None
        assert run.status == GenerationRunStatus.PUBLISHED
        assert run.source_asset_id == uuid.UUID(uploaded.json()["id"])
        assert run.render_job_id is not None
        assert (
            run.status,
            run.frame_id,
            run.generation_completed_at,
            run.render_started_at,
            run.render_completed_at,
            run.published_at,
            run.updated_at,
        ) == prior_run_state
        assert (
            current.generation_run_id,
            current.frame_id,
            current.sequence,
            current.activated_at,
        ) == prior_current_state
        render = session.get(Job, run.render_job_id)
        assert render is not None
        assert render.job_type == JobType.RENDER
        assert render.status == JobStatus.QUEUED
        assert render.payload["generation_run_id"] == str(run_id)
        render_jobs = list(session.scalars(select(Job).where(Job.job_type == JobType.RENDER)))
        assert len(render_jobs) == 1
