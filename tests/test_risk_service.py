from __future__ import annotations

import uuid

from traderos.domain.entities import Position
from traderos.domain.services.risk_service import RiskService


class TestRiskService:
    def test_assess_trade(self) -> None:
        svc = RiskService()
        result = svc.assess_trade(
            price=100.0,
            confidence=0.7,
            atr=5.0,
            account_equity=10000.0,
        )
        assert 0 <= result.kelly_fraction <= 0.25
        assert result.suggested_stop_loss < 100.0
        assert result.suggested_take_profit > 100.0
        assert result.risk_per_unit > 0
        assert result.max_risk_amount == 200.0

    def test_assess_trade_zero_win_rate(self) -> None:
        svc = RiskService()
        result = svc.assess_trade(
            price=50.0,
            confidence=0.3,
            atr=2.0,
            account_equity=5000.0,
            win_rate=0.0,
        )
        assert result.kelly_fraction == 0.0

    def test_compute_var_empty(self) -> None:
        svc = RiskService()
        assert svc.compute_var([]) == 0.0

    def test_compute_var(self) -> None:
        svc = RiskService()
        mid = uuid.uuid4()
        positions = [
            Position(
                market_id=mid, quantity=10.0, entry_price=100.0, current_price=110.0, pnl=100.0
            ),
            Position(
                market_id=mid, quantity=5.0, entry_price=200.0, current_price=190.0, pnl=-50.0
            ),
        ]
        var = svc.compute_var(positions)
        assert var >= 0

    def test_compute_max_drawdown(self) -> None:
        svc = RiskService()
        curve = [100.0, 110.0, 90.0, 95.0, 80.0]
        dd = svc.compute_max_drawdown(curve)
        assert dd == 0.2727272727272727

    def test_check_concentration(self) -> None:
        svc = RiskService()
        mid = uuid.uuid4()
        positions = [
            Position(
                market_id=mid,
                quantity=100.0,
                entry_price=100.0,
                current_price=100.0,
                pnl=0.0,
            )
        ]
        risk = svc.check_concentration(positions)
        assert len(risk.concentration_risk) == 1
        assert risk.concentration_risk[0][1] == 1.0

    def test_enforce_limits(self) -> None:
        svc = RiskService()
        mid1 = uuid.uuid4()
        mid2 = uuid.uuid4()
        mid3 = uuid.uuid4()
        mid4 = uuid.uuid4()
        mid5 = uuid.uuid4()
        positions = [
            Position(
                market_id=mid1, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid2, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid3, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid4, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid5, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
        ]
        risk = svc.check_concentration(positions)
        assert svc.enforce_limits(positions[0], risk)

    def test_enforce_limits_violation(self) -> None:
        svc = RiskService()
        mid = uuid.uuid4()
        pos = Position(
            market_id=mid, quantity=10.0, entry_price=100.0, current_price=50.0, pnl=-500.0
        )
        risk = svc.check_concentration([pos])
        assert not svc.enforce_limits(pos, risk)
