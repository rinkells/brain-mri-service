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
    decode_upload,
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


def test_decode_enforces_longer_side_limit():
    assert decode_image(image_bytes(64, 10), MAX_PIXELS, max_side=64).size == (64, 10)
    assert decode_image(image_bytes(10, 64), MAX_PIXELS, max_side=64).size == (10, 64)

    with pytest.raises(ImageTooLargeError, match="65x10"):
        decode_image(image_bytes(65, 10), MAX_PIXELS, max_side=64)
    with pytest.raises(ImageTooLargeError, match="10x65"):
        decode_image(image_bytes(10, 65), MAX_PIXELS, max_side=64)


def test_long_thin_image_is_refused_from_its_header():
    """16384x1024 fits into 4096 x 4096 pixels, but PadToSquare would make it 16384 x 16384."""
    data = encode_image(Image.new("L", (16384, 1024)))

    with pytest.raises(ImageTooLargeError, match="16384x1024"):
        decode_image(data[: len(data) // 2], 4096 * 4096)


# --- Pixel formats convert("RGB") would distort ---------------------------------

HIGH_BIT_DEPTH_MESSAGE = (
    "16-бітні зображення не підтримуються: експортуйте як 8-бітний PNG або JPEG."
)


def sixteen_bit_png(max_value: int = 65535) -> bytes:
    """A 16-bit greyscale gradient; Pillow opens such a PNG in mode I;16."""
    values = np.linspace(0, max_value, 64 * 64).astype(np.uint16).reshape(64, 64)
    return encode_image(Image.fromarray(values))


@pytest.mark.parametrize("max_value", [65535, 4095], ids=["16-bit", "12-bit-in-16"])
def test_decode_rejects_16_bit_png(max_value):
    data = sixteen_bit_png(max_value)
    assert Image.open(io.BytesIO(data)).mode == "I;16"

    with pytest.raises(UnsupportedFormatError) as error:
        decode_image(data, MAX_PIXELS)

    assert str(error.value) == HIGH_BIT_DEPTH_MESSAGE


@pytest.mark.parametrize("mode", ["I", "I;16", "I;16L", "I;16B", "F"])
def test_decode_rejects_every_high_bit_depth_mode(monkeypatch, mode):
    """A PNG only opens as I;16 today; a stand-in image covers the other wide modes."""
    stand_in = Image.new(mode, (64, 64))
    stand_in.format = "PNG"
    monkeypatch.setattr(Image, "open", lambda fp: stand_in)

    with pytest.raises(UnsupportedFormatError, match="16-бітні"):
        decode_image(b"a PNG in a wide mode", MAX_PIXELS)


def animated_png(frames: int = 2) -> bytes:
    images = [make_image(64, 64).rotate(90 * index) for index in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PNG", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def mpo_bytes(width: int = 320, height: int = 240) -> bytes:
    """A real MPO file, as phone cameras write it: two JPEG frames and an MP index ("MPF").
    The second frame is plain white, so it is easy to tell which one was read."""
    first = make_image(width, height, "RGB")
    second = Image.new("RGB", (width, height), (255, 255, 255))
    buffer = io.BytesIO()
    first.save(buffer, format="MPO", save_all=True, append_images=[second])
    return buffer.getvalue()


def test_decode_accepts_mpo_and_reads_its_first_frame():
    data = mpo_bytes()
    opened = Image.open(io.BytesIO(data))
    assert (opened.format, opened.n_frames) == ("MPO", 2)
    assert b"MPF\x00" in data

    decoded = decode_image(data, MAX_PIXELS)

    first_frame = Image.open(io.BytesIO(data)).convert("RGB")  # as the training loader reads it
    assert decoded.size == (320, 240)
    assert np.array_equal(np.asarray(decoded), np.asarray(first_frame))
    assert np.asarray(decoded).mean() < 200  # not the white second frame


def test_decode_rejects_animated_png():
    data = animated_png()
    assert Image.open(io.BytesIO(data)).n_frames == 2

    with pytest.raises(UnsupportedFormatError, match="кількома кадрами"):
        decode_image(data, MAX_PIXELS)


def rgba_with_hidden_pixels() -> Image.Image:
    """Left half: an opaque scan. Right half: bright pixels hidden under alpha 0.
    One column in the middle is half transparent."""
    pixels = np.zeros((64, 64, 4), dtype=np.uint8)
    pixels[..., :3] = np.asarray(make_image(64, 64, "RGB"))
    pixels[:, :32, 3] = 255
    pixels[:, 32:, :3] = 250
    pixels[:, 32, 3] = 128
    return Image.fromarray(pixels)


def test_transparent_pixels_become_black():
    image = rgba_with_hidden_pixels()
    rgb = np.asarray(image)[..., :3].astype(int)

    decoded = np.asarray(decode_image(encode_image(image), MAX_PIXELS)).astype(int)

    assert np.array_equal(decoded[:, :32], rgb[:, :32])
    assert np.abs(decoded[:, 32] - np.rint(rgb[:, 32] * 128 / 255)).max() <= 1
    assert (decoded[:, 33:] == 0).all()


def test_opaque_rgba_decodes_exactly_like_convert_rgb():
    """Fully opaque RGBA must give the very pixels training's convert('RGB') gave."""
    data = image_bytes(96, 80, "PNG", "RGBA")
    expected = Image.open(io.BytesIO(data)).convert("RGB")

    assert np.array_equal(np.asarray(decode_image(data, MAX_PIXELS)), np.asarray(expected))


@pytest.mark.parametrize("mode", ["LA", "P"])
def test_transparency_is_honoured_in_other_modes(mode):
    """Grey with alpha, and a palette PNG whose bright colour is marked transparent (tRNS)."""
    grey = np.full((16, 16), 200, dtype=np.uint8)
    grey[:, 8:] = 250  # hidden on the right
    buffer = io.BytesIO()
    if mode == "LA":
        alpha = np.full_like(grey, 255)
        alpha[:, 8:] = 0
        Image.fromarray(np.dstack([grey, alpha])).save(buffer, format="PNG")
    else:
        palette_image = Image.fromarray(grey).convert("P")
        hidden_index = palette_image.getpixel((15, 0))
        palette_image.save(buffer, format="PNG", transparency=hidden_index)
    opened = Image.open(io.BytesIO(buffer.getvalue()))
    assert opened.mode == mode
    assert np.asarray(opened.convert("RGB"))[0, 15, 0] == 250  # what convert("RGB") alone shows

    decoded = np.asarray(decode_image(buffer.getvalue(), MAX_PIXELS))

    assert (decoded[:, :8] == 200).all()
    assert (decoded[:, 8:] == 0).all()


# --- Grey sources -------------------------------------------------------------


def grey_with_alpha_png() -> bytes:
    pixels = np.dstack([np.asarray(make_image(64, 64)), np.full((64, 64), 128, dtype=np.uint8)])
    return encode_image(Image.fromarray(pixels, mode="LA"))


def palette_png() -> bytes:
    return encode_image(make_image(64, 64, "RGB").convert("P"))


@pytest.mark.parametrize(
    ("data", "grey"),
    [
        (image_bytes(64, 64, "PNG", "L"), True),
        (image_bytes(64, 64, "JPEG", "L"), True),
        (image_bytes(64, 64, "PNG", "1"), True),
        (grey_with_alpha_png(), True),
        (image_bytes(64, 64, "PNG", "RGB"), False),
        (image_bytes(64, 64, "JPEG", "RGB"), False),
        (image_bytes(64, 64, "PNG", "RGBA"), False),
        (palette_png(), False),
    ],
    ids=["L-png", "L-jpeg", "1-png", "LA-png", "RGB-png", "RGB-jpeg", "RGBA-png", "P-png"],
)
def test_decode_upload_tells_whether_the_file_is_grey(data, grey):
    decoded = decode_upload(data, MAX_PIXELS)

    assert decoded.grey is grey
    assert decoded.image.mode == "RGB"
    assert np.array_equal(np.asarray(decoded.image), np.asarray(decode_image(data, MAX_PIXELS)))
    if grey:
        # The promise behind the flag: no pixel has any colour.
        r, g, b = np.asarray(decoded.image).transpose(2, 0, 1)
        assert np.array_equal(r, g) and np.array_equal(g, b)
