import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import CLASS_NAMES, Settings
from app.main import create_app
from app.model import ModelLoadError
from tests.conftest import MAX_UPLOAD_MB
from tests.helpers import image_bytes

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
BOUNDARY = "test-boundary"


def post_image(client, data, filename="scan.png", content_type="image/png", **params):
    return client.post("/predict", files={"file": (filename, data, content_type)}, params=params)


def multipart_body(data: bytes) -> bytes:
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="scan.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{BOUNDARY}--\r\n".encode()


def post_chunked(client, data: bytes):
    """Upload without a Content-Length header, as a chunked request body."""
    body = multipart_body(data)
    chunks = (body[i : i + 65536] for i in range(0, len(body), 65536))
    return client.post(
        "/predict",
        content=chunks,
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


def padded_png(total_bytes: int) -> bytes:
    """A valid 512x512 PNG followed by zero bytes up to the requested file size."""
    data = image_bytes(512, 512)
    return data + b"\0" * (total_bytes - len(data))


# --- GET /health ---------------------------------------------------------------


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "device": "cpu", "classes": list(CLASS_NAMES)}


# --- POST /predict: successful requests ----------------------------------------


@pytest.mark.parametrize(
    ("fmt", "filename", "content_type"),
    [("PNG", "scan.png", "image/png"), ("JPEG", "scan.jpg", "image/jpeg")],
)
def test_predict_512_scan(client, fmt, filename, content_type):
    response = post_image(client, image_bytes(512, 512, fmt), filename, content_type)

    assert response.status_code == 200
    body = response.json()
    assert list(body["probabilities"]) == list(CLASS_NAMES)
    assert sum(body["probabilities"].values()) == pytest.approx(1.0, abs=1e-5)
    assert body["predicted_class"] == max(body["probabilities"], key=body["probabilities"].get)
    assert body["image_size"] == {"width": 512, "height": 512}
    assert body["warnings"] == []
    assert body["gradcam_png_base64"] is None


@pytest.mark.parametrize("size", [(630, 630), (640, 480), (512, 511)])
def test_predict_warns_about_other_sizes(client, size):
    response = post_image(client, image_bytes(*size))

    assert response.status_code == 200
    body = response.json()
    assert body["image_size"] == {"width": size[0], "height": size[1]}
    assert [w["code"] for w in body["warnings"]] == ["unexpected_image_size"]
    assert f"{size[0]}x{size[1]}" in body["warnings"][0]["message"]
    assert "512x512" in body["warnings"][0]["message"]


@pytest.mark.parametrize("size", [(512, 512), (640, 480)])
def test_predict_with_gradcam_returns_png_of_scan_size(client, size):
    response = post_image(client, image_bytes(*size), gradcam="true")

    assert response.status_code == 200
    heatmap = Image.open(io.BytesIO(base64.b64decode(response.json()["gradcam_png_base64"])))
    assert heatmap.format == "PNG"
    assert heatmap.mode == "RGB"
    assert heatmap.size == size


def test_gradcam_does_not_change_the_prediction(client):
    data = image_bytes(512, 512)

    plain = post_image(client, data).json()
    explained = post_image(client, data, gradcam="true").json()

    assert explained["predicted_class"] == plain["predicted_class"]
    assert list(explained["probabilities"].values()) == pytest.approx(
        list(plain["probabilities"].values()), abs=1e-6
    )


def test_gradcam_false_returns_no_heatmap(client):
    response = post_image(client, image_bytes(512, 512), gradcam="false")

    assert response.status_code == 200
    assert response.json()["gradcam_png_base64"] is None


def test_format_is_detected_from_content_not_from_name(client):
    response = post_image(client, image_bytes(512, 512, "PNG"), "notes.txt", "text/plain")

    assert response.status_code == 200


# --- 400: not an image ---------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [b"", b"just some text, not a scan", image_bytes(512, 512)[:2000]],
    ids=["empty", "text", "truncated-png"],
)
def test_predict_rejects_non_images_with_400(client, data):
    response = post_image(client, data)

    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


# --- 415: an image, but neither JPEG nor PNG -----------------------------------


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF", "WEBP"])
def test_predict_rejects_other_formats_with_415(client, fmt):
    # Named and labelled as PNG on purpose: only the content counts.
    response = post_image(client, image_bytes(512, 512, fmt))

    assert response.status_code == 415
    assert fmt in response.json()["detail"]


# --- 413: too large ------------------------------------------------------------


def test_file_of_exactly_the_limit_is_accepted(client):
    response = post_image(client, padded_png(MAX_UPLOAD_BYTES))

    assert response.status_code == 200


@pytest.mark.parametrize("extra", [1, MAX_UPLOAD_BYTES], ids=["one-byte-over", "twice-the-limit"])
def test_predict_rejects_large_files_with_413(client, extra):
    response = post_image(client, padded_png(MAX_UPLOAD_BYTES + extra))

    assert response.status_code == 413
    assert f"{MAX_UPLOAD_MB} МБ" in response.json()["detail"]


def test_chunked_upload_works(client):
    response = post_chunked(client, image_bytes(512, 512))

    assert response.status_code == 200


def test_chunked_upload_over_the_limit_is_rejected_with_413(client):
    response = post_chunked(client, padded_png(2 * MAX_UPLOAD_BYTES))

    assert response.status_code == 413
    assert f"{MAX_UPLOAD_MB} МБ" in response.json()["detail"]


def test_predict_rejects_too_many_pixels_with_413(client):
    response = post_image(client, image_bytes(1001, 1000))

    assert response.status_code == 413
    assert "1001x1000" in response.json()["detail"]


# --- 422: request validation ---------------------------------------------------


def test_predict_without_file_is_422(client):
    assert client.post("/predict").status_code == 422


def test_predict_with_wrong_field_name_is_422(client):
    response = client.post("/predict", files={"image": ("scan.png", image_bytes(), "image/png")})

    assert response.status_code == 422


def test_predict_with_invalid_gradcam_flag_is_422(client):
    response = post_image(client, image_bytes(), gradcam="maybe")

    assert response.status_code == 422


# --- Service-level behaviour ---------------------------------------------------


def test_service_recovers_after_errors(client):
    assert post_image(client, b"broken").status_code == 400
    assert post_image(client, image_bytes(512, 512)).status_code == 200


def test_openapi_documents_error_responses(client):
    responses = client.get("/openapi.json").json()["paths"]["/predict"]["post"]["responses"]

    assert {"200", "400", "413", "415", "422"} <= set(responses)


def test_service_does_not_start_without_weights(tmp_path):
    settings = Settings(_env_file=None, model_path=tmp_path / "absent.pt")

    with pytest.raises(ModelLoadError, match="not found"):
        with TestClient(create_app(settings)):
            pass
