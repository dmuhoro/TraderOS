from __future__ import annotations

import argparse
import json
import uuid
from io import StringIO
from unittest.mock import patch

from traderos.interfaces.cli import main as cli_main


def _run(cmd_func, **kwargs) -> str:
    ns = argparse.Namespace(**kwargs)
    out = StringIO()
    with patch("sys.stdout", out):
        cmd_func(ns)
    return out.getvalue()


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
    def test_audit_text(self):
        output = _run(cli_main.cmd_audit, limit=5, json=False)
        assert len(output) > 0

    def test_audit_json(self):
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

    def test_main_notify(self):
        output = _run_main(["notify", "--level", "info", "--title", "Hello"])
        assert "Hello" in output


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
        with patch("traderos.interfaces.cli.main.AuditService", return_value=svc):
            output = _run(cli_main.cmd_audit, limit=5, json=False)
        assert "No audit entries" in output

    def test_signal_no_markets(self):
        from unittest.mock import MagicMock

        orch = MagicMock()
        orch.market_ids = []
        with patch("traderos.interfaces.cli.main.build_orchestrator", return_value=orch):
            output = _run(cli_main.cmd_signal, market_id=None, json=False)
        assert "No markets configured" in output
