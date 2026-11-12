"""History endpoints: GET/DELETE /predictions and GET /stats."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.config import ClassName
from app.database import Database
from app.history import compute_stats, delete_prediction, list_predictions
from app.records import PredictionRecord
from app.schemas import (
    ErrorResponse,
    ImageSize,
    PredictionPage,
    PredictionRecordResponse,
    PredictionWarning,
    StatsResponse,
)

HISTORY_DISABLED = "Історію вимкнено: не задано DATABASE_URL."
DATABASE_UNAVAILABLE = "База даних недоступна. Спробуйте пізніше."
NOT_FOUND = "Прогноз не знайдено."

UNAVAILABLE_RESPONSE = {
    503: {"model": ErrorResponse, "description": "Історію вимкнено або база даних недоступна"}
}
NOT_FOUND_RESPONSE = {404: {"model": ErrorResponse, "description": "Прогнозу з таким id немає"}}


def get_session(request: Request) -> Iterator[Session]:
    database: Database | None = request.app.state.database
    if database is None:
        raise HTTPException(status_code=503, detail=HISTORY_DISABLED)
    with database.session() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["history"], responses=UNAVAILABLE_RESPONSE)


def to_response(record: PredictionRecord) -> PredictionRecordResponse:
    return PredictionRecordResponse(
        id=record.id,
        created_at=record.created_at,
        predicted_class=record.predicted_class,
        probabilities=record.probabilities,
        image_size=ImageSize(width=record.image_width, height=record.image_height),
        warnings=[PredictionWarning(**warning) for warning in record.warnings],
        image_sha256=record.image_sha256,
        gradcam_requested=record.gradcam_requested,
        latency_ms=record.latency_ms,
    )


@router.get("/predictions", response_model=PredictionPage, summary="Історія прогнозів")
def list_history(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Скільки записів повернути")] = 20,
    offset: Annotated[int, Query(ge=0, description="Скільки записів пропустити")] = 0,
    predicted_class: Annotated[
        ClassName | None, Query(description="Лише прогнози цього класу")
    ] = None,
) -> PredictionPage:
    records, total = list_predictions(
        session, limit=limit, offset=offset, predicted_class=predicted_class
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
    responses=NOT_FOUND_RESPONSE,
    summary="Один прогноз з історії",
)
def get_history_item(prediction_id: UUID, session: SessionDep) -> PredictionRecordResponse:
    record = session.get(PredictionRecord, prediction_id)
    if record is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return to_response(record)


@router.delete(
    "/predictions/{prediction_id}",
    status_code=204,
    response_class=Response,
    responses=NOT_FOUND_RESPONSE,
    summary="Видалити прогноз з історії",
)
def delete_history_item(prediction_id: UUID, session: SessionDep) -> Response:
    if not delete_prediction(session, prediction_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)


@router.get("/stats", response_model=StatsResponse, summary="Статистика прогнозів")
def stats(session: SessionDep) -> StatsResponse:
    result = compute_stats(session)
    return StatsResponse(
        total=result.total,
        by_class=result.by_class,
        with_warnings_share=result.with_warnings_share,
        average_latency_ms=result.average_latency_ms,
    )
