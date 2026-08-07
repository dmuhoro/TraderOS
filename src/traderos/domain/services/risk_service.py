from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Position
from traderos.domain.ports import AuditPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.reconciliation_service import PersistentKillSwitch

# Conservative fail-closed default: when no explicit daily loss dollar limit is
# configured, an order is refused once realized daily loss reaches this share of
# equity. Never unlimited.
DEFAULT_DAILY_LOSS_PCT = 0.02


class RiskAssessment(NamedTuple):
    kelly_fraction: float
    suggested_stop_loss: float
    suggested_take_profit: float
    risk_per_unit: float
    max_risk_amount: float


class PortfolioRisk(NamedTuple):
    var_95: float
    max_drawdown: float
    concentration_risk: list[tuple[uuid.UUID, float]]
    num_over_limit: int


class TradeVerdict(NamedTuple):
    allowed: bool
    reason: str


@dataclass(frozen=True)
class PerUserRiskProfile:
    """Per-trader risk rails (B2).

    Fail-closed: every cap has a conservative non-unlimited default, so a user
    who has no explicit profile still gets bounded risk — never an open
    allowance.
    """

    user_id: str
    max_gross_exposure: float = 1.0
    max_position_size: float = 0.25
    max_positions_total: int = 10
    daily_loss_pct: float = DEFAULT_DAILY_LOSS_PCT
    allowed_markets: frozenset[uuid.UUID] = frozenset()
    engaged: bool = False


class PerUserRiskResolver:
    """Resolves a per-user risk profile; unknown users fail closed.

    ``profiles`` maps a user_id to its rails. A user with no entry is resolved
    to a conservative default profile (bounded, never unlimited) and the
    ``enabled`` flag reflects that an explicit profile was found. When a user is
    unknown the caller (RiskService) denies.
    """

    def __init__(self, profiles: dict[str, PerUserRiskProfile] | None = None) -> None:
        self._profiles = profiles or {}

    def resolve(self, user_id: str) -> PerUserRiskProfile | None:
        return self._profiles.get(user_id)


@dataclass
class KillSwitch:
    consecutive_failures: int = 0
    max_consecutive_failures: int = 5
    daily_loss_limit: float | None = None
    daily_realized_pnl: float = 0.0
    _current_day: date = field(default_factory=lambda: datetime.now(UTC).date())
    circuit_open: bool = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.circuit_open = True

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_realized_pnl(self, pnl: float) -> None:
        today = datetime.now(UTC).date()
        if today != self._current_day:
            self._current_day = today
            self.daily_realized_pnl = 0.0
        self.daily_realized_pnl += pnl

    def can_trade(self) -> TradeVerdict:
        if self.circuit_open:
            return TradeVerdict(False, "Circuit breaker open")
        if self.consecutive_failures >= self.max_consecutive_failures:
            return TradeVerdict(False, f"{self.consecutive_failures} consecutive failures")
        if (
            self.daily_loss_limit is not None
            and abs(self.daily_realized_pnl) >= self.daily_loss_limit
        ):
            return TradeVerdict(False, f"Daily loss limit reached: {self.daily_realized_pnl:.2f}")
        return TradeVerdict(True, "")

    def engage(self) -> None:
        """Operator kill switch: block all trading immediately."""
        self.circuit_open = True

    def disengage(self) -> None:
        """Clear the operator kill switch and failure counters."""
        self.circuit_open = False
        self.consecutive_failures = 0

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.daily_realized_pnl = 0.0
        self.circuit_open = False


@dataclass
class RiskService:
    max_position_size: float = 0.25
    max_leverage: float = 2.0
    max_drawdown_limit: float = 0.20
    var_confidence: float = 1.645
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    persistent_kill_switch: PersistentKillSwitch | None = None
    max_positions_total: int = 10
    daily_loss_pct: float = DEFAULT_DAILY_LOSS_PCT
    max_gross_exposure: float = 1.0
    allowed_markets: frozenset[uuid.UUID] = frozenset()
    max_data_staleness_seconds: float = 300.0
    audit: AuditPort | None = None
    metrics: MetricsPort | None = None
    user_resolver: PerUserRiskResolver | None = None

    def _resolve_user(self, user_id: str | None) -> PerUserRiskProfile | None:
        if self.user_resolver is None or user_id is None:
            return None
        return self.user_resolver.resolve(user_id)

    def _audit_user(self, blocked: bool, user_id: str | None, reason: str) -> None:
        actor = user_id or "system"
        action = "risk.user_order_blocked" if blocked else "risk.user_allow"
        if self.audit:
            self.audit.record(action, actor, "trading", reason)
        if self.metrics:
            self.metrics.counter(action if blocked else "risk.order_allowed", 1.0)

    def can_trade(self, positions: list[Position], user_id: str | None = None) -> TradeVerdict:
        profile = self._resolve_user(user_id)
        if user_id is not None and self.user_resolver is not None and profile is None:
            reason = "No per-user risk profile configured for this trader"
            self._audit_user(True, user_id, reason)
            return TradeVerdict(False, reason)
        if profile is not None and profile.engaged:
            reason = "Kill switch engaged for this trader"
            self._audit_user(True, user_id, reason)
            return TradeVerdict(False, reason)
        effective_max_positions = (
            profile.max_positions_total if profile else self.max_positions_total
        )
        verdict = self.kill_switch.can_trade()
        if not verdict.allowed:
            if self.audit:
                self.audit.record("risk.kill_switch", "system", "trading", verdict.reason)
            if self.metrics:
                self.metrics.counter("circuit_breaker.tripped", 1.0)
            return verdict
        if self.persistent_kill_switch is not None and not self.persistent_kill_switch.can_trade():
            reason = "Persistent kill switch engaged"
            if self.audit:
                self.audit.record("risk.kill_switch", "system", "trading", reason)
            if self.metrics:
                self.metrics.counter("circuit_breaker.tripped", 1.0)
            return TradeVerdict(False, reason)
        if len(positions) >= effective_max_positions:
            reason = f"Max positions ({effective_max_positions}) reached"
            if self.audit:
                self.audit.record("risk.position_limit", "system", "trading", reason)
            return TradeVerdict(False, reason)
        return TradeVerdict(True, "")

    def record_realized_pnl(self, pnl: float) -> None:
        self.kill_switch.record_realized_pnl(pnl)
        if self.persistent_kill_switch is not None:
            self.persistent_kill_switch.record_realized_pnl(pnl)

    def authorize_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        equity: float,
        existing_gross_exposure: float = 0.0,
        last_candle_at: datetime | None = None,
        now: datetime | None = None,
        user_id: str | None = None,
    ) -> TradeVerdict:
        """Per-order fail-closed gate at the live submission boundary.

        Refuses an order when the daily loss budget is exhausted, when the
        market is not on the configured allowlist, when market data is stale
        (data-gap circuit breaker), when the order notional breaches
        ``max_position_size`` of equity, or when adding the order would push
        total gross exposure past ``max_gross_exposure`` of equity. An
        unconfigured daily loss limit defaults to a conservative share of
        equity (``daily_loss_pct``), never unlimited.

        When ``user_id`` is given and a per-user resolver is configured, the
        trader's own rails (daily loss, allowlist, position size, gross
        exposure) apply; an unknown trader is denied (fail-closed) and every
        verdict is attributed to the ``user_id`` in the audit trail.
        """
        profile = self._resolve_user(user_id)
        if user_id is not None and self.user_resolver is not None and profile is None:
            reason = "No per-user risk profile configured for this trader"
            self._audit_user(True, user_id, reason)
            return TradeVerdict(False, reason)
        if profile is not None and profile.engaged:
            reason = "Kill switch engaged for this trader"
            self._audit_user(True, user_id, reason)
            return TradeVerdict(False, reason)
        if equity <= 0:
            return self._block_order(
                market_id,
                side,
                f"Cannot size order against non-positive equity: {equity}",
                user_id=user_id,
            )
        eff_daily_loss_pct = profile.daily_loss_pct if profile else self.daily_loss_pct
        daily_limit = self.kill_switch.daily_loss_limit
        if daily_limit is None:
            daily_limit = equity * eff_daily_loss_pct
        realized = abs(self.kill_switch.daily_realized_pnl)
        if realized >= daily_limit:
            return self._block_order(
                market_id,
                side,
                f"Daily loss limit reached: {realized:.2f} >= {daily_limit:.2f}",
                user_id=user_id,
            )
        eff_allowed = profile.allowed_markets if profile else self.allowed_markets
        if eff_allowed and market_id not in eff_allowed:
            return self._block_order(
                market_id,
                side,
                f"Market {market_id} is not on the configured allowlist",
                user_id=user_id,
            )
        if last_candle_at is not None and now is not None:
            staleness = (now - last_candle_at).total_seconds()
            if staleness > self.max_data_staleness_seconds:
                return self._block_order(
                    market_id,
                    side,
                    f"Market data stale: last candle {staleness:.0f}s old "
                    f"(threshold {self.max_data_staleness_seconds:.0f}s)",
                    user_id=user_id,
                )
        notional = quantity * price
        eff_max_pos = profile.max_position_size if profile else self.max_position_size
        cap = equity * eff_max_pos
        if notional > cap:
            return self._block_order(
                market_id,
                side,
                f"Order notional {notional:.2f} exceeds max_position_size "
                f"({eff_max_pos} of equity = {cap:.2f})",
                user_id=user_id,
            )
        eff_gross = profile.max_gross_exposure if profile else self.max_gross_exposure
        gross_cap = equity * eff_gross
        total_exposure = existing_gross_exposure + notional
        if existing_gross_exposure > gross_cap:
            return self._block_order(
                market_id,
                side,
                f"Portfolio gross exposure {existing_gross_exposure:.2f} already "
                f"exceeds cap ({eff_gross} of equity = {gross_cap:.2f})",
                user_id=user_id,
            )
        if total_exposure > gross_cap:
            return self._block_order(
                market_id,
                side,
                f"Order would push gross exposure to {total_exposure:.2f}, "
                f"over cap ({eff_gross} of equity = {gross_cap:.2f})",
                user_id=user_id,
            )
        verdict = self.kill_switch.can_trade()
        if not verdict.allowed:
            return verdict
        if user_id is not None and self.user_resolver is not None:
            self._audit_user(False, user_id, f"order {market_id} allowed")
        return TradeVerdict(True, "")

    def _block_order(
        self, market_id: uuid.UUID, side: str, reason: str, user_id: str | None = None
    ) -> TradeVerdict:
        actor = user_id or "system"
        detail = f"side={side} market={market_id} reason={reason}"
        if self.audit:
            self.audit.record("risk.order_blocked", actor, "trading", detail)
        if self.metrics:
            self.metrics.counter("risk.order_blocked", 1.0)
        return TradeVerdict(False, reason)

    def assess_trade(
        self,
        price: float,
        confidence: float,
        atr: float,
        account_equity: float,
        win_rate: float = 0.5,
    ) -> RiskAssessment:
        if win_rate <= 0 or win_rate >= 1:
            return RiskAssessment(
                kelly_fraction=0.0,
                suggested_stop_loss=price - atr * 2,
                suggested_take_profit=price + atr * 3,
                risk_per_unit=price - (price - atr * 2),
                max_risk_amount=account_equity * 0.02,
            )
        b = win_rate / (1 - win_rate)
        kelly = (b * confidence - (1 - confidence)) / b
        kelly = max(0.0, min(kelly, self.max_position_size))

        stop_loss = price - atr * 2
        take_profit = price + atr * 3
        risk_per_unit = price - stop_loss
        max_risk = account_equity * 0.02

        return RiskAssessment(
            kelly_fraction=kelly,
            suggested_stop_loss=stop_loss,
            suggested_take_profit=take_profit,
            risk_per_unit=risk_per_unit,
            max_risk_amount=max_risk,
        )

    def compute_var(self, positions: list[Position]) -> float:
        if not positions:
            return 0.0
        total_value = sum(p.quantity * p.current_price for p in positions)
        if total_value == 0:
            return 0.0
        weights = [(p.quantity * p.current_price) / total_value for p in positions]
        returns = [
            (p.current_price - p.entry_price) / p.entry_price if p.entry_price else 0.0
            for p in positions
        ]
        mean_return = sum(w * r for w, r in zip(weights, returns, strict=False))
        variance = sum(w * (r - mean_return) ** 2 for w, r in zip(weights, returns, strict=False))
        std = math.sqrt(variance) if variance > 0 else 0.0
        return self.var_confidence * std * total_value

    def compute_max_drawdown(self, equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
        return max_dd

    def check_concentration(
        self,
        positions: list[Position],
    ) -> PortfolioRisk:
        if not positions:
            return PortfolioRisk(
                var_95=0.0, max_drawdown=0.0, concentration_risk=[], num_over_limit=0
            )
        total = sum(p.quantity * p.current_price for p in positions)
        concentrations: list[tuple[uuid.UUID, float]] = []
        over = 0
        for p in positions:
            pct = (p.quantity * p.current_price) / total
            concentrations.append((p.market_id, pct))
            if pct > self.max_position_size:
                over += 1
        var = self.compute_var(positions)
        return PortfolioRisk(
            var_95=var,
            max_drawdown=self.max_drawdown_limit,
            concentration_risk=concentrations,
            num_over_limit=over,
        )

    def enforce_limits(
        self,
        position: Position,
        portfolio_risk: PortfolioRisk,
    ) -> bool:
        if portfolio_risk.num_over_limit > 0:
            return False
        drawdown_limit = self.max_drawdown_limit * position.quantity * position.entry_price
        return not (position.pnl < 0 and abs(position.pnl) > drawdown_limit)
