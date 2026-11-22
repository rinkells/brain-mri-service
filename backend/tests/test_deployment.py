"""Deployment files: docker-compose.yml, the Docker images and the nginx config."""

import base64
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from app.config import DEFAULT_ALLOWED_ORIGINS, Settings
from app.input_check import NotMriThresholds

REPO_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_DIR / "frontend"
MIB = 1024 * 1024


@pytest.fixture(scope="module")
def services():
    """The services as `docker compose config` resolves them; no container is started.

    The example env file is used, so a developer's real .env (and its password) is
    never read.
    """
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", "config", "--format", "json"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode != 0 and "compose" in result.stderr and "not a docker command" in (
        result.stderr
    ):
        pytest.skip("Docker Compose v2 is not installed")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def test_api_container_is_limited(services):
    api = services["api"]

    assert int(api["mem_limit"]) == 1500 * MIB
    assert float(api["cpus"]) == 2
    assert api["pids_limit"] == 200
    assert api["cap_drop"] == ["ALL"]
    assert api["security_opt"] == ["no-new-privileges:true"]


def test_database_container_has_memory_and_process_limits_only(services):
    db = services["db"]

    assert int(db["mem_limit"]) == 512 * MIB
    assert db["pids_limit"] == 256
    # Postgres switches users and owns its data directory: its capabilities stay.
    assert "cap_drop" not in db
    assert "security_opt" not in db


# --- Docker image --------------------------------------------------------------

DOCKERFILE = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
# Lowest versions without known CVEs: pip CVE-2025-8869 (the base image ships pip
# 25.0.1); setuptools CVE-2024-6345 and CVE-2025-47273 (not in the base image, but
# torch pulls it in).
FIXED_PIP = (25, 3)
FIXED_SETUPTOOLS = (78, 1, 1)


def pinned_version(package: str) -> tuple[int, ...]:
    match = re.search(rf"\b{package}==([0-9][0-9.]*)", DOCKERFILE)
    assert match, f"the Dockerfile does not pin {package}"
    return tuple(int(part) for part in match.group(1).split("."))


def test_image_upgrades_pip_and_setuptools_past_known_cves():
    assert pinned_version("pip") >= FIXED_PIP
    assert pinned_version("setuptools") >= FIXED_SETUPTOOLS


def test_tooling_is_upgraded_before_the_app_dependencies_are_installed():
    """The fixed pip installs everything else, and torch finds setuptools in place."""
    assert DOCKERFILE.index("setuptools==") < DOCKERFILE.index("-r torch-requirements.txt")


def test_app_dependency_pins_are_left_alone():
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
        encoding="utf-8"
    )

    assert "pip" not in requirements.split()
    assert "setuptools" not in requirements


# NOT_MRI_COLOR_MAX and the rest, named after the fields of NotMriThresholds: the Python
# defaults are the single source, and every deployment file must repeat them.
NOT_MRI_DEFAULTS = {
    f"NOT_MRI_{name.upper()}": value for name, value in asdict(NotMriThresholds()).items()
}


def as_numbers(values: dict[str, str]) -> dict[str, float]:
    assert set(NOT_MRI_DEFAULTS) <= set(values), "missing NOT_MRI_* settings"
    return {name: float(values[name]) for name in NOT_MRI_DEFAULTS}


def test_not_mri_thresholds_reach_the_api(services):
    """Values the api container gets with the example env file."""
    assert as_numbers(services["api"]["environment"]) == NOT_MRI_DEFAULTS


def test_compose_falls_back_to_the_default_not_mri_thresholds():
    """Values the api container gets when .env does not set them: ${NAME:-value}."""
    text = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    fallbacks = dict(re.findall(r"\$\{(NOT_MRI_[A-Z_]+):-([^}]*)\}", text))

    assert as_numbers(fallbacks) == NOT_MRI_DEFAULTS


@pytest.mark.parametrize("path", [".env.example", "backend/.env.example"])
def test_example_env_files_hold_the_default_not_mri_thresholds(path):
    lines = (REPO_DIR / path).read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if line.startswith("NOT_MRI_"))

    assert as_numbers(values) == NOT_MRI_DEFAULTS



# --- Frontend image: the built UI behind nginx ----------------------------------

# The paths the UI calls (frontend/src/api/client.ts), as the Vite dev proxy lists them.
VITE_PROXY_KEY = re.compile(r"^\s*'(/[a-z]+)':\s*BACKEND_URL", re.MULTILINE)
# location [=|^~] path { ... } blocks; the config has no nested blocks inside them.
NGINX_LOCATION = re.compile(r"location\s+(?:=\s*|\^~\s*)?(\S+)\s*\{([^}]*)\}")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def nginx_conf() -> str:
    return (FRONTEND_DIR / "nginx.conf").read_text(encoding="utf-8")


def csp_directives() -> dict[str, list[str]]:
    policy = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', nginx_conf())
    assert policy, "no Content-Security-Policy header"
    directives = (part.split() for part in policy.group(1).split(";") if part.strip())
    return {name: values for name, *values in directives}


def test_nginx_sends_the_security_headers_on_every_response():
    conf = nginx_conf()

    for name, value in SECURITY_HEADERS.items():
        assert f'add_header {name} "{value}" always;' in conf
    csp = csp_directives()
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"][0] == "'self'"
    assert csp["style-src"] == ["'self'"]
    assert csp["img-src"] == ["'self'", "data:", "blob:"]
    assert csp["connect-src"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'none'"]
    assert not {"'unsafe-inline'", "'unsafe-eval'"} & {v for values in csp.values() for v in values}


def test_csp_allows_exactly_the_inline_theme_script_of_index_html():
    """index.html applies the theme before the first paint with an inline script. The CSP
    allows it by its hash, so any change to the script must update nginx.conf too."""
    with open(FRONTEND_DIR / "index.html", encoding="utf-8", newline="") as file:
        scripts = re.findall(r"<script>(.*?)</script>", file.read(), re.DOTALL)
    hashes = {
        "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
        for s in scripts
    }

    allowed = {value for value in csp_directives()["script-src"] if value.startswith("'sha256-")}
    assert hashes and allowed == hashes


def test_nginx_proxies_the_api_paths_of_the_ui_and_nothing_else():
    vite = (FRONTEND_DIR / "vite.config.ts").read_text(encoding="utf-8")
    ui_paths = set(VITE_PROXY_KEY.findall(vite))
    proxied = {path for path, body in NGINX_LOCATION.findall(nginx_conf()) if "proxy_pass" in body}

    assert ui_paths == {"/health", "/predict", "/predictions", "/stats"}
    assert proxied == ui_paths | {"/predictions/"}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert path not in nginx_conf()


def test_nginx_passes_the_client_host_and_address_to_the_api():
    """TrustedHostMiddleware checks the Host the browser sent, e.g. localhost:8080."""
    conf = nginx_conf()

    assert "proxy_set_header Host $http_host;" in conf
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in conf
    # The API adds these itself: nginx hides its copies, so they are not sent twice.
    for name in SECURITY_HEADERS:
        assert f"proxy_hide_header {name};" in conf


def test_nginx_lets_a_slightly_too_large_upload_reach_the_api():
    """Above client_max_body_size nginx answers 413 itself, without the API's explanation."""
    size = re.search(r"client_max_body_size (\d+)m;", nginx_conf())
    max_upload_mb = Settings.model_fields["max_upload_mb"].default

    assert size, "no client_max_body_size in megabytes"
    assert max_upload_mb + 1 <= int(size.group(1)) <= max_upload_mb + 5


def test_nginx_waits_long_enough_for_an_inference():
    conf = nginx_conf()

    for name in ("proxy_read_timeout", "proxy_send_timeout", "send_timeout"):
        seconds = re.search(rf"{name} (\d+)s;", conf)
        assert seconds and int(seconds.group(1)) >= 60, name


def test_nginx_caches_hashed_assets_but_revalidates_index_html():
    locations = dict(NGINX_LOCATION.findall(nginx_conf()))

    assert "expires max;" in locations["/assets/"]
    assert "expires -1;" in locations["/"]
    assert "try_files $uri $uri/ /index.html;" in locations["/"]


def test_frontend_image_is_pinned_and_does_not_run_as_root():
    dockerfile = (FRONTEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    images = re.findall(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE)
    final_stage = dockerfile[dockerfile.rindex("\nFROM ") :]
    users = re.findall(r"^USER\s+(\S+)", final_stage, re.MULTILINE)

    assert images[0].startswith("node:") and images[-1].startswith("nginxinc/nginx-unprivileged:")
    for image in images:
        tag = image.split(":", 1)[1]
        assert re.match(r"\d+\.\d+\.\d+-alpine\d", tag), image
    assert users and users[-1] not in ("root", "0")
    assert "ARG VITE_MAX_UPLOAD_MB" in dockerfile
    assert "EXPOSE 8080" in final_stage


def test_frontend_build_context_leaves_out_local_files():
    lines = set((FRONTEND_DIR / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {"node_modules", "dist", ".env"} <= lines



# --- Frontend service in Compose ------------------------------------------------

FRONTEND_ORIGINS = {"http://localhost:8080", "http://127.0.0.1:8080"}


def test_frontend_service_serves_the_ui_on_localhost_8080(services):
    frontend = services["frontend"]

    assert frontend["build"]["context"].replace("\\", "/").endswith("/frontend")
    # The UI checks file sizes with the same limit the API enforces.
    assert frontend["build"]["args"]["VITE_MAX_UPLOAD_MB"] == "10"
    assert [(p["host_ip"], p["published"], p["target"]) for p in frontend["ports"]] == [
        ("127.0.0.1", "8080", 8080)
    ]
    assert frontend["depends_on"]["api"]["condition"] == "service_healthy"
    assert frontend["healthcheck"]["test"][0] == "CMD"


def test_frontend_container_is_limited_and_read_only(services):
    frontend = services["frontend"]

    assert int(frontend["mem_limit"]) == 128 * MIB
    assert 0 < frontend["pids_limit"] <= 100
    assert frontend["cap_drop"] == ["ALL"]
    assert frontend["security_opt"] == ["no-new-privileges:true"]
    assert frontend["read_only"] is True
    # nginx-unprivileged keeps its pid file and temporary files in /tmp.
    assert any(entry.split(":")[0] == "/tmp" for entry in frontend["tmpfs"])


def test_api_stays_on_localhost_8000_for_swagger_ui(services):
    assert [(p["host_ip"], p["published"], p["target"]) for p in services["api"]["ports"]] == [
        ("127.0.0.1", "8000", 8000)
    ]


def test_the_api_accepts_writes_from_the_frontend_page(services):
    """The browser sends Origin http://localhost:8080 with POST and DELETE through nginx."""
    origins = set(services["api"]["environment"]["ALLOWED_ORIGINS"].split(","))

    assert FRONTEND_ORIGINS <= origins
    # Vite, npm run preview and Swagger UI keep working too.
    assert set(DEFAULT_ALLOWED_ORIGINS) <= origins


def test_example_env_files_list_the_frontend_origins():
    backend = (REPO_DIR / "backend" / ".env.example").read_text(encoding="utf-8")
    root = (REPO_DIR / ".env.example").read_text(encoding="utf-8")
    backend_line = re.search(r"^ALLOWED_ORIGINS=(.*)$", backend, re.MULTILINE)
    # Commented out in the root file: Compose's default follows API_PORT.
    root_line = re.search(r"^# ALLOWED_ORIGINS=(.*)$", root, re.MULTILINE)

    for line in (backend_line, root_line):
        assert line
        assert FRONTEND_ORIGINS | set(DEFAULT_ALLOWED_ORIGINS) <= set(line.group(1).split(","))
