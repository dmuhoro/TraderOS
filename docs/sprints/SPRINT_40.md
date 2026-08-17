# Sprint 40 — Release cut: version/record provenance, frozen-dataset discipline fix, drill-set promotion

**Period:** 2026-08-17
**Objective:** Take the repository from "feature-complete but drifting" to a
clean, releaseable state. Three concrete defects were closed without touching
the execution path: (1) the G17/VB6 version/tag drift — the changelog declared
a `1.1.0` release that had no matching git tag, and version sources disagreed
across files; (2) the real-market walk-forward drill silently re-fetched and
overwrote committed frozen market data on every run, destroying the
reproducibility anchor; (3) that same drill was mis-classified as a
network-gated drill despite running deterministically on committed data. All
work is evidence-verified and the release is cut as `v1.2.0`.

---

## 1. G17/VB6 version/tag drift closed (release provenance)

**Problem:** `CHANGELOG.md` listed `[1.1.0]` as released at commit `122f5bb`,
but no `v1.1.0` git tag exists locally or on origin (only `v1.0.0`). Version
sources also disagreed: `pyproject.toml` and `configs/settings.yaml` held
different values as recently as commit `122f5bb` (`1.1.0` vs `0.1.0`).

**Closure:**
- `pyproject.toml`, `configs/settings.yaml`, and
  `configs/settings.production.example.yaml` all now declare `1.2.0`.
- The CI version gate (`.github/workflows/ci.yml`) asserts
  `pyproject.toml == settings.yaml` and that no legacy `VERSION` file is
  tracked — re-verified verbatim, PASS.
- The release workflow (`.github/workflows/release.yml`) asserts the pushed
  tag equals the `pyproject.toml` version — verified against the back-fill
  plan: `v1.1.0` at commit `122f5bb` matches `1.1.0` in that commit's
  `pyproject.toml`.
- This release is cut as `v1.2.0`, matching both the working-tree version and
  the changelog header.

## 2. Frozen-dataset discipline fix

**Problem:** `scripts/evidence/run_real_market_walk_forward.py` documented
itself as running on frozen real data but on every invocation re-downloaded a
year of Binance klines and **overwrote the committed CSV** in
`docs/evidence/frozen/`. Fresh runs thus mutated committed evidence — the
reproducibility anchor drifted with every execution.

**Closure** (`scripts/evidence/run_real_market_walk_forward.py`):
- Default run reuses the newest committed frozen snapshot — zero network, bit
  deterministic (verified: identical fold results before/after across runs).
- `--refresh` writes a **new** dated snapshot and never mutates a committed
  one; the stale freeze anchor (`2026-08-06`) was git-removed and re-created
  under its honest content date `binance_btcusdt_1h_2026-08-17.csv`.
- Fail-closed: an unreadable/corrupt snapshot yields `VERDICT: NO-GO` and
  exit code 2; a failed download yields `NO-GO`, never fabricated data.
- `FROZEN_CSV` dead constant removed; snapshot discovery is by glob on the
  newest dated file.

## 3. Real-market walk-forward promoted into the deterministic CI drill set

**Problem:** `run_real_market_walk_forward.py` was listed in `KEY_GATED`
(credential/network-gated) in `scripts/evidence/run_ci_drills.py`, so it was
not part of the deterministic CI drill suite — despite running entirely on
committed data after the Section 2 fix.

**Closure:**
- Moved into `DRILLS` as `real_market_walk_forward`; the `KEY_GATED` comment
  and list updated to the true network-gated set (real-time streams,
  credentials, managed Postgres, unattended live soak).
- `tests/test_ci_drills_runner.py` inventory expectation updated to the new
  18-member set.
- Verified: `run_ci_drills.py` reports **18/18 PASS**.

## 4. Evidence drift verified and committed

All working-tree drift in `docs/evidence/` was inspected, not assumed: each
modified log carries a genuine `VERDICT: PASS` (or returns 0 via
`SystemExit(main())`), and the frozen CSV rename is a content-honest
re-anchoring. No `NO-GO` or `FAIL` record exists in the staged set.

## 5. Full verification (release gates)

| Check | Result |
|---|---|
| `pytest -q` (full suite) | 2245 passed, 7 skipped, **100.00%** coverage (gate 100) |
| `ruff check .` | All checks passed |
| `black --check .` | 367 files left unchanged |
| `pyright` (strict, `src/traderos`) | 0 errors, 0 warnings |
| CI version gate (`pyproject.toml` vs `settings.yaml`) | PASS |
| CI drill suite | 18/18 PASS |

> Note: the local `pre-commit` `pyright` hook reports `reportMissingModuleSource`
> warnings for `psycopg2`/`requests` in unrelated infra files while running in
> its sealed hook env; CI and the Makefile `typecheck` run `pyright
> src/traderos/` against the fully-installed project and are clean. This is a
> hook-environment artifact, not a project gate, and is unchanged by this
> sprint.

## 6. CI pipeline made genuinely green (three pre-existing gate failures)

**Problem:** the GitHub Actions pipeline was **never green**. Every run back
to sprint-38 failed on three jobs that had nothing to do with the release cut:

| Job | Failure (every run) | Root cause |
|---|---|---|
| `test` | `ModuleNotFoundError: No module named 'pytz'` during collection | `alpaca-py` declares `pytz` only as a transitive requirement; a fresh CI resolution did not install it, so test collection broke on the alpaca import chain. |
| `governance` | `ModuleNotFoundError: No module named 'scripts'` | `scripts/governance/live_gate.py` is executed as a bare script but imports `from scripts.governance.sign_release import ...` (package-relative). Only `src/` was added to `sys.path`, not the repo root, so `scripts` was unresolvable in the runner. |
| `security` | `traderos Dependency not found on PyPI and could not be audited: traderos (1.2.0)` | `pip-audit` attempts to audit the locally editable `traderos` package, which is not published to PyPI — a false failure on a first-party package. |

**Closure:**
- `pyproject.toml`: `pytz>=2020.1` made an explicit member of the `alpaca`
  extra (it is a hard runtime requirement of `alpaca-py==0.30.0`).
- `scripts/governance/live_gate.py`: repo root added to `sys.path` so the
  package-style `scripts.governance.*` imports resolve when the script is run
  as a file.
- `.github/workflows/ci.yml`: `pip-audit --skip-editable` — the audited
  surface is every real third-party dependency; the first-party package is
  covered by tests, not the PyPI advisory feed.
- Verified after the fixes: `pytest` 2245 passed / 7 skipped / 100% coverage,
  ruff, black, pyright, and `pre-commit` all green; `live_gate.py` exits 0 in
  paper mode and 1 (fail-closed) in live mode.

## 7. Governance / honesty notes

- As in SPRINT_39, the `0.2.12` npm-style bump reference is a stale
  transcription; the repo's real version scheme is the package version. The
  released `1.1.0` (changelog entry) is now back-filled as git tag `v1.1.0` at
  its release commit, and this sprint's work is cut as `v1.2.0` — every
  version source agrees and each tag will match its commit's `pyproject.toml`.
- The evidence refresh reflects new drill runs (timestamps 2026-08-17), all
  PASS. The single frozen-data snapshot is committed under an honest name.

## Verification closure

| Check | Result |
|---|---|
| `pytest -q` (full suite) | 2245 passed, 7 skipped, **100.00%** coverage (gate 100) |
| `ruff check .` | All checks passed |
| `black --check .` | all unchanged |
| `pyright` (strict) | 0 errors, 0 warnings |
| CI drill suite | 18/18 PASS |
| CI version gate | PASS (`1.2.0` everywhere, no tracked `VERSION`) |
