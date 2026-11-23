"""Accounts and sessions: registration, sign-in, the session cookie, sign-out and deleting
an account, with the protections around them (CSRF, caching, logs, timing).

Every test gets a fresh in-memory database. Users are created through the API, so the
real argon2id hashing runs (about 0.1 s per hash).
"""

import asyncio
import base64
import hashlib
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import argon2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError

from app.auth import session_id_of
from app.database import Database, create_db_engine
from app.main import create_app
from app.records import PredictionRecord, User, UserSession
from app.schemas import CredentialsRequest, DeleteAccountRequest
from tests.helpers import TEST_ORIGIN, add_user, image_bytes, new_browser

PASSWORD = "correct-horse-1"
WRONG_PASSWORD = "wrong-horse-22"
COOKIE = "brain_mri_session"
UNREACHABLE_POSTGRES = "postgresql+psycopg://brain_mri:secret@127.0.0.1:1/brain_mri"

LOGIN_FAILED = {"detail": "Неправильне ім'я користувача або пароль."}
NOT_SIGNED_IN = {"detail": "Потрібно увійти в обліковий запис."}
SESSION_EXPIRED = {"detail": "Сесія недійсна або закінчилася. Увійдіть знову."}
USERNAME_TAKEN = {"detail": "Це ім'я користувача вже зайняте."}
PASSWORD_MISMATCH = {"detail": "Неправильний пароль."}
BAD_USERNAME = (
    "Ім'я користувача має містити від 3 до 32 символів: латинські літери, цифри, «_», «.» "
    "або «-»."
)
BAD_PASSWORD = "Пароль має містити від 10 до 128 символів."
PASSWORD_IS_USERNAME = "Пароль не може збігатися з іменем користувача."
CREDENTIALS_BODY = {
    "detail": "Тіло запиту має бути JSON-об'єктом із текстовими полями username і password."
}
PASSWORD_BODY = {"detail": "Тіло запиту має бути JSON-об'єктом із текстовим полем password."}
MISSING_ORIGIN = {
    "detail": "Запити, що змінюють дані й надсилають cookie сесії, мають містити заголовок "
    "Origin зі списку ALLOWED_ORIGINS."
}
FOREIGN_ORIGIN = {
    "detail": "Запити з цього джерела (Origin) заборонено. Дозволені джерела задає "
    "ALLOWED_ORIGINS."
}


@pytest.fixture
def app(use_database, database):
    """The shared app with a fresh in-memory database for accounts and history."""
    return use_database(database).app


@pytest.fixture
def browser(app):
    """One browser on a page of the interface: it keeps cookies and sends Origin."""
    return new_browser(app)


@pytest.fixture
def settings_of(app):
    """Change settings of the running app for one test."""
    saved = app.state.settings

    def use(**overrides):
        app.state.settings = saved.model_copy(update=overrides)

    yield use
    app.state.settings = saved


def register(client, username="alice", password=PASSWORD):
    return client.post("/auth/register", json={"username": username, "password": password})


def login(client, username="alice", password=PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def delete_account(client, password=PASSWORD):
    return client.request("DELETE", "/auth/me", json={"password": password})


def post_scan(client, **headers):
    files = {"file": ("scan.png", image_bytes(512, 512), "image/png")}
    return client.post("/predict", files=files, headers=headers or None)


def set_cookie(response) -> tuple[str, str, dict[str, str]]:
    """Name, value and attributes (lower-case names) of the one Set-Cookie header."""
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1, headers
    first, *attributes = (part.strip() for part in headers[0].split(";"))
    name, value = first.split("=", 1)
    parsed = {}
    for attribute in attributes:
        key, _, attribute_value = attribute.partition("=")
        parsed[key.lower()] = attribute_value
    return name, value, parsed


def holding(app, value: str, origin: str | None = TEST_ORIGIN):
    """A new browser whose cookie jar holds `value` as the session cookie.

    A 401 for a bad cookie clears it, so each request that should carry the cookie
    needs a browser of its own.
    """
    browser = new_browser(app, origin=origin)
    browser.cookies.set(COOKIE, value)
    return browser


def token_of(response) -> str:
    name, value, _ = set_cookie(response)
    assert name == COOKIE
    return value


def assert_cookie_cleared(response):
    name, value, attributes = set_cookie(response)
    assert name == COOKIE
    assert value in ("", '""')
    assert attributes["max-age"] == "0"
    assert attributes["path"] == "/"


def users(database) -> list[User]:
    with database.session() as session:
        return list(session.scalars(select(User).order_by(User.created_at)))


def sessions(database) -> list[UserSession]:
    with database.session() as session:
        return list(session.scalars(select(UserSession)))


def records(database) -> list[PredictionRecord]:
    with database.session() as session:
        return list(session.scalars(select(PredictionRecord)))


def add_session(database, user_id, expires_at: datetime) -> str:
    token = secrets.token_urlsafe(32)
    with database.session() as session:
        session.add(
            UserSession(
                id=session_id_of(token),
                user_id=user_id,
                created_at=expires_at - timedelta(hours=1),
                expires_at=expires_at,
            )
        )
        session.commit()
    return token


def everything_stored(database) -> str:
    """Every value of every table, as one text."""
    with database.engine.connect() as connection:
        tables = connection.scalars(text("SELECT name FROM sqlite_master WHERE type='table'"))
        return repr(
            [connection.execute(text(f'SELECT * FROM "{table}"')).all() for table in tables]
        )


# --- Registration ----------------------------------------------------------------


def test_registration_creates_the_user_and_signs_in(browser, database):
    response = register(browser, "Alice")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "username", "created_at"}
    assert body["username"] == "Alice"
    token_of(response)
    assert browser.get("/auth/me").json() == body
    (user,) = users(database)
    assert (str(user.id), user.username) == (body["id"], "Alice")


@pytest.mark.parametrize(
    ("username", "password", "detail"),
    [
        ("ab", PASSWORD, BAD_USERNAME),
        ("a" * 33, PASSWORD, BAD_USERNAME),
        ("", PASSWORD, BAD_USERNAME),
        ("with space", PASSWORD, BAD_USERNAME),
        ("олена", PASSWORD, BAD_USERNAME),
        ("semi;colon", PASSWORD, BAD_USERNAME),
        ("alice\n", PASSWORD, BAD_USERNAME),
        ("alice", "x" * 9, BAD_PASSWORD),
        ("alice", "x" * 129, BAD_PASSWORD),
        ("alice_smith", "alice_smith", PASSWORD_IS_USERNAME),
        ("Alice_Smith", "aLICE_sMITH", PASSWORD_IS_USERNAME),
    ],
    ids=[
        "name-too-short",
        "name-too-long",
        "name-empty",
        "name-space",
        "name-cyrillic",
        "name-semicolon",
        "name-newline",
        "password-too-short",
        "password-too-long",
        "password-is-name",
        "password-is-name-other-case",
    ],
)
def test_registration_checks_the_name_and_the_password(
    browser, database, username, password, detail
):
    response = register(browser, username, password)

    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert "set-cookie" not in response.headers
    assert users(database) == []


@pytest.mark.parametrize(
    ("username", "password"),
    [("abc", "x" * 10), ("a" * 32, "y" * 128), ("A.b_c-9", PASSWORD)],
    ids=["shortest", "longest", "every-kind-of-character"],
)
def test_registration_accepts_the_edges(browser, username, password):
    assert register(browser, username, password).status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"username": "alice"},
        {"password": "s3cret-in-body"},
        {"username": 5, "password": "s3cret-in-body"},
        {"username": "alice", "password": ["s3cret-in-body"]},
        ["alice", "s3cret-in-body"],
    ],
    ids=["no-password", "no-name", "name-not-text", "password-not-text", "not-an-object"],
)
@pytest.mark.parametrize("path", ["/auth/register", "/auth/login"])
def test_a_malformed_body_is_422_in_the_usual_format_without_the_password(browser, path, body):
    response = browser.post(path, json=body)

    assert response.status_code == 422
    assert response.json() == CREDENTIALS_BODY
    assert "s3cret" not in response.text


@pytest.mark.parametrize("path", ["/auth/register", "/auth/login"])
def test_a_body_that_is_not_json_is_422_without_echoing_it(browser, path):
    response = browser.post(
        path,
        content=b'{"username": "alice", "password": "s3cret-in-body"',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == CREDENTIALS_BODY
    assert "s3cret" not in response.text


def test_names_are_unique_without_regard_to_case(app, browser, database):
    assert register(browser, "Alice").status_code == 201

    for name in ("alice", "ALICE", "aLiCe"):
        response = register(new_browser(app), name)
        assert response.status_code == 409
        assert response.json() == USERNAME_TAKEN
        assert "set-cookie" not in response.headers

    assert [user.username for user in users(database)] == ["Alice"]
    signed_in = login(new_browser(app), "aLICE")
    assert signed_in.status_code == 200
    assert signed_in.json()["username"] == "Alice"


def test_two_registrations_of_one_name_at_once_give_one_account(
    app, browser, database, monkeypatch
):
    """Both requests find the name free; the unique index stops the second insert."""
    monkeypatch.setattr("app.auth_api.username_taken", lambda database, username: False)

    assert register(browser, "alice").status_code == 201
    response = register(new_browser(app), "ALICE")

    assert response.status_code == 409
    assert response.json() == USERNAME_TAKEN
    assert len(users(database)) == 1


# --- Sign-in ------------------------------------------------------------------------


def test_login(app, database, password_hash):
    add_user(database, "alice", password_hash)
    browser = new_browser(app)

    response = login(browser, "alice", "correct-horse-1")

    assert response.status_code == 200
    assert response.json()["username"] == "alice"
    token_of(response)
    assert browser.get("/auth/me").status_code == 200


def test_unknown_name_and_wrong_password_get_the_same_answer(app, database, password_hash):
    add_user(database, "alice", password_hash)

    unknown = login(new_browser(app), "nobody", PASSWORD)
    wrong = login(new_browser(app), "alice", WRONG_PASSWORD)

    for response in (unknown, wrong):
        assert response.status_code == 401
        assert response.json() == LOGIN_FAILED
        assert "set-cookie" not in response.headers
    assert set(unknown.headers) == set(wrong.headers)


@pytest.mark.parametrize("username", ["a", "x" * 200, "semi;colon", ""])
def test_a_name_that_could_not_be_registered_is_an_ordinary_failed_login(browser, username):
    response = login(browser, username, PASSWORD)

    assert response.status_code == 401
    assert response.json() == LOGIN_FAILED


def test_an_unknown_name_still_costs_one_password_check(app, database, password_hash, monkeypatch):
    """A dummy hash with the same parameters is checked, so the time of the answer does
    not tell whether the name exists."""
    add_user(database, "alice", password_hash)
    checked = []
    real_verify = argon2.PasswordHasher.verify

    def spy(self, hash, password):
        checked.append(hash)
        return real_verify(self, hash, password)

    monkeypatch.setattr(argon2.PasswordHasher, "verify", spy)

    login(new_browser(app), "nobody", PASSWORD)
    login(new_browser(app), "alice", WRONG_PASSWORD)

    dummy, real = checked
    assert real == password_hash
    assert dummy != password_hash and dummy.startswith("$argon2id$")
    assert argon2.extract_parameters(dummy) == argon2.extract_parameters(real)


def test_an_unknown_name_takes_about_as_long_as_a_wrong_password(app, database, password_hash):
    add_user(database, "alice", password_hash)

    def fastest(username: str) -> float:
        times = []
        for _ in range(3):
            started = time.perf_counter()
            assert login(new_browser(app), username, WRONG_PASSWORD).status_code == 401
            times.append(time.perf_counter() - started)
        return min(times)

    unknown, known = fastest("nobody"), fastest("alice")

    # Both are dominated by one argon2id check (~0.1 s); a skipped check would be ~1 ms.
    assert 0.5 < unknown / known < 2


def test_password_hashing_never_runs_in_the_event_loop(settings, database, monkeypatch):
    """Not at start-up (the dummy hash), not for registration, not for sign-in."""
    calls = []

    def in_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def spying(method):
        real = getattr(argon2.PasswordHasher, method)

        def spy(self, *args, **kwargs):
            calls.append((method, in_event_loop()))
            return real(self, *args, **kwargs)

        return spy

    for method in ("hash", "verify"):
        monkeypatch.setattr(argon2.PasswordHasher, method, spying(method))

    with TestClient(create_app(settings)) as started:
        started.app.state.database = database
        assert register(new_browser(started.app), "alice").status_code == 201
        assert login(new_browser(started.app), "alice", WRONG_PASSWORD).status_code == 401
        assert login(new_browser(started.app), "alice").status_code == 200

    assert [method for method, _ in calls] == ["hash", "hash", "verify", "verify"]
    assert not any(in_loop for _, in_loop in calls)


def test_an_outdated_hash_is_replaced_at_login(app, database):
    weak = argon2.PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    old_hash = weak.hash(PASSWORD)
    add_user(database, "alice", old_hash)

    assert login(new_browser(app)).status_code == 200

    (user,) = users(database)
    assert user.password_hash != old_hash
    assert user.password_hash.startswith("$argon2id$")
    assert not argon2.PasswordHasher().check_needs_rehash(user.password_hash)
    assert login(new_browser(app)).status_code == 200


def test_every_login_starts_a_new_session_and_drops_the_one_it_replaces(
    app, browser, database, password_hash
):
    add_user(database, "alice", password_hash)

    first = token_of(login(browser))
    second = token_of(login(browser))  # the browser sends the first cookie along
    third = token_of(login(new_browser(app)))  # another device

    assert len({first, second, third}) == 3
    assert {row.id for row in sessions(database)} == {session_id_of(second), session_id_of(third)}


def test_login_never_adopts_a_cookie_it_did_not_issue(app, browser, database, password_hash):
    """Session fixation: a token planted in the browser before login stays worthless."""
    add_user(database, "alice", password_hash)
    planted = secrets.token_urlsafe(32)
    browser.cookies.set(COOKIE, planted)

    issued = token_of(login(browser))

    assert issued != planted
    assert session_id_of(planted) not in {row.id for row in sessions(database)}
    assert holding(app, planted).get("/auth/me").status_code == 401


def test_expired_sessions_are_deleted_at_login(app, browser, database, password_hash):
    bob = add_user(database, "bob", password_hash)
    now = datetime.now(UTC)
    expired = add_session(database, bob, now - timedelta(seconds=1))
    live = add_session(database, bob, now + timedelta(hours=1))
    add_user(database, "alice", password_hash)

    issued = token_of(login(browser))

    assert {row.id for row in sessions(database)} == {
        session_id_of(live),
        session_id_of(issued),
    }
    assert session_id_of(expired) not in everything_stored(database)


def test_registration_also_deletes_expired_sessions(browser, database, password_hash):
    bob = add_user(database, "bob", password_hash)
    add_session(database, bob, datetime.now(UTC) - timedelta(seconds=1))

    issued = token_of(register(browser))

    assert {row.id for row in sessions(database)} == {session_id_of(issued)}


# --- The session cookie -------------------------------------------------------------


@pytest.mark.parametrize("action", ["register", "login"])
def test_session_cookie_flags(app, browser, database, password_hash, action):
    if action == "login":
        add_user(database, "alice", password_hash)
        response = login(browser)
    else:
        response = register(browser)

    name, value, attributes = set_cookie(response)

    assert name == COOKIE
    assert "httponly" in attributes
    assert attributes["samesite"] == "Lax"
    assert attributes["path"] == "/"
    assert attributes["max-age"] == str(168 * 3600)
    assert "secure" not in attributes  # SESSION_COOKIE_SECURE is false by default
    assert "domain" not in attributes
    # 32 random bytes in URL-safe base64, without padding.
    assert len(base64.urlsafe_b64decode(value + "=")) == 32


def test_secure_and_lifetime_follow_the_settings(browser, database, settings_of):
    settings_of(session_cookie_secure=True, session_ttl_hours=2)
    before = datetime.now(UTC)

    _, _, attributes = set_cookie(register(browser))

    assert "secure" in attributes
    assert attributes["max-age"] == "7200"
    (row,) = sessions(database)
    assert before + timedelta(hours=2) <= row.expires_at <= datetime.now(UTC) + timedelta(hours=2)


def test_the_cookie_name_follows_the_setting(app, browser, settings_of):
    settings_of(session_cookie_name="mri_sid")

    name, token, _ = set_cookie(register(browser))

    assert name == "mri_sid"
    assert browser.get("/auth/me").status_code == 200
    saved = post_scan(browser)
    assert saved.json()["id"] is not None
    assert saved.headers.get("cache-control") == "no-store"
    jar = new_browser(app, origin=None)
    jar.cookies.set("mri_sid", token)
    assert jar.post("/auth/logout").json() == MISSING_ORIGIN


def test_me_without_a_session_is_401(browser):
    response = browser.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == NOT_SIGNED_IN
    assert "set-cookie" not in response.headers


def test_an_expired_session_is_401_and_its_cookie_is_cleared(browser, database):
    register(browser)
    with database.session() as session:
        session.execute(update(UserSession).values(expires_at=datetime.now(UTC)))
        session.commit()

    response = browser.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == SESSION_EXPIRED
    assert_cookie_cleared(response)
    assert browser.get("/predictions").json() == NOT_SIGNED_IN  # the cookie is gone


def test_a_session_is_valid_until_it_expires(browser, database):
    register(browser)
    with database.session() as session:
        session.execute(
            update(UserSession).values(expires_at=datetime.now(UTC) + timedelta(seconds=30))
        )
        session.commit()

    assert browser.get("/auth/me").status_code == 200


def test_logout_ends_the_session_on_the_server(app, browser, database):
    token = token_of(register(browser))

    response = browser.post("/auth/logout")

    assert response.status_code == 204
    assert_cookie_cleared(response)
    assert sessions(database) == []
    for path in ("/auth/me", "/predictions", "/stats"):
        replayed = holding(app, token).get(path)
        assert replayed.status_code == 401, path
        assert replayed.json() == SESSION_EXPIRED
    assert post_scan(holding(app, token)).status_code == 401
    assert browser.get("/auth/me").json() == NOT_SIGNED_IN


def test_logout_is_idempotent(app, browser):
    register(browser)

    assert browser.post("/auth/logout").status_code == 204
    again = browser.post("/auth/logout")

    assert again.status_code == 204
    assert_cookie_cleared(again)
    assert holding(app, secrets.token_urlsafe(32)).post("/auth/logout").status_code == 204


@pytest.mark.parametrize(
    "value",
    [
        secrets.token_urlsafe(32),
        "short",
        "!" * 43,
        secrets.token_urlsafe(64),
        "a" * 1000,
    ],
    ids=["random-token", "too-short", "not-base64", "too-long", "huge"],
)
def test_a_forged_cookie_is_401_and_saves_nothing(app, browser, database, value):
    register(browser)

    for path in ("/auth/me", "/predictions", "/stats"):
        response = holding(app, value).get(path)
        assert response.status_code == 401, path
        assert response.json() == SESSION_EXPIRED
        assert_cookie_cleared(response)
    assert post_scan(holding(app, value)).json() == SESSION_EXPIRED
    assert records(database) == []


def test_neither_the_stored_hash_nor_an_altered_token_is_a_session(app, browser, database):
    token = token_of(register(browser))
    altered = token[:-1] + ("A" if token[-1] != "A" else "B")

    for value in (session_id_of(token), altered):
        assert holding(app, value).get("/auth/me").json() == SESSION_EXPIRED


def test_the_database_holds_only_a_hash_of_the_token(browser, database):
    token = token_of(register(browser))

    (row,) = sessions(database)

    assert row.id == hashlib.sha256(token.encode()).hexdigest()
    assert token not in everything_stored(database)


def test_the_password_is_stored_as_an_argon2id_hash(browser, database):
    register(browser, "alice", PASSWORD)

    (user,) = users(database)

    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in everything_stored(database)
    hasher = argon2.PasswordHasher()  # the library's default parameters
    assert hasher.verify(user.password_hash, PASSWORD)
    assert not hasher.check_needs_rehash(user.password_hash)


# --- Deleting the account -----------------------------------------------------------


def test_deleting_the_account_needs_the_password(browser, database):
    register(browser)

    wrong = delete_account(browser, WRONG_PASSWORD)
    missing = browser.request("DELETE", "/auth/me", json={})
    no_body = browser.request("DELETE", "/auth/me")

    assert wrong.status_code == 403
    assert wrong.json() == PASSWORD_MISMATCH
    assert "set-cookie" not in wrong.headers
    for response in (missing, no_body):
        assert response.status_code == 422
        assert response.json() == PASSWORD_BODY
    assert len(users(database)) == 1
    assert browser.get("/auth/me").status_code == 200


def test_deleting_the_account_removes_its_sessions_and_records(app, browser, database):
    register(browser, "alice")
    phone = new_browser(app)
    login(phone, "alice")
    post_scan(browser)
    post_scan(phone)
    bob = new_browser(app)
    register(bob, "bob")
    bob_record = post_scan(bob).json()["id"]

    response = delete_account(browser)

    assert response.status_code == 204
    assert_cookie_cleared(response)
    assert [user.username for user in users(database)] == ["bob"]
    assert [str(record.id) for record in records(database)] == [bob_record]
    assert len(sessions(database)) == 1
    assert phone.get("/auth/me").json() == SESSION_EXPIRED
    assert login(new_browser(app), "alice").status_code == 401
    assert bob.get("/predictions").json()["total"] == 1


def test_deleting_the_account_without_a_session_is_401(browser):
    response = delete_account(browser)

    assert response.status_code == 401
    assert response.json() == NOT_SIGNED_IN


# --- Without a database --------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/auth/register", {"username": "alice", "password": PASSWORD}),
        ("POST", "/auth/login", {"username": "alice", "password": PASSWORD}),
        ("GET", "/auth/me", None),
        ("DELETE", "/auth/me", {"password": PASSWORD}),
    ],
)
def test_accounts_need_a_database(client, method, path, body):
    response = client.request(method, path, json=body)

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


def test_logout_works_without_a_database(client):
    assert client.post("/auth/logout").status_code == 204


# --- CSRF: writes with the session cookie need an allowed Origin -------------------


def test_writes_carrying_the_session_cookie_need_an_allowed_origin(app, database):
    """curl with a cookie jar, or any client that sends the cookie but no Origin."""
    jar = new_browser(app, origin=None)
    assert register(jar).status_code == 201  # no cookie yet, so no Origin needed
    record_id = post_scan(jar, Origin=TEST_ORIGIN).json()["id"]

    refused = [
        post_scan(jar),
        jar.delete(f"/predictions/{record_id}"),
        jar.post("/auth/logout"),
        jar.request("DELETE", "/auth/me", json={"password": PASSWORD}),
        login(jar),
    ]

    for response in refused:
        assert response.status_code == 403
        assert response.json() == MISSING_ORIGIN
    assert jar.get(f"/predictions/{record_id}").status_code == 200
    assert jar.get("/auth/me").status_code == 200
    assert len(records(database)) == 1


def test_a_foreign_origin_is_403_with_or_without_the_cookie(app, browser):
    register(browser)
    evil = {"Origin": "https://evil.example"}

    assert post_scan(browser, **evil).json() == FOREIGN_ORIGIN
    assert browser.post("/auth/logout", headers=evil).json() == FOREIGN_ORIGIN
    assert (
        new_browser(app, origin="https://evil.example").post(
            "/auth/login", json={"username": "alice", "password": PASSWORD}
        ).json()
        == FOREIGN_ORIGIN
    )
    assert browser.get("/auth/me").status_code == 200


def test_reads_with_the_session_cookie_need_no_origin(app, database):
    jar = new_browser(app, origin=None)
    register(jar)

    assert jar.get("/auth/me").status_code == 200
    assert jar.get("/predictions").status_code == 200
    assert jar.get("/stats").status_code == 200


def test_clients_without_a_cookie_need_no_origin(app, database):
    curl = new_browser(app, origin=None)

    assert post_scan(curl).status_code == 200
    assert register(curl).status_code == 201


# --- Order of the checks: Host, Origin, (limit), session, body ----------------------


def test_the_session_is_checked_after_host_and_origin_and_before_the_body(app, database):
    token = secrets.token_urlsafe(32)
    not_an_image = {"file": ("scan.png", b"not an image", "image/png")}
    too_large = {"file": ("scan.png", b"x" * (2 * 1024 * 1024), "image/png")}

    def forged_predict(files, **headers):
        return holding(app, token).post("/predict", files=files, headers=headers or None)

    assert forged_predict(not_an_image, Host="evil.example").status_code == 400
    assert forged_predict(not_an_image, Origin="https://evil.example").json() == FOREIGN_ORIGIN
    # A bad session is refused before the file is read: neither 400 nor 413.
    assert forged_predict(not_an_image).json() == SESSION_EXPIRED
    assert forged_predict(too_large).json() == SESSION_EXPIRED
    broken_json = new_browser(app).request(
        "DELETE", "/auth/me", content=b"{", headers={"Content-Type": "application/json"}
    )
    assert broken_json.json() == NOT_SIGNED_IN


# --- Caching -------------------------------------------------------------------------


def test_auth_responses_are_never_cached(app, browser):
    responses = [
        register(browser),
        register(new_browser(app)),  # 409
        register(new_browser(app), "bob", "short"),  # 422
        browser.get("/auth/me"),
        login(new_browser(app), "alice", WRONG_PASSWORD),  # 401
        login(browser),
        browser.post("/auth/logout"),
        browser.get("/auth/me"),  # 401
    ]
    register(browser, "carol")
    responses.append(delete_account(browser, WRONG_PASSWORD))
    responses.append(delete_account(browser))

    for response in responses:
        assert response.headers.get("cache-control") == "no-store", response.request.url


def test_a_signed_in_prediction_is_not_cached(browser):
    register(browser)

    assert post_scan(browser).headers.get("cache-control") == "no-store"


# --- Logs and representations --------------------------------------------------------


def test_passwords_and_tokens_never_reach_the_logs(app, browser, database, caplog):
    caplog.set_level(logging.DEBUG)
    tokens = [token_of(register(browser, "alice", PASSWORD))]
    register(new_browser(app), "bob", "short-pw-9")
    register(new_browser(app), "alice", PASSWORD)
    login(new_browser(app), "alice", WRONG_PASSWORD)
    login(new_browser(app), "nobody", WRONG_PASSWORD)
    phone = new_browser(app)
    tokens.append(token_of(login(phone)))
    post_scan(browser)
    browser.get("/predictions")
    phone.post("/auth/logout")
    holding(app, "forged-token-value").get("/auth/me")
    delete_account(browser, WRONG_PASSWORD)
    delete_account(browser)

    logged = caplog.text
    secrets_sent = [PASSWORD, WRONG_PASSWORD, "short-pw-9", "forged-token-value", *tokens]
    for secret in secrets_sent + [session_id_of(token) for token in tokens]:
        assert secret not in logged


def test_a_database_failure_while_checking_the_session_logs_no_token(use_database, caplog):
    caplog.set_level(logging.DEBUG)
    database = Database.from_url(UNREACHABLE_POSTGRES)
    token = secrets.token_urlsafe(32)
    try:
        response = holding(use_database(database).app, token).get("/predictions")
    finally:
        database.dispose()

    assert response.status_code == 503
    assert response.json() == {"detail": "База даних недоступна. Спробуйте пізніше."}
    assert "History database unavailable on GET /predictions" in caplog.text
    assert token not in caplog.text
    assert session_id_of(token) not in caplog.text


def test_database_errors_never_show_the_values_sent_with_them():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    try:
        with pytest.raises(OperationalError) as error, engine.connect() as connection:
            connection.execute(text("SELECT :secret FROM no_such_table"), {"secret": "s3cret-v"})
    finally:
        engine.dispose()

    assert "s3cret-v" not in str(error.value)


def test_reprs_hold_no_secrets():
    user = User(id=uuid.uuid4(), username="alice", password_hash="$argon2id$v=19$hash-value")
    now = datetime.now(UTC)
    session = UserSession(id="ab" * 32, user_id=user.id, created_at=now, expires_at=now)
    credentials = CredentialsRequest(username="alice", password="hunter2-hunter2")
    confirmation = DeleteAccountRequest(password="hunter2-hunter2")

    assert "alice" in repr(user)
    assert "hash-value" not in repr(user)
    assert "ab" * 32 not in repr(session)
    for model in (credentials, confirmation):
        assert "hunter2" not in repr(model)
        assert "hunter2" not in str(model)


# --- API description ------------------------------------------------------------------


def test_openapi_documents_the_auth_responses(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    expected = {
        ("/auth/register", "post"): {"201", "403", "409", "422", "429", "503"},
        ("/auth/login", "post"): {"200", "401", "403", "422", "429", "503"},
        ("/auth/logout", "post"): {"204", "403", "429"},
        ("/auth/me", "get"): {"200", "401", "403", "429", "503"},
        ("/auth/me", "delete"): {"204", "401", "403", "422", "429", "503"},
        ("/predictions", "get"): {"200", "401", "403", "422", "429", "503"},
        ("/predictions/{prediction_id}", "get"): {"200", "401", "403", "404", "422", "429"},
        ("/predictions/{prediction_id}", "delete"): {"204", "401", "403", "404", "422", "429"},
        ("/stats", "get"): {"200", "401", "403", "429", "503"},
        ("/predict", "post"): {"200", "400", "401", "403", "413", "415", "422", "429", "503"},
    }

    for (path, method), codes in expected.items():
        assert codes <= set(paths[path][method]["responses"]), (path, method)
    with_body = [("/auth/register", "post"), ("/auth/login", "post"), ("/auth/me", "delete")]
    for path, method in with_body:
        body = paths[path][method]["responses"]["422"]["content"]["application/json"]["schema"]
        assert body["$ref"].endswith("/ErrorResponse"), (path, method)
    password = schema["components"]["schemas"]["CredentialsRequest"]["properties"]["password"]
    assert (password["format"], password["writeOnly"]) == ("password", True)
    assert "401" not in paths["/health"]["get"]["responses"]
    assert "403" not in paths["/health"]["get"]["responses"]


# --- Size of /auth/* bodies ------------------------------------------------------------

AUTH_BODY_TOO_LARGE = {"detail": "Тіло запиту до /auth/* завелике: дозволено не більше 8 КіБ."}
AUTH_BODY_LIMIT = 8 * 1024
JSON = {"Content-Type": "application/json"}


def credentials_body(size: int) -> bytes:
    """A JSON body of exactly `size` bytes with a well-formed name and a long password."""
    head, tail = b'{"username": "alice", "password": "', b'"}'
    return head + b"p" * (size - len(head) - len(tail)) + tail


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/auth/register"), ("POST", "/auth/login"), ("DELETE", "/auth/me")],
)
def test_an_auth_body_over_8_kib_is_413_before_it_is_parsed(browser, method, path):
    register(browser)
    not_even_json = b"{" + b" " * AUTH_BODY_LIMIT

    for body in (credentials_body(AUTH_BODY_LIMIT + 1), not_even_json):
        response = browser.request(method, path, content=body, headers=JSON)
        assert response.status_code == 413, (path, len(body))
        assert response.json() == AUTH_BODY_TOO_LARGE
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/auth/register", "/auth/login"])
def test_a_chunked_auth_body_over_8_kib_is_413_too(browser, path):
    """Without Content-Length the bytes are counted as they arrive."""
    chunks = (b"{" + b" " * 1023 for _ in range(9))

    response = browser.post(path, content=chunks, headers=JSON)

    assert response.status_code == 413
    assert response.json() == AUTH_BODY_TOO_LARGE


def test_an_auth_body_of_exactly_8_kib_is_read(app, browser, password_hash, database):
    add_user(database, "alice", password_hash)

    response = browser.post("/auth/login", content=credentials_body(AUTH_BODY_LIMIT), headers=JSON)

    assert response.status_code == 401
    assert response.json() == LOGIN_FAILED


def test_the_8_kib_limit_is_only_for_auth(app, browser):
    """A scan may still be as large as MAX_UPLOAD_MB (1 MB in the tests)."""
    register(browser)
    scan = {"file": ("scan.png", image_bytes(512, 512) + b"\0" * 64 * 1024, "image/png")}

    assert browser.post("/predict", files=scan).status_code == 200


# --- At most SESSION_MAX_PER_USER sessions per user ------------------------------------


def test_a_user_keeps_at_most_ten_sessions(app, database, password_hash):
    add_user(database, "alice", password_hash)
    add_user(database, "bob", password_hash)
    bob_tokens = [token_of(login(new_browser(app), "bob")) for _ in range(2)]

    tokens = [token_of(login(new_browser(app), "alice")) for _ in range(12)]

    alice_sessions = {session_id_of(token) for token in tokens}
    stored = {row.id for row in sessions(database)}
    assert stored & alice_sessions == {session_id_of(token) for token in tokens[-10:]}
    assert {session_id_of(token) for token in bob_tokens} <= stored
    for oldest in tokens[:2]:
        assert holding(app, oldest).get("/auth/me").json() == SESSION_EXPIRED
    assert holding(app, tokens[2]).get("/auth/me").status_code == 200
    assert holding(app, tokens[-1]).get("/auth/me").status_code == 200


def test_registration_counts_towards_the_sessions_of_a_user(app, browser, database, settings_of):
    settings_of(session_max_per_user=2)
    first = token_of(register(browser, "alice"))

    later = [token_of(login(new_browser(app), "alice")) for _ in range(2)]

    assert {row.id for row in sessions(database)} == {session_id_of(token) for token in later}
    assert holding(app, first).get("/auth/me").json() == SESSION_EXPIRED
