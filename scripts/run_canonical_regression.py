#!/usr/bin/env python3
"""Canonical AMCA Studio regression harness.

Single deterministic entry point that executes the ENTIRE canonical test
universe (backend + frontend) plus TypeScript and production build
validation, records same-SHA provenance, and reports exact counts without
hard-coding any expected numbers.

Work order: OPENCODE-AMCA-STUDIO-V1.0.0-I1C-CANONICAL-REGRESSION-HARNESS-AND-
TEST-DISCOVERY-NORMALIZATION-01

Usage:
    python scripts/run_canonical_regression.py [--backend-only]
                                               [--no-frontend]
                                               [--no-ts]
                                               [--no-build]
                                               [--manifest PATH]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOFTWARE_ROOT = REPO_ROOT
BACKEND_TESTS = SOFTWARE_ROOT / "tests"
FRONTEND_ROOT = SOFTWARE_ROOT / "desktop-ui"
FRONTEND_TESTS = FRONTEND_ROOT / "tests"
DEFAULT_MANIFEST = BACKEND_TESTS / "test-universe-manifest.json"

#: Explicit exclusions with documented reasons. Kept minimal; any test that
#: belongs to the canonical universe must run by default.
DEFAULT_EXCLUSIONS = {
    "backend": {},
    "frontend": {},
}

FRONTEND_TEST_GLOBS = ("tests/**/*.test.mjs", "tests/*.test.js", "tests/*.test.ts")


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _resolve_cmd(cmd):
    """Resolve a command list so Windows .cmd shims (npx/npm) are found.

    On Windows, ``npx``/``npm`` are ``.cmd`` batch files that subprocess cannot
    launch by bare name without a shell. ``shutil.which`` locates the real
    ``.cmd`` path so the call works cross-platform without shell=True.
    """
    import shutil
    exe = shutil.which(cmd[0])
    if exe:
        return [exe] + list(cmd[1:])
    return list(cmd)


def run_capture(cmd, cwd):
    """Run a command and capture combined output. Returns (returncode, output)."""
    result = subprocess.run(
        _resolve_cmd(cmd),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = (result.stdout or "") + (result.stderr or "")
    return result.returncode, combined


def discover_backend_files() -> list[str]:
    """Discover all *.py files under software/tests/."""
    if not BACKEND_TESTS.is_dir():
        return []
    return sorted(
        p.name
        for p in BACKEND_TESTS.glob("*.py")
        if p.name.startswith("test_") and p.name not in DEFAULT_EXCLUSIONS["backend"]
    )


def discover_frontend_files() -> list[str]:
    """Discover every eligible frontend test file under desktop-ui/tests/."""
    if not FRONTEND_TESTS.is_dir():
        return []
    files = []
    for pattern in ("*.test.mjs", "*.test.js", "*.test.ts", "*.test.mts", "*.test.cts"):
        files.extend(str(p.name) for p in FRONTEND_TESTS.glob(pattern))
    return sorted(dict.fromkeys(files))


def git_provenance() -> dict:
    """Return same-SHA provenance for the current working tree."""
    branch = ""
    sha = ""
    dirty = True
    for cmd in (
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain"],
    ):
        rc, out = run_capture(cmd, SOFTWARE_ROOT)
        if rc == 0:
            text = out.strip()
            if "HEAD" in cmd:
                if cmd[0] == "git" and cmd[1] == "rev-parse" and cmd[2] == "--abbrev-ref":
                    branch = text
                else:
                    sha = text
            elif cmd[1] == "status":
                dirty = bool(text)
    return {
        "branch": branch,
        "sha": sha,
        "clean": not dirty,
        "dirty": dirty,
    }


def collect_backend_summary() -> dict:
    """Run pytest discovery on the backend test root and report counts."""
    cmd = [
        sys.executable, "-m", "pytest",
        str(BACKEND_TESTS),
        "--collect-only", "-q",
    ]
    for excluded in DEFAULT_EXCLUSIONS["backend"]:
        cmd.extend(["--ignore", str(BACKEND_TESTS / excluded)])
    rc, out = run_capture(cmd, SOFTWARE_ROOT)
    lines = out.strip().splitlines()
    discovered = []
    for line in lines:
        if line.startswith("tests/"):
            discovered.append(line.split("::")[0])
    # Last line is the summary like "2305 tests collected in 2.10s"
    summary = lines[-1] if lines else ""
    collected = 0
    import re
    m = re.search(r"(\d+) tests? collected", summary)
    if m:
        collected = int(m.group(1))
    return {
        "root": str(BACKEND_TESTS),
        "collection_rc": rc,
        "collection_summary": summary,
        "discovered_test_file_count": len(sorted(set(discovered))),
        "collected": collected,
        "collection_errors": 1 if rc != 0 else 0,
    }


def run_backend() -> dict:
    """Run the full backend suite with exact per-state counts."""
    cmd = [
        sys.executable, "-m", "pytest",
        str(BACKEND_TESTS),
        "-p", "no:cacheprovider",
    ]
    for excluded in DEFAULT_EXCLUSIONS["backend"]:
        cmd.extend(["--ignore", str(BACKEND_TESTS / excluded)])
    rc, out = run_capture(cmd, SOFTWARE_ROOT)
    tail = out.strip().splitlines()
    summary_line = ""
    for line in reversed(tail):
        if " passed" in line or " failed" in line or " collected" in line:
            summary_line = line
            break
    import re
    counts = {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0, "deselected": 0}
    if rc == 0:
        m = re.search(r"(\d+) passed", summary_line)
        if m:
            counts["passed"] = int(m.group(1))
        m = re.search(r"(\d+) skipped", summary_line)
        if m:
            counts["skipped"] = int(m.group(1))
        m = re.search(r"(\d+) deselected", summary_line)
        if m:
            counts["deselected"] = int(m.group(1))
    else:
        m = re.search(r"(\d+) failed", summary_line)
        if m:
            counts["failed"] = int(m.group(1))
        m = re.search(r"(\d+) passed", summary_line)
        if m:
            counts["passed"] = int(m.group(1))
        m = re.search(r"(\d+) skipped", summary_line)
        if m:
            counts["skipped"] = int(m.group(1))
        m = re.search(r"(\d+) xfailed", summary_line)
        if m:
            counts["xfailed"] = int(m.group(1))
        m = re.search(r"(\d+) xpassed", summary_line)
        if m:
            counts["xpassed"] = int(m.group(1))
    return {
        "returncode": rc,
        "summary": summary_line,
        "counts": counts,
        "exclusions": list(DEFAULT_EXCLUSIONS["backend"]),
    }


def run_frontend() -> dict:
    """Run every eligible frontend test file via node --test."""
    files = discover_frontend_files()
    if not files:
        return {
            "returncode": 0,
            "test_file_count": 0,
            "test_count": 0,
            "summary": "no frontend test files discovered",
            "counts": {},
        }
    cmd = ["node", "--test"] + [str(FRONTEND_TESTS / f) for f in files]
    rc, out = run_capture(cmd, FRONTEND_ROOT)
    import re
    test_count = 0
    passed = 0
    failed = 0
    for line in out.splitlines():
        m = re.search(r"# tests (\d+)", line)
        if m:
            test_count = int(m.group(1))
        m = re.search(r"# pass (\d+)", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"# fail (\d+)", line)
        if m:
            failed = int(m.group(1))
    return {
        "returncode": rc,
        "test_file_count": len(files),
        "test_count": test_count,
        "summary": f"{test_count} tests, {passed} pass, {failed} fail",
        "counts": {"passed": passed, "failed": failed},
    }


def run_ts() -> dict:
    cmd = ["npx", "tsc", "--noEmit"]
    rc, out = run_capture(cmd, FRONTEND_ROOT)
    return {"returncode": rc, "output_tail": out.strip().splitlines()[-5:]}


def run_build() -> dict:
    cmd = ["npm", "run", "build"]
    rc, out = run_capture(cmd, FRONTEND_ROOT)
    return {"returncode": rc, "output_tail": out.strip().splitlines()[-5:]}


def write_manifest(discovery: dict, path: Path) -> None:
    manifest = {
        "version": "1.0.0",
        "generated_at": utc_now(),
        "discovery_method": {
            "backend": "python -m pytest <root> --collect-only -q",
            "frontend": "glob desktop-ui/tests/*.test.{mjs,js,ts,mts,cts}",
        },
        "backend": {
            "root": "software/tests",
            "files": discovery["backend_files"],
            "file_count": len(discovery["backend_files"]),
        },
        "frontend": {
            "root": "desktop-ui/tests",
            "files": discovery["frontend_files"],
            "file_count": len(discovery["frontend_files"]),
        },
        "exclusions": DEFAULT_EXCLUSIONS,
        "exclusion_reason": (
            "No temporary exclusions. test_review_center_ui_contract.py was "
            "restored to normal discovery in I1.5 after its prerequisites "
            "(record_automation_review_item, AutomationStore, ReviewQueueStore) "
            "became importable in the reconciled canonical head."
        ),
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical AMCA Studio regression harness")
    parser.add_argument("--backend-only", action="store_true", help="Run backend tests only")
    parser.add_argument("--no-frontend", action="store_true", help="Skip frontend tests")
    parser.add_argument("--no-ts", action="store_true", help="Skip TypeScript validation")
    parser.add_argument("--no-build", action="store_true", help="Skip production build")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Manifest output path")
    args = parser.parse_args()

    provenance = git_provenance()
    backend_files = discover_backend_files()
    frontend_files = discover_frontend_files()
    discovery = {
        "backend_files": backend_files,
        "frontend_files": frontend_files,
    }
    write_manifest(discovery, Path(args.manifest))

    report: dict = {
        "generated_at": utc_now(),
        "provenance": provenance,
        "backend": {},
        "frontend": {},
        "typescript": None,
        "build": None,
    }

    # Backend always runs.
    discovery_summary = collect_backend_summary()
    backend_result = run_backend()
    report["backend"] = {
        "discovery": discovery_summary,
        "result": backend_result,
    }

    # Frontend unless disabled.
    if not args.backend_only and not args.no_frontend:
        report["frontend"] = run_frontend()

    # TypeScript.
    if not args.backend_only and not args.no_ts:
        report["typescript"] = run_ts()

    # Production build.
    if not args.backend_only and not args.no_build:
        report["build"] = run_build()

    # Print the report.
    print(json.dumps(report, indent=2, sort_keys=True))

    # Failure integrity: non-zero exit when anything failed.
    exit_code = 0
    if report["backend"]["result"]["returncode"] != 0:
        exit_code = 1
    if report.get("frontend") and report["frontend"].get("returncode", 0) != 0:
        exit_code = 1
    if report.get("typescript") and report["typescript"].get("returncode", 0) != 0:
        exit_code = 1
    if report.get("build") and report["build"].get("returncode", 0) != 0:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
