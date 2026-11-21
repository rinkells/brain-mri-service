"""Deployment files: container limits in docker-compose.yml and the Docker image."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


NOT_MRI_DEFAULTS = {
    "NOT_MRI_COLOR_MAX": "7",
    "NOT_MRI_NOISE_MAX": "0.85",
    "NOT_MRI_STD_MIN": "10",
    "NOT_MRI_LOWCONTRAST_STD": "30",
    "NOT_MRI_DARK_MIN": "0.15",
}


def test_not_mri_thresholds_reach_the_api(services):
    environment = services["api"]["environment"]

    assert {name: environment[name] for name in NOT_MRI_DEFAULTS} == NOT_MRI_DEFAULTS


@pytest.mark.parametrize("path", [".env.example", "backend/.env.example"])
def test_example_env_files_list_the_not_mri_thresholds(path):
    lines = (REPO_DIR / path).read_text(encoding="utf-8").splitlines()

    assert {f"{name}={value}" for name, value in NOT_MRI_DEFAULTS.items()} <= set(lines)
