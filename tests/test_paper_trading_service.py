from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from traderos.domain.entities.position import Position
from traderos.domain.entities.signal import Signal
from traderos.domain.entities.signal import SignalDirection
from traderos.domain.services.paper_trading_service import DeviationAnalysisService
from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.domain.services.paper_trading_service import PaperSession
from traderos.domain.services.paper_trading_service import PaperSessionStatus
from traderos.domain.services.paper_trading_service import PaperTradingService


class TestPaperBrokerAdapter:
    def test_place_market_order_fills_immediately(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, partial_fill_probability=0.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 100.0)
        assert result.filled
        assert result.fill_quantity == 100.0
        assert result.remaining == 0.0
        assert result.status == "filled"

    def test_place_market_order_rejected(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=0.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 100.0)
        assert not result.filled
        assert result.status == "rejected"

    def test_place_limit_order_triggers_buy(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_limit_order(uuid.uuid4(), "buy", 100.0, 110.0)
        assert not result.filled

    def test_place_limit_order_no_trigger(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_limit_order(uuid.uuid4(), "buy", 100.0, 90.0)
        assert not result.filled
        assert result.status == "pending"

    def test_place_stop_order_triggers_buy(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_stop_order(uuid.uuid4(), "buy", 100.0, 105.0, 110.0)
        assert result.filled

    def test_place_stop_order_no_trigger(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_stop_order(uuid.uuid4(), "buy", 100.0, 105.0, 100.0)
        assert not result.filled
        assert result.status == "pending"

    def test_cancel_order(self) -> None:
        broker = PaperBrokerAdapter()
        mid = uuid.uuid4()
        result = broker.place_limit_order(mid, "buy", 100.0, 90.0, close_price=100.0)
        assert result.status == "pending"
        assert len(broker.get_open_orders()) == 1
        cancelled = broker.cancel_order(result.order_id)
        assert cancelled.status == "cancelled"
        assert broker.get_open_orders() == []

    def test_cancel_unknown_order_rejected(self) -> None:
        broker = PaperBrokerAdapter()
        assert broker.cancel_order("nope").status == "rejected"

    def test_tracks_positions_and_balance(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        mid = uuid.uuid4()
        broker.place_market_order(mid, "buy", 10.0, close_price=100.0)
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == str(mid)
        assert positions[0]["qty"] == 10.0
        assert broker.get_account_balance() == pytest.approx(10000.0 - 10.0 * 100.0)

    def test_sell_closes_position(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        mid = uuid.uuid4()
        broker.place_market_order(mid, "buy", 10.0, close_price=100.0)
        broker.place_market_order(mid, "sell", 10.0, close_price=100.0)
        assert broker.get_positions() == []

    def test_pending_limit_recorded_as_open_order(self) -> None:
        broker = PaperBrokerAdapter()
        mid = uuid.uuid4()
        result = broker.place_limit_order(mid, "buy", 5.0, 90.0, close_price=100.0)
        assert result.status == "pending"
        orders = broker.get_open_orders()
        assert len(orders) == 1
        assert orders[0]["id"] == result.order_id
        assert orders[0]["type"] == "limit"

    def test_trailing_stop_rests_open(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        mid = uuid.uuid4()
        result = broker.place_trailing_stop_order(mid, "sell", 5.0, 0.05, market_price=100.0)
        assert not result.filled
        assert result.status == "pending"
        orders = broker.get_open_orders()
        assert len(orders) == 1
        assert orders[0]["type"] == "trailing_stop"

    def test_trailing_stop_without_market_price_pending(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        mid = uuid.uuid4()
        result = broker.place_trailing_stop_order(mid, "sell", 5.0, 0.05)
        assert not result.filled
        assert result.status == "pending"

    def test_modify_order(self) -> None:
        broker = PaperBrokerAdapter()
        mid = uuid.uuid4()
        result = broker.place_limit_order(mid, "buy", 5.0, 90.0, close_price=100.0)
        modified = broker.modify_order(result.order_id, qty=8.0)
        assert modified.status == "modified"
        orders = broker.get_open_orders()
        assert orders[0]["qty"] == 8.0

    def test_modify_unknown_order_rejected(self) -> None:
        broker = PaperBrokerAdapter()
        assert broker.modify_order("nope", qty=8.0).status == "rejected"

    def test_place_market_order_slippage(self) -> None:
        broker = PaperBrokerAdapter(slippage_bps=10.0, fill_probability=1.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 100.0)
        assert result.fill_price == 1.001

    def test_partial_fill(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, partial_fill_probability=1.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 100.0)
        assert result.filled
        assert result.fill_quantity < 100.0
        assert result.remaining > 0.0
        assert result.status == "partial"

    def test_sell_market_order(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_market_order(uuid.uuid4(), "sell", 50.0)
        assert result.filled
        assert result.fill_quantity == 50.0

    def test_stop_sell_trigger(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_stop_order(uuid.uuid4(), "sell", 50.0, 95.0, 90.0)
        assert result.filled

    def test_stop_sell_no_trigger(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0)
        result = broker.place_stop_order(uuid.uuid4(), "sell", 50.0, 95.0, 100.0)
        assert not result.filled


class TestPaperSession:
    def test_create_session_defaults(self) -> None:
        session = PaperSession(
            id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            market_ids=[uuid.uuid4()],
            status=PaperSessionStatus.CREATED,
        )
        assert session.initial_capital == 10000.0
        assert session.current_capital == 10000.0
        assert session.open_orders == []
        assert session.filled_orders == []


class TestPaperTradingService:
    def test_create_and_get_session(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        sid = uuid.uuid4()
        mids = [uuid.uuid4()]
        session = svc.create_session(sid, mids, 50000.0)
        assert session.initial_capital == 50000.0
        assert session.status == PaperSessionStatus.CREATED
        got = svc.get_session(session.id)
        assert got is not None
        assert got.id == session.id

    def test_start_session(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        session = svc.create_session(uuid.uuid4(), [uuid.uuid4()])
        started = svc.start_session(session.id)
        assert started.status == PaperSessionStatus.RUNNING
        assert started.start_time is not None

    def test_pause_session(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        session = svc.create_session(uuid.uuid4(), [uuid.uuid4()])
        svc.start_session(session.id)
        paused = svc.pause_session(session.id)
        assert paused.status == PaperSessionStatus.PAUSED

    def test_stop_session(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        session = svc.create_session(uuid.uuid4(), [uuid.uuid4()])
        svc.start_session(session.id)
        stopped = svc.stop_session(session.id)
        assert stopped.status == PaperSessionStatus.STOPPED
        assert stopped.end_time is not None

    def test_get_session_nonexistent(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        assert svc.get_session(uuid.uuid4()) is None

    def test_list_sessions(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        assert svc.list_sessions() == []
        svc.create_session(uuid.uuid4(), [uuid.uuid4()])
        assert len(svc.list_sessions()) == 1

    def test_process_candle_not_running(self) -> None:
        svc = PaperTradingService(
            broker=PaperBrokerAdapter(),
            signal_service=None,  # type: ignore
            risk_service=None,  # type: ignore
            portfolio_service=None,  # type: ignore
            execution=None,  # type: ignore
        )
        session = svc.create_session(uuid.uuid4(), [uuid.uuid4()])
        svc.process_candle(session.id, uuid.uuid4(), 100.0, datetime.now(UTC))
        assert session.equity_curve == []  # not running, no processing


class TestDeviationAnalysisService:
    def test_compare_metrics_aligned(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compare_metrics(0.5, 0.6, 0.1, 0.12, 0.6, 0.65)
        assert result["status"] == "aligned"

    def test_compare_metrics_divergent(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compare_metrics(0.5, 2.0, 0.1, 0.3, 0.6, 0.9)
        assert result["status"] == "divergent"
        assert len(str(result["warnings"])) > 0

    def test_compute_corridor(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compute_corridor([0.01, 0.02, 0.03], [0.015, 0.025, 0.035])
        assert result["correlation"] > 0.9
        assert result["rmse"] > 0

    def test_compute_corridor_insufficient_data(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compute_corridor([0.01], [0.015])
        assert result["correlation"] == 0.0
        assert result["rmse"] == 0.0

    def test_compute_corridor_negative_correlation(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compute_corridor([0.01, 0.02, 0.03], [-0.01, -0.02, -0.03])
        assert result["correlation"] < 0

    def test_compare_metrics_edge_cases(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compare_metrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert result["status"] == "aligned"

    def test_compare_metrics_reports_max_dd_and_win_rate_warnings(self) -> None:
        svc = DeviationAnalysisService()
        result = svc.compare_metrics(0.5, 0.5, 0.1, 0.8, 0.6, 0.0)
        assert result["status"] == "divergent"
        assert "Max drawdown deviates by 0.70" in result["warnings"]
        assert "Win rate deviates by -0.60" in result["warnings"]


class TestPaperBrokerAdapterFills:
    def test_add_to_long_updates_average_price(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        mid = uuid.uuid4()
        broker.place_market_order(mid, "buy", 10.0, close_price=100.0)
        broker.place_market_order(mid, "buy", 5.0, close_price=100.0)
        position = broker.get_positions()[0]
        assert position["qty"] == 15.0
        assert position["entry_price"] == 100.0

    def test_place_limit_order_fills_when_price_crossed(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        mid = uuid.uuid4()
        result = broker.place_limit_order(mid, "buy", 10.0, 110.0, close_price=105.0)
        assert result.filled
        assert result.status == "filled"
        assert broker.get_positions()[0]["qty"] == 10.0

    def test_place_sell_limit_order_fills_when_price_crossed(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        mid = uuid.uuid4()
        result = broker.place_limit_order(mid, "sell", 10.0, 90.0, close_price=95.0)
        assert result.filled
        assert result.status == "filled"

    def test_trailing_stop_triggers_at_zero_trail(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        mid = uuid.uuid4()
        result = broker.place_trailing_stop_order(mid, "buy", 10.0, 0.0, market_price=100.0)
        assert result.filled
        assert result.status == "filled"

    def test_sell_into_long_reduces_position(self) -> None:
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        mid = uuid.uuid4()
        broker.place_market_order(mid, "buy", 10.0, close_price=100.0)
        broker.place_market_order(mid, "sell", 5.0, close_price=100.0)
        position = broker.get_positions()[0]
        assert position["qty"] == 5.0


class TestPaperSessionFlow:
    def _svc(
        self,
        broker=None,
        signal_service=None,
        risk_service=None,
        portfolio_service=None,
        execution=None,
    ):
        return PaperTradingService(
            broker=broker if broker is not None else PaperBrokerAdapter(),
            signal_service=signal_service,  # type: ignore
            risk_service=risk_service,  # type: ignore
            portfolio_service=portfolio_service,  # type: ignore
            execution=execution,  # type: ignore
        )

    def test_stop_session_realizes_position_pnl(self) -> None:
        portfolio = MagicMock()
        portfolio.compute_pnl.return_value = 100.0
        svc = self._svc(portfolio_service=portfolio)
        session = svc.create_session(uuid.uuid4(), [uuid.uuid4()])
        svc.start_session(session.id)
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=110.0,
            pnl=100.0,
        )
        session.positions[pos.market_id] = pos
        stopped = svc.stop_session(session.id)
        assert stopped.status == PaperSessionStatus.STOPPED
        assert stopped.current_capital == 10000.0 + 100.0
        portfolio.compute_pnl.assert_called_once_with(pos, pos.current_price)

    def test_unknown_session_raises(self) -> None:
        svc = self._svc()
        with pytest.raises(ValueError):
            svc.start_session(uuid.uuid4())
        with pytest.raises(ValueError):
            svc.pause_session(uuid.uuid4())
        with pytest.raises(ValueError):
            svc.stop_session(uuid.uuid4())
        with pytest.raises(ValueError):
            svc.process_candle(uuid.uuid4(), uuid.uuid4(), 100.0, datetime.now(UTC))

    def _signal(self, strategy_id, market_id):
        return Signal(
            market_id=market_id,
            strategy_id=strategy_id,
            direction=SignalDirection.LONG,
            confidence=0.8,
            generated_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
            id=uuid.uuid4(),
        )

    def _running_session(self, svc, strategy_id, market_id):
        session = svc.create_session(strategy_id, [market_id])
        svc.start_session(session.id)
        return session

    def test_process_candle_executes_filled_signal(self) -> None:
        strategy_id = uuid.uuid4()
        mid = uuid.uuid4()
        signal = self._signal(strategy_id, mid)
        signal_service = MagicMock()
        signal_service.get_active_signals.return_value = [signal]
        risk = MagicMock()
        risk.assess_trade.return_value = SimpleNamespace(kelly_fraction=0.1)
        portfolio = MagicMock()
        portfolio.size_position.return_value = 50.0
        portfolio.compute_pnl.return_value = 0.0
        execution = MagicMock()
        execution.create_market_order.return_value = SimpleNamespace(id="order-1")
        broker = PaperBrokerAdapter(fill_probability=1.0, slippage_bps=0.0)
        svc = self._svc(broker, signal_service, risk, portfolio, execution)
        session = self._running_session(svc, strategy_id, mid)

        result = svc.process_candle(session.id, mid, 100.0, datetime.now(UTC))

        assert len(result.trades) == 1
        assert len(result.positions) == 1
        assert len(result.filled_orders) == 1
        assert len(result.equity_curve) == 1
        assert result.positions[mid].quantity == 50.0
        assert broker.get_positions()[0]["qty"] == 50.0

    def test_process_candle_partial_fill_tracks_open_order(self) -> None:
        strategy_id = uuid.uuid4()
        mid = uuid.uuid4()
        signal = self._signal(strategy_id, mid)
        signal_service = MagicMock()
        signal_service.get_active_signals.return_value = [signal]
        risk = MagicMock()
        risk.assess_trade.return_value = SimpleNamespace(kelly_fraction=0.1)
        portfolio = MagicMock()
        portfolio.size_position.return_value = 50.0
        portfolio.compute_pnl.return_value = 0.0
        execution = MagicMock()
        execution.create_market_order.return_value = SimpleNamespace(id="order-1")
        broker = PaperBrokerAdapter(fill_probability=1.0, partial_fill_probability=1.0)
        svc = self._svc(broker, signal_service, risk, portfolio, execution)
        session = self._running_session(svc, strategy_id, mid)

        result = svc.process_candle(session.id, mid, 100.0, datetime.now(UTC))

        assert len(result.open_orders) == 1
        assert result.filled_orders == []

    def test_process_candle_ignores_other_strategy(self) -> None:
        strategy_id = uuid.uuid4()
        mid = uuid.uuid4()
        signal = self._signal(uuid.uuid4(), mid)
        signal_service = MagicMock()
        signal_service.get_active_signals.return_value = [signal]
        svc = self._svc(
            signal_service=signal_service,
            risk_service=MagicMock(),
            portfolio_service=MagicMock(),
            execution=MagicMock(),
        )
        session = self._running_session(svc, strategy_id, mid)
        result = svc.process_candle(session.id, mid, 100.0, datetime.now(UTC))
        assert result.trades == []

    def test_process_candle_skips_zero_kelly(self) -> None:
        strategy_id = uuid.uuid4()
        mid = uuid.uuid4()
        signal = self._signal(strategy_id, mid)
        signal_service = MagicMock()
        signal_service.get_active_signals.return_value = [signal]
        risk = MagicMock()
        risk.assess_trade.return_value = SimpleNamespace(kelly_fraction=0.0)
        svc = self._svc(
            signal_service=signal_service,
            risk_service=risk,
            portfolio_service=MagicMock(),
            execution=MagicMock(),
        )
        session = self._running_session(svc, strategy_id, mid)
        result = svc.process_candle(session.id, mid, 100.0, datetime.now(UTC))
        assert result.trades == []

    def test_process_candle_skips_zero_size(self) -> None:
        strategy_id = uuid.uuid4()
        mid = uuid.uuid4()
        signal = self._signal(strategy_id, mid)
        signal_service = MagicMock()
        signal_service.get_active_signals.return_value = [signal]
        risk = MagicMock()
        risk.assess_trade.return_value = SimpleNamespace(kelly_fraction=0.1)
        portfolio = MagicMock()
        portfolio.size_position.return_value = 0.0
        portfolio.compute_pnl.return_value = 0.0
        svc = self._svc(
            signal_service=signal_service,
            risk_service=risk,
            portfolio_service=portfolio,
            execution=MagicMock(),
        )
        session = self._running_session(svc, strategy_id, mid)
        result = svc.process_candle(session.id, mid, 100.0, datetime.now(UTC))
        assert result.trades == []

    def test_process_candle_skips_unfilled_order(self) -> None:
        strategy_id = uuid.uuid4()
        mid = uuid.uuid4()
        signal = self._signal(strategy_id, mid)
        signal_service = MagicMock()
        signal_service.get_active_signals.return_value = [signal]
        risk = MagicMock()
        risk.assess_trade.return_value = SimpleNamespace(kelly_fraction=0.1)
        portfolio = MagicMock()
        portfolio.size_position.return_value = 50.0
        portfolio.compute_pnl.return_value = 0.0
        execution = MagicMock()
        execution.create_market_order.return_value = SimpleNamespace(id="order-1")
        broker = PaperBrokerAdapter(fill_probability=0.0)
        svc = self._svc(broker, signal_service, risk, portfolio, execution)
        session = self._running_session(svc, strategy_id, mid)
        result = svc.process_candle(session.id, mid, 100.0, datetime.now(UTC))
        assert result.trades == []
        assert len(result.equity_curve) == 1
