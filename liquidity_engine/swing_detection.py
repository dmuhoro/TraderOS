import pandas as pd
import numpy as np
from typing import List, Tuple

class SwingDetector:
    def __init__(self, window: int = 5):
        self.window = window

    def detect_swings(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect swing highs and lows based on a rolling window."""
        df = df.copy()
        df['swing_high'] = np.nan
        df['swing_low'] = np.nan

        for i in range(self.window, len(df) - self.window):
            # Swing High
            if all(df['high'].iloc[i] > df['high'].iloc[i-self.window:i]) and \
               all(df['high'].iloc[i] > df['high'].iloc[i+1:i+self.window+1]):
                df.at[df.index[i], 'swing_high'] = df['high'].iloc[i]

            # Swing Low
            if all(df['low'].iloc[i] < df['low'].iloc[i-self.window:i]) and \
               all(df['low'].iloc[i] < df['low'].iloc[i+1:i+self.window+1]):
                df.at[df.index[i], 'swing_low'] = df['low'].iloc[i]

        return df

    def get_recent_swings(self, df: pd.DataFrame, count: int = 5) -> Tuple[pd.Series, pd.Series]:
        highs = df[df['swing_high'].notna()]['swing_high'].tail(count)
        lows = df[df['swing_low'].notna()]['swing_low'].tail(count)
        return highs, lows
