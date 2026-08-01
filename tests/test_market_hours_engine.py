from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import time
from datetime import timedelta

from traderos.domain.services.market_hours_engine import CRYPTO_24_7
from traderos.domain.services.market_hours_engine import FOREX_24_5
from traderos.domain.services.market_hours_engine import US_EQUITY
from traderos.domain.services.market_hours_engine import US_FUTURES
from traderos.domain.services.market_hours_engine import MarketHoursEngine
from traderos.domain.services.market_hours_engine import MarketSession

WED = datetime(2026, 7, 29, tzinfo=UTC)  # Wednesday
SAT = datetime(2026, 8, 1, tzinfo=UTC)  # Saturday
SUN = datetime(2026, 8, 2, tzinfo=UTC)  # Sunday
MON = datetime(2026, 8, 3, tzinfo=UTC)  # Monday


class TestMarketSession:
    def test_contains_normal_session(self) -> None:
        session = MarketSession(open=time(9, 30), close=time(16, 0))
        assert session.contains(datetime(2026, 7, 29, 10, 0, tzinfo=UTC))
        assert not session.contains(datetime(2026, 7, 29, 17, 0, tzinfo=UTC))

    def test_contains_boundaries(self) -> None:
        session = MarketSession(open=time(9, 30), close=time(16, 0))
        assert session.contains(datetime(2026, 7, 29, 9, 30, tzinfo=UTC))
        assert session.contains(datetime(2026, 7, 29, 16, 0, tzinfo=UTC))
        assert not session.contains(datetime(2026, 7, 29, 9, 29, tzinfo=UTC))

    def test_contains_overnight_session(self) -> None:
        session = MarketSession(open=time(18, 0), close=time(17, 0))
        assert session.contains(datetime(2026, 7, 29, 19, 0, tzinfo=UTC))
        assert session.contains(datetime(2026, 7, 29, 5, 0, tzinfo=UTC))
        assert not session.contains(datetime(2026, 7, 29, 17, 30, tzinfo=UTC))


class TestMarketHoursEngineIsOpen:
    def test_unknown_symbol_always_open(self) -> None:
        engine = MarketHoursEngine()
        assert engine.is_open("BTC/USD", at=WED)

    def test_empty_calendar_is_open(self) -> None:
        engine = MarketHoursEngine()
        assert engine.is_open("ANY", at=WED)

    def test_weekend_closed_for_session_market(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        assert engine.is_open("AAPL", at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC))
        assert not engine.is_open("AAPL", at=SAT)
        assert not engine.is_open("AAPL", at=SUN)

    def test_outside_session_hours_closed(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        assert not engine.is_open("AAPL", at=datetime(2026, 7, 29, 8, 0, tzinfo=UTC))

    def test_forex_24_5_open_weekday_closed_weekend(self) -> None:
        engine = MarketHoursEngine({"EURUSD": FOREX_24_5})
        assert engine.is_open("EURUSD", at=datetime(2026, 7, 29, 3, 0, tzinfo=UTC))
        assert not engine.is_open("EURUSD", at=SAT)

    def test_crypto_24_7_always_open(self) -> None:
        engine = MarketHoursEngine({"BTC/USD": CRYPTO_24_7})
        assert engine.is_open("BTC/USD", at=SAT)
        assert engine.is_open("BTC/USD", at=MON)

    def test_is_open_defaults_to_now(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        result = engine.is_open("AAPL")
        assert isinstance(result, bool)

    def test_overnight_futures_session(self) -> None:
        engine = MarketHoursEngine({"ES": US_FUTURES})
        assert engine.is_open("ES", at=datetime(2026, 7, 29, 19, 0, tzinfo=UTC))
        assert engine.is_open("ES", at=datetime(2026, 7, 29, 3, 0, tzinfo=UTC))


class TestMarketHoursEngineNextOpen:
    def test_unknown_symbol_returns_now(self) -> None:
        engine = MarketHoursEngine()
        assert engine.next_open("BTC/USD", at=WED) == WED

    def test_crypto_returns_now(self) -> None:
        engine = MarketHoursEngine({"BTC/USD": CRYPTO_24_7})
        assert engine.next_open("BTC/USD", at=SAT) == SAT

    def test_forex_returns_now(self) -> None:
        engine = MarketHoursEngine({"EURUSD": FOREX_24_5})
        assert engine.next_open("EURUSD", at=SAT) == SAT

    def test_before_open_returns_same_day_open(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        at = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)
        expected = datetime(2026, 7, 29, 9, 30, tzinfo=UTC)
        assert engine.next_open("AAPL", at=at) == expected

    def test_after_close_returns_next_day_open(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        at = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
        expected = datetime(2026, 7, 30, 9, 30, tzinfo=UTC)
        assert engine.next_open("AAPL", at=at) == expected

    def test_weekend_returns_monday_open(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        assert engine.next_open("AAPL", at=SAT) == datetime(2026, 8, 3, 9, 30, tzinfo=UTC)
        assert engine.next_open("AAPL", at=SUN) == datetime(2026, 8, 3, 9, 30, tzinfo=UTC)

    def test_before_open_on_friday_returns_same_day(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        fri = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
        assert engine.next_open("AAPL", at=fri) == datetime(2026, 7, 31, 9, 30, tzinfo=UTC)


class TestMarketHoursEngineTimeToClose:
    def test_unknown_symbol_zero(self) -> None:
        engine = MarketHoursEngine()
        assert engine.time_to_close("BTC/USD", at=WED) == timedelta(seconds=0)

    def test_crypto_zero(self) -> None:
        engine = MarketHoursEngine({"BTC/USD": CRYPTO_24_7})
        assert engine.time_to_close("BTC/USD", at=WED) == timedelta(seconds=0)

    def test_forex_zero(self) -> None:
        engine = MarketHoursEngine({"EURUSD": FOREX_24_5})
        assert engine.time_to_close("EURUSD", at=WED) == timedelta(seconds=0)

    def test_before_close_returns_remaining(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        at = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)
        assert engine.time_to_close("AAPL", at=at) == timedelta(hours=1)

    def test_after_close_returns_next_day_close(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        at = datetime(2026, 7, 29, 16, 30, tzinfo=UTC)
        assert engine.time_to_close("AAPL", at=at) == timedelta(hours=23, minutes=30)

    def test_register_market(self) -> None:
        engine = MarketHoursEngine()
        engine.register_market("AAPL", US_EQUITY)
        assert engine._calendar["AAPL"] is US_EQUITY

    def test_time_to_close_is_delta(self) -> None:
        engine = MarketHoursEngine({"AAPL": US_EQUITY})
        result = engine.time_to_close("AAPL", at=WED)
        assert isinstance(result, timedelta)


def test_market_session_default_timezone() -> None:
    session = MarketSession(open=time(9, 30), close=time(16, 0))
    assert session.timezone == "UTC"
