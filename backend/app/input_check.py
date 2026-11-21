"""A rough check that an upload looks like a brain MRI scan at all (warning not_mri_like).

The model gives a confident answer to anything, noise or a photo included. Four simple
features catch the crude cases: colour, a nearly uniform frame, noise, and a dull image
without the dark background every scan has. It is not an out-of-distribution detector:
a grey image that is not a brain MRI (an X-ray, a CT slice, a grey photo) passes.

The thresholds were chosen on the 7200 scans of the Kaggle dataset (none flagged) and
16 synthetic and everyday images (all flagged); see the README.

The features are computed on the decoded RGB image at its own resolution. To keep
4096x4096 uploads cheap, the image is read in bands of rows with Pillow's C operations,
and every sum is taken from a 256-bin histogram: the integer sums are exact and no
whole-frame copy is made.
"""

import math
from dataclasses import dataclass

from PIL import Image, ImageChops

# A "dark" pixel has a brightness (PIL "L", 0..255) below 10 % of the scale, i.e. 0..25.
DARK_LEVEL = 0.10 * 255
_DARK_VALUES = math.ceil(DARK_LEVEL)
ROWS_PER_BAND = 512


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
    # Share of fully opaque pixels (alpha 255), from the decoder: the RGB image has no
    # alpha left. Transparent pixels turn black, a fake dark background. 1.0 without alpha.
    opaque_share: float = 1.0


@dataclass(frozen=True)
class NotMriThresholds:
    """NOT_MRI_* settings. Every comparison is strict: a value equal to a threshold passes."""

    color_max: float = 7.0
    noise_max: float = 0.85
    std_min: float = 10.0
    lowcontrast_std: float = 30.0
    dark_min: float = 0.15
    opaque_min: float = 0.99


def _total(histogram: list[int]) -> int:
    """Sum of the pixel values of an "L" image, from its 256-bin histogram."""
    return sum(value * count for value, count in enumerate(histogram))


def _squares(histogram: list[int]) -> int:
    return sum(value * value * count for value, count in enumerate(histogram))


def _difference_sum(first: Image.Image, second: Image.Image) -> int:
    """Sum of |first - second| over the pixels of two "L" images of the same size."""
    return _total(ImageChops.difference(first, second).histogram())


def compute_features(
    image: Image.Image, rows_per_band: int = ROWS_PER_BAND, *, grey: bool = False
) -> ImageFeatures:
    """grey=True promises that every pixel has R == G == B (the file was greyscale): the
    colour pass, more than half of the work, is skipped and colour is 0."""
    width, height = image.size
    pixels = width * height
    color_sum = brightness_sum = brightness_squares = neighbour_diffs = dark = 0
    previous_row: Image.Image | None = None

    for top in range(0, height, rows_per_band):
        rows = min(rows_per_band, height - top)
        band = image.crop((0, top, width, top + rows))
        if band.mode != "RGB":
            band = band.convert("RGB")
        if not grey:
            # max(|R-G|, |G-B|, |R-B|) is the largest channel minus the smallest.
            red, green, blue = band.split()
            largest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            smallest = ImageChops.darker(ImageChops.darker(red, green), blue)
            color_sum += _total(ImageChops.subtract(largest, smallest).histogram())

        gray = band.convert("L")
        histogram = gray.histogram()
        brightness_sum += _total(histogram)
        brightness_squares += _squares(histogram)
        dark += sum(histogram[:_DARK_VALUES])

        if width > 1:
            neighbour_diffs += _difference_sum(
                gray.crop((1, 0, width, rows)), gray.crop((0, 0, width - 1, rows))
            )
        if rows > 1:
            neighbour_diffs += _difference_sum(
                gray.crop((0, 1, width, rows)), gray.crop((0, 0, width, rows - 1))
            )
        if previous_row is not None:
            neighbour_diffs += _difference_sum(gray.crop((0, 0, width, 1)), previous_row)
        previous_row = gray.crop((0, rows - 1, width, rows))

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
    low contrast without the dark background of a scan, or noticeable transparency."""
    return (
        features.opaque_share < thresholds.opaque_min
        or features.color > thresholds.color_max
        or (features.noise is not None and features.noise > thresholds.noise_max)
        or features.std < thresholds.std_min
        or (features.std < thresholds.lowcontrast_std and features.dark_share < thresholds.dark_min)
    )
