import logging

import pandas as pd

from analysis_engine.indicators import MarketAnalyzer
from configs.config_loader import config
from correlation_engine.engine import CorrelationEngine
from data_pipeline.pipeline import DataPipeline
from database.db_manager import DatabaseManager
from journal_engine.logger import JournalLogger
from liquidity_engine.breakout_detection import BreakoutDetector
from liquidity_engine.liquidity_zones import LiquidityZoneMapper
from liquidity_engine.session_analysis import SessionAnalyzer
from liquidity_engine.sweep_detection import SweepDetector
from liquidity_engine.swing_detection import SwingDetector
from visualization.charts import Visualizer
from visualization.liquidity_charts import LiquidityVisualizer

# Setup Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("MarketIntelligence")


def main():
    logger.info("Initializing Market Intelligence Platform...")

    # 1. Initialize Components
    db = DatabaseManager()
    pipeline = DataPipeline(db)
    corr_engine = CorrelationEngine(db)
    journal = JournalLogger(db)
    viz = Visualizer()
    liq_viz = LiquidityVisualizer()

    # Layer 4 Components
    swing_detector = SwingDetector(window=config.get("liquidity.swing_window", 5))
    zone_mapper = LiquidityZoneMapper(
        threshold=config.get("liquidity.sr_clustering_threshold", 0.002)
    )
    sweep_detector = SweepDetector()
    breakout_detector = BreakoutDetector(
        vol_threshold=config.get("liquidity.consolidation_vol_threshold", 0.001),
        sensitivity=config.get("liquidity.breakout_sensitivity", 2.0),
    )
    session_analyzer = SessionAnalyzer(sessions=config.get("liquidity.sessions", {}))

    # 2. Run Data Pipeline
    pipeline.run()

    # 3. Perform Analysis
    symbols = config.get("data_collection.forex_symbols", []) + config.get(
        "data_collection.crypto_symbols", []
    )

    logger.info("Analyzing market regimes...")
    for symbol in symbols:
        df = db.get_ohlc(symbol)
        if not df.empty:
            # --- Layer 2: Analysis Engine ---
            df = MarketAnalyzer.detect_regime(df)
            features_df = MarketAnalyzer.prepare_features_for_db(df)
            if not features_df.empty:
                db.save_features(features_df, symbol)

            latest = df.iloc[-1]
            print(f"\n--- {symbol} INSIGHT ---")
            print(f"Latest Price: {latest['close']:.4f}")
            print(f"Current Regime: {latest['regime']}")
            print(f"Volatility (Annualized): {latest['volatility']:.2%}")

            # Save chart
            viz.plot_price_with_regime(df.sort_values("timestamp"), symbol)

            # --- Layer 4: Liquidity Analysis ---
            logger.info("Running Liquidity Mapping for %s...", symbol)
            df = swing_detector.detect_swings(df)
            zones = zone_mapper.map_zones(df)
            sweeps = sweep_detector.detect_sweeps(df)
            df = breakout_detector.analyze(df)
            events = breakout_detector.get_events(df)

            # Persist Liquidity
            if zones:
                zones_df = pd.DataFrame(zones)
                zones_df["symbol"] = symbol
                zones_df["detected_at"] = df["timestamp"].iloc[-1]
                db.save_liquidity_zones(zones_df)

            if sweeps:
                sweeps_df = pd.DataFrame(sweeps)
                sweeps_df["symbol"] = symbol
                db.save_market_events(sweeps_df)

            if events:
                events_df = pd.DataFrame(events)
                events_df["symbol"] = symbol
                db.save_market_events(events_df)

            # Session Analysis
            session_stats = session_analyzer.compute_session_stats(df)
            if not session_stats.empty:
                session_stats["symbol"] = symbol
                db.save_session_stats(session_stats)

            print(f"Liquidity Zones: {len(zones)} detected")
            print(f"Liquidity Sweeps: {len(sweeps)} detected")
            print(f"Market Structure Events: {len(events)} detected")

            # Visualize Liquidity
            liq_viz.plot_liquidity_map(df, zones, sweeps, symbol)

    # 4. Correlation Analysis
    logger.info("Computing correlations...")
    corr_matrix = corr_engine.compute_correlations(symbols)
    top_corrs = corr_engine.get_top_correlations(corr_matrix)

    print("\n--- TOP CORRELATIONS ---")
    for c in top_corrs[:5]:
        print(f"{c['pair']}: {c['correlation']:.2f}")

    viz.plot_correlation_heatmap(corr_matrix)

    # 5. Log Automatic Observation
    top_pair = top_corrs[0]["pair"] if top_corrs else "N/A"
    journal.log_entry(
        content=(
            f"System run completed. Analyzed {len(symbols)} symbols."
            f" Top correlation: {top_pair}"
        ),
        category="System",
        tags="auto-run",
    )

    db.close()
    logger.info("Platform run completed successfully. Charts saved to 'exports/' directory.")


if __name__ == "__main__":
    main()
