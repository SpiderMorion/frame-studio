from __future__ import annotations

import asyncio
import io
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import eink_frame.api as api_module
from eink_frame.api import create_app
from eink_frame.persistence import (
    Base,
    CurrentFrame,
    DeviceStatus,
    GenerationRun,
    GenerationRunStatus,
)


def png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def memory_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_device_manifest_etag_and_status_reporting(tmp_path: Path) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    planned = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
    run_id = uuid.uuid4()
    frame_id = uuid.uuid4()
    frame = bytes([0x10]) * 960000
    frame_sha = "8" * 64
    artifact_dir = tmp_path / str(frame_id)
    artifact_dir.mkdir()
    (artifact_dir / "frame.bin").write_bytes(frame)
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "frame_id": str(frame_id),
                "created_at": (planned - timedelta(minutes=5)).isoformat(),
                "width": 1200,
                "height": 1600,
                "pixel_format": "spectra6-nibbles",
                "palette_encoding": "spectra6-vendor-v1",
                "byte_length": 960000,
                "sha256": frame_sha,
                "url": f"/api/v1/frames/{frame_id}/frame.bin",
                "source_asset_id": None,
                "preset": {"id": "default", "revision": 1},
                "processing": {
                    "pipeline_version": "python-rgb-v1",
                    "dithering": "bayer-8x8",
                    "brightness": 0,
                    "contrast": 0,
                    "saturation": 0,
                    "gamma": 1,
                    "sharpness": 0,
                    "dithering_strength": 1,
                },
            }
        )
    )
    with Session(engine) as session:
        session.add(
            GenerationRun(
                id=run_id,
                planned_display_at=planned,
                generation_due_at=planned - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                status=GenerationRunStatus.PUBLISHED,
                frame_id=frame_id,
                published_at=planned,
                created_at=planned - timedelta(minutes=15),
                updated_at=planned,
            )
        )
        session.add(
            CurrentFrame(
                id=1,
                generation_run_id=run_id,
                frame_id=frame_id,
                activated_at=planned,
            )
        )
        session.commit()

    app = create_app(
        artifact_root=tmp_path,
        session_factory=factory,
        device_token="device-secret",
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer device-secret"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/api/v1/device/manifest")
        manifest = await client.get("/api/v1/device/manifest", headers=headers)
        unchanged = await client.get(
            "/api/v1/device/manifest",
            headers={**headers, "If-None-Match": manifest.headers["etag"]},
        )
        reported = await client.post(
            "/api/v1/device/status",
            headers=headers,
            json={
                "firmware_version": "0.1.0",
                "last_frame_id": str(frame_id),
                "wifi_rssi_dbm": -58,
                "battery_mv": 4012,
                "battery_pct": 82,
                "error_code": None,
                "error_detail": None,
            },
        )

    assert unauthorized.status_code == 401
    assert manifest.status_code == 200
    schema = json.loads(
        (Path(__file__).parents[1] / "contracts/device-manifest.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest.json())
    assert manifest.json()["sequence"] == int(planned.timestamp())
    assert manifest.json()["target"] == "weather-frame"
    assert manifest.json()["panel"] == "GDEP133C02"
    assert manifest.json()["byte_length"] == 960000
    assert unchanged.status_code == 304
    assert reported.status_code == 204
    with Session(engine) as session:
        status = session.get(DeviceStatus, 1)
        assert status is not None
        assert status.last_frame_id == frame_id
        assert status.battery_pct == 82


@pytest.mark.anyio
async def test_manual_publish_makes_an_ad_hoc_frame_current_with_monotonic_sequence(
    tmp_path: Path,
) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CurrentFrame(id=1))
        session.commit()
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(
        artifact_root=tmp_path,
        session_factory=factory,
        device_token="device-secret",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        converted = await client.post(
            "/api/v1/conversions",
            data={"processing_mode": "ad-hoc"},
            files={"source": ("manual.png", png_bytes((40, 80, 120)), "image/png")},
        )
        first_publish = await client.post(f"/api/v1/frames/{converted.json()['frame_id']}/publish")
        second_publish = await client.post(f"/api/v1/frames/{converted.json()['frame_id']}/publish")
        manifest = await client.get(
            "/api/v1/device/manifest",
            headers={"Authorization": "Bearer device-secret"},
        )

    assert converted.status_code == 201
    assert first_publish.status_code == 200
    assert second_publish.status_code == 200
    assert second_publish.json()["sequence"] > first_publish.json()["sequence"]
    assert manifest.status_code == 200
    assert manifest.json()["frame_id"] == converted.json()["frame_id"]
    assert manifest.json()["sequence"] == second_publish.json()["sequence"]
    with Session(engine) as session:
        current = session.get(CurrentFrame, 1)
        assert current is not None
        assert current.frame_id == uuid.UUID(converted.json()["frame_id"])
        assert current.generation_run_id is None


@pytest.mark.anyio
async def test_manifest_etag_changes_when_same_frame_is_republished(tmp_path: Path) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CurrentFrame(id=1))
        session.commit()
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(
        artifact_root=tmp_path,
        session_factory=factory,
        device_token="device-secret",
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer device-secret"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        converted = await client.post(
            "/api/v1/conversions",
            data={"processing_mode": "ad-hoc"},
            files={"source": ("same.png", png_bytes((10, 20, 30)), "image/png")},
        )
        frame_id = converted.json()["frame_id"]
        await client.post(f"/api/v1/frames/{frame_id}/publish")
        first = await client.get("/api/v1/device/manifest", headers=headers)
        await client.post(f"/api/v1/frames/{frame_id}/publish")
        second = await client.get(
            "/api/v1/device/manifest",
            headers={**headers, "If-None-Match": first.headers["etag"]},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]
    assert second.json()["sequence"] > first.json()["sequence"]


@pytest.mark.anyio
async def test_draft_publish_finalizes_current_revision_and_makes_it_current(
    tmp_path: Path,
) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CurrentFrame(id=1))
        session.commit()
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(
        artifact_root=tmp_path,
        session_factory=factory,
        device_token="device-secret",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        draft = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((40, 90, 140)), "image/png")},
        )
        draft_id = draft.json()["draft_id"]
        preview = await client.post(
            f"/api/v1/drafts/{draft_id}/preview",
            json={
                "revision": 2,
                "brightness": 20,
                "contrast": 0,
                "saturation": 0,
                "gamma": 1,
                "sharpness": 0,
                "dithering": "bayer-8x8",
                "dithering_strength": 0.8,
            },
        )
        published = await client.post(f"/api/v1/drafts/{draft_id}/publish")
        device_manifest = await client.get(
            "/api/v1/device/manifest",
            headers={"Authorization": "Bearer device-secret"},
        )

    assert preview.status_code == 200
    assert published.status_code == 200
    body = published.json()
    assert body["draft_id"] == draft_id
    assert body["revision"] == 2
    frame_id = body["frame_id"]
    frame_dir = tmp_path / frame_id
    assert (frame_dir / "frame.bin").stat().st_size == 960_000
    assert (frame_dir / "preview.png").is_file()
    assert (frame_dir / "manifest.json").is_file()
    assert not (frame_dir / "source.png").exists()
    assert len(list(tmp_path.rglob("source.png"))) == 1
    assert device_manifest.status_code == 200
    assert device_manifest.json()["frame_id"] == frame_id
    with factory() as session:
        current = session.get(CurrentFrame, 1)
        assert current is not None
        assert str(current.frame_id) == frame_id
        assert current.generation_run_id is None


@pytest.mark.anyio
async def test_draft_publish_refuses_revision_superseded_during_final_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        draft = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((40, 90, 140)), "image/png")},
        )
        draft_id = draft.json()["draft_id"]
        original = api_module.convert_panel_image
        started = threading.Event()
        release = threading.Event()

        def delayed_render(*args: Any, **kwargs: Any) -> Any:
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        monkeypatch.setattr(api_module, "convert_panel_image", delayed_render)
        publishing = asyncio.create_task(client.post(f"/api/v1/drafts/{draft_id}/publish"))
        assert await asyncio.to_thread(started.wait, 1)
        newer = asyncio.create_task(
            client.post(
                f"/api/v1/drafts/{draft_id}/preview",
                json={
                    "revision": 2,
                    "brightness": 30,
                    "contrast": 0,
                    "saturation": 0,
                    "gamma": 1,
                    "sharpness": 0,
                    "dithering": "bayer-8x8",
                    "dithering_strength": 1,
                },
            )
        )
        await asyncio.sleep(0.05)
        release.set()
        published, preview = await asyncio.gather(publishing, newer)

    assert published.status_code == 409
    assert published.json() == {"detail": "draft changed during final render"}
    assert preview.status_code == 200
    assert [path for path in tmp_path.iterdir() if path.is_dir() and path.name != "drafts"] == []
    with factory() as session:
        assert session.get(CurrentFrame, 1) is None


@pytest.mark.anyio
async def test_draft_publish_removes_frame_when_activation_fails(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path, session_factory=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        draft = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((40, 90, 140)), "image/png")},
        )
        response = await client.post(f"/api/v1/drafts/{draft.json()['draft_id']}/publish")

    assert response.status_code == 503
    frame_directories = [
        path
        for path in tmp_path.iterdir()
        if path.is_dir() and path.name != "drafts" and not path.name.startswith(".")
    ]
    assert frame_directories == []


@pytest.mark.anyio
async def test_draft_publish_fails_closed_when_current_frame_singleton_is_missing(
    tmp_path: Path,
) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        draft = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((40, 90, 140)), "image/png")},
        )
        response = await client.post(f"/api/v1/drafts/{draft.json()['draft_id']}/publish")

    assert response.status_code == 500
    assert response.json() == {"detail": "current frame singleton is missing"}
    with Session(engine) as session:
        assert session.get(CurrentFrame, 1) is None


@pytest.mark.anyio
async def test_operator_device_status_reports_current_frame_confirmation(tmp_path: Path) -> None:
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)
    frame_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            CurrentFrame(
                id=1,
                frame_id=frame_id,
                sequence=42,
                activated_at=now,
            )
        )
        session.add(
            DeviceStatus(
                id=1,
                firmware_version="0.1.1",
                last_frame_id=frame_id,
                wifi_rssi_dbm=-47,
                error_code=None,
                error_detail=None,
                reported_at=now,
            )
        )
        session.commit()

    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/device/status")

    assert response.status_code == 200
    assert response.json() == {
        "firmware_version": "0.1.1",
        "current_frame_id": str(frame_id),
        "last_frame_id": str(frame_id),
        "is_current": True,
        "wifi_rssi_dbm": -47,
        "error_code": None,
        "error_detail": None,
        "reported_at": now.isoformat().replace("+00:00", "Z"),
    }
