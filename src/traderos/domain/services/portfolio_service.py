from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import NamedTuple

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.domain.ports import AuditPort
from traderos.domain.repositories import TradeRepository
from traderos.domain.repositories.trade_repository import PositionRepository


class PortfolioSummary(NamedTuple):
    total_equity: float
    cash: float
    positions_value: float
    open_positions: list[Position]
    total_pnl: float
    position_count: int


@dataclass
class PortfolioService:
    trade_repo: TradeRepository
    position_repo: PositionRepository
    audit: AuditPort | None = None

    def get_summary(self, cash: float) -> PortfolioSummary:
        positions = self.position_repo.list_open()
        positions_value = sum(p.quantity * p.current_price for p in positions)
        total_pnl = sum(p.pnl for p in positions)
        return PortfolioSummary(
            total_equity=cash + positions_value,
            cash=cash,
            positions_value=positions_value,
            open_positions=positions,
            total_pnl=total_pnl,
            position_count=len(positions),
        )

    def size_position(
        self,
        cash: float,
        confidence: float,
        risk_per_trade: float = 0.02,
        max_allocation: float = 0.25,
    ) -> float:
        allocation = min(risk_per_trade * confidence * 10, max_allocation)
        return cash * allocation

    def compute_pnl(self, position: Position, market_price: float) -> float:
        return position.quantity * (market_price - position.entry_price)

    def update_position(
        self,
        position: Position,
        market_price: float,
    ) -> Position:
        position.update_price(market_price)
        self.position_repo.update(position)
        return position

    def open_trade(
        self,
        signal_id: uuid.UUID,
        market_id: uuid.UUID,
        side: TradeSide,
        quantity: float,
        price: float,
    ) -> Trade:
        trade = Trade(
            signal_id=signal_id,
            market_id=market_id,
            side=side,
            quantity=quantity,
            price=price,
            status=TradeStatus.PENDING,
        )
        result = self.trade_repo.add(trade)
        if self.audit:
            self.audit.record(
                "trade.open",
                "system",
                str(market_id),
                f"side={side.value} qty={quantity} price={price} trade_id={result.id}",
            )
        return result

    def update_trade(self, trade: Trade) -> Trade:
        return self.trade_repo.update(trade)

    def fill_trade(
        self,
        trade: Trade,
        fill_price: float | None = None,
    ) -> Trade:
        price = fill_price or trade.price
        if trade.status == TradeStatus.PENDING and not trade.external_order_id:
            trade.submit(f"auto-{trade.id}")
        trade.fill(trade.quantity, price)
        self.trade_repo.update(trade)
        existing = self.position_repo.get_by_market(trade.market_id)
        direction = 1 if trade.side == TradeSide.BUY else -1
        if self.audit:
            self.audit.record(
                "trade.fill",
                "system",
                str(trade.market_id),
                f"trade_id={trade.id} side={trade.side.value} qty={trade.quantity} price={price}",
            )
        if existing:
            new_qty = existing.quantity + direction * trade.quantity
            avg_price = (
                (existing.entry_price * existing.quantity + price * trade.quantity)
                / (existing.quantity + trade.quantity)
                if trade.side == TradeSide.BUY
                else existing.entry_price
            )
            existing.quantity = new_qty
            existing.entry_price = avg_price
            existing.current_price = price
            existing.updated_at = trade.filled_at or trade.updated_at
            self.position_repo.update(existing)
        else:
            pos = Position(
                market_id=trade.market_id,
                quantity=direction * trade.quantity,
                entry_price=price,
                current_price=price,
                pnl=0.0,
            )
            self.position_repo.add(pos)
        return trade

    def close_position(
        self,
        position: Position,
        close_price: float,
    ) -> float:
        realized = position.close(close_price)
        self.position_repo.update(position)
        if self.audit:
            self.audit.record(
                "position.close",
                "system",
                str(position.market_id),
                f"pnl={realized:.2f} close_price={close_price} qty={position.quantity}",
            )
        return realized

    def rebalance(
        self,
        target_allocations: dict[uuid.UUID, float],
        cash: float,
        market_prices: dict[uuid.UUID, float],
    ) -> list[Trade]:
        trades: list[Trade] = []
        total_value = cash + sum(
            v * market_prices.get(k, 0.0)
            for k, v in {p.market_id: p.quantity for p in self.position_repo.list_open()}.items()
        )
        for market_id, target_pct in target_allocations.items():
            target_value = total_value * target_pct
            current_qty = 0.0
            pos = self.position_repo.get_by_market(market_id)
            if pos:
                current_qty = pos.quantity
            current_value = current_qty * market_prices.get(market_id, 0.0)
            diff = target_value - current_value
            if abs(diff) < 1.0:
                continue
            side = TradeSide.BUY if diff > 0 else TradeSide.SELL
            qty = abs(diff) / market_prices.get(market_id, 1.0)
            trade = self.open_trade(
                signal_id=uuid.uuid4(),
                market_id=market_id,
                side=side,
                quantity=round(qty, 4),
                price=market_prices.get(market_id, 0.0),
            )
            trades.append(trade)
        return trades
