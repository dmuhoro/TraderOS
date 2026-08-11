from __future__ import annotations

import uuid

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository


class TestPortfolioService:
    def test_get_summary(self) -> None:
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(trade_repo, pos_repo)
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=110.0,
            pnl=100.0,
        )
        pos_repo.add(pos)
        summary = svc.get_summary(cash=5000.0)
        assert summary.total_equity == 5000.0 + 10.0 * 110.0
        assert summary.cash == 5000.0
        assert summary.total_pnl == 100.0
        assert summary.position_count == 1

    def test_size_position_returns_shares(self) -> None:
        svc = PortfolioService(InMemoryTradeRepository(), InMemoryPositionRepository())
        cash = 10000.0
        price = 100.0
        allocation = min(0.02 * 0.8 * 10, 0.25)
        size = svc.size_position(cash=cash, confidence=0.8, price=price)
        assert size == round(cash * allocation / price, 8)
        assert svc.size_position(cash=cash, confidence=0.8, price=0.0) == 0.0
        assert svc.size_position(cash=cash, confidence=0.0, price=price) == 0.0

    def test_compute_pnl(self) -> None:
        svc = PortfolioService(InMemoryTradeRepository(), InMemoryPositionRepository())
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=110.0,
            pnl=0.0,
        )
        pnl = svc.compute_pnl(pos, 120.0)
        assert pnl == 10.0 * (120.0 - 100.0)

    def test_update_position(self) -> None:
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(InMemoryTradeRepository(), pos_repo)
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=100.0,
            pnl=0.0,
        )
        pos_repo.add(pos)
        updated = svc.update_position(pos, 110.0)
        assert updated.current_price == 110.0
        assert updated.pnl == 100.0
        stored = pos_repo.get_by_market(pos.market_id)
        assert stored is not None
        assert stored.current_price == 110.0

    def test_open_trade(self) -> None:
        trade_repo = InMemoryTradeRepository()
        svc = PortfolioService(trade_repo, InMemoryPositionRepository())
        trade = svc.open_trade(
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=10.0,
            price=100.0,
        )
        assert trade.status == TradeStatus.PENDING
        assert trade.side == TradeSide.BUY
        assert trade.quantity == 10.0

    def test_fill_trade_creates_position(self) -> None:
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(trade_repo, pos_repo)
        mid = uuid.uuid4()
        trade = Trade(
            signal_id=uuid.uuid4(),
            market_id=mid,
            side=TradeSide.BUY,
            quantity=10.0,
            price=100.0,
            status=TradeStatus.PENDING,
        )
        trade_repo.add(trade)
        filled = svc.fill_trade(trade, 101.0)
        assert filled.status == TradeStatus.FILLED
        assert filled.filled_price == 101.0
        pos = pos_repo.get_by_market(mid)
        assert pos is not None
        assert pos.quantity == 10.0

    def test_fill_trade_updates_position(self) -> None:
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(trade_repo, pos_repo)
        mid = uuid.uuid4()
        pos_repo.add(
            Position(
                market_id=mid,
                quantity=10.0,
                entry_price=100.0,
                current_price=100.0,
                pnl=0.0,
            )
        )
        trade = Trade(
            signal_id=uuid.uuid4(),
            market_id=mid,
            side=TradeSide.BUY,
            quantity=5.0,
            price=110.0,
            status=TradeStatus.PENDING,
        )
        trade_repo.add(trade)
        svc.fill_trade(trade)
        pos = pos_repo.get_by_market(mid)
        assert pos is not None
        assert pos.quantity == 15.0

    def test_rebalance(self) -> None:
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(trade_repo, pos_repo)
        mid1 = uuid.uuid4()
        mid2 = uuid.uuid4()
        pos_repo.add(
            Position(
                market_id=mid1,
                quantity=10.0,
                entry_price=100.0,
                current_price=100.0,
                pnl=0.0,
            )
        )
        pos_repo.add(
            Position(
                market_id=mid2,
                quantity=0.0,
                entry_price=0.0,
                current_price=50.0,
                pnl=0.0,
            )
        )
        prices = {mid1: 100.0, mid2: 50.0}
        trades = svc.rebalance(
            target_allocations={mid1: 0.6, mid2: 0.4},
            cash=5000.0,
            market_prices=prices,
        )
        assert len(trades) > 0

    def test_close_position_records_audit(self) -> None:
        from unittest.mock import Mock

        pos_repo = InMemoryPositionRepository()
        audit = Mock()
        svc = PortfolioService(InMemoryTradeRepository(), pos_repo, audit=audit)
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=100.0,
            pnl=0.0,
        )
        pos_repo.add(pos)
        realized = svc.close_position(pos, 110.0)
        assert realized == 100.0
        audit.record.assert_called_once()
        assert audit.record.call_args[0][0] == "position.close"

    def test_close_position_without_audit(self) -> None:
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(InMemoryTradeRepository(), pos_repo)
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=100.0,
            pnl=0.0,
        )
        pos_repo.add(pos)
        assert svc.close_position(pos, 90.0) == -100.0

    def test_close_position_records_realized_pnl(self) -> None:
        from unittest.mock import Mock

        pos_repo = InMemoryPositionRepository()
        risk_service = Mock()
        svc = PortfolioService(InMemoryTradeRepository(), pos_repo, risk_service=risk_service)
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=100.0,
            pnl=0.0,
        )
        pos_repo.add(pos)
        svc.close_position(pos, 110.0)
        risk_service.record_realized_pnl.assert_called_once_with(100.0)

    def test_rebalance_skips_negligible_diff(self) -> None:
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(trade_repo, pos_repo)
        mid = uuid.uuid4()
        pos_repo.add(
            Position(
                market_id=mid,
                quantity=10.0,
                entry_price=100.0,
                current_price=100.0,
                pnl=0.0,
            )
        )
        trades = svc.rebalance(
            target_allocations={mid: 1.0},
            cash=0.0,
            market_prices={mid: 100.0},
        )
        assert trades == []
