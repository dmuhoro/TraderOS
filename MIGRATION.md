# TraderOS v0.2.0 Migration Guide

## New Modules
- `strategy_lab/`: Strategy development and registry.
- `backtesting/`: Historical simulation engine.
- `risk_engine/`: Portfolio risk and position sizing.

## Database Updates
The following tables have been added:
- `strategies`: Tracks registered strategy metadata.
- `backtest_results`: Stores performance metrics and equity curves.
- `risk_limits`: Configurable portfolio safety parameters.

## Research Workflow Update
Backtest results can now be linked to the Knowledge Graph. When running a backtest via the CLI, use the `--hyp_id` flag to attach the results to a specific hypothesis.

Example:
```bash
python3.11 strategy_lab_cli.py run MovingAverageTrend BTC/USDT --hyp_id 1
```
