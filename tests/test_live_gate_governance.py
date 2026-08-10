"""G-07 governance: release signing round-trip, operator acknowledgment, and
the fail-closed live gate."""

from __future__ import annotations

import pytest

from scripts.governance.operator_ack import ack as operator_ack
from scripts.governance.operator_ack import verify as verify_ack
from scripts.governance.sign_release import sign
from scripts.governance.sign_release import verify


@pytest.fixture
def artifact(tmp_path):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"release artifact bytes 0x1234")
    return f


@pytest.fixture
def sig_dir(tmp_path, monkeypatch):
    d = tmp_path / "sigs"
    monkeypatch.setenv("RELEASE_SIG_DIR", str(d))
    return d


class TestReleaseSigning:
    def test_sign_then_verify_roundtrip(self, artifact, sig_dir, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        assert sign(artifact, "RELEASE_SIGNING_KEY") == 0
        assert verify(artifact, "RELEASE_SIGNING_KEY") == 0

    def test_verify_rejects_tampered_artifact(self, artifact, sig_dir, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        sign(artifact, "RELEASE_SIGNING_KEY")
        artifact.write_bytes(b"tampered")
        assert verify(artifact, "RELEASE_SIGNING_KEY") == 1

    def test_verify_fails_closed_without_signature(self, artifact, sig_dir, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        assert verify(artifact, "RELEASE_SIGNING_KEY") == 1

    def test_verify_fails_closed_when_key_absent(self, artifact, sig_dir, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        sign(artifact, "RELEASE_SIGNING_KEY")
        monkeypatch.delenv("RELEASE_SIGNING_KEY")
        with pytest.raises(SystemExit):
            verify(artifact, "RELEASE_SIGNING_KEY")


class TestOperatorAck:
    def test_ack_then_verify_roundtrip(self, artifact, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        monkeypatch.setenv("OPERATOR_NAME", "Jane On-Call")
        monkeypatch.setenv("OPERATOR_ROLE", "on-call")
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(artifact.parent / "acks"))
        assert operator_ack(artifact) == 0
        assert verify_ack(artifact) == 0

    def test_verify_fails_closed_without_ack(self, artifact, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(artifact.parent / "acks"))
        assert verify_ack(artifact) == 1

    def test_verify_rejects_tampered_policy(self, artifact, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        monkeypatch.setenv("OPERATOR_NAME", "Jane On-Call")
        monkeypatch.setenv("OPERATOR_ROLE", "on-call")
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(artifact.parent / "acks"))
        operator_ack(artifact)
        artifact.write_bytes(b"tampered policy text")
        assert verify_ack(artifact) == 1

    def test_ack_requires_operator_identity(self, artifact, monkeypatch) -> None:
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "a-real-drill-key")
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(artifact.parent / "acks"))
        monkeypatch.delenv("OPERATOR_NAME", raising=False)
        monkeypatch.delenv("OPERATOR_ROLE", raising=False)
        assert operator_ack(artifact) == 1


class TestLiveGate:
    def test_paper_mode_is_not_blocked(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "paper")
        from scripts.governance.live_gate import main as gate

        assert gate([]) == 0

    def test_live_mode_fails_closed_without_go_declaration(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "LIVEGATEKEY1234567890")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "livesecretvalue123456")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        monkeypatch.setenv("GO_CONDITIONS_MET", "false")
        from scripts.governance.live_gate import main as gate

        assert gate([]) == 1

    def test_live_mode_passes_with_all_go_conditions(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "LIVEGATEKEY1234567890")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "livesecretvalue123456")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        monkeypatch.setenv("GO_CONDITIONS_MET", "true")
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "drill-key")
        monkeypatch.setenv("RELEASE_SIG_DIR", str(tmp_path / "sigs"))
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(tmp_path / "acks"))
        monkeypatch.setenv("OPERATOR_NAME", "Jane On-Call")
        monkeypatch.setenv("OPERATOR_ROLE", "on-call")
        for var in (
            "RISK_DAILY_LOSS_PCT",
            "RISK_MAX_GROSS_EXPOSURE",
            "RISK_MAX_POSITION_SIZE",
            "RISK_MAX_POSITIONS_TOTAL",
            "RISK_MAX_DATA_STALENESS_SECONDS",
            "RISK_ALLOWED_MARKETS",
            "RISK_REQUIRE_ALLOWLIST",
        ):
            monkeypatch.delenv(var, raising=False)

        artifact = tmp_path / "policy.md"
        artifact.write_text("# Live Run Policy\n")
        sign(artifact, "RELEASE_SIGNING_KEY")
        operator_ack(artifact)

        # WP11: a live PASS now also requires explicit production risk rails.
        settings = tmp_path / "settings.yaml"
        settings.write_text(
            "risk:\n"
            "  daily_loss_pct: 0.02\n"
            "  max_gross_exposure: 1.0\n"
            "  max_position_size: 0.25\n"
            "  max_positions_total: 10\n"
            "  require_allowlist: true\n"
            "  allowed_markets:\n"
            "    - AAPL\n"
        )

        from scripts.governance.live_gate import main as gate

        assert gate(["--artifact", str(artifact), "--settings", str(settings)]) == 0

    def test_live_mode_requires_production_risk_rails(self, monkeypatch, tmp_path) -> None:
        """WP11: every other GO condition met, but the numeric risk rails are
        not explicitly configured -> the gate stays closed (fail-closed)."""
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "LIVEGATEKEY1234567890")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "livesecretvalue123456")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        monkeypatch.setenv("GO_CONDITIONS_MET", "true")
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "drill-key")
        monkeypatch.setenv("RELEASE_SIG_DIR", str(tmp_path / "sigs"))
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(tmp_path / "acks"))
        monkeypatch.setenv("OPERATOR_NAME", "Jane On-Call")
        monkeypatch.setenv("OPERATOR_ROLE", "on-call")
        for var in (
            "RISK_DAILY_LOSS_PCT",
            "RISK_MAX_GROSS_EXPOSURE",
            "RISK_MAX_POSITION_SIZE",
            "RISK_MAX_POSITIONS_TOTAL",
            "RISK_MAX_DATA_STALENESS_SECONDS",
            "RISK_ALLOWED_MARKETS",
            "RISK_REQUIRE_ALLOWLIST",
        ):
            monkeypatch.delenv(var, raising=False)

        artifact = tmp_path / "policy.md"
        artifact.write_text("# Live Run Policy\n")
        sign(artifact, "RELEASE_SIGNING_KEY")
        operator_ack(artifact)

        settings = tmp_path / "settings.yaml"
        settings.write_text("risk: {}\n")

        from scripts.governance.live_gate import main as gate

        assert gate(["--artifact", str(artifact), "--settings", str(settings)]) == 1

    def test_live_mode_requires_signed_artifact(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "LIVEGATEKEY1234567890")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "livesecretvalue123456")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        monkeypatch.setenv("GO_CONDITIONS_MET", "true")
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "drill-key")
        monkeypatch.setenv("RELEASE_SIG_DIR", str(tmp_path / "sigs"))

        artifact = tmp_path / "policy.md"
        artifact.write_text("# Live Run Policy\n")

        from scripts.governance.live_gate import main as gate

        assert gate(["--artifact", str(artifact)]) == 1

    def test_live_mode_requires_allowlist_when_enforced(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "LIVEGATEKEY1234567890")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "livesecretvalue123456")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        monkeypatch.setenv("GO_CONDITIONS_MET", "true")
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "drill-key")
        monkeypatch.setenv("RELEASE_SIG_DIR", str(tmp_path / "sigs"))

        artifact = tmp_path / "policy.md"
        artifact.write_text("# Live Run Policy\n")
        sign(artifact, "RELEASE_SIGNING_KEY")

        settings = tmp_path / "settings.yaml"
        settings.write_text("risk:\n  require_allowlist: true\n  allowed_markets: []\n")

        from scripts.governance.live_gate import main as gate

        assert gate(["--artifact", str(artifact), "--settings", str(settings)]) == 1

    def test_live_mode_requires_operator_acknowledgment(self, monkeypatch, tmp_path) -> None:
        """Signed artifact alone must NOT pass: the operator must acknowledge
        the red-lines in writing, else the gate stays closed."""
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "LIVEGATEKEY1234567890")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "livesecretvalue123456")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        monkeypatch.setenv("GO_CONDITIONS_MET", "true")
        monkeypatch.setenv("RELEASE_SIGNING_KEY", "drill-key")
        monkeypatch.setenv("RELEASE_SIG_DIR", str(tmp_path / "sigs"))
        monkeypatch.setenv("OPERATOR_ACK_DIR", str(tmp_path / "acks"))

        artifact = tmp_path / "policy.md"
        artifact.write_text("# Live Run Policy\n")
        sign(artifact, "RELEASE_SIGNING_KEY")

        from scripts.governance.live_gate import main as gate

        assert gate(["--artifact", str(artifact)]) == 1


class TestGovernanceDrill:
    def test_governance_drill_passes(self) -> None:
        """The committed G-07 drill must stay green — signing + operator ack +
        fail-closed live gate enforced in one reproducible run."""
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "evidence" / "run_governance_drill.py"
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VERDICT: PASS" in proc.stdout
        assert "live_gate_fails_closed_without_go" in proc.stdout
