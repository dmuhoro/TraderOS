import pandas as pd


class SweepDetector:
    def detect_sweeps(self, df: pd.DataFrame) -> list[dict]:
        """
        Detect liquidity sweeps:
        - Price breaks previous swing high/low but closes back within.
        """
        sweeps = []
        if "swing_high" not in df.columns or "swing_low" not in df.columns:
            return sweeps

        # Get all swing points
        swings: pd.DataFrame = df.loc[(df["swing_high"].notna()) | (df["swing_low"].notna())]

        for i in range(1, len(df)):
            current_row = df.iloc[i]
            recent_swings: pd.DataFrame = swings.loc[swings["timestamp"] < current_row["timestamp"]]

            if recent_swings.empty:
                continue

            last_high = (
                recent_swings["swing_high"].dropna().iloc[-1]
                if not recent_swings["swing_high"].dropna().empty
                else None
            )
            last_low = (
                recent_swings["swing_low"].dropna().iloc[-1]
                if not recent_swings["swing_low"].dropna().empty
                else None
            )

            # Bullish Sweep (Liquidity Grab below Low)
            if last_low and current_row["low"] < last_low and current_row["close"] > last_low:
                sweeps.append(
                    {
                        "timestamp": current_row["timestamp"],
                        "event_type": "Liquidity Sweep (Bullish)",
                        "description": (
                            f"Price swept below previous low {last_low:.4f} and rejected."
                        ),
                    }
                )

            # Bearish Sweep (Liquidity Grab above High)
            if last_high and current_row["high"] > last_high and current_row["close"] < last_high:
                sweeps.append(
                    {
                        "timestamp": current_row["timestamp"],
                        "event_type": "Liquidity Sweep (Bearish)",
                        "description": (
                            f"Price swept above previous high {last_high:.4f} and rejected."
                        ),
                    }
                )

        return sweeps
