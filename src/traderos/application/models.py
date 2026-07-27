from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TradingMode(Enum):
    PAPER = "paper"
    LIVE = "live"
    BACKTEST = "backtest"


@dataclass
class CycleResult:
    market_id: uuid.UUID
    signals: int
    trades: int
    errors: list[str]
    duration_ms: float
    timestamp: datetime
