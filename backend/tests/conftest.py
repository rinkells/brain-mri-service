"""Shared fixtures.

Tests do not depend on the real weights: a randomly initialised ResNet18 of the
same architecture is saved to a temporary directory and used instead.

A random network is unsure of itself (top-1 probability about 0.55), so every
prediction would carry a low_confidence warning. A large bias on one output makes
the test model confident, as the trained one is on most scans. Tests of the
warning itself replace the model with a stand-in (see test_api.py).
"""

import argon2
import pytest
import torch
from fastapi.testclient import TestClient
from torchvision.models import resnet18

from app.auth import start_session
from app.config import CLASS_NAMES, Settings
from app.database import Base, Database
from app.main import create_app
from app.rate_limit import build_login_failures
from tests.helpers import OWNER_ID, OWNER_NAME, TEST_PASSWORD, add_user, new_browser

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
    """Point the shared app at another history database for one test.

    Failed sign-ins are counted afresh too: unlike the rate limit, that limit is on in
    the shared app, and the counts of one test must not reach the next.
    """
    app = client.app
    previous = (app.state.database, app.state.login_failures)

    def use(database):
        app.state.database = database
        app.state.login_failures = build_login_failures(app.state.settings)
        return client

    yield use
    app.state.database, app.state.login_failures = previous


@pytest.fixture(scope="session")
def password_hash():
    """One argon2id hash of TEST_PASSWORD for every user the fixtures create (~0.1 s each)."""
    return argon2.PasswordHasher().hash(TEST_PASSWORD)


@pytest.fixture
def sign_in(client, use_database, password_hash):
    """sign_in(database, username, user_id) -> a Browser signed in as a new user.

    The user and its session are written straight to the database; the browser holds
    the session cookie and sends an allowed Origin, as a page of the interface does.
    """
    app = client.app

    def sign_in(database, username: str = OWNER_NAME, user_id=None):
        use_database(database)
        user_id = add_user(database, username, password_hash, user_id)
        settings = app.state.settings
        token = start_session(database, user_id, settings.session_ttl)
        browser = new_browser(app)
        browser.cookies.set(settings.session_cookie_name, token)
        browser.user_id, browser.token = user_id, token
        return browser

    return sign_in


@pytest.fixture
def history_client(sign_in, database):
    """A browser signed in as OWNER_ID, with a fresh, empty in-memory history database."""
    return sign_in(database, OWNER_NAME, OWNER_ID)
