"""FastAPI application: health check, prediction and prediction history."""

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import make_url
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.config import (
    CLASS_NAMES,
    EXPECTED_IMAGE_SIZE,
    LOW_CONFIDENCE,
    UNEXPECTED_IMAGE_SIZE,
    Settings,
    get_settings,
)
from app.database import Database
from app.gradcam import encode_png_base64, overlay_heatmap
from app.history import save_prediction
from app.history_api import DATABASE_REJECTED_VALUE, DATABASE_UNAVAILABLE
from app.history_api import router as history_router
from app.limiter import InferenceLimiter, ServiceBusyError
from app.model import BrainMRIClassifier, Prediction
from app.preprocessing import (
    ImageTooLargeError,
    NotAnImageError,
    UnsupportedFormatError,
    decode_image,
)
from app.records import PredictionRecord
from app.schemas import (
    ErrorResponse,
    HealthResponse,
    ImageSize,
    PredictionResponse,
    PredictionWarning,
)
from app.security import OriginCheckMiddleware

logger = logging.getLogger("uvicorn.error")

DESCRIPTION = (
    "Класифікація знімків МРТ мозку на чотири класи: glioma, meningioma, notumor, pituitary.\n\n"
    "**Навчальний проєкт. Це не медичний виріб, і його результати не можна "
    "використовувати для діагностики чи лікування.**"
)

# Room for the multipart boundaries and part headers that surround the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

# Sent as Retry-After when /predict is refused because the queue is full.
BUSY_RETRY_AFTER_S = 5
BUSY_DETAIL = "Сервер зараз зайнятий іншими знімками. Спробуйте ще раз за кілька секунд."


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
    415: {
        "model": ErrorResponse,
        "description": "Зображення не у форматі JPEG чи PNG, 16-бітне або має кілька кадрів",
    },
    503: {
        "model": ErrorResponse,
        "description": (
            "Сервер зайнятий: виконується MAX_CONCURRENT_INFERENCES прогнозів і ще MAX_QUEUE "
            "чекають; заголовок Retry-After каже, через скільки секунд повторити"
        ),
    },
}


# The recall figures come from the 5-fold cross-validation of resnet18_v2c (see README).
SIZE_WARNING_MESSAGE = (
    "Розмір знімка відрізняється від 512×512. Для гліом такого формату recall у перевірці "
    "був ≈ 0,51 (проти ≈ 0,99 для 512×512): якщо є підозра на гліому, результат менш надійний."
)


def size_warning(width: int, height: int) -> PredictionWarning | None:
    if (width, height) == EXPECTED_IMAGE_SIZE:
        return None
    return PredictionWarning(code=UNEXPECTED_IMAGE_SIZE, message=SIZE_WARNING_MESSAGE)


def confidence_warning(probability: float, threshold: float) -> PredictionWarning | None:
    """Warn when the top-1 probability is below the threshold.

    The raw probability is compared, never a rounded one: 0.8999 is below 0.9 even
    though the interface shows it as 90,0 %.
    """
    if probability >= threshold:
        return None
    return PredictionWarning(
        code=LOW_CONFIDENCE,
        message=(
            "Модель недостатньо впевнена в цьому прогнозі: ймовірність передбаченого класу "
            f"нижча за {threshold * 100:g} %."
        ),
    )


# async: answered in the event loop, so it never waits for a worker thread busy with a scan.
@router.get("/health", response_model=HealthResponse, summary="Стан сервісу")
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    classifier: BrainMRIClassifier = request.app.state.classifier
    return HealthResponse(
        status="ok",
        device=str(classifier.device),
        classes=list(CLASS_NAMES),
        model_version=settings.model_version,
    )


@router.post(
    "/predict",
    response_model=PredictionResponse,
    responses=ERROR_RESPONSES,
    summary="Класифікувати знімок МРТ",
)
async def predict(
    request: Request,
    file: Annotated[UploadFile, File(description="Знімок МРТ у форматі JPEG або PNG")],
    gradcam: Annotated[
        bool, Query(description="Додати теплову карту Grad-CAM для передбаченого класу")
    ] = False,
) -> PredictionResponse:
    settings: Settings = request.app.state.settings
    classifier: BrainMRIClassifier = request.app.state.classifier
    limiter: InferenceLimiter = request.app.state.inference_limiter

    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=file_too_large_detail(settings))

    # The request waits for its turn here, in the event loop; only the CPU-heavy part
    # runs in a worker thread, at most MAX_CONCURRENT_INFERENCES of them at a time.
    try:
        async with limiter.slot():
            started = time.perf_counter()
            analysis = await run_in_threadpool(analyse, data, settings, classifier, gradcam)
    except ServiceBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail=BUSY_DETAIL,
            headers={"Retry-After": str(BUSY_RETRY_AFTER_S)},
        ) from exc

    prediction = analysis.prediction
    top_probability = prediction.probabilities[prediction.predicted_class]
    candidates = (
        size_warning(analysis.width, analysis.height),
        confidence_warning(top_probability, settings.low_confidence_threshold),
    )
    warnings = [warning for warning in candidates if warning is not None]
    latency_ms = (time.perf_counter() - started) * 1000

    # Only the outcome is stored: never the scan itself or its file name. Saving runs in a
    # worker thread, because reaching a database that is down can take seconds.
    prediction_id = await run_in_threadpool(
        save_prediction,
        request.app.state.database,
        PredictionRecord(
            predicted_class=prediction.predicted_class,
            probabilities=prediction.probabilities,
            image_width=analysis.width,
            image_height=analysis.height,
            warnings=[w.model_dump() for w in warnings],
            image_sha256=analysis.sha256,
            gradcam_requested=gradcam,
            latency_ms=latency_ms,
            model_version=settings.model_version,
        ),
    )

    return PredictionResponse(
        id=prediction_id,
        predicted_class=prediction.predicted_class,
        probabilities=prediction.probabilities,
        image_size=ImageSize(width=analysis.width, height=analysis.height),
        warnings=warnings,
        gradcam_png_base64=analysis.heatmap,
        model_version=settings.model_version,
    )


@dataclass(frozen=True)
class Analysis:
    """What /predict learns from an uploaded file."""

    width: int
    height: int
    prediction: Prediction
    heatmap: str | None  # PNG in base64, only when Grad-CAM was asked for
    sha256: str


def analyse(
    data: bytes, settings: Settings, classifier: BrainMRIClassifier, gradcam: bool
) -> Analysis:
    """Decode and classify a scan: the CPU-heavy part of /predict, run in a worker thread."""
    try:
        image = decode_image(data, settings.max_pixels, settings.max_image_side)
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

    return Analysis(
        width=image.width,
        height=image.height,
        prediction=prediction,
        heatmap=heatmap,
        sha256=hashlib.sha256(data).hexdigest(),
    )


async def database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "History database unavailable on %s %s: %s", request.method, request.url.path, exc
    )
    return JSONResponse({"detail": DATABASE_UNAVAILABLE}, status_code=503)


async def database_data_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning(
        "History database rejected a value on %s %s: %s", request.method, request.url.path, exc
    )
    return JSONResponse({"detail": DATABASE_REJECTED_VALUE}, status_code=422)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A missing or incompatible weights file stops the service right at startup.
        classifier = BrainMRIClassifier.from_file(settings.model_path, settings.device)
        app.state.classifier = classifier
        app.state.inference_limiter = InferenceLimiter(
            settings.max_concurrent_inferences, settings.max_queue
        )
        logger.info(
            "Model %s loaded from %s on %s",
            settings.model_version,
            settings.model_path,
            classifier.device,
        )

        # The database is not contacted here: predictions must work even while it is down.
        app.state.database = None
        if settings.database_url:
            app.state.database = Database.from_url(settings.database_url)
            safe_url = make_url(settings.database_url).render_as_string(hide_password=True)
            logger.info("Prediction history is stored in %s", safe_url)
        else:
            logger.warning("DATABASE_URL is not set: predictions will not be saved to history")
        try:
            yield
        finally:
            if app.state.database is not None:
                app.state.database.dispose()

    app = FastAPI(
        title="Brain MRI classifier",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # The middleware added last runs first: the Host is checked, then the Origin,
    # and only then is the body read.
    app.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        detail=file_too_large_detail(settings),
    )
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    # Only a database that cannot be reached is "unavailable" (503). A value the database
    # rejects is the client's mistake (422). Anything else is a bug and stays a 500.
    app.add_exception_handler(OperationalError, database_unavailable_handler)
    app.add_exception_handler(PoolTimeoutError, database_unavailable_handler)
    app.add_exception_handler(DataError, database_data_error_handler)
    app.include_router(router)
    app.include_router(history_router)
    return app


app = create_app()
