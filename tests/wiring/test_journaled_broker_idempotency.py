"""B-2: the derived idempotency key conflated "same request shape" with
"same intent".

``JournaledBroker._client_key`` derived the key from
``(market_id, side, quantity, method)``. Two genuinely distinct orders that
happened to share those four values therefore collided, and the second was
silently REPLAYED instead of submitted.

This is worst on ``place_flatten_order``, which accepted no
``client_order_id`` at all: every flatten used the derived key, so a second
emergency close of the same size was dropped and the caller was handed the
FIRST order's id -- a kill-switch exit that reported success while the
position stayed open.

The contract these tests pin:
  * a genuine second intent  -> submits
  * a true retry of one intent -> replays, never double-submits
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.journal import OrderEventJournal
from traderos.infrastructure.journaled_broker import JournaledBroker


class _CountingBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.submits: list[str] = []

    def _fill(self, tag: str) -> FillResult:
        self.submits.append(tag)
        return FillResult(
            filled=True,
            fill_quantity=1.0,
            fill_price=100.0,
            remaining=0.0,
            status="filled",
            order_id=f"ext-{tag}",
        )

    def place_market_order(
        self, market_id, side, quantity, close_price=None, client_order_id=None
    ) -> FillResult:
        return self._fill(str(len(self.submits) + 1))

    def place_flatten_order(
        self, market_id, side, quantity, close_price=None, client_order_id=None
    ) -> FillResult:
        return self._fill(str(len(self.submits) + 1))

    def cancel_order(self, order_id: str) -> FillResult:
        return FillResult(False, 0.0, 0.0, 0.0, "cancelled")

    def place_limit_order(self, *a, **k) -> FillResult:
        return self._fill(str(len(self.submits) + 1))

    def place_stop_order(self, *a, **k) -> FillResult:
        return self._fill(str(len(self.submits) + 1))

    def place_trailing_stop_order(self, *a, **k) -> FillResult:
        return self._fill(str(len(self.submits) + 1))

    def modify_order(self, order_id, **k) -> FillResult:
        return FillResult(False, 0.0, 0.0, 0.0, "unchanged")

    def get_account_balance(self) -> float:
        return 10_000.0

    def get_positions(self) -> list[dict]:
        return []

    def get_open_orders(self) -> list[dict]:
        return []


def _build() -> tuple[sqlite3.Connection, _CountingBroker, JournaledBroker]:
    conn = sqlite3.connect(":memory:")
    journal = OrderEventJournal(conn)
    inner = _CountingBroker()
    return conn, inner, JournaledBroker(inner, journal)


class TestB2DistinctIntentSubmits:
    """A second, genuinely distinct intent must reach the broker."""

    def test_second_flatten_of_same_size_submits(self) -> None:
        conn, inner, jb = _build()
        mid = uuid.uuid4()
        r1 = jb.place_flatten_order(mid, "sell", 4.0, close_price=100.0)
        r2 = jb.place_flatten_order(mid, "sell", 4.0, close_price=100.0)

        assert len(inner.submits) == 2, (
            f"two distinct flatten intents must both submit; broker saw " f"{len(inner.submits)}"
        )
        assert (
            r1.order_id != r2.order_id
        ), f"replayed the first order's id: {r1.order_id!r} == {r2.order_id!r}"
        conn.close()

    def test_second_market_order_same_shape_submits(self) -> None:
        conn, inner, jb = _build()
        mid = uuid.uuid4()
        a = uuid.uuid4()
        b = uuid.uuid4()
        jb.place_market_order(mid, "buy", 2.0, close_price=100.0, client_order_id=a)
        jb.place_market_order(mid, "buy", 2.0, close_price=100.0, client_order_id=b)
        assert len(inner.submits) == 2, "distinct client_order_ids must both submit"
        conn.close()

    def test_flatten_across_a_restart_still_submits_a_new_intent(self) -> None:
        """A restart must not turn the next real flatten into a replay."""
        conn = sqlite3.connect(":memory:")
        journal = OrderEventJournal(conn)
        mid = uuid.uuid4()

        first = _CountingBroker()
        JournaledBroker(first, journal).place_flatten_order(
            mid, "sell", 4.0, close_price=100.0, client_order_id="intent-1"
        )
        assert len(first.submits) == 1

        # Simulated restart against the same durable journal.
        after = _CountingBroker()
        jb = JournaledBroker(after, journal)
        jb.place_flatten_order(mid, "sell", 4.0, close_price=100.0, client_order_id="intent-2")

        assert len(after.submits) == 1, "a new intent after restart must submit, not replay"
        conn.close()


class TestB2TrueRetryStillReplays:
    """The exactly-once guarantee must survive the fix. A retry of ONE
    intent must never double-submit -- that is what the journal is for."""

    def test_same_client_order_id_replays(self) -> None:
        conn, inner, jb = _build()
        mid = uuid.uuid4()
        cid = "intent-abc"
        r1 = jb.place_market_order(mid, "buy", 2.0, close_price=100.0, client_order_id=cid)
        r2 = jb.place_market_order(mid, "buy", 2.0, close_price=100.0, client_order_id=cid)

        assert len(inner.submits) == 1, "a true retry must not double-submit"
        assert r2.order_id == r1.order_id
        conn.close()

    def test_same_client_order_id_replays_flatten(self) -> None:
        conn, inner, jb = _build()
        mid = uuid.uuid4()
        cid = "flatten-abc"
        r1 = jb.place_flatten_order(mid, "sell", 4.0, close_price=100.0, client_order_id=cid)
        r2 = jb.place_flatten_order(mid, "sell", 4.0, close_price=100.0, client_order_id=cid)

        assert len(inner.submits) == 1, "a true retry must not double-submit"
        assert r2.order_id == r1.order_id
        conn.close()

    def test_retry_after_restart_replays(self) -> None:
        conn = sqlite3.connect(":memory:")
        journal = OrderEventJournal(conn)
        mid = uuid.uuid4()
        cid = "intent-restart"

        before = _CountingBroker()
        JournaledBroker(before, journal).place_market_order(
            mid, "buy", 2.0, close_price=100.0, client_order_id=cid
        )
        assert len(before.submits) == 1

        after = _CountingBroker()
        r = JournaledBroker(after, journal).place_market_order(
            mid, "buy", 2.0, close_price=100.0, client_order_id=cid
        )
        assert len(after.submits) == 0, "a retry across restart must replay, not resubmit"
        assert r.filled is True
        conn.close()

    def test_intent_only_surfaces_as_pending_not_resubmitted(self) -> None:
        """An intent recorded before a crash must NOT be blindly resubmitted;
        it must surface for reconciliation."""
        conn = sqlite3.connect(":memory:")
        journal = OrderEventJournal(conn)
        inner = _CountingBroker()
        jb = JournaledBroker(inner, journal)
        mid = uuid.uuid4()
        cid = "intent-orphan"

        journal.record(cid, cid, "intent", {"method": "place_market_order"})
        res = jb.place_market_order(mid, "buy", 2.0, close_price=100.0, client_order_id=cid)

        assert res.status == "needs_reconcile"
        assert len(inner.submits) == 0
        assert len(jb.pending()) == 1
        conn.close()


class TestB2NoClientOrderIdStillUniquePerCall:
    """With no caller-supplied id the wrapper must not invent one that
    collides. Each call is treated as its own intent."""

    def test_consecutive_idempotent_flatten_calls_both_submit(self) -> None:
        conn, inner, jb = _build()
        mid = uuid.uuid4()
        jb.place_flatten_order(mid, "sell", 1.0, close_price=100.0)
        jb.place_flatten_order(mid, "sell", 1.0, close_price=100.0)
        jb.place_flatten_order(mid, "sell", 1.0, close_price=100.0)
        assert len(inner.submits) == 3
        conn.close()

    def test_quantity_change_is_a_distinct_intent(self) -> None:
        conn, inner, jb = _build()
        mid = uuid.uuid4()
        jb.place_flatten_order(mid, "sell", 1.0, close_price=100.0)
        jb.place_flatten_order(mid, "sell", 2.0, close_price=100.0)
        assert len(inner.submits) == 2
        conn.close()


@pytest.mark.parametrize("quantity", [0.001, 1.0, 4.0, 1234.5678])
def test_b2_arbitrary_quantities_do_not_collide(quantity: float) -> None:
    conn, inner, jb = _build()
    mid = uuid.uuid4()
    jb.place_flatten_order(mid, "sell", quantity, close_price=100.0)
    jb.place_flatten_order(mid, "sell", quantity, close_price=100.0)
    assert len(inner.submits) == 2, f"collision at quantity={quantity}"
    conn.close()
