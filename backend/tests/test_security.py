"""Checks of the Host and Origin headers.

A browser adds Origin to every POST and DELETE, so a page on another site that
tries to send a scan or delete the history is refused. Clients that are not
browsers (curl, scripts, these tests) send no Origin and work as before.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.config import Settings
from tests.helpers import image_bytes, make_record

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
    record = make_record()
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
