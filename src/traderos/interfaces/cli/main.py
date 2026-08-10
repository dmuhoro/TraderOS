from __future__ import annotations

import argparse
import json
import sys
import uuid
from importlib.metadata import version
from typing import Any

from traderos.application.factory import build_orchestrator
from traderos.domain.exceptions import ConfigError
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.database.backup import create_backup
from traderos.infrastructure.database.backup import list_backups
from traderos.infrastructure.database.backup import restore_backup
from traderos.infrastructure.database.connection import close_all_pools
from traderos.infrastructure.database.connection import get_connection
from traderos.infrastructure.database.connection import resolve_backend
from traderos.infrastructure.database.migration_manager import get_current_version
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.health import HealthService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TraderOS Unified CLI")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    sub = parser.add_subparsers(dest="command")

    p_strat = sub.add_parser("strategies", help="List and inspect strategies")
    p_strat.add_argument("--name", help="Show details for a specific strategy")

    p_backtest = sub.add_parser("backtest", help="Run a backtest")
    p_backtest.add_argument("strategy", help="Strategy name")
    p_backtest.add_argument("--candles", type=int, default=500, help="Number of candles")
    p_backtest.add_argument(
        "--source",
        default="synthetic",
        choices=["synthetic", "binance", "alpaca"],
        help="Historical data source (default: synthetic)",
    )
    p_backtest.add_argument(
        "--symbol",
        default="",
        help="Provider symbol, e.g. BTCUSDT (binance) or BTC/USD (alpaca)",
    )
    p_backtest.add_argument(
        "--timeframe", default="1h", choices=["1m", "5m", "15m", "1h", "4h", "1d"]
    )
    p_backtest.add_argument(
        "--no-cache", action="store_true", help="Bypass the durable candle cache"
    )
    p_backtest.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
        help="Side-aware market-impact slippage in basis points (default 5.0)",
    )
    p_backtest.add_argument(
        "--fee-bps",
        type=float,
        default=0.0,
        help="Commission/fee in basis points of traded notional (default 0.0)",
    )
    p_backtest.add_argument(
        "--min-fee",
        type=float,
        default=0.0,
        help="Minimum fee per order in currency units (default 0.0)",
    )

    p_paper = sub.add_parser("papertrade", help="Paper trading commands")
    p_paper_sub = p_paper.add_subparsers(dest="paper_cmd")
    p_paper_sub.add_parser("create", help="Create a new paper session")
    p_paper_sub.add_parser("list", help="List paper sessions")

    sub.add_parser("health", help="System health status")

    p_audit = sub.add_parser("audit", help="View audit trail / verify chain / query")
    p_audit.add_argument("--limit", type=int, default=10, help="Number of entries")
    p_audit_sub = p_audit.add_subparsers(dest="audit_cmd")
    p_audit_sub.add_parser("verify", help="Verify the audit chain integrity")
    p_audit_query = p_audit_sub.add_parser("query", help="Query audit entries")
    p_audit_query.add_argument(
        "--filter",
        default="",
        help='Filter entries, e.g. "action=crash.recovery" or "actor=system"',
    )
    p_audit_query.add_argument("--limit", type=int, default=10, help="Number of entries")

    p_notify = sub.add_parser("notify", help="Send a test notification")
    p_notify.add_argument(
        "--level", default="info", choices=["info", "warning", "error", "critical"]
    )
    p_notify.add_argument("--title", default="Test Notification")
    p_notify.add_argument("--message", default="")

    p_regime = sub.add_parser("signal", help="Check active signals for a market")
    p_regime.add_argument("market_id", type=str, help="Market UUID")

    p_daemon = sub.add_parser("daemon", help="Run the trading daemon")
    p_daemon.add_argument("action", nargs="?", default="run", choices=["run", "start"])
    p_daemon.add_argument("--interval", type=int, default=60, help="Cycle interval in seconds")
    p_daemon.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])

    p_run = sub.add_parser("run", help="Run the trading engine (alias of daemon start)")
    p_run.add_argument("--interval", type=int, default=60, help="Cycle interval in seconds")
    p_run.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])

    p_status = sub.add_parser(
        "status", help="System status (mode, health, kill switch, reconciliation)"
    )
    p_status.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])

    p_risk = sub.add_parser("risk", help="Risk / kill-switch controls (ADR-007)")
    p_risk_sub = p_risk.add_subparsers(dest="risk_cmd")
    for _cmd in ("status", "check", "reset", "kill"):
        _p = p_risk_sub.add_parser(_cmd, help=f"Risk: {_cmd}")
        _p.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])
    p_reconcile = p_risk_sub.add_parser("reconcile", help="Risk: reconcile broker/journal state")
    p_reconcile.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])
    p_reconcile.add_argument(
        "verb",
        nargs="?",
        default=None,
        const="status",
        help="Optional: 'status' for gate status only",
    )

    p_metrics = sub.add_parser("metrics", help="Metrics controls")
    p_metrics_sub = p_metrics.add_subparsers(dest="metrics_cmd")
    p_metrics_sub.add_parser("snapshot", help="Print a metrics snapshot")
    p_metrics_watch = p_metrics_sub.add_parser("watch", help="Run cycles and print metrics")
    p_metrics_watch.add_argument("--cycles", type=int, default=3, help="Number of cycles")
    p_metrics_watch.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])

    p_validate = sub.add_parser("validate", help="Validate configuration and environment")

    p_pilot = sub.add_parser("pilot", help="Controlled-pilot readiness and dry-run checks")
    p_pilot_sub = p_pilot.add_subparsers(dest="pilot_cmd")
    p_pilot_readiness = p_pilot_sub.add_parser(
        "readiness", help="Run the live-readiness gate (no orders placed)"
    )
    p_pilot_readiness.add_argument(
        "--mode",
        default="paper",
        choices=["paper", "live", "backtest"],
        help="Orchestrator mode used for the check (default: paper)",
    )
    p_pilot_dryrun = p_pilot_sub.add_parser(
        "dry-run",
        help="Rehearse the operator workflow end to end without live orders",
    )
    p_pilot_dryrun.add_argument(
        "--mode",
        default="paper",
        choices=["paper", "live", "backtest"],
        help="Orchestrator mode used for the check (default: paper)",
    )
    p_validate.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])

    p_security = sub.add_parser("security", help="Deployment security posture commands")
    p_security_sub = p_security.add_subparsers(dest="security_cmd")
    p_security_sub.add_parser("audit", help="Audit the deployment security posture")

    p_db = sub.add_parser("db", help="Database management commands")
    p_db_sub = p_db.add_subparsers(dest="db_cmd")

    p_db_sub.add_parser("migrate", help="Run pending migrations")
    p_db_sub.add_parser("check", help="Check database integrity")

    p_db_rollback = p_db_sub.add_parser("rollback", help="Rollback migrations")
    p_db_rollback.add_argument("--target", type=int, required=True, help="Target schema version")

    p_db_sub.add_parser("backup", help="Create a database backup")
    p_db_restore = p_db_sub.add_parser("restore", help="Restore from a backup")
    p_db_restore.add_argument("backup", nargs="?", help="Path to backup file")
    p_db_restore.add_argument("--backup", dest="backup_flag", help="Path to backup file")
    p_db_restore.add_argument(
        "--latest", action="store_true", help="Restore from the newest backup"
    )

    p_db_sub.add_parser("list-backups", help="List available backups")

    return parser


def cmd_strategies(args: argparse.Namespace) -> None:
    if args.json:
        if args.name:
            strat = strategy_registry.get(args.name)
            if strat is None:
                print(json.dumps({"error": f"Strategy '{args.name}' not found"}))
            else:
                print(json.dumps({"name": args.name, "version": strat.version}))
        else:
            print(json.dumps({"strategies": strategy_registry.list()}))
        return
    if args.name:
        strat = strategy_registry.get(args.name)
        if strat:
            print(f"Strategy: {args.name}")
        else:
            print(f"Strategy '{args.name}' not found")
    else:
        print("Registered strategies:")
        for name in strategy_registry.list():
            print(f"  - {name}")


def cmd_backtest(args: argparse.Namespace) -> None:
    strat_cls = strategy_registry.get(args.strategy)
    if strat_cls is None:
        print(f"Unknown strategy: {args.strategy}")
        return
    strat = strat_cls()

    source = getattr(args, "source", "synthetic")
    timeframe = getattr(args, "timeframe", "1h")
    symbol = getattr(args, "symbol", "")
    if not symbol and source != "synthetic":
        symbol = {"binance": "BTCUSDT", "alpaca": "BTC/USD"}.get(source, symbol)

    if source == "synthetic":
        from traderos.domain.services.backtesting_service import synthetic_candles

        mid = uuid.uuid4()
        candles = synthetic_candles(count=args.candles, market_id=mid)
    else:
        candles = _historical_candles_for_backtest(args)

    svc = BacktestingService(
        execution=ExecutionService(
            slippage_bps=getattr(args, "slippage_bps", 5.0),
            fee_bps=getattr(args, "fee_bps", 0.0),
            min_fee=getattr(args, "min_fee", 0.0),
        )
    )
    mid = candles[0].market_id
    result, steps = svc.run(strat, candles, mid)
    m = result.metrics
    print(f"Source: {source}")
    print(f"Symbol: {symbol or 'n/a'}  Timeframe: {timeframe}  Candles: {len(candles)}")
    print(f"Period: {result.period_start.date()} -> {result.period_end.date()}")
    print(
        f"Cost model: slippage {getattr(args, 'slippage_bps', 5.0)}bps "
        f"fee {getattr(args, 'fee_bps', 0.0)}bps"
    )
    print(f"Total Return: {m.total_return:.4f}")
    print(f"Sharpe: {m.sharpe_ratio:.4f}")
    print(f"Sortino: {m.sortino_ratio:.4f}")
    print(f"Max DD: {m.max_drawdown:.4f}")
    print(f"Win Rate: {m.win_rate:.4f}")
    print(f"Profit Factor: {m.profit_factor:.4f}")
    print(f"Total Trades: {m.total_trades}")
    print(f"Expectancy/bar: {m.expectancy:.4f}")
    print(f"Steps: {len(steps)}")


def _historical_candles_for_backtest(args: argparse.Namespace) -> list:
    from traderos.domain.services.historical_data import HistoricalDataService
    from traderos.infrastructure.collectors.alpaca_collector import AlpacaCollector
    from traderos.infrastructure.collectors.binance_collector import BinanceCollector
    from traderos.infrastructure.config.config_loader import Config
    from traderos.infrastructure.database.connection import get_connection
    from traderos.infrastructure.database.migration_manager import migrate
    from traderos.infrastructure.repositories.sqlite.historical_candles import (
        SQLiteHistoricalCandleRepository,
    )

    source = getattr(args, "source", "synthetic")
    timeframe = getattr(args, "timeframe", "1h")
    default_symbols = {"binance": "BTCUSDT", "alpaca": "BTC/USD"}
    symbol = getattr(args, "symbol", "") or default_symbols.get(source, "")

    cache = None
    try:
        cfg = Config.load()
        conn = get_connection(cfg)
        migrate(conn, target_version=7)
        conn.commit()
        cache = SQLiteHistoricalCandleRepository(conn)
    except Exception:
        cache = None

    collectors: dict[str, Any] = {
        "binance": BinanceCollector(),
        "alpaca": AlpacaCollector(),
    }
    service = HistoricalDataService(cache=cache, collectors=collectors)
    return service.get_candles(
        source,
        timeframe,
        symbol,
        limit=args.candles,
        use_cache=not getattr(args, "no_cache", False),
    )


def cmd_paper(args: argparse.Namespace) -> None:
    orch = build_orchestrator(mode="paper")
    if orch.paper is None:
        print("Paper trading not available")
        return
    if args.paper_cmd == "create":
        session = orch.paper.create_session(uuid.uuid4(), [uuid.uuid4()])
        print(f"Created session {session.id}")
    elif args.paper_cmd == "list":
        sessions = orch.paper.list_sessions()
        for s in sessions:
            print(f"  {s.id} — {s.status.value} — ${s.current_capital:.2f}")


def cmd_health(args: argparse.Namespace) -> None:
    svc = HealthService()
    ver = version("traderos")
    svc.report_healthy("cli", f"TraderOS CLI v{ver}")
    summary = svc.summary()
    if args.json:
        print(json.dumps({"version": ver, "services": summary}, indent=2, default=str))
        return
    print("System Health:")
    for name, healthy in summary.items():
        status = "PASS" if healthy else "FAIL"
        print(f"  [{status}] {name}")


def _build_audit_service() -> Any:
    """Durable audit on the configured DB — the same backend the daemon writes.

    ``audit``/``audit query``/``audit verify`` must read the real trail, not a
    fresh in-memory one, or the runbook's "review the audit log for crash
    events" step would silently report nothing on the one process that matters.
    """
    cfg = Config.load()
    conn = get_connection(cfg)
    if resolve_backend(cfg.database_url) == "postgres":
        from traderos.infrastructure.observability_postgres import PostgresAuditService

        return PostgresAuditService(conn)
    from traderos.infrastructure.observability import SQLiteAuditService

    return SQLiteAuditService(conn)


def _read_audit(limit: int = 10) -> list[Any]:
    try:
        svc = _build_audit_service()
        return svc.get_entries(limit=limit)
    except Exception as e:
        print(f"Audit trail unavailable: {e}. Run `python -m traderos db migrate` first.")
        sys.exit(1)


def cmd_audit(args: argparse.Namespace) -> None:
    entries = _read_audit(limit=args.limit)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "timestamp": e.timestamp.isoformat(),
                        "action": e.action,
                        "actor": e.actor,
                        "resource": e.resource,
                    }
                    for e in entries
                ],
                indent=2,
                default=str,
            )
        )
        return
    if not entries:
        print("No audit entries")
        return
    for e in entries:
        print(f"  [{e.timestamp.isoformat()}] {e.action} by {e.actor} on {e.resource}")


def cmd_audit_verify(args: argparse.Namespace) -> None:
    try:
        svc = _build_audit_service()
        ok = svc.verify_chain()
    except Exception as e:
        print(f"Audit trail unavailable: {e}. Run `python -m traderos db migrate` first.")
        sys.exit(1)
    print("Audit chain verification:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


_AUDIT_FILTER_FIELDS = ("action", "actor", "resource", "detail")


def cmd_audit_query(args: argparse.Namespace) -> None:
    entries = _read_audit(limit=getattr(args, "limit", 10))
    filters: dict[str, str] = {}
    for token in (getattr(args, "filter", "") or "").split(","):
        key, sep, value = token.partition("=")
        key = key.strip().lower()
        if sep and key in _AUDIT_FILTER_FIELDS and value:
            filters[key] = value.strip()
    filtered = [
        e
        for e in entries
        if all(needle in getattr(e, field, "") for field, needle in filters.items())
    ]
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "timestamp": e.timestamp.isoformat(),
                        "action": e.action,
                        "actor": e.actor,
                        "resource": e.resource,
                        "detail": e.detail,
                    }
                    for e in filtered
                ],
                indent=2,
                default=str,
            )
        )
        return
    if not filtered:
        print("No audit entries match the filter")
        return
    for e in filtered:
        print(f"  [{e.timestamp.isoformat()}] {e.action} by {e.actor} on {e.resource} ({e.detail})")


def cmd_risk(args: argparse.Namespace) -> None:
    orch = build_orchestrator(mode=getattr(args, "mode", "paper"))
    kill = orch.risk_service.kill_switch
    if args.risk_cmd == "status":
        verdict = orch.risk_service.can_trade([])
        acc = (
            orch.broker_reconciliation.can_accept_orders
            if orch.broker_reconciliation is not None
            else False
        )
        halted = not verdict.allowed
        data = {
            "kill_switch": "OPEN (trading halted)" if halted else "closed",
            "trading_halted": halted,
            "reason": verdict.reason,
            "orders_accepted": acc,
        }
        if args.json:
            print(json.dumps(data, indent=2, default=str))
            return
        print("Kill switch:", data["kill_switch"])
        print("Order acceptance (reconciled):", "allowed" if acc else "blocked")
        return
    if args.risk_cmd == "check":
        verdict = orch.risk_service.can_trade([])
        detail = verdict.reason or "no blocking conditions"
        print(f"Risk check: {'PASS' if verdict.allowed else 'FAIL'} ({detail})")
        sys.exit(0 if verdict.allowed else 1)
        return
    if args.risk_cmd == "reset":
        kill.reset()
        print("Kill switch reset (ADR-007 manual-reset semantics)")
        return
    if args.risk_cmd == "kill":
        kill.record_failure()
        print("Kill switch engaged (orders rejected until explicit reset)")
        return
    if args.risk_cmd == "reconcile":
        if orch.broker_reconciliation is None:
            print("Broker reconciliation not available")
            sys.exit(1)
        if getattr(args, "verb", None) == "status":
            accepted = orch.broker_reconciliation.can_accept_orders
            print("Reconciliation gate:", "allowed" if accepted else "blocked")
            sys.exit(0 if accepted else 1)
            return
        pending = _pending_from_broker(orch)
        res = orch.broker_reconciliation.reconcile(journal_pending=pending)
        accepted = orch.broker_reconciliation.can_accept_orders
        print(f"Reconciliation mismatches: {len(res.mismatches)}")
        print(f"Order acceptance: {'allowed' if accepted else 'blocked'}")
        sys.exit(0 if accepted else 1)
        return
    parser = build_parser()
    parser.parse_args([args.command, "--help"])


def _pending_from_broker(orch: Any) -> list[dict] | None:
    pending = getattr(orch.broker, "pending", None)
    return pending() if pending is not None else None


def cmd_metrics(args: argparse.Namespace) -> None:
    orch = build_orchestrator(mode=getattr(args, "mode", "paper"))
    if args.metrics_cmd == "snapshot":
        data = orch.metrics.snapshot()
        if args.json:
            print(json.dumps(data, indent=2, default=str))
            return
        print("Metrics snapshot:")
        for name, value in sorted(data.items()):
            print(f"  {name} = {value}")
        return
    if args.metrics_cmd == "watch":
        mids = orch.market_ids or []
        for _ in range(max(1, args.cycles)):
            for mid in mids:
                close = orch.data_ingestion.get_latest_close(mid) if orch.data_ingestion else None
                result = orch.run_cycle(mid, close if close else 100.0)
                print(
                    f"cycle={result.market_id} trades={result.trades} "
                    f"duration_ms={result.duration_ms:.0f} errors={len(result.errors)}"
                )
        return
    parser = build_parser()
    parser.parse_args(["metrics", "--help"])


def cmd_notify(args: argparse.Namespace) -> None:
    from traderos.domain.services.notification_service import NotificationLevel
    from traderos.domain.services.notification_service import NotificationService

    svc = NotificationService()
    level = NotificationLevel[args.level.upper()]
    svc.send(level, args.title, args.message)
    print(f"Sent {args.level} notification: {args.title}")


def cmd_signal(args: argparse.Namespace) -> None:
    cfg = Config.load()
    orch = build_orchestrator(mode="paper", config=cfg)
    if args.market_id:
        mids = [uuid.UUID(args.market_id)]
    else:
        mids = list(orch.market_ids) if orch.market_ids else []
    if not mids:
        print("No markets configured. Use --market-id or configure markets in config.")
        return
    if args.json:
        result = {}
        for mid in mids:
            signals = orch.signal_service.get_active_signals(mid)
            result[str(mid)] = [
                {
                    "direction": s.direction.name,
                    "confidence": s.confidence,
                    "expires_at": s.expires_at.isoformat(),
                }
                for s in signals
            ]
        print(json.dumps(result, indent=2, default=str))
        return
    for mid in mids:
        signals = orch.signal_service.get_active_signals(mid)
        if signals:
            print(f"Active signals for {mid}:")
            for s in signals:
                expires = s.expires_at.strftime("%H:%M")
                print(f"  {s.direction.name:>8}  conf={s.confidence:.2f}  expires={expires}")
        else:
            print(f"Active signals for {mid}: (none)")


def cmd_daemon(args: argparse.Namespace) -> None:
    cfg = Config.load()
    cfg.validate()
    orch = build_orchestrator(mode=args.mode, config=cfg)
    orch.run_forever(interval_seconds=args.interval)


def cmd_run(args: argparse.Namespace) -> None:
    cfg = Config.load()
    cfg.validate()
    orch = build_orchestrator(mode=args.mode, config=cfg)
    orch.run_forever(interval_seconds=args.interval)


def cmd_status(args: argparse.Namespace) -> None:
    orch = build_orchestrator(mode=getattr(args, "mode", "paper"))
    status = orch.get_status()
    verdict = orch.risk_service.can_trade([])
    acc = (
        orch.broker_reconciliation.can_accept_orders
        if orch.broker_reconciliation is not None
        else False
    )
    data: dict[str, Any] = {
        "mode": status.get("mode"),
        "running": status.get("running", False),
        "markets": status.get("markets", 0),
        "crash_recovered": status.get("crash_recovered", False),
        "trading_halted": not verdict.allowed,
        "reason": verdict.reason,
        "orders_accepted": acc,
        "health": status.get("health", {}),
    }
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return
    print(f"Mode: {data['mode']}  Running: {data['running']}  Markets: {data['markets']}")
    print(f"Crash recovered: {data['crash_recovered']}")
    print("Kill switch:", "OPEN (trading halted)" if data["trading_halted"] else "closed")
    print("Order acceptance (reconciled):", "allowed" if acc else "blocked")
    print("Health:")
    for name, healthy in sorted(data["health"].items()):
        print(f"  [{'PASS' if healthy else 'FAIL'}] {name}")


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = Config.load()
        cfg.validate()
        print(f"Configuration OK (mode={args.mode})")
        return 0
    except ConfigError as e:
        print(f"Configuration FAILED: {e}")
        return 1


def cmd_pilot(args: argparse.Namespace) -> None:
    """Controlled-pilot gates: readiness verification and dry-run rehearsal.

    ``readiness`` runs the live-readiness check (never places an order) and
    exits 0 only when every verifiable precondition passes.
    ``dry-run`` rehearses the operator workflow end to end with
    ``dry_run=True`` so live execution stays disabled throughout.
    """
    from traderos.application.factory import build_orchestrator
    from traderos.domain.services.operator_workflow import OperatorStep

    mode = getattr(args, "mode", "paper")
    orch = build_orchestrator(mode=mode)

    if args.pilot_cmd == "readiness":
        if orch.live_readiness is None:
            print("Live readiness service is not configured.")
            sys.exit(1)
        verdict = orch.live_readiness.check()
        if args.json:
            print(json.dumps(verdict.to_dict(), indent=2, default=str))
        else:
            print("Controlled-pilot readiness:")
            for name, ok in sorted(verdict.checks.items()):
                status = "PASS" if ok else "FAIL"
                print(f"  [{status}] {name}")
            for reason in verdict.reasons:
                print(f"  FAIL: {reason}")
            mode_line = (
                "LIVE execution ENABLED"
                if verdict.live_execution_enabled
                else "dry-run (live execution disabled)"
            )
            print(f"Verdict: {'READY' if verdict.ready else 'NOT READY'} ({mode_line})")
        sys.exit(0 if verdict.ready else 1)

    if args.pilot_cmd == "dry-run":
        if orch.operator_session is None:
            print("Operator workflow service is not configured.")
            sys.exit(1)
        session = orch.operator_session
        outcomes = []
        blocked = False
        while True:
            step = session.workflow.next_step()
            if step is None:
                break
            if step is OperatorStep.STRATEGY_PROMOTION:
                session.workflow.advance(
                    step, actor="pilot-dry-run", result="skipped (operator decision)"
                )
                if session.repository is not None:
                    session.repository.save(session.workflow)
                outcomes.append(
                    None if args.json else "SKIP: strategy promotion requires operator decision"
                )
                continue
            outcome = session.perform(step, actor="pilot-dry-run", dry_run=True)
            outcomes.append(outcome)
            if not args.json:
                status = "PASS" if outcome.ok else "FAIL"
                print(f"  [{status}] {step.value}: {outcome.result}")
            if not outcome.ok:
                blocked = True
                break
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "step": o.step.value if o else "strategy_promotion",
                            "ok": o.ok if o else None,
                            "result": o.result if o else "skipped (operator decision)",
                        }
                        for o in outcomes
                    ],
                    indent=2,
                )
            )
        sys.exit(0 if not blocked and all(o is None or o.ok for o in outcomes) else 1)


def cmd_security(args: argparse.Namespace) -> None:
    """Audit the deployment security posture.

    Reports authentication, TLS, CORS and secret-rotation state against the
    policy for the active environment (``TRADEROS_ENV``). Exits non-zero when
    the posture is insufficient; production runs fail closed.
    """
    from traderos.infrastructure.security_policy import check_security_posture

    report = check_security_posture()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"Security posture (environment: {report.environment}):")
        for finding in report.findings:
            status = "PASS" if finding.ok else "FAIL"
            print(f"  [{status}] {finding.check}: {finding.detail}")
        verdict = "SECURE" if report.all_ok else "INSUFFICIENT"
        print(f"Verdict: {verdict}")
    sys.exit(0 if report.all_ok else 1)


def cmd_db(args: argparse.Namespace) -> None:
    cfg = Config.load()
    conn = get_connection(cfg)
    try:
        if args.db_cmd == "migrate":
            migrate(conn)
            ver = get_current_version(conn)
            print(f"Migrations up to date. Schema version: {ver}")
        elif args.db_cmd == "rollback":
            migrate(conn, target_version=args.target)
            ver = get_current_version(conn)
            print(f"Rolled back to version {ver}")
        elif args.db_cmd == "check":
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                ver = get_current_version(conn)
                print(f"Database OK. Schema version: {ver}")
            except Exception as e:
                print(f"Database check FAILED: {e}")
        elif args.db_cmd == "backup":
            path = create_backup(cfg)
            print(f"Backup created: {path}")
        elif args.db_cmd == "restore":
            path = getattr(args, "backup_flag", None) or getattr(args, "backup", None)
            if getattr(args, "latest", False):
                backups = list_backups()
                if not backups:
                    print("No backups found.")
                    return
                path = backups[0]["path"]
            if not path:
                print("No backup specified. Use <path>, --backup <path>, or --latest.")
                sys.exit(1)
            result = restore_backup(path, cfg)
            if result:
                print(f"Database restored: {result}")
            else:
                print("Database restored.")
        elif args.db_cmd == "list-backups":
            backups = list_backups()
            if not backups:
                print("No backups found.")
                return
            for b in backups:
                print(f"  {b['path']}  ({b['size_bytes']} bytes, {b['modified']})")
    finally:
        if conn is not None:
            conn.close()
        close_all_pools()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "strategies":
        cmd_strategies(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "papertrade":
        cmd_paper(args)
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "audit":
        if args.audit_cmd == "verify":
            cmd_audit_verify(args)
        elif args.audit_cmd == "query":
            cmd_audit_query(args)
        else:
            cmd_audit(args)
    elif args.command == "notify":
        cmd_notify(args)
    elif args.command == "signal":
        cmd_signal(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "db":
        cmd_db(args)
    elif args.command == "validate":
        sys.exit(cmd_validate(args))
    elif args.command == "pilot":
        cmd_pilot(args)
    elif args.command == "risk":
        cmd_risk(args)
    elif args.command == "metrics":
        cmd_metrics(args)
    elif args.command == "security":
        cmd_security(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
