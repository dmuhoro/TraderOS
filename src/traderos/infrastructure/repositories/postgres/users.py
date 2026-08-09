from __future__ import annotations

import uuid
from typing import Any

from traderos.domain.entities.user import User
from traderos.domain.entities.user import UserApiKey
from traderos.domain.entities.user import UserRole
from traderos.domain.entities.user import UserSession
from traderos.domain.entities.user import UserStatus
from traderos.domain.repositories.user_repository import UserRepository
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_uuid

_USERS = "users"
_SESSIONS = "user_sessions"
_API_KEYS = "user_api_keys"


class PostgresUserRepository(UserRepository):
    """PostgreSQL-backed user/account store mirroring the SQLite repository.

    Tables are created by the ``v008_user_accounts`` migration (and also
    create-if-absent here so a cold boot against any PG database works). All
    password/session material is stored already-hashed by ``AccountService``;
    this repository never sees, stores, or logs plaintext credentials.
    """

    def __init__(self, connection: Any) -> None:
        self.conn = connection
        self._create_tables()

    def _create_tables(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_USERS} (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_SESSIONS} (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES {_USERS}(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_API_KEYS} (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES {_USERS}(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                )
            """)
        self.conn.commit()

    def create_user(self, user: User) -> User:
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_USERS} (id, username, password_hash, role, status, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
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
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_USERS} WHERE id = %s", (str(user_id),))
            row = cur.fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_username(self, username: str) -> User | None:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_USERS} WHERE username = %s", (username,))
            row = cur.fetchone()
        return self._user_from_row(row) if row else None

    def list_users(self) -> list[User]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_USERS} ORDER BY created_at")
            rows = cur.fetchall()
        return [self._user_from_row(row) for row in rows]

    def create_session(self, session: UserSession) -> UserSession:
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_SESSIONS} (token_hash, user_id, expires_at, created_at)"
                " VALUES (%s, %s, %s, %s)",
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
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_SESSIONS} WHERE token_hash = %s", (token_hash,))
            row = cur.fetchone()
        if row is None:
            return None
        return UserSession(
            token_hash=row[0],
            user_id=to_uuid(row[1]),
            expires_at=to_dt(row[2]),
            created_at=to_dt(row[3]),
        )

    def delete_session(self, token_hash: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_SESSIONS} WHERE token_hash = %s", (token_hash,))
        self.conn.commit()

    def create_api_key(self, key: UserApiKey) -> UserApiKey:
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_API_KEYS} (id, user_id, label, key_hash, prefix, created_at,"
                " revoked_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
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
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_API_KEYS} WHERE key_hash = %s", (key_hash,))
            row = cur.fetchone()
        return self._key_from_row(row) if row else None

    def list_api_keys(self, user_id: uuid.UUID) -> list[UserApiKey]:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {_API_KEYS} WHERE user_id = %s ORDER BY created_at",
                (str(user_id),),
            )
            rows = cur.fetchall()
        return [self._key_from_row(row) for row in rows]

    def revoke_api_key(self, key_id: uuid.UUID) -> None:
        from datetime import UTC
        from datetime import datetime

        with self.conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_API_KEYS} SET revoked_at = %s WHERE id = %s",
                (datetime.now(UTC).isoformat(), str(key_id)),
            )
        self.conn.commit()

    @staticmethod
    def _user_from_row(row: Any) -> User:
        return User(
            id=to_uuid(row[0]),
            username=row[1],
            password_hash=row[2],
            role=UserRole(row[3]),
            status=UserStatus(row[4]),
            created_at=to_dt(row[5]),
        )

    @staticmethod
    def _key_from_row(row: Any) -> UserApiKey:
        revoked = to_dt(row[6]) if row[6] else None
        return UserApiKey(
            id=to_uuid(row[0]),
            user_id=to_uuid(row[1]),
            label=row[2],
            key_hash=row[3],
            prefix=row[4],
            created_at=to_dt(row[5]),
            revoked_at=revoked,
        )
