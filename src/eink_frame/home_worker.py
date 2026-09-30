from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from .converter import DitheringAlgorithm, ProcessingPreset, convert_panel_image

_FRAME_WIDTH = 1600
_FRAME_HEIGHT = 1200
_FRAME_BYTE_LENGTH = 960_000
_ARTIFACT_NAMES = ("source.png", "preview.png", "frame.bin", "manifest.json")


class SourceImageError(ValueError):
    """Raised when supplied source bytes cannot be decoded as an image."""


@dataclass(frozen=True)
class HardwareProfile:
    """Immutable hardware values that affect packed frame bytes."""

    profile_id: str
    width: int
    height: int
    palette_codes: tuple[int, int, int, int, int, int]
    palette_encoding: str
    rotation: Literal["clockwise", "counterclockwise", "transverse"] = "clockwise"


@dataclass(frozen=True)
class RenderPayload:
    """Immutable caller-owned provenance and processing snapshot for one render."""

    source_asset_id: str
    planned_display_at: datetime
    generation_run_id: str
    preset_id: str
    preset_revision: int
    preset_settings: ProcessingPreset
    dithering: DitheringAlgorithm
    hardware_profile: HardwareProfile
    source_asset_path: Path | None = None
    source_asset_bytes_ref: str | None = None

    def __post_init__(self) -> None:
        if self.planned_display_at.tzinfo is None or self.planned_display_at.utcoffset() is None:
            raise ValueError("planned_display_at must be timezone-aware")
        if (self.source_asset_path is None) == (self.source_asset_bytes_ref is None):
            raise ValueError("provide exactly one source asset path or bytes reference")
        if not self.source_asset_id:
            raise ValueError("source_asset_id must not be empty")
        if self.preset_revision < 1:
            raise ValueError("preset_revision must be at least 1")


@dataclass(frozen=True)
class RenderResult:
    staging_dir: Path
    frame_sha256: str
    frame_byte_length: int
    manifest: dict[str, Any]


def render_home_artifacts(
    source_rgb_bytes: bytes, payload: RenderPayload, staging_dir: Path
) -> RenderResult:
    """Render a complete local artifact set; publication remains the caller's responsibility."""
    if staging_dir.exists():
        raise FileExistsError(f"staging directory already exists: {staging_dir}")
    profile = payload.hardware_profile
    if (profile.width, profile.height) != (1200, 1600):
        raise ValueError("home render hardware profile must use native 1200x1600 geometry")

    source = _decode_rgb(source_rgb_bytes)
    converted = convert_panel_image(
        source,
        palette_codes=profile.palette_codes,
        preset=payload.preset_settings,
        dithering=payload.dithering,
        rotation=profile.rotation,
    )
    if converted.preview.size != (_FRAME_WIDTH, _FRAME_HEIGHT):
        raise ValueError("converter produced an unexpected preview size")
    if len(converted.packed) != _FRAME_BYTE_LENGTH:
        raise ValueError("converter produced an unexpected packed byte length")

    source_png = _encode_png(source)
    preview_png = _encode_png(converted.preview)
    frame_sha256 = hashlib.sha256(converted.packed).hexdigest()
    manifest = _build_manifest(payload, frame_sha256)
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    artifacts = {
        "source.png": source_png,
        "preview.png": preview_png,
        "frame.bin": converted.packed,
        "manifest.json": manifest_bytes,
    }

    try:
        staging_dir.mkdir(parents=False)
        for name in _ARTIFACT_NAMES:
            _write_fsynced(staging_dir / name, artifacts[name])
        written_frame = (staging_dir / "frame.bin").read_bytes()
        if len(written_frame) != _FRAME_BYTE_LENGTH:
            raise OSError("staged frame has an unexpected byte length")
        if hashlib.sha256(written_frame).hexdigest() != frame_sha256:
            raise OSError("staged frame checksum mismatch")
        _fsync_directory(staging_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return RenderResult(
        staging_dir=staging_dir,
        frame_sha256=frame_sha256,
        frame_byte_length=_FRAME_BYTE_LENGTH,
        manifest=manifest,
    )


def _decode_rgb(content: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(content)) as opened:
            opened.load()
            converted: Image.Image = opened.convert("RGB")
            return converted
    except (OSError, UnidentifiedImageError) as error:
        raise SourceImageError("unsupported or corrupt source image") from error


def _encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _build_manifest(payload: RenderPayload, frame_sha256: str) -> dict[str, Any]:
    profile = payload.hardware_profile
    settings = asdict(payload.preset_settings)
    source: dict[str, str] = {"asset_id": payload.source_asset_id}
    if payload.source_asset_path is not None:
        source["path"] = str(payload.source_asset_path)
    else:
        assert payload.source_asset_bytes_ref is not None
        source["bytes_ref"] = payload.source_asset_bytes_ref
    return {
        "schema_version": 1,
        "artifact_type": "home-render",
        "generation_run_id": payload.generation_run_id,
        "planned_display_at": payload.planned_display_at.isoformat(),
        "source": source,
        "preset": {"id": payload.preset_id, "revision": payload.preset_revision},
        "processing": {
            "pipeline_version": "python-rgb-v1",
            "dithering": payload.dithering.value,
            **settings,
        },
        "hardware_profile": {
            "id": profile.profile_id,
            "width": profile.width,
            "height": profile.height,
            "palette_codes": list(profile.palette_codes),
            "palette_encoding": profile.palette_encoding,
            "rotation": profile.rotation,
        },
        "frame": {
            "filename": "frame.bin",
            "pixel_format": "spectra6-nibbles",
            "byte_length": _FRAME_BYTE_LENGTH,
            "sha256": frame_sha256,
        },
        "artifacts": {
            "source": "source.png",
            "preview": "preview.png",
            "frame": "frame.bin",
        },
    }


def _write_fsynced(path: Path, content: bytes) -> None:
    with path.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
