from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum


class AssetClass(Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    EQUITY = "equity"
    FUTURES = "futures"


class MarketStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


@dataclass(frozen=True)
class Market:
    symbol: str
    asset_class: AssetClass
    exchange: str
    status: MarketStatus = MarketStatus.ACTIVE
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
