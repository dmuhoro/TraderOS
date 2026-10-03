from __future__ import annotations

import sqlite3
from uuid import uuid4

from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.journal import OrderEventJournal
from traderos.infrastructure.journaled_broker import JournaledBroker


class FakeBroker:
    def __init__(self) -> None:
        self.calls = 0
        self.last_result = FillResult(True, 2.0, 100.0, 0.0, "filled", "ext-1")

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        self.calls += 1
        return self.last_result

    def place_flatten_order(
        self, market_id, side, quantity, close_price=None, client_order_id=None
    ):
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
    """Exactly-once is keyed on INTENT, not on request shape (B-2).

    This test used to submit the same (market, side, qty, method) twice with
    no id and assert one broker call -- which is precisely the defect: two
    genuinely distinct orders that share a shape were collapsed into one.
    Exactly-once is now expressed the only way it can be honoured: the same
    client_order_id presented twice.
    """
    conn, broker, jb = _make()
    mid = uuid4()
    cid = "intent-1"
    jb.place_market_order(mid, "buy", 2.0, client_order_id=cid)
    assert broker.calls == 1
    res2 = jb.place_market_order(mid, "buy", 2.0, client_order_id=cid)
    assert broker.calls == 1  # same intent -> short-circuited, no second call
    assert res2.status == "filled"  # replayed stored outcome
    conn.close()


def test_distinct_intents_with_identical_shape_both_submit():
    """B-2 regression: same shape, different intent -> two broker calls."""
    conn, broker, jb = _make()
    mid = uuid4()
    jb.place_market_order(mid, "buy", 2.0, client_order_id="intent-a")
    jb.place_market_order(mid, "buy", 2.0, client_order_id="intent-b")
    assert broker.calls == 2
    conn.close()


def test_restart_replays_without_broker_call():
    """A retry of the SAME intent across a restart must replay (B-2).

    The caller carries the stable client_order_id through the restart; that
    is what makes this replayable rather than a blind resubmit.
    """
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    mid = uuid4()
    cid = "intent-restart"

    crashed = FakeBroker()
    JournaledBroker(crashed, journal).place_market_order(mid, "buy", 2.0, client_order_id=cid)
    assert crashed.calls == 1

    fresh = FakeBroker()
    jb_restart = JournaledBroker(fresh, journal)  # same durable journal
    res = jb_restart.place_market_order(mid, "buy", 2.0, client_order_id=cid)
    assert fresh.calls == 0  # broker never contacted again
    assert res.order_id == "ext-1"
    conn.close()


def test_intent_only_surfaces_as_pending_for_reconcile():
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    mid = uuid4()
    broker = FakeBroker()
    jb = JournaledBroker(broker, journal)

    key = "intent-orphan"
    journal.record(key, key, "intent", {"method": "place_market_order"})

    res = jb.place_market_order(mid, "buy", 2.0, client_order_id=key)
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


def test_pending_empty_without_journal():
    _, broker, _ = _make()
    jb = JournaledBroker(broker, None)
    assert jb.pending() == []


def test_limit_stop_trailing_and_modify_submit_through_journal():
    conn, _broker, jb = _make()
    mid = uuid4()
    assert jb.place_limit_order(mid, "buy", 2.0, 99.0).order_id == "ext-1"
    assert jb.place_stop_order(mid, "buy", 2.0, 95.0).order_id == "ext-1"
    assert jb.place_trailing_stop_order(mid, "buy", 2.0, 0.01).order_id == "ext-1"
    assert jb.modify_order("ord-1", qty=3.0).order_id == "ext-1"
    conn.close()


def test_flatten_order_journaled_and_idempotent():
    conn, broker, jb = _make()
    mid = uuid4()
    res = jb.place_flatten_order(mid, "sell", 2.0, close_price=100.0, client_order_id="f-1")
    assert res == broker.last_result
    assert broker.calls == 1
    assert jb.pending() == []  # intent confirmed, no drift

    # A retry of the SAME flatten intent replays and never re-submits
    # (exactly-once even across the seam).
    res2 = jb.place_flatten_order(mid, "sell", 2.0, close_price=100.0, client_order_id="f-1")
    assert broker.calls == 1
    assert res2.order_id == "ext-1"
    conn.close()


def test_second_distinct_flatten_submits():
    """B-2 regression: an emergency close must never be silently dropped.

    Two flattens of the same size used to collapse onto one derived key, so
    the second returned the FIRST order's id while the broker was never
    called -- a kill-switch exit that reported success and left the position
    open.
    """
    conn, broker, jb = _make()
    mid = uuid4()
    jb.place_flatten_order(mid, "sell", 2.0, close_price=100.0, client_order_id="f-1")
    jb.place_flatten_order(mid, "sell", 2.0, close_price=100.0, client_order_id="f-2")
    assert broker.calls == 2
    conn.close()
