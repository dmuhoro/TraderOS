import pandas as pd


class BreakoutDetector:
    def __init__(self, vol_threshold: float = 0.001, sensitivity: float = 2.0):
        self.vol_threshold = vol_threshold
        self.sensitivity = sensitivity

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect consolidation and breakout zones."""
        df = df.copy()

        # Consolidation: Low volatility and tight range
        df["vol_std"] = df["close"].pct_change().rolling(window=10).std()
        df["is_consolidating"] = df["vol_std"] < self.vol_threshold

        # Breakout: Volatility spike + range expansion
        df["vol_ma"] = df["vol_std"].rolling(window=20).mean()
        df["is_breaking_out"] = (df["vol_std"] > df["vol_ma"] * self.sensitivity) & (
            ~df["is_consolidating"].shift(1).fillna(False)
        )

        return df

    def get_events(self, df: pd.DataFrame) -> list[dict]:
        events = []
        for i in range(1, len(df)):
            if df["is_breaking_out"].iloc[i] and not df["is_breaking_out"].iloc[i - 1]:
                events.append(
                    {
                        "timestamp": df["timestamp"].iloc[i],
                        "event_type": "Breakout",
                        "description": f"Volatility breakout detected at {df['close'].iloc[i]:.4f}",
                    }
                )
            elif df["is_consolidating"].iloc[i] and not df["is_consolidating"].iloc[i - 1]:
                events.append(
                    {
                        "timestamp": df["timestamp"].iloc[i],
                        "event_type": "Consolidation",
                        "description": "Market entering consolidation phase.",
                    }
                )
        return events
