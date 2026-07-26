from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from datetime import UTC

from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_framework import SignalResult
from traderos.infrastructure.repositories.in_memory import InMemorySignalRepository


class TestSignalService:
    def test_process_evaluation_creates_signal(self) -> None:
        repo = InMemorySignalRepository()
        svc = SignalService(repo)
        market_id = uuid.uuid4()
        strategy_id = uuid.uuid4()
        result = SignalResult(direction="long", confidence=0.85, metadata={"sma": 100.0})
        provenance = svc.process_evaluation(
            market_id,
            strategy_id,
            "TestStrat",
            result,
            {"sma": 100.0},
        )
        assert provenance is not None
        assert provenance.signal.direction == SignalDirection.LONG
        assert provenance.signal.confidence == 0.85
        assert provenance.signal.expires_at > provenance.signal.generated_at
        assert provenance.strategy_name == "TestStrat"
        assert provenance.indicators_used == {"sma": 100.0}

    def test_validate_signal_expired(self) -> None:
        repo = InMemorySignalRepository()
        svc = SignalService(repo)
        old = datetime(2020, 1, 1, tzinfo=UTC)
        signal = Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.5,
            generated_at=old,
            expires_at=old + timedelta(minutes=1),
        )
        assert not svc.validate_signal(signal)

    def test_validate_signal_active(self) -> None:
        repo = InMemorySignalRepository()
        svc = SignalService(repo)
        now = datetime.now(UTC)
        signal = Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.5,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        assert svc.validate_signal(signal)

    def test_deduplicate_highest_confidence(self) -> None:
        repo = InMemorySignalRepository()
        svc = SignalService(repo)
        mid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(UTC)
        s1 = Signal(
            market_id=mid,
            strategy_id=sid,
            direction=SignalDirection.LONG,
            confidence=0.5,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        s2 = Signal(
            market_id=mid,
            strategy_id=sid,
            direction=SignalDirection.SHORT,
            confidence=0.9,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        result = svc.deduplicate([s1, s2], "highest_confidence")
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_get_active_signals_filters_expired(self) -> None:
        repo = InMemorySignalRepository()
        svc = SignalService(repo)
        mid = uuid.uuid4()
        now = datetime.now(UTC)
        active = Signal(
            market_id=mid,
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.5,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        expired = Signal(
            market_id=mid,
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.3,
            generated_at=datetime(2020, 1, 1, tzinfo=UTC),
            expires_at=datetime(2020, 1, 2, tzinfo=UTC),
        )
        repo.add(active)
        repo.add(expired)
        signals = svc.get_active_signals(mid)
        assert len(signals) == 1
        assert signals[0].confidence == 0.5

    def test_get_signals_for_strategy(self) -> None:
        repo = InMemorySignalRepository()
        svc = SignalService(repo)
        sid = uuid.uuid4()
        now = datetime.now(UTC)
        s1 = Signal(
            market_id=uuid.uuid4(),
            strategy_id=sid,
            direction=SignalDirection.LONG,
            confidence=0.5,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        s2 = Signal(
            market_id=uuid.uuid4(),
            strategy_id=sid,
            direction=SignalDirection.SHORT,
            confidence=0.7,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        s3 = Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.NEUTRAL,
            confidence=0.9,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        for s in (s1, s2, s3):
            repo.add(s)
        result = svc.get_signals_for_strategy(sid)
        assert len(result) == 2
