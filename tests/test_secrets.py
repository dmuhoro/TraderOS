from __future__ import annotations

import pytest

from traderos.infrastructure.secrets import EnvSecretProvider
from traderos.infrastructure.secrets import SecretRotator


class TestSecretRotator:
    def test_get_from_env(self):
        rotator = SecretRotator()
        rotator.add_provider(EnvSecretProvider())
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("TEST_SECRET", "secret-value")
            assert rotator.get("TEST_SECRET") == "secret-value"

    def test_get_returns_none_for_missing(self):
        rotator = SecretRotator()
        assert rotator.get("NONEXISTENT_SECRET") is None

    def test_rotate_updates_version(self):
        rotator = SecretRotator()
        rotator.add_provider(EnvSecretProvider())
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ROTATE_KEY", "v1")
            rotator.get("ROTATE_KEY")
            mp.setenv("ROTATE_KEY", "v2")
            assert rotator.rotate("ROTATE_KEY") is True
            assert rotator.get("ROTATE_KEY") == "v2"
            stats = rotator.stats
            assert stats["versions"]["ROTATE_KEY"] == 2

    def test_rotate_all(self):
        rotator = SecretRotator()
        rotator.add_provider(EnvSecretProvider())
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("KEY_A", "a1")
            mp.setenv("KEY_B", "b1")
            rotator.get("KEY_A")
            rotator.get("KEY_B")
            mp.setenv("KEY_A", "a2")
            mp.setenv("KEY_B", "b2")
            count = rotator.rotate_all()
            assert count == 2
            assert rotator.get("KEY_A") == "a2"
            assert rotator.get("KEY_B") == "b2"

    def test_start_stop(self):
        rotator = SecretRotator()
        rotator.start()
        assert rotator._bg_thread is not None
        assert rotator._bg_thread.is_alive()
        rotator.stop()
        assert not rotator._bg_thread.is_alive()

    def test_stats(self):
        rotator = SecretRotator()
        rotator.add_provider(EnvSecretProvider())
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("STAT_KEY", "val")
            rotator.get("STAT_KEY")
            stats = rotator.stats
            assert stats["total_secrets"] == 1
            assert stats["versions"]["STAT_KEY"] == 1

    def test_custom_provider(self):
        rotator = SecretRotator()
        rotator.add_provider(lambda k: "custom-val" if k == "MY_KEY" else None)
        assert rotator.get("MY_KEY") == "custom-val"

    def test_rotate_returns_false_when_no_provider_holds_key(self):
        rotator = SecretRotator()
        rotator.add_provider(lambda k: None)
        assert rotator.rotate("NOT_IN_ANY_PROVIDER") is False

    def test_rotate_custom_provider(self):
        rotator = SecretRotator()
        rotator.add_provider(lambda k: "rotated" if k == "ROTATE_ME" else None)
        assert rotator.rotate("ROTATE_ME") is True
        assert rotator.get("ROTATE_ME") == "rotated"
        assert rotator.stats["versions"]["ROTATE_ME"] == 1

    def _recording_observers(self):
        class _Audit:
            def __init__(self):
                self.entries = []

            def record(self, action, actor, resource, detail=""):
                self.entries.append((action, actor, resource, detail))

        class _Metrics:
            def __init__(self):
                self.counts = {}

            def counter(self, name, delta=1.0):
                self.counts[name] = self.counts.get(name, 0.0) + delta
                return self.counts[name]

        return _Audit(), _Metrics()

    def test_get_and_rotate_record_metrics(self):
        audit, metrics = self._recording_observers()
        rotator = SecretRotator(audit=audit, metrics=metrics)
        rotator.add_provider(lambda k: "v1" if k == "METRIC_KEY" else None)
        rotator.get("METRIC_KEY")
        assert metrics.counts.get("secret.accessed.read.provider") == 1.0
        rotator.rotate("METRIC_KEY")
        assert metrics.counts.get("secret.rotated") == 1.0
        assert audit.entries and any(a == "secret.rotated" for a, _, _, _ in audit.entries)
