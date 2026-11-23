"""GET /health (liveness) and GET /health/ready (readiness).

/health says only that the process answers; /health/ready also checks that the model is
loaded and the database answers SELECT 1, and is what the Compose healthcheck of api asks.
"""

import logging
import secrets
import threading
import time

import pytest
from sqlalchemy.exc import OperationalError

from app.database import Database
from app.rate_limit import bucket_of
from app.readiness import DatabaseProbe
from tests.helpers import new_browser
from tests.test_rate_limit import limited  # noqa: F401  (fixture)

READY = {"status": "ready", "model": True, "database": True}
# Words of the exception the stand-in engine raises; none of them may reach a response.
SECRET_ERROR = 'password authentication failed for user "brain_mri" at db:5432 (secret-7Qx)'
FOREIGN_PAGE = {
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "no-cors",
    "Origin": "https://evil.example",
}
HEALTH_PATHS = ("/health", "/health/ready")


class FailingEngine:
    """Stands in for a SQLAlchemy engine whose database cannot be reached."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.connects = 0

    def connect(self):
        self.connects += 1
        raise self.error

    def dispose(self) -> None:
        pass


class StalledEngine:
    """An engine whose connect() hangs until released, like a database that never answers."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.connects = 0

    def connect(self):
        self.connects += 1
        self.release.wait(timeout=30)
        raise OperationalError("SELECT 1", {}, Exception("timed out"))

    def dispose(self) -> None:
        pass


@pytest.fixture
def replace_state(client):
    """replace_state(name, value) sets app.state.<name> for one test and restores it after.

    Not monkeypatch: Starlette's State keeps its values outside __dict__, so monkeypatch
    would delete the attribute on undo instead of restoring it.
    """
    state = client.app.state
    saved = {}

    def replace(name: str, value) -> None:
        saved.setdefault(name, getattr(state, name))
        setattr(state, name, value)

    yield replace
    for name, value in saved.items():
        setattr(state, name, value)


def assert_security_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


# --- GET /health/ready -----------------------------------------------------------


def test_ready_when_the_model_is_loaded_and_the_database_answers(use_database, database):
    client = use_database(database)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == READY
    assert response.headers["cache-control"] == "no-store"
    assert_security_headers(response)


def test_not_ready_without_a_loaded_model(use_database, database, replace_state):
    client = use_database(database)
    replace_state("classifier", None)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "model": False, "database": True}
    assert response.headers["cache-control"] == "no-store"
    assert_security_headers(response)


@pytest.mark.parametrize(
    "error",
    [OperationalError("SELECT 1", {}, Exception(SECRET_ERROR)), RuntimeError(SECRET_ERROR)],
    ids=["operational-error", "any-other-error"],
)
def test_not_ready_when_the_database_cannot_be_reached(use_database, error):
    engine = FailingEngine(error)
    client = use_database(Database(engine))

    response = client.get("/health/ready")

    assert engine.connects == 1
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "model": True, "database": False}
    # Neither the text of the exception nor anything else internal is sent.
    assert "secret" not in response.text and "brain_mri" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert_security_headers(response)


def test_ready_without_database_url(client):
    """Without DATABASE_URL the service is meant to run without a database: it is not a
    dependency, so it is not checked, and database is null ("not used")."""
    assert client.app.state.database is None

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "model": True, "database": None}
    assert response.headers["cache-control"] == "no-store"


def test_without_database_url_the_probe_is_not_asked(client, replace_state):
    class UnusedProbe:
        async def reachable(self, database):
            raise AssertionError("the database is not configured, so it is not checked")

    replace_state("readiness", UnusedProbe())

    assert client.get("/health/ready").status_code == 200


def test_not_ready_without_a_model_even_without_database_url(client, replace_state):
    replace_state("classifier", None)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "model": False, "database": None}


def test_a_database_that_does_not_answer_is_given_up_after_the_timeout(
    use_database, replace_state
):
    """SELECT 1 runs outside the event loop with a time limit (2 s in production): a
    hung database makes the answer 503, not a hung healthcheck. Only one check runs at
    a time, so probes during the hang wait for it instead of starting more threads."""
    engine = StalledEngine()
    client = use_database(Database(engine))
    probe = DatabaseProbe(timeout_s=0.2)
    replace_state("readiness", probe)
    try:
        started = time.perf_counter()
        answers = [client.get("/health/ready") for _ in range(3)]
        elapsed = time.perf_counter() - started

        assert [r.status_code for r in answers] == [503] * 3
        assert answers[0].json() == {"status": "not_ready", "model": True, "database": False}
        assert elapsed < 3
        assert engine.connects == 1
    finally:
        engine.release.set()
        probe.close()


def test_the_production_timeout_is_two_seconds():
    probe = DatabaseProbe()
    try:
        assert probe.timeout_s == 2.0
    finally:
        probe.close()


def test_ready_again_once_the_database_answers(use_database, database):
    client = use_database(Database(FailingEngine(OperationalError("SELECT 1", {}, None))))
    assert client.get("/health/ready").status_code == 503

    use_database(database)

    assert client.get("/health/ready").json() == READY


def test_only_changes_of_the_database_state_are_logged(
    use_database, database, replace_state, caplog
):
    """The error goes to the log, not the response; a probe repeated every few seconds
    while the database is down does not repeat it."""
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    probe = DatabaseProbe()
    client = use_database(Database(FailingEngine(RuntimeError(SECRET_ERROR))))
    replace_state("readiness", probe)
    try:
        for _ in range(3):
            client.get("/health/ready")
        use_database(database)
        client.get("/health/ready")
    finally:
        probe.close()

    assert caplog.text.count("Readiness: the database is unreachable") == 1
    assert SECRET_ERROR in caplog.text
    assert caplog.text.count("Readiness: the database answers again") == 1


def test_a_timeout_does_not_hide_the_error_the_slow_check_ends_with(
    use_database, replace_state, caplog
):
    """Seen in Docker with the db container stopped: the request gives up after 2 s and
    logs the outage; the check that was still connecting fails a second later, and its
    error, which says why, must be logged too. Later cycles of the outage stay quiet."""
    engine = StalledEngine()
    client = use_database(Database(engine))
    probe = DatabaseProbe(timeout_s=0.1)
    replace_state("readiness", probe)
    try:
        assert client.get("/health/ready").status_code == 503
        engine.release.set()
        for _ in range(50):
            if "OperationalError" in caplog.text:
                break
            time.sleep(0.05)
        # The engine now fails at once; the outage goes on without more log lines.
        for _ in range(3):
            assert client.get("/health/ready").status_code == 503
    finally:
        engine.release.set()
        probe.close()

    unreachable = [r.getMessage() for r in caplog.records if "unreachable" in r.getMessage()]
    assert len(unreachable) == 2
    assert "no answer within 0.1 s" in unreachable[0]
    assert "OperationalError" in unreachable[1] and "timed out" in unreachable[1]


# --- /health stays liveness ---------------------------------------------------------


def test_health_answers_without_asking_the_database(use_database):
    engine = FailingEngine(OperationalError("SELECT 1", {}, None))
    client = use_database(Database(engine))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert engine.connects == 0


# --- Neither path is limited, checked for cross-site or tied to a session -------------


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_health_paths_count_against_no_bucket(path):
    assert bucket_of("GET", path) is None
    assert bucket_of("HEAD", path) is None


def test_health_paths_skip_the_rate_limit_and_the_cross_site_check(
    limited, use_database, database  # noqa: F811
):
    clock, model, queue, connect = limited(rate_limit_read_per_minute=1)
    use_database(database)
    browser = connect()
    browser.get("/stats")
    assert browser.get("/stats").status_code == 429

    for path in HEALTH_PATHS:
        statuses = [browser.get(path, headers=FOREIGN_PAGE).status_code for _ in range(200)]
        assert statuses == [200] * 200, path


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_health_paths_need_no_session(path, use_database, database):
    anonymous = new_browser(use_database(database).app, origin=None)

    response = anonymous.get(path)

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_a_stale_session_cookie_is_ignored(path, use_database, database):
    """The session is not looked up, so the cookie is neither refused nor cleared."""
    browser = new_browser(use_database(database).app)
    browser.cookies.set("brain_mri_session", secrets.token_urlsafe(32))

    response = browser.get(path)

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


# --- API description ------------------------------------------------------------------


def test_openapi_describes_both_health_paths(client):
    paths = client.get("/openapi.json").json()["paths"]

    live, ready = paths["/health"]["get"], paths["/health/ready"]["get"]
    assert "liveness" in live["description"].lower()
    assert "readiness" in ready["description"].lower()
    assert set(ready["responses"]) == {"200", "503"}
    for code in ("200", "503"):
        schema = ready["responses"][code]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ReadinessResponse"}
    for operation in (live, ready):
        assert not {"401", "403", "429", "422"} & set(operation["responses"])

    readiness = client.get("/openapi.json").json()["components"]["schemas"]["ReadinessResponse"]
    assert set(readiness["properties"]) == {"status", "model", "database"}
    assert readiness["properties"]["status"]["enum"] == ["ready", "not_ready"]
    assert readiness["properties"]["model"]["type"] == "boolean"
    # null: the database is not used (DATABASE_URL is not set).
    database = readiness["properties"]["database"]
    assert database["anyOf"] == [{"type": "boolean"}, {"type": "null"}]
    assert "null" in database["description"]
    assert set(readiness["required"]) == {"status", "model", "database"}
