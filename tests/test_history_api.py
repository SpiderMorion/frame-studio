from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from eink_frame.api import create_app
from eink_frame.assets import read_asset_bytes
from eink_frame.persistence import (
    Asset,
    AssetKind,
    Base,
    CurrentFrame,
    GenerationRun,
    GenerationRunStatus,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_generation_history_is_newest_first_and_exposes_durations(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    base = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
    with Session(engine) as session:
        for index, status in enumerate((GenerationRunStatus.PUBLISHED, GenerationRunStatus.FAILED)):
            planned = base + timedelta(hours=index)
            session.add(
                GenerationRun(
                    id=uuid.uuid4(),
                    planned_display_at=planned,
                    generation_due_at=planned - timedelta(minutes=15),
                    provider="comfyui",
                    model="qwen-image-2512",
                    status=status,
                    frame_id=uuid.uuid4() if status == GenerationRunStatus.PUBLISHED else None,
                    weather_snapshot={"temperature_c": 18 + index},
                    prompt_snapshot={"final_prompt": f"prompt {index}"},
                    generation_started_at=planned - timedelta(minutes=15),
                    generation_completed_at=planned - timedelta(minutes=12),
                    render_started_at=planned - timedelta(minutes=12),
                    render_completed_at=planned - timedelta(minutes=11),
                    published_at=planned if status == GenerationRunStatus.PUBLISHED else None,
                    error_code="worker_error" if status == GenerationRunStatus.FAILED else None,
                    error_detail="provider failed"
                    if status == GenerationRunStatus.FAILED
                    else None,
                    created_at=planned - timedelta(minutes=15),
                    updated_at=planned,
                )
            )
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/generations?limit=10")

    assert response.status_code == 200
    body = response.json()
    assert [item["status"] for item in body] == ["failed", "published"]
    assert body[1]["generation_duration_seconds"] == 180
    assert body[1]["render_duration_seconds"] == 60
    assert body[1]["preview_url"].endswith("/preview.png")
    assert body[1]["source"] is None
    assert body[0]["source"] is None
    assert body[0]["error_detail"] == "provider failed"


@pytest.mark.anyio
async def test_generation_history_exposes_exact_source_and_download(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    source = BytesIO()
    Image.new("RGB", (37, 19), (12, 34, 56)).save(source, format="PNG")
    source_bytes = source.getvalue()
    digest = hashlib.sha256(source_bytes).hexdigest()
    asset_id = uuid.uuid4()
    run_id = uuid.uuid4()
    planned = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / digest).write_bytes(source_bytes)
    with Session(engine) as session:
        session.add(
            Asset(
                id=asset_id,
                kind=AssetKind.PROVIDER_OUTPUT,
                storage_key=digest,
                media_type="image/png",
                byte_length=len(source_bytes),
                sha256=digest,
                metadata_json={"width": 37, "height": 19},
                created_at=planned,
            )
        )
        session.add(
            GenerationRun(
                id=run_id,
                planned_display_at=planned,
                generation_due_at=planned - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                status=GenerationRunStatus.READY,
                source_asset_id=asset_id,
                created_at=planned,
                updated_at=planned,
            )
        )
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        history = await client.get("/api/v1/generations")
        inline = await client.get(f"/api/v1/assets/{asset_id}")
        download = await client.get(f"/api/v1/assets/{asset_id}/download")

    item = history.json()[0]
    assert item["source"] == {
        "id": str(asset_id),
        "url": f"/api/v1/assets/{asset_id}",
        "download_url": f"/api/v1/assets/{asset_id}/download",
        "media_type": "image/png",
        "byte_length": len(source_bytes),
        "sha256": digest,
        "width": 37,
        "height": 19,
    }
    assert "storage_key" not in item["source"]
    for response in (inline, download):
        assert response.status_code == 200
        assert response.content == source_bytes
        assert response.headers["content-type"] == "image/png"
        assert response.headers["etag"] == f'"{digest}"'
        assert response.headers["cache-control"] == "public, immutable"
    assert "content-disposition" not in inline.headers
    assert download.headers["content-disposition"] == (
        f'attachment; filename="generation-source-{asset_id}.png"'
    )


@pytest.mark.parametrize("storage_key", ["/absolute", "../escape", "a/../escape", "a\\b", "a//b", "./a"])
def test_descriptor_asset_reader_rejects_non_relative_posix_components(
    tmp_path: Path, storage_key: str
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()

    with pytest.raises(OSError, match="invalid asset storage key"):
        read_asset_bytes(assets, storage_key)


def test_descriptor_asset_reader_refuses_symlinks_and_special_files(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (assets / "link").symlink_to(outside)
    special = assets / "directory"
    special.mkdir()

    with pytest.raises(OSError):
        read_asset_bytes(assets, "link")
    with pytest.raises(OSError, match="not a regular file"):
        read_asset_bytes(assets, "directory")


@pytest.mark.anyio
async def test_asset_endpoints_refuse_traversal_and_missing_metadata_or_file(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    traversal_id = uuid.uuid4()
    missing_file_id = uuid.uuid4()
    missing_metadata_id = uuid.uuid4()
    symlink_id = uuid.uuid4()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not allowed")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "external-link.png").symlink_to(outside)
    now = datetime.now(UTC)
    with Session(engine) as session:
        for asset_id, storage_key in (
            (traversal_id, "../outside.png"),
            (missing_file_id, "does-not-exist.png"),
            (symlink_id, "external-link.png"),
        ):
            session.add(
                Asset(
                    id=asset_id,
                    kind=AssetKind.PROVIDER_OUTPUT,
                    storage_key=storage_key,
                    media_type="image/png",
                    byte_length=1,
                    sha256="0" * 64,
                    metadata_json={},
                    created_at=now,
                )
            )
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get(f"/api/v1/assets/{asset_id}{suffix}")
            for asset_id in (traversal_id, missing_file_id, missing_metadata_id, symlink_id)
            for suffix in ("", "/download")
        ]

    assert [response.status_code for response in responses] == [404] * 8


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["length", "hash"])
async def test_asset_endpoints_fail_closed_when_stored_bytes_are_corrupt(
    tmp_path: Path, corruption: str
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    expected = b"expected exact bytes"
    actual = b"short" if corruption == "length" else b"different exact bytes"
    if corruption == "hash":
        actual = actual.ljust(len(expected), b"!")[: len(expected)]
    asset_id = uuid.uuid4()
    path = tmp_path / "assets" / "corrupt.bin"
    path.parent.mkdir()
    path.write_bytes(actual)
    with Session(engine) as session:
        session.add(
            Asset(
                id=asset_id,
                kind=AssetKind.PROVIDER_OUTPUT,
                storage_key="corrupt.bin",
                media_type="application/octet-stream",
                byte_length=len(expected),
                sha256=hashlib.sha256(expected).hexdigest(),
                metadata_json={},
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get(f"/api/v1/assets/{asset_id}{suffix}")
            for suffix in ("", "/download")
        ]

    for response in responses:
        assert response.status_code == 409
        assert "etag" not in response.headers
        assert response.json() == {"detail": "asset failed integrity validation"}


def write_frame_artifact(root: Path, frame_id: uuid.UUID, *, valid: bool = True) -> str:
    frame_bytes = bytes(960_000)
    digest = hashlib.sha256(frame_bytes).hexdigest()
    artifact = root / str(frame_id)
    artifact.mkdir()
    (artifact / "frame.bin").write_bytes(frame_bytes)
    (artifact / "manifest.json").write_text(
        json.dumps(
            {
                "frame_id": str(frame_id),
                "byte_length": 960_000,
                "palette_encoding": "gdep133c02-native-transverse-v1",
                "sha256": digest if valid else "f" * 64,
            }
        ),
        encoding="utf-8",
    )
    return digest


@pytest.mark.anyio
async def test_generation_publish_reactivates_exact_frame_without_mutating_history(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    run_id = uuid.uuid4()
    frame_id = uuid.uuid4()
    digest = write_frame_artifact(tmp_path, frame_id)
    published_at = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            GenerationRun(
                id=run_id,
                planned_display_at=published_at,
                generation_due_at=published_at - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                status=GenerationRunStatus.SUPERSEDED,
                frame_id=frame_id,
                published_at=published_at,
                created_at=published_at - timedelta(minutes=15),
                updated_at=published_at,
            )
        )
        session.add(CurrentFrame(id=1, sequence=123))
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(f"/api/v1/generations/{run_id}/publish")
        second = await client.post(f"/api/v1/generations/{run_id}/publish")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["frame_id"] == str(frame_id)
    assert first.json()["sha256"] == digest
    assert first.json()["already_current"] is False
    assert second.json()["already_current"] is True
    assert second.json()["sequence"] == first.json()["sequence"]
    assert second.json()["activated_at"] == first.json()["activated_at"]
    assert first.json()["activated_at"]
    with Session(engine) as session:
        current = session.get(CurrentFrame, 1)
        run = session.get(GenerationRun, run_id)
        assert current is not None
        assert current.frame_id == frame_id
        assert current.generation_run_id == run_id
        assert run is not None
        assert run.status == GenerationRunStatus.SUPERSEDED
        assert run.published_at == published_at.replace(tzinfo=None)


@pytest.mark.anyio
async def test_concurrent_publish_serializes_seeded_singleton_activation(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with Session(engine) as session:
        session.add(CurrentFrame(id=1))
        session.commit()
    frame_ids = [uuid.uuid4(), uuid.uuid4()]
    for frame_id in frame_ids:
        write_frame_artifact(tmp_path, frame_id)

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *(client.post(f"/api/v1/frames/{frame_id}/publish") for frame_id in frame_ids)
        )

    assert [response.status_code for response in responses] == [200, 200]
    sequences = [response.json()["sequence"] for response in responses]
    assert sequences[0] != sequences[1]
    with Session(engine) as session:
        rows = list(session.scalars(select(CurrentFrame)))
        assert len(rows) == 1
        assert rows[0].frame_id in frame_ids
        assert rows[0].sequence == max(sequences)


@pytest.mark.anyio
@pytest.mark.parametrize("publish_path", ["frame", "generation"])
async def test_api_publish_fails_closed_when_current_frame_singleton_is_missing(
    tmp_path: Path, publish_path: str
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    frame_id = uuid.uuid4()
    run_id = uuid.uuid4()
    write_frame_artifact(tmp_path, frame_id)
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            GenerationRun(
                id=run_id,
                planned_display_at=now,
                generation_due_at=now - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                status=GenerationRunStatus.READY,
                frame_id=frame_id,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    endpoint = (
        f"/api/v1/frames/{frame_id}/publish"
        if publish_path == "frame"
        else f"/api/v1/generations/{run_id}/publish"
    )
    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(endpoint)

    assert response.status_code == 500
    assert response.json() == {"detail": "current frame singleton is missing"}
    with Session(engine) as session:
        assert session.get(CurrentFrame, 1) is None


@pytest.mark.anyio
async def test_generation_publish_reports_missing_run_frame_and_invalid_artifact(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    no_frame_run_id = uuid.uuid4()
    missing_artifact_run_id = uuid.uuid4()
    invalid_artifact_run_id = uuid.uuid4()
    missing_frame_id = uuid.uuid4()
    invalid_frame_id = uuid.uuid4()
    write_frame_artifact(tmp_path, invalid_frame_id, valid=False)
    base = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    with Session(engine) as session:
        for index, (run_id, frame_id) in enumerate(
            (
                (no_frame_run_id, None),
                (missing_artifact_run_id, missing_frame_id),
                (invalid_artifact_run_id, invalid_frame_id),
            )
        ):
            planned = base + timedelta(hours=index)
            session.add(
                GenerationRun(
                    id=run_id,
                    planned_display_at=planned,
                    generation_due_at=planned - timedelta(minutes=15),
                    provider="comfyui",
                    model="qwen-image-2512",
                    status=GenerationRunStatus.READY,
                    frame_id=frame_id,
                    created_at=planned,
                    updated_at=planned,
                )
            )
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_run = await client.post(f"/api/v1/generations/{uuid.uuid4()}/publish")
        no_frame = await client.post(f"/api/v1/generations/{no_frame_run_id}/publish")
        missing_artifact = await client.post(
            f"/api/v1/generations/{missing_artifact_run_id}/publish"
        )
        invalid_artifact = await client.post(
            f"/api/v1/generations/{invalid_artifact_run_id}/publish"
        )

    assert missing_run.status_code == 404
    assert no_frame.status_code == 404
    assert missing_artifact.status_code == 404
    assert invalid_artifact.status_code == 409
