"""Fail-safe position flattening when the kill switch engages (G-03).

``FlattenService`` is the "kill-switch → flatten" rail: when the risk circuit
opens, every position the application believes it holds is closed via market
orders through the **real** broker adapter (the same seam live orders use), so
the flatten provably goes through the true submission path — journaled broker →
circuit breaker → Alpaca. The flatten uses the dedicated ``place_flatten_order``
emergency seam, which bypasses the broker rate limiter and the order-size
guardrails: a kill-switch close must never be throttled by a strategy's own rate
usage nor refused by size policy (it closes exactly the exposure we hold).
Idempotency journaling and the circuit breaker stay on the path. Flattening is
exactly-once per process (broker idempotency is the adapter's job; this service
guarantees it is not re-issued on every cycle once the circuit is open).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.entities import Position
from traderos.domain.ports import AuditPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.portfolio_service import PortfolioService


@dataclass
class FlattenResult:
    flattened: bool
    close_orders: int = 0
    failed_orders: int = 0
    errors: list[str] = field(default_factory=list)
    reason: str | None = None


class FlattenService:
    def __init__(
        self,
        broker: BrokerAdapter,
        portfolio_service: PortfolioService,
        notifications: NotificationService,
        audit: AuditPort | None = None,
        metrics: MetricsPort | None = None,
        market_prices: Callable[[uuid.UUID], float] | None = None,
    ) -> None:
        self._broker = broker
        self._portfolio_service = portfolio_service
        self._notifications = notifications
        self._audit = audit
        self._metrics = metrics
        self._market_prices = market_prices
        self._flattened: FlattenResult | None = None

    @property
    def flattened(self) -> bool:
        return self._flattened is not None

    def flatten(self, reason: str = "Kill switch engaged") -> FlattenResult:
        if self._flattened is not None:
            return self._flattened

        positions: list[Position] = self._portfolio_service.get_summary(0.0).open_positions
        result = FlattenResult(flattened=True, reason=reason)
        for pos in positions:
            qty = abs(float(pos.quantity))
            if qty <= 0.0:
                continue
            side = "sell" if pos.quantity > 0 else "buy"
            price = self._market_prices(pos.market_id) if self._market_prices else pos.current_price
            try:
                fill = self._broker.place_flatten_order(pos.market_id, side, qty, close_price=price)
            except Exception as e:  # noqa: BLE001 — a failed close must never crash flatten
                result.failed_orders += 1
                result.errors.append(f"{pos.market_id}: flatten close failed: {e}")
                continue
            if fill.filled:
                result.close_orders += 1
                if self._audit:
                    self._audit.record(
                        "risk.flatten",
                        "system",
                        str(pos.market_id),
                        f"side={side} qty={qty} price={price} reason={reason}",
                    )
            else:
                result.failed_orders += 1
                result.errors.append(f"{pos.market_id}: flatten close not filled ({fill.status})")

        if self._metrics:
            self._metrics.counter("risk.flatten", 1.0)
            self._metrics.counter("risk.flatten.close_orders", float(result.close_orders))
        self._notifications.critical(
            "Kill Switch Flatten",
            f"{result.close_orders} positions closed, "
            f"{result.failed_orders} failed — reason: {reason}",
        )
        self._flattened = result
        return result
