"""GET /health (liveness) and GET /health/ready (readiness).

/health says only that the process answers; /health/ready also checks that the model is
loaded and the database answers SELECT 1, and is what the Compose healthcheck of api asks.
"""

import asyncio
import logging
import secrets
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.database import DISPOSE_TIMEOUT_S, PROBE_CONNECT_TIMEOUT_S, Database
from app.main import create_app
from app.rate_limit import bucket_of
from app.readiness import CHECK_THREAD_NAME, DatabaseProbe
from tests.helpers import new_browser
from tests.test_rate_limit import limited  # noqa: F401  (fixture)

READY = {"status": "ready", "model": True, "database": True}
DATABASE_DOWN = {"status": "not_ready", "model": True, "database": False}
# Words of the exception the stand-in engine raises; none of them may reach a response.
SECRET_ERROR = 'password authentication failed for user "brain_mri" at db:5432 (secret-7Qx)'
FOREIGN_PAGE = {
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "no-cors",
    "Origin": "https://evil.example",
}
HEALTH_PATHS = ("/health", "/health/ready")
# Test servers speak plain TCP: no TLS or GSSAPI negotiation first.
NO_ENCRYPTION = "sslmode=disable&gssencmode=disable"


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
    """An engine whose connect() hangs until released, like a database that never answers
    or a check stuck in name resolution; with stall_dispose, dispose() hangs too.

    Like a driver, it gives up a moment after being released (GIVE_UP_S), so a test that
    releases it must wait for its check to end (see no_check_outlives_its_test)."""

    GIVE_UP_S = 0.5

    def __init__(self, stall_dispose: bool = False) -> None:
        self.release = threading.Event()
        self.connects = 0
        self.stall_dispose = stall_dispose

    def connect(self):
        self.connects += 1
        self.release.wait(timeout=30)
        time.sleep(self.GIVE_UP_S)
        raise OperationalError("SELECT 1", {}, Exception("timed out"))

    def dispose(self) -> None:
        if self.stall_dispose:
            self.release.wait(timeout=30)


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
    before = set(live_checks())
    engine = StalledEngine()
    client = use_database(Database(engine))
    probe = DatabaseProbe(timeout_s=0.2)
    replace_state("readiness", probe)
    try:
        started = time.perf_counter()
        answers = [client.get("/health/ready") for _ in range(3)]
        elapsed = time.perf_counter() - started

        assert [r.status_code for r in answers] == [503] * 3
        assert answers[0].json() == DATABASE_DOWN
        assert elapsed < 3
        assert engine.connects == 1
    finally:
        engine.release.set()
        assert wait_until(lambda: not live_checks(before))
        probe.close()


def test_the_production_timeout_and_cache_are_two_seconds():
    probe = DatabaseProbe()
    try:
        assert (probe.timeout_s, probe.ttl_s) == (2.0, 2.0)
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


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def execute(self, statement) -> None:
        pass


class AnsweringEngine:
    """An engine whose SELECT 1 succeeds; with `gate`, connect() first waits for it."""

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.connects = 0
        self.answered = 0

    def connect(self):
        self.connects += 1
        if self.gate is not None:
            self.gate.wait(timeout=30)
        self.answered += 1
        return FakeConnection()

    def dispose(self) -> None:
        pass


def wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def readiness_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Readiness:")]


def live_checks(other_than=frozenset()) -> list[threading.Thread]:
    """Threads of readiness checks still running, except those in other_than."""
    return [
        t
        for t in threading.enumerate()
        if t.name == CHECK_THREAD_NAME and t.is_alive() and t not in other_than
    ]


@pytest.fixture(autouse=True)
def no_check_outlives_its_test():
    """A check still running after its test would log "Readiness: ..." into the caplog of
    a later one. A thread that has just answered gets a moment to exit; a test that
    releases a stalled engine must wait for its check itself."""
    before = set(live_checks())
    yield
    assert wait_until(lambda: not live_checks(before), timeout=0.2), (
        "a readiness check outlived its test"
    )


# --- The check: a connection of its own, time limits, a short cache ---------------------


def test_the_check_does_not_wait_for_the_shared_pool(tmp_path, use_database, replace_state):
    """Predictions and the history may hold every connection of the shared pool; the
    check opens its own and still answers at once."""
    database = Database.from_url(f"sqlite:///{tmp_path / 'history.db'}")
    pool = database.engine.pool
    held = [database.engine.connect() for _ in range(pool.size() + pool._max_overflow)]
    probe = DatabaseProbe()
    replace_state("readiness", probe)
    try:
        client = use_database(database)
        started = time.perf_counter()

        response = client.get("/health/ready")

        assert response.json() == READY
        assert time.perf_counter() - started < 1.5
        assert database.probe_engine is not database.engine
        assert isinstance(database.probe_engine.pool, NullPool)
    finally:
        for connection in held:
            connection.close()
        probe.close()
        database.dispose()


def test_postgres_checks_open_a_new_connection_with_a_limit_on_every_stage():
    database = Database.from_url("postgresql+psycopg://user:secret@127.0.0.1:1/brain_mri")
    seen = {}

    @event.listens_for(database.probe_engine, "do_connect")
    def capture(dialect, record, cargs, cparams):
        seen.update(cparams)
        raise RuntimeError("not connecting in this test")

    try:
        with pytest.raises(RuntimeError):
            database.probe_engine.connect()
    finally:
        database.dispose()

    # A new connection for each check, closed after it: nothing is pooled.
    assert isinstance(database.probe_engine.pool, NullPool)
    assert not isinstance(database.engine.pool, NullPool)
    assert seen["connect_timeout"] == 2
    assert "-c statement_timeout=1000" in seen["options"]
    assert seen["tcp_user_timeout"] == 2000
    assert seen["application_name"] == "brain-mri-readiness"
    # A SELECT 1 the server acknowledged but never answered, because the network went
    # away for long: keepalive probes give up on it after about 2 + 3 × 1 s, instead of
    # the OS default of about 2 hours.
    assert seen["keepalives"] == 1
    assert seen["keepalives_idle"] == 2
    assert seen["keepalives_interval"] == 1
    assert seen["keepalives_count"] == 3


@pytest.fixture
def silent_server():
    """A TCP server that accepts connections and never sends a byte: like a paused
    Postgres, whose kernel still completes the handshake. Yields its port."""
    server = socket.create_server(("127.0.0.1", 0))
    accepted = []
    stop = threading.Event()

    def serve():
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                accepted.append(server.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    thread.join(timeout=2)
    for connection in accepted:
        connection.close()
    server.close()


def test_a_server_that_never_answers_the_connection_is_given_up(
    silent_server, use_database, replace_state, caplog
):
    """connect_timeout: the check ends with an error after 2 s, not when TCP gives up."""
    database = Database.from_url(
        f"postgresql+psycopg://u:p@127.0.0.1:{silent_server}/db?{NO_ENCRYPTION}"
    )
    probe = DatabaseProbe()
    replace_state("readiness", probe)
    client = use_database(database)
    try:
        started = time.perf_counter()
        assert client.get("/health/ready").status_code == 503
        assert wait_until(lambda: readiness_lines(caplog), timeout=5)
        assert time.perf_counter() - started < PROBE_CONNECT_TIMEOUT_S + 1.0
    finally:
        probe.close()
        database.dispose()
    assert "OperationalError" in readiness_lines(caplog)[0]


def test_a_fresh_result_is_reused_for_two_seconds(use_database, replace_state):
    clock = FakeClock()
    engine = AnsweringEngine()
    probe = DatabaseProbe(clock=clock)
    replace_state("readiness", probe)
    client = use_database(Database(engine))
    try:
        assert client.get("/health/ready").json() == READY
        assert client.get("/health/ready").json() == READY
        clock.advance(1.9)
        assert client.get("/health/ready").json() == READY
        assert engine.connects == 1

        clock.advance(0.1)
        assert client.get("/health/ready").json() == READY
        assert engine.connects == 2
    finally:
        probe.close()


def test_a_failure_is_reused_for_two_seconds_too(use_database, replace_state):
    clock = FakeClock()
    engine = FailingEngine(OperationalError("SELECT 1", {}, None))
    probe = DatabaseProbe(clock=clock)
    replace_state("readiness", probe)
    client = use_database(Database(engine))
    try:
        assert [client.get("/health/ready").status_code for _ in range(50)] == [503] * 50
        assert engine.connects == 1
        clock.advance(2.0)
        assert client.get("/health/ready").status_code == 503
        assert engine.connects == 2
    finally:
        probe.close()


def test_a_slow_but_successful_check_reports_no_outage(use_database, replace_state, caplog):
    """The request gives up after its timeout, but the check succeeds a moment later: the
    state stays "reachable" and nothing is logged, neither an outage nor a recovery."""
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    clock = FakeClock()
    gate = threading.Event()
    gate.set()
    engine = AnsweringEngine(gate)
    probe = DatabaseProbe(timeout_s=0.1, clock=clock)
    replace_state("readiness", probe)
    client = use_database(Database(engine))
    try:
        assert client.get("/health/ready").json() == READY
        clock.advance(5)
        gate.clear()

        assert client.get("/health/ready").status_code == 503
        gate.set()
        assert wait_until(lambda: engine.answered == 2)

        assert client.get("/health/ready").json() == READY
        assert engine.connects == 2
    finally:
        gate.set()
        probe.close()
    assert readiness_lines(caplog) == []


def test_a_request_that_times_out_writes_nothing_to_the_log(
    use_database, replace_state, caplog
):
    """Only the check writes the state and the log. Its own error, once it has one, is
    logged once; a timed-out request adds nothing."""
    engine = StalledEngine()
    probe = DatabaseProbe(timeout_s=0.1)
    replace_state("readiness", probe)
    client = use_database(Database(engine))
    try:
        assert client.get("/health/ready").status_code == 503
        assert readiness_lines(caplog) == []
        assert probe.last_result() is None

        engine.release.set()
        assert wait_until(lambda: readiness_lines(caplog))
        assert [client.get("/health/ready").status_code for _ in range(3)] == [503] * 3
    finally:
        engine.release.set()
        probe.close()

    lines = readiness_lines(caplog)
    assert len(lines) == 1
    assert "OperationalError" in lines[0] and "timed out" in lines[0]


def test_a_stuck_check_is_the_only_one_until_it_ends(use_database, replace_state):
    """At most one check is alive. While it hangs where no limit reaches it (e.g. name
    resolution), requests wait for it up to the timeout and get 503 without starting
    another, however long it hangs; once it ends, the next request starts a new one."""
    before = set(live_checks())
    clock = FakeClock()
    engine = StalledEngine()
    probe = DatabaseProbe(timeout_s=0.05, clock=clock)
    replace_state("readiness", probe)
    client = use_database(Database(engine))
    try:
        for _ in range(5):
            assert client.get("/health/ready").json() == DATABASE_DOWN
            clock.advance(60)
        assert engine.connects == 1
        assert len(live_checks(before)) == 1

        engine.release.set()
        assert wait_until(lambda: probe.last_result() is False)
        clock.advance(60)
        assert client.get("/health/ready").json() == DATABASE_DOWN
        assert engine.connects == 2
    finally:
        engine.release.set()
        assert wait_until(lambda: not live_checks(before))
        probe.close()


def test_close_does_not_wait_for_a_stuck_check(use_database, replace_state):
    before = set(live_checks())
    engine = StalledEngine()
    probe = DatabaseProbe(timeout_s=0.05)
    replace_state("readiness", probe)
    client = use_database(Database(engine))
    try:
        client.get("/health/ready")
        checks = live_checks(before)
        started = time.perf_counter()
        probe.close()
        assert time.perf_counter() - started < 0.2
        # Never waited for at exit either, so a stuck check cannot hold the process.
        assert len(checks) == 1 and checks[0].daemon
        # A closed probe starts no more checks.
        assert client.get("/health/ready").json() == DATABASE_DOWN
        assert engine.connects == 1
    finally:
        engine.release.set()
        assert wait_until(lambda: not live_checks(before))


def test_shutdown_with_a_stuck_check_does_not_block_the_event_loop(settings, tmp_path):
    """The lifespan neither waits for a stuck check nor closes the engines in the event
    loop: it disposes of them in a thread of its own, waiting DISPOSE_TIMEOUT_S at most,
    while the loop keeps running other tasks."""
    app = create_app(
        settings.model_copy(update={"database_url": f"sqlite:///{tmp_path / 'history.db'}"})
    )
    before = set(live_checks())
    stalled = StalledEngine(stall_dispose=True)

    async def shut_down_while_ticking() -> tuple[float, float]:
        gaps = []

        async def tick() -> None:
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        async with app.router.lifespan_context(app):
            app.state.readiness = DatabaseProbe(timeout_s=0.05)
            app.state.database.probe_engine = stalled
            assert await app.state.readiness.reachable(app.state.database) is False
            ticker = asyncio.create_task(tick())
            await asyncio.sleep(0.05)
            started = time.perf_counter()
        took = time.perf_counter() - started
        # One more tick, so a gap that lasted until now is recorded too.
        await asyncio.sleep(0.03)
        ticker.cancel()
        return max(gaps), took

    try:
        longest_gap, took = asyncio.run(shut_down_while_ticking())
    finally:
        stalled.release.set()
        assert wait_until(lambda: not live_checks(before))

    assert longest_gap < 0.2
    assert took < DISPOSE_TIMEOUT_S + 0.5


def test_shutdown_closes_the_probe_and_both_engines(settings, tmp_path, monkeypatch):
    app = create_app(
        settings.model_copy(update={"database_url": f"sqlite:///{tmp_path / 'history.db'}"})
    )
    closed = []

    def record(name, target, method):
        original = getattr(target, method)

        def recorded():
            closed.append(name)
            return original()

        monkeypatch.setattr(target, method, recorded)

    with TestClient(app) as client:
        assert client.get("/health/ready").json() == READY
        state = app.state
        record("probe", state.readiness, "close")
        record("engine", state.database.engine, "dispose")
        record("probe_engine", state.database.probe_engine, "dispose")

    # The probe first, so no check is left using its engine when the engines go.
    assert closed == ["probe", "engine", "probe_engine"]


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
