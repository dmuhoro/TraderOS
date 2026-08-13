from __future__ import annotations

import sqlite3
import sys
from typing import Any

import pytest

from traderos.application import factory
from traderos.application.factory import _stream_interval_seconds
from traderos.application.factory import _sync_strategy_registry
from traderos.application.factory import build_orchestrator
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.journaled_broker import JournaledBroker


@pytest.fixture(autouse=True)
def _memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    for var in (
        "VAULT_ADDR",
        "VAULT_TOKEN",
        "VAULT_MOUNT",
        "PROBE_HEALTH_URL",
        "PAGERDUTY_ROUTING_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _config(**extra: Any) -> Config:
    settings: dict[str, Any] = {
        "data_collection": {
            "forex_symbols": ["EURUSD"],
            "crypto_symbols": ["BTCUSDT"],
        }
    }
    settings.update(extra)
    return Config(db_path=":memory:", _raw_settings=settings)


class _Rails:
    daily_loss_pct = 5.0
    max_position_size = 0.25
    max_positions_total = 10
    max_gross_exposure = 1.0
    max_data_staleness_seconds = 300.0
    allowed_markets: tuple[str, ...] = ("BTCUSDT",)
    require_allowlist = False
    explicit_fields = frozenset()


class _StubAlpaca:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_oncall_provider_failure_is_warned_not_fatal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom() -> None:
        raise RuntimeError("transport unavailable")

    monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "not-a-real-key")
    monkeypatch.setattr(factory, "PagerDutyTransport", _boom)
    with caplog.at_level("WARNING"):
        orch = build_orchestrator(config=_config())
    assert orch is not None
    assert "on-call provider PAGERDUTY_ROUTING_KEY not wired" in caplog.text


def test_streaming_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("feed init failed")

    monkeypatch.setattr(
        "traderos.infrastructure.collectors.streaming_collector.StreamingFeedRunner", _boom
    )
    orch = build_orchestrator(
        config=_config(
            data_collection={
                "forex_symbols": ["EURUSD"],
                "crypto_symbols": ["BTCUSDT"],
                "binance": {"enabled": True, "streaming": True},
            }
        )
    )
    assert orch is not None
    assert orch.streaming_feed is None


def test_vault_and_health_probes_are_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_TOKEN", "dev-token")
    monkeypatch.setenv("VAULT_MOUNT", "secret")
    monkeypatch.setenv("PROBE_HEALTH_URL", "http://healthz")
    orch = build_orchestrator(config=_config())
    assert len(orch.probe_scheduler._probes) == 4
    rotator = factory._build_secret_rotator(None, None)
    assert any(type(p).__name__ == "VaultSecretProvider" for p in rotator.providers())


def test_stream_interval_seconds_fallback_and_parse() -> None:
    assert (
        _stream_interval_seconds(
            Config(
                db_path=":memory:", _raw_settings={"data_collection": {"timeframe": "quarterly"}}
            )
        )
        == 3600
    )
    assert (
        _stream_interval_seconds(
            Config(db_path=":memory:", _raw_settings={"data_collection": {"timeframe": "5m"}})
        )
        == 300
    )


def test_failover_manager_built_when_enabled() -> None:
    orch = build_orchestrator(config=_config(ha={"enabled": True}))
    assert orch.failover is not None
    assert orch.failover.status()["leading"] in (True, False)


def test_per_user_profiles_skip_invalid_entries() -> None:
    orch = build_orchestrator(
        config=_config(
            risk={
                "operator_user_id": "trader-1",
                "per_users": [123, {"user_id": ""}, {"user_id": "trader-1"}],
            }
        )
    )
    resolver = orch.risk_service.user_resolver
    assert resolver is not None
    assert resolver.resolve("trader-1") is not None
    assert resolver.resolve("trader-2") is None


def test_live_missing_credentials_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.setattr(factory, "resolve_risk_rails", lambda *a, **k: _Rails())
    with pytest.raises(RuntimeError, match="LIVE mode requires ALPACA_API_KEY"):
        build_orchestrator(mode="live", config=_config())


def test_live_missing_adapter_import_is_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(factory, "resolve_risk_rails", lambda *a, **k: _Rails())
    monkeypatch.setitem(sys.modules, "traderos.infrastructure.alpaca_broker", None)
    with pytest.raises(ImportError):
        build_orchestrator(mode="live", config=_config())


def test_live_adapter_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(factory, "resolve_risk_rails", lambda *a, **k: _Rails())

    def _boom(**kwargs: Any) -> None:
        raise ValueError("bad credentials")

    monkeypatch.setattr("traderos.infrastructure.alpaca_broker.AlpacaBrokerAdapter", _boom)
    with pytest.raises(RuntimeError, match="LIVE broker init failed and will not degrade to paper"):
        build_orchestrator(mode="live", config=_config())


def test_live_journal_wraps_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(factory, "resolve_risk_rails", lambda *a, **k: _Rails())
    monkeypatch.setattr("traderos.infrastructure.alpaca_broker.AlpacaBrokerAdapter", _StubAlpaca)
    orch = build_orchestrator(mode="live", config=_config())
    assert isinstance(orch.broker, JournaledBroker)


def test_live_journal_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(factory, "resolve_risk_rails", lambda *a, **k: _Rails())
    monkeypatch.setattr("traderos.infrastructure.alpaca_broker.AlpacaBrokerAdapter", _StubAlpaca)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("journal db unavailable")

    monkeypatch.setattr("traderos.infrastructure.journaled_broker.JournaledBroker", _boom)
    orch = build_orchestrator(mode="live", config=_config())
    assert not isinstance(orch.broker, JournaledBroker)
    assert type(orch.broker).__name__ == "CircuitBreakeredBroker"


def test_sync_strategy_registry_postgres_inserts_missing() -> None:
    class _PG:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def execute(self, sql: str, params: tuple | None = None) -> None:
            self.calls.append(sql)

        def fetchall(self) -> list[tuple]:
            return []

        def commit(self) -> None:
            self.calls.append("COMMIT")

    db = _PG()
    _sync_strategy_registry(db, "postgres")
    assert any("INSERT INTO strategy_registry" in c for c in db.calls)


def test_sync_strategy_registry_sqlite_inserts_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE strategy_registry ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, params TEXT, "
        "version TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
    )
    _sync_strategy_registry(conn, "sqlite")
    rows = conn.execute("SELECT name FROM strategy_registry").fetchall()
    assert rows
    conn.close()
