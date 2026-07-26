import pandas as pd


class SessionAnalyzer:
    def __init__(self, sessions: dict[str, list[int]]):
        self.sessions = sessions

    def assign_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Assign session labels to each data point."""
        df = df.copy()
        df["session"] = "Other"

        for name, hours in self.sessions.items():
            start, end = hours
            if start < end:
                mask = (df["timestamp"].dt.hour >= start) & (df["timestamp"].dt.hour < end)
            else:  # Overnight sessions
                mask = (df["timestamp"].dt.hour >= start) | (df["timestamp"].dt.hour < end)
            df.loc[mask, "session"] = name

        return df

    def compute_session_stats(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute statistics per session per day."""
        df = self.assign_sessions(df)
        df["date"] = df["timestamp"].dt.date

        stats = (
            df.groupby(["date", "session"])
            .agg({"close": ["std", lambda x: x.max() - x.min()], "timestamp": "count"})
            .reset_index()
        )

        stats.columns = ["date", "session", "volatility", "range_size", "count"]
        return stats
