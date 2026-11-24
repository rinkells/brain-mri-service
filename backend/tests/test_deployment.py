"""Deployment files: docker-compose.yml, the Docker images and the nginx config."""

import base64
import hashlib
import ipaddress
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
def compose():
    """The whole file as `docker compose config` resolves it; no container is started.

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
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def services(compose):
    return compose["services"]


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

# Keys of the Vite dev proxy: path prefixes, or regular expressions when they start with ^.
VITE_PROXY_KEY = re.compile(r"^\s*'([^']+)':\s*BACKEND_URL", re.MULTILINE)
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


def vite_proxy_keys() -> list[str]:
    vite = (FRONTEND_DIR / "vite.config.ts").read_text(encoding="utf-8")
    # The TypeScript source escapes backslashes in the string literal.
    return [key.replace("\\\\", "\\") for key in VITE_PROXY_KEY.findall(vite)]


def vite_proxies(url: str) -> bool:
    """Whether the Vite dev server forwards url to the API, matching keys as Vite does."""
    return any(
        re.search(key, url) if key.startswith("^") else url.startswith(key)
        for key in vite_proxy_keys()
    )


def test_nginx_proxies_the_api_paths_of_the_ui_and_nothing_else():
    # The paths the UI calls (frontend/src/api/client.ts), as the Vite dev proxy lists them.
    ui_paths = {re.match(r"\^?(/[a-z/]+)", key).group(1) for key in vite_proxy_keys()}
    proxied = {path for path, body in NGINX_LOCATION.findall(nginx_conf()) if "proxy_pass" in body}

    assert ui_paths == {"/health", "/predict", "/predictions", "/stats", "/auth"}
    # Vite matches keys as prefixes; nginx needs /predictions/ besides /predictions, and
    # takes everything under /auth/ (the API has no /auth path of its own).
    assert proxied == (ui_paths - {"/auth"}) | {"/predictions/", "/auth/"}
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


def test_compose_healthcheck_of_api_asks_for_readiness(services):
    """The api service is healthy only while the model is loaded and Postgres answers,
    and frontend waits for exactly that."""
    healthcheck = services["api"]["healthcheck"]
    command = " ".join(healthcheck["test"])

    assert healthcheck["test"][0] == "CMD"
    assert "'http://127.0.0.1:8000/health/ready'" in command
    # The probe gives up on the database after 2 s; the check must outlast it.
    assert float(re.search(r"timeout=(\d+(?:\.\d+)?)", command).group(1)) > 2
    assert healthcheck["timeout"] == "5s"
    assert services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_image_healthcheck_stays_on_liveness():
    """A container started from the image alone (without Compose) has no database to
    wait for: its own HEALTHCHECK asks /health, which never contacts the database."""
    healthcheck = re.search(r"^HEALTHCHECK .*\n\s+CMD (.*)$", DOCKERFILE, re.MULTILINE)

    assert healthcheck, "no HEALTHCHECK in the backend Dockerfile"
    assert "'http://127.0.0.1:8000/health'" in healthcheck.group(1)


def test_the_readiness_check_stays_inside_compose():
    """/health/ready tells whether the database is up and is not rate limited, so the UI
    port does not expose it: nginx answers 404 itself (without this, the SPA fallback of
    location / would answer 200 with index.html) and the Vite dev proxy does not forward
    it. The Compose healthcheck asks the API on 127.0.0.1:8000 inside its container."""
    locations = dict(NGINX_LOCATION.findall(nginx_conf()))

    assert locations["/health/ready"].strip() == "return 404;"
    assert locations["/health"].strip() == "proxy_pass $api;"
    assert vite_proxies("/health") and vite_proxies("/health?probe=1")
    assert not vite_proxies("/health/ready")


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



# --- Rate limiting and the client's address ----------------------------------------

RATE_LIMIT_DEFAULTS = {
    name.upper(): str(field.default).lower()
    for name, field in Settings.model_fields.items()
    if name.startswith("rate_limit_")
}


def test_nginx_sets_x_real_ip_itself():
    """Whatever X-Real-IP the client sends is replaced by the address nginx sees."""
    assert "proxy_set_header X-Real-IP $remote_addr;" in nginx_conf()


def test_only_the_frontend_container_is_a_trusted_proxy(compose):
    ipam = compose["networks"]["default"]["ipam"]["config"][0]
    subnet = ipaddress.ip_network(ipam["subnet"])
    dynamic = ipaddress.ip_network(ipam["ip_range"])
    frontend = compose["services"]["frontend"]["networks"]["default"]["ipv4_address"]
    trusted = compose["services"]["api"]["environment"]["TRUSTED_PROXIES"]

    # A fixed address outside ip_range, and nothing else is trusted. With ip_range set,
    # Docker takes the gateway from that range too (172.30.250.128, seen under Docker
    # Desktop), and direct connections to the published API port come from the gateway.
    assert ipaddress.ip_address(frontend) in subnet
    assert dynamic.subnet_of(subnet) and ipaddress.ip_address(frontend) not in dynamic
    assert trusted == frontend


def test_rate_limits_reach_the_api_with_the_python_defaults(services):
    environment = services["api"]["environment"]

    assert set(RATE_LIMIT_DEFAULTS) == {
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_PREDICT_PER_MINUTE",
        "RATE_LIMIT_WRITE_PER_MINUTE",
        "RATE_LIMIT_READ_PER_MINUTE",
        "RATE_LIMIT_AUTH_PER_MINUTE",
    }
    assert {name: environment[name].lower() for name in RATE_LIMIT_DEFAULTS} == RATE_LIMIT_DEFAULTS


def test_compose_falls_back_to_the_default_rate_limits():
    text = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    fallbacks = dict(re.findall(r"\$\{(RATE_LIMIT_[A-Z_]+):-([^}]*)\}", text))

    assert {name: value.lower() for name, value in fallbacks.items()} == RATE_LIMIT_DEFAULTS


@pytest.mark.parametrize("path", [".env.example", "backend/.env.example"])
def test_example_env_files_hold_the_default_rate_limits(path):
    lines = (REPO_DIR / path).read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if line.startswith("RATE_LIMIT_"))

    assert {name: value.lower() for name, value in values.items()} == RATE_LIMIT_DEFAULTS


# --- Accounts and sessions -----------------------------------------------------------

AUTH_DEFAULTS = {
    name.upper(): str(field.default).lower()
    for name, field in Settings.model_fields.items()
    if name.startswith(("session_", "login_failures_"))
}
AUTH_SETTING = re.compile(r"^(?:SESSION|LOGIN_FAILURES)_[A-Z_]+$")


def test_auth_settings_reach_the_api_with_the_python_defaults(services):
    environment = services["api"]["environment"]

    assert set(AUTH_DEFAULTS) == {
        "SESSION_TTL_HOURS",
        "SESSION_COOKIE_NAME",
        "SESSION_COOKIE_SECURE",
        "SESSION_MAX_PER_USER",
        "LOGIN_FAILURES_MAX",
        "LOGIN_FAILURES_WINDOW_MINUTES",
    }
    assert {name: environment[name].lower() for name in AUTH_DEFAULTS} == AUTH_DEFAULTS


def test_compose_falls_back_to_the_default_auth_settings():
    text = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    fallbacks = dict(re.findall(r"\$\{((?:SESSION|LOGIN_FAILURES)_[A-Z_]+):-([^}]*)\}", text))

    assert {name: value.lower() for name, value in fallbacks.items()} == AUTH_DEFAULTS


@pytest.mark.parametrize("path", [".env.example", "backend/.env.example"])
def test_example_env_files_hold_the_default_auth_settings(path):
    lines = (REPO_DIR / path).read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if AUTH_SETTING.match(line.split("=")[0]))

    assert {name: value.lower() for name, value in values.items()} == AUTH_DEFAULTS


def test_nginx_passes_the_session_cookie_both_ways():
    """Set-Cookie from the API and Cookie from the browser must pass nginx unchanged."""
    conf = nginx_conf()

    for directive in (
        "proxy_hide_header Set-Cookie",
        "proxy_ignore_headers Set-Cookie",
        "proxy_cookie_",
        "proxy_set_header Cookie",
        "proxy_cache ",
    ):
        assert directive not in conf


def test_uvicorn_leaves_proxy_headers_to_the_app(services):
    """Without --no-proxy-headers uvicorn would take the client from X-Forwarded-For sent
    from 127.0.0.1 (e.g. through the Vite dev proxy) and change the rate limit key."""
    dockerfile = (REPO_DIR / "backend" / "Dockerfile").read_text(encoding="utf-8")
    cmd = json.loads(re.search(r"^CMD (\[.*\])$", dockerfile, re.MULTILINE).group(1))
    command = " ".join(services["api"]["command"])

    assert cmd[0] == "uvicorn" and "--no-proxy-headers" in cmd
    assert "exec uvicorn" in command and "--no-proxy-headers" in command


def test_nginx_passes_the_sec_fetch_headers_on_to_the_api():
    """The API refuses requests from pages of other sites by Sec-Fetch-Site
    (CrossSiteCheckMiddleware), so nginx must forward the browser's headers unchanged."""
    conf = nginx_conf()

    assert "proxy_pass_request_headers off" not in conf
    assert not re.search(r"proxy_set_header\s+Sec-Fetch-", conf, re.IGNORECASE)
    assert "more_clear_input_headers" not in conf
