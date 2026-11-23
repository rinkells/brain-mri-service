"""History endpoints: GET/DELETE /predictions and GET /stats, each for the signed-in
user's own records only."""

import logging
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.auth import UNAUTHORIZED_RESPONSE, CurrentUser
from app.config import CLASS_NAMES, ClassName
from app.database import Database
from app.history import (
    compute_stats,
    delete_prediction,
    get_prediction,
    list_predictions,
    well_formed_warnings,
)
from app.rate_limit import RATE_LIMITED_RESPONSE
from app.records import PredictionRecord
from app.schemas import (
    ErrorResponse,
    ImageSize,
    PredictionPage,
    PredictionRecordResponse,
    PredictionWarning,
    StatsResponse,
)
from app.security import FORBIDDEN_READ_RESPONSE, FORBIDDEN_WRITE_RESPONSE

logger = logging.getLogger("uvicorn.error")

HISTORY_DISABLED = "Облікові записи та історію вимкнено: не задано DATABASE_URL."
DATABASE_UNAVAILABLE = "База даних недоступна. Спробуйте пізніше."
DATABASE_REJECTED_VALUE = "База даних не прийняла значення із запиту. Перевірте параметри."
NOT_FOUND = "Прогноз не знайдено."

# Far beyond any real history, and well inside the BIGINT range Postgres takes for OFFSET.
MAX_OFFSET = 1_000_000

UNAVAILABLE_RESPONSE = {
    503: {
        "model": ErrorResponse,
        "description": "Облікові записи й історію вимкнено або база даних недоступна",
    }
}
# Another user's record answers exactly like a missing one, so ids reveal nothing.
NOT_FOUND_RESPONSE = {
    404: {"model": ErrorResponse, "description": "У цього користувача немає прогнозу з таким id"}
}


def get_session(request: Request) -> Iterator[Session]:
    database: Database | None = request.app.state.database
    if database is None:
        raise HTTPException(status_code=503, detail=HISTORY_DISABLED)
    with database.session() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(
    tags=["history"],
    responses={**UNAVAILABLE_RESPONSE, **RATE_LIMITED_RESPONSE, **UNAUTHORIZED_RESPONSE},
)


def readable_warnings(record: PredictionRecord) -> list[PredictionWarning]:
    """The record's well-formed warnings; anything else is logged and left out."""
    warnings = well_formed_warnings(record.warnings)
    if not isinstance(record.warnings, list) or len(warnings) != len(record.warnings):
        logger.warning(
            "Prediction %s has malformed warnings; showing %d of them. Stored value: %.200r",
            record.id,
            len(warnings),
            record.warnings,
        )
    return [PredictionWarning(**warning) for warning in warnings]


def to_response(record: PredictionRecord) -> PredictionRecordResponse:
    return PredictionRecordResponse(
        id=record.id,
        created_at=record.created_at,
        predicted_class=record.predicted_class,
        # JSONB does not keep key order; return the classes in model order, as /predict does.
        probabilities={
            name: record.probabilities[name] for name in CLASS_NAMES if name in record.probabilities
        },
        image_size=ImageSize(width=record.image_width, height=record.image_height),
        warnings=readable_warnings(record),
        gradcam_requested=record.gradcam_requested,
        latency_ms=record.latency_ms,
        model_version=record.model_version,
    )


@router.get(
    "/predictions",
    response_model=PredictionPage,
    responses=FORBIDDEN_READ_RESPONSE,
    summary="Історія прогнозів користувача",
)
def list_history(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Скільки записів повернути")] = 20,
    offset: Annotated[
        int, Query(ge=0, le=MAX_OFFSET, description="Скільки записів пропустити")
    ] = 0,
    predicted_class: Annotated[
        ClassName | None, Query(description="Лише прогнози цього класу")
    ] = None,
) -> PredictionPage:
    records, total = list_predictions(
        session, user.id, limit=limit, offset=offset, predicted_class=predicted_class
    )
    return PredictionPage(
        items=[to_response(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/predictions/{prediction_id}",
    response_model=PredictionRecordResponse,
    responses={**NOT_FOUND_RESPONSE, **FORBIDDEN_READ_RESPONSE},
    summary="Один прогноз з історії користувача",
)
def get_history_item(
    prediction_id: UUID, user: CurrentUser, session: SessionDep
) -> PredictionRecordResponse:
    record = get_prediction(session, user.id, prediction_id)
    if record is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return to_response(record)


@router.delete(
    "/predictions/{prediction_id}",
    status_code=204,
    response_class=Response,
    responses={**NOT_FOUND_RESPONSE, **FORBIDDEN_WRITE_RESPONSE},
    summary="Видалити прогноз з історії користувача",
)
def delete_history_item(prediction_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    if not delete_prediction(session, user.id, prediction_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)


@router.get(
    "/stats",
    response_model=StatsResponse,
    responses=FORBIDDEN_READ_RESPONSE,
    summary="Статистика прогнозів користувача",
)
def stats(user: CurrentUser, session: SessionDep) -> StatsResponse:
    result = compute_stats(session, user.id)
    return StatsResponse(
        total=result.total,
        by_class=result.by_class,
        with_warnings_share=result.with_warnings_share,
        average_latency_ms=result.average_latency_ms,
        warnings_by_code=result.warnings_by_code,
    )
