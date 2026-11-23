"""FastAPI application: health checks, prediction and prediction history."""

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
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
    NOT_MRI_LIKE,
    UNEXPECTED_IMAGE_SIZE,
    Settings,
    get_settings,
)
from app.auth import (
    AUTH_BODY_TOO_LARGE,
    AUTH_MAX_BODY_BYTES,
    SESSION_USER,
    Passwords,
    SessionUser,
)
from app.auth_api import body_validation_error_handler
from app.auth_api import router as auth_router
from app.database import Database
from app.errors import (
    BODY_TOO_LARGE,
    CodedHTTPException,
    coded_http_exception_handler,
    error_body,
)
from app.gradcam import encode_png_base64, overlay_heatmap
from app.history import save_prediction
from app.history_api import DATABASE_REJECTED_VALUE, DATABASE_UNAVAILABLE
from app.history_api import router as history_router
from app.input_check import ImageFeatures, NotMriThresholds, compute_features, looks_not_mri
from app.limiter import InferenceLimiter, ServiceBusyError
from app.model import BrainMRIClassifier, Prediction
from app.rate_limit import (
    RATE_LIMITED_RESPONSE,
    RateLimitMiddleware,
    build_login_failures,
    build_rate_limiters,
)
from app.sessions import SessionMiddleware
from app.preprocessing import (
    ImageTooLargeError,
    NotAnImageError,
    UnsupportedFormatError,
    decode_upload,
)
from app.readiness import DatabaseProbe
from app.records import PredictionRecord
from app.schemas import (
    ErrorResponse,
    HealthResponse,
    ImageSize,
    PredictionResponse,
    PredictionWarning,
    ReadinessResponse,
)
from app.security import (
    AUTH_PREFIX,
    FORBIDDEN_WRITE_RESPONSE,
    HEALTH_PATH,
    NO_STORE,
    READY_PATH,
    CrossSiteCheckMiddleware,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
)

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
    """Answer 413 to oversized request bodies before they are buffered to disk or parsed.

    Bodies of /auth/* hold a name and a password: they get AUTH_MAX_BODY_BYTES, far less
    than a scan, so a sign-in never makes the API parse megabytes of JSON.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, detail: str) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.detail = detail

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope["path"].startswith(AUTH_PREFIX):
            max_bytes, detail, code = AUTH_MAX_BODY_BYTES, AUTH_BODY_TOO_LARGE, BODY_TOO_LARGE
        else:
            max_bytes, detail, code = self.max_bytes, self.detail, None

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            response = JSONResponse(error_body(detail, code), status_code=413)
            await response(scope, receive, send)
            return

        # Content-Length may be absent (chunked upload), so the bytes are counted too.
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    # Raised while FastAPI reads the form or the JSON; it becomes a 413.
                    if code is not None:
                        raise CodedHTTPException(413, detail, code)
                    raise HTTPException(status_code=413, detail=detail)
            return message

        await self.app(scope, limited_receive, send)


router = APIRouter()

ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Файл не є зображенням або пошкоджений"},
    401: {
        "model": ErrorResponse,
        "description": (
            "Cookie сесії недійсна або сесія закінчилася (відповідь видаляє cookie): прогноз "
            "не зберігся б, тож його не виконано. Без cookie прогноз працює анонімно"
        ),
    },
    **FORBIDDEN_WRITE_RESPONSE,
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
    **RATE_LIMITED_RESPONSE,
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


NOT_MRI_LIKE_MESSAGE = (
    "Зображення не схоже на знімок МРТ (кольорове, шумне, однорідне або без темного фону). "
    "Результат не можна вважати достовірним."
)


def not_mri_warning(
    features: ImageFeatures, thresholds: NotMriThresholds
) -> PredictionWarning | None:
    """A warning, not a refusal: the prediction is still returned."""
    if not looks_not_mri(features, thresholds):
        return None
    return PredictionWarning(code=NOT_MRI_LIKE, message=NOT_MRI_LIKE_MESSAGE)


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


HEALTH_DESCRIPTION = (
    "Liveness: процес працює й відповідає. Базу не опитує, тож відповідає 200, навіть коли "
    "Postgres недоступний; заодно повертає пристрій, класи й версію моделі для інтерфейсу. "
    "Не обмежується лімітом, не потребує сесії й не відхиляється перевіркою запитів зі "
    "сторінок інших сайтів. Готовність до роботи перевіряє GET /health/ready."
)
READY_DESCRIPTION = (
    "Readiness: сервіс готовий до повної роботи — модель завантажена, а база відповіла на "
    "SELECT 1 не довше ніж за 2 с. Без DATABASE_URL база не використовується й не "
    "перевіряється: database=null, і готовність залежить лише від моделі. Тоді 200 і "
    "status=ready, інакше 503 і status=not_ready; поля model і database кажуть, що саме не "
    "готове, без тексту помилки. Цим шляхом перевіряє стан сервісу api Docker "
    "Compose. Не обмежується лімітом, не потребує сесії, не відхиляється перевіркою "
    "запитів зі сторінок інших сайтів і не кешується (Cache-Control: no-store)."
)


# async: answered in the event loop, so it never waits for a worker thread busy with a scan.
@router.get(
    HEALTH_PATH,
    response_model=HealthResponse,
    summary="Стан процесу (liveness)",
    description=HEALTH_DESCRIPTION,
)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    classifier: BrainMRIClassifier = request.app.state.classifier
    return HealthResponse(
        status="ok",
        device=str(classifier.device),
        classes=list(CLASS_NAMES),
        model_version=settings.model_version,
    )


@router.get(
    READY_PATH,
    response_model=ReadinessResponse,
    summary="Готовність до роботи (readiness)",
    description=READY_DESCRIPTION,
    responses={
        200: {
            "description": (
                "Модель завантажена, а база відповідає (або не використовується: database=null)"
            )
        },
        503: {
            "model": ReadinessResponse,
            "description": "Модель не завантажена або задана база не відповіла за 2 с",
        },
    },
)
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    model = getattr(state, "classifier", None) is not None
    database: Database | None = getattr(state, "database", None)
    # Without DATABASE_URL the service runs without a database by design: it is not a
    # dependency, so it is not checked (null). Otherwise SELECT 1 runs in a thread of the
    # probe, never in the event loop.
    database_ok: bool | None = None
    if database is not None:
        probe: DatabaseProbe = state.readiness
        database_ok = await probe.reachable(database)
    body = ReadinessResponse(
        status="ready" if model and database_ok is not False else "not_ready",
        model=model,
        database=database_ok,
    )
    return JSONResponse(
        body.model_dump(),
        status_code=200 if body.status == "ready" else 503,
        headers=NO_STORE,
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
    # Found by SessionMiddleware before the file was read; None for an anonymous request.
    user: SessionUser | None = request.scope.get(SESSION_USER)

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
    # The most serious first: an image that is not a scan makes the rest beside the point.
    candidates = (
        not_mri_warning(analysis.features, settings.not_mri_thresholds),
        size_warning(analysis.width, analysis.height),
        confidence_warning(top_probability, settings.low_confidence_threshold),
    )
    warnings = [warning for warning in candidates if warning is not None]
    latency_ms = (time.perf_counter() - started) * 1000

    # Saved only for a signed-in user, as that user's record; an anonymous prediction is
    # answered and forgotten. Only the outcome is stored: never the scan itself or its
    # file name. Saving runs in a worker thread, because reaching a database that is down
    # can take seconds.
    prediction_id = None
    if user is not None:
        prediction_id = await run_in_threadpool(
            save_prediction,
            request.app.state.database,
            PredictionRecord(
                user_id=user.id,
                predicted_class=prediction.predicted_class,
                probabilities=prediction.probabilities,
                image_width=analysis.width,
                image_height=analysis.height,
                warnings=[w.model_dump() for w in warnings],
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
    features: ImageFeatures  # for the not_mri_like warning


def analyse(
    data: bytes, settings: Settings, classifier: BrainMRIClassifier, gradcam: bool
) -> Analysis:
    """Decode and classify a scan: the CPU-heavy part of /predict, run in a worker thread."""
    try:
        decoded = decode_upload(data, settings.max_pixels, settings.max_image_side)
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except NotAnImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    image = decoded.image

    prediction = classifier.predict(image, with_cam=gradcam)

    heatmap = None
    if prediction.cam is not None:
        heatmap = encode_png_base64(overlay_heatmap(image, prediction.cam))

    return Analysis(
        width=image.width,
        height=image.height,
        prediction=prediction,
        heatmap=heatmap,
        features=replace(
            compute_features(image, grey=decoded.grey), opaque_share=decoded.opaque_share
        ),
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
        # argon2id hashing in worker threads; the dummy hash (~0.1 s) is made once here.
        app.state.passwords = await Passwords.create()
        logger.info(
            "Model %s loaded from %s on %s",
            settings.model_version,
            settings.model_path,
            classifier.device,
        )

        # The database is not contacted here: predictions must work even while it is down.
        # GET /health/ready asks it, through a probe with a thread of its own.
        app.state.readiness = DatabaseProbe()
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
            app.state.readiness.close()
            if app.state.database is not None:
                app.state.database.dispose()

    # ENABLE_DOCS=false removes Swagger UI, ReDoc and the schema they are built from.
    docs = settings.enable_docs
    app = FastAPI(
        title="Brain MRI classifier",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.rate_limiters = build_rate_limiters(settings)
    app.state.login_failures = build_login_failures(settings)
    # The middleware added last runs first: the Host is checked, then the Origin (with the
    # session cookie, an Origin is required for writes), then whether a page of another
    # site sent it, then the rate limit, then the session, and only then is the body read.
    app.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        detail=file_too_large_detail(settings),
    )
    app.add_middleware(SessionMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CrossSiteCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    # Outermost, so the refusals of the checks above get the headers too.
    app.add_middleware(SecurityHeadersMiddleware)
    # Only a database that cannot be reached is "unavailable" (503). A value the database
    # rejects is the client's mistake (422). Anything else is a bug and stays a 500.
    app.add_exception_handler(OperationalError, database_unavailable_handler)
    app.add_exception_handler(PoolTimeoutError, database_unavailable_handler)
    app.add_exception_handler(DataError, database_data_error_handler)
    # Errors the interface tells apart carry a stable code next to the text.
    app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)
    # Bodies of /auth/* that cannot be parsed get 422 without the values they held.
    app.add_exception_handler(RequestValidationError, body_validation_error_handler)
    app.include_router(router)
    app.include_router(history_router)
    app.include_router(auth_router)
    return app


app = create_app()
