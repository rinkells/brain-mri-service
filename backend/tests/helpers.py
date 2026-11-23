"""Synthetic images, history records and users for tests, so no real scans are needed."""

import io
import uuid

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.records import PredictionRecord, User

# An origin of the interface: a page of it sends this with every POST and DELETE.
TEST_ORIGIN = "http://localhost:5173"
# The user history tests sign in as; records they add belong to it unless told otherwise.
OWNER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
OWNER_NAME = "owner"
TEST_PASSWORD = "correct-horse-1"


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
    """A history record with plausible values; any column can be overridden.

    It has no owner (user_id is NULL) unless one is given.
    """
    values = {
        "predicted_class": "meningioma",
        "probabilities": {"glioma": 0.05, "meningioma": 0.8, "notumor": 0.12, "pituitary": 0.03},
        "image_width": 512,
        "image_height": 512,
        "warnings": [],
        "gradcam_requested": False,
        "latency_ms": 12.5,
        "model_version": "resnet18_v2c",
    }
    values.update(fields)
    return PredictionRecord(**values)


def add_user(
    database, username: str, password_hash: str, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Insert a user straight into the database, without the cost of hashing a password."""
    user = User(id=user_id or uuid.uuid4(), username=username, password_hash=password_hash)
    with database.session() as session:
        session.add(user)
        session.commit()
    return user.id


class Browser(TestClient):
    """A client with its own cookie jar, like one browser.

    It sends Origin with every request, as a page of the interface does with POST and
    DELETE. It does not run the app's lifespan: the shared app is already running.
    """

    user_id: uuid.UUID | None = None
    token: str | None = None


def new_browser(app, origin: str | None = TEST_ORIGIN, **kwargs) -> Browser:
    headers = {"Origin": origin} if origin else {}
    return Browser(app, headers=headers, **kwargs)
