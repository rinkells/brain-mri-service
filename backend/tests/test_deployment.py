"""Deployment files: container limits in docker-compose.yml and the Docker image."""

import json
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from app.input_check import NotMriThresholds

REPO_DIR = Path(__file__).resolve().parents[2]
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
