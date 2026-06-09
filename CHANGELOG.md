# Changelog - TraderOS

## [0.2.0] - 2026-06-01
### Added
- **Strategy Lab:** New module for developing and registering trading strategies.
- **Starter Strategies:** Moving Average Trend, Volatility Breakout, and Mean Reversion.
- **Backtest Engine:** Historical replay system with commissions, spread assumptions, and equity curve generation.
- **Risk Engine:** Volatility-based position sizing, exposure limits, and kill switch framework.
- **Knowledge Graph Integration:** Backtest results can now be linked directly to research hypotheses.
- **Strategy Lab CLI:** Command-line interface for running backtests and managing strategies.

### Fixed
- Timezone mismatch in correlation engine.
- Session statistics database schema synchronization.
