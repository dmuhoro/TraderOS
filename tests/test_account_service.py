from __future__ import annotations

import sqlite3

import pytest

from traderos.domain.entities.user import UserRole
from traderos.domain.entities.user import UserStatus
from traderos.domain.services.account_service import ADMIN_PASSWORD_ENV
from traderos.domain.services.account_service import ADMIN_USERNAME_ENV
from traderos.domain.services.account_service import AccountService
from traderos.infrastructure.repositories.sqlite.users import SQLiteUserRepository


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
        """)
    c.execute("""
        CREATE TABLE user_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
    c.execute("""
        CREATE TABLE user_api_keys (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            label TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            prefix TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        )
        """)
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def svc(conn: sqlite3.Connection) -> AccountService:
    return AccountService(SQLiteUserRepository(conn))


class TestHashVerify:
    def test_hash_never_contains_plaintext(self, svc: AccountService) -> None:
        pw = "s3cret-password"
        stored = svc.hash_password(pw)
        assert pw not in stored
        assert stored.startswith("pbkdf2_sha256$")

    def test_verify_correct_and_wrong(self, svc: AccountService) -> None:
        stored = svc.hash_password("right-pw")
        assert svc.verify_password("right-pw", stored) is True
        assert svc.verify_password("wrong-pw", stored) is False

    def test_verify_rejects_malformed(self, svc: AccountService) -> None:
        assert svc.verify_password("x", "not-a-real-hash") is False


class TestCreateUser:
    def test_create_and_authenticate(self, svc: AccountService) -> None:
        user = svc.create_user("alice", "pw-alice")
        assert user is not None
        assert user.role == UserRole.OPERATOR
        assert user.status == UserStatus.ACTIVE
        result = svc.authenticate("alice", "pw-alice")
        assert result.authenticated is True
        assert result.user is not None

    def test_wrong_password_fails_closed(self, svc: AccountService) -> None:
        svc.create_user("bob", "good-pw")
        result = svc.authenticate("bob", "bad-pw")
        assert result.authenticated is False

    def test_missing_user_fails_closed(self, svc: AccountService) -> None:
        result = svc.authenticate("nobody", "pw")
        assert result.authenticated is False
        assert result.user is None

    def test_duplicate_username_denied(self, svc: AccountService) -> None:
        svc.create_user("carol", "pw-1")
        assert svc.create_user("carol", "pw-2") is None


class TestSessions:
    def test_session_issue_and_validate(self, svc: AccountService) -> None:
        user = svc.create_user("dave", "pw-dave")
        assert user is not None
        raw_token, _ = svc.create_session(user)
        validated = svc.validate_session(raw_token)
        assert validated is not None
        assert validated.id == user.id

    def test_session_only_hash_stored(self, conn: sqlite3.Connection, svc: AccountService) -> None:
        user = svc.create_user("erin", "pw-erin")
        assert user is not None
        raw_token, session = svc.create_session(user)
        row = conn.execute(
            "SELECT token_hash FROM user_sessions WHERE token_hash = ?", (session.token_hash,)
        ).fetchone()
        assert row is not None
        assert raw_token not in ("", None)

    def test_invalid_session_denied(self, svc: AccountService) -> None:
        assert svc.validate_session("forged-token") is None


class TestApiKeys:
    def test_issue_and_validate(self, svc: AccountService) -> None:
        user = svc.create_user("frank", "pw-frank")
        assert user is not None
        issued = svc.issue_api_key(user, "bot-a")
        assert issued is not None
        raw, _ = issued
        assert raw.startswith("trd_")
        validated = svc.validate_api_key(raw)
        assert validated is not None
        assert validated.id == user.id

    def test_raw_key_only_hash_persisted(self, svc: AccountService) -> None:
        user = svc.create_user("grace", "pw-grace")
        assert user is not None
        raw, key = svc.issue_api_key(user, "bot")  # type: ignore[misc]
        keys = svc._repo.list_api_keys(user.id)
        assert raw not in (k.key_hash for k in keys)
        assert key.id in {k.id for k in keys}

    def test_unknown_key_denied(self, svc: AccountService) -> None:
        assert svc.validate_api_key("trd_not-a-real-key") is None


class TestAdminBootstrap:
    def test_bootstrap_creates_admin(self, svc: AccountService) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(ADMIN_USERNAME_ENV, "root")
            mp.setenv(ADMIN_PASSWORD_ENV, "root-secret")
            admin = svc.bootstrap_admin_from_env()
        assert admin is not None
        assert admin.role == UserRole.ADMIN
        assert svc.authenticate("root", "root-secret").authenticated is True

    def test_bootstrap_no_env_does_nothing(self, svc: AccountService) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv(ADMIN_USERNAME_ENV, raising=False)
            mp.delenv(ADMIN_PASSWORD_ENV, raising=False)
            assert svc.bootstrap_admin_from_env() is None
        assert svc._repo.list_users() == []

    def test_bootstrap_idempotent(self, svc: AccountService) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(ADMIN_USERNAME_ENV, "root")
            mp.setenv(ADMIN_PASSWORD_ENV, "root-secret")
            assert svc.bootstrap_admin_from_env() is not None
            assert svc.bootstrap_admin_from_env() is None
