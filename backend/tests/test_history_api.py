import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.database import Database
from app.records import PredictionRecord
from tests.helpers import image_bytes, make_record

UNREACHABLE_POSTGRES = "postgresql+psycopg://brain_mri:secret@127.0.0.1:1/brain_mri"
BASE_TIME = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def post_scan(client, data=None, **params):
    data = data if data is not None else image_bytes(512, 512)
    return client.post("/predict", files={"file": ("scan.png", data, "image/png")}, params=params)


def add_records(database, *records):
    with database.session() as session:
        session.add_all(records)
        session.commit()
    return [record.id for record in records]


def records_at(*classes, warnings_from=None):
    """One record per class name, one minute apart; the last one is the newest.

    Records from index `warnings_from` on carry a warning; latencies are 10, 20, 30... ms.
    """
    warning = [{"code": "unexpected_image_size", "message": "Розмір знімка 640x480."}]
    return [
        make_record(
            predicted_class=name,
            created_at=BASE_TIME + timedelta(minutes=index),
            warnings=warning if warnings_from is not None and index >= warnings_from else [],
            latency_ms=10.0 * (index + 1),
        )
        for index, name in enumerate(classes)
    ]


# --- Saving after POST /predict -----------------------------------------------


def test_successful_prediction_is_saved(history_client, database):
    data = image_bytes(640, 480)

    response = post_scan(history_client, data)

    body = response.json()
    prediction_id = uuid.UUID(body["id"])
    with database.session() as session:
        record = session.get(PredictionRecord, prediction_id)
    assert record.predicted_class == body["predicted_class"]
    assert record.probabilities == pytest.approx(body["probabilities"])
    assert (record.image_width, record.image_height) == (640, 480)
    assert record.warnings == body["warnings"]
    assert record.warnings[0]["code"] == "unexpected_image_size"
    assert record.image_sha256 == hashlib.sha256(data).hexdigest()
    assert record.gradcam_requested is False
    assert record.latency_ms > 0


def test_gradcam_request_is_recorded(history_client, database):
    body = post_scan(history_client, gradcam="true").json()

    with database.session() as session:
        record = session.get(PredictionRecord, uuid.UUID(body["id"]))
    assert record.gradcam_requested is True


def test_each_prediction_gets_its_own_record(history_client):
    first = post_scan(history_client).json()["id"]
    second = post_scan(history_client).json()["id"]

    assert first != second
    assert history_client.get("/predictions").json()["total"] == 2


@pytest.mark.parametrize(
    "data",
    [b"not an image", image_bytes(64, 64, "GIF"), image_bytes(1001, 1000)],
    ids=["400", "415", "413"],
)
def test_failed_predictions_are_not_saved(history_client, data):
    assert post_scan(history_client, data).status_code in (400, 413, 415)

    assert history_client.get("/predictions").json()["total"] == 0


# --- GET /predictions ----------------------------------------------------------


def test_empty_history(history_client):
    response = history_client.get("/predictions")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_list_is_newest_first_with_full_records(history_client, database):
    ids = add_records(database, *records_at("glioma", "meningioma", "notumor"))

    items = history_client.get("/predictions").json()["items"]

    assert [item["id"] for item in items] == [str(i) for i in reversed(ids)]
    newest = items[0]
    assert newest["predicted_class"] == "notumor"
    assert newest["created_at"] in ("2026-11-01T12:02:00Z", "2026-11-01T12:02:00+00:00")
    assert newest["image_size"] == {"width": 512, "height": 512}
    assert set(newest) == {
        "id",
        "created_at",
        "predicted_class",
        "probabilities",
        "image_size",
        "warnings",
        "image_sha256",
        "gradcam_requested",
        "latency_ms",
    }


def test_pagination(history_client, database):
    ids = add_records(database, *records_at(*["glioma"] * 5))
    newest_first = [str(i) for i in reversed(ids)]

    first = history_client.get("/predictions", params={"limit": 2}).json()
    second = history_client.get("/predictions", params={"limit": 2, "offset": 2}).json()
    last = history_client.get("/predictions", params={"limit": 2, "offset": 4}).json()
    beyond = history_client.get("/predictions", params={"limit": 2, "offset": 10}).json()

    assert [i["id"] for i in first["items"]] == newest_first[0:2]
    assert [i["id"] for i in second["items"]] == newest_first[2:4]
    assert [i["id"] for i in last["items"]] == newest_first[4:5]
    assert beyond["items"] == []
    assert {page["total"] for page in (first, second, last, beyond)} == {5}
    assert (second["limit"], second["offset"]) == (2, 2)


def test_filter_by_class(history_client, database):
    add_records(database, *records_at("glioma", "pituitary", "glioma", "notumor"))

    body = history_client.get("/predictions", params={"predicted_class": "glioma"}).json()

    assert body["total"] == 2
    assert {item["predicted_class"] for item in body["items"]} == {"glioma"}


def test_filter_combines_with_pagination(history_client, database):
    add_records(database, *records_at("glioma", "pituitary", "glioma", "glioma"))

    body = history_client.get(
        "/predictions", params={"predicted_class": "glioma", "limit": 2, "offset": 2}
    ).json()

    assert body["total"] == 3
    assert len(body["items"]) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"limit": "many"},
        {"predicted_class": "astrocytoma"},
    ],
)
def test_invalid_list_parameters_are_422(history_client, params):
    assert history_client.get("/predictions", params=params).status_code == 422


# --- GET and DELETE /predictions/{id} ------------------------------------------


def test_get_one_prediction(history_client):
    created = post_scan(history_client).json()

    response = history_client.get(f"/predictions/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["predicted_class"] == created["predicted_class"]


def test_unknown_id_is_404(history_client):
    response = history_client.get(f"/predictions/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Прогноз не знайдено."}


def test_malformed_id_is_422(history_client):
    assert history_client.get("/predictions/not-a-uuid").status_code == 422
    assert history_client.delete("/predictions/not-a-uuid").status_code == 422


def test_delete(history_client, database):
    keep, remove = add_records(database, *records_at("glioma", "notumor"))

    response = history_client.delete(f"/predictions/{remove}")

    assert response.status_code == 204
    assert response.content == b""
    assert history_client.get(f"/predictions/{remove}").status_code == 404
    assert history_client.get(f"/predictions/{keep}").status_code == 200
    assert history_client.get("/predictions").json()["total"] == 1


def test_delete_twice_is_404(history_client, database):
    (record_id,) = add_records(database, make_record())

    assert history_client.delete(f"/predictions/{record_id}").status_code == 204
    assert history_client.delete(f"/predictions/{record_id}").status_code == 404


# --- GET /stats ----------------------------------------------------------------


def test_stats_of_empty_history(history_client):
    assert history_client.get("/stats").json() == {
        "total": 0,
        "by_class": {"glioma": 0, "meningioma": 0, "notumor": 0, "pituitary": 0},
        "with_warnings_share": None,
        "average_latency_ms": None,
    }


def test_stats(history_client, database):
    # Latencies 10, 20, 30, 40 ms; the last two records have warnings.
    add_records(database, *records_at("glioma", "glioma", "pituitary", "notumor", warnings_from=2))

    assert history_client.get("/stats").json() == {
        "total": 4,
        "by_class": {"glioma": 2, "meningioma": 0, "notumor": 1, "pituitary": 1},
        "with_warnings_share": 0.5,
        "average_latency_ms": 25.0,
    }


def test_stats_follow_new_predictions_and_deletes(history_client):
    created = post_scan(history_client, image_bytes(640, 480)).json()
    post_scan(history_client, image_bytes(512, 512))

    stats = history_client.get("/stats").json()
    assert stats["total"] == 2
    assert stats["with_warnings_share"] == 0.5

    history_client.delete(f"/predictions/{created['id']}")

    stats = history_client.get("/stats").json()
    assert stats["total"] == 1
    assert stats["with_warnings_share"] == 0.0


# --- Database unavailable or not configured ------------------------------------


@pytest.fixture
def broken_client(use_database):
    database = Database.from_url(UNREACHABLE_POSTGRES)
    yield use_database(database)
    database.dispose()


def test_prediction_still_works_when_database_is_down(broken_client, caplog):
    caplog.set_level(logging.ERROR)
    started = time.perf_counter()

    response = post_scan(broken_client)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] is None
    assert body["predicted_class"] in body["probabilities"]
    assert "Could not save the prediction to the history database" in caplog.text
    assert time.perf_counter() - started < 10


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/predictions"),
        ("get", f"/predictions/{uuid.uuid4()}"),
        ("delete", f"/predictions/{uuid.uuid4()}"),
        ("get", "/stats"),
    ],
)
def test_history_endpoints_report_503_when_database_is_down(broken_client, method, path):
    response = getattr(broken_client, method)(path)

    assert response.status_code == 503
    assert response.json() == {"detail": "База даних недоступна. Спробуйте пізніше."}


def test_health_does_not_depend_on_the_database(broken_client):
    assert broken_client.get("/health").status_code == 200


def test_without_database_url_predictions_are_not_saved(client):
    assert client.app.state.database is None

    body = post_scan(client).json()

    assert body["id"] is None


@pytest.mark.parametrize("path", ["/predictions", "/stats", f"/predictions/{uuid.uuid4()}"])
def test_history_is_503_without_database_url(client, path):
    response = client.get(path)

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


# --- API description -----------------------------------------------------------


def test_openapi_documents_history_errors(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    item = paths["/predictions/{prediction_id}"]

    assert {"200", "422", "503"} <= set(paths["/predictions"]["get"]["responses"])
    assert {"200", "404", "422", "503"} <= set(item["get"]["responses"])
    assert {"204", "404", "422", "503"} <= set(item["delete"]["responses"])
    assert {"200", "503"} <= set(paths["/stats"]["get"]["responses"])
    assert "id" in schema["components"]["schemas"]["PredictionResponse"]["required"]
