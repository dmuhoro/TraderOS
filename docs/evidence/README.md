# Programme Ω — Execution Evidence

This directory holds **raw, timestamped execution records** for Programme Ω
(the genuine-run evidence programme). Each file is captured directly from a
real command run — never simulated, never narrated.

## Conventions

1. **Redaction.** Any credential that appears in a captured command is masked
   as `***REDACTED***` before the log is saved. Secrets are never committed.
2. **Fidelity.** Files are the actual command + stdout/exit code; timestamps
   are captured at run time. No post-hoc embellishment.
3. **Status labels.** An evidence file's header states `STATUS: DONE` or
   `STATUS: PARTIAL` and, if partial, the exact blocker (e.g. missing live
   credentials of a human-owned step).

## Index

| File | Programme step | Status |
|---|---|---|
| `2026-08-02_dry_run_paper_rehearsal.log` | Pilot dry-run rehearsal | **DONE** (real Alpaca paper account, workflow READY / exit 0) |
| `2026-08-02_backup_restore_drill.log` | Backup → restore drill | **DONE** (SHA-256 round-trip equal) |
| `2026-08-02_rollback_drill.log` | Deployment rollback drill | **DONE** (6→3→6, integrity ok) |
