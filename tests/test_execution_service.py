from __future__ import annotations

import uuid

from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.execution_service import OrderStatus
from traderos.domain.services.execution_service import OrderType


class TestExecutionService:
    def test_create_market_order(self) -> None:
        svc = ExecutionService()
        order = svc.create_market_order(
            market_id=uuid.uuid4(),
            side="buy",
            quantity=10.0,
        )
        assert order.order_type == OrderType.MARKET
        assert order.status == OrderStatus.PENDING
        assert order.price is None

    def test_create_limit_order(self) -> None:
        svc = ExecutionService()
        order = svc.create_limit_order(
            market_id=uuid.uuid4(),
            side="sell",
            quantity=5.0,
            price=100.0,
        )
        assert order.order_type == OrderType.LIMIT
        assert order.price == 100.0

    def test_create_stop_order(self) -> None:
        svc = ExecutionService()
        order = svc.create_stop_order(
            market_id=uuid.uuid4(),
            side="buy",
            quantity=10.0,
            stop_price=105.0,
        )
        assert order.order_type == OrderType.STOP
        assert order.stop_price == 105.0

    def test_process_market_order(self) -> None:
        svc = ExecutionService(slippage_bps=0)
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        result = svc.process_market_order(order, 100.0)
        assert result.filled
        assert result.fill_price == 100.0
        assert result.status == OrderStatus.FILLED

    def test_process_market_order_slippage(self) -> None:
        svc = ExecutionService(slippage_bps=10)
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        result = svc.process_market_order(order, 100.0)
        assert result.fill_price == 100.0 * (1 + 10 / 10000)

    def test_process_limit_order_fills_when_price_hits(self) -> None:
        svc = ExecutionService()
        order = svc.create_limit_order(uuid.uuid4(), "buy", 10.0, 100.0)
        result = svc.process_limit_order(order, 99.0)
        assert result.filled
        assert result.fill_price == 100.0

    def test_process_limit_order_pends_when_price_not_hit(self) -> None:
        svc = ExecutionService()
        order = svc.create_limit_order(uuid.uuid4(), "buy", 10.0, 100.0)
        result = svc.process_limit_order(order, 101.0)
        assert not result.filled
        assert result.status == OrderStatus.PENDING

    def test_process_stop_order_triggers(self) -> None:
        svc = ExecutionService()
        order = svc.create_stop_order(uuid.uuid4(), "buy", 10.0, 105.0)
        result = svc.process_stop_order(order, 106.0)
        assert result.filled
        assert result.status == OrderStatus.FILLED

    def test_process_stop_order_not_triggered(self) -> None:
        svc = ExecutionService()
        order = svc.create_stop_order(uuid.uuid4(), "buy", 10.0, 105.0)
        result = svc.process_stop_order(order, 104.0)
        assert not result.filled
        assert result.status == OrderStatus.PENDING

    def test_cancel_order(self) -> None:
        svc = ExecutionService()
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        cancelled = svc.cancel_order(order)
        assert cancelled.status == OrderStatus.CANCELLED
