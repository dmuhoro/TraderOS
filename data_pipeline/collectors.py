import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Optional
import logging
import yfinance as yf
import ccxt
import time

logger = logging.getLogger(__name__)

class BaseCollector:
    def fetch_data(self, symbol: str, days: int) -> pd.DataFrame:
        raise NotImplementedError

class BinanceCollector(BaseCollector):
    """Real Crypto data collector using ccxt (Binance)."""
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })

    def fetch_data(self, symbol: str, days: int = 30) -> pd.DataFrame:
        # Binance uses 'BTC/USDT', but often users provide 'BTCUSDT'
        if "/" not in symbol and len(symbol) > 5:
            symbol = f"{symbol[:3]}/{symbol[3:]}"
        logger.info(f"Fetching Binance data for {symbol} (last {days} days)")
        try:
            since = self.exchange.parse8601((datetime.now() - timedelta(days=days)).isoformat())
            all_ohlcv = []
            while since < self.exchange.milliseconds():
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1h', since=since)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + 1
                time.sleep(self.exchange.rateLimit / 1000)
            
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching Binance data for {symbol}: {e}")
            return pd.DataFrame()

class YFinanceCollector(BaseCollector):
    """Real Forex/Indices data collector using yfinance."""
    def fetch_data(self, symbol: str, days: int = 30) -> pd.DataFrame:
        logger.info(f"Fetching yfinance data for {symbol} (last {days} days)")
        try:
            # yfinance uses symbols like 'EURUSD=X' for Forex
            yf_symbol = f"{symbol}=X" if len(symbol) == 6 else symbol
            logger.info(f"Using yfinance symbol: {yf_symbol}")
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period=f"{days}d", interval="1h")
            if df.empty:
                return pd.DataFrame()
            
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            # Standardize columns
            df = df.rename(columns={'datetime': 'timestamp', 'date': 'timestamp'})
            return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        except Exception as e:
            logger.error(f"Error fetching yfinance data for {symbol}: {e}")
            return pd.DataFrame()

class MockDataCollector(BaseCollector):
    """Fallback mock collector."""
    def fetch_data(self, symbol: str, days: int = 30) -> pd.DataFrame:
        logger.info(f"Generating mock data for {symbol}")
        periods = days * 24
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        timestamps = pd.date_range(start=start_date, periods=periods, freq='h')
        np.random.seed(hash(symbol) % 2**32)
        returns = np.random.normal(0, 0.001, size=periods)
        price = 100 * (1 + returns).cumsum()
        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': price * (1 + np.random.normal(0, 0.0005, periods)),
            'high': price * (1 + abs(np.random.normal(0, 0.001, periods))),
            'low': price * (1 - abs(np.random.normal(0, 0.001, periods))),
            'close': price,
            'volume': np.random.uniform(100, 1000, periods)
        })
        df['high'] = df[['open', 'close', 'high']].max(axis=1)
        df['low'] = df[['open', 'close', 'low']].min(axis=1)
        return df
