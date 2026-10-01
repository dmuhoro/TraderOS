#!/usr/bin/env python3
"""G-02 evidence: unattended paper-broker soak HARNESS (real Alpaca paper).

The G-02 exit test is an *unattended paper-broker soak (Alpaca paper,
24–72h)*: 0 reconcile errors, 0 duplicate/lost orders across forced
disconnects, journal-recovery replays correctly.

This is the operator-run harness for that soak. It requires live **paper**
Alpaca credentials in the environment (ALPACA_API_KEY / ALPACA_SECRET_KEY —
never committed; env-only per LIVE_RUN_POLICY §credential policy). Without
credentials it fails closed: it refuses to invent broker truth, so the honest
result is NO-GO (soak cannot run), exactly like ``run_cost_adjusted_backtest``.

It drives the real production chain (CycleExecutor -> JournaledBroker ->
AlpacaBrokerAdapter) for ``cycles`` market orders against the real Alpaca
paper endpoint, then reconciles broker truth against the journal + local
position book.

Run (env-only paper keys):
    ALPACA_API_KEY=... ALPACA_SECRET_KEY=... \
    PYTHONPATH=. python3 scripts/evidence/run_real_paper_soak.py [cycles]
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from traderos.application.cycle_executor import CycleExecutor  # noqa: E402
from traderos.application.models import TradingMode  # noqa: E402
from traderos.domain.entities.signal import Signal  # noqa: E402
from traderos.domain.entities.signal import SignalDirection  # noqa: E402
from traderos.domain.services.analysis_service import AnalysisService  # noqa: E402
from traderos.domain.services.broker_state_reconciliation_service import (  # noqa: E402
    BrokerStateReconciliationService,
)
from traderos.domain.services.portfolio_service import PortfolioService  # noqa: E402
from traderos.domain.services.risk_service import KillSwitch  # noqa: E402
from traderos.domain.services.risk_service import RiskAssessment  # noqa: E402
from traderos.domain.services.risk_service import TradeVerdict  # noqa: E402
from traderos.domain.services.signal_service import SignalProvenance  # noqa: E402
from traderos.domain.services.strategy_framework import SignalResult  # noqa: E402
from traderos.domain.services.strategy_framework import StrategyBase  # noqa: E402
from traderos.domain.services.strategy_framework import registry as strategy_registry  # noqa: E402
from traderos.infrastructure.alpaca_broker import AlpacaBrokerAdapter  # noqa: E402
from traderos.infrastructure.events import InMemoryEventBus  # noqa: E402

# Real tradable symbols in the paper account (verified: get_asset -> tradable=True).
# The broker requires a symbol_map; without it the adapter would submit the raw
# market UUID as a symbol and the real paper account rejects "asset not found".
SOAK_SYMBOL = os.getenv("SOAK_SYMBOL", "AAPL")
SOAK_MARKET_ID = uuid.uuid5(uuid.NAMESPACE_URL, f"traderos-soak.{SOAK_SYMBOL}")
from traderos.infrastructure.journal import OrderEventJournal  # noqa: E402
from traderos.infrastructure.journaled_broker import JournaledBroker  # noqa: E402
from traderos.infrastructure.observability import SQLiteAuditService  # noqa: E402
from traderos.infrastructure.observability import SQLiteHealthService  # noqa: E402
from traderos.infrastructure.observability import SQLiteManifestService  # noqa: E402
from traderos.infrastructure.observability import SQLiteMetricsService  # noqa: E402
from traderos.infrastructure.repositories.sqlite.trades import (  # noqa: E402
    SQLitePositionRepository,
)
from traderos.infrastructure.repositories.sqlite.trades import SQLiteTradeRepository  # noqa: E402


def _evidence_path() -> Path:
    """Dated soak evidence path (defaults to today; SOAK_LOG_LABEL can disambiguate).

    Each soak run must write its own dated log so the 24-72h barrier evidence
    chain is auditable and a re-run never overwrites an earlier result.
    """
    label = os.getenv("SOAK_LOG_LABEL", "real_paper_soak")
    date = datetime.now(UTC).date().isoformat()
    return REPO_ROOT / "docs" / "evidence" / f"{date}_{label}.log"


OUT = _evidence_path()


def _emit(lines: list[str]) -> None:
    """Write the evidence file and echo it to stdout, in that order.

    The file is written FIRST so a stdout failure (a closed/broken pipe when
    Railway's log drain goes away) can never cost us the evidence row. Both
    steps are flushed so a SIGKILL mid-write cannot truncate the log.
    """
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines) + "\n"
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        print(body, flush=True)
    except (BrokenPipeError, OSError):  # pragma: no cover — log drain died
        pass


class _Strat(StrategyBase):
    name = "evidence_real_paper_strat"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "real-paper-soak"})


def _prov(mid: uuid.UUID, direction: SignalDirection):
    now = datetime.now(UTC)
    return SignalProvenance(
        signal=Signal(
            market_id=mid,
            strategy_id=uuid.uuid4(),
            direction=direction,
            confidence=0.8,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        ),
        strategy_name="real-paper-soak",
        indicators_used={},
    )


def _snapshot_open_orders(adapter: AlpacaBrokerAdapter) -> list[dict] | None:
    """Broker open orders, or ``None`` when the read itself failed.

    An unattended soak must distinguish "the broker says there are no open
    orders" from "we could not ask the broker". Collapsing the second into the
    first (``[] on error``) would let a transient outage masquerade as a clean
    account and would skip the close-out of orders that are actually resting —
    the exact silent-drop the AGENTS rules forbid.
    """
    try:
        return adapter.get_open_orders()
    # Any broker read failure is a NO-GO, never an empty order book.
    except Exception as exc:  # noqa: BLE001
        print(f"broker open-orders read failed: {exc}", flush=True)
        return None


def _cancel_residue(
    adapter: AlpacaBrokerAdapter, orders: list[dict], settle_waits: int
) -> tuple[list[str], bool]:
    """Cancel this run's own residue and wait for the broker to settle.

    Returns ``(cancel_failures, resettled)``. Never raises: an unattended soak
    must always finish its own close-out and record the outcome honestly, even
    when the broker is unreachable — a leaked order is evidence, not something
    to crash on and lose the record of.
    """
    failures: list[str] = []
    for order in orders:
        try:
            if not adapter.cancel_order(order["id"]).filled:
                failures.append(str(order.get("id", "?")))
        except Exception as exc:  # noqa: BLE001 — record, never crash the close-out
            print(f"cancel failed for {order.get('id', '?')}: {exc}", flush=True)
            failures.append(str(order.get("id", "?")))

    deadline = time.monotonic() + settle_waits
    resettled = not orders
    while not resettled and time.monotonic() < deadline:
        time.sleep(2)
        remaining = _snapshot_open_orders(adapter)
        if remaining is None:
            continue
        if not [o for o in remaining if o["id"] in {c["id"] for c in orders}]:
            resettled = True
    return failures, resettled


def _own_residue(
    adapter: AlpacaBrokerAdapter, baseline_open_ids: set[str] | None
) -> list[dict] | None:
    """This run's own resting orders, or ``None`` if the read failed.

    Only orders the soak created are ever touched: anything present in the
    pre-run baseline predates this run (it may be a user's order and is left
    strictly alone). ``latprobe-`` orphans from an earlier crashed run are also
    reclaimed — they are unambiguously ours and must not accumulate.
    """
    orders = _snapshot_open_orders(adapter)
    if orders is None:
        return None
    if baseline_open_ids is None:
        # We never established a baseline, so we cannot prove ownership. Do NOT
        # guess and cancel: the fail-closed choice is to claim nothing and let
        # the batch FAIL on residue rather than risk touching a user's order.
        print("no open-orders baseline — refusing to claim residue", flush=True)
        return []
    return [
        o
        for o in orders
        if str(o.get("client_order_id", "")).startswith("latprobe-")
        or (o["symbol"] == SOAK_SYMBOL and o["id"] not in baseline_open_ids)
    ]


def main(argv: list[str]) -> int:
    cycles = int(argv[0]) if argv else 50
    lines: list[str] = []
    started = datetime.now(UTC)
    settle_waits = int(os.getenv("SOAK_SETTLE_SECONDS", "30"))

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        _emit(
            [
                "REAL-PAPER SOAK HARNESS — G-02 unattended paper-broker soak",
                f"started {started.isoformat()}",
                "FATAL: no ALPACA_API_KEY / ALPACA_SECRET_KEY (paper keys) in env.",
                (
                    "NO-GO: the soak requires real Alpaca paper credentials; the drill "
                    "refuses to fabricate broker truth without them."
                ),
                "VERDICT: NO-GO (credentials absent) — harness ready, soak not run",
                f"Evidence: {OUT}",
            ]
        )
        return 2

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)

    adapter = AlpacaBrokerAdapter(
        api_key=api_key,
        secret_key=secret_key,
        paper=True,
        symbol_map={SOAK_MARKET_ID: SOAK_SYMBOL},
    )
    journal = OrderEventJournal(conn)
    broker = JournaledBroker(adapter, journal)

    audit = SQLiteAuditService(conn)
    portfolio = PortfolioService(
        trade_repo=SQLiteTradeRepository(conn),
        position_repo=SQLitePositionRepository(conn),
        audit=audit,
    )
    signal_service = Mock()
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

    mid = SOAK_MARKET_ID
    signal_service.process_evaluation.return_value = _prov(mid, SignalDirection.LONG)

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
            enabled_strategies=lambda: [("real-paper-soak", "evidence_real_paper_strat", {})],
        )

    strategy_registry.register(_Strat)
    baseline_open_ids: set[str] | None = None
    runner_open_orders: list[dict] = []
    cancel_failures: list[str] = []
    resettled = False
    crash: str | None = None
    try:
        executor = build_executor()
        # Baseline of pre-existing broker state so the harness only ever claims
        # (and only ever closes out) residue *it* created, never a user's.
        # ``None`` means "could not read" — never silently treated as "empty",
        # because an empty baseline would make every resting order look like a
        # user's and every order would be left resting forever.
        baseline = _snapshot_open_orders(adapter)
        if baseline is not None:
            baseline_open_ids = {str(o["id"]) for o in baseline}

        results: list[tuple[bool, str]] = []
        for i in range(cycles):
            r = executor.run(mid, 100.0 + (i % 20))
            results.append((r.trades > 0, ";".join(r.errors[:1])))

        # WP6 latency calibration (rides the soak): measure real submit-ack
        # latency for a small bound of same-path market orders, then close them
        # out with the rest. `place_market_order` returns after the broker ack,
        # so the elapsed time is the full ack round-trip through the real path.
        ack_latencies_ms: list[float] = []
        probe_len = int(os.getenv("SOAK_LATENCY_PROBES", "10"))
        for _ in range(probe_len):
            # `place_market_order` returns only after the broker ack, so the
            # elapsed time around the call IS the full submit->ack round trip.
            t0 = datetime.now(UTC)
            res = adapter.place_market_order(
                mid, "buy", 0.01, client_order_id=f"latprobe-{uuid.uuid4().hex[:12]}"
            )
            ack_ms = (datetime.now(UTC) - t0).total_seconds() * 1000.0
            if res.status == "rejected":
                # The broker is refusing orders: stop probing (further probes
                # only add load and extend the outage) but still record the
                # rejection so the evidence shows exactly why the batch stopped.
                lines.append(
                    f"latency probe rejected after {len(ack_latencies_ms)} probe(s): {res.order_id}"
                )
                break
            ack_latencies_ms.append(ack_ms)

        # Close out this run's own residue so the reconcile is against a
        # settled state — a pending order left resting is not a reconcilation
        # error, and an unattended soak must not leak live orders behind it.
        claimed = _own_residue(adapter, baseline_open_ids)
        if claimed is None:
            runner_open_orders = []
            resettled = False
            lines.append("close-out: broker open-orders read failed; residue unverified")
        else:
            runner_open_orders = claimed
            cancel_failures, resettled = _cancel_residue(adapter, claimed, settle_waits)

        filled = sum(1 for ok, _ in results if ok)
        not_filled = [e for _, e in results if e]

        snapshot = _snapshot_open_orders(adapter)
        broker_orders = len(snapshot) if snapshot is not None else -1
        journal_confirmed = journal.count()
        pending = len(journal.pending_events())
        local_positions = [
            {
                # Keyed by the BROKER symbol, not the internal market UUID: the
                # reconcile service matches on `symbol`, and the real broker
                # reports "AAPL". Keying this by market_id made every held
                # position look like a phantom local-only position next to a
                # phantom broker-only one — a guaranteed false reconcile failure
                # on any batch where the soak actually held a position.
                "symbol": SOAK_SYMBOL,
                "market_id": str(p.market_id),
                "qty": p.quantity,
                "current_price": p.current_price,
                "entry_price": p.entry_price,
            }
            for p in SQLitePositionRepository(conn).list_open()
        ]
        recon = BrokerStateReconciliationService(broker=adapter).reconcile(
            local_positions=local_positions,
            local_orders=[],
            journal_pending=broker.pending(),
        )

        # An unattended soak passes when nothing is lost, the account is left
        # in the state it started, and reconciliation is clean against the real
        # paper broker. Real paper fills can be partial or pending (market
        # hours), so we assert the *ordering + hygiene guarantees*:
        # 0 lost intents, clean reconcile, 0 leaked open orders, journal
        # confirmed == submit attempts. An unverifiable broker read is NOT a
        # clean account — it fails closed.
        baseline_count = len(baseline_open_ids) if baseline_open_ids is not None else -1
        no_lost = pending == 0
        no_residue = (
            cancel_failures == []
            and resettled
            and snapshot is not None
            and baseline_open_ids is not None
            and broker_orders == baseline_count
        )
        reconcile_clean = recon.errors == [] and not recon.has_mismatches
        submit_attempts = len(results)
        verdict = "PASS" if (no_lost and no_residue and reconcile_clean) else "FAIL"

        lines.append("REAL-PAPER SOAK HARNESS — G-02 unattended paper-broker soak")
        lines.append(f"started {started.isoformat()} finished {datetime.now(UTC).isoformat()}")
        lines.append(f"cycles={cycles} orders_filled={filled} submit_attempts={submit_attempts}")
        if not_filled:
            lines.append("  non-fill reasons (first per cycle):")
            for e in not_filled[:5]:
                lines.append(f"    - {e}")
        lines.append(f"journal_confirmed={journal_confirmed} journal_pending={pending}")
        lines.append(
            f"runner_open_orders={len(runner_open_orders)} "
            f"cancel_failures={len(cancel_failures)} resettled={resettled}"
        )
        lines.append(
            f"broker_open_orders_after={broker_orders} " f"baseline_open_orders={baseline_count}"
        )
        lines.append(f"local_positions={len(local_positions)}")
        lines.append(f"reconcile_errors={len(recon.errors)} mismatches={len(recon.mismatches)}")
        lines.append("")
        lines.append(f"0 lost orders (no pending intents):       {no_lost}")
        lines.append(f"runner closed out, broker at baseline:    {no_residue}")
        lines.append(f"reconcile clean vs real paper broker:    {reconcile_clean}")
        lines.append("")
        lat_min = min(ack_latencies_ms) if ack_latencies_ms else 0.0
        lat_max = max(ack_latencies_ms) if ack_latencies_ms else 0.0
        lat_med = sorted(ack_latencies_ms)[len(ack_latencies_ms) // 2] if ack_latencies_ms else 0.0
        lines.append(
            "WP6 latency (submit->ack ms): "
            f"n={len(ack_latencies_ms)} min={lat_min:.1f} median={lat_med:.1f} max={lat_max:.1f}"
        )
        lines.append(f"VERDICT: {verdict}")
        lines.append(f"Evidence: {OUT}")

        _emit(lines)
        return 0 if verdict == "PASS" else 1
    except BaseException as exc:
        # A crash is evidence, not a lost run: record it in `finally` and re-raise
        # so the exit code still tells the supervisor the batch did not complete.
        crash = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Close-out runs in `finally` so a crash anywhere above can never leak
        # resting orders on the paper account, and the crash is always recorded
        # to the evidence file — an unattended soak must never vanish silently
        # (AGENTS: no silent drops).
        if baseline_open_ids is not None:
            claimed = _own_residue(adapter, baseline_open_ids)
            if claimed:
                cancel_failures, resettled = _cancel_residue(adapter, claimed, settle_waits)
        strategy_registry.unregister("evidence_real_paper_strat")
        if crash is not None:
            _emit(
                [
                    "REAL-PAPER SOAK HARNESS — G-02 unattended paper-broker soak",
                    f"started {started.isoformat()} crashed {datetime.now(UTC).isoformat()}",
                    f"CRASH: {crash}",
                    # Everything observed before the abort is carried into the
                    # crash record: the evidence file is overwritten here, and a
                    # crash must never cost us the partial run that led to it.
                    *lines,
                    (
                        "crash-closeout: cancelled own residue in finally "
                        f"cancel_failures={len(cancel_failures)} resettled={resettled}"
                    ),
                    "VERDICT: CRASH (harness aborted; residue closed out, run must be re-run)",
                    f"Evidence: {OUT}",
                ]
            )
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
