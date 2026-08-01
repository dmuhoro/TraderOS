from __future__ import annotations

from typing import Any

import pytest

from traderos.application.factory import build_orchestrator
from traderos.domain.collectors.base import CollectorType
from traderos.infrastructure.config.config_loader import Config


@pytest.fixture(autouse=True)
def _memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")


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
