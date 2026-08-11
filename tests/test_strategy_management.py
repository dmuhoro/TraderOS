from __future__ import annotations

from traderos.domain.entities import StrategyStatus
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_management import StrategyCatalogService
from traderos.domain.services.strategy_management import StrategyLifecycleError
from traderos.infrastructure.repositories.in_memory import InMemoryBacktestResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryStrategyRepository


def _catalog() -> StrategyCatalogService:
    return StrategyCatalogService(
        repo=InMemoryStrategyRepository(),
        backtest=BacktestingService(execution=ExecutionService()),
        backtest_results=InMemoryBacktestResultRepository(),
    )


class TestStrategyCatalogSeeding:
    def test_ensure_seeded_creates_builtin_templates(self) -> None:
        catalog = _catalog()
        seeded = catalog.ensure_seeded()
        assert seeded == 3
        names = [s.name for s in catalog.list()]
        assert "moving_average_trend" in names
        assert "volatility_breakout" in names
        assert "mean_reversion" in names

    def test_ensure_seeded_is_idempotent(self) -> None:
        catalog = _catalog()
        catalog.ensure_seeded()
        assert catalog.ensure_seeded() == 0

    def test_seeded_strategies_are_active(self) -> None:
        catalog = _catalog()
        catalog.ensure_seeded()
        assert all(s.status == StrategyStatus.ACTIVE for s in catalog.get_enabled())


class TestStrategyCatalogLifecycle:
    def setup_method(self) -> None:
        self.catalog = _catalog()
        self.catalog.ensure_seeded()

    def test_get_unknown_returns_none(self) -> None:
        assert self.catalog.get("does_not_exist") is None

    def test_create_requires_valid_name(self) -> None:
        try:
            self.catalog.create("  ", "moving_average_trend")
        except StrategyLifecycleError:
            pass
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_create_requires_known_template(self) -> None:
        try:
            self.catalog.create("ma_fast", "unknown_template")
        except StrategyLifecycleError as exc:
            assert "Unknown template" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_create_rejects_duplicate(self) -> None:
        self.catalog.create("ma_fast", "moving_average_trend")
        try:
            self.catalog.create("ma_fast", "moving_average_trend")
        except StrategyLifecycleError:
            pass
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_create_makes_draft_with_template(self) -> None:
        created = self.catalog.create("ma_fast", "moving_average_trend", {"fast": 5, "slow": 15})
        assert created.status == StrategyStatus.DRAFT
        assert created.template == "moving_average_trend"
        assert created.params == {"fast": 5, "slow": 15}

    def test_clone_copies_params_and_template(self) -> None:
        self.catalog.create("ma_fast", "moving_average_trend", {"fast": 5, "slow": 15})
        cloned = self.catalog.clone("ma_fast", "ma_fast_clone")
        assert cloned.template == "moving_average_trend"
        assert cloned.params == {"fast": 5, "slow": 15}
        assert cloned.status == StrategyStatus.DRAFT

    def test_clone_unknown_source_raises(self) -> None:
        try:
            self.catalog.clone("nope", "other")
        except StrategyLifecycleError:
            pass
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_clone_duplicate_target_raises(self) -> None:
        self.catalog.create("ma_fast", "moving_average_trend")
        self.catalog.clone("ma_fast", "ma_fast_clone")
        try:
            self.catalog.clone("ma_fast", "ma_fast_clone")
        except StrategyLifecycleError as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_disable_retired_raises(self) -> None:
        self.catalog.archive("mean_reversion")
        try:
            self.catalog.disable("mean_reversion")
        except StrategyLifecycleError as exc:
            assert "retired" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_enable_and_disable(self) -> None:
        self.catalog.create("ma_fast", "moving_average_trend")
        enabled = self.catalog.enable("ma_fast")
        assert enabled.status == StrategyStatus.ACTIVE
        assert "ma_fast" in [s.name for s in self.catalog.get_enabled()]
        disabled = self.catalog.disable("ma_fast")
        assert disabled.status == StrategyStatus.DISABLED
        assert "ma_fast" not in [s.name for s in self.catalog.get_enabled()]

    def test_disable_seeded_strategy_gates_execution(self) -> None:
        assert "mean_reversion" in [s.name for s in self.catalog.get_enabled()]
        self.catalog.disable("mean_reversion")
        assert "mean_reversion" not in [s.name for s in self.catalog.get_enabled()]

    def test_promote_requires_active(self) -> None:
        self.catalog.create("ma_fast", "moving_average_trend")
        try:
            self.catalog.promote("ma_fast")
        except StrategyLifecycleError:
            pass
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_promote_demotes_previous_promoted(self) -> None:
        first = self.catalog.promote("moving_average_trend")
        assert first.status == StrategyStatus.PROMOTED
        second = self.catalog.promote("mean_reversion")
        assert second.status == StrategyStatus.PROMOTED
        assert self.catalog.get("moving_average_trend").status == StrategyStatus.ACTIVE
        promoted = [
            s.name for s in self.catalog.get_enabled() if s.status == StrategyStatus.PROMOTED
        ]
        assert promoted == ["mean_reversion"]

    def test_promoted_stays_enabled(self) -> None:
        self.catalog.promote("moving_average_trend")
        assert "moving_average_trend" in [s.name for s in self.catalog.get_enabled()]

    def test_archive_retires(self) -> None:
        archived = self.catalog.archive("mean_reversion")
        assert archived.status == StrategyStatus.RETIRED
        assert "mean_reversion" not in [s.name for s in self.catalog.get_enabled()]

    def test_archive_promoted_is_rejected(self) -> None:
        self.catalog.promote("moving_average_trend")
        try:
            self.catalog.archive("moving_average_trend")
        except StrategyLifecycleError as exc:
            assert "demote" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_retired_strategy_cannot_be_re_enabled(self) -> None:
        self.catalog.archive("mean_reversion")
        try:
            self.catalog.enable("mean_reversion")
        except StrategyLifecycleError:
            pass
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_get_enabled_returns_sorted(self) -> None:
        names = [s.name for s in self.catalog.get_enabled()]
        assert names == sorted(names)

    def test_enable_active_returns_strategy_unchanged(self) -> None:
        seeded = self.catalog.get("moving_average_trend")
        assert seeded.status == StrategyStatus.ACTIVE
        again = self.catalog.enable("moving_average_trend")
        assert again.status == StrategyStatus.ACTIVE
        assert again.id == seeded.id

    def test_disable_disabled_returns_strategy_unchanged(self) -> None:
        self.catalog.disable("moving_average_trend")
        disabled = self.catalog.get("moving_average_trend")
        again = self.catalog.disable("moving_average_trend")
        assert again.status == StrategyStatus.DISABLED
        assert again.id == disabled.id

    def test_promote_retired_raises(self) -> None:
        self.catalog.archive("mean_reversion")
        try:
            self.catalog.promote("mean_reversion")
        except StrategyLifecycleError as exc:
            assert "retired" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_compare_without_backtest_raises(self) -> None:
        catalog = StrategyCatalogService(
            repo=InMemoryStrategyRepository(),
            backtest=None,
            backtest_results=InMemoryBacktestResultRepository(),
        )
        catalog.ensure_seeded()
        try:
            catalog.compare(["moving_average_trend"])
        except StrategyLifecycleError as exc:
            assert "Backtesting not configured" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")


class TestStrategyCatalogAnalysis:
    def setup_method(self) -> None:
        self.catalog = _catalog()
        self.catalog.ensure_seeded()

    def test_compare_ranks_by_sharpe(self) -> None:
        comparison = self.catalog.compare(
            ["moving_average_trend", "volatility_breakout", "mean_reversion"], candles=50
        )
        assert set(comparison.names) == {
            "moving_average_trend",
            "volatility_breakout",
            "mean_reversion",
        }
        assert len(comparison.ranking) == 3
        assert all("sharpe_ratio" in m for m in comparison.metrics.values())

    def test_compare_requires_at_least_one_name(self) -> None:
        try:
            self.catalog.compare([])
        except StrategyLifecycleError:
            pass
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_compare_unknown_template_raises(self) -> None:
        created = self.catalog.create("custom", "moving_average_trend")
        from traderos.domain.entities import Strategy

        broken = Strategy(
            name="custom",
            params={},
            version=self.catalog.version,
            status=StrategyStatus.ACTIVE,
            id=created.id,
            template="broken_template",
        )
        self.catalog.repo.update(broken)
        try:
            self.catalog.compare(["custom"])
        except StrategyLifecycleError as exc:
            assert "unknown template" in str(exc)
        else:
            raise AssertionError("expected StrategyLifecycleError")

    def test_review_reports_state_and_backtests(self) -> None:
        report = self.catalog.review("moving_average_trend")
        assert report["name"] == "moving_average_trend"
        assert report["template"] == "moving_average_trend"
        assert report["status"] == StrategyStatus.ACTIVE.value
        assert "created_at" in report
