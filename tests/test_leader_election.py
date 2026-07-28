from __future__ import annotations

import os
import tempfile

import pytest

from traderos.infrastructure.leader_election import FileBasedLeaderElection


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
