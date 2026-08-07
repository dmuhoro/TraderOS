from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from enum import Enum
from enum import StrEnum


class UserRole(Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    username: str
    password_hash: str
    role: UserRole
    status: UserStatus
    created_at: datetime


@dataclass(frozen=True)
class UserSession:
    token_hash: str
    user_id: uuid.UUID
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class UserApiKey:
    id: uuid.UUID
    user_id: uuid.UUID
    label: str
    key_hash: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)
