import io

import numpy as np
import pytest
import torch
from PIL import Image, ImageOps
from torchvision import transforms as T

from app.preprocessing import (
    IMAGENET_MEAN,
    IMAGENET_STD,
    ImageTooLargeError,
    NotAnImageError,
    PadToSquare,
    UnsupportedFormatError,
    build_transform,
    decode_image,
    square_padding,
)
from tests.helpers import encode_image, image_bytes, make_image

MAX_PIXELS = 1_000_000

# Square 512, square of another size, landscape, portrait with odd padding, very narrow.
SIZES = [(512, 512), (630, 630), (640, 480), (213, 236), (201, 512)]


class NotebookPadToSquare:
    """Verbatim copy of PadToSquare from the training notebook."""

    def __call__(self, img):
        w, h = img.size
        s = max(w, h)
        canvas = Image.new("RGB", (s, s), (0, 0, 0))
        canvas.paste(img, ((s - w) // 2, (s - h) // 2))
        return canvas


def notebook_eval_tf2():
    """Verbatim copy of eval_tf2 from the training notebook (IMG_SIZE = 224)."""
    mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
    return T.Compose(
        [
            NotebookPadToSquare(),
            T.RandomAutocontrast(p=1.0),
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize(mean, std),
        ]
    )


# --- PadToSquare ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((512, 512), (0, 0)),
        ((640, 480), (0, 80)),
        ((480, 640), (80, 0)),
        ((5, 2), (0, 1)),  # odd difference: the extra row goes to the bottom
    ],
)
def test_square_padding(size, expected):
    assert square_padding(*size) == expected


def test_pad_to_square_centres_image_on_black():
    white = Image.new("RGB", (300, 200), (255, 255, 255))

    padded = np.asarray(PadToSquare()(white))

    assert padded.shape == (300, 300, 3)
    assert (padded[:50] == 0).all()
    assert (padded[50:250] == 255).all()
    assert (padded[250:] == 0).all()


def test_pad_to_square_keeps_square_image_unchanged():
    image = make_image(64, 64, "RGB")

    padded = PadToSquare()(image)

    assert np.array_equal(np.asarray(padded), np.asarray(image))


# --- Full transform ------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_transform_output_is_normalised_224_tensor(size):
    tensor = build_transform()(make_image(*size, "RGB"))

    assert tensor.shape == (3, 224, 224)
    assert tensor.dtype == torch.float32


@pytest.mark.parametrize("size", SIZES)
def test_transform_is_identical_to_training_notebook(size):
    image = make_image(*size, "RGB")

    assert torch.equal(build_transform()(image), notebook_eval_tf2()(image))


@pytest.mark.parametrize("size", SIZES)
def test_transform_matches_step_by_step_reference(size):
    """Each step is redone with plain Pillow and numpy, in the required order."""
    image = make_image(*size, "RGB")
    width, height = image.size
    side = max(width, height)

    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(image, ((side - width) // 2, (side - height) // 2))
    stretched = ImageOps.autocontrast(canvas)
    resized = stretched.resize((224, 224), Image.Resampling.BILINEAR)
    pixels = np.asarray(resized, dtype=np.float32) / 255.0
    expected = (pixels - np.array(IMAGENET_MEAN, dtype=np.float32)) / np.array(
        IMAGENET_STD, dtype=np.float32
    )

    actual = build_transform()(image).permute(1, 2, 0).numpy()

    assert np.allclose(actual, expected, atol=1e-5)


def test_autocontrast_stretches_low_contrast_scan():
    # The synthetic scan only uses values 20..220; after autocontrast it spans 0..255.
    tensor = build_transform()(make_image(512, 512, "RGB"))
    darkest = (0.0 - IMAGENET_MEAN[0]) / IMAGENET_STD[0]
    brightest = (1.0 - IMAGENET_MEAN[0]) / IMAGENET_STD[0]

    assert tensor[0].min().item() == pytest.approx(darkest, abs=0.02)
    assert tensor[0].max().item() == pytest.approx(brightest, abs=0.02)


def test_transform_handles_uniform_image():
    tensor = build_transform()(Image.new("RGB", (512, 512), (0, 0, 0)))

    assert torch.isfinite(tensor).all()


# --- Decoding ------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_decode_accepts_jpeg_and_png(fmt):
    image = decode_image(image_bytes(320, 240, fmt), MAX_PIXELS)

    assert image.mode == "RGB"
    assert image.size == (320, 240)


@pytest.mark.parametrize("mode", ["L", "RGB", "RGBA", "P"])
def test_decode_converts_any_mode_to_rgb(mode):
    image = decode_image(image_bytes(64, 64, "PNG", mode), MAX_PIXELS)

    assert image.mode == "RGB"


def test_decode_matches_training_loader():
    """Training read files with Image.open(path).convert('RGB')."""
    data = image_bytes(128, 96, "JPEG")

    expected = Image.open(io.BytesIO(data)).convert("RGB")

    assert np.array_equal(np.asarray(decode_image(data, MAX_PIXELS)), np.asarray(expected))


@pytest.mark.parametrize("data", [b"", b"this is definitely not an image", b"\x00" * 2048])
def test_decode_rejects_non_images(data):
    with pytest.raises(NotAnImageError):
        decode_image(data, MAX_PIXELS)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF", "WEBP"])
def test_decode_rejects_other_image_formats(fmt):
    with pytest.raises(UnsupportedFormatError, match=fmt):
        decode_image(image_bytes(64, 64, fmt), MAX_PIXELS)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_decode_rejects_truncated_files(fmt):
    data = image_bytes(512, 512, fmt)

    with pytest.raises(NotAnImageError):
        decode_image(data[: len(data) // 2], MAX_PIXELS)


def test_decode_enforces_pixel_limit():
    assert decode_image(image_bytes(1000, 1000), MAX_PIXELS).size == (1000, 1000)

    with pytest.raises(ImageTooLargeError, match="1001x1000"):
        decode_image(image_bytes(1001, 1000), MAX_PIXELS)


def test_pixel_limit_is_checked_before_format_is_decoded():
    """A huge image must be refused from its header alone, without loading pixels."""
    data = encode_image(Image.new("L", (3000, 3000)))

    with pytest.raises(ImageTooLargeError):
        decode_image(data[: len(data) // 2], MAX_PIXELS)
