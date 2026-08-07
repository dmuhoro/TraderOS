from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.entities.user import User
from traderos.domain.entities.user import UserApiKey
from traderos.domain.entities.user import UserRole
from traderos.domain.entities.user import UserSession
from traderos.domain.entities.user import UserStatus
from traderos.domain.repositories.user_repository import UserRepository
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteUserRepository(UserRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
            """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                label TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                prefix TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """)
        self.conn.commit()

    def create_user(self, user: User) -> User:
        self.conn.execute(
            "INSERT INTO users (id, username, password_hash, role, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(user.id),
                user.username,
                user.password_hash,
                user.role.value,
                user.status.value,
                user.created_at.isoformat(),
            ),
        )
        self.conn.commit()
        return user

    def get_user(self, user_id: uuid.UUID) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_username(self, username: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._user_from_row(row) if row else None

    def list_users(self) -> list[User]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._user_from_row(row) for row in rows]

    def create_session(self, session: UserSession) -> UserSession:
        self.conn.execute(
            "INSERT INTO user_sessions (token_hash, user_id, expires_at, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                session.token_hash,
                str(session.user_id),
                session.expires_at.isoformat(),
                session.created_at.isoformat(),
            ),
        )
        self.conn.commit()
        return session

    def get_session(self, token_hash: str) -> UserSession | None:
        row = self.conn.execute(
            "SELECT * FROM user_sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            return None
        return UserSession(
            token_hash=row["token_hash"],
            user_id=to_uuid(row["user_id"]),
            expires_at=to_dt(row["expires_at"]),
            created_at=to_dt(row["created_at"]),
        )

    def delete_session(self, token_hash: str) -> None:
        self.conn.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))
        self.conn.commit()

    def create_api_key(self, key: UserApiKey) -> UserApiKey:
        self.conn.execute(
            "INSERT INTO user_api_keys (id, user_id, label, key_hash, prefix, created_at,"
            " revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(key.id),
                str(key.user_id),
                key.label,
                key.key_hash,
                key.prefix,
                key.created_at.isoformat(),
                key.revoked_at.isoformat() if key.revoked_at else None,
            ),
        )
        self.conn.commit()
        return key

    def get_api_key(self, key_hash: str) -> UserApiKey | None:
        row = self.conn.execute(
            "SELECT * FROM user_api_keys WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        return self._key_from_row(row) if row else None

    def list_api_keys(self, user_id: uuid.UUID) -> list[UserApiKey]:
        rows = self.conn.execute(
            "SELECT * FROM user_api_keys WHERE user_id = ? ORDER BY created_at",
            (str(user_id),),
        ).fetchall()
        return [self._key_from_row(row) for row in rows]

    def revoke_api_key(self, key_id: uuid.UUID) -> None:
        self.conn.execute(
            "UPDATE user_api_keys SET revoked_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), str(key_id)),
        )
        self.conn.commit()

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        return User(
            id=to_uuid(row["id"]),
            username=row["username"],
            password_hash=row["password_hash"],
            role=UserRole(row["role"]),
            status=UserStatus(row["status"]),
            created_at=to_dt(row["created_at"]),
        )

    @staticmethod
    def _key_from_row(row: sqlite3.Row) -> UserApiKey:
        revoked = to_dt(row["revoked_at"]) if row["revoked_at"] else None
        return UserApiKey(
            id=to_uuid(row["id"]),
            user_id=to_uuid(row["user_id"]),
            label=row["label"],
            key_hash=row["key_hash"],
            prefix=row["prefix"],
            created_at=to_dt(row["created_at"]),
            revoked_at=revoked,
        )
