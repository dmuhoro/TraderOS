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


@dataclass
class RetailOrderResult:
    """Result of a retail-seam order submission.

    ``allowed`` is False only when the real risk gate refused the order before
    any broker call (fail-closed). ``order_id`` is the persisted trade/order id
    when the fill happened, else None.
    """

    allowed: bool
    reason: str
    order_id: str | None
    signal_id: str = ""
