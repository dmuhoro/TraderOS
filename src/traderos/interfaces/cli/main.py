from __future__ import annotations

import argparse
import uuid
from datetime import UTC

from traderos.application.factory import build_orchestrator
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.health import HealthService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TraderOS Unified CLI")
    sub = parser.add_subparsers(dest="command")

    p_strat = sub.add_parser("strategies", help="List and inspect strategies")
    p_strat.add_argument("--name", help="Show details for a specific strategy")

    p_backtest = sub.add_parser("backtest", help="Run a backtest")
    p_backtest.add_argument("strategy", help="Strategy name")
    p_backtest.add_argument("--candles", type=int, default=50, help="Number of candles")

    p_paper = sub.add_parser("papertrade", help="Paper trading commands")
    p_paper_sub = p_paper.add_subparsers(dest="paper_cmd")
    p_paper_sub.add_parser("create", help="Create a new paper session")
    p_paper_sub.add_parser("list", help="List paper sessions")

    sub.add_parser("health", help="System health status")

    p_audit = sub.add_parser("audit", help="View audit trail")
    p_audit.add_argument("--limit", type=int, default=10, help="Number of entries")

    p_notify = sub.add_parser("notify", help="Send a test notification")
    p_notify.add_argument(
        "--level", default="info", choices=["info", "warning", "error", "critical"]
    )
    p_notify.add_argument("--title", default="Test Notification")
    p_notify.add_argument("--message", default="")

    p_regime = sub.add_parser("signal", help="Check active signals for a market")
    p_regime.add_argument("market_id", type=str, help="Market UUID")

    p_daemon = sub.add_parser("daemon", help="Run the trading daemon")
    p_daemon.add_argument("--interval", type=int, default=60, help="Cycle interval in seconds")
    p_daemon.add_argument("--mode", default="paper", choices=["paper", "live", "backtest"])

    return parser


def cmd_strategies(args: argparse.Namespace) -> None:
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
    from datetime import datetime
    from decimal import Decimal

    from traderos.domain.entities import OHLCV
    from traderos.domain.entities import Candle
    from traderos.domain.entities import Timeframe

    mid = uuid.uuid4()
    candles: list[Candle] = []
    for i in range(args.candles):
        candles.append(
            Candle(
                market_id=mid,
                ohlcv=OHLCV(
                    open=Decimal(str(100 + i)),
                    high=Decimal(str(101 + i)),
                    low=Decimal(str(99 + i)),
                    close=Decimal(str(100 + i)),
                    volume=Decimal(1000),
                ),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                timeframe=Timeframe.DAY_1,
            )
        )
    svc = BacktestingService(execution=ExecutionService())
    result, steps = svc.run(strat, candles, mid)
    m = result.metrics
    print(f"Total Return: {m.total_return:.4f}")
    print(f"Sharpe: {m.sharpe_ratio:.4f}")
    print(f"Max DD: {m.max_drawdown:.4f}")
    print(f"Win Rate: {m.win_rate:.4f}")
    print(f"Steps: {len(steps)}")


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
    svc.report_healthy("cli", "TraderOS CLI v0.3.0")
    print("System Health:")
    for name, healthy in svc.summary().items():
        status = "PASS" if healthy else "FAIL"
        print(f"  [{status}] {name}")


def cmd_audit(args: argparse.Namespace) -> None:
    svc = AuditService()
    entries = svc.get_entries(limit=args.limit)
    if not entries:
        print("No audit entries")
        return
    for e in entries:
        print(f"  [{e.timestamp.isoformat()}] {e.action} by {e.actor} on {e.resource}")


def cmd_notify(args: argparse.Namespace) -> None:
    from traderos.domain.services.notification_service import NotificationLevel
    from traderos.domain.services.notification_service import NotificationService

    svc = NotificationService()
    level = NotificationLevel[args.level.upper()]
    svc.send(level, args.title, args.message)
    print(f"Sent {args.level} notification: {args.title}")


def cmd_signal(args: argparse.Namespace) -> None:
    mid = uuid.UUID(args.market_id) if args.market_id else uuid.uuid4()
    print(f"Active signals for market {mid}: (none — requires running system)")


def cmd_daemon(args: argparse.Namespace) -> None:
    cfg = Config.load()
    orch = build_orchestrator(mode=args.mode, config=cfg)
    orch.run_forever(interval_seconds=args.interval)


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
        cmd_audit(args)
    elif args.command == "notify":
        cmd_notify(args)
    elif args.command == "signal":
        cmd_signal(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
