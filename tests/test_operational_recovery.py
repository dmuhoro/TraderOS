from __future__ import annotations

import time
from unittest.mock import MagicMock

from traderos.domain.services.reconciliation_service import OrderReconciliationService
from traderos.domain.services.reconciliation_service import PersistentKillSwitch


class TestTimedBackup:
    def test_backup_completes_within_slo(self, tmp_path, monkeypatch) -> None:
        import sqlite3

        from traderos.infrastructure.config.config_loader import Config
        from traderos.infrastructure.database.backup import create_backup

        db_path = tmp_path / "test_timed.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'hello')")
        conn.commit()
        conn.close()

        monkeypatch.setenv("DB_PATH", str(db_path))
        monkeypatch.setenv("DB_BACKUP_DIR", str(tmp_path / "backups"))

        start = time.perf_counter()
        result_path = create_backup(Config())
        elapsed = time.perf_counter() - start

        assert result_path.exists(), "Backup file should exist"
        assert result_path.suffix == ".gz", f"Expected .gz backup, got {result_path.suffix}"
        assert elapsed < 5.0, f"Backup took {elapsed:.2f}s, expected < 5s (SLO)"

    def test_restore_completes_within_slo(self, tmp_path, monkeypatch) -> None:
        import sqlite3

        from traderos.infrastructure.config.config_loader import Config
        from traderos.infrastructure.database.backup import create_backup
        from traderos.infrastructure.database.backup import restore_backup

        db_path = tmp_path / "test_restore_timed.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'original')")
        conn.commit()
        conn.close()

        monkeypatch.setenv("DB_PATH", str(db_path))
        monkeypatch.setenv("DB_BACKUP_DIR", str(tmp_path / "backups"))

        backup_path = create_backup(Config())
        assert backup_path.exists()

        import os

        os.remove(db_path)

        start = time.perf_counter()
        restored_path = restore_backup(backup_path, Config())
        elapsed = time.perf_counter() - start

        assert restored_path is not None, "Restore should return a path"
        assert restored_path.exists(), "Restored DB should exist"
        assert elapsed < 5.0, f"Restore took {elapsed:.2f}s, expected < 5s (SLO)"

        conn = sqlite3.connect(str(restored_path))
        row = conn.execute("SELECT val FROM test WHERE id = 1").fetchone()
        assert row[0] == "original"
        conn.close()


class TestCrashRecoveryDrill:
    def test_recovery_after_simulated_crash(self) -> None:
        reconciliation = OrderReconciliationService()

        trade = MagicMock()
        trade.external_order_id = "ord-1"
        trade.status.name = "SUBMITTED"
        trade.filled_quantity = 0.0

        broker_orders = [
            MagicMock(
                order_id="ord-1",
                filled_qty=1.0,
                filled_price=100.0,
                remaining_qty=0.0,
                symbol="BTC/USD",
                status="filled",
            )
        ]

        result = reconciliation.reconcile_orders(
            local_trades=[trade],
            broker_orders=broker_orders,
        )

        assert result.matched >= 1
        assert result.reconciled >= 0

    def test_kill_switch_resets_after_recovery(self) -> None:
        ks = PersistentKillSwitch(max_consecutive_failures=3)
        for _ in range(3):
            ks.record_failure()
        assert not ks.can_trade()

        ks.reset()
        assert ks.can_trade()

    def test_reconciliation_recovers_after_broker_outage(self) -> None:
        from traderos.domain.services.broker_state_reconciliation_service import (
            BrokerStateReconciliationService,
        )

        class _OutageThenOkBroker:
            def __init__(self):
                self._called = 0

            def get_positions(self):
                self._called += 1
                if self._called <= 2:
                    raise RuntimeError("Broker unreachable")
                return [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]

            def get_open_orders(self):
                if self._called <= 2:
                    raise RuntimeError("Broker unreachable")
                return []

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_OutageThenOkBroker())

        r1 = svc.reconcile()
        assert len(r1.errors) == 1
        assert not svc.can_accept_orders

        r2 = svc.reconcile()
        assert len(r2.errors) == 1
        assert not svc.can_accept_orders

        r3 = svc.reconcile(
            local_positions=[{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}],
            local_orders=[],
        )
        assert not r3.errors
        assert svc.can_accept_orders

    def test_preflight_passes_after_full_recovery(self) -> None:
        from traderos.domain.services.preflight_service import PreflightService
        from traderos.domain.services.risk_service import KillSwitch

        class _ValidAudit:
            def verify_chain(self):
                return True

        class _Reconciled:
            @property
            def can_accept_orders(self):
                return True

        ks = KillSwitch()
        preflight = PreflightService(
            audit=_ValidAudit(),
            broker_reconciliation=_Reconciled(),
            kill_switch=ks,
        )
        verdict = preflight.check(live_mode=False)
        assert verdict.passed
        assert verdict.checks["audit_chain"]
        assert verdict.checks["broker_reconciliation"]
        assert verdict.checks["kill_switch"]


class TestReconciliationDrill:
    def test_full_reconciliation_cycle(self) -> None:
        from traderos.domain.services.broker_state_reconciliation_service import (
            BrokerStateReconciliationService,
        )

        class _SyncBroker:
            def get_positions(self):
                return [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]

            def get_open_orders(self):
                return [{"id": "ord-1"}]

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_SyncBroker())
        local_positions = [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]
        local_orders = [{"id": "ord-1"}]

        result = svc.reconcile(local_positions=local_positions, local_orders=local_orders)

        assert not result.errors
        assert not result.has_mismatches
        assert result.matched_positions == 1
        assert svc.can_accept_orders

    def test_full_reconciliation_fix_after_mismatch(self) -> None:
        from traderos.domain.services.broker_state_reconciliation_service import (
            BrokerStateReconciliationService,
        )

        class _MisalignedBroker:
            def get_positions(self):
                return [{"symbol": "BTC/USD", "qty": 2.0, "current_price": 50000.0}]

            def get_open_orders(self):
                return [{"id": "ord-1"}]

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_MisalignedBroker())
        local_positions = [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]
        local_orders = [{"id": "ord-1"}]

        r1 = svc.reconcile(local_positions=local_positions, local_orders=local_orders)
        assert r1.has_mismatches
        assert not svc.can_accept_orders

        local_positions_fixed = [{"symbol": "BTC/USD", "qty": 2.0, "current_price": 50000.0}]
        r2 = svc.reconcile(local_positions=local_positions_fixed, local_orders=local_orders)
        assert not r2.errors
        assert not r2.has_mismatches
        assert svc.can_accept_orders
