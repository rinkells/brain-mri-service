"""Requests per client per minute: the sliding window, the client key, and HTTP 429.

A fake clock drives the window, so no test waits for real time to pass.
"""

import ipaddress
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.model import Prediction
from app.rate_limit import (
    MAX_KEYS,
    SlidingWindowLimiter,
    bucket_of,
    build_rate_limiters,
    rate_limit_key,
)
from tests.helpers import encode_image, image_bytes, make_image

PROXY = "172.30.250.10"  # the frontend container in Compose
GATEWAY = "172.30.250.1"  # direct connections to the published API port
SCAN = image_bytes(512, 512)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- The window ------------------------------------------------------------------


def test_allows_the_limit_then_refuses_with_the_time_to_wait():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_s=60, clock=clock)

    assert limiter.hit("a") == 0
    clock.advance(10)
    assert limiter.hit("a") == 0
    clock.advance(10)

    # The first request is 20 s old: it leaves the window in 40 s.
    assert limiter.hit("a") == pytest.approx(40)


def test_the_window_slides_one_request_at_a_time():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_s=60, clock=clock)
    limiter.hit("a")  # t = 0
    clock.advance(30)
    limiter.hit("a")  # t = 30

    clock.advance(29.5)  # t = 59.5
    assert limiter.hit("a") == pytest.approx(0.5)
    clock.advance(0.5)  # t = 60: the first request is out of the window
    assert limiter.hit("a") == 0
    assert limiter.hit("a") == pytest.approx(30)  # the one from t = 30 is still in


def test_refused_requests_do_not_push_the_window_further():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=clock)
    limiter.hit("a")

    for _ in range(100):
        clock.advance(0.5)
        limiter.hit("a")

    clock.advance(10)  # 60 s after the only accepted request
    assert limiter.hit("a") == 0


def test_keys_are_counted_separately():
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=FakeClock())

    assert limiter.hit("a") == 0
    assert limiter.hit("a") > 0
    assert limiter.hit("b") == 0


def test_memory_per_key_is_bounded_by_the_limit():
    limiter = SlidingWindowLimiter(limit=5, window_s=60, clock=FakeClock())

    for _ in range(1000):
        limiter.hit("a")

    assert limiter.tracked("a") == 5


def test_the_number_of_keys_is_capped():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_s=60, max_keys=100, clock=clock)

    for n in range(150):
        limiter.hit(f"client-{n}")

    assert len(limiter) == 100
    # The least recently active keys were dropped; the newest are kept.
    assert limiter.tracked("client-149") == 1
    assert limiter.tracked("client-0") == 0


def test_keys_whose_window_has_passed_are_dropped():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=clock)
    for n in range(50):
        limiter.hit(f"client-{n}")

    clock.advance(61)
    limiter.hit("late")

    assert len(limiter) == 1


def test_default_key_cap():
    assert SlidingWindowLimiter(limit=1).max_keys == MAX_KEYS == 10_000


# --- Which requests count where ----------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "bucket"),
    [
        ("POST", "/predict", "predict"),
        ("DELETE", "/predictions/6f1c2a3e-0000-4000-8000-000000000001", "write"),
        ("GET", "/predictions", "read"),
        ("GET", "/stats", "read"),
        ("GET", "/predictions/6f1c2a3e-0000-4000-8000-000000000001", "read"),
        ("GET", "/docs", "read"),
        ("GET", "/health", None),
        ("HEAD", "/health", None),
    ],
)
def test_bucket_of_a_request(method, path, bucket):
    assert bucket_of(method, path) == bucket


def test_limiters_follow_the_settings(settings):
    custom = settings.model_copy(
        update={
            "rate_limit_predict_per_minute": 3,
            "rate_limit_write_per_minute": 4,
            "rate_limit_read_per_minute": 5,
        }
    )

    limiters = build_rate_limiters(custom)

    assert {name: limiter.limit for name, limiter in limiters.items()} == {
        "predict": 3,
        "write": 4,
        "read": 5,
    }


# --- The client key ----------------------------------------------------------------


def scope(peer: str | None, real_ip: str | None = None) -> dict:
    headers = [] if real_ip is None else [(b"x-real-ip", real_ip.encode())]
    return {
        "type": "http",
        "headers": headers,
        "client": None if peer is None else (peer, 40000),
    }


TRUSTED = frozenset({ipaddress.ip_address(PROXY)})


@pytest.mark.parametrize(
    ("peer", "real_ip", "trusted", "key"),
    [
        ("198.51.100.7", None, TRUSTED, "198.51.100.7"),
        # A peer that is not a trusted proxy cannot choose its key.
        (GATEWAY, "203.0.113.9", TRUSTED, GATEWAY),
        ("198.51.100.7", "203.0.113.9", TRUSTED, "198.51.100.7"),
        # The trusted proxy says who the client is.
        (PROXY, "203.0.113.9", TRUSTED, "203.0.113.9"),
        # Without TRUSTED_PROXIES the header is ignored, even from the proxy.
        (PROXY, "203.0.113.9", frozenset(), PROXY),
        # A malformed header from the proxy: the proxy's own address, one shared bucket.
        (PROXY, "203.0.113.9, 10.0.0.1", TRUSTED, PROXY),
        (PROXY, "not-an-ip", TRUSTED, PROXY),
        (PROXY, "", TRUSTED, PROXY),
        (PROXY, None, TRUSTED, PROXY),
    ],
    ids=[
        "direct",
        "gateway-spoofs",
        "client-spoofs",
        "trusted-proxy",
        "no-trusted-proxies",
        "proxy-list",
        "proxy-garbage",
        "proxy-empty",
        "proxy-missing",
    ],
)
def test_key_is_the_client_address(peer, real_ip, trusted, key):
    assert rate_limit_key(scope(peer, real_ip), trusted) == key


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:abcd::5", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
    ],
)
def test_ipv6_clients_are_grouped_by_their_64_prefix(address, key):
    assert rate_limit_key(scope(address), frozenset()) == key
    assert rate_limit_key(scope(PROXY, address), TRUSTED) == key


def test_a_peer_without_an_address_still_gets_a_key():
    assert rate_limit_key(scope("testclient"), TRUSTED) == "testclient"
    assert rate_limit_key(scope(None), TRUSTED) == "unknown"


# --- HTTP 429 --------------------------------------------------------------------------


class CountingClassifier:
    """Stands in for the model and counts how often it was asked."""

    def __init__(self, device) -> None:
        self.device = device
        self.calls = 0

    def predict(self, image, with_cam=False) -> Prediction:
        self.calls += 1
        return Prediction(
            "notumor", {"glioma": 0.01, "meningioma": 0.01, "notumor": 0.97, "pituitary": 0.01}
        )


class CountingInferenceLimiter:
    """Wraps the inference queue and counts the requests that entered it."""

    def __init__(self, real) -> None:
        self.real = real
        self.entered = 0

    @asynccontextmanager
    async def slot(self):
        self.entered += 1
        async with self.real.slot():
            yield


@pytest.fixture
def limited(client):
    """The shared app with rate limiting on, a fake clock and counting stand-ins.

    use(**settings) returns (clock, model, queue, connect), where connect(peer) gives a
    TestClient whose requests come from that address.
    """
    app = client.app
    saved = (
        app.state.settings,
        app.state.classifier,
        app.state.inference_limiter,
        getattr(app.state, "rate_limiters", None),
    )

    def use(**overrides):
        settings = saved[0].model_copy(update={"rate_limit_enabled": True, **overrides})
        clock = FakeClock()
        app.state.settings = settings
        app.state.rate_limiters = build_rate_limiters(settings, clock=clock)
        model = CountingClassifier(saved[1].device)
        app.state.classifier = model
        queue = CountingInferenceLimiter(saved[2])
        app.state.inference_limiter = queue

        def connect(peer: str = "198.51.100.7") -> TestClient:
            return TestClient(app, client=(peer, 40000))

        return clock, model, queue, connect

    yield use
    (
        app.state.settings,
        app.state.classifier,
        app.state.inference_limiter,
        app.state.rate_limiters,
    ) = saved


def post_scan(client, data=SCAN, filename="scan.png", content_type="image/png", **headers):
    return client.post(
        "/predict", files={"file": (filename, data, content_type)}, headers=headers or None
    )


def test_429_after_the_limit_with_retry_after_and_the_usual_error_format(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=2)
    browser = connect()

    assert post_scan(browser).status_code == 200
    clock.advance(15)
    assert post_scan(browser).status_code == 200
    clock.advance(5)
    refused = post_scan(browser)

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "40"
    assert refused.json() == {"detail": "Забагато запитів. Спробуйте ще раз через 40 с."}
    for name in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert name in refused.headers


def test_a_refused_request_touches_neither_the_model_nor_the_queue(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=1)
    browser = connect()
    post_scan(browser)

    assert post_scan(browser).status_code == 429

    assert (model.calls, queue.entered) == (1, 1)


def test_retry_after_is_whole_seconds_and_at_least_one(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=1)
    browser = connect()
    post_scan(browser)

    clock.advance(59.7)
    assert post_scan(browser).headers["Retry-After"] == "1"
    clock.advance(0.3)
    assert post_scan(browser).status_code == 200


def test_the_window_resets_after_a_minute(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=2)
    browser = connect()
    post_scan(browser)
    post_scan(browser)
    assert post_scan(browser).status_code == 429

    clock.advance(60)

    assert post_scan(browser).status_code == 200


def test_every_predict_attempt_counts_even_a_refused_file(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=3)
    browser = connect()

    assert post_scan(browser, b"not an image").status_code == 400
    assert post_scan(browser, encode_image(make_image(64, 64), "GIF")).status_code == 415
    assert post_scan(browser, b"x" * (2 * 1024 * 1024)).status_code == 413

    assert post_scan(browser).status_code == 429
    assert model.calls == 0


def test_clients_are_limited_separately(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=1)
    first, second = connect("198.51.100.7"), connect("198.51.100.8")

    assert post_scan(first).status_code == 200
    assert post_scan(first).status_code == 429
    assert post_scan(second).status_code == 200


def test_reads_writes_and_predictions_have_their_own_buckets(limited):
    clock, model, queue, connect = limited(
        rate_limit_predict_per_minute=1, rate_limit_read_per_minute=2, rate_limit_write_per_minute=1
    )
    browser = connect()

    assert post_scan(browser).status_code == 200
    assert post_scan(browser).status_code == 429
    # No database in the shared app: 503, but the request passed the limit.
    assert browser.get("/stats").status_code == 503
    assert browser.get("/predictions").status_code == 503
    assert browser.get("/stats").status_code == 429
    gone = "/predictions/6f1c2a3e-0000-4000-8000-000000000001"
    assert browser.delete(gone).status_code == 503
    assert browser.delete(gone).status_code == 429


def test_health_is_never_limited(limited):
    clock, model, queue, connect = limited(rate_limit_read_per_minute=1)
    browser = connect()
    browser.get("/stats")
    assert browser.get("/stats").status_code == 429

    assert [browser.get("/health").status_code for _ in range(20)] == [200] * 20


def test_the_origin_check_comes_first_and_its_refusals_do_not_count(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=2)
    browser = connect()
    evil = {"Origin": "http://evil.example"}

    assert post_scan(browser).status_code == 200
    assert [post_scan(browser, **evil).status_code for _ in range(3)] == [403] * 3
    assert post_scan(browser).status_code == 200
    # Over the limit now, yet a foreign page still gets 403, not 429.
    assert post_scan(browser, **evil).status_code == 403
    assert post_scan(browser).status_code == 429


def test_the_host_check_comes_first_and_its_refusals_do_not_count(limited):
    clock, model, queue, connect = limited(rate_limit_read_per_minute=1)
    browser = connect()

    assert browser.get("/stats", headers={"Host": "evil.example"}).status_code == 400
    assert browser.get("/stats").status_code == 503
    assert browser.get("/stats").status_code == 429


def test_a_trusted_proxy_names_the_client(limited):
    clock, model, queue, connect = limited(
        rate_limit_predict_per_minute=1, trusted_proxies=[PROXY]
    )
    nginx = connect(PROXY)

    assert post_scan(nginx, **{"X-Real-IP": "203.0.113.1"}).status_code == 200
    assert post_scan(nginx, **{"X-Real-IP": "203.0.113.2"}).status_code == 200
    assert post_scan(nginx, **{"X-Real-IP": "203.0.113.1"}).status_code == 429


def test_an_untrusted_peer_cannot_escape_its_bucket_with_x_real_ip(limited):
    clock, model, queue, connect = limited(
        rate_limit_predict_per_minute=1, trusted_proxies=[PROXY]
    )
    direct = connect(GATEWAY)

    assert post_scan(direct, **{"X-Real-IP": "203.0.113.1"}).status_code == 200
    assert post_scan(direct, **{"X-Real-IP": "203.0.113.2"}).status_code == 429
    assert post_scan(direct).status_code == 429


def test_without_trusted_proxies_x_real_ip_is_ignored(limited):
    clock, model, queue, connect = limited(rate_limit_predict_per_minute=1)
    nginx = connect(PROXY)

    assert post_scan(nginx, **{"X-Real-IP": "203.0.113.1"}).status_code == 200
    assert post_scan(nginx, **{"X-Real-IP": "203.0.113.2"}).status_code == 429


def test_a_malformed_x_real_ip_from_the_proxy_shares_the_proxy_bucket(limited):
    clock, model, queue, connect = limited(
        rate_limit_predict_per_minute=1, trusted_proxies=[PROXY]
    )
    nginx = connect(PROXY)

    assert post_scan(nginx, **{"X-Real-IP": "garbage"}).status_code == 200
    assert post_scan(nginx, **{"X-Real-IP": "1.2.3.4, 5.6.7.8"}).status_code == 429
    assert post_scan(nginx, **{"X-Real-IP": "203.0.113.1"}).status_code == 200


def test_switched_off_nothing_is_limited(limited):
    clock, model, queue, connect = limited(
        rate_limit_enabled=False, rate_limit_predict_per_minute=1
    )
    browser = connect()

    assert [post_scan(browser).status_code for _ in range(5)] == [200] * 5


def test_openapi_documents_429_on_every_limited_route(client):
    paths = client.get("/openapi.json").json()["paths"]

    for path, method in [
        ("/predict", "post"),
        ("/predictions", "get"),
        ("/predictions/{prediction_id}", "get"),
        ("/predictions/{prediction_id}", "delete"),
        ("/stats", "get"),
    ]:
        response = paths[path][method]["responses"]["429"]
        assert "Retry-After" in response["headers"], (path, method)
    assert "429" not in paths["/health"]["get"]["responses"]
