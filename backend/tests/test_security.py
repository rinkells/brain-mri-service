"""Checks of the Host and Origin headers.

A browser adds Origin to every POST and DELETE, so a page on another site that
tries to send a scan or delete the history is refused. Clients that are not
browsers (curl, scripts, these tests) send no Origin and work as before.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.helpers import OWNER_ID, image_bytes, make_record

BACKEND_DIR = Path(__file__).resolve().parents[1]
FOREIGN_ORIGIN = "https://evil.example"
FORBIDDEN_ORIGIN = {
    "detail": "Запити з цього джерела (Origin) заборонено. Дозволені джерела задає "
    "ALLOWED_ORIGINS."
}
DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


def post_scan(client, **headers):
    files = {"file": ("scan.png", image_bytes(512, 512), "image/png")}
    return client.post("/predict", files=files, headers=headers)


def add_record(database):
    """A record of the user history_client is signed in as."""
    record = make_record(user_id=OWNER_ID)
    with database.session() as session:
        session.add(record)
        session.commit()
    return record.id


# --- Origin --------------------------------------------------------------------


def test_post_from_a_foreign_origin_is_403(client):
    response = post_scan(client, Origin=FOREIGN_ORIGIN)

    assert response.status_code == 403
    assert response.json() == FORBIDDEN_ORIGIN


def test_delete_from_a_foreign_origin_is_403_and_keeps_the_record(history_client, database):
    record_id = add_record(database)

    response = history_client.delete(
        f"/predictions/{record_id}", headers={"Origin": FOREIGN_ORIGIN}
    )

    assert response.status_code == 403
    assert response.json() == FORBIDDEN_ORIGIN
    assert history_client.get(f"/predictions/{record_id}").status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "null",  # sandboxed frames and local files
        "http://localhost:5174",
        "https://localhost:5173",
        "http://localhost.evil.example:5173",
        "http://evil.example:8000",
    ],
)
def test_near_misses_of_allowed_origins_are_403(client, origin):
    assert post_scan(client, Origin=origin).status_code == 403


def test_requests_without_origin_work_as_before(client):
    assert post_scan(client).status_code == 200


@pytest.mark.parametrize("origin", DEFAULT_ORIGINS)
def test_default_origins_may_post(client, origin):
    assert post_scan(client, Origin=origin).status_code == 200


def test_origin_is_compared_without_case(client):
    assert post_scan(client, Origin="HTTP://LOCALHOST:5173").status_code == 200


def test_reads_from_a_foreign_origin_are_not_blocked(client):
    """Without CORS headers the browser keeps the answer from the foreign page anyway."""
    assert client.get("/health", headers={"Origin": FOREIGN_ORIGIN}).status_code == 200


# --- Cross-site requests (Sec-Fetch-Site) ----------------------------------------

CROSS_SITE = {
    "detail": "Запити зі сторінок інших сайтів заборонено. Дозволені джерела задає "
    "ALLOWED_ORIGINS."
}
# What a browser sends for <img src="http://localhost:8080/stats"> on another site.
FOREIGN_IMAGE = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
@pytest.mark.parametrize("path", ["/stats", "/predictions", "/docs", "/openapi.json"])
def test_a_page_of_another_site_cannot_read_the_api(client, site, path):
    response = client.get(path, headers={"Sec-Fetch-Site": site})

    assert response.status_code == 403
    assert response.json() == CROSS_SITE
    assert_security_headers(response)


def test_a_foreign_image_request_is_403(client):
    assert client.get("/stats", headers=FOREIGN_IMAGE).json() == CROSS_SITE


def test_a_foreign_origin_is_403_for_a_cross_site_read(client):
    headers = {"Sec-Fetch-Site": "cross-site", "Origin": FOREIGN_ORIGIN}

    assert client.get("/stats", headers=headers).status_code == 403


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:8000"])
def test_a_cross_site_request_from_an_allowed_origin_passes(client, origin):
    headers = {"Sec-Fetch-Site": "cross-site", "Origin": origin}

    # 503: the shared app has no history database; the request got past the checks.
    assert client.get("/stats", headers=headers).status_code == 503
    assert post_scan(client, **headers).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}],
    ids=["same-origin", "typed-in-the-address-bar", "no-header-curl"],
)
def test_own_pages_and_clients_without_sec_fetch_pass(client, headers):
    assert client.get("/stats", headers=headers).status_code == 503
    assert post_scan(client, **headers).status_code == 200


def test_health_is_not_checked_for_cross_site(client):
    assert client.get("/health", headers=FOREIGN_IMAGE).status_code == 200


def test_the_origin_check_answers_first_for_a_foreign_post(client):
    headers = {"Sec-Fetch-Site": "cross-site", "Origin": FOREIGN_ORIGIN}

    assert post_scan(client, **headers).json() == FORBIDDEN_ORIGIN


# --- Host ----------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "localhost.evil.example"])
def test_foreign_host_is_400(client, host):
    response = client.get("/health", headers={"Host": host})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8000", "127.0.0.1:8000", "api:8000", "localhost:5173"]
)
def test_allowed_hosts_are_served(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


# --- The ways the service is really reached ------------------------------------


def test_docker_healthcheck_passes_the_host_check(client):
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    url = re.search(r"HEALTHCHECK.*?urlopen\('([^']+)'", dockerfile, re.DOTALL).group(1)
    parts = urlsplit(url)

    assert parts.hostname in Settings(_env_file=None).allowed_hosts
    assert client.get(parts.path, headers={"Host": parts.netloc}).status_code == 200


def test_docker_healthcheck_passes_whatever_allowed_hosts_says(settings):
    """The healthcheck asks 127.0.0.1 even when ALLOWED_HOSTS names only a public host."""
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    url = re.search(r"HEALTHCHECK.*?urlopen\('([^']+)'", dockerfile, re.DOTALL).group(1)
    parts = urlsplit(url)
    values = {**settings.model_dump(), "allowed_hosts": ["mri.example"]}
    public_only = Settings(_env_file=None, **values)

    with TestClient(create_app(public_only)) as client:
        assert client.get(parts.path, headers={"Host": parts.netloc}).status_code == 200
        assert client.get("/health", headers={"Host": "localhost:8000"}).status_code == 200
        assert client.get("/health", headers={"Host": "mri.example"}).status_code == 200
        assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400


def test_ui_through_the_vite_dev_proxy(history_client):
    """The browser talks to Vite on port 5173. Vite forwards /health, /predict and
    /predictions with the browser's Host and Origin (changeOrigin is not set)."""
    headers = {"Host": "localhost:5173", "Origin": "http://localhost:5173"}

    assert history_client.get("/health", headers=headers).status_code == 200
    created = post_scan(history_client, **headers)
    assert created.status_code == 200
    deleted = history_client.delete(f"/predictions/{created.json()['id']}", headers=headers)
    assert deleted.status_code == 204


def test_swagger_ui_on_the_api_port(client):
    """Swagger UI is served by the API, so "Try it out" sends the API's own origin."""
    docs = client.get("/docs", headers={"Host": "127.0.0.1:8000"})
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text

    response = post_scan(client, Host="127.0.0.1:8000", Origin="http://127.0.0.1:8000")

    assert response.status_code == 200


# --- Security headers ----------------------------------------------------------

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}


def assert_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        ("get", "/health", {}, 200),
        ("get", "/docs", {}, 200),
        ("get", "/openapi.json", {}, 200),
        ("get", "/no-such-page", {}, 404),
        ("get", "/health", {"Host": "evil.example"}, 400),
        (
            "delete",
            "/predictions/00000000-0000-4000-8000-000000000000",
            {"Origin": FOREIGN_ORIGIN},
            403,
        ),
        ("get", "/predictions", {}, 503),  # history disabled in this app
    ],
    ids=["health", "docs", "openapi", "404", "foreign-host", "foreign-origin", "503"],
)
def test_every_response_has_security_headers(client, method, path, headers, status):
    response = getattr(client, method)(path, headers=headers)

    assert response.status_code == status
    assert_security_headers(response)


def test_prediction_has_security_headers(client):
    response = post_scan(client)

    assert response.status_code == 200
    assert_security_headers(response)


def test_history_is_never_cached(history_client, database):
    record_id = add_record(database)

    for path in ("/predictions", f"/predictions/{record_id}", "/stats"):
        response = history_client.get(path)
        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == "no-store", path
    deleted = history_client.delete(f"/predictions/{record_id}")
    assert deleted.headers.get("cache-control") == "no-store"
    missing = history_client.get(f"/predictions/{record_id}")
    assert missing.status_code == 404
    assert missing.headers.get("cache-control") == "no-store"


def test_no_store_is_not_added_to_public_responses(client):
    """/predict shares a prefix with /predictions, but an anonymous prediction is not
    part of anyone's history (signed-in responses: see test_auth.py)."""
    assert client.get("/health").headers.get("cache-control") is None
    assert post_scan(client).headers.get("cache-control") is None


# --- ENABLE_DOCS ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_on_by_default(client, path):
    assert client.get(path).status_code == 200


@pytest.fixture(scope="module")
def client_without_docs(settings):
    with TestClient(create_app(settings.model_copy(update={"enable_docs": False}))) as client:
        yield client


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_docs_can_be_switched_off(client_without_docs, path):
    response = client_without_docs.get(path)

    assert response.status_code == 404
    assert_security_headers(response)


def test_api_works_without_docs(client_without_docs):
    assert client_without_docs.get("/health").status_code == 200
    assert post_scan(client_without_docs).status_code == 200
