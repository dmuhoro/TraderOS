from __future__ import annotations

import math
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Candle


class SessionStats(NamedTuple):
    date: datetime
    session: str
    volatility: float
    range_size: float
    bar_count: int


class SessionAnalysisService:
    @staticmethod
    def assign_sessions(
        candles: list[Candle],
        sessions: dict[str, list[int]],
    ) -> dict[datetime, str]:
        result: dict[datetime, str] = {}
        for candle in candles:
            hour = candle.timestamp.hour
            assigned = "Other"
            for name, (start, end) in sessions.items():
                if start < end:
                    if start <= hour < end:
                        assigned = name
                        break
                else:
                    if hour >= start or hour < end:
                        assigned = name
                        break
            result[candle.timestamp] = assigned
        return result

    @staticmethod
    def compute_session_stats(
        candles: list[Candle],
        sessions: dict[str, list[int]],
    ) -> list[SessionStats]:
        assignments = SessionAnalysisService.assign_sessions(candles, sessions)

        groups: dict[tuple[datetime, str], list[Candle]] = {}
        for candle in candles:
            session_name = assignments.get(candle.timestamp, "Other")
            day = candle.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            key = (day, session_name)
            if key not in groups:
                groups[key] = []
            groups[key].append(candle)

        stats: list[SessionStats] = []
        for (day, session_name), group in groups.items():
            closes = [float(c.ohlcv.close) for c in group]
            mean = sum(closes) / len(closes)
            variance = sum((c - mean) ** 2 for c in closes) / len(closes)
            volatility = math.sqrt(variance)
            high = max(float(c.ohlcv.high) for c in group)
            low = min(float(c.ohlcv.low) for c in group)
            stats.append(
                SessionStats(
                    date=day,
                    session=session_name,
                    volatility=volatility,
                    range_size=high - low,
                    bar_count=len(group),
                )
            )

        return stats
