from __future__ import annotations

from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.audit import _compute_hash


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

    def test_verify_chain_detects_mutated_action(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD")
        entry = svc._entries[1]
        svc._entries[1] = entry._replace(action="tampered.action")
        assert not svc.verify_chain()

    def test_verify_chain_detects_mutated_actor(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD")
        entry = svc._entries[1]
        svc._entries[1] = entry._replace(actor="tampered.actor")
        assert not svc.verify_chain()

    def test_verify_chain_detects_mutated_resource(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD")
        entry = svc._entries[1]
        svc._entries[1] = entry._replace(resource="tampered.resource")
        assert not svc.verify_chain()

    def test_verify_chain_detects_mutated_detail(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD", "buy 0.1")
        entry = svc._entries[1]
        svc._entries[1] = entry._replace(detail="tampered.detail")
        assert not svc.verify_chain()

    def test_verify_chain_detects_mutated_timestamp(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD")
        entry = svc._entries[1]
        svc._entries[1] = entry._replace(timestamp=entry.timestamp.replace(year=2020))
        assert not svc.verify_chain()

    def test_verify_chain_detects_mutated_previous_hash(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD")
        entry = svc._entries[1]
        svc._entries[1] = entry._replace(previous_hash="tampered")
        assert not svc.verify_chain()

    def test_verify_chain_detects_mutated_hash_field(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        entry = svc._entries[0]
        svc._entries[0] = entry._replace(hash="tampered")
        assert not svc.verify_chain()

    def test_verify_chain_detects_broken_link(self) -> None:
        svc = AuditService()
        svc.record("start", "system", "app")
        svc.record("trade.open", "strategy", "BTC/USD")
        entry1 = svc._entries[1]
        # Recompute entry1's hash with a tampered previous_hash so the row's
        # own hash stays internally valid — only the link to entry0 is broken.
        tampered = entry1._replace(previous_hash="tampered")
        svc._entries[1] = tampered._replace(hash=_compute_hash(tampered))
        assert not svc.verify_chain()

    def test_compute_hash_is_deterministic(self) -> None:
        svc = AuditService()
        e1 = svc.record("start", "system", "app")
        e2 = svc.record("start", "system", "app")
        assert _compute_hash(e1) == _compute_hash(e1)
        assert e2.previous_hash == e1.hash

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
