from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.analysis_service import AnalysisService


def _candle(
    close: float, high: float | None = None, low: float | None = None, idx: int = 0
) -> Candle:
    h = close + 5 if high is None else high
    l = close - 5 if low is None else low
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(close)),
            high=Decimal(str(h)),
            low=Decimal(str(l)),
            close=Decimal(str(close)),
            volume=Decimal("1000"),
        ),
        timestamp=datetime(2024, 1, 1 + idx, tzinfo=None),
        timeframe=Timeframe.DAY_1,
    )


def _candle_ohlcv(open_v: float, high: float, low: float, close: float, idx: int = 0) -> Candle:
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(open_v)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal("1000"),
        ),
        timestamp=datetime(2024, 1, 1 + idx, tzinfo=None),
        timeframe=Timeframe.DAY_1,
    )


class TestSMA:
    def test_empty(self) -> None:
        assert AnalysisService.compute_sma([], 3) == []

    def test_sma_window_3(self) -> None:
        candles = [_candle(10, idx=0), _candle(20, idx=1), _candle(30, idx=2), _candle(40, idx=3)]
        result = AnalysisService.compute_sma(candles, 3)
        assert len(result) == 2
        assert result[0].value == 20.0  # (10+20+30)/3
        assert result[1].value == 30.0  # (20+30+40)/3
        assert result[0].name == "sma_3"
        assert result[1].name == "sma_3"

    def test_sma_window_1(self) -> None:
        candles = [_candle(10, idx=0), _candle(20, idx=1)]
        result = AnalysisService.compute_sma(candles, 1)
        assert len(result) == 2
        assert result[0].value == 10.0
        assert result[1].value == 20.0


class TestEMA:
    def test_empty(self) -> None:
        assert AnalysisService.compute_ema([], 3) == []

    def test_ema_window_3(self) -> None:
        candles = [_candle(10, idx=0), _candle(20, idx=1), _candle(30, idx=2), _candle(40, idx=3)]
        result = AnalysisService.compute_ema(candles, 3)
        assert len(result) == 2
        # first EMA = SMA(10,20,30) = 20
        assert result[0].value == 20.0
        # multiplier = 2/(3+1) = 0.5
        # second = (40-20)*0.5 + 20 = 30
        assert result[1].value == 30.0

    def test_ema_rising(self) -> None:
        candles = [_candle(10, idx=0), _candle(20, idx=1), _candle(30, idx=2), _candle(50, idx=3)]
        result = AnalysisService.compute_ema(candles, 3)
        assert len(result) == 2
        assert result[0].value == 20.0
        assert result[1].value == 35.0  # (50-20)*0.5 + 20 = 35


class TestRSI:
    def test_empty(self) -> None:
        assert AnalysisService.compute_rsi([], 14) == []

    def test_rsi_window_3(self) -> None:
        # prices: 44.34, 44.09, 43.61, 44.33
        # changes: -0.25, -0.48, +0.72
        candles = [
            _candle(44.34, idx=0),
            _candle(44.09, idx=1),
            _candle(43.61, idx=2),
            _candle(44.33, idx=3),
        ]
        result = AnalysisService.compute_rsi(candles, 3)
        assert len(result) == 1
        avg_gain = 0.72 / 3
        avg_loss = (0.25 + 0.48) / 3
        rs = avg_gain / avg_loss
        expected_rsi = 100.0 - 100.0 / (1.0 + rs)
        assert abs(result[0].value - expected_rsi) < 0.01

    def test_rsi_all_up(self) -> None:
        candles = [_candle(10, idx=0), _candle(11, idx=1), _candle(12, idx=2), _candle(13, idx=3)]
        result = AnalysisService.compute_rsi(candles, 3)
        assert len(result) == 1
        assert result[0].value == 100.0  # no losses, RSI = 100

    def test_rsi_all_down(self) -> None:
        candles = [_candle(13, idx=0), _candle(12, idx=1), _candle(11, idx=2), _candle(10, idx=3)]
        result = AnalysisService.compute_rsi(candles, 3)
        assert len(result) == 1
        assert result[0].value == 0.0  # no gains, RSI = 0


class TestATR:
    def test_empty(self) -> None:
        assert AnalysisService.compute_atr([], 14) == []

    def test_atr_window_3(self) -> None:
        candles = [
            _candle_ohlcv(50, 52, 48, 51, idx=0),
            _candle_ohlcv(52, 55, 50, 53, idx=1),
            _candle_ohlcv(54, 56, 49, 50, idx=2),
            _candle_ohlcv(52, 56, 51, 55, idx=3),
        ]
        result = AnalysisService.compute_atr(candles, 3)
        assert len(result) == 1
        # j=1: TR = max(55-50=5, |55-51|=4, |50-51|=1) = 5
        # j=2: TR = max(56-49=7, |56-53|=3, |49-53|=4) = 7
        # j=3: TR = max(56-51=5, |56-50|=6, |51-50|=1) = 6
        # ATR = (5+7+6)/3 = 6.0
        assert abs(result[0].value - 6.0) < 0.01


class TestBollingerBands:
    def test_empty(self) -> None:
        bb = AnalysisService.compute_bollinger_bands([], 3)
        assert bb.middle == []
        assert bb.upper == []
        assert bb.lower == []

    def test_bb_window_3(self) -> None:
        candles = [_candle(10, idx=0), _candle(12, idx=1), _candle(14, idx=2), _candle(16, idx=3)]
        bb = AnalysisService.compute_bollinger_bands(candles, 3)
        assert len(bb.middle) == 2
        assert len(bb.upper) == 2
        assert len(bb.lower) == 2
        # i=2: mean=(10+12+14)/3=12, var=((4+0+4)/3)=8/3, std=√(8/3)≈1.633
        import math

        std = math.sqrt(8 / 3)
        assert bb.middle[0].value == 12.0
        assert abs(bb.upper[0].value - (12 + 2 * std)) < 0.01
        assert abs(bb.lower[0].value - (12 - 2 * std)) < 0.01
        # i=3: mean=(12+14+16)/3=14
        assert bb.middle[1].value == 14.0

    def test_bb_name_format(self) -> None:
        candles = [_candle(10, idx=0), _candle(12, idx=1), _candle(14, idx=2)]
        bb = AnalysisService.compute_bollinger_bands(candles, 3)
        assert bb.middle[0].name == "bb_middle_3"
        assert bb.upper[0].name == "bb_upper_3"
        assert bb.lower[0].name == "bb_lower_3"


class TestStochastics:
    def test_empty(self) -> None:
        stoch = AnalysisService.compute_stochastics([], 3, 2)
        assert stoch.k == []
        assert stoch.d == []

    def test_stoch_k_window_3_d_2(self) -> None:
        candles = [
            _candle_ohlcv(45, 50, 40, 45, idx=0),
            _candle_ohlcv(50, 55, 42, 52, idx=1),
            _candle_ohlcv(46, 53, 44, 48, idx=2),
            _candle_ohlcv(52, 56, 46, 54, idx=3),
        ]
        stoch = AnalysisService.compute_stochastics(candles, 3, 2)
        assert len(stoch.k) == 2
        assert len(stoch.d) == 1
        # i=2: high=max(50,55,53)=55, low=min(40,42,44)=40
        # %K = (48-40)/(55-40)*100 = 8/15*100 ≈ 53.33
        expected_k0 = (48 - 40) / (55 - 40) * 100
        assert abs(stoch.k[0].value - expected_k0) < 0.01
        # i=3: high=max(55,53,56)=56, low=min(42,44,46)=42
        # %K = (54-42)/(56-42)*100 = 12/14*100 ≈ 85.714
        expected_k1 = (54 - 42) / (56 - 42) * 100
        assert abs(stoch.k[1].value - expected_k1) < 0.01
        # %D at i=3: SMA(%K, 2) = (53.33+85.714)/2 ≈ 69.524
        expected_d0 = (expected_k0 + expected_k1) / 2
        assert abs(stoch.d[0].value - expected_d0) < 0.01

    def test_stoch_flat(self) -> None:
        candles = [_candle(50, idx=0), _candle(50, idx=1), _candle(50, idx=2)]
        stoch = AnalysisService.compute_stochastics(candles, 2, 2)
        assert len(stoch.k) == 2  # i=1, i=2 both valid
        assert stoch.k[0].value == 50.0
        assert stoch.k[1].value == 50.0


class TestIndicatorMetadata:
    def test_indicator_has_market_id_and_timestamp(self) -> None:
        candles = [_candle(10, idx=0), _candle(20, idx=1), _candle(30, idx=2)]
        result = AnalysisService.compute_sma(candles, 3)
        assert len(result) == 1
        assert result[0].market_id == candles[0].market_id
        assert result[0].timestamp == candles[2].timestamp
        assert result[0].value == 20.0
