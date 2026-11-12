"""Prediction history: saving, listing, deleting and statistics."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import FunctionElement

from app.config import CLASS_NAMES, ClassName
from app.database import Database
from app.records import PredictionRecord

logger = logging.getLogger("uvicorn.error")


class json_array_length(FunctionElement[int]):
    """Length of a JSON array: jsonb_array_length on Postgres, json_array_length on SQLite."""

    type = Integer()
    inherit_cache = True


@compiles(json_array_length)
def _json_array_length_default(element, compiler, **kw):
    return f"json_array_length({compiler.process(element.clauses, **kw)})"


@compiles(json_array_length, "postgresql")
def _json_array_length_postgres(element, compiler, **kw):
    return f"jsonb_array_length({compiler.process(element.clauses, **kw)})"


def save_prediction(database: Database | None, record: PredictionRecord) -> uuid.UUID | None:
    """Store a finished prediction and return its id.

    History must never break a prediction: when there is no database, or it
    fails, the error is logged and None is returned.
    """
    if database is None:
        return None
    try:
        with database.session() as session:
            session.add(record)
            session.commit()
            return record.id
    except Exception:
        logger.exception("Could not save the prediction to the history database")
        return None


def list_predictions(
    session: Session, *, limit: int, offset: int, predicted_class: ClassName | None = None
) -> tuple[list[PredictionRecord], int]:
    """One page of records, newest first, and the number of records matching the filter."""
    conditions = []
    if predicted_class is not None:
        conditions.append(PredictionRecord.predicted_class == predicted_class)

    total = session.scalar(select(func.count()).select_from(PredictionRecord).where(*conditions))
    records = session.scalars(
        select(PredictionRecord)
        .where(*conditions)
        .order_by(PredictionRecord.created_at.desc(), PredictionRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(records), total or 0


def delete_prediction(session: Session, prediction_id: uuid.UUID) -> bool:
    record = session.get(PredictionRecord, prediction_id)
    if record is None:
        return False
    session.delete(record)
    session.commit()
    return True


@dataclass(frozen=True)
class HistoryStats:
    total: int
    by_class: dict[ClassName, int]
    with_warnings_share: float | None
    average_latency_ms: float | None


def compute_stats(session: Session) -> HistoryStats:
    total = session.scalar(select(func.count()).select_from(PredictionRecord)) or 0

    counts = dict.fromkeys(CLASS_NAMES, 0)
    rows = session.execute(
        select(PredictionRecord.predicted_class, func.count()).group_by(
            PredictionRecord.predicted_class
        )
    )
    for name, count in rows:
        counts[name] = count

    with_warnings = session.scalar(
        select(func.count())
        .select_from(PredictionRecord)
        .where(json_array_length(PredictionRecord.warnings) > 0)
    )
    average_latency = session.scalar(select(func.avg(PredictionRecord.latency_ms)))

    return HistoryStats(
        total=total,
        by_class=counts,
        with_warnings_share=with_warnings / total if total else None,
        average_latency_ms=float(average_latency) if average_latency is not None else None,
    )
