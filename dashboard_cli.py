import argparse
from database.db_manager import DatabaseManager
from tabulate import tabulate
import json

def main():
    parser = argparse.ArgumentParser(description="TraderOS Dashboard CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Strategy Leaderboard
    subparsers.add_parser("leaderboard", help="Show strategy performance leaderboard")

    # Risk Summary
    subparsers.add_parser("risk", help="Show current portfolio risk summary")

    args = parser.parse_args()
    db = DatabaseManager()

    if args.command == "leaderboard":
        cursor = db.conn.cursor()
        query = """
            SELECT s.name, b.symbol, b.metrics_json, b.timestamp
            FROM backtest_results b
            JOIN strategies s ON b.strategy_id = s.id
            ORDER BY b.timestamp DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        leaderboard_data = []
        for row in rows:
            name, symbol, metrics_json, ts = row
            metrics = json.loads(metrics_json)
            leaderboard_data.append([
                name, 
                symbol, 
                f"{metrics.get('total_return', 0):.2%}", 
                f"{metrics.get('win_rate', 0):.2%}", 
                f"{metrics.get('sharpe_ratio', 0):.2f}",
                ts
            ])
        
        print("\n=== STRATEGY LEADERBOARD ===")
        print(tabulate(leaderboard_data, headers=["Strategy", "Symbol", "Return", "Win Rate", "Sharpe", "Last Run"]))

    elif args.command == "risk":
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM risk_limits WHERE is_active = 1 LIMIT 1")
        limits = cursor.fetchone()
        
        print("\n=== RISK DASHBOARD ===")
        if limits:
            print(f"Max Drawdown Limit:    {limits[1]:.2%}")
            print(f"Max Position Size:     {limits[2]:.2%}")
            print(f"Max Correlation:       {limits[3]:.2%}")
        else:
            print("No active risk limits configured.")
            
        # Add summary of latest correlations
        cursor.execute("SELECT symbol_a, symbol_b, correlation_value FROM correlations ORDER BY timestamp DESC LIMIT 5")
        corrs = cursor.fetchall()
        if corrs:
            print("\n--- Latest Market Correlations ---")
            print(tabulate(corrs, headers=["Asset A", "Asset B", "Corr"]))

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
