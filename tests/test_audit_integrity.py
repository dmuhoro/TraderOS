from __future__ import annotations

import hashlib
import json
import subprocess
import sys

from traderos.infrastructure.audit import compute_audit_hash


def test_sha256_is_deterministic() -> None:
    h1 = compute_audit_hash(
        "id1", "action", "actor", "resource", "detail", "2026-07-29T00:00:00", "prev"
    )
    h2 = compute_audit_hash(
        "id1", "action", "actor", "resource", "detail", "2026-07-29T00:00:00", "prev"
    )
    assert h1 == h2


def test_sha256_known_value() -> None:
    canonical = json.dumps(
        ["id1", "action", "actor", "resource", "detail", "2026-07-29T00:00:00", "prev"],
        separators=(",", ":"),
        sort_keys=False,
        ensure_ascii=True,
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result = compute_audit_hash(
        "id1", "action", "actor", "resource", "detail", "2026-07-29T00:00:00", "prev"
    )
    assert result == expected


def test_different_inputs_produce_different_hashes() -> None:
    h_base = compute_audit_hash("id1", "action", "actor", "r", "d", "2026-01-01T00:00:00", "prev")
    h_diff_action = compute_audit_hash(
        "id1", "DIFFERENT", "actor", "r", "d", "2026-01-01T00:00:00", "prev"
    )
    h_diff_actor = compute_audit_hash(
        "id1", "action", "DIFFERENT", "r", "d", "2026-01-01T00:00:00", "prev"
    )
    h_diff_resource = compute_audit_hash(
        "id1", "action", "actor", "DIFFERENT", "d", "2026-01-01T00:00:00", "prev"
    )
    h_diff_detail = compute_audit_hash(
        "id1", "action", "actor", "r", "DIFFERENT", "2026-01-01T00:00:00", "prev"
    )
    h_diff_ts = compute_audit_hash(
        "id1", "action", "actor", "r", "d", "2026-06-01T00:00:00", "prev"
    )
    h_diff_prev = compute_audit_hash(
        "id1", "action", "actor", "r", "d", "2026-01-01T00:00:00", "DIFFERENT"
    )
    all_hashes = {
        h_base,
        h_diff_action,
        h_diff_actor,
        h_diff_resource,
        h_diff_detail,
        h_diff_ts,
        h_diff_prev,
    }
    assert len(all_hashes) == 7, "Each field change should produce a distinct hash"


def test_canonical_json_excludes_hash_field() -> None:
    canonical = json.dumps(
        ["id1", "action", "actor", "resource", "detail", "2026-01-01T00:00:00", "prev"],
        separators=(",", ":"),
        sort_keys=False,
        ensure_ascii=True,
    )
    assert "hash" not in canonical


def test_hash_is_independent_of_python_hash_seed() -> None:
    script = """from traderos.infrastructure.audit import compute_audit_hash
print(compute_audit_hash("id1","action","actor","r","d","2026-01-01T00:00:00","prev"))
"""
    results = set()
    for seed in (0, 1, 42, 12345, 99999):
        env = {**__import__("os").environ, "PYTHONHASHSEED": str(seed)}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, f"seed={seed} stderr={proc.stderr}"
        results.add(proc.stdout.strip())
    assert len(results) == 1, f"Expected same hash across seeds, got {results}"
