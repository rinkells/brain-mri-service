"""/predict under load: a limit on simultaneous predictions, a bounded queue, and a
/health that keeps answering while predictions are slow.

The model is replaced with a stand-in that blocks until the test releases it, so the
tests control exactly how many predictions are running.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import anyio
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.model import Prediction
from tests.helpers import image_bytes

SCAN = image_bytes(512, 512)
# Upper bound for anything a test waits for; reached only when the code is wrong.
WAIT_S = 10
BUSY_DETAIL = "Сервер зараз зайнятий іншими знімками. Спробуйте ще раз за кілька секунд."


class SlowClassifier:
    """Stands in for the model: each prediction blocks until `release` is set."""

    def __init__(self, device) -> None:
        self.device = device
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.started = 0
        self.running = 0
        self.max_running = 0

    def predict(self, image, with_cam=False) -> Prediction:
        with self._lock:
            self.started += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            self.release.wait(WAIT_S)
        finally:
            with self._lock:
                self.running -= 1
        return Prediction(
            "notumor", {"glioma": 0.01, "meningioma": 0.01, "notumor": 0.97, "pituitary": 0.01}
        )


def post_scan(client):
    return client.post("/predict", files={"file": ("scan.png", SCAN, "image/png")})


def wait_until(condition, timeout: float = WAIT_S) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("The expected state was not reached in time")
        time.sleep(0.01)


def wait_until_steady(read, quiet_s: float = 0.5, timeout: float = WAIT_S) -> None:
    """Wait until read() has not changed for quiet_s seconds."""
    deadline = time.monotonic() + timeout
    last, since = read(), time.monotonic()
    while time.monotonic() - since < quiet_s:
        if time.monotonic() > deadline:
            raise AssertionError("The value kept changing")
        time.sleep(0.02)
        if (current := read()) != last:
            last, since = current, time.monotonic()


@pytest.fixture
def slow_service(settings):
    """Start an app with a slow model and the given settings.

    Returns the client, the stand-in model and a thread pool for parallel requests.
    At the end the model is released first, then the requests finish, then the app stops.
    """
    with ExitStack() as stack:

        def start(**overrides):
            client = stack.enter_context(
                TestClient(create_app(settings.model_copy(update=overrides)))
            )
            model = SlowClassifier(client.app.state.classifier.device)
            client.app.state.classifier = model
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=64))
            stack.callback(model.release.set)
            return client, model, pool

        yield start


def test_health_answers_while_slow_predictions_fill_the_thread_pool(slow_service):
    client, model, pool = slow_service()
    worker_threads = client.portal.call(
        lambda: anyio.to_thread.current_default_thread_limiter().total_tokens
    )
    for _ in range(worker_threads + 5):
        pool.submit(post_scan, client)
    wait_until(lambda: model.started >= 1)
    wait_until_steady(lambda: model.started)

    started = time.perf_counter()
    response = pool.submit(client.get, "/health").result(timeout=2)

    assert response.status_code == 200
    assert time.perf_counter() - started < 1


def test_no_more_than_max_concurrent_inferences_run_at_once(slow_service):
    client, model, pool = slow_service(max_concurrent_inferences=2, max_queue=10)

    requests = [pool.submit(post_scan, client) for _ in range(6)]
    wait_until(lambda: model.started >= 2)
    wait_until_steady(lambda: model.started)

    assert model.started == 2  # the other four wait for a free slot
    wait_until(lambda: client.app.state.inference_limiter.in_flight == 6)
    model.release.set()
    assert [r.result(timeout=WAIT_S).status_code for r in requests] == [200] * 6
    assert model.max_running == 2


def test_requests_beyond_the_queue_get_503_with_retry_after(slow_service):
    client, model, pool = slow_service(max_concurrent_inferences=1, max_queue=1)
    running = pool.submit(post_scan, client)
    wait_until(lambda: model.started == 1)
    queued = pool.submit(post_scan, client)
    wait_until_steady(lambda: model.started)
    assert model.started == 1  # the second request waits instead of running
    wait_until(lambda: client.app.state.inference_limiter.in_flight == 2)

    rejected = pool.submit(post_scan, client).result(timeout=WAIT_S)

    assert rejected.status_code == 503
    assert rejected.headers["Retry-After"] == "5"
    assert rejected.json() == {"detail": BUSY_DETAIL}
    assert model.started == 1
    model.release.set()
    assert running.result(timeout=WAIT_S).status_code == 200
    assert queued.result(timeout=WAIT_S).status_code == 200


def test_queue_frees_up_after_the_rush(slow_service):
    client, model, pool = slow_service(max_concurrent_inferences=1, max_queue=0)
    first = pool.submit(post_scan, client)
    wait_until(lambda: model.started == 1)
    assert pool.submit(post_scan, client).result(timeout=WAIT_S).status_code == 503

    model.release.set()

    assert first.result(timeout=WAIT_S).status_code == 200
    assert post_scan(client).status_code == 200


def test_openapi_documents_503_for_predict(client):
    responses = client.get("/openapi.json").json()["paths"]["/predict"]["post"]["responses"]

    assert "503" in responses
