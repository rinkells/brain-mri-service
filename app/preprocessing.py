"""Decoding of uploaded files and the preprocessing the model was trained with."""

import io

from PIL import Image
from torchvision import transforms as T

IMG_SIZE = 224
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
ALLOWED_FORMATS = ("JPEG", "PNG")


class ImageError(Exception):
    """Problem with an uploaded file. The message is shown to the API client."""


class NotAnImageError(ImageError):
    """The file is not an image or cannot be decoded."""


class UnsupportedFormatError(ImageError):
    """The file is an image, but neither JPEG nor PNG."""


class ImageTooLargeError(ImageError):
    """The image has more pixels than MAX_PIXELS allows."""


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


def decode_image(data: bytes, max_pixels: int) -> Image.Image:
    """Decode an uploaded JPEG or PNG into an RGB image, as the training Dataset did.

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

    # Checked on the header, before any pixel data is decompressed.
    width, height = image.size
    if width * height > max_pixels:
        raise ImageTooLargeError(
            f"Зображення {width}x{height} завелике: дозволено не більше {max_pixels} пікселів."
        )

    try:
        return image.convert("RGB")
    except Exception as exc:
        raise NotAnImageError("Зображення пошкоджене, його не вдалося прочитати.") from exc
