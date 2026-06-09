import pandas as pd
import numpy as np
from typing import Dict

class MarketAnalyzer:
    @staticmethod
    def compute_moving_averages(df: pd.DataFrame, windows=[20, 50, 200]) -> pd.DataFrame:
        for window in windows:
            df[f'sma_{window}'] = df['close'].rolling(window=window).mean()
        return df

    @staticmethod
    def compute_volatility(df: pd.DataFrame, window=14) -> pd.DataFrame:
        # Standard deviation of returns
        df['returns'] = df['close'].pct_change()
        df['volatility'] = df['returns'].rolling(window=window).std() * np.sqrt(252 * 24) # Annualized
        
        # ATR - Average True Range
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(window=window).mean()
        
        return df

    @staticmethod
    def detect_regime(df: pd.DataFrame) -> pd.DataFrame:
        """
        Classify market regime:
        - Trending Bullish: Price > SMA 50 > SMA 200
        - Trending Bearish: Price < SMA 50 < SMA 200
        - Ranging: Price between SMA 50 and SMA 200
        - High Volatility: Volatility > rolling mean of volatility
        """
        df = MarketAnalyzer.compute_moving_averages(df)
        df = MarketAnalyzer.compute_volatility(df)
        
        def classify(row):
            if pd.isna(row['sma_200']):
                return "Unknown"
            
            trend = "Ranging"
            if row['close'] > row['sma_50'] > row['sma_200']:
                trend = "Trending Bullish"
            elif row['close'] < row['sma_50'] < row['sma_200']:
                trend = "Trending Bearish"
            
            vol_mean = df['volatility'].mean()
            vol_status = "High Vol" if row['volatility'] > vol_mean else "Low Vol"
            
            return f"{trend} ({vol_status})"

        df['regime'] = df.apply(classify, axis=1)
        return df

    @staticmethod
    def prepare_features_for_db(df: pd.DataFrame) -> pd.DataFrame:
        """Format features for the database."""
        # We'll store regime, volatility, and atr as features
        melted = []
        for feature in ['regime', 'volatility', 'atr']:
            if feature in df.columns:
                temp = df[['timestamp', feature]].copy()
                temp['feature_name'] = feature
                temp['feature_value'] = temp[feature]
                melted.append(temp[['timestamp', 'feature_name', 'feature_value']])
        
        if not melted:
            return pd.DataFrame()
        return pd.concat(melted)
