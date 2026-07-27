import argparse

from traderos.domain.services.strategy_framework import registry as new_registry


def main():
    parser = argparse.ArgumentParser(description="TraderOS Strategy Lab CLI (deprecated)")
    parser.parse_args()
    strategies = new_registry.list()
    print("\n=== AVAILABLE STRATEGIES ===")
    for s in strategies:
        print(f"- {s}")
    print("\nUse the unified `traderos` CLI for backtesting and paper trading.")


if __name__ == "__main__":
    main()
