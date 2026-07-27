from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType
from traderos.domain.services.data_normalizer import DataNormalizer
from traderos.domain.services.data_validator import DataValidator
from traderos.infrastructure.collectors import BinanceCollector
from traderos.infrastructure.collectors import MockDataCollector
from traderos.infrastructure.collectors import YFinanceCollector


class TestCollectorRegistry:
    def test_register_and_get(self) -> None:
        registry = CollectorRegistry()
        mock = MockDataCollector()
        registry.register(mock)
        assert registry.get(CollectorType.MOCK) is mock

    def test_list_types(self) -> None:
        registry = CollectorRegistry()
        registry.register(MockDataCollector())
        registry.register(BinanceCollector())
        types = registry.list_types()
        assert CollectorType.BINANCE in types
        assert CollectorType.MOCK in types

    def test_unregister(self) -> None:
        registry = CollectorRegistry()
        registry.register(MockDataCollector())
        registry.unregister(CollectorType.MOCK)
        assert registry.get(CollectorType.MOCK) is None

    def test_get_nonexistent_returns_none(self) -> None:
        registry = CollectorRegistry()
        assert registry.get(CollectorType.BINANCE) is None


class TestMockDataCollector:
    def test_collector_type(self) -> None:
        assert MockDataCollector().collector_type == CollectorType.MOCK

    def test_fetch_historical_returns_data(self) -> None:
        data = MockDataCollector().fetch_historical("BTCUSDT", "1h", limit=10)
        assert len(data) == 10

    def test_fetch_historical_valid_ohlcv(self) -> None:
        data = MockDataCollector().fetch_historical("BTCUSDT", "1h", limit=1)
        assert len(data) == 1
        item = data[0]
        assert item.open > 0
        assert item.high > item.low
        assert item.volume >= 0

    def test_validate_symbol_valid(self) -> None:
        assert MockDataCollector().validate_symbol("BTCUSDT") is True

    def test_validate_symbol_invalid(self) -> None:
        assert MockDataCollector().validate_symbol("") is False
        assert MockDataCollector().validate_symbol("ab") is False


class TestBinanceCollector:
    def test_collector_type(self) -> None:
        assert BinanceCollector().collector_type == CollectorType.BINANCE

    def test_validate_symbol(self) -> None:
        assert BinanceCollector().validate_symbol("BTCUSDT") is True
        assert BinanceCollector().validate_symbol("") is False

    def test_fetch_historical_returns_parsed_data(self) -> None:
        import json
        from unittest.mock import patch

        mock_json = json.dumps([
            [
                1704067200000,
                "44000.0",
                "44500.0",
                "43900.0",
                "44200.0",
                "1234.5",
                1704067260000,
                "54321000.0",
                100,
                "2200.0",
                "98765000.0",
                "0",
            ]
        ])
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = mock_json.encode()
            collector = BinanceCollector()
            result = collector.fetch_historical("BTCUSDT", "1h", limit=1)
            assert len(result) == 1
            assert float(result[0].open) == 44000.0
            assert float(result[0].high) == 44500.0
            assert float(result[0].low) == 43900.0
            assert float(result[0].close) == 44200.0
            assert float(result[0].volume) == 1234.5
            assert result[0].symbol == "BTCUSDT"


class TestYFinanceCollector:
    def test_collector_type(self) -> None:
        assert YFinanceCollector().collector_type == CollectorType.YFINANCE

    def test_validate_symbol(self) -> None:
        assert YFinanceCollector().validate_symbol("AAPL") is True
        assert YFinanceCollector().validate_symbol("") is False


class TestDataNormalizer:
    def test_to_candle(self) -> None:
        import uuid

        ohlcv = CollectorOHLCV(
            open=Decimal(100),
            high=Decimal(105),
            low=Decimal(99),
            close=Decimal(102),
            volume=Decimal(1000),
            timestamp=datetime.now(tz=UTC),
            symbol="BTCUSDT",
        )
        mid = uuid.uuid4()
        candle = DataNormalizer.to_candle(ohlcv, str(mid), interval="1h", source="test")
        assert candle.market_id == mid
        assert candle.ohlcv.open == Decimal(100)
        assert candle.ohlcv.close == Decimal(102)
        assert candle.timeframe.value == "1h"

    def test_normalize_sorts_by_timestamp(self) -> None:
        now = datetime.now(tz=UTC)
        later = now + timedelta(hours=1)
        o1 = CollectorOHLCV(
            open=Decimal(1),
            high=Decimal(2),
            low=Decimal(1),
            close=Decimal(2),
            volume=Decimal(100),
            timestamp=later,
            symbol="T",
        )
        o2 = CollectorOHLCV(
            open=Decimal(1),
            high=Decimal(2),
            low=Decimal(1),
            close=Decimal(2),
            volume=Decimal(100),
            timestamp=now,
            symbol="T",
        )
        result = DataNormalizer.normalize([o1, o2])
        assert result[0].timestamp == now
        assert result[1].timestamp == later


class TestDataValidator:
    def test_empty_data_returns_invalid(self) -> None:
        result = DataValidator.validate([])
        assert result.is_valid is False
        assert "Empty" in result.errors[0]

    def test_valid_data_passes(self) -> None:
        data = [
            CollectorOHLCV(
                open=Decimal(100),
                high=Decimal(105),
                low=Decimal(99),
                close=Decimal(102),
                volume=Decimal(1000),
                timestamp=datetime.now(tz=UTC),
                symbol="T",
            ),
        ]
        result = DataValidator.validate(data)
        assert result.is_valid is True

    def test_high_low_inversion_detected(self) -> None:
        data = [
            CollectorOHLCV(
                open=Decimal(100),
                high=Decimal(99),
                low=Decimal(101),
                close=Decimal(100),
                volume=Decimal(1000),
                timestamp=datetime.now(tz=UTC),
                symbol="T",
            ),
        ]
        result = DataValidator.validate(data)
        assert result.is_valid is False

    def test_negative_price_detected(self) -> None:
        data = [
            CollectorOHLCV(
                open=Decimal(-1),
                high=Decimal(5),
                low=Decimal(-2),
                close=Decimal(3),
                volume=Decimal(100),
                timestamp=datetime.now(tz=UTC),
                symbol="T",
            ),
        ]
        result = DataValidator.validate(data)
        assert result.is_valid is False

    def test_negative_volume_detected(self) -> None:
        data = [
            CollectorOHLCV(
                open=Decimal(100),
                high=Decimal(105),
                low=Decimal(99),
                close=Decimal(102),
                volume=Decimal(-1),
                timestamp=datetime.now(tz=UTC),
                symbol="T",
            ),
        ]
        result = DataValidator.validate(data)
        assert result.is_valid is False

    def test_price_gap_triggers_warning(self) -> None:
        now = datetime.now(tz=UTC)
        later = now + timedelta(hours=1)
        data = [
            CollectorOHLCV(
                open=Decimal(100),
                high=Decimal(105),
                low=Decimal(99),
                close=Decimal(100),
                volume=Decimal(1000),
                timestamp=now,
                symbol="T",
            ),
            CollectorOHLCV(
                open=Decimal(200),
                high=Decimal(205),
                low=Decimal(199),
                close=Decimal(200),
                volume=Decimal(1000),
                timestamp=later,
                symbol="T",
            ),
        ]
        result = DataValidator.validate(data)
        assert result.is_valid is True
        assert len(result.warnings) > 0
