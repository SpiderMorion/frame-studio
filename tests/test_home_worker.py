from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from eink_frame.converter import DitheringAlgorithm, ProcessingPreset
from eink_frame.home_worker import (
    HardwareProfile,
    RenderPayload,
    SourceImageError,
    render_home_artifacts,
)


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format="PNG")
    return output.getvalue()


def _payload() -> RenderPayload:
    return RenderPayload(
        source_asset_id="asset-42",
        source_asset_path=Path("generation/run-7/output.png"),
        planned_display_at=datetime(2026, 8, 24, 6, 30, tzinfo=UTC),
        generation_run_id="run-7",
        preset_id="preset-weather",
        preset_revision=3,
        preset_settings=ProcessingPreset(brightness=5.0, contrast=10.0),
        dithering=DitheringAlgorithm.NONE,
        hardware_profile=HardwareProfile(
            profile_id="gdep133c02-native-transverse-v1",
            width=1200,
            height=1600,
            palette_codes=(0, 1, 2, 3, 5, 6),
            palette_encoding="gdep133c02-native-transverse-v1",
            rotation="transverse",
        ),
    )


def test_render_builds_complete_verified_staging_artifacts(tmp_path: Path) -> None:
    staging_dir = tmp_path / "render-staging"

    result = render_home_artifacts(_png_bytes(), _payload(), staging_dir)

    assert {path.name for path in staging_dir.iterdir()} == {
        "source.png",
        "preview.png",
        "frame.bin",
        "manifest.json",
    }
    with Image.open(staging_dir / "source.png") as source:
        assert source.mode == "RGB"
    with Image.open(staging_dir / "preview.png") as preview:
        assert preview.size == (1600, 1200)
    assert (staging_dir / "frame.bin").stat().st_size == 960_000
    manifest = json.loads((staging_dir / "manifest.json").read_text(encoding="utf-8"))
    assert result.staging_dir == staging_dir
    assert result.frame_sha256 == manifest["frame"]["sha256"]
    assert result.frame_byte_length == 960_000
    assert result.manifest == manifest


def test_render_rejects_corrupt_image_without_partial_artifacts(tmp_path: Path) -> None:
    staging_dir = tmp_path / "render-staging"

    with pytest.raises(SourceImageError, match="unsupported or corrupt source image"):
        render_home_artifacts(b"not an image", _payload(), staging_dir)

    assert not staging_dir.exists()
    assert list(tmp_path.iterdir()) == []


def test_render_produces_deterministic_packed_hash(tmp_path: Path) -> None:
    first = render_home_artifacts(_png_bytes(), _payload(), tmp_path / "first")
    second = render_home_artifacts(_png_bytes(), _payload(), tmp_path / "second")

    expected_hash = "109fac026930ef800539bc36813f342a4716901e6be9a0f58adc0cb5a50f4d4b"
    assert first.frame_sha256 == expected_hash
    assert second.frame_sha256 == expected_hash
    assert (tmp_path / "first" / "frame.bin").read_bytes() == (
        tmp_path / "second" / "frame.bin"
    ).read_bytes()
    assert (
        hashlib.sha256((tmp_path / "first" / "frame.bin").read_bytes()).hexdigest() == expected_hash
    )


def test_manifest_has_exact_input_and_artifact_linkage(tmp_path: Path) -> None:
    result = render_home_artifacts(_png_bytes(), _payload(), tmp_path / "staging")

    assert result.manifest == {
        "schema_version": 1,
        "artifact_type": "home-render",
        "generation_run_id": "run-7",
        "planned_display_at": "2026-08-24T06:30:00+00:00",
        "source": {
            "asset_id": "asset-42",
            "path": "generation/run-7/output.png",
        },
        "preset": {"id": "preset-weather", "revision": 3},
        "processing": {
            "pipeline_version": "python-rgb-v1",
            "dithering": "none",
            "brightness": 5.0,
            "contrast": 10.0,
            "saturation": 0.0,
            "gamma": 1.0,
            "sharpness": 0.0,
            "dithering_strength": 1.0,
        },
        "hardware_profile": {
            "id": "gdep133c02-native-transverse-v1",
            "width": 1200,
            "height": 1600,
            "palette_codes": [0, 1, 2, 3, 5, 6],
            "palette_encoding": "gdep133c02-native-transverse-v1",
            "rotation": "transverse",
        },
        "frame": {
            "filename": "frame.bin",
            "pixel_format": "spectra6-nibbles",
            "byte_length": 960_000,
            "sha256": "109fac026930ef800539bc36813f342a4716901e6be9a0f58adc0cb5a50f4d4b",
        },
        "artifacts": {
            "source": "source.png",
            "preview": "preview.png",
            "frame": "frame.bin",
        },
    }
