from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from traderos.domain.entities.user import User
from traderos.domain.entities.user import UserApiKey
from traderos.domain.entities.user import UserRole
from traderos.domain.entities.user import UserSession
from traderos.domain.entities.user import UserStatus
from traderos.domain.entities.user import utcnow
from traderos.domain.ports import AuditPort
from traderos.domain.repositories.user_repository import UserRepository

ADMIN_USERNAME_ENV = "TRADEROS_ADMIN_USERNAME"
ADMIN_PASSWORD_ENV = "TRADEROS_ADMIN_PASSWORD"
SESSION_TTL_SECONDS = 3600 * 12  # 12h operator session


@dataclass(frozen=True)
class AuthResult:
    user: User | None
    authenticated: bool


class AccountService:
    """User/account domain service: hashed credentials, sessions, per-user keys.

    Fail-closed by default: a missing user, wrong password, expired session, or
    revoked/invalid key all deny. Credential verification uses PBKDF2-HMAC-SHA256
    (100k iterations) with per-user random salt and a constant-time compare.

    Plaintext passwords and raw API keys are never persisted — only their
    salted-derived hashes.
    """

    def __init__(
        self,
        repository: UserRepository,
        *,
        session_ttl_seconds: int = SESSION_TTL_SECONDS,
        pbkdf2_iterations: int = 100_000,
        audit: AuditPort | None = None,
    ) -> None:
        self._repo = repository
        self._ttl = session_ttl_seconds
        self._iterations = pbkdf2_iterations
        self._audit = audit

    # --- credential hashing (store) ---
    def hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, self._iterations)
        return (
            "pbkdf2_sha256$"
            + str(self._iterations)
            + "$"
            + base64.b64encode(salt).decode()
            + "$"
            + base64.b64encode(digest).decode()
        )

    def verify_password(self, password: str, stored: str) -> bool:
        try:
            scheme, iter_str, salt_b64, digest_b64 = stored.split("$")
            if scheme != "pbkdf2_sha256":
                return False
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iter_str))
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False

    # --- users ---
    def create_user(
        self,
        username: str,
        password: str,
        role: UserRole = UserRole.OPERATOR,
    ) -> User | None:
        username = username.strip()
        if not username or not password:
            return None
        if self._repo.get_user_by_username(username) is not None:
            return None
        user = User(
            id=uuid.uuid4(),
            username=username,
            password_hash=self.hash_password(password),
            role=role,
            status=UserStatus.ACTIVE,
            created_at=utcnow(),
        )
        self._repo.create_user(user)
        if self._audit:
            self._audit.record("user.created", "account-service", username, role.value)
        return user

    def authenticate(self, username: str, password: str) -> AuthResult:
        user = self._repo.get_user_by_username(username)
        if user is None:
            return AuthResult(user=None, authenticated=False)
        if user.status != UserStatus.ACTIVE:
            return AuthResult(user=user, authenticated=False)
        ok = self.verify_password(password, user.password_hash)
        if self._audit:
            self._audit.record("user.login", username, "account-service", "ok" if ok else "denied")
        return AuthResult(user=user, authenticated=ok)

    # --- sessions ---
    def create_session(self, user: User) -> tuple[str, UserSession]:
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)
        session = UserSession(
            token_hash=token_hash,
            user_id=user.id,
            expires_at=utcnow() + timedelta(seconds=self._ttl),
            created_at=utcnow(),
        )
        self._repo.create_session(session)
        return raw_token, session

    def validate_session(self, raw_token: str) -> User | None:
        if not raw_token:
            return None
        session = self._repo.get_session(self._hash_token(raw_token))
        if session is None:
            return None
        if utcnow() > session.expires_at:
            self._repo.delete_session(session.token_hash)
            return None
        return self._repo.get_user(session.user_id)

    def revoke_session(self, raw_token: str) -> None:
        """Invalidate a session token server-side (fail-closed logout)."""
        if not raw_token:
            return
        session = self._repo.get_session(self._hash_token(raw_token))
        if session is not None:
            self._repo.delete_session(session.token_hash)

    # --- per-user API keys ---
    def issue_api_key(self, user: User, label: str) -> tuple[str, UserApiKey] | None:
        if user.status != UserStatus.ACTIVE:
            return None
        raw = "trd_" + secrets.token_urlsafe(24)
        key = UserApiKey(
            id=uuid.uuid4(),
            user_id=user.id,
            label=label,
            key_hash=self._hash_token(raw),
            prefix=raw[:10],
            created_at=utcnow(),
        )
        self._repo.create_api_key(key)
        return raw, key

    def validate_api_key(self, raw_key: str) -> User | None:
        if not raw_key:
            return None
        found = self._repo.get_api_key(self._hash_token(raw_key))
        if found is None or found.revoked_at is not None:
            return None
        return self._repo.get_user(found.user_id)

    # --- admin bootstrap from env ---
    def bootstrap_admin_from_env(self) -> User | None:
        username = os.getenv(ADMIN_USERNAME_ENV, "").strip()
        password = os.getenv(ADMIN_PASSWORD_ENV, "")
        if not username or not password:
            return None
        if self._repo.get_user_by_username(username) is not None:
            return None
        return self.create_user(username, password, role=UserRole.ADMIN)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
