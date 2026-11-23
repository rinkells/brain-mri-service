"""Who sees which predictions: each user only their own records, nobody the records saved
before accounts existed (user_id NULL), and anonymous predictions are not kept at all."""

import uuid

import pytest
from sqlalchemy import func, select

from app.records import PredictionRecord
from tests.helpers import image_bytes, make_record, new_browser

NOT_SIGNED_IN = {"detail": "Потрібно увійти в обліковий запис."}
NOT_FOUND = {"detail": "Прогноз не знайдено."}
RECORD_ID = "6f1c2a3e-0000-4000-8000-000000000001"
SECURITY_HEADERS = ("x-content-type-options", "referrer-policy", "x-frame-options")


def post_scan(client, **params):
    files = {"file": ("scan.png", image_bytes(512, 512), "image/png")}
    return client.post("/predict", files=files, params=params)


def add_records(database, *records):
    with database.session() as session:
        session.add_all(records)
        session.commit()
    return [record.id for record in records]


def count_records(database) -> int:
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(PredictionRecord))


@pytest.fixture
def alice_and_bob(sign_in, database):
    return sign_in(database, "alice"), sign_in(database, "bob")


# --- Without a session ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/predictions"),
        ("GET", f"/predictions/{RECORD_ID}"),
        ("DELETE", f"/predictions/{RECORD_ID}"),
        ("GET", "/stats"),
    ],
)
def test_history_needs_a_session(use_database, database, method, path):
    anonymous = new_browser(use_database(database).app)

    response = anonymous.request(method, path)

    assert response.status_code == 401
    assert response.json() == NOT_SIGNED_IN
    assert response.headers["cache-control"] == "no-store"
    for name in SECURITY_HEADERS:
        assert name in response.headers


def test_an_anonymous_prediction_is_returned_but_not_saved(use_database, database):
    anonymous = new_browser(use_database(database).app)

    plain = post_scan(anonymous)
    with_heatmap = post_scan(anonymous, gradcam="true")

    for response in (plain, with_heatmap):
        assert response.status_code == 200
        body = response.json()
        assert body["id"] is None
        assert set(body) == {
            "id",
            "predicted_class",
            "probabilities",
            "image_size",
            "warnings",
            "gradcam_png_base64",
            "model_version",
        }
        assert "cache-control" not in response.headers
    assert with_heatmap.json()["gradcam_png_base64"]
    assert count_records(database) == 0


def test_a_signed_in_prediction_is_saved_for_the_user(history_client, database):
    body = post_scan(history_client).json()

    with database.session() as session:
        record = session.get(PredictionRecord, uuid.UUID(body["id"]))
    assert record.user_id == history_client.user_id
    assert history_client.get("/predictions").json()["items"][0]["id"] == body["id"]


# --- Between users ------------------------------------------------------------------


def test_each_user_lists_only_their_own_records(alice_and_bob, database):
    alice, bob = alice_and_bob
    alice_ids = add_records(
        database,
        make_record(user_id=alice.user_id, predicted_class="glioma"),
        make_record(user_id=alice.user_id, predicted_class="notumor"),
    )
    (bob_id,) = add_records(database, make_record(user_id=bob.user_id, predicted_class="glioma"))
    add_records(database, make_record(predicted_class="glioma"))  # saved before accounts

    alice_page = alice.get("/predictions").json()
    bob_page = bob.get("/predictions").json()
    alice_gliomas = alice.get("/predictions", params={"predicted_class": "glioma"}).json()

    assert {item["id"] for item in alice_page["items"]} == {str(i) for i in alice_ids}
    assert alice_page["total"] == 2
    assert [item["id"] for item in bob_page["items"]] == [str(bob_id)]
    assert bob_page["total"] == 1
    assert alice_gliomas["total"] == 1


def test_predictions_through_the_api_stay_with_their_user(alice_and_bob):
    alice, bob = alice_and_bob

    mine = post_scan(alice).json()["id"]
    post_scan(bob)
    post_scan(bob)

    assert [item["id"] for item in alice.get("/predictions").json()["items"]] == [mine]
    assert bob.get("/predictions").json()["total"] == 2


def test_another_users_record_is_404_like_a_missing_one(alice_and_bob, database):
    alice, bob = alice_and_bob
    (bobs,) = add_records(database, make_record(user_id=bob.user_id))
    missing = uuid.uuid4()

    for record_id in (bobs, missing):
        read = alice.get(f"/predictions/{record_id}")
        deleted = alice.delete(f"/predictions/{record_id}")
        assert (read.status_code, read.json()) == (404, NOT_FOUND)
        assert (deleted.status_code, deleted.json()) == (404, NOT_FOUND)

    assert bob.get(f"/predictions/{bobs}").status_code == 200
    assert count_records(database) == 1


def test_records_saved_before_accounts_are_shown_to_nobody(alice_and_bob, database):
    alice, bob = alice_and_bob
    (legacy,) = add_records(database, make_record())

    for user in (alice, bob):
        assert user.get("/predictions").json()["total"] == 0
        assert user.get(f"/predictions/{legacy}").status_code == 404
        assert user.delete(f"/predictions/{legacy}").status_code == 404
        assert user.get("/stats").json()["total"] == 0
    assert count_records(database) == 1


def test_stats_count_only_the_users_own_records(alice_and_bob, database):
    alice, bob = alice_and_bob
    warning = [{"code": "low_confidence", "message": "Модель недостатньо впевнена."}]
    add_records(
        database,
        make_record(user_id=alice.user_id, predicted_class="glioma", latency_ms=10.0),
        make_record(user_id=bob.user_id, predicted_class="pituitary", warnings=warning),
        make_record(user_id=bob.user_id, predicted_class="pituitary", latency_ms=30.0),
        make_record(predicted_class="notumor", warnings=warning),
    )

    alice_stats = alice.get("/stats").json()
    bob_stats = bob.get("/stats").json()

    assert alice_stats["total"] == 1
    assert alice_stats["by_class"] == {"glioma": 1, "meningioma": 0, "notumor": 0, "pituitary": 0}
    assert alice_stats["average_latency_ms"] == 10.0
    assert alice_stats["with_warnings_share"] == 0.0
    assert alice_stats["warnings_by_code"]["low_confidence"] == 0
    assert bob_stats["total"] == 2
    assert bob_stats["warnings_by_code"]["low_confidence"] == 1


def test_a_user_can_delete_only_their_own_record(alice_and_bob, database):
    alice, bob = alice_and_bob
    (alices,) = add_records(database, make_record(user_id=alice.user_id))

    assert bob.delete(f"/predictions/{alices}").status_code == 404
    assert alice.delete(f"/predictions/{alices}").status_code == 204
    assert count_records(database) == 0
