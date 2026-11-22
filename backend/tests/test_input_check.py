"""Features and rule of the not_mri_like warning, on synthetic images only."""

import math
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from app.input_check import ImageFeatures, NotMriThresholds, compute_features, looks_not_mri
from tests.helpers import make_image

RNG_SEED = 0
DEFAULTS = NotMriThresholds()


def rgb(pixels: np.ndarray) -> Image.Image:
    """An RGB image from an (h, w) brightness array or an (h, w, 3) colour array."""
    image = Image.fromarray(np.asarray(pixels, dtype=np.uint8))
    return image.convert("RGB")


def solid(value: int, size: int = 64) -> Image.Image:
    return Image.new("RGB", (size, size), (value, value, value))


def uniform_noise(size: int = 256) -> Image.Image:
    return rgb(np.random.default_rng(RNG_SEED).integers(0, 256, (size, size)))


def gaussian_noise(size: int = 256) -> Image.Image:
    noise = np.random.default_rng(RNG_SEED).normal(128, 50, (size, size))
    return rgb(np.clip(noise, 0, 255))


def tinted_scan() -> Image.Image:
    """The scan-like test image in orange: colour is the only thing wrong with it."""
    scan = np.asarray(make_image(256, 256), dtype=np.float64)
    return rgb(np.dstack([scan, scan * 0.6, scan * 0.3]))


def reference_features(image: Image.Image) -> ImageFeatures:
    """The definitions of the measurement, on whole-frame float64 arrays."""
    pixels = np.asarray(image.convert("RGB"), dtype=np.int16)
    r, g, b = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    color = np.maximum(np.maximum(np.abs(r - g), np.abs(g - b)), np.abs(r - b)).mean()
    gray = np.asarray(image.convert("L"), dtype=np.float64)
    std = gray.std()
    dx, dy = np.abs(np.diff(gray, axis=1)), np.abs(np.diff(gray, axis=0))
    noise = (dx.sum() + dy.sum()) / (dx.size + dy.size) / std if std > 0 else None
    return ImageFeatures(
        color=float(color),
        std=float(std),
        noise=None if noise is None else float(noise),
        dark_share=float((gray < 25.5).mean()),
    )


def assert_same_features(actual: ImageFeatures, expected: ImageFeatures) -> None:
    assert actual.color == pytest.approx(expected.color, abs=1e-9)
    assert actual.std == pytest.approx(expected.std, abs=1e-9)
    assert actual.dark_share == pytest.approx(expected.dark_share, abs=1e-12)
    if expected.noise is None:
        assert actual.noise is None
    else:
        assert actual.noise == pytest.approx(expected.noise, rel=1e-9)


# --- Features ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "dark_share"), [(0, 1.0), (255, 0.0), (128, 0.0)], ids=["black", "white", "grey"]
)
def test_solid_frame_has_no_spread_and_no_noise(value, dark_share):
    features = compute_features(solid(value))

    assert features == ImageFeatures(color=0.0, std=0.0, noise=None, dark_share=dark_share)


def test_one_pixel_image_does_not_divide_by_zero():
    assert compute_features(solid(7, size=1)).noise is None


@pytest.mark.parametrize("image", [uniform_noise(), gaussian_noise()], ids=["uniform", "gaussian"])
def test_noise_has_large_neighbour_differences(image):
    features = compute_features(image)

    # Independent pixels: E|X - Y| / std is about 1.13 to 1.15.
    assert 1.0 < features.noise < 1.3
    assert features.color == 0.0


def test_neighbour_difference_is_normalised_by_the_spread():
    # Columns alternate 0 and 200: every horizontal pair differs by 200, vertical ones by 0.
    stripes = np.tile([0, 200], (4, 3))  # 4 rows, 6 columns
    features = compute_features(rgb(stripes))

    pairs = 4 * 5 + 3 * 6
    assert features.std == pytest.approx(100.0)
    assert features.noise == pytest.approx((200 * 4 * 5 / pairs) / 100.0)
    assert features.dark_share == pytest.approx(0.5)


def test_colour_is_the_mean_largest_channel_difference():
    pixels = np.zeros((2, 2, 3), dtype=np.uint8)
    pixels[0, 0] = (200, 30, 30)  # 170
    pixels[0, 1] = (10, 10, 50)  # 40
    pixels[1, 0] = (90, 90, 90)  # 0 (grey)
    pixels[1, 1] = (0, 255, 128)  # 255

    assert compute_features(Image.fromarray(pixels)).color == pytest.approx((170 + 40 + 255) / 4)


def test_grey_image_given_in_mode_l_is_read_as_its_rgb_version():
    scan = make_image(128, 96)

    assert compute_features(scan) == compute_features(scan.convert("RGB"))


@pytest.mark.parametrize(
    "image",
    [
        make_image(300, 200, "RGB"),
        tinted_scan(),
        uniform_noise(97),
        rgb(np.random.default_rng(RNG_SEED).integers(0, 256, (37, 23, 3))),
        solid(90, size=5),
        rgb(np.arange(1, 11).reshape(1, 10) * 20),  # a single row
        rgb(np.arange(1, 11).reshape(10, 1) * 20),  # a single column
    ],
    ids=["scan", "tinted", "noise", "colour-noise", "solid", "one-row", "one-column"],
)
@pytest.mark.parametrize("rows_per_band", [1, 2, 7, 256])
def test_bands_give_the_same_result_as_the_whole_frame(image, rows_per_band):
    actual = compute_features(image, rows_per_band=rows_per_band)

    assert_same_features(actual, reference_features(image))


@pytest.mark.parametrize(
    "image",
    [
        make_image(300, 200, "RGB"),
        uniform_noise(97),
        gaussian_noise(),
        solid(0),
        solid(255),
        rgb(np.arange(1, 11).reshape(1, 10) * 20),
    ],
    ids=["scan", "uniform-noise", "gaussian-noise", "black", "white", "one-row"],
)
def test_grey_shortcut_gives_the_full_result(image):
    """For a grey source the colour pass is skipped; everything else is the same."""
    assert compute_features(image, grey=True) == compute_features(image)


def test_colour_images_are_measured_in_full_by_default():
    image = tinted_scan()

    assert compute_features(image).color > DEFAULTS.color_max
    assert_same_features(compute_features(image), reference_features(image))


# --- Rule ----------------------------------------------------------------------

# Values a real scan typically has (see the README): nothing is near a threshold.
SCAN_LIKE = ImageFeatures(color=0.0, std=45.0, noise=0.08, dark_share=0.5)


def features(**changes) -> ImageFeatures:
    return replace(SCAN_LIKE, **changes)


@pytest.mark.parametrize(
    ("changes", "flagged"),
    [
        ({}, False),
        ({"color": 7.0}, False),
        ({"color": 7.001}, True),
        ({"noise": 0.85}, False),
        ({"noise": 0.8501}, True),
        ({"std": 10.0}, False),
        ({"std": 9.999}, True),
        # Low contrast counts only together with few dark pixels.
        ({"std": 30.0, "dark_share": 0.1}, False),
        ({"std": 29.999, "dark_share": 0.1}, True),
        ({"std": 29.999, "dark_share": 0.15}, False),
        ({"std": 29.999, "dark_share": 0.1499}, True),
        ({"std": 12.0, "dark_share": 0.6}, False),
        ({"std": 0.0, "noise": None, "dark_share": 1.0}, True),
        ({"opaque_share": 0.99}, False),
        ({"opaque_share": 0.9899}, True),
    ],
    ids=[
        "scan-like",
        "colour-at-threshold",
        "colour-above",
        "noise-at-threshold",
        "noise-above",
        "std-at-threshold",
        "std-below",
        "low-contrast-std-at-threshold",
        "low-contrast-std-below",
        "low-contrast-dark-at-threshold",
        "low-contrast-dark-below",
        "low-contrast-but-dark-background",
        "solid-frame-without-noise",
        "opaque-at-threshold",
        "opaque-below",
    ],
)
def test_thresholds_are_strict(changes, flagged):
    assert looks_not_mri(features(**changes), DEFAULTS) is flagged


def test_default_thresholds():
    assert DEFAULTS == NotMriThresholds(
        color_max=7.0,
        noise_max=0.85,
        std_min=10.0,
        lowcontrast_std=30.0,
        dark_min=0.15,
        opaque_min=0.99,
    )


def test_other_thresholds_are_used():
    lenient = NotMriThresholds(
        color_max=255, noise_max=math.inf, std_min=0, lowcontrast_std=0, dark_min=0, opaque_min=0
    )

    assert not looks_not_mri(features(color=200.0, noise=1.2), lenient)
    assert not looks_not_mri(compute_features(solid(0)), lenient)


@pytest.mark.parametrize(
    "image",
    [solid(0), solid(255), solid(128), uniform_noise(), gaussian_noise(), tinted_scan()],
    ids=["black", "white", "grey", "uniform-noise", "gaussian-noise", "colour"],
)
def test_synthetic_non_scans_are_flagged(image):
    assert looks_not_mri(compute_features(image), DEFAULTS)


@pytest.mark.parametrize("size", [(512, 512), (640, 480), (225, 225)])
def test_scan_like_image_is_not_flagged(size):
    features = compute_features(make_image(*size, "RGB"))

    assert not looks_not_mri(features, DEFAULTS)
