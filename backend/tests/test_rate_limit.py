"""Requests per client per minute: the sliding window, the client key, and HTTP 429.

A fake clock drives the window, so no test waits for real time to pass.
"""

import ipaddress
from collections import OrderedDict
from contextlib import asynccontextmanager

import argon2
import pytest
from fastapi.testclient import TestClient

from app.model import Prediction
from app.rate_limit import (
    MAX_KEYS,
    SlidingWindowLimiter,
    bucket_of,
    build_login_failures,
    build_rate_limiters,
    rate_limit_key,
)
from tests.helpers import (
    TEST_ORIGIN,
    TEST_PASSWORD,
    add_user,
    encode_image,
    image_bytes,
    make_image,
)

PROXY = "172.30.250.10"  # the frontend container in Compose
GATEWAY = "172.30.250.128"  # direct connections to the published API port (seen in Compose)
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


class CountingOrderedDict(OrderedDict):
    """Counts every entry an iteration over it looks at."""

    visits = 0

    def _count(self, entries):
        for entry in entries:
            self.visits += 1
            yield entry

    def __iter__(self):
        return self._count(super().__iter__())

    def keys(self):
        return self._count(super().keys())

    def values(self):
        return self._count(super().values())

    def items(self):
        return self._count(super().items())


def full_limiter(clock, max_keys=1000):
    limiter = SlidingWindowLimiter(limit=1, window_s=60, max_keys=max_keys, clock=clock)
    for n in range(max_keys):
        limiter.hit(f"client-{n}")
    # Internal on purpose: count how many tracked keys each request looks at.
    limiter._hits = CountingOrderedDict(limiter._hits)
    return limiter


def test_a_new_key_in_a_full_table_evicts_the_oldest_without_walking_the_table():
    clock = FakeClock()
    limiter = full_limiter(clock)

    for n in range(50):
        limiter.hit(f"new-{n}")

    assert limiter._hits.visits == 0
    assert len(limiter) == 1000
    assert [limiter.tracked(f"client-{n}") for n in (0, 49, 50)] == [0, 0, 1]
    assert limiter.tracked("new-49") == 1


def test_stale_keys_are_swept_at_most_once_per_window():
    clock = FakeClock()
    limiter = full_limiter(clock)
    clock.advance(30)
    limiter.hit("before-the-window-ends")
    assert limiter._hits.visits == 0

    clock.advance(31)  # one window after the table was filled
    limiter.hit("after")
    first_sweep = limiter._hits.visits
    for n in range(100):
        limiter.hit(f"later-{n}")

    assert first_sweep > 0  # the stale keys were looked at once ...
    assert limiter._hits.visits == first_sweep  # ... and not again within the window
    assert limiter.tracked("client-0") == 0 and limiter.tracked("before-the-window-ends") == 1


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
        # Only signing in and registering share the strict auth bucket. GET /auth/me runs
        # on every page load, so it is an ordinary read; logout and deletion are writes.
        ("POST", "/auth/register", "auth"),
        ("POST", "/auth/login", "auth"),
        ("POST", "/auth/logout", "write"),
        ("GET", "/auth/me", "read"),
        ("DELETE", "/auth/me", "write"),
        ("GET", "/auth/login", "read"),
        ("GET", "/auth/no-such-path", "read"),
        ("GET", "/authors", "read"),
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
            "rate_limit_auth_per_minute": 6,
        }
    )

    limiters = build_rate_limiters(custom)

    assert {name: limiter.limit for name, limiter in limiters.items()} == {
        "predict": 3,
        "write": 4,
        "read": 5,
        "auth": 6,
    }


def test_the_failed_sign_in_limit_follows_the_settings(settings):
    custom = settings.model_copy(
        update={"login_failures_max": 7, "login_failures_window_minutes": 20}
    )

    limiter = build_login_failures(custom)

    assert (limiter.limit, limiter.window_s) == (7, 20 * 60)


def test_retry_after_looks_without_counting():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_s=60, clock=clock)

    assert limiter.retry_after("a") == 0
    limiter.hit("a")
    clock.advance(10)
    limiter.hit("a")

    assert limiter.retry_after("a") == pytest.approx(50)
    assert limiter.retry_after("a") == pytest.approx(50)
    assert limiter.tracked("a") == 2
    assert limiter.retry_after("unknown") == 0 and len(limiter) == 1


def test_forget_starts_a_key_afresh():
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=FakeClock())
    limiter.hit("a")

    limiter.forget("a")
    limiter.forget("never-seen")

    assert limiter.hit("a") == 0


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


@pytest.mark.parametrize(
    "peer",
    ["::ffff:172.30.250.10", "::FFFF:172.30.250.10", "0:0:0:0:0:ffff:ac1e:fa0a"],
    ids=["mapped", "mapped-upper-case", "mapped-hex"],
)
def test_a_proxy_on_a_dual_stack_socket_is_still_trusted(peer):
    """With `uvicorn --host ::` nginx connects as ::ffff:172.30.250.10."""
    assert rate_limit_key(scope(peer, "203.0.113.9"), TRUSTED) == "203.0.113.9"


def test_an_ipv4_mapped_x_real_ip_counts_as_the_ipv4_client():
    assert rate_limit_key(scope(PROXY, "::ffff:203.0.113.9"), TRUSTED) == "203.0.113.9"


def test_a_scoped_ipv6_proxy_is_trusted_without_its_zone():
    trusted = frozenset({ipaddress.ip_address("fe80::10")})

    assert rate_limit_key(scope("fe80::10%eth0", "203.0.113.9"), trusted) == "203.0.113.9"


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
        app.state.login_failures,
    )

    def use(**overrides):
        settings = saved[0].model_copy(update={"rate_limit_enabled": True, **overrides})
        clock = FakeClock()
        app.state.settings = settings
        app.state.rate_limiters = build_rate_limiters(settings, clock=clock)
        app.state.login_failures = build_login_failures(settings, clock=clock)
        model = CountingClassifier(saved[1].device)
        app.state.classifier = model
        queue = CountingInferenceLimiter(saved[2])
        app.state.inference_limiter = queue

        def connect(peer: str = "198.51.100.7", origin: str | None = None) -> TestClient:
            """A client from that address; with `origin`, it sends Origin like a page."""
            headers = {"Origin": origin} if origin else None
            return TestClient(app, client=(peer, 40000), headers=headers)

        return clock, model, queue, connect

    yield use
    (
        app.state.settings,
        app.state.classifier,
        app.state.inference_limiter,
        app.state.rate_limiters,
        app.state.login_failures,
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


def test_a_cross_site_request_counts_for_nothing_and_never_reaches_the_model(limited):
    """A page of another site in the user's browser cannot use up the shared limits."""
    clock, model, queue, connect = limited(
        rate_limit_read_per_minute=1, rate_limit_predict_per_minute=1
    )
    browser = connect()
    foreign = {"Sec-Fetch-Site": "cross-site"}

    assert [browser.get("/stats", headers=foreign).status_code for _ in range(5)] == [403] * 5
    assert [post_scan(browser, **foreign).status_code for _ in range(5)] == [403] * 5
    assert (model.calls, queue.entered) == (0, 0)
    # The buckets are untouched: the first own request of each kind still passes.
    assert browser.get("/stats").status_code == 503
    assert post_scan(browser).status_code == 200
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
        ("/auth/register", "post"),
        ("/auth/login", "post"),
        ("/auth/logout", "post"),
        ("/auth/me", "get"),
        ("/auth/me", "delete"),
    ]:
        response = paths[path][method]["responses"]["429"]
        assert "Retry-After" in response["headers"], (path, method)
    assert "429" not in paths["/health"]["get"]["responses"]


# --- /auth/*: a bucket of their own, and failed sign-ins per name and address -----------

WRONG = "wrong-horse-22"


def too_many_failures(seconds: int) -> dict:
    return {"detail": f"Забагато невдалих спроб входу. Спробуйте ще раз через {seconds} с."}


@pytest.fixture
def accounts(use_database, database, password_hash):
    """The shared app with a database that holds alice (password TEST_PASSWORD).

    use_database runs before `limited`, so the fake clock of `limited` drives the count
    of failed sign-ins.
    """
    use_database(database)
    add_user(database, "alice", password_hash)
    return database


def login(client, username="alice", password=TEST_PASSWORD, **headers):
    return client.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers=headers or None,
    )


def test_auth_requests_have_their_own_bucket(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=2)
    browser = connect(origin=TEST_ORIGIN)

    assert login(browser).status_code == 200
    # Neither of these counts against the auth bucket.
    assert browser.get("/auth/me").status_code == 200
    assert browser.post("/auth/logout").status_code == 204
    assert login(browser).status_code == 200
    refused = browser.post("/auth/register", json={"username": "bob", "password": TEST_PASSWORD})

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert refused.json() == {"detail": "Забагато запитів. Спробуйте ще раз через 60 с."}
    assert refused.headers["cache-control"] == "no-store"
    # The other buckets are untouched.
    assert post_scan(browser).status_code == 200
    assert browser.get("/predictions").status_code == 200  # signed in by the second login
    clock.advance(60)
    assert login(browser).status_code == 200


def test_auth_refusals_by_origin_do_not_count(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1)
    browser = connect()

    for _ in range(3):
        assert login(browser, Origin="https://evil.example").status_code == 403
    assert login(browser).status_code == 200
    assert login(connect()).status_code == 429


def test_failed_logins_are_limited_per_name_and_address(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)

    for _ in range(5):
        clock.advance(60)
        assert login(connect(), password=WRONG).status_code == 401
    refused = login(connect())  # the right password, but too late

    # The first failure was 4 minutes ago: it leaves the 15-minute window in 11.
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "660"
    assert refused.json() == too_many_failures(660)
    assert "set-cookie" not in refused.headers
    # Another name from this address, and this name from another address, go on.
    assert login(connect(), "bob", WRONG).status_code == 401
    assert login(connect("198.51.100.8")).status_code == 200
    clock.advance(660)
    assert login(connect()).status_code == 200


def test_unknown_names_are_limited_like_real_ones(accounts, limited):
    """Otherwise the limit itself would tell which names exist."""
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)

    for name in ("alice", "nobody"):
        for _ in range(5):
            assert login(connect(), name, WRONG).status_code == 401
        assert login(connect(), name, WRONG).json() == too_many_failures(900)


def test_the_case_of_the_name_does_not_open_a_new_window(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)

    for name in ("alice", "ALICE", "Alice", "aLiCe", "alicE"):
        assert login(connect(), name, WRONG).status_code == 401

    assert login(connect(), "ALIce").status_code == 429


def test_a_successful_login_forgets_the_earlier_failures(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)
    for _ in range(4):
        login(connect(), password=WRONG)

    assert login(connect()).status_code == 200

    assert [login(connect(), password=WRONG).status_code for _ in range(6)] == [401] * 5 + [429]


def test_failed_logins_are_limited_even_with_rate_limiting_off(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_enabled=False)

    for _ in range(5):
        login(connect(), password=WRONG)

    assert login(connect()).status_code == 429


def test_a_refused_attempt_checks_no_password(accounts, limited, monkeypatch):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)
    for _ in range(5):
        login(connect(), password=WRONG)
    checked = []
    real_verify = argon2.PasswordHasher.verify

    def spy(self, hash, password):
        checked.append(hash)
        return real_verify(self, hash, password)

    monkeypatch.setattr(argon2.PasswordHasher, "verify", spy)

    assert login(connect()).status_code == 429
    assert checked == []


def test_wrong_passwords_for_deleting_the_account_count_as_failures(accounts, limited):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)
    browser = connect(origin=TEST_ORIGIN)
    login(browser)

    def delete_account(password):
        return browser.request("DELETE", "/auth/me", json={"password": password})

    assert [delete_account(WRONG).status_code for _ in range(5)] == [403] * 5
    assert delete_account(TEST_PASSWORD).status_code == 429
    assert login(connect()).status_code == 429
    assert browser.get("/auth/me").status_code == 200


def test_failed_logins_count_by_the_address_the_proxy_names(accounts, limited):
    clock, model, queue, connect = limited(
        rate_limit_auth_per_minute=1000, trusted_proxies=[PROXY]
    )

    for _ in range(5):
        login(connect(PROXY), password=WRONG, **{"X-Real-IP": "203.0.113.1"})

    assert login(connect(PROXY), **{"X-Real-IP": "203.0.113.1"}).status_code == 429
    assert login(connect(PROXY), **{"X-Real-IP": "203.0.113.2"}).status_code == 200


def test_a_huge_name_does_not_grow_the_table_of_failures(accounts, limited, client):
    clock, model, queue, connect = limited(rate_limit_auth_per_minute=1000)

    # The longest name that still fits into an 8 KiB body.
    assert login(connect(), "x" * 7_000, WRONG).status_code == 401

    assert all(len(key) < 200 for key in client.app.state.login_failures._hits)


def test_the_table_of_failed_sign_ins_is_bounded_and_evicts_in_constant_time(settings):
    """The same bound and O(1) eviction as the per-minute limits: names never seen before
    cannot grow it without end, nor make one sign-in walk the whole table."""
    clock = FakeClock()
    limiter = build_login_failures(settings, clock=clock)
    assert limiter.max_keys == MAX_KEYS
    for n in range(MAX_KEYS):
        limiter.hit(f"198.51.100.7 name-{n}")
    limiter._hits = CountingOrderedDict(limiter._hits)

    for n in range(50):
        limiter.hit(f"198.51.100.7 new-{n}")

    assert limiter._hits.visits == 0
    assert len(limiter) == MAX_KEYS
    assert limiter.tracked("198.51.100.7 name-0") == 0
    assert limiter.tracked("198.51.100.7 name-50") == 1


# --- /auth/me is a read, logout and deletion are writes ---------------------------------


def register_as(client, username):
    return client.post("/auth/register", json={"username": username, "password": TEST_PASSWORD})


def test_fifteen_page_loads_a_minute_ask_auth_me_without_429(accounts, limited):
    """Every page load asks GET /auth/me: it must not share the 10 of signing in."""
    clock, model, queue, connect = limited()  # the default limits
    browser = connect(origin=TEST_ORIGIN)
    login(browser)

    answers = [browser.get("/auth/me").status_code for _ in range(15)]
    anonymous = [connect().get("/auth/me").status_code for _ in range(15)]

    assert answers == [200] * 15
    assert anonymous == [401] * 15


def test_sign_in_and_registration_still_get_429_after_ten(accounts, limited):
    clock, model, queue, connect = limited()  # RATE_LIMIT_AUTH_PER_MINUTE=10
    # Different names, so the limit of failed sign-ins per name never applies.
    attempts = [login(connect(), f"nobody{n}", WRONG).status_code for n in range(5)]
    attempts += [register_as(connect(), f"new_user{n}").status_code for n in range(5)]

    assert attempts == [401] * 5 + [201] * 5
    for refused in (login(connect(), "nobody99", WRONG), register_as(connect(), "new_user99")):
        assert refused.status_code == 429
        assert refused.json() == {"detail": "Забагато запитів. Спробуйте ще раз через 60 с."}
        assert refused.headers["Retry-After"] == "60"


def test_after_a_429_on_sign_in_auth_me_still_answers(accounts, limited):
    clock, model, queue, connect = limited()
    browser = connect(origin=TEST_ORIGIN)
    assert login(browser).status_code == 200
    for n in range(9):
        login(connect(), f"nobody{n}", WRONG)
    assert login(connect()).status_code == 429

    assert browser.get("/auth/me").status_code == 200
    assert connect().get("/auth/me").status_code == 401


def test_logout_and_account_deletion_count_as_writes(accounts, limited):
    clock, model, queue, connect = limited(
        rate_limit_write_per_minute=2, rate_limit_auth_per_minute=3
    )
    browser = connect(origin=TEST_ORIGIN)
    login(browser)

    assert browser.post("/auth/logout").status_code == 204
    assert browser.post("/auth/logout").status_code == 204
    refused = browser.request("DELETE", "/auth/me", json={"password": TEST_PASSWORD})

    assert refused.status_code == 429
    assert refused.json() == {"detail": "Забагато запитів. Спробуйте ще раз через 60 с."}
    # The auth bucket is untouched by them: the first sign-in is its only request so far.
    assert login(connect()).status_code == 200
    assert login(connect()).status_code == 200
    assert login(connect()).status_code == 429


def test_the_api_description_names_the_limit_of_each_auth_route(client):
    paths = client.get("/openapi.json").json()["paths"]

    def limit_of(path, method):
        return paths[path][method]["responses"]["429"]["description"]

    for path in ("/auth/login", "/auth/register"):
        assert "RATE_LIMIT_AUTH_PER_MINUTE" in limit_of(path, "post"), path
    assert "RATE_LIMIT_READ_PER_MINUTE" in limit_of("/auth/me", "get")
    assert "RATE_LIMIT_AUTH_PER_MINUTE" not in limit_of("/auth/me", "get")
    for path, method in (("/auth/logout", "post"), ("/auth/me", "delete")):
        assert "RATE_LIMIT_WRITE_PER_MINUTE" in limit_of(path, method), path
    # Wrong passwords have their own limit where a password is checked.
    for path, method in (("/auth/login", "post"), ("/auth/me", "delete")):
        assert "LOGIN_FAILURES_" in limit_of(path, method), path
    for path, method in (("/auth/register", "post"), ("/auth/me", "get"), ("/auth/logout", "post")):
        assert "LOGIN_FAILURES_" not in limit_of(path, method), path
