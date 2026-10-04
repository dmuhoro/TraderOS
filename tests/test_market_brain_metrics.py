from __future__ import annotations

import uuid

from traderos.domain.services.market_brain_service import MarketBrainService


def test_unknown_snapshot_does_not_claim_measured_zero_volatility_percentile() -> None:
    brain = MarketBrainService()
    market_id = uuid.uuid4()

    snapshot = brain.snapshot(market_id)

    assert snapshot.known is False
    assert snapshot.volatility_percentile is None
    advice = brain.advise(market_id)
    assert advice.allowed is False
    assert advice.moves == []
    assert "insufficient data" in advice.reason
