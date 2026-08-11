from __future__ import annotations

import argparse
import json
import uuid
from io import StringIO
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


class TestCliStrategies:
    def test_list_strategies_json(self):
        output = _run(cli_main.cmd_strategies, json=True, name=None)
        data = json.loads(output)
        assert "strategies" in data

    def test_list_strategies_text(self):
        output = _run(cli_main.cmd_strategies, json=False, name=None)
        assert "Registered strategies" in output

    def test_get_strategy_json(self):
        output = _run(cli_main.cmd_strategies, json=True, name="mean_reversion")
        data = json.loads(output)
        assert data["name"] == "mean_reversion"

    def test_get_strategy_not_found_json(self):
        output = _run(cli_main.cmd_strategies, json=True, name="invalid")
        data = json.loads(output)
        assert "error" in data

    def test_get_strategy_text(self):
        output = _run(cli_main.cmd_strategies, json=False, name="mean_reversion")
        assert "Strategy: mean_reversion" in output

    def test_get_strategy_not_found_text(self):
        output = _run(cli_main.cmd_strategies, json=False, name="invalid")
        assert "not found" in output


class TestCliBacktest:
    def test_backtest_text(self):
        output = _run(cli_main.cmd_backtest, strategy="mean_reversion", candles=10)
        assert "Total Return" in output

    def test_backtest_strategy_not_found(self):
        output = _run(cli_main.cmd_backtest, strategy="invalid", candles=10)
        assert "Unknown strategy" in output


class TestCliPaperTrade:
    def test_paper_create_text(self):
        output = _run(cli_main.cmd_paper, paper_cmd="create")
        assert "Created session" in output

    def test_paper_list_text(self):
        from traderos.application.factory import build_orchestrator

        orch = build_orchestrator(mode="paper")
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            _run(cli_main.cmd_paper, paper_cmd="create")
            output = _run(cli_main.cmd_paper, paper_cmd="list")
        assert len(output) > 0


class TestCliHealth:
    def test_health_text(self):
        output = _run(cli_main.cmd_health, json=False)
        assert "[PASS]" in output or "System Health" in output

    def test_health_json(self):
        output = _run(cli_main.cmd_health, json=True)
        data = json.loads(output)
        assert "version" in data
        assert "services" in data


class TestCliAudit:
    def _svc(self):
        from traderos.infrastructure.audit import AuditService

        return AuditService()

    def test_audit_text(self):
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=self._svc()):
            output = _run(cli_main.cmd_audit, limit=5, json=False)
        assert len(output) > 0

    def test_audit_json(self):
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=self._svc()):
            output = _run(cli_main.cmd_audit, limit=5, json=True)
        data = json.loads(output)
        assert isinstance(data, list)


class TestCliNotify:
    def test_notify_info(self):
        output = _run(cli_main.cmd_notify, level="info", title="TestMessage", message="")
        assert "TestMessage" in output
        assert "Sent" in output

    def test_notify_error(self):
        output = _run(cli_main.cmd_notify, level="error", title="ErrMsg", message="")
        assert "ErrMsg" in output


class TestCliSignal:
    def test_signal_json(self):
        mid = str(uuid.uuid4())
        output = _run(cli_main.cmd_signal, market_id=mid, json=True)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_signal_text(self):
        mid = str(uuid.uuid4())
        output = _run(cli_main.cmd_signal, market_id=mid, json=False)
        assert len(output) > 0


class TestCliMainDispatch:
    def test_main_no_command(self):
        output = _run_main([])
        assert "usage" in output.lower() or "TraderOS" in output

    def test_main_health(self):
        output = _run_main(["health"])
        assert "[PASS]" in output

    def test_main_strategies(self):
        output = _run_main(["strategies"])
        assert "Registered strategies" in output

    def test_main_backtest(self):
        output = _run_main(["backtest", "mean_reversion", "--candles", "5"])
        assert "Total Return" in output

    def test_main_papertrade_create(self):
        output = _run_main(["papertrade", "create"])
        assert "Created session" in output

    def test_main_audit(self):
        output = _run_main(["audit", "--limit", "3"])
        assert len(output) > 0

    def test_main_audit_query(self):
        output = _run_main(["audit", "query", "--filter", "action=crash.recovery"])
        assert len(output) > 0

    def test_main_status(self):
        output = _run_main(["status"])
        assert "Mode: paper" in output

    def test_main_notify(self):
        output = _run_main(["notify", "--level", "info", "--title", "Hello"])
        assert "Hello" in output


class TestCliAuditQuery:
    def _svc(self):
        from traderos.infrastructure.audit import AuditService

        svc = AuditService()
        svc.record("crash.recovery", "system", "daemon", "recovered from crash")
        svc.record("order.placed", "operator", "broker", "fill")
        return svc

    def test_filter_text(self):
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=self._svc()):
            output = _run(
                cli_main.cmd_audit_query, filter="action=crash.recovery", limit=10, json=False
            )
        assert "crash.recovery" in output
        assert "order.placed" not in output

    def test_filter_json(self):
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=self._svc()):
            output = _run(
                cli_main.cmd_audit_query, filter="action=crash.recovery", limit=10, json=True
            )
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["action"] == "crash.recovery"

    def test_filter_no_match(self):
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=self._svc()):
            output = _run(
                cli_main.cmd_audit_query, filter="action=does.not.exist", limit=10, json=False
            )
        assert "No audit entries match the filter" in output

    def test_audit_trail_unavailable_fails_closed(self):
        def _raise(*_args, **_kwargs):
            raise RuntimeError("no such table: audit_log")

        with patch("traderos.interfaces.cli.main._build_audit_service", side_effect=_raise):
            out = StringIO()
            with patch("sys.stdout", out):
                try:
                    cli_main.cmd_audit_query(
                        argparse.Namespace(filter="action=crash.recovery", limit=10, json=False)
                    )
                except SystemExit as exc:
                    assert exc.code == 1
                else:
                    raise AssertionError("cmd_audit_query must fail closed")
        assert "Audit trail unavailable" in out.getvalue()


class TestCliStatus:
    def _orch(self):
        from traderos.application.factory import build_orchestrator

        return build_orchestrator(mode="paper")

    def test_status_text(self):
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=self._orch()):
            output = _run(cli_main.cmd_status, mode="paper", json=False)
        assert "Mode: paper" in output
        assert "Kill switch:" in output
        assert "Order acceptance" in output

    def test_status_json(self):
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=self._orch()):
            output = _run(cli_main.cmd_status, mode="paper", json=True)
        data = json.loads(output)
        assert data["mode"] == "paper"
        assert "orders_accepted" in data
        assert "crash_recovered" in data


class TestCliRun:
    def test_run_starts_engine(self):

        orch = MagicMock()
        cfg = MagicMock()
        with (
            patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch),
            patch("traderos.interfaces.cli.main.Config.load", return_value=cfg),
        ):
            _run(cli_main.cmd_run, mode="paper", interval=60)
        orch.run_forever.assert_called_once_with(interval_seconds=60)


class TestCliRiskStatus:
    def _orch(self):
        from traderos.application.factory import build_orchestrator

        return build_orchestrator(mode="paper")

    def test_status_orders_accepted_token(self):
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=self._orch()):
            output = _run(cli_main.cmd_risk, risk_cmd="status", mode="paper", json=True)
        data = json.loads(output)
        assert "orders_accepted" in data

    def test_reconcile_status(self):
        orch = self._orch()
        out = StringIO()
        with (
            patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch),
            patch("sys.stdout", out),
        ):
            try:
                cli_main.cmd_risk(
                    argparse.Namespace(
                        risk_cmd="reconcile", mode="paper", verb="status", json=False
                    )
                )
            except SystemExit:
                pass
        assert "Reconciliation gate" in out.getvalue()


class TestCliPilot:
    def _run_pilot(self, cmd: str, json: bool) -> str:
        from traderos.application.factory import build_orchestrator

        orch = build_orchestrator(mode="paper")
        ns = argparse.Namespace(pilot_cmd=cmd, mode="paper", json=json)
        out = StringIO()
        with (
            patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch),
            patch("sys.stdout", out),
        ):
            try:
                cli_main.cmd_pilot(ns)
            except SystemExit:
                pass
        return out.getvalue()

    def test_pilot_readiness_text(self):
        output = self._run_pilot("readiness", json=False)
        assert "Controlled-pilot readiness" in output
        assert "[PASS]" in output
        assert "broker_connected" in output

    def test_pilot_readiness_json(self):
        output = self._run_pilot("readiness", json=True)
        data = json.loads(output)
        assert "checks" in data
        assert "ready" in data
        assert isinstance(data["checks"], dict)

    def test_pilot_dry_run_text(self):
        output = self._run_pilot("dry-run", json=False)
        assert "preflight" in output
        assert "[PASS]" in output or "[FAIL]" in output

    def test_pilot_dry_run_json(self):
        output = self._run_pilot("dry-run", json=True)
        data = json.loads(output)
        assert isinstance(data, list)
        assert all("step" in row and "ok" in row for row in data)


class TestCliSecurity:
    def _run_security(self, json: bool) -> tuple[str, int | None]:
        out = StringIO()
        exit_code = None
        with patch("sys.stdout", out):
            try:
                cli_main.cmd_security(argparse.Namespace(json=json, security_cmd="audit"))
            except SystemExit as exc:
                exit_code = exc.code
        return out.getvalue(), exit_code

    def test_security_audit_text(self, monkeypatch):
        monkeypatch.delenv("TRADEROS_ENV", raising=False)
        output, code = self._run_security(json=False)
        assert code == 0
        assert "Security posture" in output
        assert "Verdict: SECURE" in output
        assert "[PASS] auth" in output

    def test_security_audit_json(self, monkeypatch):
        monkeypatch.delenv("TRADEROS_ENV", raising=False)
        output, code = self._run_security(json=True)
        assert code == 0
        data = json.loads(output)
        assert data["environment"] == "development"
        assert data["verdict"] == "SECURE"
        assert isinstance(data["findings"], list)

    def test_security_audit_production_open_fails(self, monkeypatch):
        monkeypatch.setenv("TRADEROS_ENV", "production")
        output, code = self._run_security(json=False)
        assert code == 1
        assert "[FAIL] auth" in output
        assert "Verdict: INSUFFICIENT" in output

    def test_security_main_dispatch(self, monkeypatch):
        monkeypatch.delenv("TRADEROS_ENV", raising=False)
        output = _run_main(["security", "audit"])
        assert "Security posture" in output


class TestCliEdgeCases:
    def test_paper_not_available(self):
        from traderos.application.factory import build_orchestrator

        orch = build_orchestrator(mode="paper")
        orch.paper = None
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_paper, paper_cmd="create")
        assert "not available" in output

    def test_audit_no_entries(self):
        from traderos.infrastructure.audit import AuditService

        svc = AuditService()
        svc._entries = []
        with patch("traderos.interfaces.cli.main._build_audit_service", return_value=svc):
            output = _run(cli_main.cmd_audit, limit=5, json=False)
        assert "No audit entries" in output

    def test_signal_no_markets(self):

        orch = MagicMock()
        orch.market_ids = []
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_signal, market_id=None, json=False)
        assert "No markets configured" in output
