"""G-02 live order ops drill (bounded, deterministic soak).

Proves, through the REAL production submission code (AlpacaBrokerAdapter's
idempotent retry + JournaledBroker's durable journal), that ack-loss and
restart cannot create duplicate or lost orders, and that an unconfirmed intent
is surfaced fail-closed instead of silently dropped.

The FlakyAlpacaClient stands in for Alpaca: it accepts an order, then drops the
acknowledgement (raises) exactly like a lost TCP response, and dedupes repeat
submissions by ``client_order_id`` exactly like Alpaca does intra-day.
"""

from __future__ import annotations

import sqlite3
import types
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock

import pytest

import traderos.infrastructure.retry as _retry_module
from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.entities.signal import Signal
from traderos.domain.entities.signal import SignalDirection
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.broker_state_reconciliation_service import MismatchType
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import RiskAssessment
from traderos.domain.services.risk_service import TradeVerdict
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.alpaca_broker import AlpacaBrokerAdapter
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.journal import OrderEventJournal
from traderos.infrastructure.journaled_broker import JournaledBroker
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.repositories.sqlite.trades import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite.trades import SQLiteTradeRepository


class _Order:
    def __init__(self, symbol, qty, side, client_order_id) -> None:
        self.id = f"ord-{client_order_id[:8]}"
        self.symbol = symbol
        self.qty = qty
        self.side = side
        self.type = "market"
        self.client_order_id = client_order_id
        self.filled_qty = qty
        self.filled_avg_price = 100.0


class FlakyAlpacaClient:
    """Accepts orders, drops acks, dedupes by client_order_id (like Alpaca)."""

    def __init__(self) -> None:
        self.orders: dict[str, _Order] = {}
        self.submit_calls = 0
        self.dropped_cids: set[str] = set()
        self._drop_on_new = 0
        self._drop_every = 0

    def arm_ack_drop_every(self, n: int) -> None:
        self._drop_every = n

    def submit_order(self, order_data):
        self.submit_calls += 1
        cid = order_data.client_order_id
        existing = self.orders.get(cid)
        if existing is not None:
            return existing
        order = _Order(
            symbol=order_data.symbol,
            qty=order_data.qty,
            side=order_data.side,
            client_order_id=cid,
        )
        self.orders[cid] = order
        if self._drop_every and len(self.orders) % self._drop_every == 0:
            self.dropped_cids.add(cid)
            raise TimeoutError("ack lost after order accepted")
        return order

    def get_account(self):
        return types.SimpleNamespace(equity=10000.0)

    def get_all_positions(self):
        net: dict[str, float] = {}
        for o in self.orders.values():
            delta = o.qty if o.side == "buy" else -o.qty
            net[o.symbol] = net.get(o.symbol, 0.0) + delta
        return [
            types.SimpleNamespace(symbol=s, qty=q, market_value=q * 100.0)
            for s, q in net.items()
            if abs(q) > 1e-9
        ]

    def get_orders(self, _request):
        return []

    def replace_order_by_id(self, order_id, order_data=None):
        return None

    def cancel_order_by_id(self, order_id):
        return None


class _SoakStrat(StrategyBase):
    name = "soak_strat"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "soak"})


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return conn


def _register():
    strategy_registry._strategies["soak_strat"] = _SoakStrat


def _unregister():
    strategy_registry._strategies.pop("soak_strat", None)


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(_retry_module.time, "sleep", lambda s: None)


def _prov(confidence=0.8):
    now = datetime.now(UTC)
    return SignalProvenance(
        signal=Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=confidence,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        ),
        strategy_name="soak",
        indicators_used={},
    )


class TestSoakDisconnectDrill:
    def test_ack_loss_is_masked_by_idempotent_retry_zero_duplicates(self) -> None:
        flaky = FlakyAlpacaClient()
        flaky._drop_every = 1  # drop the ack on every new order
        adapter = AlpacaBrokerAdapter(api_key="x", secret_key="x", paper=True, client=flaky)

        mid = uuid.uuid4()
        result = adapter.place_market_order(mid, "buy", 1.0, close_price=100.0)

        assert result.filled is True
        assert result.order_id
        assert flaky.submit_calls >= 2, "a retry must have happened after the dropped ack"
        assert len(flaky.orders) == 1, "retry must NOT create a duplicate order"
        assert len(flaky.dropped_cids) == 1

    def test_journal_restart_replays_without_resubmission(self) -> None:
        conn = _make_conn()
        flaky = FlakyAlpacaClient()
        adapter = AlpacaBrokerAdapter(api_key="x", secret_key="x", paper=True, client=flaky)
        journal = OrderEventJournal(conn)

        broker_v1 = JournaledBroker(adapter, journal)
        mid = uuid.uuid4()
        first = broker_v1.place_market_order(mid, "buy", 2.0, close_price=100.0)
        assert first.filled and first.order_id
        calls_before = flaky.submit_calls
        orders_before = len(flaky.orders)

        broker_v2 = JournaledBroker(adapter, journal)
        second = broker_v2.place_market_order(mid, "buy", 2.0, close_price=100.0)

        assert second.filled and second.order_id == first.order_id
        assert flaky.submit_calls == calls_before, "restart must NOT re-submit to the broker"
        assert len(flaky.orders) == orders_before, "no duplicate broker orders after restart"
        conn.close()

    def test_unconfirmed_intent_is_surfaced_fail_closed(self) -> None:
        conn = _make_conn()
        journal = OrderEventJournal(conn)
        key = str(uuid.uuid5(uuid.NAMESPACE_DNS, "soak:intent"))
        journal.record(key, key, "intent", {"method": "place_market_order", "quantity": 1.0})
        journaled = JournaledBroker(Mock(), journal)

        adapter = AlpacaBrokerAdapter(
            api_key="x", secret_key="x", paper=True, client=FlakyAlpacaClient()
        )
        reconciliation = BrokerStateReconciliationService(broker=adapter)
        result = reconciliation.reconcile(journal_pending=journaled.pending())

        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.UNCONFIRMED_INTENT for m in result.mismatches)
        assert reconciliation.can_accept_orders is False, "must fail closed, not silently drop"
        conn.close()

    def test_soak_cycles_through_real_submission_path_no_duplicates_or_loss(self) -> None:
        conn = _make_conn()
        _register()
        try:
            flaky = FlakyAlpacaClient()
            flaky.arm_ack_drop_every(3)
            adapter = AlpacaBrokerAdapter(api_key="x", secret_key="x", paper=True, client=flaky)
            journal = OrderEventJournal(conn)
            broker = JournaledBroker(adapter, journal)

            audit = SQLiteAuditService(conn)
            portfolio = PortfolioService(
                trade_repo=SQLiteTradeRepository(conn),
                position_repo=SQLitePositionRepository(conn),
                audit=audit,
            )
            signal_service = Mock()
            signal_service.process_evaluation.return_value = _prov()
            risk = Mock()
            risk.can_trade.return_value = TradeVerdict(True, "")
            risk.kill_switch = KillSwitch()
            risk.assess_trade.return_value = RiskAssessment(
                kelly_fraction=0.5,
                suggested_stop_loss=99.0,
                suggested_take_profit=102.0,
                risk_per_unit=1.0,
                max_risk_amount=200.0,
            )
            risk.authorize_order.return_value = Mock(allowed=True, reason="")

            def build_executor():
                return CycleExecutor(
                    mode=TradingMode.PAPER,
                    signal_service=signal_service,
                    risk_service=risk,
                    portfolio_service=portfolio,
                    execution=Mock(),
                    analysis=AnalysisService(),
                    broker=broker,
                    event_bus=InMemoryEventBus(),
                    health=SQLiteHealthService(conn),
                    audit=audit,
                    metrics=SQLiteMetricsService(conn),
                    notifications=Mock(),
                    run_manifest=SQLiteManifestService(conn),
                    enabled_strategies=lambda: [("soak", "soak_strat", {})],
                )

            executor = build_executor()
            mid = uuid.uuid4()
            for i in range(12):
                signal_service.process_evaluation.return_value = _prov()
                executor.run(mid, 100.0 + i)

            broker_orders = len(flaky.orders)
            confirmed = journal.count()
            trades = len(portfolio.trade_repo.list())
            pending = len(journal.pending_events())

            assert pending == 0, "every accepted order must be journal-confirmed"
            assert broker_orders == confirmed == trades, (
                f"order/book/journal/trade divergence: broker={broker_orders} "
                f"confirmed={confirmed} trades={trades}"
            )
            assert len(flaky.dropped_cids) >= 2, "the soak must have forced disconnects"

            pre_restart_cids = set(flaky.orders)
            executor = build_executor()
            signal_service.process_evaluation.return_value = _prov()
            executor.run(mid, 200.0)

            assert (
                len(flaky.orders) == broker_orders + 1
            ), "restart must add exactly one NEW order, never re-submit old ones"
            assert pre_restart_cids.issubset(set(flaky.orders)), "old orders must survive"
            assert (
                journal.count() == len(flaky.orders) == len(portfolio.trade_repo.list())
            ), "invariant broken after restart"

            reconciliation = BrokerStateReconciliationService(broker=adapter)
            local_positions = [
                {
                    "market_id": str(p.market_id),
                    "qty": p.quantity,
                    "current_price": p.current_price,
                    "entry_price": p.entry_price,
                }
                for p in SQLitePositionRepository(conn).list_open()
            ]
            result = reconciliation.reconcile(
                local_positions=local_positions,
                local_orders=[],
                journal_pending=broker.pending(),
            )
            assert result.errors == []
            assert not result.has_mismatches, f"unexpected mismatches: {result.mismatches}"
        finally:
            _unregister()
        conn.close()
