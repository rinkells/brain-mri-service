"""Shared fixtures.

Tests do not depend on the real weights: a randomly initialised ResNet18 of the
same architecture is saved to a temporary directory and used instead.

A random network is unsure of itself (top-1 probability about 0.55), so every
prediction would carry a low_confidence warning. A large bias on one output makes
the test model confident, as the trained one is on most scans. Tests of the
warning itself replace the model with a stand-in (see test_api.py).
"""

import pytest
import torch
from fastapi.testclient import TestClient
from torchvision.models import resnet18

from app.config import CLASS_NAMES, Settings
from app.database import Base, Database
from app.main import create_app

MAX_UPLOAD_MB = 1
MAX_PIXELS = 1_000_000
IN_MEMORY_DB = "sqlite+pysqlite:///:memory:"
MODEL_VERSION = "test-model"
LOW_CONFIDENCE_THRESHOLD = 0.9
CONFIDENT_BIAS = 5.0
# Host header TestClient sends; allowed in tests next to the production defaults.
TEST_HOST = "testserver"
PRODUCTION_HOSTS = ["localhost", "127.0.0.1", "api"]


@pytest.fixture
def database():
    """A fresh in-memory SQLite history database; Postgres is not needed for tests."""
    db = Database.from_url(IN_MEMORY_DB)
    Base.metadata.create_all(db.engine)
    yield db
    db.dispose()


@pytest.fixture(scope="session")
def weights_path(tmp_path_factory):
    torch.manual_seed(0)
    model = resnet18(weights=None, num_classes=len(CLASS_NAMES))
    with torch.no_grad():
        model.fc.bias[CLASS_NAMES.index("notumor")] += CONFIDENT_BIAS
    path = tmp_path_factory.mktemp("weights") / "resnet18_random.pt"
    torch.save(model.state_dict(), path)
    return path


@pytest.fixture(scope="session")
def settings(weights_path):
    # Explicit values, so neither the environment nor a local .env affects the tests.
    return Settings(
        _env_file=None,
        model_path=weights_path,
        model_version=MODEL_VERSION,
        device="cpu",
        max_upload_mb=MAX_UPLOAD_MB,
        max_pixels=MAX_PIXELS,
        low_confidence_threshold=LOW_CONFIDENCE_THRESHOLD,
        allowed_hosts=[*PRODUCTION_HOSTS, TEST_HOST],
        # The shared app serves every test; with a limit its requests would add up across
        # them. test_rate_limit.py switches it on with its own limits and a fake clock.
        rate_limit_enabled=False,
    )


@pytest.fixture(scope="session")
def client(settings):
    """App without DATABASE_URL: predictions work, history is disabled."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def use_database(client):
    """Point the shared app at another history database for one test."""
    app = client.app
    previous = app.state.database

    def use(database):
        app.state.database = database
        return client

    yield use
    app.state.database = previous


@pytest.fixture
def history_client(use_database, database):
    """The shared app with a fresh, empty in-memory history database."""
    return use_database(database)
