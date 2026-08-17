"""Sprint 38 (Slice A): the Market Brain — a per-market, tick-fed chart watcher.

The Brain is the "chart watcher" that tells the Custom Expert Advisor what is
happening in the market and the possible moves to make. It is a pure domain
service: fed real history (``seed_candles``) and live ticks (``update_tick``),
it produces a ``StateSnapshot`` (regime, trend stage, volatility, momentum)
and ranked ``Advice`` whose risk fraction is hard-capped.

Design rules (execution guardrails / Constitution):
- Fail closed: with insufficient data the state is UNKNOWN and ``advise``
  yields NO moves — the Brain never fabricates an edge it cannot see.
- Never over-leveraged: every advised ``risk_fraction`` is clamped to a
  configured maximum; volatility actively *reduces* the size, never raises it.
- Deterministic state: indicators (EMA/ATR/RSI/Bollinger) are computed
  index-based on the ordered candle series, so a given history yields a given
  read. Regime is derived from trend stage + volatility rather than
  ``RegimeDetectionService``, whose timestamp-keyed algorithm silently
  collapses synthetic series that share an identical timestamp.
- No silent advice: an unready or range-bound market returns an explicit
  ``reason``, which the async daemon audits and surfaces.
- Durable replay (Slice C): when a ``CandleStorePort`` is wired, seeded history
  and tick aggregates are persisted, and ``warm_from_store`` rebuilds a fresh
  Brain to the same state across a restart (the daemon warms before its first
  read). Persistence is per-bar-timestamp (the store's natural identity), so
  distinct bars sharing a timestamp collapse deterministically LAST-WINS on
  replay while the in-memory index-based read keeps every bar.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple
from typing import Protocol

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.regime_detection import Regime


class _PriceTick(Protocol):
    """Structural tick the Brain consumes (the async daemon hands it the
    infrastructure ``Tick``). Kept in the domain as a protocol so the domain
    never imports infrastructure (dependency-direction guard)."""

    @property
    def price(self) -> Decimal: ...

    @property
    def quantity(self) -> Decimal: ...

    @property
    def exchange_timestamp(self) -> datetime: ...


class CandleStorePort(Protocol):
    """Durable candle persistence the Brain can replay across a restart.

    Kept as a protocol in the domain so the domain never imports
    infrastructure (dependency-direction guard). The adapter keys bars by
    (timeframe, ts); ``load_candles`` returns the market's bars across
    timeframes in timestamp order so the index-based indicators replay exactly.
    """

    def save_candles(self, market_id: uuid.UUID, candles: Iterable[Candle]) -> None: ...

    def load_candles(self, market_id: uuid.UUID, limit: int) -> list[Candle]: ...


class TrendStage(StrEnum):
    ACCUMULATION = "accumulation"
    MARKUP = "markup"
    DISTRIBUTION = "distribution"
    MARKDOWN = "markdown"
    UNKNOWN = "unknown"


class Move(NamedTuple):
    direction: str
    confidence: float
    risk_fraction: float
    rationale: str


class StateSnapshot(NamedTuple):
    market_id: uuid.UUID
    timestamp: datetime
    known: bool
    regime: str
    trend_stage: str
    volatility_percentile: float
    momentum: float
    rsi: float
    liquidity: int
    indicators: dict[str, float]


class Advice(NamedTuple):
    allowed: bool
    moves: list[Move]
    reason: str
    snapshot: StateSnapshot | None


@dataclass
class _BrainMarketView:
    candles: list[Candle] = field(default_factory=list)
    ticks: deque[_PriceTick] = field(default_factory=lambda: deque(maxlen=512))


@dataclass
class MarketBrainService:
    """Per-market market-state watcher + advisor.

    ``min_candles`` (default 60) is the data-sufficiency floor — below it the
    Brain is UNKNOWN and refuses to advise. ``action_threshold`` is the
    confidence floor for a directional move. ``max_risk_fraction`` is the
    hard cap on any advised position as a fraction of account equity.
    """

    min_candles: int = 60
    action_threshold: float = 0.55
    max_risk_fraction: float = 0.01
    momentum_window: int = 10
    candle_seconds: int = 60
    confidence_momentum_scale: float = 4.0
    high_volatility_percentile: float = 0.8
    store: CandleStorePort | None = None

    _views: dict[uuid.UUID, _BrainMarketView] = field(
        default_factory=lambda: defaultdict(_BrainMarketView)
    )

    def seed_candles(self, market_id: uuid.UUID, candles: Iterable[Candle]) -> None:
        """Seed historical candles (e.g. from the data-ingestion source or a
        replay tape). Idempotent by bar identity (timestamp + full OHLCV):
        re-seeding the exact same bars replaces them; distinct bars that share
        a timestamp (e.g. a synthetic tape) are all kept and read index-based.
        When a durable store is wired, the merged series is persisted
        idempotently (the store upserts by (timeframe, ts))."""
        self._merge_candles(market_id, candles)
        if self.store is not None:
            view = self._views[market_id]
            self.store.save_candles(market_id, view.candles)

    def warm_from_store(self, market_id: uuid.UUID, limit: int = 300) -> bool:
        """Replay the durable bars for a market into memory (restart-safe).

        Returns True when durable history existed and was loaded into the
        in-memory view; False when there is no store or no durable history, in
        which case the Brain stays UNKNOWN (fail closed — nothing is assumed).
        """
        if self.store is None:
            return False
        candles = self.store.load_candles(market_id, limit)
        if not candles:
            return False
        self._merge_candles(market_id, candles)
        return True

    def _merge_candles(self, market_id: uuid.UUID, candles: Iterable[Candle]) -> None:
        """Merge bars into the market's in-memory view, idempotent by identity."""
        view = self._views[market_id]
        key = lambda c: (  # noqa: E731
            c.timestamp,
            float(c.ohlcv.open),
            float(c.ohlcv.high),
            float(c.ohlcv.low),
            float(c.ohlcv.close),
            float(c.ohlcv.volume),
            c.timeframe.value,
        )
        merged = {key(c): c for c in view.candles}
        for candle in candles:
            merged[key(candle)] = candle
        view.candles = sorted(merged.values(), key=lambda c: c.timestamp)

    def update_tick(self, market_id: uuid.UUID, tick: _PriceTick) -> None:
        """Ingest one live tick: record it for liquidity/momentum and aggregate
        into the candle for its time interval when it is a newer bar. New
        aggregate candles are persisted when a durable store is wired."""
        view = self._views[market_id]
        view.ticks.append(tick)
        interval_start = self._interval_start(tick.exchange_timestamp, self.candle_seconds)
        if view.candles and interval_start > view.candles[-1].timestamp:
            candle = self._candle_from_ticks(market_id, interval_start)
            view.candles.append(candle)
            if self.store is not None:
                self.store.save_candles(market_id, [candle])

    @staticmethod
    def _interval_start(ts: datetime, seconds: int | None = None) -> datetime:
        sec = seconds or 60
        boundary = (ts.timestamp() // sec) * sec
        return datetime.fromtimestamp(boundary, tz=ts.tzinfo)

    def _candle_from_ticks(self, market_id: uuid.UUID, interval_start: datetime) -> Candle:
        in_interval = [
            t
            for t in self._views[market_id].ticks
            if self._interval_start(t.exchange_timestamp, self.candle_seconds) == interval_start
        ]
        price = round(float(sum(float(t.price) for t in in_interval)) / len(in_interval), 6)
        high = max(float(t.price) for t in in_interval)
        low = min(float(t.price) for t in in_interval)
        volume = sum(float(t.quantity) for t in in_interval)
        return Candle(
            market_id=market_id,
            ohlcv=OHLCV(
                open=Decimal(str(price)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(price)),
                volume=Decimal(str(volume)),
            ),
            timestamp=interval_start,
            timeframe=Timeframe.MINUTE_1,
        )

    def snapshot(self, market_id: uuid.UUID) -> StateSnapshot:
        """Compute the current market state. Returns a fail-closed UNKNOWN
        snapshot when the market is unwatched or data is insufficient."""
        unknown = lambda: StateSnapshot(  # noqa: E731
            market_id=market_id,
            timestamp=datetime.now(tz=UTC),
            known=False,
            regime="unknown",
            trend_stage=TrendStage.UNKNOWN.value,
            volatility_percentile=0.0,
            momentum=0.0,
            rsi=0.0,
            liquidity=0,
            indicators={},
        )
        view = self._views.get(market_id)
        if view is None or not view.candles:
            return unknown()
        candles = sorted(view.candles, key=lambda c: c.timestamp)
        if len(candles) < self.min_candles:
            return unknown()

        indicators: dict[str, float] = {}
        close = float(candles[-1].ohlcv.close)
        indicators["close"] = close

        ema_20 = self._last_value(AnalysisService.compute_ema(candles, 20))
        ema_50 = self._last_value(AnalysisService.compute_ema(candles, 50))
        atr_14 = self._last_value(AnalysisService.compute_atr(candles, 14))
        rsi_14 = self._last_value(AnalysisService.compute_rsi(candles, 14))
        if ema_20 is not None:
            indicators["ema_20"] = ema_20
        if ema_50 is not None:
            indicators["ema_50"] = ema_50
        if atr_14 is not None:
            indicators["atr_14"] = atr_14
        if rsi_14 is not None:
            indicators["rsi_14"] = rsi_14

        bands = AnalysisService.compute_bollinger_bands(candles, 20)
        if bands and bands.upper and bands.lower:
            indicators["bb_upper_20"] = float(bands.upper[-1].value)
            indicators["bb_lower_20"] = float(bands.lower[-1].value)

        closes = [float(c.ohlcv.close) for c in candles]
        n = min(self.momentum_window, len(closes) - 1)
        if n <= 0:
            momentum = 0.0
        else:
            prev = closes[-(1 + n)]
            momentum = (closes[-1] - prev) / prev if prev else 0.0
        indicators["momentum"] = momentum

        atr_series = AnalysisService.compute_atr(candles, 14)
        vol_pct = self._percentile_rank([float(a.value) for a in atr_series])
        indicators["volatility_percentile"] = vol_pct

        stage = self._trend_stage(close, ema_20, ema_50)
        regime = self._derive_regime(stage, vol_pct)

        return StateSnapshot(
            market_id=market_id,
            timestamp=candles[-1].timestamp,
            known=True,
            regime=regime.value,
            trend_stage=stage.value,
            volatility_percentile=vol_pct,
            momentum=momentum,
            rsi=rsi_14 or 0.0,
            liquidity=len(view.ticks),
            indicators=indicators,
        )

    @staticmethod
    def _last_value(items) -> float | None:
        if not items:
            return None
        return float(items[-1].value)

    @staticmethod
    def _percentile_rank(series: list[float]) -> float:
        if len(series) < 2:
            return 0.0
        if max(series) == min(series):
            return 0.0  # flat tape — no volatility relative to its own history
        current = series[-1]
        prior = series[:-1]
        count = sum(1 for v in prior if v <= current)
        return count / len(prior)

    @staticmethod
    def _trend_stage(close: float, ema_20: float | None, ema_50: float | None) -> TrendStage:
        if ema_20 is None or ema_50 is None:
            return TrendStage.UNKNOWN
        if close > ema_20 > ema_50:
            return TrendStage.MARKUP
        if close < ema_20 < ema_50:
            return TrendStage.MARKDOWN
        if close > ema_20:
            return TrendStage.ACCUMULATION
        if close < ema_20:
            return TrendStage.DISTRIBUTION
        return TrendStage.UNKNOWN

    @staticmethod
    def _derive_regime(stage: TrendStage, vol_pct: float) -> Regime:
        if stage in (TrendStage.MARKUP, TrendStage.ACCUMULATION):
            return Regime.TRENDING_BULLISH
        if stage in (TrendStage.MARKDOWN, TrendStage.DISTRIBUTION):
            return Regime.TRENDING_BEARISH
        if vol_pct >= 0.8:
            return Regime.HIGH_VOLATILITY
        return Regime.RANGING

    def advise(self, market_id: uuid.UUID) -> Advice:
        """Rank the possible moves. ``allowed=True`` only when there is a
        directional move at or above the action threshold; otherwise the Brain
        says exactly why it is standing flat."""
        snap = self.snapshot(market_id)
        if not snap.known:
            return Advice(
                allowed=False,
                moves=[],
                reason="brain warming up: insufficient data",
                snapshot=snap,
            )

        direction = self._candidate_direction(snap.regime, snap.trend_stage)
        if direction is None:
            return Advice(
                allowed=False,
                moves=[],
                reason=f"range-bound ({snap.regime} / {snap.trend_stage}): no directional edge",
                snapshot=snap,
            )

        confidence = self._confidence(direction, snap.momentum, snap.rsi)
        if confidence < self.action_threshold:
            return Advice(
                allowed=False,
                moves=[],
                reason=(
                    f"trend present ({snap.regime} / {snap.trend_stage}) but confidence "
                    f"{confidence:.2f} below action threshold"
                ),
                snapshot=snap,
            )

        risk_fraction = min(
            self.max_risk_fraction,
            self.max_risk_fraction * confidence * (1.0 - 0.3 * snap.volatility_percentile),
        )
        rationale = (
            f"regime={snap.regime} stage={snap.trend_stage} "
            f"momentum={snap.momentum:.4f} rsi={snap.rsi:.1f} "
            f"vol_pct={snap.volatility_percentile:.2f}"
        )
        move = Move(
            direction=direction,
            confidence=round(confidence, 4),
            risk_fraction=round(risk_fraction, 6),
            rationale=rationale,
        )
        return Advice(allowed=True, moves=[move], reason=move.rationale, snapshot=snap)

    @staticmethod
    def _candidate_direction(regime: str, stage: str) -> str | None:
        if regime in (Regime.TRENDING_BULLISH.value, Regime.HIGH_VOLATILITY.value) and stage in (
            TrendStage.MARKUP.value,
            TrendStage.ACCUMULATION.value,
        ):
            return "long"
        if regime in (Regime.TRENDING_BEARISH.value, Regime.HIGH_VOLATILITY.value) and stage in (
            TrendStage.MARKDOWN.value,
            TrendStage.DISTRIBUTION.value,
        ):
            return "short"
        return None

    @staticmethod
    def _confidence(direction: str, momentum: float, rsi: float) -> float:
        strength = 0.5 + abs(momentum) * 4.0
        if direction == "long":
            if rsi >= 75:
                strength -= 0.15  # overbought — late entry, trim conviction
            elif 40 <= rsi <= 70:
                strength += 0.05
        else:
            if rsi <= 25:
                strength -= 0.15  # oversold — late short, trim conviction
            elif 30 <= rsi <= 60:
                strength += 0.05
        return min(max(strength, 0.05), 0.98)
