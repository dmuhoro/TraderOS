"""WP11 (G-03) — production risk-rail configuration: fail-closed resolution.

The numeric rails (daily loss, gross exposure, position size, max positions)
arm the same ``RiskService.authorize_order`` gate that protects the real
submission path (``CycleExecutor`` -> broker). These tests prove:

* paper mode keeps conservative defaults when nothing is configured;
* explicit values are resolved (yaml and ``RISK_*`` env, env wins);
* out-of-range / non-numeric values are rejected, never coerced;
* LIVE refuses to arm without explicit rails + a mandatory allowlist;
* the factory wires the resolved rails into the real ``RiskService`` and a
  live boot fails closed on bad rails before any broker is built.
"""

from __future__ import annotations

import pytest

from traderos.application import factory as factory_mod
from traderos.application.risk_config import resolve_risk_rails
from traderos.application.risk_config import validate_production_risk_settings
from traderos.domain.exceptions import ConfigError
from traderos.infrastructure.config.config_loader import Config

_LIVE_COMPLETE = {
    "daily_loss_pct": 0.01,
    "max_gross_exposure": 0.8,
    "max_position_size": 0.1,
    "max_positions_total": 4,
    "max_data_staleness_seconds": 120.0,
    "require_allowlist": True,
    "allowed_markets": ["AAPL"],
}


class TestPaperResolution:
    def test_defaults_when_unconfigured(self) -> None:
        rails = resolve_risk_rails(None, live=False)
        assert rails.daily_loss_pct == pytest.approx(0.02)
        assert rails.max_gross_exposure == pytest.approx(1.0)
        assert rails.max_position_size == pytest.approx(0.25)
        assert rails.max_positions_total == 10
        assert rails.max_data_staleness_seconds == pytest.approx(300.0)
        assert rails.allowed_markets == ()
        assert rails.require_allowlist is False

    def test_explicit_values_respected(self) -> None:
        rails = resolve_risk_rails({"daily_loss_pct": 0.03, "max_positions_total": "5"}, live=False)
        assert rails.daily_loss_pct == pytest.approx(0.03)
        assert rails.max_positions_total == 5
        assert rails.max_position_size == pytest.approx(0.25)

    @pytest.mark.parametrize(
        ("section", "needle"),
        [
            ({"daily_loss_pct": 0.0}, "daily_loss_pct"),
            ({"daily_loss_pct": 1.5}, "daily_loss_pct"),
            ({"max_gross_exposure": 0}, "max_gross_exposure"),
            ({"max_gross_exposure": 11}, "max_gross_exposure"),
            ({"max_position_size": "not-a-number"}, "max_position_size"),
            ({"max_positions_total": 0}, "max_positions_total"),
            ({"max_positions_total": 1001}, "max_positions_total"),
            ({"max_data_staleness_seconds": -5}, "max_data_staleness_seconds"),
            ({"require_allowlist": "maybe"}, "require_allowlist"),
            ({"allowed_markets": [1, 2]}, "allowed_markets"),
        ],
    )
    def test_invalid_values_rejected(self, section: dict, needle: str) -> None:
        with pytest.raises(ConfigError, match=needle):
            resolve_risk_rails(section, live=False)

    def test_non_numeric_types_rejected(self) -> None:
        with pytest.raises(ConfigError, match="daily_loss_pct"):
            resolve_risk_rails({"daily_loss_pct": True}, live=False)
        with pytest.raises(ConfigError, match="daily_loss_pct"):
            resolve_risk_rails({"daily_loss_pct": {}}, live=False)

    def test_non_integer_types_rejected(self) -> None:
        with pytest.raises(ConfigError, match="max_positions_total"):
            resolve_risk_rails({"max_positions_total": 3.5}, live=False)
        with pytest.raises(ConfigError, match="max_positions_total"):
            resolve_risk_rails({"max_positions_total": "abc"}, live=False)

    def test_non_list_allowlist_rejected(self) -> None:
        with pytest.raises(ConfigError, match="allowed_markets"):
            resolve_risk_rails({"allowed_markets": {"AAPL": 1}}, live=False)

    def test_false_allowlist_string(self) -> None:
        rails = resolve_risk_rails({"require_allowlist": "false"}, live=False)
        assert rails.require_allowlist is False


class TestEnvOverrides:
    def test_env_wins_over_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RISK_DAILY_LOSS_PCT", "0.015")
        rails = resolve_risk_rails({"daily_loss_pct": 0.04}, live=False)
        assert rails.daily_loss_pct == pytest.approx(0.015)

    def test_env_csv_allowlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RISK_ALLOWED_MARKETS", "AAPL, MSFT")
        monkeypatch.setenv("RISK_REQUIRE_ALLOWLIST", "true")
        rails = resolve_risk_rails(None, live=False)
        assert rails.allowed_markets == ("AAPL", "MSFT")
        assert rails.require_allowlist is True

    def test_env_satisfies_live_explicitness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RISK_DAILY_LOSS_PCT", "0.01")
        monkeypatch.setenv("RISK_MAX_GROSS_EXPOSURE", "0.5")
        monkeypatch.setenv("RISK_MAX_POSITION_SIZE", "0.1")
        monkeypatch.setenv("RISK_MAX_POSITIONS_TOTAL", "3")
        monkeypatch.setenv("RISK_REQUIRE_ALLOWLIST", "true")
        monkeypatch.setenv("RISK_ALLOWED_MARKETS", "AAPL")
        rails = resolve_risk_rails(None, live=True)
        assert rails.max_positions_total == 3


class TestLiveFailClosed:
    def test_live_without_explicit_rails_refuses(self) -> None:
        with pytest.raises(ConfigError, match="risk.daily_loss_pct"):
            resolve_risk_rails(None, live=True)

    def test_live_partial_rails_refuses_and_names_gaps(self) -> None:
        with pytest.raises(ConfigError, match="risk.max_positions_total"):
            resolve_risk_rails(
                {"daily_loss_pct": 0.01, "max_gross_exposure": 0.8, "max_position_size": 0.1},
                live=True,
            )

    def test_live_without_mandatory_allowlist_refuses(self) -> None:
        section = {k: v for k, v in _LIVE_COMPLETE.items() if k != "require_allowlist"}
        with pytest.raises(ConfigError, match="allowlist"):
            resolve_risk_rails(section, live=True)
        section = dict(_LIVE_COMPLETE, allowed_markets=[])
        with pytest.raises(ConfigError, match="allowlist"):
            resolve_risk_rails(section, live=True)

    def test_live_complete_config_arms(self) -> None:
        rails = resolve_risk_rails(_LIVE_COMPLETE, live=True)
        assert rails.daily_loss_pct == pytest.approx(0.01)
        assert rails.allowed_markets == ("AAPL",)
        assert rails.require_allowlist is True

    def test_gate_validator_reports_problems_not_raises(self) -> None:
        problems = validate_production_risk_settings({})
        assert problems and "risk.daily_loss_pct" in problems[0]
        assert validate_production_risk_settings(_LIVE_COMPLETE) == []


def _config(risk: dict | None) -> Config:
    settings: dict = {}
    if risk is not None:
        settings["risk"] = risk
    return Config(db_path=":memory:", _raw_settings=settings)


class TestFactoryWiring:
    def test_paper_defaults_reach_risk_service(self) -> None:
        orch = factory_mod.build_orchestrator(config=_config(None))
        rs = orch.risk_service
        assert rs.daily_loss_pct == pytest.approx(0.02)
        assert rs.max_position_size == pytest.approx(0.25)
        assert rs.max_positions_total == 10
        assert rs.max_gross_exposure == pytest.approx(1.0)

    def test_explicit_rails_reach_risk_service(self) -> None:
        orch = factory_mod.build_orchestrator(
            config=_config({"daily_loss_pct": 0.03, "max_positions_total": 7})
        )
        rs = orch.risk_service
        assert rs.daily_loss_pct == pytest.approx(0.03)
        assert rs.max_positions_total == 7

    def test_explicit_rails_arm_the_real_order_gate(self) -> None:
        """The resolved rails must be the ones gating order submission: with a
        1% daily-loss budget and equity 1000, an order after a -10 realized
        loss is refused by the very RiskService the loop uses."""
        import uuid

        orch = factory_mod.build_orchestrator(config=_config({"daily_loss_pct": 0.01}))
        rs = orch.risk_service
        market_id = uuid.uuid5(uuid.NAMESPACE_DNS, "traderos/AAPL")
        rs.record_realized_pnl(-10.0)
        verdict = rs.authorize_order(
            market_id=market_id, side="buy", quantity=1, price=100.0, equity=1000.0
        )
        assert not verdict.allowed
        assert "daily" in verdict.reason.lower()

    def test_live_boot_fails_closed_without_rails(self) -> None:
        with pytest.raises(ConfigError, match="risk"):
            factory_mod.build_orchestrator(mode="live", config=_config(None))

    def test_live_boot_passes_rails_then_hits_credential_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
            factory_mod.build_orchestrator(mode="live", config=_config(_LIVE_COMPLETE))
