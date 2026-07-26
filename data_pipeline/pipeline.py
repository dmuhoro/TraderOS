import logging

from configs.config_loader import config
from data_pipeline.collectors import BinanceCollector
from data_pipeline.collectors import MockDataCollector
from data_pipeline.collectors import YFinanceCollector
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class DataPipeline:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.binance = BinanceCollector()
        self.yfinance = YFinanceCollector()
        self.mock = MockDataCollector()

    def run(self, symbols: list[str] | None = None):
        """Execute the data collection pipeline."""
        logger.info("Starting data collection pipeline...")

        if not symbols:
            forex = config.get("data_collection.forex_symbols", [])
            crypto = config.get("data_collection.crypto_symbols", [])
        else:
            # Simple heuristic to split symbols
            forex = [s for s in symbols if "=" in s or "X" in s]
            crypto = [s for s in symbols if "/" in s]

        days = config.get("data_collection.history_days", 30)

        # Process Forex
        for symbol in forex:
            try:
                df = self.yfinance.fetch_data(symbol, days)
                if not df.empty:
                    self.db.save_ohlc(df, symbol)
                    logger.info("Successfully collected %d rows for %s", len(df), symbol)
                else:
                    logger.warning("No data returned for %s, trying mock...", symbol)
                    df = self.mock.fetch_data(symbol, days)
                    self.db.save_ohlc(df, symbol)
            except (ValueError, ConnectionError) as e:
                logger.error("Pipeline error for %s: %s", symbol, e)

        # Process Crypto
        for symbol in crypto:
            try:
                df = self.binance.fetch_data(symbol, days)
                if not df.empty:
                    self.db.save_ohlc(df, symbol)
                    logger.info("Successfully collected %d rows for %s", len(df), symbol)
                else:
                    logger.warning("No data returned for %s, trying mock...", symbol)
                    df = self.mock.fetch_data(symbol, days)
                    self.db.save_ohlc(df, symbol)
            except (ValueError, ConnectionError) as e:
                logger.error("Pipeline error for %s: %s", symbol, e)

        logger.info("Data collection pipeline completed.")
