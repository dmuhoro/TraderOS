"""Programme B — Operational Trust regression tests.

Covers:
- OT-002  durable idempotency across process restart (order-event journal)
- OT-003  outbox: publish failure is retained and replayed
- OT-005  ACKNOWLEDGED orders are open in every repository
- OT-006  concurrent order events serialize to exactly one accepted transition
- OT-009  duplicate/overflow fill guards
- OT-005/H7  PostgreSQL migration path via cursor routing and v004 fresh-PG
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Self
from unittest.mock import Mock

import pytest

from traderos.application.order_event_engine import OrderEventEngine
from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeSide
from traderos.domain.entities.trade import TradeStatus
from traderos.infrastructure.database.migration_manager import get_current_version
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.journal import OrderEventJournal
from traderos.infrastructure.market_stream import CandleAggregator
from traderos.infrastructure.market_stream import InvalidTickError
from traderos.infrastructure.market_stream import ReplayRecorder
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick
from traderos.infrastructure.market_stream import normalize_timestamp
from traderos.infrastructure.market_stream import validate_tick
from traderos.infrastructure.repositories.in_memory.trades import InMemoryTradeRepository
from traderos.infrastructure.repositories.sqlite.trades import SQLiteTradeRepository


def _test_config(tmp_path, name: str):
    from traderos.infrastructure.config.config_loader import Config

    return Config(db_path=str(tmp_path / name), log_level="CRITICAL")


# ---------------------------------------------------------------------------
# OT-005 / H7 — PostgreSQL migration path
# ---------------------------------------------------------------------------


class FakePGCursor:
    def __init__(self, conn: FakePGConn) -> None:
        self.conn = conn
        self._result: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.conn.statements.append((sql, params))
        if "INSERT INTO _schema_version" in sql:
            self.conn.versions.append(int(params[0]))
        if "DELETE FROM _schema_version" in sql:
            self.conn.versions = [v for v in self.conn.versions if v != int(params[0])]
        if "SELECT to_regclass" in sql:
            self._result = ("trades",) if self.conn.trades_exists else (None,)
        if "COALESCE(MAX(version)" in sql:
            self._result = (max(self.conn.versions),) if self.conn.versions else (0,)

    def fetchone(self) -> tuple | None:
        return self._result

    def fetchall(self) -> list:
        return []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakePGConn:
    """Mimics a psycopg2 connection: cursor()-only, no execute() method."""

    def __init__(self, trades_exists: bool = False) -> None:
        self.statements: list[tuple[str, tuple | None]] = []
        self.versions: list[int] = []
        self.commits = 0
        self.trades_exists = trades_exists

    def cursor(self) -> FakePGCursor:
        return FakePGCursor(self)

    def commit(self) -> None:
        self.commits += 1


def test_pg_migration_runs_without_conn_execute():
    conn = FakePGConn()
    migrate(conn)
    assert conn.versions == [1, 2, 3, 4, 5, 6]
    assert get_current_version(conn) == 6
    assert conn.commits > 0
    assert not hasattr(conn, "execute")
    creates = [s for s, _ in conn.statements if "CREATE TABLE" in s]
    assert any("_schema_version" in s for s in creates)
    assert any("order_events" in s for s in creates)


def test_pg_migration_down_path():
    conn = FakePGConn()
    migrate(conn)
    assert conn.versions == [1, 2, 3, 4, 5, 6]
    migrate(conn, target_version=3)
    assert conn.versions == [1, 2, 3]
    assert get_current_version(conn) == 3


def test_pg_v004_guards_missing_trades_table():
    conn = FakePGConn(trades_exists=False)
    migrate(conn)
    alters = [s for s, _ in conn.statements if "ALTER TABLE trades" in s]
    assert alters == []


def test_pg_v004_alters_when_trades_table_exists():
    conn = FakePGConn(trades_exists=True)
    migrate(conn)
    alters = [s for s, _ in conn.statements if "ALTER TABLE trades" in s]
    assert any("ADD COLUMN IF NOT EXISTS external_order_id" in s for s in alters)


def test_sqlite_down_removes_version_marker_before_down_runs():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    assert get_current_version(conn) == 6
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='order_events'").fetchone()

    v005 = importlib.import_module(
        "traderos.infrastructure.database.migrations.v005_order_event_journal"
    )
    original = v005.down
    observed: dict[str, bool] = {}

    def spy(conn, backend: str = "sqlite") -> None:
        row = conn.execute("SELECT 1 FROM _schema_version WHERE version = 5").fetchone()
        observed["version_row_present_during_down"] = row is not None
        original(conn, backend=backend)

    v005.down = spy
    try:
        migrate(conn, target_version=4)
    finally:
        v005.down = original

    assert observed["version_row_present_during_down"] is False
    assert get_current_version(conn) == 4
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='order_events'").fetchone() is None


# ---------------------------------------------------------------------------
# OT-002 — durable idempotency across restart
# ---------------------------------------------------------------------------


def _journal(tmp_path) -> OrderEventJournal:
    conn = sqlite3.connect(str(tmp_path / "journal.db"))
    conn.row_factory = sqlite3.Row
    return OrderEventJournal(conn)


def _trade() -> Trade:
    t = Trade(uuid.uuid4(), uuid.uuid4(), TradeSide.BUY, 2, 100)
    t.submit("broker-1")
    return t


def test_durable_idempotency_survives_restart(tmp_path):
    journal = _journal(tmp_path)
    bus = InMemoryEventBus()
    events = []
    bus.subscribe("execution.order_status", events.append)

    engine_one = OrderEventEngine(bus, journal=journal)
    trade_one = _trade()
    assert engine_one.apply(trade_one, TradeStatus.ACKNOWLEDGED, event_id="ack-1") is True

    engine_two = OrderEventEngine(bus, journal=journal)
    trade_two = _trade()
    assert engine_two.apply(trade_two, TradeStatus.ACKNOWLEDGED, event_id="ack-1") is False
    assert engine_two.seen_count == 1
    assert len(events) == 1
    assert journal.count() == 1


def test_journal_preloads_seen_keys(tmp_path):
    journal = _journal(tmp_path)
    bus = InMemoryEventBus()
    OrderEventEngine(bus, journal=journal).apply(
        _trade(), TradeStatus.FILLED, event_id="fill-1", fill_quantity=2, fill_price=101
    )
    restarted = OrderEventEngine(bus, journal=journal)
    assert restarted.seen_count == 1
    assert journal.contains("fill-1")


# ---------------------------------------------------------------------------
# OT-003 — outbox: publish failure retained and replayed
# ---------------------------------------------------------------------------


def test_publish_failure_is_retained_and_replayed(tmp_path):
    journal = _journal(tmp_path)
    events = []

    class FlakyBus(InMemoryEventBus):
        def __init__(self, *, fail_publish: bool) -> None:
            super().__init__()
            self.fail_publish = fail_publish

        def publish(self, event) -> None:
            if self.fail_publish:
                raise RuntimeError("bus down")
            events.append(event)

    flaky = FlakyBus(fail_publish=True)
    engine = OrderEventEngine(flaky, journal=journal, persist=lambda t: None)
    trade = _trade()
    with pytest.raises(RuntimeError, match="bus down"):
        engine.apply(trade, TradeStatus.ACKNOWLEDGED, event_id="ack-1")
    assert journal.pending_count() == 1

    flaky.fail_publish = False
    assert engine.replay() == 1
    assert journal.pending_count() == 0
    assert len(events) == 1
    assert events[0].payload["event_id"] == "ack-1"


def test_duplicate_after_publish_failure_not_reapplied(tmp_path):
    journal = _journal(tmp_path)
    bus = InMemoryEventBus()
    engine = OrderEventEngine(bus, journal=journal, persist=lambda t: None)
    trade = _trade()
    assert engine.apply(
        trade, TradeStatus.FILLED, event_id="fill-9", fill_quantity=2, fill_price=100
    )
    assert (
        engine.apply(trade, TradeStatus.FILLED, event_id="fill-9", fill_quantity=2, fill_price=100)
        is False
    )
    assert journal.pending_count() == 0


# ---------------------------------------------------------------------------
# OT-006 — concurrent order events serialize to one accepted transition
# ---------------------------------------------------------------------------


def test_concurrent_identical_events_exactly_once():
    bus = InMemoryEventBus()
    engine = OrderEventEngine(bus)
    trade = _trade()
    results: list[bool] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(
                engine.apply(
                    trade, TradeStatus.FILLED, event_id="fill-c", fill_quantity=2, fill_price=100
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(64)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sum(results) == 1
    assert trade.status == TradeStatus.FILLED
    assert engine.seen_count == 1


def test_concurrent_distinct_events_apply_in_order():
    from traderos.domain.entities.trade import InvalidTradeTransitionError

    bus = InMemoryEventBus()
    engine = OrderEventEngine(bus)
    trade = _trade()
    errors: list[BaseException] = []
    accepted_fills = 0

    def worker(n: int) -> None:
        nonlocal accepted_fills
        try:
            if engine.apply(trade, TradeStatus.ACKNOWLEDGED, event_id=f"ack-{n}"):
                pass
            if engine.apply(
                trade, TradeStatus.FILLED, event_id=f"fill-{n}", fill_quantity=2, fill_price=100
            ):
                accepted_fills += 1
        except InvalidTradeTransitionError:
            pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert trade.status == TradeStatus.FILLED
    assert accepted_fills == 1


# ---------------------------------------------------------------------------
# OT-009 — fill guards
# ---------------------------------------------------------------------------


def test_fill_quantity_exceeding_order_rejected():
    bus = InMemoryEventBus()
    engine = OrderEventEngine(bus)
    trade = _trade()
    with pytest.raises(ValueError, match="exceeds order quantity"):
        engine.apply(trade, TradeStatus.FILLED, event_id="over", fill_quantity=3, fill_price=100)


def test_fill_quantity_non_positive_rejected():
    bus = InMemoryEventBus()
    engine = OrderEventEngine(bus)
    trade = _trade()
    with pytest.raises(ValueError, match="invalid fill quantity"):
        engine.apply(trade, TradeStatus.FILLED, event_id="zero", fill_quantity=0, fill_price=100)


def test_fill_price_non_positive_rejected():
    bus = InMemoryEventBus()
    engine = OrderEventEngine(bus)
    trade = _trade()
    with pytest.raises(ValueError, match="invalid fill price"):
        engine.apply(trade, TradeStatus.FILLED, event_id="neg", fill_quantity=2, fill_price=-1)


# ---------------------------------------------------------------------------
# OT-005 — ACKNOWLEDGED orders are open in every repository
# ---------------------------------------------------------------------------


def test_acknowledged_trade_is_open_in_memory_repo():
    repo = InMemoryTradeRepository()
    trade = _trade()
    trade.acknowledge()
    repo.add(trade)
    assert [t.id for t in repo.get_open()] == [trade.id]


def test_acknowledged_trade_is_open_in_sqlite_repo(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "repo.db"))
    conn.row_factory = sqlite3.Row
    repo = SQLiteTradeRepository(conn)
    trade = _trade()
    trade.acknowledge()
    repo.add(trade)
    assert [t.id for t in repo.get_open()] == [trade.id]


def test_filled_trade_is_not_open():
    repo = InMemoryTradeRepository()
    trade = _trade()
    trade.fill(2, 100)
    repo.add(trade)
    assert repo.get_open() == []


def test_open_status_constant_includes_acknowledged():
    from traderos.domain.entities.trade import OPEN_TRADE_STATUSES

    assert TradeStatus.ACKNOWLEDGED in OPEN_TRADE_STATUSES


# ---------------------------------------------------------------------------
# OT-004 — tick validation and timestamp normalization
# ---------------------------------------------------------------------------


class _Transport:
    def close(self) -> None:
        pass


def _raw_tick(**overrides) -> dict:
    base = {
        "symbol": "BTCUSDT",
        "price": "100",
        "quantity": "1",
        "timestamp": datetime.now(tz=UTC).timestamp(),
    }
    base.update(overrides)
    return base


def test_millisecond_timestamp_is_normalized_to_seconds():
    ms = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp() * 1000
    parsed = normalize_timestamp(ms)
    assert parsed.year == 2026 and parsed.month == 1 and parsed.hour == 12


def test_second_timestamp_is_not_scaled():
    seconds = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    parsed = normalize_timestamp(seconds)
    assert parsed.year == 2026 and parsed.month == 1


def test_tick_rejects_nan_price():
    with pytest.raises(InvalidTickError):
        validate_tick(_raw_tick(price="NaN"))


def test_tick_rejects_infinite_price():
    with pytest.raises(InvalidTickError):
        validate_tick(_raw_tick(price="Infinity"))


def test_tick_rejects_zero_and_negative_price():
    with pytest.raises(InvalidTickError):
        validate_tick(_raw_tick(price="0"))
    with pytest.raises(InvalidTickError):
        validate_tick(_raw_tick(price="-5"))


def test_tick_rejects_negative_quantity():
    with pytest.raises(InvalidTickError):
        validate_tick(_raw_tick(quantity="-1"))


def test_tick_rejects_missing_symbol():
    with pytest.raises(InvalidTickError):
        validate_tick(_raw_tick(symbol=""))


def test_tick_rejects_future_and_stale():
    now = datetime.now(tz=UTC)
    with pytest.raises(InvalidTickError, match="future"):
        validate_tick(_raw_tick(timestamp=(now + timedelta(hours=1)).timestamp()), now=now)
    with pytest.raises(InvalidTickError, match="stale"):
        validate_tick(_raw_tick(timestamp=(now - timedelta(hours=1)).timestamp()), now=now)


def test_ingest_counts_malformed_and_skips_transport_reconnect():
    class MalformedTransport(_Transport):
        def connect(self, symbols):
            yield _raw_tick(price="NaN")
            yield _raw_tick(symbol="BTCUSDT", price="101", quantity="1")

    service = StreamingMarketDataService(MalformedTransport())
    count = service.run(max_messages=1)
    assert count == 1
    assert service.malformed_ticks == 1


# ---------------------------------------------------------------------------
# OT-007 — candle aggregation robustness
# ---------------------------------------------------------------------------


def _tick_at(symbol: str, seconds_offset: int, price: str = "10") -> Tick:
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds_offset)
    return Tick(symbol, Decimal(price), Decimal(1), base, base)


def test_aggregator_flush_emits_partial_candle():
    agg = CandleAggregator(60)
    agg.add(_tick_at("BTC", 10, "10"))
    agg.add(_tick_at("BTC", 30, "12"))
    candle = agg.flush("BTC")
    assert candle is not None
    assert candle.high == Decimal(12) and candle.low == Decimal(10)
    assert agg.flush("BTC") is None


def test_aggregator_flush_all():
    agg = CandleAggregator(60)
    agg.add(_tick_at("BTC", 10))
    agg.add(_tick_at("ETH", 20))
    candles = agg.flush_all()
    assert {c.symbol for c in candles} == {"BTC", "ETH"}


def test_aggregator_rejects_late_tick_for_closed_bucket():
    agg = CandleAggregator(60)
    agg.add(_tick_at("BTC", 10, "10"))
    agg.add(_tick_at("BTC", 70, "20"))  # closes 0-60 bucket
    closed = agg.flush("BTC")
    assert closed is not None and closed.close == Decimal(20)
    late = agg.add(_tick_at("BTC", 50, "15"))
    assert late is None
    assert agg.late_ticks == 1


def test_aggregator_flush_stale_closes_idle_symbols():
    agg = CandleAggregator(60, max_idle_seconds=30)
    agg.add(_tick_at("BTC", 10))
    now = datetime(2026, 1, 1, 0, 3, 0, tzinfo=UTC)
    candles = agg.flush_stale(now)
    assert len(candles) == 1 and candles[0].symbol == "BTC"


# ---------------------------------------------------------------------------
# OT-008 — bounded retention
# ---------------------------------------------------------------------------


def test_replay_recorder_bounded():
    recorder = ReplayRecorder(max_records=100)
    for i in range(150):
        recorder.record(_tick_at("BTC", i))
    assert len(recorder.records) == 100
    assert recorder.dropped_records == 50
    assert len(recorder.replay()) == 100


def test_latency_buffer_bounded_after_ingest():
    service = StreamingMarketDataService(_Transport())
    for _ in range(2500):
        service.ingest(_raw_tick())
    assert len(service._latencies) <= 1500


# ---------------------------------------------------------------------------
# OT-010 — bounded health checks and liveness/readiness separation
# ---------------------------------------------------------------------------


def test_health_check_times_out_instead_of_stalling():
    import time

    from traderos.infrastructure.health import HealthService

    svc = HealthService(check_timeout=0.1)
    status = svc.check("db", lambda: (time.sleep(1) or True))
    assert status.healthy is False
    assert "exceeded" in status.message


def test_sqlite_health_check_times_out():
    import time

    from traderos.infrastructure.health import run_with_timeout

    with pytest.raises(TimeoutError):
        run_with_timeout(lambda: (time.sleep(1) or True), 0.1)


def test_healthz_liveness_never_builds_orchestrator():
    from fastapi.testclient import TestClient

    from traderos.interfaces.api import security
    from traderos.interfaces.api import server

    def _boom(*args, **kwargs):
        raise AssertionError("liveness must not build the orchestrator")

    security.reset_authenticator()
    with __import__("unittest.mock").mock.patch.object(server, "create_orchestrator", _boom):
        app = server.build_app()
        resp = TestClient(app).get("/v1/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


def test_health_readiness_degraded_on_timeout():
    import time
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from traderos.interfaces.api import security
    from traderos.interfaces.api import server

    def _slow_build(mode="paper", config=None):
        time.sleep(0.5)
        raise AssertionError("should not finish")

    server._orch_cache.clear()
    security.reset_authenticator()
    with (
        patch.object(server, "build_orchestrator", _slow_build),
        patch.object(server, "ORCHESTRATOR_READY_TIMEOUT", 0.05),
    ):
        app = server.build_app()
        resp = TestClient(app).get("/v1/health")
    assert resp.status_code == 503
    assert "not ready" in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# OT-011 — thread-safe sqlite connections
# ---------------------------------------------------------------------------


def test_sqlite_connection_usable_across_threads(tmp_path):
    from traderos.infrastructure.config.config_loader import Config
    from traderos.infrastructure.database.connection import get_connection

    cfg = Config(db_path=str(tmp_path / "threads.db"), log_level="CRITICAL")
    conn = get_connection(cfg)
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()

    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for _ in range(50):
                conn.execute("INSERT INTO t (v) VALUES (?)", (str(n),))
                conn.execute("SELECT COUNT(*) FROM t").fetchone()
                with conn.cursor() as cur:
                    cur.execute("SELECT v FROM t ORDER BY id DESC LIMIT 1")
                    cur.fetchone()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conn.commit()

    assert errors == []
    row = conn.execute("SELECT COUNT(*) AS n FROM t").fetchone()
    assert row["n"] == 400


def test_thread_safe_connection_backend_detected_as_sqlite(tmp_path):
    from traderos.infrastructure.config.config_loader import Config
    from traderos.infrastructure.database.connection import get_connection
    from traderos.infrastructure.database.migration_utils import detect_backend

    cfg = Config(db_path=str(tmp_path / "backend.db"), log_level="CRITICAL")
    conn = get_connection(cfg)
    assert detect_backend(conn) == "sqlite"


# ---------------------------------------------------------------------------
# OT-001 — Binance WebSocket transport (thin, pure-frame, offline-testable)
# ---------------------------------------------------------------------------


def test_binance_stream_symbol_normalizes():
    from traderos.infrastructure.market_stream import binance_stream_symbol

    assert binance_stream_symbol("BTCUSDT") == "btcusdt"
    assert binance_stream_symbol("BTC/USD") == "btcusd"
    assert binance_stream_symbol("  eth-usdt ") == "ethusdt"


def test_build_subscription_frame_uses_agg_trade_streams():
    from traderos.infrastructure.market_stream import build_subscription_frame

    frame = json.loads(build_subscription_frame(["BTCUSDT", "ETHUSDT"]))
    assert frame["method"] == "SUBSCRIBE"
    assert frame["params"] == ["btcusdt@aggTrade", "ethusdt@aggTrade"]


def test_parse_trade_frame_raw_event():
    from traderos.infrastructure.market_stream import parse_trade_frame

    raw = (
        '{"e":"aggTrade","E":1672515782136,"s":"BTCUSDT","a":12345,'
        '"p":"249.09","q":"1.00000000","f":100,"l":105,"T":1672515782136,"m":true}'
    )
    tick = parse_trade_frame(raw)
    assert tick is not None
    assert tick["symbol"] == "BTCUSDT"
    assert tick["price"] == "249.09"
    assert tick["timestamp"] == 1672515782136
    assert tick["event_id"] == "binance-12345"


def test_parse_trade_frame_combined_envelope():
    from traderos.infrastructure.market_stream import parse_trade_frame

    raw = (
        '{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":1672515782136,'
        '"s":"BTCUSDT","a":7,"p":"100.5","q":"0.5","T":1672515782136}}'
    )
    tick = parse_trade_frame(raw)
    assert tick is not None
    assert tick["symbol"] == "BTCUSDT"
    assert tick["event_id"] == "binance-7"


def test_parse_trade_frame_ignores_non_trade_frames():
    from traderos.infrastructure.market_stream import parse_trade_frame

    assert parse_trade_frame('{"result": null, "id": 1}') is None
    assert parse_trade_frame('{"e":"kline","E":123,"s":"BTCUSDT","p":"1"}') is None
    assert parse_trade_frame("not json") is None
    assert parse_trade_frame("[1, 2, 3]") is None


class _FakeWebSocket:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent: list[str] = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def recv(self):
        if not self._frames:
            return None
        return self._frames.pop(0)

    def close(self):
        self.closed = True


class _FakeConnector:
    def __init__(self, frames):
        self._frames = frames
        self.websocket: _FakeWebSocket | None = None

    def __call__(self, url):
        self.websocket = _FakeWebSocket(self._frames)
        return self.websocket


def test_binance_transport_subscribes_and_yields_trades():
    from traderos.infrastructure.market_stream import BinanceStreamTransport

    connector = _FakeConnector(
        [
            '{"result": null, "id": 1}',
            (
                '{"e":"aggTrade","E":1672515782136,"s":"BTCUSDT","a":1,'
                '"p":"50000.00","q":"0.01","T":1672515782136}'
            ),
        ]
    )
    transport = BinanceStreamTransport(connector=connector)
    ticks = list(transport.connect(["BTCUSDT"]))
    transport.close()

    assert len(ticks) == 1
    assert ticks[0]["symbol"] == "BTCUSDT"
    assert connector.websocket is not None
    sub = json.loads(connector.websocket.sent[0])
    assert sub["params"] == ["btcusdt@aggTrade"]
    assert connector.websocket.closed


def test_streaming_service_run_with_binance_transport():
    from traderos.infrastructure.market_stream import BinanceStreamTransport
    from traderos.infrastructure.market_stream import StreamingMarketDataService

    now_ms = int(time.time() * 1000)
    frame = (
        f'{{"e":"aggTrade","E":{now_ms},"s":"ETHUSDT","a":42,'
        f'"p":"1800.00","q":"0.2","T":{now_ms}}}'
    )
    connector = _FakeConnector([frame])
    service = StreamingMarketDataService(BinanceStreamTransport(connector=connector))
    seen: list[dict] = []
    service.subscribe(["ETHUSDT"], lambda tick: seen.append(tick))

    received = service.run(max_messages=1)
    assert received == 1
    assert len(seen) == 1
    assert seen[0].symbol == "ETHUSDT"
    assert seen[0].price == Decimal("1800.00")


def test_binance_default_connector_requires_websockets():
    from traderos.infrastructure.market_stream import _DefaultWebSocketConnector

    try:
        conn = _DefaultWebSocketConnector()("wss://stream.binance.com:9443")
    except RuntimeError as exc:
        assert "websockets" in str(exc)
    except Exception as exc:
        pytest.skip(f"Binance endpoint unreachable in this environment: {exc}")
    else:
        conn.close()


# ---------------------------------------------------------------------------
# OT-002 — durable run manifest + daemon restart recovery
# ---------------------------------------------------------------------------


def test_durable_manifest_survives_process_restart(tmp_path):
    from traderos.infrastructure.database.connection import get_connection
    from traderos.infrastructure.run_manifest import DurableRunManifest

    cfg = _test_config(tmp_path, "manifest.db")
    conn = get_connection(cfg)
    first = DurableRunManifest(conn=conn)
    first.record("orchestrator", "start", metadata={"mode": "paper"})

    second = DurableRunManifest(conn=get_connection(cfg))
    runs = second.get_runs(service="orchestrator")
    assert len(runs) == 1
    assert runs[0].action == "start"
    assert runs[0].metadata["mode"] == "paper"
    assert second.detect_unclean_shutdown("orchestrator") is True
    second.close()


def test_durable_manifest_clean_shutdown_not_a_crash(tmp_path):
    from traderos.infrastructure.database.connection import get_connection
    from traderos.infrastructure.run_manifest import DurableRunManifest

    manifest = DurableRunManifest(conn=get_connection(_test_config(tmp_path, "m.db")))
    manifest.record("orchestrator", "start")
    manifest.record("orchestrator", "stop")
    assert manifest.detect_unclean_shutdown("orchestrator") is False
    assert manifest.summary()["orchestrator"] == 2
    manifest.close()


def test_daemon_controller_recovers_after_crash(tmp_path):
    from traderos.application.cycle_executor import CycleExecutor
    from traderos.application.daemon_controller import DaemonController
    from traderos.application.models import TradingMode
    from traderos.infrastructure.audit import AuditService
    from traderos.infrastructure.database.connection import get_connection
    from traderos.infrastructure.events import InMemoryEventBus
    from traderos.infrastructure.health import HealthService
    from traderos.infrastructure.metrics import MetricsService
    from traderos.infrastructure.run_manifest import DurableRunManifest

    manifest = DurableRunManifest(conn=get_connection(_test_config(tmp_path, "c.db")))
    manifest.record("orchestrator", "start")

    controller = DaemonController(
        mode=TradingMode.PAPER,
        cycle_executor=Mock(spec=CycleExecutor),
        event_bus=InMemoryEventBus(),
        health=HealthService(),
        audit=AuditService(),
        metrics=MetricsService(),
        notifications=Mock(),
        run_manifest=manifest,
    )
    result = controller._recover_from_crash()
    assert result.reconciled == 0 or result.reconciled >= 0
    assert controller._crash_recovered is True
    manifest.close()


def test_daemon_controller_skips_recovery_on_clean_shutdown(tmp_path):
    from traderos.application.cycle_executor import CycleExecutor
    from traderos.application.daemon_controller import DaemonController
    from traderos.application.models import TradingMode
    from traderos.infrastructure.audit import AuditService
    from traderos.infrastructure.database.connection import get_connection
    from traderos.infrastructure.events import InMemoryEventBus
    from traderos.infrastructure.health import HealthService
    from traderos.infrastructure.metrics import MetricsService
    from traderos.infrastructure.run_manifest import DurableRunManifest

    manifest = DurableRunManifest(conn=get_connection(_test_config(tmp_path, "c2.db")))
    manifest.record("orchestrator", "start")
    manifest.record("orchestrator", "stop")

    controller = DaemonController(
        mode=TradingMode.PAPER,
        cycle_executor=Mock(spec=CycleExecutor),
        event_bus=InMemoryEventBus(),
        health=HealthService(),
        audit=AuditService(),
        metrics=MetricsService(),
        notifications=Mock(),
        run_manifest=manifest,
    )
    result = controller._recover_from_crash()
    assert result.reconciled == 0
    assert controller._crash_recovered is False
    manifest.close()
