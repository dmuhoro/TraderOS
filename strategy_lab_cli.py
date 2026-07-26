import argparse

from backtesting.engine import BacktestEngine
from database.db_manager import DatabaseManager
from journal_engine.research_engine import ResearchEngine
from strategy_lab.strategies import registry


def main():
    parser = argparse.ArgumentParser(description="TraderOS Strategy Lab & Backtest CLI")
    subparsers = parser.add_subparsers(dest="command")

    # List Strategies
    subparsers.add_parser("list", help="List available strategies")

    # Run Backtest
    run_parser = subparsers.add_parser("run", help="Run a backtest")
    run_parser.add_argument("strategy", help="Strategy name")
    run_parser.add_argument("symbol", help="Market symbol")
    run_parser.add_argument("--hyp_id", type=int, help="Optional: Link to Hypothesis ID")

    args = parser.parse_args()
    db = DatabaseManager()
    bt_engine = BacktestEngine(db)
    research = ResearchEngine(db)

    if args.command == "list":
        strategies = registry.list_strategies()
        print("\n=== AVAILABLE STRATEGIES ===")
        for s in strategies:
            print(f"- {s}")

    elif args.command == "run":
        try:
            strategy = registry.get_strategy(args.strategy)
            df = db.get_ohlc(args.symbol)
            if df.empty:
                print(f"No data found for {args.symbol}")
                return

            print(f"Running backtest for {args.strategy} on {args.symbol}...")
            results = bt_engine.run_backtest(strategy, df)

            metrics = results["metrics"]
            print("\n=== BACKTEST RESULTS ===")
            for k, v in metrics.items():
                print(f"{k.replace('_', ' ').title()}: {v:.4f}")

            # Link to Knowledge Graph if hyp_id provided
            if args.hyp_id:
                # Find the latest backtest_id
                cursor = db.conn.cursor()
                cursor.execute("SELECT id FROM backtest_results ORDER BY id DESC LIMIT 1")
                bt_id = cursor.fetchone()[0]

                tid = research.create_test(
                    args.hyp_id,
                    {"strategy": args.strategy, "symbol": args.symbol},
                    backtest_id=bt_id,
                )
                research.record_result(tid, metrics)
                print(f"\nSuccessfully linked backtest to Hypothesis #{args.hyp_id}")

        except (ValueError, RuntimeError) as e:
            print(f"Error: {e}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
