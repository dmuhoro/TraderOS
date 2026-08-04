"""Causal replay (G-05): reconstruct the per-fill causal chain from durable data.

The live loop (CycleExecutor) records each causal step durably as a hash-chained
audit row: ``signal.generated`` -> ``decision.made`` -> ``order.placed`` ->
``trade.fill``, each with structured JSON detail keyed by ``signal_id`` (and
``trade_id`` once the order fills). This service replays a window and rebuilds
those chains, then recomputes per-fill realized PnL with FIFO matching across
the filled trades stored in the ``trades`` table.

Only the real submission path is replayed: a blocked decision surfaces as a
blocked chain, and a fill that closed a position carries its realized PnL.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeSide
from traderos.domain.ports import AuditEntry
from traderos.domain.ports import AuditPort
from traderos.domain.repositories.trade_repository import TradeRepository

CAUSAL_ACTIONS = {"signal.generated", "decision.made", "order.placed", "trade.fill"}

_CAUSAL_ORDER = {"signal.generated": 0, "decision.made": 1, "order.placed": 2, "trade.fill": 3}


@dataclass(frozen=True)
class CausalStep:
    action: str
    at: datetime
    actor: str
    detail: dict


@dataclass
class ReplayFill:
    signal_id: str
    market_id: str
    trade_id: str = ""
    strategy: str = ""
    direction: str = ""
    confidence: float = 0.0
    decision: str = ""
    decision_reason: str = ""
    order_status: str = ""
    order_id: str = ""
    side: str = ""
    qty: float = 0.0
    price: float = 0.0
    filled_qty: float = 0.0
    filled_price: float = 0.0
    filled_at: str = ""
    realized_pnl: float | None = None

    @property
    def filled(self) -> bool:
        return self.filled_qty > 0 and self.order_status == "filled"


@dataclass
class ReplayChain:
    signal_id: str
    market_id: str
    signal_at: datetime
    strategy: str
    direction: str
    confidence: float
    steps: list[CausalStep]
    fill: ReplayFill | None = None
    blocked: bool = False

    @property
    def complete(self) -> bool:
        return self.fill is not None and self.fill.filled

    @property
    def realized_pnl(self) -> float | None:
        return self.fill.realized_pnl if self.fill is not None else None


@dataclass
class ReplayDayReport:
    chains: list[ReplayChain]
    total_realized_pnl: float
    total_blocked: int
    total_unfilled: int

    @property
    def total_fills(self) -> int:
        return sum(1 for c in self.chains if c.complete)


class _Lot:
    __slots__ = ("price", "qty", "trade_id")

    def __init__(self, qty: float, price: float, trade_id: str) -> None:
        self.qty = qty
        self.price = price
        self.trade_id = trade_id


class ReplayService:
    def __init__(self, audit: AuditPort, trade_repo: TradeRepository) -> None:
        self._audit = audit
        self._trades = trade_repo

    def replay_day(self, start: datetime, end: datetime) -> ReplayDayReport:
        entries = [
            e
            for e in self._audit.get_entries(limit=5000)
            if e.action in CAUSAL_ACTIONS and start <= e.timestamp <= end
        ]
        by_signal: dict[str, list[CausalStep]] = defaultdict(list)
        for e in entries:
            detail = self._parse_detail(e)
            signal_id = detail.get("signal_id")
            if not signal_id:
                continue
            by_signal[signal_id].append(
                CausalStep(action=e.action, at=e.timestamp, actor=e.actor, detail=detail)
            )

        realized = self._fifo_realized_pnl(
            [
                t
                for t in self._trades.list()
                if start <= t.created_at <= end and t.status.value == "filled"
            ]
        )

        chains: list[ReplayChain] = []
        for signal_id, raw_steps in by_signal.items():
            steps = sorted(raw_steps, key=lambda s: (s.at, _CAUSAL_ORDER.get(s.action, 9)))
            chains.append(self._build_chain(signal_id, steps, realized))
        chains.sort(key=lambda c: c.signal_at)

        return ReplayDayReport(
            chains=chains,
            total_realized_pnl=sum(c.realized_pnl or 0.0 for c in chains),
            total_blocked=sum(1 for c in chains if c.blocked),
            total_unfilled=sum(1 for c in chains if not c.complete and not c.blocked),
        )

    def _build_chain(
        self,
        signal_id: str,
        steps: list[CausalStep],
        realized: dict[str, float],
    ) -> ReplayChain:
        gen = next((s for s in steps if s.action == "signal.generated"), None)
        dec = next((s for s in steps if s.action == "decision.made"), None)
        ord_step = next((s for s in steps if s.action == "order.placed"), None)
        fill_step = next((s for s in steps if s.action == "trade.fill"), None)

        blocked = bool(dec and dec.detail.get("outcome") == "blocked")
        anchor = next((s for s in (gen, dec, ord_step, fill_step) if s is not None), None)
        chain = ReplayChain(
            signal_id=signal_id,
            market_id=str(anchor.detail.get("market_id", "")) if anchor else "",
            signal_at=gen.at if gen else steps[0].at,
            strategy=str(gen.detail.get("strategy", "")) if gen else "",
            direction=str(gen.detail.get("direction", "")) if gen else "",
            confidence=float(gen.detail.get("confidence", 0.0)) if gen else 0.0,
            steps=steps,
            blocked=blocked,
        )
        if not fill_step:
            return chain

        d = fill_step.detail
        fill = ReplayFill(
            signal_id=signal_id,
            market_id=str(d.get("market_id", chain.market_id)),
            trade_id=str(d.get("trade_id", "")),
            strategy=chain.strategy,
            direction=chain.direction,
            confidence=chain.confidence,
            decision=str(dec.detail.get("outcome", "")) if dec else "",
            decision_reason=str(dec.detail.get("reason", "")) if dec else "",
            order_status=str(ord_step.detail.get("status", "")) if ord_step else "",
            order_id=str(ord_step.detail.get("order_id", "")) if ord_step else "",
            side=str(d.get("side", "")),
            qty=float(d.get("qty", 0.0)),
            price=float(d.get("price", 0.0)),
            filled_qty=float(d.get("qty", 0.0)),
            filled_price=float(d.get("price", 0.0)),
            filled_at=str(d.get("filled_at", "")),
        )
        trade_id = str(d.get("trade_id", ""))
        if trade_id in realized:
            fill.realized_pnl = realized[trade_id]
        chain.fill = fill
        return chain

    @staticmethod
    def _parse_detail(entry: AuditEntry) -> dict:
        if not entry.detail:
            return {}
        try:
            parsed = json.loads(entry.detail)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _fifo_realized_pnl(trades: list[Trade]) -> dict[str, float]:
        by_market: dict[str, list[Trade]] = defaultdict(list)
        for t in trades:
            by_market[str(t.market_id)].append(t)

        realized: dict[str, float] = {}
        for market_trades in by_market.values():
            long_lots: deque[_Lot] = deque()
            short_lots: deque[_Lot] = deque()
            for t in sorted(market_trades, key=lambda x: x.created_at):
                qty = t.filled_quantity
                price = t.filled_price
                close_trade_id = str(t.id)
                if t.side == TradeSide.BUY:
                    while qty > 1e-9 and short_lots:
                        lot = short_lots[0]
                        take = min(lot.qty, qty)
                        realized[close_trade_id] = realized.get(close_trade_id, 0.0) + take * (
                            lot.price - price
                        )
                        lot.qty -= take
                        qty -= take
                        if lot.qty <= 1e-9:
                            short_lots.popleft()
                    if qty > 1e-9:
                        long_lots.append(_Lot(qty, price, close_trade_id))
                else:
                    while qty > 1e-9 and long_lots:
                        lot = long_lots[0]
                        take = min(lot.qty, qty)
                        realized[close_trade_id] = realized.get(close_trade_id, 0.0) + take * (
                            price - lot.price
                        )
                        lot.qty -= take
                        qty -= take
                        if lot.qty <= 1e-9:
                            long_lots.popleft()
                    if qty > 1e-9:
                        short_lots.append(_Lot(qty, price, close_trade_id))
        return realized
