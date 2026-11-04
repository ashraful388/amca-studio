# AMCA Studio Canonical Regression Harness

**Milestone**: `AG-AMCA-STUDIO-V1.0.0-I1C-CANONICAL-REGRESSION-HARNESS-AND-TEST-DISCOVERY-NORMALIZATION-01`

---

## Overview

`scripts/run_regression.py` is the single authoritative entry point for the AMCA Studio canonical regression suite. It orchestrates backend and frontend test discovery/execution, TypeScript validation, production frontend build, same-SHA provenance capture, and test-universe manifest generation.

---

## Commands

### Full regression (all suites)

```powershell
python scripts/run_regression.py
```

### Backend only

```powershell
python scripts/run_regression.py --backend-only
```

### Frontend only (tests + tsc + build)

```powershell
python scripts/run_regression.py --frontend-only
```

### Generate manifest without running tests

```powershell
python scripts/run_regression.py --manifest-only
```

### Skip TypeScript check or build step

```powershell
python scripts/run_regression.py --skip-tsc
python scripts/run_regression.py --skip-build
```

---

## Discovery Rules

### Backend

- **Root**: `software/tests/`
- **Discovery**: `pytest --collect-only` — pytest discovers all `test_*.py` files under the root per `pyproject.toml` configuration
- **Exclusions**: none currently
- **Expected count**: derived from actual discovery (not hardcoded)

### Frontend

- **Root**: `software/desktop-ui/tests/`
- **Discovery**: glob `tests/*.test.mjs` — all `.test.mjs` files in the test directory are discovered automatically
- **Runner**: `node --experimental-strip-types --test tests/*.test.mjs`
- **Normalization**: New valid test files added to `software/desktop-ui/tests/` are automatically included without modifying any runner configuration
- **Exclusions**: none currently

---

## Current Test-Universe Counts

Observed on canonical head at time of I1C milestone:

| Suite | Files | Tests |
| :--- | :--- | :--- |
| Backend | 191 | 2,296 (188 collected by pytest, includes ZCode restored files) |
| Frontend | 59 | ~573 |

> The authoritative current count is always produced by running `--manifest-only` against HEAD — never rely on historical numbers.

---

## Same-SHA Provenance

Every invocation captures:

| Field | Description |
| :--- | :--- |
| `sha` | Full `git rev-parse HEAD` SHA |
| `branch` | Current branch name |
| `is_dirty` | `true` if working tree has uncommitted changes |
| `dirty_files` | List of modified/untracked files (up to all) |
| `timestamp` | UTC ISO-8601 timestamp of harness invocation |

**This prevents future claims that a test result came from a different SHA than it actually did.**

Provenance is printed to stdout at harness startup and embedded in the generated manifest.

---

## Test-Universe Manifest

Location: `software/docs/architecture/test_universe_manifest.json`

The manifest is regenerated on every harness run (including `--manifest-only`). It is derived from actual file-system discovery — never manually maintained.

Contents:

```json
{
  "generated_at": "...",
  "generated_by": "scripts/run_regression.py",
  "provenance": { "sha": "...", "branch": "...", "is_dirty": false, ... },
  "backend": {
    "root": "software/tests",
    "discovery": "pytest --collect-only",
    "file_count": 191,
    "files": [ ... ],
    "exclusions": [],
    "exclusion_reasons": []
  },
  "frontend": {
    "root": "software/desktop-ui/tests",
    "discovery": "glob: tests/*.test.mjs",
    "file_count": 59,
    "files": [ ... ],
    "exclusions": [],
    "exclusion_reasons": []
  }
}
```

---

## Failure Behavior

The harness:
- Returns **exit code 1** when any suite fails
- Returns **exit code 2** on configuration/discovery errors
- Returns **exit code 0** only when all executed suites pass
- **Never** auto-reruns until green
- **Never** converts failures into skips
- **Never** hides collection errors
- Preserves original failure output (all subprocess output flows to stdout/stderr)

> For the current canonical tree, known backend failures (owned by ZCode's I1.2 reconciliation) may remain. The harness correctly detects and reports them without masking.

---

## Regression Guard

`software/tests/test_studio_v100_regression_harness_guard.py` (12 tests) validates:

- Harness script exists and has no syntax errors
- Backend root exists and is not empty (cannot be silently excluded)
- Frontend glob discovery identifies all eligible `.test.mjs` files (not just legacy 13)
- Manifest was generated and contains all required fields
- Manifest backend count reflects actual discovery (not hardcoded)
- Provenance SHA is captured in manifest
- Dirty working-tree state is disclosed in manifest
- Exclusions are always reported (even if empty list)
- `--manifest-only` exits 0 successfully

---

## Known Limitations

1. **TypeScript check**: Requires `npm run type-check` script in `package.json` or `npx tsc --noEmit`. Falls back to the latter if the npm script is absent.
2. **Frontend runner**: Uses `node --experimental-strip-types` which is experimental. This matches the existing established pattern in the repository.
3. **Cargo/Tauri build**: Not included — the harness targets the software regression suite only, not native binary compilation.
4. **Backend failures**: 64 backend failures on the canonical B head are owned by ZCode's I1.2 reconciliation task. The harness correctly reports them without repair.
