from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import time
from datetime import timedelta


@dataclass(frozen=True)
class MarketSession:
    open: time
    close: time
    timezone: str = "UTC"

    def contains(self, dt: datetime) -> bool:
        t = dt.time()
        if self.open == self.close:
            return True
        if self.open <= self.close:
            return self.open <= t <= self.close
        return t >= self.open or t <= self.close


FOREX_24_5 = MarketSession(open=time(0, 0), close=time(0, 0))
US_EQUITY = MarketSession(open=time(9, 30), close=time(16, 0))
US_FUTURES = MarketSession(open=time(18, 0), close=time(17, 0))
CRYPTO_24_7 = MarketSession(open=time(0, 0), close=time(0, 0))


class MarketHoursEngine:
    def __init__(self, market_calendar: dict[str, MarketSession] | None = None) -> None:
        self._calendar = market_calendar or {}

    def register_market(self, symbol: str, session: MarketSession) -> None:
        self._calendar[symbol] = session

    def is_open(self, symbol: str, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        session = self._calendar.get(symbol)
        if session is None:
            return True
        if session is CRYPTO_24_7:
            return True
        if session is FOREX_24_5:
            return now.weekday() < 5
        if now.weekday() >= 5:
            return False
        return session.contains(now)

    def next_open(self, symbol: str, at: datetime | None = None) -> datetime:
        now = at or datetime.now(UTC)
        session = self._calendar.get(symbol)
        if session is None:
            return now
        if session is FOREX_24_5 or session is CRYPTO_24_7:
            return now
        candidate = datetime.combine(now.date(), session.open, tzinfo=UTC)
        if now > candidate:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def time_to_close(self, symbol: str, at: datetime | None = None) -> timedelta:
        now = at or datetime.now(UTC)
        session = self._calendar.get(symbol)
        if session is None:
            return timedelta(seconds=0)
        if session is FOREX_24_5 or session is CRYPTO_24_7:
            return timedelta(seconds=0)
        market_close = datetime.combine(now.date(), session.close, tzinfo=UTC)
        if now > market_close:
            market_close += timedelta(days=1)
        return market_close - now
