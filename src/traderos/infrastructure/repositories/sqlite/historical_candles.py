from __future__ import annotations

from typing import Any

_COLUMNS = ("source", "symbol", "timeframe", "ts", "open", "high", "low", "close", "volume")


class SQLiteHistoricalCandleRepository:
    """Durable provider candle store keyed by (source, symbol, timeframe, ts).

    ``ts`` is epoch-seconds so the key is provider/timeframe-aggregation
    independent; rows are upserted idempotently so repeated backtest runs reuse
    the same trusted bar instead of refetching the wire.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def _row(self, row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        return {c: row[c] for c in _COLUMNS}

    def upsert(self, source: str, symbol: str, timeframe: str, rows: list[dict[str, Any]]) -> None:
        sql = (
            "INSERT INTO historical_candles (source, symbol, timeframe, ts, "
            "open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source, symbol, timeframe, ts) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume"
        )
        for r in rows:
            self.conn.execute(
                sql,
                (
                    source,
                    symbol,
                    timeframe,
                    int(r["ts"]),
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    float(r["volume"]),
                ),
            )
        self.conn.commit()

    def load(
        self,
        source: str,
        symbol: str,
        timeframe: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT source, symbol, timeframe, ts, open, high, low, close, volume "
            "FROM historical_candles WHERE source=? AND symbol=?"
        )
        params: list[Any] = [source, symbol]
        if timeframe is not None:
            sql += " AND timeframe=?"
            params.append(timeframe)
        if start_ts is not None:
            sql += " AND ts>=?"
            params.append(int(start_ts))
        if end_ts is not None:
            sql += " AND ts<=?"
            params.append(int(end_ts))
        sql += " ORDER BY ts ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def count(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> int:
        sql = (
            "SELECT COUNT(*) FROM historical_candles " "WHERE source=? AND symbol=? AND timeframe=?"
        )
        params: list[Any] = [source, symbol, timeframe]
        if start_ts is not None:
            sql += " AND ts>=?"
            params.append(int(start_ts))
        if end_ts is not None:
            sql += " AND ts<=?"
            params.append(int(end_ts))
        row = self.conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def last_ts(self, source: str, symbol: str, timeframe: str) -> int | None:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(ts), 0) FROM historical_candles "
            "WHERE source=? AND symbol=? AND timeframe=?",
            (source, symbol, timeframe),
        ).fetchone()
        val = int(row[0]) if row else 0
        return val if val > 0 else None
