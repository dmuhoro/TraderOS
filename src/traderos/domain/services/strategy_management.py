from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import ENABLED_STRATEGY_STATUSES
from traderos.domain.entities import BacktestResult
from traderos.domain.entities import Strategy
from traderos.domain.entities import StrategyStatus
from traderos.domain.entities.value_objects import EquityCurve
from traderos.domain.entities.value_objects import Metrics
from traderos.domain.exceptions import DomainError
from traderos.domain.repositories.strategy_repository import BacktestResultRepository
from traderos.domain.repositories.strategy_repository import StrategyRepository
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.backtesting_service import synthetic_candles
from traderos.domain.services.strategy_framework import StrategyRegistry
from traderos.domain.services.strategy_framework import registry as default_registry


class StrategyLifecycleError(DomainError):
    """Raised when a strategy lifecycle operation violates catalog rules."""


class StrategyComparison(NamedTuple):
    names: list[str]
    metrics: dict[str, dict[str, float]]
    ranking: list[str]


@dataclass
class StrategyCatalogService:
    """Operator-facing strategy lifecycle (Programme C, WP-C3).

    Strategies are catalog entries persisted through ``StrategyRepository``.
    Each entry references a built-in ``template`` (the Python strategy class
    name) plus an operator-supplied ``params`` dict — operators create, clone,
    enable, disable, promote, archive and review without editing Python.
    """

    repo: StrategyRepository
    registry: StrategyRegistry = field(default_factory=lambda: default_registry)
    backtest: BacktestingService | None = None
    backtest_results: BacktestResultRepository | None = None
    version: str = "1.0.0"

    # --- catalog ---

    def ensure_seeded(self) -> int:
        """Insert the built-in templates as enabled strategies if absent."""
        seeded = 0
        for template in self._template_names():
            if self.repo.get_by_name(template) is None:
                self.repo.add(
                    Strategy(
                        name=template,
                        params={},
                        version=self.version,
                        status=StrategyStatus.ACTIVE,
                        template=template,
                    )
                )
                seeded += 1
        return seeded

    def list(self) -> list[Strategy]:
        return sorted(self.repo.list(), key=lambda s: s.name)

    def get(self, name: str) -> Strategy | None:
        return self.repo.get_by_name(name)

    def get_enabled(self) -> list[Strategy]:
        return [s for s in self.list() if s.status in ENABLED_STRATEGY_STATUSES]

    # --- lifecycle ---

    def create(self, name: str, template: str, params: dict | None = None) -> Strategy:
        if not name or not name.strip():
            raise StrategyLifecycleError("Strategy name must not be empty")
        if self.repo.get_by_name(name) is not None:
            raise StrategyLifecycleError(f"Strategy '{name}' already exists")
        if self.registry.get(template) is None:
            raise StrategyLifecycleError(
                f"Unknown template '{template}'. Templates: {', '.join(self._template_names())}"
            )
        strategy = Strategy(
            name=name.strip(),
            params=dict(params or {}),
            version=self.version,
            status=StrategyStatus.DRAFT,
            template=template,
        )
        self.repo.add(strategy)
        return strategy

    def clone(self, source_name: str, new_name: str, params: dict | None = None) -> Strategy:
        source = self.get(source_name)
        if source is None:
            raise StrategyLifecycleError(f"Strategy '{source_name}' not found")
        if self.repo.get_by_name(new_name) is not None:
            raise StrategyLifecycleError(f"Strategy '{new_name}' already exists")
        cloned = Strategy(
            name=new_name.strip(),
            params=dict(params if params is not None else source.params),
            version=source.version,
            status=StrategyStatus.DRAFT,
            template=source.template or source.name,
        )
        self.repo.add(cloned)
        return cloned

    def _require(self, name: str) -> Strategy:
        strategy = self.get(name)
        if strategy is None:
            raise StrategyLifecycleError(f"Strategy '{name}' not found")
        return strategy

    def enable(self, name: str) -> Strategy:
        strategy = self._require(name)
        if strategy.status in (StrategyStatus.ACTIVE, StrategyStatus.PROMOTED):
            return strategy
        if strategy.status == StrategyStatus.RETIRED:
            raise StrategyLifecycleError(f"Cannot enable retired strategy '{name}'")
        updated = _with_status(strategy, StrategyStatus.ACTIVE)
        self.repo.update(updated)
        return updated

    def disable(self, name: str) -> Strategy:
        strategy = self._require(name)
        if strategy.status == StrategyStatus.RETIRED:
            raise StrategyLifecycleError(f"Cannot disable retired strategy '{name}'")
        if strategy.status == StrategyStatus.DISABLED:
            return strategy
        updated = _with_status(strategy, StrategyStatus.DISABLED)
        self.repo.update(updated)
        return updated

    def promote(self, name: str) -> Strategy:
        """Promote a strategy toward controlled live.

        Exactly one strategy is PROMOTED at a time: promoting a new one
        demotes any existing PROMOTED entry back to ACTIVE.
        """
        strategy = self._require(name)
        if strategy.status == StrategyStatus.RETIRED:
            raise StrategyLifecycleError(f"Cannot promote retired strategy '{name}'")
        if strategy.status != StrategyStatus.ACTIVE:
            raise StrategyLifecycleError(
                f"Only active strategies can be promoted ('{name}' is {strategy.status.value})"
            )
        for existing in self.repo.list():
            if existing.status == StrategyStatus.PROMOTED and existing.id != strategy.id:
                self.repo.update(_with_status(existing, StrategyStatus.ACTIVE))
        promoted = _with_status(strategy, StrategyStatus.PROMOTED)
        self.repo.update(promoted)
        return promoted

    def archive(self, name: str) -> Strategy:
        strategy = self._require(name)
        if strategy.status == StrategyStatus.PROMOTED:
            raise StrategyLifecycleError(
                f"Cannot archive promoted strategy '{name}' — demote first"
            )
        updated = _with_status(strategy, StrategyStatus.RETIRED)
        self.repo.update(updated)
        return updated

    # --- analysis ---

    def compare(self, names: list[str], candles: int = 50) -> StrategyComparison:
        if self.backtest is None:
            raise StrategyLifecycleError("Backtesting not configured")
        if not names:
            raise StrategyLifecycleError("Provide at least one strategy name")
        market_id = uuid.uuid4()
        series = synthetic_candles(candles, market_id=market_id)
        metrics: dict[str, dict[str, float]] = {}
        for name in names:
            strategy = self._require(name)
            template = strategy.template or strategy.name
            cls = self.registry.get(template)
            if cls is None:
                raise StrategyLifecycleError(
                    f"Strategy '{name}' references unknown template '{template}'"
                )
            result, _ = self.backtest.run(cls(params=strategy.params), series, market_id)
            m = result.metrics
            metrics[name] = {
                "total_return": m.total_return,
                "sharpe_ratio": m.sharpe_ratio,
                "sortino_ratio": m.sortino_ratio,
                "calmar_ratio": m.calmar_ratio,
                "max_drawdown": m.max_drawdown,
                "win_rate": m.win_rate,
                "profit_factor": m.profit_factor,
                "expectancy": m.expectancy,
            }
        ranking = sorted(metrics, key=lambda n: metrics[n]["sharpe_ratio"], reverse=True)
        return StrategyComparison(names=list(names), metrics=metrics, ranking=ranking)

    def review(self, name: str) -> dict:
        """Performance review: latest backtest results and lifecycle state."""
        strategy = self._require(name)
        report: dict = {
            "name": strategy.name,
            "template": strategy.template or strategy.name,
            "version": strategy.version,
            "status": strategy.status.value,
            "params": strategy.params,
            "created_at": strategy.created_at.isoformat(),
            "backtests": [],
        }
        if self.backtest_results is not None:
            results = sorted(
                self.backtest_results.get_by_strategy(strategy.id),
                key=lambda r: r.created_at,
                reverse=True,
            )[:5]
            report["backtests"] = [
                {
                    "market_id": str(r.market_id),
                    "total_return": r.metrics.total_return,
                    "sharpe_ratio": r.metrics.sharpe_ratio,
                    "max_drawdown": r.metrics.max_drawdown,
                    "win_rate": r.metrics.win_rate,
                    "period_end": r.period_end.isoformat(),
                    "created_at": r.created_at.isoformat(),
                }
                for r in results
            ]
        return report

    def record_backtest(
        self,
        strategy_name: str,
        market_id: uuid.UUID,
        metrics: Metrics,
        equity_curve: EquityCurve,
        period_start: datetime,
        period_end: datetime,
    ) -> uuid.UUID | None:
        """Persist a backtest result against a strategy if a durable results
        repo is wired. Returns the result id, or None when no repo is present
        (results are then ephemeral — callers should surface that honestly).
        """
        if self.backtest_results is None:
            return None
        strategy = self._require(strategy_name)
        result = BacktestResult(
            strategy_id=strategy.id,
            market_id=market_id,
            metrics=metrics,
            equity_curve=equity_curve,
            period_start=period_start,
            period_end=period_end,
        )
        self.backtest_results.add(result)
        return result.id

    def history(self, strategy_name: str, limit: int = 20) -> dict:
        """Recent persisted backtest results for a strategy (empty if none)."""
        strategy = self._require(strategy_name)
        results: list[dict] = []
        if self.backtest_results is not None:
            sorted_results = sorted(
                self.backtest_results.get_by_strategy(strategy.id),
                key=lambda r: r.created_at,
                reverse=True,
            )[:limit]
            results = [
                {
                    "id": str(r.id),
                    "market_id": str(r.market_id),
                    "total_return": r.metrics.total_return,
                    "sharpe_ratio": r.metrics.sharpe_ratio,
                    "sortino_ratio": r.metrics.sortino_ratio,
                    "max_drawdown": r.metrics.max_drawdown,
                    "win_rate": r.metrics.win_rate,
                    "period_start": r.period_start.isoformat(),
                    "period_end": r.period_end.isoformat(),
                    "created_at": r.created_at.isoformat(),
                }
                for r in sorted_results
            ]
        return {"strategy": strategy_name, "results": results}

    def _template_names(self) -> list[str]:
        return list(self.registry.list())


def _with_status(strategy: Strategy, status: StrategyStatus) -> Strategy:
    return Strategy(
        name=strategy.name,
        params=strategy.params,
        version=strategy.version,
        status=status,
        id=strategy.id,
        template=strategy.template,
        created_at=strategy.created_at,
    )
