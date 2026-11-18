"""Deployment files: container limits in docker-compose.yml and the Docker image."""

import json
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
