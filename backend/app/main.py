"""FastAPI application: GET /health and POST /predict."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.config import CLASS_NAMES, EXPECTED_IMAGE_SIZE, Settings, get_settings
from app.gradcam import encode_png_base64, overlay_heatmap
from app.model import BrainMRIClassifier
from app.preprocessing import (
    ImageTooLargeError,
    NotAnImageError,
    UnsupportedFormatError,
    decode_image,
)
from app.schemas import (
    ErrorResponse,
    HealthResponse,
    ImageSize,
    PredictionResponse,
    PredictionWarning,
)

logger = logging.getLogger("uvicorn.error")

DESCRIPTION = (
    "Класифікація знімків МРТ мозку на чотири класи: glioma, meningioma, notumor, pituitary.\n\n"
    "**Навчальний проєкт. Це не медичний виріб, і його результати не можна "
    "використовувати для діагностики чи лікування.**"
)

# Room for the multipart boundaries and part headers that surround the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def file_too_large_detail(settings: Settings) -> str:
    return f"Файл завеликий: дозволено не більше {settings.max_upload_mb:g} МБ."


class MaxBodySizeMiddleware:
    """Answer 413 to oversized request bodies before they are buffered to disk."""

    def __init__(self, app: ASGIApp, max_bytes: int, detail: str) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.detail = detail

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            response = JSONResponse({"detail": self.detail}, status_code=413)
            await response(scope, receive, send)
            return

        # Content-Length may be absent (chunked upload), so the bytes are counted too.
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised while FastAPI parses the form; it becomes a 413 response.
                    raise HTTPException(status_code=413, detail=self.detail)
            return message

        await self.app(scope, limited_receive, send)


router = APIRouter()

ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Файл не є зображенням або пошкоджений"},
    413: {"model": ErrorResponse, "description": "Файл або зображення завелике"},
    415: {"model": ErrorResponse, "description": "Зображення не у форматі JPEG чи PNG"},
}


def size_warning(width: int, height: int) -> PredictionWarning | None:
    if (width, height) == EXPECTED_IMAGE_SIZE:
        return None
    expected = "x".join(map(str, EXPECTED_IMAGE_SIZE))
    return PredictionWarning(
        code="unexpected_image_size",
        message=(
            f"Розмір знімка {width}x{height}, а не {expected}. На знімках іншого розміру "
            "модель працює гірше і може пропустити гліому, бо в навчальних даних усі "
            f"гліоми були {expected}."
        ),
    )


@router.get("/health", response_model=HealthResponse, summary="Стан сервісу")
def health(request: Request) -> HealthResponse:
    classifier: BrainMRIClassifier = request.app.state.classifier
    return HealthResponse(status="ok", device=str(classifier.device), classes=list(CLASS_NAMES))


@router.post(
    "/predict",
    response_model=PredictionResponse,
    responses=ERROR_RESPONSES,
    summary="Класифікувати знімок МРТ",
)
def predict(
    request: Request,
    file: Annotated[UploadFile, File(description="Знімок МРТ у форматі JPEG або PNG")],
    gradcam: Annotated[
        bool, Query(description="Додати теплову карту Grad-CAM для передбаченого класу")
    ] = False,
) -> PredictionResponse:
    settings: Settings = request.app.state.settings
    classifier: BrainMRIClassifier = request.app.state.classifier

    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=file_too_large_detail(settings))

    try:
        image = decode_image(data, settings.max_pixels)
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except NotAnImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    prediction = classifier.predict(image, with_cam=gradcam)

    heatmap = None
    if prediction.cam is not None:
        heatmap = encode_png_base64(overlay_heatmap(image, prediction.cam))

    warning = size_warning(*image.size)
    return PredictionResponse(
        predicted_class=prediction.predicted_class,
        probabilities=prediction.probabilities,
        image_size=ImageSize(width=image.width, height=image.height),
        warnings=[warning] if warning else [],
        gradcam_png_base64=heatmap,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A missing or incompatible weights file stops the service right at startup.
        classifier = BrainMRIClassifier.from_file(settings.model_path, settings.device)
        app.state.classifier = classifier
        logger.info("Model loaded from %s on %s", settings.model_path, classifier.device)
        yield

    app = FastAPI(
        title="Brain MRI classifier",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        detail=file_too_large_detail(settings),
    )
    app.include_router(router)
    return app


app = create_app()
