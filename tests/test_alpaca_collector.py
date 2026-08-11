import os
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from unittest.mock import patch

import pandas as pd
import pytest

from traderos.domain.collectors.base import CollectorType
from traderos.infrastructure.collectors.alpaca_collector import AlpacaCollector
from traderos.infrastructure.collectors.alpaca_collector import _frame_interval

pytest.importorskip("alpaca")

from alpaca.data.timeframe import TimeFrame  # noqa: E402
from alpaca.data.timeframe import TimeFrameUnit  # noqa: E402


def _fake_client() -> MagicMock:
    client = MagicMock()
    bars = MagicMock()
    client.get_crypto_bars.return_value = bars
    return client


def _frame_eq(a: TimeFrame, amount: int, unit: TimeFrameUnit) -> bool:
    return a.amount == amount and a.unit == unit


class TestFrameInterval:
    def test_minute_mapping(self) -> None:
        assert _frame_eq(_frame_interval("1m"), 1, TimeFrameUnit.Minute)

    def test_five_minute_mapping(self) -> None:
        assert _frame_eq(_frame_interval("5m"), 5, TimeFrameUnit.Minute)

    def test_fifteen_minute_mapping(self) -> None:
        assert _frame_eq(_frame_interval("15m"), 15, TimeFrameUnit.Minute)

    def test_hour_mapping(self) -> None:
        assert _frame_eq(_frame_interval("1h"), 1, TimeFrameUnit.Hour)

    def test_four_hour_mapping(self) -> None:
        assert _frame_eq(_frame_interval("4h"), 6, TimeFrameUnit.Hour)

    def test_day_mapping(self) -> None:
        assert _frame_eq(_frame_interval("1d"), 1, TimeFrameUnit.Day)

    def test_unknown_defaults_to_hour(self) -> None:
        assert _frame_eq(_frame_interval("3m"), 1, TimeFrameUnit.Hour)


class TestAlpacaCollector:
    def test_collector_type(self) -> None:
        assert AlpacaCollector().collector_type == CollectorType.ALPACA

    def test_validate_symbol_valid(self) -> None:
        assert AlpacaCollector().validate_symbol("BTC/USD") is True

    def test_validate_symbol_invalid(self) -> None:
        assert AlpacaCollector().validate_symbol("") is False
        assert AlpacaCollector().validate_symbol("BTCUSD") is False
        assert AlpacaCollector().validate_symbol("B/U") is False

    @patch(
        "alpaca.data.historical.crypto.CryptoHistoricalDataClient",
    )
    def test_fetch_historical_returns_empty_when_no_df(self, mock_client_cls) -> None:
        client = _fake_client()
        client.get_crypto_bars.return_value.df = None
        mock_client_cls.return_value = client
        result = AlpacaCollector("k", "s").fetch_historical("BTC/USD", "1d")
        assert result == []
        mock_client_cls.assert_called_once_with("k", "s")

    @patch("alpaca.data.historical.crypto.CryptoHistoricalDataClient")
    def test_fetch_historical_uses_env_keys(self, mock_client_cls) -> None:
        client = _fake_client()
        client.get_crypto_bars.return_value.df = None
        mock_client_cls.return_value = client
        with patch.dict(
            os.environ,
            {"ALPACA_API_KEY": "env-key", "ALPACA_SECRET_KEY": "env-secret"},
        ):
            AlpacaCollector().fetch_historical("BTC/USD", "1d")
        mock_client_cls.assert_called_once_with("env-key", "env-secret")

    @patch("alpaca.data.historical.crypto.CryptoHistoricalDataClient")
    def test_fetch_historical_parses_multilevel_df(self, mock_client_cls) -> None:
        ts = pd.Timestamp("2024-01-01T10:00:00Z")
        df = pd.DataFrame(
            {
                "open": [44000.0],
                "high": [44500.0],
                "low": [43900.0],
                "close": [44200.0],
                "volume": [1234.5],
            },
            index=pd.MultiIndex.from_tuples(
                [("BTC/USD", ts)],
                names=["symbol", "timestamp"],
            ),
        )
        client = _fake_client()
        client.get_crypto_bars.return_value.df = df
        mock_client_cls.return_value = client
        result = AlpacaCollector("k", "s").fetch_historical("BTC/USD", "1h")
        assert len(result) == 1
        item = result[0]
        assert item.symbol == "BTC/USD"
        assert item.timestamp == ts.to_pydatetime()
        assert item.open == Decimal("44000")
        assert item.high == Decimal("44500")
        assert item.low == Decimal("43900")
        assert item.close == Decimal("44200")
        assert item.volume == Decimal("1234.5")

    @patch("alpaca.data.historical.crypto.CryptoHistoricalDataClient")
    def test_fetch_historical_parses_plain_string_index(self, mock_client_cls) -> None:
        df = pd.DataFrame(
            {
                "open": [100.0],
                "high": [105.0],
                "low": [99.0],
                "close": [102.0],
                "volume": [50.0],
            },
            index=pd.Index(["2024-02-01T00:00:00"], name="timestamp"),
        )
        client = _fake_client()
        client.get_crypto_bars.return_value.df = df
        mock_client_cls.return_value = client
        result = AlpacaCollector("k", "s").fetch_historical("BTC/USD", "1h")
        assert len(result) == 1
        assert result[0].timestamp == datetime.fromisoformat("2024-02-01T00:00:00")
