import pandas as pd
import numpy as np
from typing import Dict, List
import json
from database.db_manager import DatabaseManager

class BacktestEngine:
    def __init__(self, db_manager: DatabaseManager, initial_capital: float = 100000.0):
        self.db = db_manager
        self.initial_capital = initial_capital
        self.commission = 0.001 # 0.1% commission
        self.spread = 0.0001   # Fixed spread assumption

    def run_backtest(self, strategy, df: pd.DataFrame) -> Dict:
        """Run a historical backtest on a single symbol."""
        if df.empty:
            return {}

        df = strategy.generate_signal(df)
        df = df.sort_values('timestamp')
        
        capital = self.initial_capital
        position = 0
        equity_curve = []
        trades = []
        
        for i in range(1, len(df)):
            current_row = df.iloc[i]
            prev_row = df.iloc[i-1]
            
            # Simple Backtest Logic
            signal = prev_row['signal']
            price = current_row['open']
            
            # Exit previous position if signal changes or opposite
            if position != 0 and signal != position:
                # Close position
                exit_price = price * (1 - self.spread) if position == 1 else price * (1 + self.spread)
                pnl = (exit_price - entry_price) * position
                capital += pnl - (exit_price * self.commission)
                trades.append({'exit_price': exit_price, 'pnl': pnl})
                position = 0
            
            # Enter new position
            if position == 0 and signal != 0:
                position = signal
                entry_price = price * (1 + self.spread) if position == 1 else price * (1 - self.spread)
                capital -= (entry_price * self.commission)
            
            # Track equity
            current_equity = capital
            if position != 0:
                unrealized_pnl = (current_row['close'] - entry_price) * position
                current_equity += unrealized_pnl
            
            equity_curve.append({
                'timestamp': current_row['timestamp'].isoformat(),
                'equity': current_equity
            })

        metrics = self._calculate_metrics(equity_curve, trades)
        
        # Persist results
        self._save_results(strategy.name, df['symbol'].iloc[0], df['timestamp'].iloc[0], df['timestamp'].iloc[-1], metrics, equity_curve)
        
        return {
            'metrics': metrics,
            'equity_curve': equity_curve
        }

    def _calculate_metrics(self, equity_curve: List[Dict], trades: List[Dict]) -> Dict:
        if not equity_curve:
            return {}
            
        equities = [e['equity'] for e in equity_curve]
        returns = pd.Series(equities).pct_change().dropna()
        
        total_return = (equities[-1] - self.initial_capital) / self.initial_capital
        win_rate = len([t for t in trades if t['pnl'] > 0]) / len(trades) if trades else 0
        
        # Max Drawdown
        peak = pd.Series(equities).expanding().max()
        drawdown = (pd.Series(equities) - peak) / peak
        max_drawdown = drawdown.min()
        
        # Sharpe Ratio (Approximate)
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 24)) if not returns.empty and returns.std() != 0 else 0
        
        return {
            'total_return': float(total_return),
            'win_rate': float(win_rate),
            'max_drawdown': float(max_drawdown),
            'sharpe_ratio': float(sharpe),
            'total_trades': len(trades)
        }

    def _save_results(self, strategy_name, symbol, start, end, metrics, equity_curve):
        cursor = self.db.conn.cursor()
        # Get strategy ID
        cursor.execute("SELECT id FROM strategies WHERE name = ?", (strategy_name,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO strategies (name) VALUES (?)", (strategy_name,))
            strategy_id = cursor.lastrowid
        else:
            strategy_id = row[0]
            
        cursor.execute('''
            INSERT INTO backtest_results (strategy_id, symbol, start_date, end_date, metrics_json, equity_curve_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (strategy_id, symbol, str(start), str(end), json.dumps(metrics), json.dumps(equity_curve)))
        self.db.conn.commit()
