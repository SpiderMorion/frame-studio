from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image

import eink_frame.api as api_module
from eink_frame.api import create_app


def png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_convert_upload_persists_preview_frame_and_manifest(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/conversions",
            files={"source": ("red.png", png_bytes((255, 0, 0)), "image/png")},
        )

    assert response.status_code == 201
    body = response.json()
    schema = json.loads(
        (Path(__file__).parents[1] / "contracts/frame-manifest.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(body)
    assert body["processing"]["dithering"] == "good-display-floyd-v1"
    artifact_dir = tmp_path / body["frame_id"]
    assert body["width"] == 1200
    assert body["height"] == 1600
    assert body["byte_length"] == 960_000
    assert len(body["sha256"]) == 64
    assert (artifact_dir / "source.png").is_file()
    assert (artifact_dir / "preview.png").is_file()
    assert (artifact_dir / "frame.bin").stat().st_size == 960_000
    assert (artifact_dir / "manifest.json").is_file()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        frame_response = await client.get(body["url"])

    assert frame_response.status_code == 200
    assert len(frame_response.content) == 960_000
    assert frame_response.headers["etag"] == f'"{body["sha256"]}"'
    assert hashlib.sha256(frame_response.content).hexdigest() == body["sha256"]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unchanged = await client.get(
            body["url"], headers={"If-None-Match": frame_response.headers["etag"]}
        )

    assert unchanged.status_code == 304
    assert unchanged.content == b""

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview_response = await client.get(f"/api/v1/frames/{body['frame_id']}/preview.png")

    assert preview_response.status_code == 200
    preview = Image.open(io.BytesIO(preview_response.content))
    assert preview.size == (1600, 1200)
    assert preview.getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.anyio
async def test_draft_upload_stores_one_source_and_preview_without_frame_artifacts(
    tmp_path: Path,
) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((80, 120, 160)), "image/png")},
        )
        preview = await client.get(response.json()["preview_url"])

    assert response.status_code == 201
    body = response.json()
    assert body["revision"] == 1
    draft_dir = tmp_path / "drafts" / body["draft_id"]
    assert (draft_dir / "source.png").is_file()
    assert (draft_dir / "revisions" / "1" / "preview.png").is_file()
    settings_path = draft_dir / "revisions" / "1" / "settings.json"
    assert settings_path.is_file()
    assert json.loads(settings_path.read_text())["dithering"] == "good-display-floyd-v1"
    assert json.loads((draft_dir / "current.json").read_text())["revision"] == 1
    assert json.loads((draft_dir / "requested.json").read_text())["revision"] == 1
    assert preview.status_code == 200
    assert list(tmp_path.rglob("source.png")) == [draft_dir / "source.png"]
    assert list(tmp_path.rglob("frame.bin")) == []
    assert list(tmp_path.rglob("manifest.json")) == []


@pytest.mark.anyio
async def test_draft_preview_keeps_only_the_latest_requested_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((80, 120, 160)), "image/png")},
        )
        draft_id = uploaded.json()["draft_id"]
        original = api_module.convert_panel_image
        started = threading.Event()
        release = threading.Event()

        def delayed_render(*args: Any, **kwargs: Any) -> Any:
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        monkeypatch.setattr(api_module, "convert_panel_image", delayed_render)
        base_settings = {
            "contrast": 0,
            "saturation": 0,
            "gamma": 1,
            "sharpness": 0,
            "dithering": "bayer-8x8",
            "dithering_strength": 1,
        }
        older = asyncio.create_task(
            client.post(
                f"/api/v1/drafts/{draft_id}/preview",
                json={**base_settings, "revision": 2, "brightness": 10},
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        latest = asyncio.create_task(
            client.post(
                f"/api/v1/drafts/{draft_id}/preview",
                json={**base_settings, "revision": 3, "brightness": 30},
            )
        )
        await asyncio.sleep(0.05)
        release.set()
        older_response, latest_response = await asyncio.gather(older, latest)

    assert older_response.status_code == 409
    assert older_response.json() == {
        "detail": {
            "code": "preview_superseded",
            "current_revision": 1,
            "requested_revision": 3,
        }
    }
    assert latest_response.status_code == 200
    assert latest_response.json()["revision"] == 3
    draft_dir = tmp_path / "drafts" / draft_id
    assert json.loads((draft_dir / "current.json").read_text())["revision"] == 3
    settings = json.loads((draft_dir / "revisions" / "3" / "settings.json").read_text())
    assert settings["brightness"] == 30
    assert len(list(tmp_path.rglob("source.png"))) == 1
    assert list(tmp_path.rglob("frame.bin")) == []


@pytest.mark.anyio
async def test_draft_revisions_are_coordinated_across_app_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_a = create_app(artifact_root=tmp_path)
    app_b = create_app(artifact_root=tmp_path)
    transport_a = httpx.ASGITransport(app=app_a)
    transport_b = httpx.ASGITransport(app=app_b)
    async with (
        httpx.AsyncClient(transport=transport_a, base_url="http://a") as client_a,
        httpx.AsyncClient(transport=transport_b, base_url="http://b") as client_b,
    ):
        uploaded = await client_a.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((80, 120, 160)), "image/png")},
        )
        draft_id = uploaded.json()["draft_id"]
        original = api_module.convert_panel_image
        started = threading.Event()
        release = threading.Event()

        def delayed_render(*args: Any, **kwargs: Any) -> Any:
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        monkeypatch.setattr(api_module, "convert_panel_image", delayed_render)
        base = {
            "contrast": 0,
            "saturation": 0,
            "gamma": 1,
            "sharpness": 0,
            "dithering": "bayer-8x8",
            "dithering_strength": 1,
        }
        older = asyncio.create_task(
            client_a.post(
                f"/api/v1/drafts/{draft_id}/preview",
                json={**base, "revision": 2, "brightness": 10},
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        latest = asyncio.create_task(
            client_b.post(
                f"/api/v1/drafts/{draft_id}/preview",
                json={**base, "revision": 3, "brightness": 30},
            )
        )
        await asyncio.sleep(0.05)
        release.set()
        older_response, latest_response = await asyncio.gather(older, latest)

    assert older_response.status_code == 409
    assert latest_response.status_code == 200
    draft_dir = tmp_path / "drafts" / draft_id
    assert json.loads((draft_dir / "current.json").read_text())["revision"] == 3


@pytest.mark.anyio
async def test_failed_draft_preview_can_retry_the_same_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((80, 120, 160)), "image/png")},
        )
        draft_id = uploaded.json()["draft_id"]
        original = api_module.convert_panel_image
        attempts = 0

        def fail_once(*args: Any, **kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected preview failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(api_module, "convert_panel_image", fail_once)
        payload = {
            "revision": 2,
            "brightness": 10,
            "contrast": 0,
            "saturation": 0,
            "gamma": 1,
            "sharpness": 0,
            "dithering": "bayer-8x8",
            "dithering_strength": 1,
        }
        failed = await client.post(f"/api/v1/drafts/{draft_id}/preview", json=payload)
        retried = await client.post(f"/api/v1/drafts/{draft_id}/preview", json=payload)

    assert failed.status_code == 500
    assert retried.status_code == 200
    assert retried.json()["revision"] == 2


@pytest.mark.anyio
async def test_draft_creation_has_a_hard_session_limit(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path, draft_limit=1)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/drafts",
            files={"source": ("one.png", png_bytes((1, 2, 3)), "image/png")},
        )
        second = await client.post(
            "/api/v1/drafts",
            files={"source": ("two.png", png_bytes((4, 5, 6)), "image/png")},
        )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json() == {"detail": "draft session limit reached"}


@pytest.mark.anyio
async def test_oversized_revision_cannot_poison_a_draft(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((1, 2, 3)), "image/png")},
        )
        draft_id = uploaded.json()["draft_id"]
        settings = {
            "brightness": 0,
            "contrast": 0,
            "saturation": 0,
            "gamma": 1,
            "sharpness": 0,
            "dithering": "bayer-8x8",
            "dithering_strength": 1,
        }
        rejected = await client.post(
            f"/api/v1/drafts/{draft_id}/preview",
            json={**settings, "revision": int("9" * 300)},
        )
        valid = await client.post(
            f"/api/v1/drafts/{draft_id}/preview",
            json={**settings, "revision": 2},
        )

    assert rejected.status_code == 422
    assert valid.status_code == 200


@pytest.mark.anyio
async def test_draft_retains_only_a_bounded_number_of_revisions(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/drafts",
            files={"source": ("source.png", png_bytes((1, 2, 3)), "image/png")},
        )
        draft_id = uploaded.json()["draft_id"]
        for revision in range(2, 8):
            response = await client.post(
                f"/api/v1/drafts/{draft_id}/preview",
                json={
                    "revision": revision,
                    "brightness": revision,
                    "contrast": 0,
                    "saturation": 0,
                    "gamma": 1,
                    "sharpness": 0,
                    "dithering": "bayer-8x8",
                    "dithering_strength": 1,
                },
            )
            assert response.status_code == 200

    revisions = tmp_path / "drafts" / draft_id / "revisions"
    assert sorted(path.name for path in revisions.iterdir()) == ["4", "5", "6", "7"]


@pytest.mark.anyio
async def test_convert_rejects_invalid_image_bytes(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/conversions",
            files={"source": ("fake.png", b"not an image", "image/png")},
        )

    assert response.status_code == 415
    assert response.json() == {"detail": "unsupported or corrupt image"}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_convert_rejects_upload_over_configured_limit(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path, max_upload_bytes=8)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/conversions",
            files={"source": ("large.bin", b"123456789", "application/octet-stream")},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "upload exceeds 8 bytes"}


@pytest.mark.anyio
async def test_provider_catalog_exposes_supported_connection_types(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/provider-kinds")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["kind"] == "comfyui"
    assert body[0]["default"] is True
    assert {item["kind"] for item in body} == {
        "comfyui",
        "openai",
        "openai-compatible",
        "gemini",
        "bfl",
        "stability",
        "replicate",
        "fal",
    }
    assert all("secret_value" not in item for item in body)


@pytest.mark.anyio
async def test_conversion_dithering_parameter_changes_preview(tmp_path: Path) -> None:
    gradient = Image.linear_gradient("L").resize((64, 64)).convert("RGB")
    encoded = io.BytesIO()
    gradient.save(encoded, format="PNG")
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        nearest = await client.post(
            "/api/v1/conversions",
            data={"dithering": "none"},
            files={"source": ("gradient.png", encoded.getvalue(), "image/png")},
        )
        bayer = await client.post(
            "/api/v1/conversions",
            data={"dithering": "bayer-8x8"},
            files={"source": ("gradient.png", encoded.getvalue(), "image/png")},
        )
        nearest_preview = await client.get(
            f"/api/v1/frames/{nearest.json()['frame_id']}/preview.png"
        )
        bayer_preview = await client.get(f"/api/v1/frames/{bayer.json()['frame_id']}/preview.png")

    assert nearest.status_code == 201
    assert bayer.status_code == 201
    assert nearest_preview.content != bayer_preview.content


@pytest.mark.anyio
async def test_reprocesses_an_existing_source_without_reuploading_it(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/conversions",
            files={"source": ("source.png", png_bytes((80, 120, 160)), "image/png")},
        )
        response = await client.post(
            f"/api/v1/conversions/{uploaded.json()['frame_id']}",
            json={
                "brightness": 25,
                "contrast": -10,
                "saturation": 40,
                "gamma": 1.3,
                "sharpness": 15,
                "dithering": "floyd-steinberg",
                "dithering_strength": 0.7,
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["frame_id"] != uploaded.json()["frame_id"]
    assert body["processing"] == {
        "pipeline_version": "python-rgb-v1",
        "brightness": 25.0,
        "contrast": -10.0,
        "saturation": 40.0,
        "gamma": 1.3,
        "sharpness": 15.0,
        "dithering": "floyd-steinberg",
        "dithering_strength": 0.7,
    }
    assert (tmp_path / body["frame_id"] / "source.png").read_bytes() == (
        tmp_path / uploaded.json()["frame_id"] / "source.png"
    ).read_bytes()


@pytest.mark.anyio
async def test_health_remains_responsive_while_conversion_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = api_module.convert_panel_image

    def slow_render(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.4)
        return original(*args, **kwargs)

    monkeypatch.setattr(api_module, "convert_panel_image", slow_render)
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = time.perf_counter()
        conversion = asyncio.create_task(
            client.post(
                "/api/v1/conversions",
                files={"source": ("red.png", png_bytes((255, 0, 0)), "image/png")},
            )
        )
        health = asyncio.create_task(client.get("/api/v1/health"))
        health_response = await health
        health_elapsed = time.perf_counter() - started
        conversion_response = await conversion

    assert health_response.status_code == 200
    assert health_elapsed < 0.2
    assert conversion_response.status_code == 201


@pytest.mark.anyio
async def test_health_reports_ready_artifact_store(tmp_path: Path) -> None:
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "artifact_store": "writable"}


@pytest.mark.anyio
async def test_failed_render_leaves_no_partial_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_render(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected renderer failure")

    monkeypatch.setattr(api_module, "convert_panel_image", fail_render)
    app = create_app(artifact_root=tmp_path)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/conversions",
            files={"source": ("red.png", png_bytes((255, 0, 0)), "image/png")},
        )

    assert response.status_code == 500
    assert list(tmp_path.iterdir()) == []
