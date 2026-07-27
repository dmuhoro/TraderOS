import argparse

from traderos.domain.services.strategy_framework import registry as new_registry


def main():
    parser = argparse.ArgumentParser(description="TraderOS Strategy Lab & Backtest CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="List available strategies")

    run_parser = subparsers.add_parser("run", help="Run a backtest")
    run_parser.add_argument("strategy", help="Strategy name")
    run_parser.add_argument("symbol", help="Market symbol")
    run_parser.add_argument("--hyp_id", type=int, help="Optional: Link to Hypothesis ID")

    args = parser.parse_args()

    if args.command == "list":
        strategies = new_registry.list()
        print("\n=== AVAILABLE STRATEGIES ===")
        for s in strategies:
            print(f"- {s}")
        print("\nNote: Use the unified `traderos` CLI for backtesting with real data.")

    elif args.command == "run":
        print("Backtest execution is not yet implemented in the strategy lab CLI.")
        print("Use the unified `traderos backtest` command or the API `/backtest` endpoint.")
        print(f"Strategy: {args.strategy}")
        print(f"Symbol: {args.symbol}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
