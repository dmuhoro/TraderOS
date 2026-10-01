"""G-02 unattended soak: close out the run's own POSITION, not just its orders.

The real defect this pins: the soak harness only ever closed out resting
*orders*. Every cycle and every latency probe is a market BUY, so a long
unattended run walked the account into a holding. On the actual paper account
that reached 712.5 AAPL with ``cash=-122422.75`` and ``buying_power=0`` — after
which every new buy was rejected, so the window produced no fills, no latency
samples and a guaranteed reconcile mismatch, and could never return PASS.

Two properties are asserted here:

1. ``_flatten_own_delta`` closes only the delta this run opened, back to the
   batch's own pre-run baseline, and fails closed when broker truth cannot be
   read or the position will not come back.
2. The real harness, driven end to end with a fake adapter, returns
   ``VERDICT: PASS`` while leaving a pre-existing holding untouched — and
   returns ``VERDICT: FAIL`` when the close is refused, so the gate is proven
   to reject the residue it is meant to reject.

The end-to-end tests run the UNMODIFIED harness: only the broker adapter is
substituted, exactly as the repo's other evidence drills do, so what is under
test is the shipped script rather than a re-implementation of it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "evidence" / "run_real_paper_soak.py"

# A holding the soak must never liquidate: on a shared account it may be the
# operator's. Quantities are fractional, as real paper fills are.
BASELINE_QTY = 10.0


class _FakeAdapter:
    """In-memory stand-in for the Alpaca adapter, sized to the soak's use.

    ``allow_flatten=False`` refuses the market close, which is how the
    fail-closed path becomes observable end to end.
    """

    def __init__(self, *, baseline_qty: float = BASELINE_QTY, allow_flatten: bool = True) -> None:
        self.qty = baseline_qty
        self.allow_flatten = allow_flatten
        self.sells = 0.0
        self.buy_qty = 0.0

    def get_positions(self) -> list[dict]:
        if not self.qty:
            return []
        return [{"symbol": _soak_symbol(), "qty": self.qty, "market_value": self.qty * 100.0}]

    def get_open_orders(self) -> list[dict]:
        return []

    def cancel_order(self, order_id: str):
        raise AssertionError("no resting orders to cancel in this fake")

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ):
        if side == "sell":
            if not self.allow_flatten:
                return _fill(False, quantity, "rejected", "blocked")
            self.sells += quantity
            self.qty -= quantity
        else:
            self.buy_qty += quantity
            self.qty += quantity
        return _fill(True, quantity, "filled", f"ord-{uuid.uuid4().hex[:8]}")


def _fill(filled: bool, quantity: float, status: str, order_id: str):
    from traderos.domain.adapters.broker_adapter import FillResult

    return FillResult(
        filled=filled,
        fill_quantity=quantity if filled else 0.0,
        fill_price=100.0 if filled else 0.0,
        remaining=0.0 if filled else quantity,
        status=status,
        order_id=order_id,
    )


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("soak_harness_under_test", HARNESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _soak_symbol() -> str:
    return os.getenv("SOAK_SYMBOL", "AAPL")


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load_harness()


# --------------------------------------------------------------------------
# _positions_by_symbol
# --------------------------------------------------------------------------


def test_positions_by_symbol_keys_and_sums(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=4.25)
    assert harness._positions_by_symbol(adapter) == {"AAPL": pytest.approx(4.25)}


def test_positions_by_symbol_drops_a_flattened_account(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=0.0)
    assert harness._positions_by_symbol(adapter) == {}


def test_positions_by_symbol_is_none_when_the_broker_cannot_be_asked(harness, monkeypatch) -> None:
    """An unreadable position book is NOT a flat one — the close-out must know
    the difference or it would liquidate blind and call a full account clean."""
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)

    class _Down(_FakeAdapter):
        def get_positions(self) -> list[dict]:
            raise RuntimeError("503 from broker")

    assert harness._positions_by_symbol(_Down()) is None


# --------------------------------------------------------------------------
# _flatten_own_delta
# --------------------------------------------------------------------------


def test_flatten_closes_only_this_run_delta(harness, monkeypatch) -> None:
    """The run bought into a pre-existing holding: only the increment is sold."""
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=BASELINE_QTY)
    adapter.qty = BASELINE_QTY + 7.5  # the soak bought 7.5

    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": BASELINE_QTY}, settle_waits=30
    )

    assert failures == []
    assert resettled is True
    assert adapter.sells == pytest.approx(7.5), "only this run's delta may be closed"
    assert adapter.qty == pytest.approx(BASELINE_QTY), "the pre-existing holding is untouched"


def test_flatten_buys_back_a_short_it_opened(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=3.0)
    adapter.qty = 3.0 - 1.5  # the soak sold short into the baseline

    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": 3.0}, settle_waits=30
    )

    assert failures == []
    assert resettled is True
    assert adapter.qty == pytest.approx(3.0)


def test_flatten_is_a_no_op_when_nothing_was_opened(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=BASELINE_QTY)

    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": BASELINE_QTY}, settle_waits=30
    )

    assert failures == []
    assert resettled is True
    assert adapter.sells == 0.0


def test_flatten_fails_closed_without_a_baseline(harness, monkeypatch) -> None:
    """No baseline means no proof of ownership: flatten nothing, report failure."""
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=BASELINE_QTY)
    adapter.qty = 99.0

    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), None, settle_waits=30
    )

    assert resettled is False
    assert failures and "baseline" in failures[0]
    assert adapter.qty == 99.0, "a refused close must not trade"


def test_flatten_fails_closed_when_the_close_is_refused(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=BASELINE_QTY, allow_flatten=False)
    adapter.qty = BASELINE_QTY + 5.0

    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": BASELINE_QTY}, settle_waits=30
    )

    assert resettled is False
    assert any("did not return to baseline" in f for f in failures)


def test_flatten_does_not_loop_forever_against_a_refusing_broker(harness, monkeypatch) -> None:
    """A refused close is bounded by the attempt cap, not just the deadline."""
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _FakeAdapter(baseline_qty=BASELINE_QTY, allow_flatten=False)
    adapter.qty = BASELINE_QTY + 5.0

    _, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": BASELINE_QTY}, settle_waits=3600
    )

    assert resettled is False
    assert adapter.sells == 0.0, "a refusing broker must not be spammed"


def test_lagging_position_book_cannot_produce_a_false_pass(harness, monkeypatch) -> None:
    """The second-order defect: Alpaca's position book LAGS fill settlement.

    A batch read positions once at the end of close-out, saw a stale flat book,
    and printed ``after_qty=0.000000`` / ``positions_back_at_baseline=True`` /
    ``VERDICT: PASS`` — while 132.05154639 shares of its own buying were still
    in flight. The next run inherited them as a "pre-run baseline" it was
    forbidden to touch, so the leak became permanent and self-legitimising.

    So the book must be shown to be AT REST (two consecutive identical reads)
    before it may declare anything. Here the fill only becomes visible on the
    third read: a single-read implementation returns "flat" and passes.
    """
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)

    class _LaggingBook(_FakeAdapter):
        """Reports flat until the second read, then reveals the in-flight fill."""

        def __init__(self) -> None:
            super().__init__(baseline_qty=0.0)
            self.reads = 0
            self._revealed = False

        def get_positions(self) -> list[dict]:
            self.reads += 1
            if self.reads <= 2:
                return []  # the fill has not propagated yet
            if not self._revealed:
                self._revealed = True
                self.qty = 132.05154639
            return super().get_positions()

    adapter = _LaggingBook()
    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": 0.0}, settle_waits=60
    )

    assert resettled is True, "the disclosed delta is closed"
    assert failures == []
    assert adapter.qty == pytest.approx(0.0), "the in-flight fill is not left behind"
    assert adapter.reads >= 4, "a single read would have declared a false flat"


def test_position_book_that_never_settles_fails_closed(harness, monkeypatch) -> None:
    """A book still moving at the deadline fails closed rather than passing."""
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)

    class _AlwaysMoving(_FakeAdapter):
        def __init__(self) -> None:
            super().__init__(baseline_qty=0.0)
            self.reads = 0

        def get_positions(self) -> list[dict]:
            self.reads += 1
            self.qty = float(self.reads)  # never the same twice
            return super().get_positions()

    adapter = _AlwaysMoving()
    failures, resettled, _notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": 0.0}, settle_waits=0
    )

    assert resettled is False
    assert any("never reached rest" in f for f in failures)


def test_flatten_waits_out_a_pending_close_instead_of_failing(harness, monkeypatch) -> None:
    """The real close-out shape: a fractional market close acks ``pending``.

    It fills a moment later. The verdict is "back at baseline", so a settling
    close must NOT be scored as a failed close-out — scoring the ack is what
    failed every real batch against the paper broker.
    """
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)

    class _AcksPending(_FakeAdapter):
        """A fractional close acks ``pending``; the fill lands moments later."""

        def place_market_order(self, *a, **kw):
            side, quantity = a[1], a[2]
            self.qty += quantity if side == "buy" else -quantity
            return _fill(False, 0.0, "pending", "ord-pending")

    adapter = _AcksPending(baseline_qty=BASELINE_QTY)
    adapter.qty = BASELINE_QTY + 6.0
    failures, resettled, notes = harness._flatten_own_delta(
        adapter, uuid.uuid4(), {"AAPL": BASELINE_QTY}, settle_waits=30
    )

    assert failures == []
    assert resettled is True
    assert any("pending" in n for n in notes), "the settling close is recorded, not hidden"


# --------------------------------------------------------------------------
# _cancel_residue — the guarantee is "not resting", not "cancel returned OK"
# --------------------------------------------------------------------------


class _OrderBookAdapter:
    """Broker whose resting orders can be made to ignore cancels.

    ``filled_on_cancel`` reproduces the real race: a market order fills between
    the open-orders snapshot and the cancel, so the broker refuses to cancel it.
    """

    def __init__(self, order_id: str, *, filled_on_cancel: bool, allow_cancel: bool) -> None:
        self.order_id = order_id
        self.open = {order_id}
        self.filled_on_cancel = filled_on_cancel
        self.allow_cancel = allow_cancel

    def get_open_orders(self) -> list[dict]:
        return [{"id": oid, "symbol": _soak_symbol(), "qty": 1.0} for oid in self.open]

    def cancel_order(self, order_id: str):
        if self.filled_on_cancel:
            self.open.discard(order_id)
            return _fill(False, 0.0, "rejected", "order cannot be cancelled once filled")
        if not self.allow_cancel:
            return _fill(False, 0.0, "rejected", "cancel refused")
        self.open.discard(order_id)
        return _fill(True, 0.0, "cancelled", order_id)


def test_order_that_filled_during_close_out_is_not_residue(harness, monkeypatch) -> None:
    """The real batch-failing race: a fill between snapshot and cancel.

    The cancel is refused, but nothing of ours is resting any more, so the
    close-out succeeded. Scoring the refused response as residue failed batches
    that had left the account exactly as they found it.
    """
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _OrderBookAdapter("ord-1", filled_on_cancel=True, allow_cancel=False)

    still, resettled = harness._cancel_residue(
        adapter, [{"id": "ord-1", "symbol": _soak_symbol(), "qty": 1.0}], settle_waits=30
    )

    assert resettled is True
    assert still == []


def test_order_that_stays_resting_is_reported_as_residue(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    adapter = _OrderBookAdapter("ord-2", filled_on_cancel=False, allow_cancel=False)

    still, resettled = harness._cancel_residue(
        adapter, [{"id": "ord-2", "symbol": _soak_symbol(), "qty": 1.0}], settle_waits=0
    )

    assert resettled is False
    assert still == ["ord-2"], "an order left resting is leaked residue and must be reported"


def test_nothing_to_cancel_is_clean(harness, monkeypatch) -> None:
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    still, resettled = harness._cancel_residue(
        _OrderBookAdapter("x", filled_on_cancel=False, allow_cancel=False), [], settle_waits=0
    )
    assert still == []
    assert resettled is True


# --------------------------------------------------------------------------
# End to end, through the real harness
# --------------------------------------------------------------------------

_FAKE_SOURCE = '''

from traderos.domain.adapters.broker_adapter import FillResult as _FR


class _FakeAdapter:
    """Pre-existing holding that the soak must not touch; buys fill; the
    market close is honoured only when ALLOW_FLATTEN is set."""

    def __init__(self, **kwargs):
        self.qty = BASELINE_QTY
        self.buys = 0.0
        self.sells = 0.0
        self.allow_flatten = ALLOW_FLATTEN

    def get_positions(self):
        if not self.qty:
            return []
        return [{"symbol": SOAK_SYMBOL, "qty": self.qty, "market_value": self.qty * 100.0}]

    def get_open_orders(self):
        return []

    def cancel_order(self, order_id):
        return _FR(False, 0.0, 0.0, 0.0, "cancelled", order_id)

    def place_market_order(self, market_id, side, quantity, close_price=None,
                           client_order_id=None):
        if side == "sell":
            if not self.allow_flatten:
                return _FR(False, 0.0, 0.0, quantity, "rejected", "blocked")
            self.sells += quantity
            self.qty -= quantity
        else:
            self.buys += quantity
            self.qty += quantity
        return _FR(True, quantity, 100.0, 0.0, "filled", "ord-" + client_order_id[-8:])
'''


def _run_real_harness(tmp_path: Path, *, allow_flatten: bool) -> subprocess.CompletedProcess[str]:
    src = HARNESS.read_text(encoding="utf-8")
    src = src.replace(
        "REPO_ROOT = Path(__file__).resolve().parents[2]", f'REPO_ROOT = Path("{tmp_path}")'
    )
    src = src.replace(
        "    adapter = AlpacaBrokerAdapter(\n"
        "        api_key=api_key,\n"
        "        secret_key=secret_key,\n"
        "        paper=True,\n"
        "        symbol_map={SOAK_MARKET_ID: SOAK_SYMBOL},\n"
        "    )",
        "    adapter = _FakeAdapter()",
    )
    assert "_FakeAdapter()" in src, "adapter construction was not substituted"
    src = src.replace(
        "def main(argv: list[str]) -> int:",
        f"BASELINE_QTY = {BASELINE_QTY}\nALLOW_FLATTEN = {allow_flatten}\n"
        f"{_FAKE_SOURCE}\n\ndef main(argv: list[str]) -> int:",
    )
    shim = tmp_path / "harness_under_test.py"
    shim.write_text(src, encoding="utf-8")
    (tmp_path / "docs" / "evidence").mkdir(parents=True, exist_ok=True)

    return subprocess.run(
        [sys.executable, str(shim), "2"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=dict(
            os.environ,
            PYTHONPATH=str(REPO_ROOT / "src"),
            ALPACA_API_KEY="stub-key",
            ALPACA_SECRET_KEY="stub-secret",
            SOAK_LATENCY_PROBES="2",
            SOAK_SETTLE_SECONDS="30",
            SOAK_LOG_LABEL="soak_flat_drill",
        ),
        timeout=300,
        check=False,
    )


def test_real_harness_closes_its_own_position_and_passes(tmp_path) -> None:
    """The shipped harness returns PASS and leaves the operator's holding intact.

    Pre-fix this reported ``mismatches=1`` (broker-only position) whenever the
    account held anything at batch start — which is why the real window could
    not return PASS.
    """
    proc = _run_real_harness(tmp_path, allow_flatten=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERDICT: PASS" in proc.stdout, proc.stdout
    assert "mismatches=0" in proc.stdout, proc.stdout
    assert "positions_back_at_baseline=True" in proc.stdout, proc.stdout
    assert f"baseline_qty={BASELINE_QTY:.6f}" in proc.stdout, proc.stdout
    assert "orders_filled=2" in proc.stdout, proc.stdout


def test_real_harness_fails_when_its_own_position_cannot_be_closed(tmp_path) -> None:
    """The negative control: with the close refused the gate must FAIL.

    Without this, a green harness could still be green because nothing checked
    the position at all — which is precisely the defect being fixed.
    """
    proc = _run_real_harness(tmp_path, allow_flatten=False)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "VERDICT: FAIL" in proc.stdout, proc.stdout
    assert "runner closed out, broker at baseline:    False" in proc.stdout, proc.stdout
    assert "positions_back_at_baseline=False" in proc.stdout, proc.stdout
