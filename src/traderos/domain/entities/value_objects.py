from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple


class Timeframe(StrEnum):
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"


class OHLCV(NamedTuple):
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def validate(self) -> None:
        if self.low > self.high:
            raise ValueError(f"low ({self.low}) > high ({self.high})")
        if self.open < 0 or self.high < 0 or self.low < 0 or self.close < 0:
            raise ValueError("Prices must be non-negative")
        if self.volume < 0:
            raise ValueError("Volume must be non-negative")


@dataclass(frozen=True)
class EquityCurve:
    points: tuple[tuple[datetime, float], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Metrics:
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    expectancy: float = 0.0
    recovery_factor: float = 0.0


@dataclass(frozen=True)
class SessionConfig:
    name: str
    start_hour: int
    end_hour: int
    session_id: uuid.UUID = field(default_factory=uuid.uuid4)
