from __future__ import annotations

import uuid
from abc import ABC
from abc import abstractmethod

from traderos.domain.entities.user import User
from traderos.domain.entities.user import UserApiKey
from traderos.domain.entities.user import UserSession


class UserRepository(ABC):
    """Persists user accounts, sessions, and per-user API keys."""

    @abstractmethod
    def create_user(self, user: User) -> User: ...

    @abstractmethod
    def get_user(self, user_id: uuid.UUID) -> User | None: ...

    @abstractmethod
    def get_user_by_username(self, username: str) -> User | None: ...

    @abstractmethod
    def list_users(self) -> list[User]: ...

    @abstractmethod
    def create_session(self, session: UserSession) -> UserSession: ...

    @abstractmethod
    def get_session(self, token_hash: str) -> UserSession | None: ...

    @abstractmethod
    def delete_session(self, token_hash: str) -> None: ...

    @abstractmethod
    def create_api_key(self, key: UserApiKey) -> UserApiKey: ...

    @abstractmethod
    def get_api_key(self, key_hash: str) -> UserApiKey | None: ...

    @abstractmethod
    def list_api_keys(self, user_id: uuid.UUID) -> list[UserApiKey]: ...

    @abstractmethod
    def revoke_api_key(self, key_id: uuid.UUID) -> None: ...
