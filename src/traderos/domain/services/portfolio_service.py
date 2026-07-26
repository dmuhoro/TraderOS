from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import NamedTuple

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
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
        updated = Position(
            market_id=position.market_id,
            quantity=position.quantity,
            entry_price=position.entry_price,
            current_price=market_price,
            pnl=self.compute_pnl(position, market_price),
            id=position.id,
            created_at=position.created_at,
        )
        self.position_repo.update(updated)
        return updated

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
        return self.trade_repo.add(trade)

    def fill_trade(
        self,
        trade: Trade,
        fill_price: float | None = None,
    ) -> Trade:
        price = fill_price or trade.price
        filled = Trade(
            signal_id=trade.signal_id,
            market_id=trade.market_id,
            side=trade.side,
            quantity=trade.quantity,
            price=price,
            status=TradeStatus.FILLED,
            id=trade.id,
            created_at=trade.created_at,
        )
        self.trade_repo.update(filled)
        existing = self.position_repo.get_by_market(filled.market_id)
        direction = 1 if filled.side == TradeSide.BUY else -1
        if existing:
            new_qty = existing.quantity + direction * filled.quantity
            avg_price = (
                (existing.entry_price * existing.quantity + price * filled.quantity)
                / (existing.quantity + filled.quantity)
                if filled.side == TradeSide.BUY
                else existing.entry_price
            )
            updated = Position(
                market_id=filled.market_id,
                quantity=new_qty,
                entry_price=avg_price,
                current_price=price,
                pnl=0.0,
                id=existing.id,
                created_at=existing.created_at,
            )
            self.position_repo.update(updated)
        else:
            pos = Position(
                market_id=filled.market_id,
                quantity=direction * filled.quantity,
                entry_price=price,
                current_price=price,
                pnl=0.0,
            )
            self.position_repo.add(pos)
        return filled

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
