from __future__ import annotations

import sqlite3
from uuid import uuid4

from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.journal import OrderEventJournal
from traderos.infrastructure.journaled_broker import JournaledBroker
from traderos.infrastructure.journaled_broker import _client_key


class FakeBroker:
    def __init__(self) -> None:
        self.calls = 0
        self.last_result = FillResult(True, 2.0, 100.0, 0.0, "filled", "ext-1")

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        self.calls += 1
        return self.last_result

    def get_account_balance(self):
        return 100.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def place_limit_order(self, *a, **k):
        return self.last_result

    def place_stop_order(self, *a, **k):
        return self.last_result

    def place_trailing_stop_order(self, *a, **k):
        return self.last_result

    def modify_order(self, *a, **k):
        return self.last_result

    def cancel_order(self, order_id):
        return FillResult(False, 0.0, 0.0, 1.0, "cancelled", order_id)


def _make() -> tuple[sqlite3.Connection, FakeBroker, JournaledBroker]:
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    broker = FakeBroker()
    return conn, broker, JournaledBroker(broker, journal)


def test_forwards_result_into_journal():
    conn, broker, jb = _make()
    mid = uuid4()
    res = jb.place_market_order(mid, "buy", 2.0, close_price=100.0)
    assert res == broker.last_result
    assert jb.pending() == []
    conn.close()


def test_duplicate_submit_does_not_resubmit_broker():
    conn, broker, jb = _make()
    mid = uuid4()
    jb.place_market_order(mid, "buy", 2.0)
    assert broker.calls == 1
    res2 = jb.place_market_order(mid, "buy", 2.0)
    assert broker.calls == 1  # short-circuited, no second broker call
    assert res2.status == "filled"  # replayed stored outcome
    conn.close()


def test_restart_replays_without_broker_call():
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    mid = uuid4()

    crashed = FakeBroker()
    JournaledBroker(crashed, journal).place_market_order(mid, "buy", 2.0)
    assert crashed.calls == 1

    fresh = FakeBroker()
    jb_restart = JournaledBroker(fresh, journal)  # same durable journal
    res = jb_restart.place_market_order(mid, "buy", 2.0)
    assert fresh.calls == 0  # broker never contacted again
    assert res.order_id == "ext-1"
    conn.close()


def test_intent_only_surfaces_as_pending_for_reconcile():
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    mid = uuid4()
    broker = FakeBroker()
    jb = JournaledBroker(broker, journal)

    key = _client_key(mid, "buy", 2.0, "place_market_order")
    journal.record(key, key, "intent", {"method": "place_market_order"})

    res = jb.place_market_order(mid, "buy", 2.0)
    assert res.status == "needs_reconcile"
    assert broker.calls == 0  # must not double-submit
    assert len(jb.pending()) == 1
    conn.close()


def test_disabled_is_passthrough():
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    broker = FakeBroker()
    jb = JournaledBroker(broker, journal, disable=True)
    mid = uuid4()
    jb.place_market_order(mid, "buy", 2.0)
    assert broker.calls == 1
    conn.close()


def test_readonly_and_cancel_pass_through():
    conn, _broker, jb = _make()
    assert jb.get_account_balance() == 100.0
    assert jb.get_positions() == []
    assert jb.get_open_orders() == []
    res = jb.cancel_order("o1")
    assert res.status == "cancelled"
    conn.close()
