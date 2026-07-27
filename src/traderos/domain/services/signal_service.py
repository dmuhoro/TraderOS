from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import NamedTuple

from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.repositories import SignalRepository
from traderos.domain.services.strategy_framework import SignalResult


class SignalProvenance(NamedTuple):
    signal: Signal
    strategy_name: str
    indicators_used: dict[str, float]


@dataclass
class SignalService:
    repo: SignalRepository

    def process_evaluation(
        self,
        market_id: uuid.UUID,
        strategy_id: uuid.UUID,
        strategy_name: str,
        result: SignalResult,
        indicators: dict[str, float],
        ttl_minutes: int = 60,
    ) -> SignalProvenance | None:
        direction = SignalDirection(result.direction)
        now = datetime.now(UTC)
        signal = Signal(
            market_id=market_id,
            strategy_id=strategy_id,
            direction=direction,
            confidence=result.confidence,
            generated_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        stored = self.repo.add(signal)
        return SignalProvenance(
            signal=stored,
            strategy_name=strategy_name,
            indicators_used=indicators,
        )

    def validate_signal(self, signal: Signal) -> bool:
        return signal.expires_at > datetime.now(UTC)

    def deduplicate(
        self,
        signals: list[Signal],
        policy: str = "highest_confidence",
    ) -> list[Signal]:
        if not signals:
            return []

        by_market: dict[uuid.UUID, list[Signal]] = {}
        for s in signals:
            by_market.setdefault(s.market_id, []).append(s)

        result: list[Signal] = []
        for grouped in by_market.values():
            if policy == "highest_confidence":
                best = max(grouped, key=lambda s: s.confidence)
            elif policy == "latest":
                best = max(grouped, key=lambda s: s.generated_at)
            else:
                result.extend(grouped)
                continue
            result.append(best)

        return result

    def get_active_signals(self, market_id: uuid.UUID) -> list[Signal]:
        all_active = self.repo.get_active(market_id)
        return [s for s in all_active if self.validate_signal(s)]

    def get_signals_for_strategy(
        self,
        strategy_id: uuid.UUID,
    ) -> list[Signal]:
        return self.repo.get_by_strategy(strategy_id)
