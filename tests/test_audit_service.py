from __future__ import annotations

from traderos.infrastructure.audit import AuditService


class TestAuditService:
    def test_record_entry(self) -> None:
        svc = AuditService()
        entry = svc.record("backtest.run", "system", "backtest_engine", "ran OK")
        assert entry.action == "backtest.run"
        assert entry.actor == "system"
        assert entry.resource == "backtest_engine"
        assert entry.previous_hash == "genesis"
        assert entry.hash != ""

    def test_verify_chain_single(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        assert svc.verify_chain()

    def test_verify_chain_multiple(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD", "buy 0.1")
        svc.record("trade.fill", "broker", "BTC/USD", "filled 0.1 @ 50000")
        assert svc.verify_chain()

    def test_find_by_action(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("stop", "system", "app")
        results = svc.find(action="start")
        assert len(results) == 1
        assert results[0].action == "start"

    def test_find_by_actor(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy_x", "BTC/USD")
        results = svc.find(actor="strategy_x")
        assert len(results) == 1

    def test_get_entries_pagination(self) -> None:
        svc = AuditService()
        for i in range(10):
            svc.record(f"event.{i}", "system", "test")
        page = svc.get_entries(limit=5, offset=0)
        assert len(page) == 5

    def test_clear(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.clear()
        assert svc.get_entries() == []
