# ADR-008: Audit Chain — SHA256 over Canonical Serialization

**Status:** Adopted  
**Date:** 2026-07-29  
**Driver:** WP-10.1 — replace non-deterministic `hash()` with deterministic SHA256  

## Context

The audit chain links entries via `previous_hash`. The original implementation used Python's built-in `hash()` function — which is non-deterministic across interpreter restarts (PYTHONHASHSEED randomization), not cryptographically secure, and platform-dependent. Additionally, the pipe-delimiter serialization (`"|".join(parts)`) creates an ambiguity bug: any field containing `|` silently corrupts the canonical payload.

## Decision

1. Replace `hash()` with `hashlib.sha256().hexdigest()` over a canonical JSON-serialized payload.
2. Canonical serialization uses `json.dumps(fields, separators=(",", ":"), ensure_ascii=True)` — a list (not dict) preserves field order and eliminates delimiter-collision bugs.
3. Do **not** retroactively rehash existing entries in the database. Entries recorded before this ADR was deployed retain their original `hash()` values. This creates a verifiable chain boundary at the deployment timestamp.

## Pre-fix Chain Boundary

- **Legacy entries** (pre-ADR-008): hash computed with `hash("|".join(parts))` — non-deterministic, pipe-delimiter serialization.
- **Post-fix entries** (ADR-008 onward): hash computed with SHA256 over canonical JSON — deterministic, cryptographically secure.
- `verify_chain()` recomputes the expected hash using the **current** method (SHA256). Legacy entries will **fail** verification because `hash()` is non-deterministic across restarts. This is an accepted consequence: the pre-fix chain is considered a best-effort audit trail, not a cryptographically verifiable one.
- Operators with access to the pre-fix database can manually verify legacy entries by running the same `hash()` call within a single interpreter session (pre-fix entries recorded in the same process are verifiable until restart).

## Consequences

- **Positive:** Deterministic, collision-resistant, tamper-evident audit hashes. Pipe-delimiter bug eliminated. Cryptographically secure.
- **Negative:** Existing audit entries recorded before this ADR cannot be re-verified after an interpreter restart. The chain has a permanent seam at the deployment boundary.
- **Mitigation:** Record a chain-boundary audit entry at deployment time that explicitly links the last legacy hash to the first SHA256 entry, documenting the transition.

## Implementation

- `infrastructure/audit.py` exports `compute_audit_hash(...)` used by all three audit backends (InMemory, SQLite, PostgreSQL).
- Canonical form: `json.dumps([id, action, actor, resource, detail, timestamp_iso, previous_hash], separators=(",", ":"))`.
- Hash: `hashlib.sha256(canonical.encode("utf-8")).hexdigest()`.
