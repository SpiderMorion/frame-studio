from __future__ import annotations

from time import perf_counter

import numpy as np
import pytest
from PIL import Image

from eink_frame.converter import (
    SPECTRA6_RGB,
    DitheringAlgorithm,
    ProcessingPreset,
    apply_adjustments,
    convert_image,
    convert_panel_image,
    pack_indices,
    pack_vendor_rows,
    quantize,
    quantize_nearest,
)


def test_nearest_quantization_preserves_exact_nominal_palette_colors() -> None:
    pixels = np.array(
        [
            [
                [0, 0, 0],
                [255, 255, 255],
                [255, 255, 0],
                [255, 0, 0],
                [0, 0, 255],
                [0, 255, 0],
            ]
        ],
        dtype=np.uint8,
    )

    indices = quantize_nearest(pixels)

    assert indices.dtype == np.uint8
    assert indices.tolist() == [[0, 1, 2, 3, 4, 5]]
    assert np.array_equal(SPECTRA6_RGB[indices], pixels)


def test_pack_indices_encodes_two_pixels_per_byte_high_nibble_first() -> None:
    indices = np.array([[0, 1, 2, 3, 4, 5]], dtype=np.uint8)
    palette_codes = (0x0, 0x1, 0x2, 0x3, 0x5, 0x6)

    packed = pack_indices(indices, palette_codes=palette_codes)

    assert packed == bytes((0x01, 0x23, 0x56))


def test_vendor_row_packing_reverses_two_pixel_pairs() -> None:
    indices = np.array([[0, 1, 2, 3, 4, 5]], dtype=np.uint8)

    packed = pack_vendor_rows(indices, palette_codes=(0x0, 0x1, 0x2, 0x3, 0x5, 0x6))

    assert packed == bytes((0x56, 0x23, 0x01))


def test_pack_indices_supports_low_nibble_first_hardware_profile() -> None:
    indices = np.array([[0, 1, 2, 3]], dtype=np.uint8)

    packed = pack_indices(
        indices,
        palette_codes=(0x0, 0x1, 0x2, 0x3, 0x5, 0x6),
        first_pixel_high_nibble=False,
    )

    assert packed == bytes((0x10, 0x32))


def test_pack_indices_rejects_logical_index_outside_palette() -> None:
    indices = np.array([[0, 6]], dtype=np.uint8)

    with pytest.raises(ValueError, match="logical palette index"):
        pack_indices(indices, palette_codes=(0, 1, 2, 3, 5, 6))


@pytest.mark.parametrize(
    "palette_codes",
    [
        (0, 1, 2, 3, 5, 5),
        (0, 1, 2, 3, 5, 16),
    ],
)
def test_pack_indices_rejects_invalid_hardware_mapping(
    palette_codes: tuple[int, int, int, int, int, int],
) -> None:
    indices = np.array([[0, 1]], dtype=np.uint8)

    with pytest.raises(ValueError, match="hardware palette mapping"):
        pack_indices(indices, palette_codes=palette_codes)


def test_convert_image_produces_full_size_preview_and_960000_byte_frame() -> None:
    source = Image.new("RGB", (800, 800), (255, 0, 0))

    result = convert_image(
        source,
        width=1600,
        height=1200,
        palette_codes=(0x0, 0x1, 0x2, 0x3, 0x5, 0x6),
    )

    assert result.preview.size == (1600, 1200)
    assert result.preview.getpixel((0, 0)) == (255, 0, 0)
    assert len(result.packed) == 960_000
    assert result.packed[:4] == bytes((0x33, 0x33, 0x33, 0x33))


def test_panel_conversion_keeps_landscape_preview_and_emits_native_frame() -> None:
    source = Image.new("RGB", (1600, 1200), (255, 0, 0))

    result = convert_panel_image(
        source,
        palette_codes=(0x0, 0x1, 0x2, 0x3, 0x5, 0x6),
        rotation="clockwise",
    )

    assert result.preview.size == (1600, 1200)
    assert result.preview.getpixel((0, 0)) == (255, 0, 0)
    assert len(result.packed) == 960_000
    assert result.packed[:4] == bytes((0x33, 0x33, 0x33, 0x33))


def test_transverse_panel_transform_matches_observed_physical_orientation() -> None:
    source = Image.new("RGB", (1600, 1200))
    source.paste((255, 0, 0), (0, 0, 800, 600))
    source.paste((0, 255, 0), (800, 0, 1600, 600))
    source.paste((0, 0, 255), (0, 600, 800, 1200))
    source.paste((255, 255, 0), (800, 600, 1600, 1200))

    result = convert_panel_image(
        source,
        palette_codes=(0, 1, 2, 3, 5, 6),
        rotation="transverse",
    )
    rows = np.frombuffer(result.packed, dtype=np.uint8).reshape(1600, 600)[:, ::-1]

    assert rows[0, 0] >> 4 == 2
    assert rows[0, -1] & 0x0F == 6
    assert rows[-1, 0] >> 4 == 5
    assert rows[-1, -1] & 0x0F == 3


def test_good_display_panel_conversion_dithers_before_native_transform() -> None:
    x = np.arange(1600, dtype=np.uint16)
    y = np.arange(1200, dtype=np.uint16)[:, None]
    pixels = np.empty((1200, 1600, 3), dtype=np.uint8)
    pixels[..., 0] = (x + y) % 256
    pixels[..., 1] = (2 * x + y) % 256
    pixels[..., 2] = (x + 2 * y) % 256
    source = Image.fromarray(pixels)
    expected_indices = quantize(pixels, DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1)
    expected_native = np.asarray(
        Image.fromarray(expected_indices).transpose(Image.Transpose.TRANSVERSE),
        dtype=np.uint8,
    )

    result = convert_panel_image(
        source,
        palette_codes=(0, 1, 2, 3, 5, 6),
        dithering=DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1,
        rotation="transverse",
    )

    assert np.array_equal(np.asarray(result.preview), SPECTRA6_RGB[expected_indices])
    assert result.packed == pack_vendor_rows(
        expected_native,
        palette_codes=(0, 1, 2, 3, 5, 6),
    )


def test_processing_preset_is_immutable() -> None:
    preset = ProcessingPreset()

    with pytest.raises(AttributeError):
        preset.brightness = 10.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("brightness", -100.1),
        ("contrast", 100.1),
        ("saturation", -101.0),
        ("gamma", 0.0),
        ("gamma", 5.1),
        ("sharpness", 101.0),
        ("dithering_strength", -0.01),
        ("dithering_strength", 1.01),
    ],
)
def test_processing_preset_rejects_controls_outside_supported_ranges(
    field: str, value: float
) -> None:
    with pytest.raises(ValueError, match=field):
        ProcessingPreset(**{field: value})  # type: ignore[arg-type]


def test_adjustments_apply_brightness_without_mutating_input() -> None:
    pixels = np.array([[[128, 64, 32]]], dtype=np.uint8)
    original = pixels.copy()

    adjusted = apply_adjustments(pixels, ProcessingPreset(brightness=100.0))

    assert adjusted.tolist() == [[[255, 128, 64]]]
    assert np.array_equal(pixels, original)


def test_adjustments_use_brightness_contrast_saturation_then_gamma_order() -> None:
    pixels = np.array([[[40, 100, 200], [200, 80, 30]]], dtype=np.uint8)
    preset = ProcessingPreset(brightness=25.0, contrast=50.0, saturation=-50.0, gamma=2.0)

    adjusted = apply_adjustments(pixels, preset)

    assert adjusted.tolist() == [[[122, 170, 214], [221, 164, 127]]]


def test_adjustments_apply_sharpness_last() -> None:
    values = np.array([[50, 50, 50], [50, 100, 50], [50, 50, 50]], dtype=np.uint8)
    pixels = np.repeat(values[..., None], 3, axis=2)

    adjusted = apply_adjustments(pixels, ProcessingPreset(sharpness=100.0))

    assert adjusted[1, 1].tolist() == [131, 131, 131]


def test_no_dither_matches_nearest_and_does_not_mutate_input() -> None:
    pixels = np.array([[[10, 20, 30], [240, 230, 220]]], dtype=np.uint8)
    original = pixels.copy()

    indices = quantize(pixels, DitheringAlgorithm.NONE)

    assert np.array_equal(indices, quantize_nearest(pixels))
    assert np.array_equal(pixels, original)


def test_floyd_steinberg_diffuses_quantization_error() -> None:
    pixels = np.full((4, 5, 3), 128, dtype=np.uint8)

    indices = quantize(pixels, DitheringAlgorithm.FLOYD_STEINBERG)

    assert indices.tolist() == [
        [1, 0, 1, 0, 1],
        [0, 1, 0, 1, 0],
        [1, 0, 1, 0, 1],
        [0, 1, 0, 1, 0],
    ]


def test_good_display_floyd_v1_matches_uint8_clamped_reference() -> None:
    pixels = np.array(
        [
            [[248, 194, 190], [207, 147, 26], [237, 21, 245], [211, 239, 45], [5, 157, 159]],
            [[60, 54, 236], [109, 46, 199], [82, 32, 205], [36, 7, 134], [222, 57, 146]],
            [[8, 149, 14], [15, 22, 10], [144, 208, 24], [24, 7, 41], [11, 85, 59]],
            [[104, 225, 110], [226, 201, 8], [159, 26, 27], [163, 122, 33], [41, 201, 67]],
        ],
        dtype=np.uint8,
    )

    indices = quantize(pixels, DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1)

    assert indices.tolist() == [
        [1, 3, 1, 2, 4],
        [4, 4, 4, 0, 1],
        [5, 0, 2, 0, 0],
        [2, 2, 3, 2, 5],
    ]


@pytest.mark.parametrize(
    "pixels",
    [
        np.zeros((2, 2), dtype=np.uint8),
        np.zeros((2, 2, 1), dtype=np.uint8),
        np.zeros((2, 2, 4), dtype=np.uint8),
    ],
)
def test_good_display_floyd_v1_rejects_non_rgb_arrays(pixels: np.ndarray) -> None:
    with pytest.raises(ValueError, match="expected an H×W×3 RGB array"):
        quantize(pixels, DitheringAlgorithm.GOOD_DISPLAY_FLOYD_V1)


def test_error_diffusion_is_fast_enough_for_interactive_preview() -> None:
    quantize(np.zeros((2, 2, 3), dtype=np.uint8), DitheringAlgorithm.JARVIS_JUDICE_NINKE)
    pixels = np.random.default_rng(1).integers(0, 256, (240, 320, 3), dtype=np.uint8)

    started = perf_counter()
    indices = quantize(pixels, DitheringAlgorithm.JARVIS_JUDICE_NINKE)
    elapsed = perf_counter() - started

    assert indices.shape == (240, 320)
    assert elapsed < 1.0, f"interactive preview kernel took {elapsed:.3f}s"


def test_atkinson_diffuses_three_quarters_of_quantization_error() -> None:
    pixels = np.full((4, 5, 3), 128, dtype=np.uint8)

    indices = quantize(pixels, DitheringAlgorithm.ATKINSON)

    assert indices.tolist() == [
        [1, 0, 0, 1, 1],
        [0, 1, 1, 0, 0],
        [0, 1, 1, 0, 1],
        [1, 0, 0, 1, 1],
    ]


def test_sierra_lite_uses_its_three_neighbor_kernel() -> None:
    values = np.array(
        [
            [30, 60, 90, 120, 150],
            [45, 75, 105, 135, 165],
            [60, 90, 120, 150, 180],
            [75, 105, 135, 165, 195],
        ],
        dtype=np.uint8,
    )
    pixels = np.repeat(values[..., None], 3, axis=2)

    indices = quantize(pixels, DitheringAlgorithm.SIERRA_LITE)

    assert indices.tolist() == [
        [0, 0, 0, 1, 0],
        [0, 1, 0, 1, 1],
        [0, 0, 1, 0, 1],
        [0, 1, 0, 1, 1],
    ]


def test_jarvis_judice_ninke_uses_two_row_kernel() -> None:
    values = np.array(
        [
            [30, 60, 90, 120, 150],
            [45, 75, 105, 135, 165],
            [60, 90, 120, 150, 180],
            [75, 105, 135, 165, 195],
        ],
        dtype=np.uint8,
    )
    pixels = np.repeat(values[..., None], 3, axis=2)

    indices = quantize(pixels, DitheringAlgorithm.JARVIS_JUDICE_NINKE)

    assert indices.tolist() == [
        [0, 0, 0, 1, 1],
        [0, 0, 1, 0, 1],
        [0, 1, 0, 1, 1],
        [0, 0, 1, 1, 1],
    ]


def test_bayer_4x4_applies_periodic_ordered_thresholds() -> None:
    pixels = np.full((4, 4, 3), 128, dtype=np.uint8)

    indices = quantize(pixels, DitheringAlgorithm.BAYER_4X4)

    assert indices.tolist() == [
        [0, 1, 0, 1],
        [1, 0, 1, 0],
        [0, 1, 0, 1],
        [1, 0, 1, 0],
    ]


def test_bayer_8x8_applies_full_threshold_matrix() -> None:
    pixels = np.full((8, 8, 3), 96, dtype=np.uint8)

    indices = quantize(pixels, DitheringAlgorithm.BAYER_8X8)

    assert indices.tolist() == [
        [0, 0, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1, 0, 1, 0],
        [0, 1, 0, 0, 0, 1, 0, 0],
        [1, 0, 1, 0, 1, 0, 1, 0],
        [0, 0, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1, 0, 1, 0],
        [0, 1, 0, 0, 0, 1, 0, 0],
        [1, 0, 1, 0, 1, 0, 1, 0],
    ]


@pytest.mark.parametrize("algorithm", list(DitheringAlgorithm))
def test_zero_dithering_strength_reduces_every_algorithm_to_nearest(
    algorithm: DitheringAlgorithm,
) -> None:
    pixels = np.full((4, 5, 3), 128, dtype=np.uint8)

    indices = quantize(pixels, algorithm, strength=0.0)

    assert np.array_equal(indices, quantize_nearest(pixels))


def test_dithering_strength_scales_propagated_error() -> None:
    pixels = np.full((1, 8, 3), 100, dtype=np.uint8)

    indices = quantize(pixels, DitheringAlgorithm.FLOYD_STEINBERG, strength=0.5)

    assert indices.tolist() == [[0, 0, 0, 1, 0, 0, 0, 0]]


def test_dithering_strength_scales_ordered_thresholds() -> None:
    pixels = np.full((4, 4, 3), 100, dtype=np.uint8)

    indices = quantize(pixels, DitheringAlgorithm.BAYER_4X4, strength=0.5)

    assert indices.tolist() == [
        [0, 0, 0, 0],
        [1, 0, 1, 0],
        [0, 1, 0, 0],
        [1, 0, 1, 0],
    ]


@pytest.mark.parametrize("strength", [-0.01, 1.01, float("nan")])
def test_quantize_rejects_invalid_dithering_strength(strength: float) -> None:
    pixels = np.zeros((1, 1, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="strength"):
        quantize(pixels, DitheringAlgorithm.FLOYD_STEINBERG, strength=strength)


def test_convert_image_adjusts_before_quantizing_to_palette_indices() -> None:
    source = Image.new("RGB", (2, 1), (100, 100, 100))

    result = convert_image(
        source,
        width=2,
        height=1,
        palette_codes=(0, 1, 2, 3, 4, 5),
        preset=ProcessingPreset(brightness=100.0),
        dithering=DitheringAlgorithm.NONE,
    )

    assert list(result.preview.getdata()) == [(255, 255, 255), (255, 255, 255)]
    assert result.packed == bytes([0x11])


@pytest.mark.parametrize("algorithm", list(DitheringAlgorithm))
def test_quantization_is_repeatable_palette_indices_and_leaves_input_unchanged(
    algorithm: DitheringAlgorithm,
) -> None:
    pixels = np.array(
        [
            [[17, 91, 203], [220, 44, 81], [113, 172, 59]],
            [[199, 201, 17], [73, 80, 88], [149, 21, 233]],
        ],
        dtype=np.uint8,
    )
    original = pixels.copy()

    first = quantize(pixels, algorithm, strength=0.75)
    second = quantize(pixels, algorithm, strength=0.75)

    assert first.dtype == np.uint8
    assert np.array_equal(first, second)
    assert np.all(first < len(SPECTRA6_RGB))
    assert np.array_equal(pixels, original)
