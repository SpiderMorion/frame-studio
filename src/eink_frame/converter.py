from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, cast

import numpy as np
from numba import njit  # type: ignore[import-untyped]
from numpy.typing import NDArray
from PIL import Image, ImageEnhance, ImageOps

SPECTRA6_RGB: NDArray[np.uint8] = np.array(
    [
        [0, 0, 0],
        [255, 255, 255],
        [255, 255, 0],
        [255, 0, 0],
        [0, 0, 255],
        [0, 255, 0],
    ],
    dtype=np.uint8,
)


class DitheringAlgorithm(StrEnum):
    NONE = "none"
    FLOYD_STEINBERG = "floyd-steinberg"
    GOOD_DISPLAY_FLOYD_V1 = "good-display-floyd-v1"
    ATKINSON = "atkinson"
    SIERRA_LITE = "sierra-lite"
    JARVIS_JUDICE_NINKE = "jarvis-judice-ninke"
    BAYER_4X4 = "bayer-4x4"
    BAYER_8X8 = "bayer-8x8"


@dataclass(frozen=True)
class ConversionResult:
    preview: Image.Image
    packed: bytes


@dataclass(frozen=True)
class ProcessingPreset:
    """Deterministic image-processing controls."""

    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    gamma: float = 1.0
    sharpness: float = 0.0
    dithering_strength: float = 1.0

    def __post_init__(self) -> None:
        ranges = {
            "brightness": (-100.0, 100.0),
            "contrast": (-100.0, 100.0),
            "saturation": (-100.0, 100.0),
            "gamma": (0.1, 5.0),
            "sharpness": (-100.0, 100.0),
            "dithering_strength": (0.0, 1.0),
        }
        for field, (minimum, maximum) in ranges.items():
            value = getattr(self, field)
            if not isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"{field} must be between {minimum} and {maximum}")


def apply_adjustments(rgb: NDArray[np.uint8], preset: ProcessingPreset) -> NDArray[np.uint8]:
    """Apply deterministic processing controls without changing the source array."""
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("expected an H×W×3 RGB array")
    image = Image.fromarray(rgb.copy())
    image = ImageEnhance.Brightness(image).enhance(1.0 + preset.brightness / 100.0)
    image = ImageEnhance.Contrast(image).enhance(1.0 + preset.contrast / 100.0)
    image = ImageEnhance.Color(image).enhance(1.0 + preset.saturation / 100.0)
    gamma_lut = np.rint(
        (np.arange(256, dtype=np.float64) / 255.0) ** (1.0 / preset.gamma) * 255.0
    ).astype(np.uint8)
    image = image.point(gamma_lut.tolist() * 3)
    image = ImageEnhance.Sharpness(image).enhance(1.0 + preset.sharpness / 100.0)
    return np.asarray(image, dtype=np.uint8).copy()


def quantize_nearest(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Return the nearest nominal Spectra 6 palette index for each RGB pixel."""
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("expected an H×W×3 RGB array")
    colors = rgb.astype(np.int32, copy=False)
    palette = SPECTRA6_RGB.astype(np.int32, copy=False)
    distances = np.sum((colors[..., None, :] - palette) ** 2, axis=-1)
    result = np.argmin(distances, axis=-1).astype(np.uint8)
    return cast(NDArray[np.uint8], result)


def quantize(
    rgb: NDArray[np.uint8],
    algorithm: DitheringAlgorithm = DitheringAlgorithm.NONE,
    *,
    strength: float = 1.0,
) -> NDArray[np.uint8]:
    """Quantize RGB pixels to exact Spectra 6 palette indices."""
    if not isfinite(strength) or not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0.0 and 1.0")
    if algorithm is DitheringAlgorithm.NONE or strength == 0.0:
        return quantize_nearest(rgb)
    if algorithm is DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1:
        if rgb.ndim != 3 or rgb.shape[-1] != 3:
            raise ValueError("expected an H×W×3 RGB array")
        return cast(NDArray[np.uint8], _good_display_floyd_v1_compiled(rgb, strength))
    if algorithm is DitheringAlgorithm.FLOYD_STEINBERG:
        return _error_diffusion(
            rgb,
            ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)),
            strength,
        )
    if algorithm is DitheringAlgorithm.ATKINSON:
        return _error_diffusion(
            rgb,
            (
                (1, 0, 1 / 8),
                (2, 0, 1 / 8),
                (-1, 1, 1 / 8),
                (0, 1, 1 / 8),
                (1, 1, 1 / 8),
                (0, 2, 1 / 8),
            ),
            strength,
        )
    if algorithm is DitheringAlgorithm.SIERRA_LITE:
        return _error_diffusion(rgb, ((1, 0, 2 / 4), (-1, 1, 1 / 4), (0, 1, 1 / 4)), strength)
    if algorithm is DitheringAlgorithm.JARVIS_JUDICE_NINKE:
        return _error_diffusion(
            rgb,
            (
                (1, 0, 7 / 48),
                (2, 0, 5 / 48),
                (-2, 1, 3 / 48),
                (-1, 1, 5 / 48),
                (0, 1, 7 / 48),
                (1, 1, 5 / 48),
                (2, 1, 3 / 48),
                (-2, 2, 1 / 48),
                (-1, 2, 3 / 48),
                (0, 2, 5 / 48),
                (1, 2, 3 / 48),
                (2, 2, 1 / 48),
            ),
            strength,
        )
    if algorithm is DitheringAlgorithm.BAYER_4X4:
        return _ordered_dither(
            rgb,
            np.array(
                [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                dtype=np.float64,
            ),
            strength,
        )
    if algorithm is DitheringAlgorithm.BAYER_8X8:
        return _ordered_dither(
            rgb,
            np.array(
                [
                    [0, 32, 8, 40, 2, 34, 10, 42],
                    [48, 16, 56, 24, 50, 18, 58, 26],
                    [12, 44, 4, 36, 14, 46, 6, 38],
                    [60, 28, 52, 20, 62, 30, 54, 22],
                    [3, 35, 11, 43, 1, 33, 9, 41],
                    [51, 19, 59, 27, 49, 17, 57, 25],
                    [15, 47, 7, 39, 13, 45, 5, 37],
                    [63, 31, 55, 23, 61, 29, 53, 21],
                ],
                dtype=np.float64,
            ),
            strength,
        )
    raise ValueError(f"unsupported dithering algorithm: {algorithm}")


def _ordered_dither(
    rgb: NDArray[np.uint8], matrix: NDArray[np.float64], strength: float
) -> NDArray[np.uint8]:
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("expected an H×W×3 RGB array")
    height, width, _ = rgb.shape
    size = matrix.shape[0]
    tiled = np.tile(matrix, ((height + size - 1) // size, (width + size - 1) // size))[
        :height, :width
    ]
    offset = ((tiled + 0.5) / float(size * size) - 0.5) * 255.0 * strength
    adjusted = np.clip(rgb.astype(np.float64) + offset[..., None], 0.0, 255.0)
    palette = SPECTRA6_RGB.astype(np.float64, copy=False)
    distances = np.sum((adjusted[..., None, :] - palette) ** 2, axis=-1)
    return cast(NDArray[np.uint8], np.argmin(distances, axis=-1).astype(np.uint8))


def _error_diffusion(
    rgb: NDArray[np.uint8],
    weights: tuple[tuple[int, int, float], ...],
    strength: float,
) -> NDArray[np.uint8]:
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("expected an H×W×3 RGB array")
    compiled = _error_diffusion_compiled(
        rgb,
        np.asarray(weights, dtype=np.float64),
        strength,
    )
    return cast(NDArray[np.uint8], compiled)


@njit(nogil=True)  # type: ignore[untyped-decorator]
def _good_display_floyd_v1_compiled(rgb: NDArray[np.uint8], strength: float) -> NDArray[np.uint8]:
    """Match Good Display's byte-clamped six-color Floyd–Steinberg tool."""
    work = rgb.copy()
    height, width, _ = work.shape
    indices = np.empty((height, width), dtype=np.uint8)
    palette = np.array(
        (
            (0, 0, 0),
            (255, 255, 255),
            (255, 255, 0),
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
        ),
        dtype=np.uint8,
    )
    logical_indices = np.array((0, 1, 2, 3, 5, 4), dtype=np.uint8)
    offsets = ((1, 0, 7.0 / 16.0), (-1, 1, 3.0 / 16.0), (0, 1, 5.0 / 16.0), (1, 1, 1.0 / 16.0))
    for y in range(height):
        for x in range(width):
            best_index = 0
            best_distance = np.inf
            for palette_index in range(6):
                distance = 0.0
                for channel in range(3):
                    difference = float(work[y, x, channel]) - float(palette[palette_index, channel])
                    distance += difference * difference
                if distance < best_distance:
                    best_distance = distance
                    best_index = palette_index
            indices[y, x] = logical_indices[best_index]
            error = np.empty(3, dtype=np.float64)
            for channel in range(3):
                error[channel] = float(work[y, x, channel]) - float(palette[best_index, channel])
                work[y, x, channel] = palette[best_index, channel]
            for dx, dy, factor in offsets:
                target_x = x + int(dx)
                target_y = y + int(dy)
                if 0 <= target_x < width and target_y < height:
                    for channel in range(3):
                        value = float(work[target_y, target_x, channel]) + (
                            error[channel] * factor * strength
                        )
                        work[target_y, target_x, channel] = np.uint8(
                            min(255.0, max(0.0, np.rint(value)))
                        )
    return indices


@njit(nogil=True)  # type: ignore[untyped-decorator]
def _error_diffusion_compiled(
    rgb: NDArray[np.uint8], weights: NDArray[np.float64], strength: float
) -> NDArray[np.uint8]:
    work = rgb.astype(np.float64)
    height, width, _ = work.shape
    indices = np.empty((height, width), dtype=np.uint8)
    palette = SPECTRA6_RGB.astype(np.float64)
    for y in range(height):
        for x in range(width):
            best_index = 0
            best_distance = np.inf
            for palette_index in range(6):
                distance = 0.0
                for channel in range(3):
                    difference = palette[palette_index, channel] - work[y, x, channel]
                    distance += difference * difference
                if distance < best_distance:
                    best_distance = distance
                    best_index = palette_index
            indices[y, x] = best_index
            for weight_index in range(weights.shape[0]):
                target_x = x + int(weights[weight_index, 0])
                target_y = y + int(weights[weight_index, 1])
                if 0 <= target_x < width and 0 <= target_y < height:
                    weight = weights[weight_index, 2] * strength
                    for channel in range(3):
                        error = work[y, x, channel] - palette[best_index, channel]
                        value = work[target_y, target_x, channel] + error * weight
                        work[target_y, target_x, channel] = min(255.0, max(0.0, value))
    return indices


def pack_indices(
    indices: NDArray[np.uint8],
    *,
    palette_codes: tuple[int, int, int, int, int, int],
    first_pixel_high_nibble: bool = True,
) -> bytes:
    """Pack two palette indices per byte using an explicit hardware nibble order."""
    flat = indices.reshape(-1)
    if flat.size % 2:
        raise ValueError("pixel count must be even")
    if len(set(palette_codes)) != 6 or any(code < 0 or code > 15 for code in palette_codes):
        raise ValueError("hardware palette mapping must contain six unique nibbles")
    if np.any(flat > 5):
        raise ValueError("logical palette index must be between 0 and 5")
    codes = np.asarray(palette_codes, dtype=np.uint8)[flat]
    first, second = codes[0::2], codes[1::2]
    packed = (first << 4) | second if first_pixel_high_nibble else (second << 4) | first
    return packed.tobytes()


def pack_vendor_rows(
    indices: NDArray[np.uint8],
    *,
    palette_codes: tuple[int, int, int, int, int, int],
) -> bytes:
    """Pack native panel rows in the exact GDEP133C02 vendor pair order."""
    if indices.ndim != 2 or indices.shape[1] % 2:
        raise ValueError("vendor frame must be a two-dimensional even-width index buffer")
    if len(set(palette_codes)) != 6 or any(code < 0 or code > 15 for code in palette_codes):
        raise ValueError("hardware palette mapping must contain six unique nibbles")
    if np.any(indices > 5):
        raise ValueError("logical palette index must be between 0 and 5")
    codes = np.asarray(palette_codes, dtype=np.uint8)[indices]
    packed_pairs = (codes[:, 0::2] << 4) | codes[:, 1::2]
    return packed_pairs[:, ::-1].tobytes()


def convert_image(
    source: Image.Image,
    *,
    width: int,
    height: int,
    palette_codes: tuple[int, int, int, int, int, int],
    preset: ProcessingPreset | None = None,
    dithering: DitheringAlgorithm = DitheringAlgorithm.NONE,
) -> ConversionResult:
    """Fit, adjust and quantize an image before creating output artifacts."""
    fitted = ImageOps.fit(source.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    controls = preset or ProcessingPreset()
    adjusted = apply_adjustments(np.asarray(fitted, dtype=np.uint8), controls)
    indices = quantize(adjusted, dithering, strength=controls.dithering_strength)
    preview = Image.fromarray(SPECTRA6_RGB[indices])
    packed = pack_indices(indices, palette_codes=palette_codes)
    return ConversionResult(preview=preview, packed=packed)


def convert_panel_image(
    source: Image.Image,
    *,
    palette_codes: tuple[int, int, int, int, int, int],
    preset: ProcessingPreset | None = None,
    dithering: DitheringAlgorithm = DitheringAlgorithm.NONE,
    rotation: Literal["clockwise", "counterclockwise", "transverse"] = "clockwise",
) -> ConversionResult:
    """Create a landscape preview and native 1200×1600 vendor-layout panel frame."""
    fitted = ImageOps.fit(source.convert("RGB"), (1600, 1200), Image.Resampling.LANCZOS)
    controls = preset or ProcessingPreset()
    adjusted = apply_adjustments(np.asarray(fitted, dtype=np.uint8), controls)
    if rotation == "clockwise":
        native_transform = Image.Transpose.ROTATE_270
        preview_inverse = Image.Transpose.ROTATE_90
    elif rotation == "counterclockwise":
        native_transform = Image.Transpose.ROTATE_90
        preview_inverse = Image.Transpose.ROTATE_270
    elif rotation == "transverse":
        native_transform = Image.Transpose.TRANSVERSE
        preview_inverse = Image.Transpose.TRANSVERSE
    else:
        raise ValueError("rotation must be clockwise, counterclockwise or transverse")
    if dithering is DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1:
        landscape_indices = quantize(
            adjusted,
            dithering,
            strength=controls.dithering_strength,
        )
        preview = Image.fromarray(SPECTRA6_RGB[landscape_indices])
        native_indices = np.asarray(
            Image.fromarray(landscape_indices).transpose(native_transform),
            dtype=np.uint8,
        )
        if native_indices.shape != (1600, 1200):
            raise ValueError("native panel geometry must be 1200x1600")
        packed = pack_vendor_rows(native_indices, palette_codes=palette_codes)
        return ConversionResult(preview=preview, packed=packed)
    adjusted_image = Image.fromarray(adjusted)
    native = adjusted_image.transpose(native_transform)
    if native.size != (1200, 1600):
        raise ValueError("native panel geometry must be 1200x1600")
    indices = quantize(
        np.asarray(native, dtype=np.uint8),
        dithering,
        strength=controls.dithering_strength,
    )
    native_preview = Image.fromarray(SPECTRA6_RGB[indices])
    preview = native_preview.transpose(preview_inverse)
    packed = pack_vendor_rows(indices, palette_codes=palette_codes)
    return ConversionResult(preview=preview, packed=packed)
