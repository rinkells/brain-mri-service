"""Prediction history: saving, listing, deleting and statistics."""

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import Integer, delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import FunctionElement

from app.config import CLASS_NAMES, WARNING_CODES, ClassName
from app.database import Database
from app.records import PredictionRecord

logger = logging.getLogger("uvicorn.error")

# After a failed save, skip the database for this long. Reaching a database that is
# down can take seconds (DNS lookup, connect timeout), and every prediction would wait.
SAVE_RETRY_AFTER_S = 30.0


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
    fails, the error is logged and None is returned. Only when the database cannot
    be reached (or no connection is free in time) is it not tried again for
    SAVE_RETRY_AFTER_S seconds; a record it rejects says nothing about the others.
    """
    if database is None:
        return None
    if time.monotonic() < database.saving_paused_until:
        logger.warning(
            "Prediction not saved: the history database failed less than %.0f s ago",
            SAVE_RETRY_AFTER_S,
        )
        return None
    try:
        with database.session() as session:
            session.add(record)
            session.commit()
            return record.id
    except (OperationalError, PoolTimeoutError):
        database.saving_paused_until = time.monotonic() + SAVE_RETRY_AFTER_S
        logger.exception("Could not save the prediction to the history database")
        return None
    except Exception:
        # The database answered but refused this record (e.g. a constraint): no pause.
        logger.exception("The history database rejected the prediction; it was not saved")
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
    """Delete a record; return False if there was none.

    The answer comes from the rows the DELETE itself removed. Reading the record
    first would let two simultaneous requests both see it and both report success.
    """
    result = session.execute(
        delete(PredictionRecord).where(PredictionRecord.id == prediction_id)
    )
    session.commit()
    return result.rowcount > 0


@dataclass(frozen=True)
class HistoryStats:
    total: int
    by_class: dict[ClassName, int]
    with_warnings_share: float | None
    average_latency_ms: float | None
    warnings_by_code: dict[str, int]


def well_formed_warnings(warnings: object) -> list[dict[str, str]]:
    """The warnings of a stored record that have a text code and message.

    The service only ever saves such a list, but a record changed by hand or by
    another tool may hold anything. Whatever is malformed is left out, so one
    damaged record cannot break the history or the statistics.
    """
    if not isinstance(warnings, list):
        return []
    return [
        warning
        for warning in warnings
        if isinstance(warning, dict)
        and isinstance(warning.get("code"), str)
        and isinstance(warning.get("message"), str)
    ]


def count_warning_codes(session: Session) -> dict[str, int]:
    """Number of predictions with each warning code; known codes are always present.

    A prediction with several warnings counts once for each of its codes. The codes
    are counted in Python: unpacking a JSON array in SQL takes different functions on
    Postgres and SQLite. Only records that have warnings are read, and only their
    well-formed warnings count, the same ones the history shows.
    """
    counts = dict.fromkeys(WARNING_CODES, 0)
    rows = session.scalars(
        select(PredictionRecord.warnings)
        .where(json_array_length(PredictionRecord.warnings) > 0)
        .execution_options(yield_per=1000)
    )
    for warnings in rows:
        for code in {warning["code"] for warning in well_formed_warnings(warnings)}:
            counts[code] = counts.get(code, 0) + 1
    return counts


def compute_stats(session: Session) -> HistoryStats:
    """Statistics of the whole history, all taken at the same moment.

    The counts and the average come from one statement, so the total always equals the
    sum over the classes, even while predictions are being saved. On Postgres both
    statements also run in one REPEATABLE READ transaction, so warnings_by_code sees
    the same records. The session must not have run anything before.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    has_warnings = json_array_length(PredictionRecord.warnings) > 0
    per_class = (
        func.count().filter(PredictionRecord.predicted_class == name) for name in CLASS_NAMES
    )
    row = session.execute(
        select(
            func.count(),
            func.count().filter(has_warnings),
            func.avg(PredictionRecord.latency_ms),
            *per_class,
        ).select_from(PredictionRecord)
    ).one()
    total, with_warnings, average_latency, *class_counts = row

    return HistoryStats(
        total=total,
        by_class=dict(zip(CLASS_NAMES, class_counts)),
        with_warnings_share=with_warnings / total if total else None,
        average_latency_ms=float(average_latency) if average_latency is not None else None,
        warnings_by_code=count_warning_codes(session),
    )
