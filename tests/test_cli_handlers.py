from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from traderos.interfaces.cli import main as cli_main


def _run(cmd_func, **kwargs) -> str:
    ns = argparse.Namespace(**kwargs)
    out = StringIO()
    with patch("sys.stdout", out):
        cmd_func(ns)
    return out.getvalue()


def _run_exit(cmd_func, **kwargs) -> tuple[str, int | None]:
    ns = argparse.Namespace(**kwargs)
    out = StringIO()
    code = None
    with patch("sys.stdout", out):
        try:
            cmd_func(ns)
        except SystemExit as exc:
            code = exc.code
    return out.getvalue(), code


def _run_main(args: list[str]) -> str:
    out = StringIO()
    with (
        patch("sys.argv", ["traderos"] + args),
        patch("sys.stdout", out),
    ):
        try:
            cli_main.main()
        except SystemExit:
            pass
    return out.getvalue()


class TestCliBacktestHistorical:
    def _candles(self, count=10):
        from traderos.domain.services.backtesting_service import synthetic_candles

        return synthetic_candles(count=count, market_id=uuid.uuid4())

    def _patch_historical_path(self, monkeypatch, candles, config_raises: bool = False):
        conn = MagicMock()
        if config_raises:
            monkeypatch.setattr(
                "traderos.infrastructure.config.config_loader.Config.load",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no config")),
            )
        else:
            monkeypatch.setattr(
                "traderos.infrastructure.config.config_loader.Config.load",
                lambda *a, **k: SimpleNamespace(),
            )
        monkeypatch.setattr(
            "traderos.infrastructure.database.connection.get_connection", lambda c: conn
        )
        monkeypatch.setattr(
            "traderos.infrastructure.database.migration_manager.migrate", lambda *a, **k: None
        )
        monkeypatch.setattr(
            "traderos.infrastructure.repositories.sqlite.historical_candles."
            "SQLiteHistoricalCandleRepository",
            MagicMock,
        )
        monkeypatch.setattr(
            "traderos.domain.services.historical_data.HistoricalDataService.get_candles",
            lambda self, *_a, **_k: candles,
        )
        return conn

    def test_backtest_binance_uses_historical_candles(self, monkeypatch):
        self._patch_historical_path(monkeypatch, self._candles())
        output = _run(
            cli_main.cmd_backtest,
            strategy="mean_reversion",
            candles=10,
            source="binance",
            symbol="",
            timeframe="1h",
            slippage_bps=5.0,
            fee_bps=0.0,
            min_fee=0.0,
        )
        assert "Source: binance" in output
        assert "Symbol: BTCUSDT" in output

    def test_backtest_alpaca_uses_historical_candles(self, monkeypatch):
        self._patch_historical_path(monkeypatch, self._candles())
        output = _run(
            cli_main.cmd_backtest,
            strategy="mean_reversion",
            candles=10,
            source="alpaca",
            symbol="",
            timeframe="1h",
            slippage_bps=5.0,
            fee_bps=0.0,
            min_fee=0.0,
        )
        assert "Source: alpaca" in output
        assert "Symbol: BTC/USD" in output

    def test_historical_candles_cache_setup_failure_falls_back(self, monkeypatch):
        candles = self._candles(5)
        self._patch_historical_path(monkeypatch, candles, config_raises=True)
        output = _run(
            cli_main.cmd_backtest,
            strategy="mean_reversion",
            candles=5,
            source="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            slippage_bps=5.0,
            fee_bps=0.0,
            min_fee=0.0,
        )
        assert "Total Return" in output


class TestCliAuditTextEntries:
    def _svc_with_entries(self, count=2):
        svc = MagicMock()
        svc.get_entries.return_value = [
            SimpleNamespace(
                timestamp=datetime.now(UTC),
                action=f"action{i}",
                actor="operator",
                resource="BTCUSDT",
                detail="detail",
            )
            for i in range(count)
        ]
        return svc

    def test_audit_text_prints_entries(self):
        with patch(
            "traderos.interfaces.cli.main._build_audit_service",
            return_value=self._svc_with_entries(),
        ):
            output = _run(cli_main.cmd_audit, limit=5, json=False)
        assert "action0 by operator on BTCUSDT" in output
        assert "action1 by operator on BTCUSDT" in output


class TestCliAuditServiceBuilder:
    def test_build_audit_service_postgres_backend(self, monkeypatch):
        fake = MagicMock()
        conn = MagicMock()
        monkeypatch.setattr(
            cli_main.Config, "load", lambda: SimpleNamespace(database_url="postgres")
        )
        monkeypatch.setattr(cli_main, "get_connection", lambda cfg: conn)
        monkeypatch.setattr(cli_main, "resolve_backend", lambda url: "postgres")
        monkeypatch.setattr(
            "traderos.infrastructure.observability_postgres.PostgresAuditService",
            lambda c: fake,
        )
        assert cli_main._build_audit_service() is fake

    def test_build_audit_service_sqlite_backend(self, monkeypatch):
        fake = MagicMock()
        conn = MagicMock()
        monkeypatch.setattr(cli_main.Config, "load", lambda: SimpleNamespace(database_url="sqlite"))
        monkeypatch.setattr(cli_main, "get_connection", lambda cfg: conn)
        monkeypatch.setattr(cli_main, "resolve_backend", lambda url: "sqlite")
        monkeypatch.setattr(
            "traderos.infrastructure.observability.SQLiteAuditService",
            lambda c: fake,
        )
        assert cli_main._build_audit_service() is fake


class TestCliAuditVerify:
    def _run_verify(self, svc) -> tuple[str, int | None]:
        out = StringIO()
        code = None
        with (
            patch("traderos.interfaces.cli.main._build_audit_service", return_value=svc),
            patch("sys.stdout", out),
        ):
            try:
                cli_main.cmd_audit_verify(argparse.Namespace())
            except SystemExit as exc:
                code = exc.code
        return out.getvalue(), code

    def test_verify_pass(self):
        svc = MagicMock()
        svc.verify_chain.return_value = True
        out, code = self._run_verify(svc)
        assert "PASS" in out
        assert code == 0

    def test_verify_fail(self):
        svc = MagicMock()
        svc.verify_chain.return_value = False
        out, code = self._run_verify(svc)
        assert "FAIL" in out
        assert code == 1

    def test_verify_trail_unavailable(self):
        def _raise(*_a, **_k):
            raise RuntimeError("no audit table")

        out = StringIO()
        code = None
        with (
            patch("traderos.interfaces.cli.main._build_audit_service", side_effect=_raise),
            patch("sys.stdout", out),
        ):
            try:
                cli_main.cmd_audit_verify(argparse.Namespace())
            except SystemExit as exc:
                code = exc.code
        assert "Audit trail unavailable" in out.getvalue()
        assert code == 1


class TestCliRiskCommands:
    def _orch(self):
        from traderos.application.factory import build_orchestrator

        return build_orchestrator(mode="paper")

    def test_risk_status_text(self):
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=self._orch()):
            output = _run(cli_main.cmd_risk, risk_cmd="status", mode="paper", json=False)
        assert "Kill switch:" in output
        assert "Order acceptance" in output

    def test_risk_check_allowed(self):
        orch = self._orch()
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(cli_main.cmd_risk, risk_cmd="check", mode="paper", json=False)
        assert "Risk check: PASS" in out
        assert code == 0

    def test_risk_check_blocked(self):
        orch = self._orch()
        orch.risk_service.kill_switch.engage()
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(cli_main.cmd_risk, risk_cmd="check", mode="paper", json=False)
        assert "Risk check: FAIL" in out
        assert code == 1

    def test_risk_reset(self):
        orch = self._orch()
        orch.risk_service.kill_switch.engage()
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_risk, risk_cmd="reset", mode="paper", json=False)
        assert "Kill switch reset" in output
        assert orch.risk_service.kill_switch.circuit_open is False

    def test_risk_kill(self):
        orch = self._orch()
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_risk, risk_cmd="kill", mode="paper", json=False)
        assert "Kill switch engaged" in output
        assert orch.risk_service.kill_switch.consecutive_failures == 1

    def test_risk_reconcile_runs_reconcile(self):
        orch = MagicMock()
        orch.risk_service.kill_switch = MagicMock()
        orch.broker_reconciliation = MagicMock()
        orch.broker_reconciliation.can_accept_orders = True
        orch.broker_reconciliation.reconcile.return_value = SimpleNamespace(mismatches=[])
        orch.broker.pending = lambda: []
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(
                cli_main.cmd_risk, risk_cmd="reconcile", mode="paper", json=False, verb=None
            )
        orch.broker_reconciliation.reconcile.assert_called_once()
        assert "Reconciliation mismatches: 0" in out
        assert "Order acceptance: allowed" in out
        assert code == 0

    def test_risk_reconcile_blocked(self):
        orch = MagicMock()
        orch.risk_service.kill_switch = MagicMock()
        orch.broker_reconciliation = MagicMock()
        orch.broker_reconciliation.can_accept_orders = False
        orch.broker_reconciliation.reconcile.return_value = SimpleNamespace(mismatches=[1])
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(
                cli_main.cmd_risk, risk_cmd="reconcile", mode="paper", json=False, verb=None
            )
        assert "Reconciliation mismatches: 1" in out
        assert "Order acceptance: blocked" in out
        assert code == 1

    def test_risk_reconcile_not_available(self):
        orch = MagicMock()
        orch.risk_service.kill_switch = MagicMock()
        orch.broker_reconciliation = None
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(
                cli_main.cmd_risk, risk_cmd="reconcile", mode="paper", json=False, verb=None
            )
        assert "Broker reconciliation not available" in out
        assert code == 1

    def test_risk_unknown_subcommand_shows_help(self):
        orch = MagicMock()
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(
                cli_main.cmd_risk, risk_cmd="bogus", mode="paper", json=False, command="risk"
            )
        assert code in (0, 2)


class TestCliMetrics:
    def _orch(self):
        from traderos.application.factory import build_orchestrator

        return build_orchestrator(mode="paper")

    def test_snapshot_text(self):
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=self._orch()):
            output = _run(cli_main.cmd_metrics, metrics_cmd="snapshot", mode="paper", json=False)
        assert "Metrics snapshot" in output

    def test_snapshot_text_with_metrics_data(self):
        orch = MagicMock()
        orch.metrics.snapshot.return_value = {"signals": 3, "trades": 2}
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_metrics, metrics_cmd="snapshot", mode="paper", json=False)
        assert "signals = 3" in output
        assert "trades = 2" in output

    def test_snapshot_json(self):
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=self._orch()):
            output = _run(cli_main.cmd_metrics, metrics_cmd="snapshot", mode="paper", json=True)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_watch_runs_cycles(self):
        orch = MagicMock()
        orch.market_ids = [uuid.uuid4()]
        orch.data_ingestion = None
        orch.run_cycle.return_value = MagicMock(
            market_id=str(uuid.uuid4()), trades=1, duration_ms=1.2, errors=[]
        )
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(
                cli_main.cmd_metrics, metrics_cmd="watch", mode="paper", json=False, cycles=2
            )
        assert "cycle=" in output
        assert orch.run_cycle.call_count == 2

    def test_metrics_unknown_subcommand_shows_help(self):
        orch = MagicMock()
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            out, code = _run_exit(
                cli_main.cmd_metrics, metrics_cmd="bogus", mode="paper", json=False
            )
        assert code in (0, 2)


class TestCliSignalTextActive:
    def test_signal_text_with_active_signals(self):
        from traderos.domain.entities.signal import Signal
        from traderos.domain.entities.signal import SignalDirection

        now = datetime.now(UTC)
        orch = MagicMock()
        orch.signal_service.get_active_signals.return_value = [
            Signal(
                market_id=uuid.uuid4(),
                strategy_id=uuid.uuid4(),
                direction=SignalDirection.LONG,
                confidence=0.9,
                generated_at=now,
                expires_at=now + timedelta(hours=1),
            )
        ]
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_signal, market_id=str(uuid.uuid4()), json=False)
        assert "Active signals for" in output
        assert "conf=0.90" in output


class TestCliDaemon:
    def test_daemon_starts_engine(self):
        orch = MagicMock()
        cfg = MagicMock()
        with (
            patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch),
            patch("traderos.interfaces.cli.main.Config.load", return_value=cfg),
        ):
            _run(cli_main.cmd_daemon, mode="paper", interval=60)
        cfg.validate.assert_called_once()
        orch.run_forever.assert_called_once_with(interval_seconds=60)


class TestCliStatusHealthDetail:
    def _status(self, healthy: bool):
        orch = MagicMock()
        orch.get_status.return_value = {
            "mode": "paper",
            "running": False,
            "markets": 0,
            "crash_recovered": False,
            "health": {"db": healthy},
        }
        orch.risk_service.can_trade.return_value = SimpleNamespace(allowed=True, reason="")
        orch.broker_reconciliation = MagicMock()
        orch.broker_reconciliation.can_accept_orders = True
        return orch

    def test_status_text_lists_health(self):
        with patch(
            "traderos.interfaces.cli.main.build_orchestrator", return_value=self._status(True)
        ):
            output = _run(cli_main.cmd_status, mode="paper", json=False)
        assert "[PASS] db" in output

    def test_status_text_health_fail(self):
        with patch(
            "traderos.interfaces.cli.main.build_orchestrator", return_value=self._status(False)
        ):
            output = _run(cli_main.cmd_status, mode="paper", json=False)
        assert "[FAIL] db" in output


class TestCliValidate:
    def test_validate_ok(self, monkeypatch):
        cfg = MagicMock()
        monkeypatch.setattr(cli_main.Config, "load", lambda: cfg)
        code = cli_main.cmd_validate(argparse.Namespace(mode="paper"))
        assert code == 0
        cfg.validate.assert_called_once()

    def test_validate_config_error(self, monkeypatch):
        from traderos.domain.exceptions import ConfigError

        cfg = MagicMock()
        cfg.validate.side_effect = ConfigError("bad risk rails")
        monkeypatch.setattr(cli_main.Config, "load", lambda: cfg)
        out = StringIO()
        with patch("sys.stdout", out):
            code = cli_main.cmd_validate(argparse.Namespace(mode="live"))
        assert code == 1
        assert "Configuration FAILED" in out.getvalue()


class TestCliPilotEdgeCases:
    _FACTORY = "traderos.application.factory.build_orchestrator"

    def test_readiness_not_configured(self):
        orch = MagicMock()
        orch.live_readiness = None
        with patch(self._FACTORY, return_value=orch):
            out, code = _run_exit(
                cli_main.cmd_pilot, pilot_cmd="readiness", mode="paper", json=False
            )
        assert "not configured" in out
        assert code == 1

    def test_dry_run_not_configured(self):
        orch = MagicMock()
        orch.operator_session = None
        with patch(self._FACTORY, return_value=orch):
            out, code = _run_exit(cli_main.cmd_pilot, pilot_cmd="dry-run", mode="paper", json=True)
        assert "Operator workflow service is not configured" in out
        assert code == 1

    def test_dry_run_empty_workflow(self):
        orch = MagicMock()
        orch.operator_session.workflow.next_step.return_value = None
        with patch(self._FACTORY, return_value=orch):
            out, code = _run_exit(cli_main.cmd_pilot, pilot_cmd="dry-run", mode="paper", json=True)
        assert code == 0
        assert json.loads(out) == []

    def test_dry_run_strategy_promotion_skipped(self):
        from traderos.domain.services.operator_workflow import OperatorStep

        orch = MagicMock()
        workflow = orch.operator_session.workflow
        workflow.next_step.side_effect = [OperatorStep.STRATEGY_PROMOTION, None]
        orch.operator_session.repository = MagicMock()
        with patch(self._FACTORY, return_value=orch):
            out, code = _run_exit(cli_main.cmd_pilot, pilot_cmd="dry-run", mode="paper", json=True)
        data = json.loads(out)
        assert data[0]["step"] == "strategy_promotion"
        assert data[0]["ok"] is None
        workflow.advance.assert_called_once()
        orch.operator_session.repository.save.assert_called_once_with(workflow)
        assert code == 0


class TestCliDb:
    def _conn(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.execute.return_value = None
        cur.fetchone.return_value = (1,)
        conn.cursor.return_value.__enter__.return_value = cur
        return conn

    def _cfg(self):
        return MagicMock()

    def _db_patches(self, monkeypatch, cfg, conn):
        monkeypatch.setattr(cli_main.Config, "load", lambda: cfg)
        monkeypatch.setattr(cli_main, "get_connection", lambda c: conn)

    def _cmd_db(self, monkeypatch, **kwargs):
        cfg = self._cfg()
        conn = self._conn()
        self._db_patches(monkeypatch, cfg, conn)
        base = {
            "db_cmd": "migrate",
            "target": 0,
            "backup": None,
            "backup_flag": None,
            "latest": False,
        }
        base.update(kwargs)
        ns = argparse.Namespace(**base)
        out = StringIO()
        with patch("sys.stdout", out):
            cli_main.cmd_db(ns)
        return out.getvalue()

    def test_db_migrate(self, monkeypatch):
        monkeypatch.setattr(cli_main, "migrate", lambda c, **k: None)
        monkeypatch.setattr(cli_main, "get_current_version", lambda c: 6)
        output = self._cmd_db(monkeypatch)
        assert "Schema version: 6" in output

    def test_db_rollback(self, monkeypatch):
        calls = {}

        def _migrate(c, **k):
            calls["target"] = k.get("target_version")

        monkeypatch.setattr(cli_main, "migrate", _migrate)
        monkeypatch.setattr(cli_main, "get_current_version", lambda c: 3)
        output = self._cmd_db(monkeypatch, db_cmd="rollback", target=3)
        assert calls["target"] == 3
        assert "Rolled back to version 3" in output

    def test_db_check_ok(self, monkeypatch):
        monkeypatch.setattr(cli_main, "get_current_version", lambda c: 6)
        output = self._cmd_db(monkeypatch, db_cmd="check")
        assert "Database OK. Schema version: 6" in output

    def test_db_check_fails(self, monkeypatch):
        conn = MagicMock()
        conn.cursor.side_effect = RuntimeError("db corrupt")
        cfg = MagicMock()
        monkeypatch.setattr(cli_main.Config, "load", lambda: cfg)
        monkeypatch.setattr(cli_main, "get_connection", lambda c: conn)
        ns = argparse.Namespace(
            db_cmd="check", target=0, backup=None, backup_flag=None, latest=False
        )
        out = StringIO()
        with patch("sys.stdout", out):
            cli_main.cmd_db(ns)
        assert "Database check FAILED: db corrupt" in out.getvalue()

    def test_db_backup(self, monkeypatch):
        monkeypatch.setattr(cli_main, "create_backup", lambda cfg: "/backups/x.sqlite.gz")
        output = self._cmd_db(monkeypatch, db_cmd="backup")
        assert "Backup created: /backups/x.sqlite.gz" in output

    def test_db_restore_backup_flag(self, monkeypatch):
        seen = {}

        def _restore(path, cfg):
            seen["path"] = path
            return "/restored.db"

        monkeypatch.setattr(cli_main, "restore_backup", _restore)
        output = self._cmd_db(monkeypatch, db_cmd="restore", backup_flag="/backups/x.sqlite.gz")
        assert seen["path"] == "/backups/x.sqlite.gz"
        assert "Database restored: /restored.db" in output

    def test_db_restore_positional(self, monkeypatch):
        seen = {}

        def _restore(path, cfg):
            seen["path"] = path
            return None

        monkeypatch.setattr(cli_main, "restore_backup", _restore)
        output = self._cmd_db(monkeypatch, db_cmd="restore", backup="/backups/y.sqlite.gz")
        assert seen["path"] == "/backups/y.sqlite.gz"
        assert "Database restored." in output

    def test_db_restore_latest(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            cli_main,
            "list_backups",
            lambda: [{"path": "/backups/newest.gz", "size_bytes": 1, "modified": "x"}],
        )

        def _restore(path, cfg):
            seen["path"] = path
            return "/restored"

        monkeypatch.setattr(cli_main, "restore_backup", _restore)
        output = self._cmd_db(monkeypatch, db_cmd="restore", latest=True)
        assert seen["path"] == "/backups/newest.gz"
        assert "Database restored" in output

    def test_db_restore_latest_no_backups(self, monkeypatch):
        monkeypatch.setattr(cli_main, "list_backups", lambda: [])
        output = self._cmd_db(monkeypatch, db_cmd="restore", latest=True)
        assert "No backups found." in output

    def test_db_restore_no_path(self, monkeypatch):
        cfg = self._cfg()
        conn = self._conn()
        self._db_patches(monkeypatch, cfg, conn)
        ns = argparse.Namespace(
            db_cmd="restore", target=0, backup=None, backup_flag=None, latest=False
        )
        out = StringIO()
        code = None
        with patch("sys.stdout", out):
            try:
                cli_main.cmd_db(ns)
            except SystemExit as exc:
                code = exc.code
        assert "No backup specified" in out.getvalue()
        assert code == 1

    def test_db_list_backups(self, monkeypatch):
        monkeypatch.setattr(
            cli_main,
            "list_backups",
            lambda: [{"path": "/a.gz", "size_bytes": 12, "modified": "2026-01-01"}],
        )
        output = self._cmd_db(monkeypatch, db_cmd="list-backups")
        assert "/a.gz" in output
        assert "12 bytes" in output

    def test_db_list_backups_empty(self, monkeypatch):
        monkeypatch.setattr(cli_main, "list_backups", lambda: [])
        output = self._cmd_db(monkeypatch, db_cmd="list-backups")
        assert "No backups found." in output


class TestCliMainDispatchRemaining:
    def test_main_audit_verify(self):
        svc = MagicMock()
        svc.verify_chain.return_value = True
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=svc):
            output = _run_main(["audit", "verify"])
        assert "PASS" in output

    def test_main_signal(self):
        output = _run_main(["signal", str(uuid.uuid4())])
        assert "Active signals" in output

    def test_main_daemon(self):
        orch = MagicMock()
        cfg = MagicMock()
        with (
            patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch),
            patch("traderos.interfaces.cli.main.Config.load", return_value=cfg),
        ):
            output = _run_main(["daemon", "run"])
        orch.run_forever.assert_called_once()
        assert output == ""

    def test_main_run(self):
        orch = MagicMock()
        cfg = MagicMock()
        with (
            patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch),
            patch("traderos.interfaces.cli.main.Config.load", return_value=cfg),
        ):
            output = _run_main(["run", "--interval", "1"])
        orch.run_forever.assert_called_once_with(interval_seconds=1)
        assert output == ""

    def test_main_db(self, monkeypatch):
        cfg = MagicMock()
        conn = MagicMock()
        monkeypatch.setattr(cli_main.Config, "load", lambda: cfg)
        monkeypatch.setattr(cli_main, "get_connection", lambda c: conn)
        monkeypatch.setattr(cli_main, "migrate", lambda c, **k: None)
        monkeypatch.setattr(cli_main, "get_current_version", lambda c: 6)
        output = _run_main(["db", "migrate"])
        assert "Schema version: 6" in output

    def test_main_validate(self, monkeypatch):
        cfg = MagicMock()
        monkeypatch.setattr(cli_main.Config, "load", lambda: cfg)
        output = _run_main(["validate"])
        assert "Configuration OK" in output

    def test_main_pilot_readiness(self):
        output = _run_main(["pilot", "readiness"])
        assert "Controlled-pilot readiness" in output

    def test_main_risk_status(self):
        output = _run_main(["risk", "status"])
        assert "Kill switch:" in output

    def test_main_metrics_snapshot(self):
        output = _run_main(["metrics", "snapshot"])
        assert "Metrics snapshot" in output
