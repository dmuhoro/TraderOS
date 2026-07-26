import pandas as pd

from database.db_manager import DatabaseManager


class CorrelationEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def compute_correlations(self, symbols: list[str], window: int = 50) -> pd.DataFrame:
        """Compute rolling correlations between a list of symbols."""
        data = {}
        timestamps = []
        for symbol in symbols:
            df = self.db.get_ohlc(symbol, limit=window * 2)
            if not df.empty:
                df = df.sort_values("timestamp")
                # Ensure timestamps are naive for pandas join
                df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
                data[symbol] = df.set_index("timestamp")["close"].pct_change()
                if not timestamps:
                    timestamps = df["timestamp"].tolist()

        returns_df = pd.DataFrame(data)
        corr_matrix = returns_df.corr()

        # Prepare for persistence
        corr_data = []
        latest_ts = timestamps[-1] if timestamps else None
        if latest_ts:
            for i in range(len(symbols)):
                for j in range(i + 1, len(symbols)):
                    corr_data.append(
                        {
                            "symbol_a": symbols[i],
                            "symbol_b": symbols[j],
                            "timestamp": latest_ts,
                            "correlation_value": corr_matrix.loc[symbols[i], symbols[j]],
                            "window_size": window,
                        }
                    )
            if corr_data:
                self.db.save_correlations(pd.DataFrame(corr_data))

        return corr_matrix

    def get_top_correlations(self, corr_matrix: pd.DataFrame) -> list[dict]:
        """Extract top correlations from the matrix."""
        corrs = []
        symbols = corr_matrix.columns
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                corrs.append(
                    {"pair": f"{symbols[i]}-{symbols[j]}", "correlation": corr_matrix.iloc[i, j]}
                )
        return sorted(corrs, key=lambda x: abs(x["correlation"]), reverse=True)
