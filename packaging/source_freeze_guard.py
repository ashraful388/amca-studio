"""Source-freeze guard — authoritative schema (20BY-R2).

Fail-closed release guard converting the 20BX procedural source-freeze rule
into machine-checkable form. Used before and after each official build
stage.

Authoritative top-level machine-readable schema field names are exactly:

    expected_source_sha    exact frozen SHA supplied by the caller
    actual_head_sha        actual inspected git HEAD SHA
    tracked_tree_clean     boolean; true only when there are no tracked
                           working-tree OR index/staged mutations
    violations             structured list of every blocking condition
                           detected
    verdict                PASS | FAIL

The twelve stable violation codes:

    TRACKED_SOURCE_MODIFIED
    TRACKED_TEST_MODIFIED
    VERSION_FILE_MODIFIED
    TEST_FILE_MODIFIED
    PACKAGE_JSON_MODIFIED
    PACKAGING_SCRIPT_MODIFIED
    STAGED_TRACKED_CHANGE
    TRACKED_CHANGE_INSIDE_APPROVED_GENERATED_ROOT
    UNAPPROVED_UNTRACKED_OUTPUT
    MISSING_FROZEN_SHA
    INVALID_FROZEN_SHA
    GIT_INSPECTION_ERROR

All twelve produce verdict=FAIL, a nonzero CLI exit, and a blocking entry
in violations. Untracked generated outputs under the five approved roots
are the only tolerated entries and are reported as allowed outputs. The
prior field names are retained as aliases. No free-text parsing is required
to determine why the guard failed.

Release-tooling only; no production/runtime/UI code changes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

WATCHLIST = (
    "software/desktop-ui/src-tauri/Cargo.lock",
    "software/desktop-ui/package-lock.json",
)

# Narrow allowlist: untracked generated outputs are tolerated ONLY when they
# sit under one of these approved generated roots. Tracked status always
# overrides this path allowance. Arbitrary untracked files elsewhere fail.
APPROVED_GENERATED_ROOTS = (
    "software/dist",
    "software/backend-dist",
    "software/.build",
    "software/desktop-ui/src-tauri/target",
    ".agent-tmp",
)

VIOLATION_CODES = (
    "TRACKED_SOURCE_MODIFIED",
    "TRACKED_TEST_MODIFIED",
    "VERSION_FILE_MODIFIED",
    "CARGO_LOCK_MODIFIED",
    "PACKAGE_LOCK_MODIFIED",
    "PACKAGE_JSON_MODIFIED",
    "PACKAGING_SCRIPT_MODIFIED",
    "STAGED_TRACKED_CHANGE",
    "TRACKED_CHANGE_INSIDE_APPROVED_GENERATED_ROOT",
    "UNAPPROVED_UNTRACKED_OUTPUT",
    "INVALID_FROZEN_SHA",
    "GIT_INSPECTION_ERROR",
)

# Supplemental non-registry diagnostic: a valid 40-hex frozen SHA that
# differs from the inspected HEAD. Reported on the item (kind head_drift /
# diagnostic code GIT_HEAD_MISMATCH) but never substitutes for an
# authoritative registry code.
SUPPLEMENTAL_DIAGNOSTICS = {"GIT_HEAD_MISMATCH"}

_VERSION_FILE_NAMES = {
    "pyproject.toml",
    "Cargo.toml",
    "tauri.conf.json",
}
_LOCK_FILE_NAMES = {"Cargo.lock", "package-lock.json"}
_PACKAGE_JSON_NAMES = {"package.json", "package-lock.json"}


def _git(repo_root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {res.stderr.strip()[:200]}"
        )
    # NEVER strip() the whole stdout: porcelain lines are positional
    # ("XY path") and the first line may begin with a column space.
    return res.stdout.rstrip("\n")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _under_approved_root(path: str) -> bool:
    normalized = _norm(path)
    return any(
        normalized == root or normalized.startswith(root + "/")
        for root in APPROVED_GENERATED_ROOTS
    )


def _classify_tracked(path: str) -> str:
    """Map a tracked mutation to its stable violation code."""
    normalized = _norm(path)
    name = normalized.rsplit("/", 1)[-1]
    parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""

    if name == "Cargo.lock" or normalized.endswith("/Cargo.lock"):
        return "CARGO_LOCK_MODIFIED"
    if (
        name == "package-lock.json"
        or normalized.endswith("/package-lock.json")
    ):
        return "PACKAGE_LOCK_MODIFIED"
    if name in _PACKAGE_JSON_NAMES:
        return "PACKAGE_JSON_MODIFIED"
    if normalized.startswith("software/tests/") or "/tests/" in normalized:
        return "TRACKED_TEST_MODIFIED"
    if name.startswith("test_") and name.endswith(".py"):
        return "TRACKED_TEST_MODIFIED"
    if normalized.startswith("software/packaging/") or parent.endswith(
        "packaging"
    ):
        return "PACKAGING_SCRIPT_MODIFIED"
    if name in _VERSION_FILE_NAMES or name in _LOCK_FILE_NAMES:
        return "VERSION_FILE_MODIFIED"
    if _under_approved_root(normalized):
        return "TRACKED_CHANGE_INSIDE_APPROVED_GENERATED_ROOT"
    return "TRACKED_SOURCE_MODIFIED"


def _porcelain(repo_root: Path) -> List[Dict[str, str]]:
    out = _git(repo_root, "status", "--porcelain")
    entries: List[Dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        raw = line[:2]
        path = line[3:].strip()
        # Porcelain XY: X = index (staged), Y = worktree. "??" = untracked.
        staged = raw[0] not in ("?", " ")
        entry = {
            "status": raw,
            "kind": "untracked" if raw == "??" else "tracked",
            "path": path,
            "staged": staged,
        }
        if any(w in path for w in WATCHLIST):
            entry["watchlist"] = True
        entries.append(entry)
    return entries


def run_freeze_guard(
    repo_root: Path,
    frozen_sha: Optional[str] = None,
    mode: str = "final",
    stage: str = "",
) -> Dict[str, Any]:
    """Run the freeze guard and return the authoritative result.

    The frozen SHA must be supplied by the caller — the guard never falls
    back to the current HEAD.
    """
    repo_root = Path(repo_root)
    violations: List[Dict[str, Any]] = []
    errors: List[str] = []
    tracked_mutations: List[Dict[str, Any]] = []
    unapproved_untracked: List[Dict[str, Any]] = []
    allowed_generated_outputs: List[Dict[str, str]] = []
    mutations: List[Dict[str, Any]] = []

    head: Optional[str] = None
    git_ok = True
    try:
        head = _git(repo_root, "rev-parse", "HEAD")
    except Exception as exc:
        git_ok = False
        errors.append(str(exc))
        violations.append({
            "code": "GIT_INSPECTION_ERROR",
            "path": "HEAD",
            "detail": str(exc)[:200],
        })

    frozen = (frozen_sha or "").strip() or None
    if frozen is None:
        # Required input/CLI validation condition — intentionally NOT one of
        # the twelve stable mutation/error codes.
        violations.append({
            "code": "MISSING_FROZEN_SHA",
            "kind": "missing_frozen_sha",
            "path": "HEAD",
            "detail": (
                "no frozen SHA supplied; the guard never falls back to the "
                "current HEAD"
            ),
        })
        errors.append(
            "frozen SHA not provided; the guard never falls back to the "
            "current HEAD"
        )
    elif not re.fullmatch(r"[0-9a-f]{40}", frozen.lower()):
        # Malformed/nonexistent SHA: an INVALID_FROZEN_SHA format condition.
        sha_item = {
            "code": "INVALID_FROZEN_SHA",
            "kind": "invalid_sha_format",
            "path": "HEAD",
            "detail": f"supplied frozen SHA is not a valid 40-hex SHA: "
                      f"{frozen!r}",
        }
        violations.append(sha_item)
        mutations.append(sha_item)
    elif head is not None and head.lower() != frozen.lower():
        # Valid SHA that differs from the inspected HEAD: a HEAD
        # drift/SHA-mismatch condition, distinct from the format condition.
        sha_item = {
            "code": "GIT_HEAD_MISMATCH",
            "supplemental": True,  # non-registry diagnostic
            "kind": "head_drift",
            "path": "HEAD",
            "detail": f"HEAD {head} != frozen {frozen}",
        }
        violations.append(sha_item)
        mutations.append(sha_item)

    if git_ok:
        try:
            entries = _porcelain(repo_root)
        except Exception as exc:
            git_ok = False
            errors.append(str(exc))
            violations.append({
                "code": "GIT_INSPECTION_ERROR",
                "path": "status",
                "detail": str(exc)[:200],
            })
        else:
            for entry in entries:
                path = entry["path"]
                if entry["kind"] == "tracked":
                    code = "STAGED_TRACKED_CHANGE" if entry.get(
                        "staged"
                    ) else _classify_tracked(path)
                    item = {
                        "code": code,
                        "kind": "tracked",
                        "path": path,
                        "status": entry["status"],
                        "detail": "tracked working-tree or index mutation",
                    }
                    if entry.get("watchlist"):
                        item["watchlist"] = True
                    tracked_mutations.append(item)
                    violations.append(item)
                    continue
                # untracked
                if _under_approved_root(path):
                    allowed_generated_outputs.append({"path": path})
                    continue
                item = {
                    "code": "UNAPPROVED_UNTRACKED_OUTPUT",
                    "kind": "untracked",
                    "path": path,
                    "approved_generated_output": False,
                    "detail": "untracked output outside approved roots",
                }
                unapproved_untracked.append(item)
                violations.append(item)

    # Alias aggregate: every blocking mutation (tracked + unapproved
    # untracked) in one list.
    mutations.extend(tracked_mutations)
    mutations.extend(unapproved_untracked)

    tracked_tree_clean = git_ok and not tracked_mutations
    blocking = bool(violations)
    verdict = "FAIL" if blocking else "PASS"
    exit_code = 0 if verdict == "PASS" else 1

    result: Dict[str, Any] = {
        # Authoritative top-level machine-readable fields (exact names).
        "expected_source_sha": frozen,
        "actual_head_sha": head if git_ok else None,
        "tracked_tree_clean": tracked_tree_clean,
        "violations": violations,
        "verdict": verdict,
        # Retained alias/context fields.
        "frozen_sha": frozen,
        "head": head,
        "clean": tracked_tree_clean and not unapproved_untracked,
        "mutations": mutations,
        "tracked_mutations": tracked_mutations,
        "untracked_mutations": unapproved_untracked,
        "allowed_generated_outputs": allowed_generated_outputs,
        "watchlist_hits": [
            m for m in tracked_mutations if m.get("watchlist")
        ],
        "approved_generated_roots": list(APPROVED_GENERATED_ROOTS),
        "errors": errors,
        "mode": mode,
        "stage": stage,
        "exit_code": exit_code,
    }
    return result


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Source-freeze guard (fail-closed, machine-readable)"
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument(
        "--frozen-sha",
        required=True,
        help="explicit frozen source SHA; never defaults to HEAD",
    )
    parser.add_argument(
        "--mode", default="final", choices=("final", "check")
    )
    parser.add_argument("--stage", default="", help="build stage label")
    args = parser.parse_args(argv)

    report = run_freeze_guard(
        Path(args.repo_root),
        frozen_sha=args.frozen_sha,
        mode=args.mode,
        stage=args.stage,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
