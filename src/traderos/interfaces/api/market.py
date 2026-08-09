# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""Market Overview + Research Lab (WP9).

Serves the operator dashboard's market and research panes from the REAL runtime
services already wired into the orchestrator:

* Market Overview — a per-symbol table (last close, daily change, RSI, ATR,
  moving-average cross state) computed from ``DataIngestionService`` candles
  and the shared ``AnalysisService`` indicators (no new market source).
* Research Lab — the C2-observable research journal (observations /
  hypotheses / results via ``ResearchService``) and the indicator/backtest
  toolkit that runs a registered strategy against that symbol's candles.

Every endpoint fails closed: unknown symbol -> 404, missing service -> 503,
never a silent empty-dataset claim.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field

from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.interfaces.api.security import require_operate
from traderos.interfaces.api.security import require_read

OrchestratorProvider = Callable[[], TradingOrchestrator]


class ObservationCreate(BaseModel):
    symbol: str
    content: str
    tags: list[str] = Field(default_factory=list)


def _candles_for(orch: TradingOrchestrator, symbol: str, limit: int = 90) -> list[Candle]:
    """Pull the symbol's candles through the live ingest service."""
    if orch.data_ingestion is None:
        raise HTTPException(503, "Data ingestion not configured")
    raw = orch.data_ingestion.fetch_all(limit=limit)
    rows = raw.get(symbol)
    if not rows:
        raise HTTPException(404, f"Unknown or empty market symbol '{symbol}'")
    mid = next(
        (s.market_id for s in orch.data_ingestion.sources if s.symbol == symbol),
        None,
    )
    if mid is None:
        raise HTTPException(404, f"No registered data source for market '{symbol}'")
    candles: list[Candle] = []
    for r in rows:
        ts = r.get("timestamp")
        ts_val: Any = ts
        if isinstance(ts_val, str):
            try:
                ts_val = datetime.fromisoformat(ts_val)
            except ValueError:
                ts_val = None
        candles.append(
            Candle(
                market_id=mid,
                ohlcv=OHLCV(
                    open=Decimal(str(r["open"])),
                    high=Decimal(str(r["high"])),
                    low=Decimal(str(r["low"])),
                    close=Decimal(str(r["close"])),
                    volume=Decimal(str(r.get("volume", 0))),
                ),
                timestamp=ts_val,
                timeframe=Timeframe.HOUR_1,
                source=symbol,
            )
        )
    return candles


def register_market_research_endpoints(
    router: APIRouter, orch_provider: OrchestratorProvider
) -> None:
    analysis = AnalysisService()

    @router.get("/market/overview", dependencies=[Depends(require_read)])
    def market_overview():
        """Per-symbol market snapshot: last close, change, volume, trend state."""
        orch = orch_provider()
        if orch.data_ingestion is None:
            raise HTTPException(503, "Data ingestion not configured")
        rows = orch.data_ingestion.fetch_all(limit=90)
        out: list[dict[str, Any]] = []
        for symbol, candles in rows.items():
            if not candles:
                continue
            series = _candles_for(orch, symbol, limit=90)
            closes = [float(c.ohlcv.close) for c in series]
            last_close = closes[-1] if closes else 0.0
            prev_close = closes[-2] if len(closes) > 1 else last_close
            change = (last_close - prev_close) / prev_close if prev_close else 0.0
            sma20 = closes[-1] if len(closes) < 20 else sum(closes[-20:]) / 20
            sma50 = closes[-1] if len(closes) < 50 else sum(closes[-50:]) / 50
            rsi_series = analysis.compute_rsi(series, window=14)
            rsi = rsi_series[-1].value if rsi_series else None
            atr_series = analysis.compute_atr(series, window=14)
            atr = atr_series[-1].value if atr_series else None
            state = (
                "uptrend"
                if last_close > sma50 > sma20
                else "downtrend" if last_close < sma50 < sma20 else "range"
            )
            out.append(
                {
                    "symbol": symbol,
                    "last": last_close,
                    "change_pct": round(change * 100, 2),
                    "volume": float(candles[-1].get("volume", 0)),
                    "sma20": round(sma20, 4),
                    "sma50": round(sma50, 4),
                    "rsi": round(rsi, 2) if rsi is not None else None,
                    "atr": round(atr, 4) if atr is not None else None,
                    "state": state,
                }
            )
        out.sort(key=lambda r: r["symbol"])
        return {"markets": out}

    @router.get("/market/candles", dependencies=[Depends(require_read)])
    def market_candles(symbol: str, limit: int = 90):
        orch = orch_provider()
        series = _candles_for(orch, symbol, limit=limit)
        return {
            "symbol": symbol,
            "candles": [
                {
                    "timestamp": c.timestamp.isoformat() if c.timestamp else None,
                    "open": float(c.ohlcv.open),
                    "high": float(c.ohlcv.high),
                    "low": float(c.ohlcv.low),
                    "close": float(c.ohlcv.close),
                    "volume": float(c.ohlcv.volume),
                }
                for c in series
            ],
        }

    @router.get("/market/symbols", dependencies=[Depends(require_read)])
    def market_symbols():
        orch = orch_provider()
        if orch.data_ingestion is None:
            raise HTTPException(503, "Data ingestion not configured")
        return {"symbols": [s.symbol for s in orch.data_ingestion.sources]}

    @router.get("/research/indicators", dependencies=[Depends(require_read)])
    def research_indicators(symbol: str, limit: int = 90):
        orch = orch_provider()
        series = _candles_for(orch, symbol, limit=limit)
        sma20 = analysis.compute_sma(series, 20)
        sma50 = analysis.compute_sma(series, 50)
        ema12 = analysis.compute_ema(series, 12)
        rsi = analysis.compute_rsi(series, 14)
        atr = analysis.compute_atr(series, 14)
        bb = analysis.compute_bollinger_bands(series, 20)
        stoch = analysis.compute_stochastics(series, 14)

        def _series(values: list[Any]) -> list[tuple[str | None, float]]:
            return [
                (v.timestamp.isoformat() if v.timestamp else None, round(float(v.value), 4))
                for v in values
            ]

        def _bb(values: list[Any]) -> list[tuple[str | None, float]]:
            return [
                (v.timestamp.isoformat() if v.timestamp else None, round(float(v.value), 4))
                for v in values
            ]

        return {
            "symbol": symbol,
            "indicators": {
                "sma20": _series(sma20),
                "sma50": _series(sma50),
                "ema12": _series(ema12),
                "rsi14": _series(rsi),
                "atr14": _series(atr),
                "bollinger": {
                    "upper": _bb(bb.upper),
                    "middle": _bb(bb.middle),
                    "lower": _bb(bb.lower),
                },
                "stochastics": {
                    "k": _series(stoch.k),
                    "d": _series(stoch.d),
                },
            },
        }

    @router.post("/research/backtest", dependencies=[Depends(require_read)])
    def research_backtest(body: dict):
        strategy = body.get("strategy") or ""
        symbol = body.get("symbol") or ""
        if strategy not in strategy_registry.list():
            raise HTTPException(404, f"Strategy '{strategy}' not found")
        orch = orch_provider()
        series = _candles_for(orch, symbol, limit=120)
        cls = strategy_registry.get(strategy)
        from traderos.domain.services.backtesting_service import BacktestingService
        from traderos.domain.services.execution_service import ExecutionService

        svc = BacktestingService(execution=ExecutionService())
        try:
            result, _ = svc.run(cls(), series, series[0].market_id)
        except Exception as exc:
            raise HTTPException(400, f"Backtest failed: {exc}") from exc
        m = result.metrics
        return {
            "strategy": strategy,
            "symbol": symbol,
            "total_return": m.total_return,
            "sharpe_ratio": m.sharpe_ratio,
            "sortino_ratio": m.sortino_ratio,
            "calmar_ratio": m.calmar_ratio,
            "max_drawdown": m.max_drawdown,
            "win_rate": m.win_rate,
            "profit_factor": m.profit_factor,
            "expectancy": m.expectancy,
            "candles": len(series),
        }

    @router.get("/research/observations", dependencies=[Depends(require_read)])
    def research_observations(limit: int = 50):
        orch = orch_provider()
        if orch.research is None:
            raise HTTPException(503, "Research service not configured")
        obs = orch.research.observations.list()
        obs = sorted(obs, key=lambda o: o.timestamp, reverse=True)[:limit]
        return {
            "observations": [
                {
                    "id": str(o.id),
                    "symbol": o.symbol,
                    "content": o.content,
                    "tags": o.tags,
                    "timestamp": o.timestamp.isoformat(),
                }
                for o in obs
            ]
        }

    @router.post("/research/observations", dependencies=[Depends(require_operate)])
    def create_observation(body: ObservationCreate):
        orch = orch_provider()
        if orch.research is None:
            raise HTTPException(503, "Research service not configured")
        try:
            obs = orch.research.create_observation(body.symbol, body.content, body.tags)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "id": str(obs.id),
            "symbol": obs.symbol,
            "content": obs.content,
            "tags": obs.tags,
            "timestamp": obs.timestamp.isoformat(),
        }
