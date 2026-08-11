from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from traderos.infrastructure.leader_election import FileBasedLeaderElection
from traderos.infrastructure.leader_election import LeaderElection


class FakeConn:
    """Minimal stand-in for a DB connection exposing execute() -> cursor."""

    def __init__(self, acquire: bool = True) -> None:
        self._acquire = acquire
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> MagicMock:
        self.calls.append((sql, params))
        cur = MagicMock()
        cur.fetchone.return_value = (self._acquire,)
        return cur


class TestLeaderElection:
    def test_acquires_leadership_and_fires_callback(self) -> None:
        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)
        elected: list[str] = []
        election.on_elected(lambda: elected.append("elected"))
        assert election.try_acquire() is True
        assert election.is_leader is True
        assert elected == ["elected"]

    def test_acquire_when_already_leader_no_duplicate_callback(self) -> None:
        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)
        elected: list[str] = []
        election.on_elected(lambda: elected.append("elected"))
        election.try_acquire()
        election.try_acquire()
        assert elected == ["elected"]

    def test_lost_leadership_fires_deposed(self) -> None:
        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)
        deposed: list[str] = []
        election.on_deposed(lambda: deposed.append("deposed"))
        assert election.try_acquire() is True
        conn._acquire = False
        assert election.try_acquire() is False
        assert election.is_leader is False
        assert deposed == ["deposed"]

    def test_connection_error_releases_leadership(self) -> None:
        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)
        deposed: list[str] = []
        election.on_deposed(lambda: deposed.append("deposed"))
        election.try_acquire()

        def boom(*_args, **_kwargs):
            raise RuntimeError("db gone")

        conn.execute = boom
        assert election.try_acquire() is False
        assert election.is_leader is False
        assert deposed == ["deposed"]

    def test_connection_error_when_not_leader(self) -> None:
        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)

        def boom(*_args, **_kwargs):
            raise RuntimeError("db gone")

        conn.execute = boom
        assert election.try_acquire() is False
        assert election.is_leader is False

    def test_release_unlocks(self) -> None:
        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)
        election.try_acquire()
        election.release()
        assert election.is_leader is False
        assert conn.calls[-1][0].startswith("SELECT pg_advisory_unlock")

    def test_release_tolerates_connection_error(self) -> None:
        conn = FakeConn(acquire=True)

        def boom(*_args, **_kwargs):
            raise RuntimeError("db gone")

        conn.execute = boom
        election = LeaderElection(conn, lock_id=42)
        election.release()
        assert election.is_leader is False

    def test_start_stop_heartbeat(self) -> None:
        from traderos.infrastructure import leader_election as mod

        conn = FakeConn(acquire=True)
        election = LeaderElection(conn, lock_id=42)
        old = mod.HEARTBEAT_INTERVAL
        mod.HEARTBEAT_INTERVAL = 0.02
        try:
            election.start()
            assert election.is_leader is True
            election.stop()
        finally:
            mod.HEARTBEAT_INTERVAL = old
        assert election.is_leader is False


class TestFileBasedLeaderElection:
    @pytest.fixture
    def lock_path(self) -> str:
        with tempfile.NamedTemporaryFile(suffix=".lock", delete=False) as f:
            path = f.name
        yield path
        try:
            os.unlink(path)
        except OSError:
            pass

    def test_acquire_release(self, lock_path: str):
        election = FileBasedLeaderElection(lock_path)
        assert election.try_acquire() is True
        assert election.is_leader is True
        election.release()
        assert election.is_leader is False

    def test_only_one_leader(self, lock_path: str):
        e1 = FileBasedLeaderElection(lock_path)
        e2 = FileBasedLeaderElection(lock_path)
        assert e1.try_acquire() is True
        assert e1.is_leader is True
        assert e2.try_acquire() is False
        assert e2.is_leader is False
        e1.release()
        assert e2.try_acquire() is True
        assert e2.is_leader is True

    def test_on_elected_callback(self, lock_path: str):
        calls = []
        election = FileBasedLeaderElection(lock_path)
        election.on_elected(lambda: calls.append("elected"))
        election.try_acquire()
        assert calls == ["elected"]

    def test_on_deposed_callback(self, lock_path: str):
        e1 = FileBasedLeaderElection(lock_path)
        e1.try_acquire()
        depose_calls = []
        e1.on_deposed(lambda: depose_calls.append("deposed"))
        import fcntl

        e1._lock_file.close()
        e1._lock_file = None
        with open(lock_path, "w") as f2:
            fcntl.flock(f2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            e1.try_acquire()
        assert depose_calls == ["deposed"]

    def test_start_stop(self, lock_path: str):
        election = FileBasedLeaderElection(lock_path)
        election.start()
        assert election.is_leader is True
        election.stop()
        assert election.is_leader is False
        assert not os.path.exists(lock_path)

    def test_release_tolerates_flock_failure(self, lock_path: str, monkeypatch):
        import fcntl

        election = FileBasedLeaderElection(lock_path)
        assert election.try_acquire() is True

        def _boom(*_args, **_kwargs):
            raise OSError("lock gone")

        monkeypatch.setattr(fcntl, "flock", _boom)
        election.release()
        assert election.is_leader is False

    def test_stop_tolerates_missing_lock_file(self, lock_path: str):
        election = FileBasedLeaderElection(lock_path)
        assert election.try_acquire() is True
        election.release()
        os.unlink(lock_path)  # lock file removed externally before stop
        election.stop()
        assert election.is_leader is False
