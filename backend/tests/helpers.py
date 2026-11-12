"""Synthetic images and history records for tests, so no real scans are needed."""

import io

import numpy as np
from PIL import Image

from app.records import PredictionRecord


def make_image(width: int = 512, height: int = 512, mode: str = "L") -> Image.Image:
    """Scan-like picture: a bright blob on a dark, but not fully black, background."""
    ys, xs = np.mgrid[0:height, 0:width]
    blob = np.exp(
        -(((xs - width * 0.4) / (width * 0.25)) ** 2 + ((ys - height * 0.55) / (height * 0.3)) ** 2)
    )
    pixels = (20 + 200 * blob).astype(np.uint8)
    return Image.fromarray(pixels).convert(mode)


def encode_image(image: Image.Image, fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def image_bytes(width: int = 512, height: int = 512, fmt: str = "PNG", mode: str = "L") -> bytes:
    return encode_image(make_image(width, height, mode), fmt)


def make_record(**fields) -> PredictionRecord:
    """A history record with plausible values; any column can be overridden."""
    values = {
        "predicted_class": "meningioma",
        "probabilities": {"glioma": 0.05, "meningioma": 0.8, "notumor": 0.12, "pituitary": 0.03},
        "image_width": 512,
        "image_height": 512,
        "warnings": [],
        "image_sha256": "0" * 64,
        "gradcam_requested": False,
        "latency_ms": 12.5,
    }
    values.update(fields)
    return PredictionRecord(**values)
