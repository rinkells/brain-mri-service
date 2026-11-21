"""Decoding of uploaded files and the preprocessing the model was trained with."""

import io
from dataclasses import dataclass

from PIL import Image
from torchvision import transforms as T

from app.config import DEFAULT_MAX_IMAGE_SIDE

IMG_SIZE = 224
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
# MPO is how phone cameras save JPEG photos: the photo itself is the first frame,
# followed by previews or a stereo pair. Only that first frame is read.
ALLOWED_FORMATS = ("JPEG", "MPO", "PNG")

# Modes with more than 8 bits per channel. convert("RGB") clips them to 0..255
# instead of scaling, so a 16-bit greyscale PNG would turn almost entirely white.
HIGH_BIT_DEPTH_MODES = frozenset({"I", "I;16", "I;16L", "I;16B", "I;16N", "F"})
HIGH_BIT_DEPTH_MESSAGE = (
    "16-бітні зображення не підтримуються: експортуйте як 8-бітний PNG або JPEG."
)
# Modes without colour: after the conversion to RGB every pixel has R == G == B.
GREY_MODES = frozenset({"1", "L", "LA"})
MULTI_FRAME_MESSAGE = (
    "Зображення з кількома кадрами (анімований PNG) не підтримуються: збережіть знімок "
    "як звичайний PNG або JPEG."
)


class ImageError(Exception):
    """Problem with an uploaded file. The message is shown to the API client."""


class NotAnImageError(ImageError):
    """The file is not an image or cannot be decoded."""


class UnsupportedFormatError(ImageError):
    """The file is an image, but not a single 8-bit JPEG or PNG frame."""


class ImageTooLargeError(ImageError):
    """The image has more pixels than MAX_PIXELS, or a longer side than MAX_IMAGE_SIDE allows."""


def square_padding(width: int, height: int) -> tuple[int, int]:
    """Offset (left, top) that centres a width x height image on its bounding square."""
    side = max(width, height)
    return (side - width) // 2, (side - height) // 2


class PadToSquare:
    """Pad a scan with black borders to a square, so non-square scans are not stretched.

    Same logic as PadToSquare in the training notebook.
    """

    def __call__(self, img: Image.Image) -> Image.Image:
        width, height = img.size
        side = max(width, height)
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        canvas.paste(img, square_padding(width, height))
        return canvas


def build_transform() -> T.Compose:
    """Evaluation transform the weights were validated with (eval_tf2 in the notebook).

    The steps and their order must stay identical to training: autocontrast runs
    after padding, so the black borders take part in the contrast stretch.
    """
    return T.Compose(
        [
            PadToSquare(),
            T.RandomAutocontrast(p=1.0),
            T.Resize((IMG_SIZE, IMG_SIZE)),
            T.ToTensor(),
            T.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )


@dataclass(frozen=True)
class DecodedImage:
    image: Image.Image  # RGB, as the training Dataset loaded it
    # The file itself was greyscale (GREY_MODES), so every pixel has R == G == B.
    grey: bool


def decode_image(
    data: bytes, max_pixels: int, max_side: int = DEFAULT_MAX_IMAGE_SIDE
) -> Image.Image:
    """Decode an uploaded JPEG (or MPO) or PNG into an RGB image, as the training Dataset did."""
    return decode_upload(data, max_pixels, max_side).image


def decode_upload(
    data: bytes, max_pixels: int, max_side: int = DEFAULT_MAX_IMAGE_SIDE
) -> DecodedImage:
    """decode_image, and whether the file was greyscale before the conversion to RGB.

    The format is detected from the file content, not from its name or Content-Type.
    """
    if not data:
        raise NotAnImageError("Файл порожній.")

    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as exc:
        raise ImageTooLargeError("Зображення завелике за кількістю пікселів.") from exc
    except Exception as exc:  # Pillow raises many unrelated error types for broken files
        raise NotAnImageError("Файл не є зображенням.") from exc

    if image.format not in ALLOWED_FORMATS:
        raise UnsupportedFormatError(
            f"Формат {image.format} не підтримується: потрібен JPEG або PNG."
        )

    # Checked on the header, before any pixel data is decompressed. The side limit
    # also bounds the square canvas PadToSquare makes later.
    width, height = image.size
    if width * height > max_pixels:
        raise ImageTooLargeError(
            f"Зображення {width}x{height} завелике: дозволено не більше {max_pixels} пікселів."
        )
    if max(width, height) > max_side:
        raise ImageTooLargeError(
            f"Зображення {width}x{height} завелике: дозволено не більше {max_side} пікселів "
            "по довшій стороні."
        )

    if image.mode in HIGH_BIT_DEPTH_MODES:
        raise UnsupportedFormatError(HIGH_BIT_DEPTH_MESSAGE)
    # Otherwise only the first frame of an animated PNG would be classified, silently.
    # An MPO is a JPEG photo whose extra frames are not part of the picture.
    if image.format != "MPO" and getattr(image, "n_frames", 1) > 1:
        raise UnsupportedFormatError(MULTI_FRAME_MESSAGE)

    try:
        return DecodedImage(image=to_rgb(image), grey=image.mode in GREY_MODES)
    except Exception as exc:
        raise NotAnImageError("Зображення пошкоджене, його не вдалося прочитати.") from exc


def to_rgb(image: Image.Image) -> Image.Image:
    """convert("RGB"), except that transparent pixels become the black they look like.

    convert("RGB") alone drops the alpha channel and so shows whatever colours the
    transparent pixels hold. Images without transparency (every JPEG, most PNGs) take
    the plain convert("RGB") path, exactly as in training; on fully opaque pixels the
    result is identical either way.
    """
    if not image.has_transparency_data:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, (0, 0, 0))
    canvas.paste(rgba, mask=rgba.getchannel("A"))
    return canvas
