"""A rough check that an upload looks like a brain MRI scan at all (warning not_mri_like).

The model gives a confident answer to anything, noise or a photo included. Four simple
features catch the crude cases: colour, a nearly uniform frame, noise, and a dull image
without the dark background every scan has. It is not an out-of-distribution detector:
a grey image that is not a brain MRI (an X-ray, a CT slice, a grey photo) passes.

The thresholds were chosen on the 7200 scans of the Kaggle dataset (none flagged) and
16 synthetic and everyday images (all flagged); see the README.

The features are computed on the decoded RGB image at its own resolution. To keep
4096x4096 uploads cheap, the image is read in bands of rows and only integer sums are
kept, so the result is exact and no whole-frame array is ever made.
"""

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

# A "dark" pixel has a brightness (PIL "L", 0..255) below 10 % of the scale.
DARK_LEVEL = 0.10 * 255
ROWS_PER_BAND = 256


@dataclass(frozen=True)
class ImageFeatures:
    # Mean over pixels of max(|R-G|, |G-B|, |R-B|), 0..255. 0 for a grey image.
    color: float
    # Standard deviation of the brightness, 0..255.
    std: float
    # Mean absolute difference of horizontal and vertical neighbours, divided by std.
    # About 1.1 for pixel noise, below 0.7 for scans. None when std is 0 (a uniform frame).
    noise: float | None
    # Share of pixels darker than DARK_LEVEL, 0..1.
    dark_share: float


@dataclass(frozen=True)
class NotMriThresholds:
    """NOT_MRI_* settings. Every comparison is strict: a value equal to a threshold passes."""

    color_max: float = 7.0
    noise_max: float = 0.85
    std_min: float = 10.0
    lowcontrast_std: float = 30.0
    dark_min: float = 0.15


def compute_features(image: Image.Image, rows_per_band: int = ROWS_PER_BAND) -> ImageFeatures:
    width, height = image.size
    pixels = width * height
    color_sum = brightness_sum = brightness_squares = neighbour_diffs = dark = 0
    previous_row: np.ndarray | None = None

    for top in range(0, height, rows_per_band):
        band = image.crop((0, top, width, min(top + rows_per_band, height)))
        if band.mode != "RGB":
            band = band.convert("RGB")
        rgb = np.asarray(band)
        # max(|R-G|, |G-B|, |R-B|) is the largest channel minus the smallest.
        color_sum += int((rgb.max(axis=2) - rgb.min(axis=2)).sum(dtype=np.int64))

        gray = np.asarray(band.convert("L"))
        values = gray.astype(np.int32)
        brightness_sum += int(values.sum(dtype=np.int64))
        brightness_squares += int((values * values).sum(dtype=np.int64))
        dark += int(np.count_nonzero(gray < DARK_LEVEL))

        neighbour_diffs += int(np.abs(np.diff(values, axis=1)).sum(dtype=np.int64))
        neighbour_diffs += int(np.abs(np.diff(values, axis=0)).sum(dtype=np.int64))
        if previous_row is not None:
            neighbour_diffs += int(np.abs(values[0] - previous_row).sum(dtype=np.int64))
        previous_row = values[-1]

    # Exact integer variance: (n * sum(x^2) - sum(x)^2) / n^2.
    variance = (pixels * brightness_squares - brightness_sum**2) / pixels**2
    std = math.sqrt(variance)
    noise = None
    if std > 0:
        # std > 0 needs at least two pixels, so there is at least one neighbour pair.
        pairs = height * (width - 1) + (height - 1) * width
        noise = neighbour_diffs / pairs / std
    return ImageFeatures(
        color=color_sum / pixels,
        std=std,
        noise=noise,
        dark_share=dark / pixels,
    )


def looks_not_mri(features: ImageFeatures, thresholds: NotMriThresholds) -> bool:
    """True when at least one condition holds: colour, noise, a nearly uniform frame,
    or low contrast without the dark background of a scan."""
    return (
        features.color > thresholds.color_max
        or (features.noise is not None and features.noise > thresholds.noise_max)
        or features.std < thresholds.std_min
        or (features.std < thresholds.lowcontrast_std and features.dark_share < thresholds.dark_min)
    )
