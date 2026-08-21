from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from traderos.application.factory import build_async_daemon
from traderos.application.factory import build_orchestrator
from traderos.domain.collectors.base import CollectorType
from traderos.domain.exceptions import ServiceError
from traderos.infrastructure.config.config_loader import Config


@pytest.fixture(autouse=True)
def _memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.delenv("BINANCE_ENABLED", raising=False)
    monkeypatch.delenv("BINANCE_STREAMING", raising=False)


def _config(binance_enabled: bool | None = None) -> Config:
    settings: dict[str, Any] = {
        "data_collection": {
            "forex_symbols": ["EURUSD"],
            "crypto_symbols": ["BTCUSDT"],
        }
    }
    if binance_enabled is not None:
        settings["data_collection"]["binance"] = {"enabled": binance_enabled}
    return Config(db_path=":memory:", _raw_settings=settings)


def _streaming_config(binance_enabled: bool = True) -> Config:
    settings: dict[str, Any] = {
        "data_collection": {
            "forex_symbols": ["EURUSD"],
            "crypto_symbols": ["BTCUSDT"],
            "binance": {"enabled": binance_enabled, "streaming": True},
        }
    }
    return Config(db_path=":memory:", _raw_settings=settings)


def _source_types(orch: Any) -> dict[str, str]:
    return {s.symbol: s.collector_type.value for s in orch.data_ingestion.sources}


def test_crypto_defaults_to_mock_when_binance_not_configured() -> None:
    orch = build_orchestrator(config=_config())
    mock = CollectorType.MOCK.value
    assert _source_types(orch) == {"EURUSD": mock, "BTCUSDT": mock}


def test_crypto_defaults_to_mock_when_binance_disabled() -> None:
    orch = build_orchestrator(config=_config(binance_enabled=False))
    mock = CollectorType.MOCK.value
    assert _source_types(orch) == {"EURUSD": mock, "BTCUSDT": mock}


def test_crypto_uses_binance_when_enabled_and_installed() -> None:
    orch = build_orchestrator(config=_config(binance_enabled=True))
    sources = _source_types(orch)
    assert sources["BTCUSDT"] == CollectorType.BINANCE.value
    assert sources["EURUSD"] == CollectorType.MOCK.value


def test_forex_never_uses_binance() -> None:
    orch = build_orchestrator(config=_config(binance_enabled=True))
    sources = _source_types(orch)
    assert sources["EURUSD"] == CollectorType.MOCK.value


def test_binance_env_override_enables_real_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator can enable the real Binance feed on a deployed instance via
    env var without editing the committed YAML (which stays off for CI)."""
    monkeypatch.setenv("BINANCE_ENABLED", "true")
    orch = build_orchestrator(config=_config(binance_enabled=False))
    sources = _source_types(orch)
    assert sources["BTCUSDT"] == CollectorType.BINANCE.value


def test_binance_env_override_false_disables_real_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINANCE_ENABLED", "false")
    orch = build_orchestrator(config=_config(binance_enabled=True))
    sources = _source_types(orch)
    assert sources["BTCUSDT"] == CollectorType.MOCK.value


def test_streaming_off_by_default_even_when_binance_enabled() -> None:
    """A2: the live streaming feed must never engage unless explicitly enabled,
    so CI/tests stay network-free. Binance REST only is not enough."""
    orch = build_orchestrator(config=_config(binance_enabled=True))
    sources = _source_types(orch)
    assert sources["BTCUSDT"] == CollectorType.BINANCE.value
    assert orch.streaming_feed is None


def test_streaming_enabled_registers_streaming_source_and_feed() -> None:
    """A2: enabling binance.streaming wires a real streaming feed onto the
    orchestrator and routes crypto candles through the STREAMING collector."""
    orch = build_orchestrator(config=_streaming_config())
    sources = _source_types(orch)
    assert sources["BTCUSDT"] == CollectorType.STREAMING.value
    assert sources["EURUSD"] == CollectorType.MOCK.value
    assert orch.streaming_feed is not None
    orch.streaming_feed.stop()


def _symbol_mid(symbol: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{symbol}")


def test_build_async_daemon_routes_the_same_production_symbol_map() -> None:
    """The async daemon must route a tick to the SAME market the sync loop
    trades (deterministic ``uuid5("traderos/{symbol}")``), so a validated tick
    hits the real cycle executor for the configured market."""
    daemon = build_async_daemon(config=_config(binance_enabled=True))
    assert daemon.market_symbols == {
        _symbol_mid("EURUSD"): "EURUSD",
        _symbol_mid("BTCUSDT"): "BTCUSDT",
    }
    # No feed configured: fail-closed — the async daemon refuses to idle without
    # an ingestor rather than silently doing nothing.
    assert daemon._ingestor is None
    with pytest.raises(ServiceError):
        asyncio.run(daemon.run_forever())


def test_build_async_daemon_wires_ingestor_when_streaming_enabled() -> None:
    """Enabling binance.streaming wires a real ParetoWebSocketIngestor onto the
    async daemon so validated ticks flow into the real submission path."""
    daemon = build_async_daemon(config=_streaming_config())
    assert daemon._ingestor is not None
    assert daemon._ingestor._symbols == ["BTCUSDT"]


def test_build_async_daemon_fails_closed_when_streaming_constructor_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken streaming transport must never crash the factory: the daemon is
    still built but without a feed, so run_forever fails closed."""
    from traderos.infrastructure import async_streaming

    class _BoomTransport:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("transport unavailable")

    monkeypatch.setattr(async_streaming, "AsyncBinanceStreamTransport", _BoomTransport)
    daemon = build_async_daemon(config=_streaming_config())
    assert daemon._ingestor is None
    with pytest.raises(ServiceError):
        asyncio.run(daemon.run_forever())


def test_build_async_daemon_reuses_the_real_cycle_executor() -> None:
    """The async daemon must drive a REAL CycleExecutor — the same broker chain
    and risk gate the sync loop uses, never a mock."""
    from traderos.application.cycle_executor import CycleExecutor
    from traderos.infrastructure.broker_circuit_breaker import CircuitBreakeredBroker

    daemon = build_async_daemon(config=_config(binance_enabled=True))
    executor = daemon._cycle_executor
    assert isinstance(executor, CycleExecutor)
    # The broker seam is the real wrapped production chain (circuit breaker ->
    # rate limiter -> guardrail -> paper), not a stub.
    assert isinstance(executor._broker, CircuitBreakeredBroker)


def _per_user_risk_config() -> Config:
    settings: dict[str, Any] = {
        "risk": {
            "operator_user_id": "trader-1",
            "per_users": [
                {
                    "user_id": "trader-1",
                    "max_gross_exposure": 0.5,
                    "max_position_size": 0.1,
                    "max_positions_total": 3,
                    "allowed_markets": ["BTCUSDT", "ETHUSDT"],
                },
                {"user_id": "trader-2", "engaged": True},
            ],
        },
        "data_collection": {
            "forex_symbols": ["EURUSD"],
            "crypto_symbols": ["BTCUSDT"],
        },
    }
    return Config(db_path=":memory:", _raw_settings=settings)


def test_factory_wires_per_user_risk_resolver_from_config() -> None:
    """B2: the production factory must route per-user rails from config — proven
    through the resolver it actually installs on its RiskService."""
    orch = build_orchestrator(config=_per_user_risk_config())
    resolver = orch.risk_service.user_resolver
    assert resolver is not None
    assert orch.trading_user_id == "trader-1"

    p1 = resolver.resolve("trader-1")
    assert p1 is not None
    assert p1.max_gross_exposure == 0.5
    assert p1.max_position_size == 0.1
    assert p1.max_positions_total == 3
    assert p1.engaged is False

    p2 = resolver.resolve("trader-2")
    assert p2 is not None
    assert p2.engaged is True

    assert resolver.resolve("unknown-user") is None
