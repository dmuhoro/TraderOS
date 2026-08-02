# TraderOS — Next Steps to Completion

**Tracked from:** 2026-08-02 · **Programme Ω:** Execution-Evidence delivery

## Programme Ω — definition

**Goal.** Produce the first genuine, command-logged execution records the
repository has ever contained: a real pilot dry-run rehearsal, a real
backup→restore drill, and a real rollback drill — each with attached raw
evidence in `docs/evidence/`. No simulated `PASS`. A correctly-labelled
`PARTIAL` beats a false `DONE`.

**Acceptance criteria (Ω-DoD).**
1. `pilot dry-run` succeeds from a genuinely fresh checkout with **zero
   manual setup steps**.
2. At least one real, timestamped, command-logged execution record exists in
   `docs/evidence/` for each of: dry-run rehearsal, backup/restore, rollback.
3. `AUDIT_GROUND_TRUTH.md`, this file, and `FINISH_LINE_DASHBOARD.md` state
   only what is genuinely proven.
4. The reported result is the honest status of each evidence type, with the
   exact blocker (if any) that prevents `DONE`.

## Programme Ω — result (2026-08-02)

| Gate | Status | Blocker (if any) |
|---|---|---|
| Ω-1 Bootstrap fix | **DONE** | — |
| Ω-2 Pilot dry-run rehearsal (real Alpaca paper) | **DONE** (workflow READY / exit 0) | — (paper account; real-money live still requires operator-controlled pilot) |
| Ω-3 Backup→restore | **DONE** (SHA-256 round-trip equal) | — |
| Ω-4 Rollback | **DONE** (6→3→6, integrity `ok`) | — |
| Ω-5 Governance update | **DONE** | — |

## Work package tracker

| ID | Work package | Status | Evidence |
|---|---|---|---|
| Ω-1 | Bootstrap fix (auto-create runtime dirs in `Config.load`) | **DONE** | regression test added; verified in fresh dir |
| Ω-2 | Run `pilot dry-run` rehearsal for real (broker/paper wired) | **DONE** | `docs/evidence/2026-08-02_dry_run_paper_rehearsal.log` (real Alpaca paper account) |
| Ω-3 | Backup → restore drill (populated DB, timed, checksums) | **DONE** | `docs/evidence/2026-08-02_backup_restore_drill.log` |
| Ω-4 | Rollback drill (prior migration, timed, verified) | **DONE** | `docs/evidence/2026-08-02_rollback_drill.log` |
| Ω-5 | Governance evidence-only update | **DONE** | this file + `AUDIT_GROUND_TRUTH.md` §Δ + `FINISH_LINE_DASHBOARD.md`

> Status cells are updated only from actual evidence (Task 4); never from aspiration.
