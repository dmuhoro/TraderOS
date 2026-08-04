"""G-07 governance: release signing round-trip + fail-closed live gate."""

from __future__ import annotations

import pytest

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

        artifact = tmp_path / "policy.md"
        artifact.write_text("# Live Run Policy\n")
        sign(artifact, "RELEASE_SIGNING_KEY")

        from scripts.governance.live_gate import main as gate

        assert gate(["--artifact", str(artifact)]) == 0

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
