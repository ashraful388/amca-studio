#!/usr/bin/env python3
"""Canonical AMCA Studio certification gate.

Deterministic certification layer on top of the canonical regression
harness. AMCA cannot be declared canonical/release-ready unless:

- all evidence belongs to the SAME SHA (same-SHA integrity);
- mandatory regression gates pass (backend failures = 0, collection
  errors = 0, frontend failures = 0, TypeScript PASS, build PASS);
- every exclusion has an explicit reason and temporary exclusions are
  clearly marked;
- the working tree is clean (unless audit-only mode is explicitly
  requested);
- the active version contract is consistent across metadata locations.

Work order: OPENCODE-AMCA-STUDIO-V1.0.0-I1D-CANONICAL-CERTIFICATION-GATE-AND-
RELEASE-READINESS-AUTOMATION-01

Usage:
    python scripts/run_canonical_certification.py
    python scripts/run_canonical_certification.py --audit-only
    python scripts/run_canonical_certification.py --skip-regression
    python scripts/run_canonical_certification.py --result PATH
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
BACKEND_TESTS = SOFTWARE_ROOT / "tests"
FRONTEND_ROOT = SOFTWARE_ROOT / "desktop-ui"
FRONTEND_TESTS = FRONTEND_ROOT / "tests"
SCRIPTS = SOFTWARE_ROOT / "scripts"
HARNESS = SCRIPTS / "run_canonical_regression.py"
DEFAULT_MANIFEST = BACKEND_TESTS / "test-universe-manifest.json"
DEFAULT_RESULT = BACKEND_TESTS / "certification-result.json"

#: Expected active version contract (approved 0.9.0 metadata contract).
EXPECTED_VERSION = "0.9.0"

#: Version metadata locations that must agree.
VERSION_FILES = {
    "pyproject.toml": r'^version\s*=\s*"([^"]+)"',
    "desktop-ui/package.json": r'"version"\s*:\s*"([^"]+)"',
    "desktop-ui/src-tauri/tauri.conf.json": r'"version"\s*:\s*"([^"]+)"',
    "desktop-ui/src-tauri/Cargo.toml": r'^version\s*=\s*"([^"]+)"',
    "src/amca/__init__.py": r'__version__\s*=\s*"([^"]+)"',
}


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run_capture(cmd, cwd):
    cwd_str = str(cwd)
    if not os.path.isdir(cwd_str):
        cwd_str = None
    result = subprocess.run(
        cmd, cwd=cwd_str, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    combined = (result.stdout or "") + (result.stderr or "")
    return result.returncode, combined


def git_provenance() -> dict:
    branch, sha = "", ""
    dirty = True
    rc, out = run_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"], SOFTWARE_ROOT)
    if rc == 0:
        branch = out.strip()
    rc, out = run_capture(["git", "rev-parse", "HEAD"], SOFTWARE_ROOT)
    if rc == 0:
        sha = out.strip()
    rc, out = run_capture(["git", "status", "--porcelain"], SOFTWARE_ROOT)
    if rc == 0:
        dirty = bool(out.strip())
    return {"branch": branch, "sha": sha, "clean": not dirty, "dirty": dirty}


def read_version(file_path: str) -> str | None:
    target = SOFTWARE_ROOT / file_path
    if not target.is_file():
        return None
    text = target.read_text(encoding="utf-8")
    pattern = VERSION_FILES[file_path]
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip()


def check_version_contract() -> dict:
    """Verify the active version contract is consistent across metadata files."""
    versions = {}
    for rel_path in VERSION_FILES:
        versions[rel_path] = read_version(rel_path)
    all_consistent = all(v == EXPECTED_VERSION for v in versions.values() if v is not None)
    mismatches = {k: v for k, v in versions.items() if v != EXPECTED_VERSION}
    return {
        "expected": EXPECTED_VERSION,
        "observed": versions,
        "consistent": all_consistent,
        "mismatches": mismatches,
    }


def check_same_sha(result: dict) -> dict:
    """Verify all evidence shares one SHA."""
    sha = result.get("provenance", {}).get("sha", "")
    sources = {
        "provenance_sha": sha,
        "backend_sha": (result.get("backend") or {}).get("sha"),
        "frontend_sha": (result.get("frontend") or {}).get("sha"),
        "build_sha": (result.get("build") or {}).get("sha"),
        "version_contract_sha": (result.get("version_contract") or {}).get("sha"),
    }
    non_null = {k: v for k, v in sources.items() if v}
    all_match = bool(sha) and all(v == sha for v in non_null.values())
    return {"sources": sources, "all_match": all_match}


def check_exclusions(manifest: dict) -> dict:
    """Verify every exclusion has an explicit reason."""
    exclusions = manifest.get("exclusions", {})
    reason = manifest.get("exclusion_reason", "")
    unexplained = {}
    for kind in ("backend", "frontend"):
        for key in exclusions.get(kind, {}):
            entry = exclusions[kind][key]
            if isinstance(entry, str):
                if not entry.strip():
                    unexplained[f"{kind}:{key}"] = "no reason given"
            elif isinstance(entry, dict):
                if not entry.get("reason", "").strip():
                    unexplained[f"{kind}:{key}"] = "no reason given"
    # The temporary review-center exclusion must be explicitly marked.
    temp_marked = False
    rc = exclusions.get("backend", {}).get("test_review_center_ui_contract.py")
    if isinstance(rc, str):
        temp_marked = "temporary" in rc.lower() or "I1.3" in rc
    elif isinstance(rc, dict):
        temp_marked = rc.get("temporary") or "temporary" in str(rc.get("reason", "")).lower()
    return {
        "unexplained": unexplained,
        "all_explained": not unexplained,
        "review_center_marked_temporary": temp_marked,
    }


def check_gates(backend: dict, frontend: dict, typescript: dict, build: dict) -> dict:
    """Evaluate mandatory regression gates."""
    b = backend.get("result", {})
    counts = b.get("counts", {})
    gates = {
        "backend_failures_zero": counts.get("failed", 0) == 0,
        "collection_errors_zero": backend.get("discovery", {}).get("collection_errors", 0) == 0,
        "frontend_failures_zero": (frontend or {}).get("counts", {}).get("failed", 0) == 0,
        "typescript_pass": (typescript or {}).get("returncode") == 0,
        "build_pass": (build or {}).get("returncode") == 0,
    }
    return {"gates": gates, "all_pass": all(gates.values())}


def build_certification(audit_only: bool, skip_regression: bool, result_path: Path) -> dict:
    """Run the certification and produce a machine-readable verdict."""
    provenance = git_provenance()
    report: dict = {
        "generated_at": utc_now(),
        "certification": {
            "command": "python scripts/run_canonical_certification.py",
            "audit_only": audit_only,
            "sha": provenance["sha"],
            "branch": provenance["branch"],
            "clean": provenance["clean"],
            "dirty": provenance["dirty"],
        },
        "provenance": provenance,
        "version_contract": check_version_contract(),
        "exclusions": {},
        "backend": {},
        "frontend": None,
        "typescript": None,
        "build": None,
    }

    # Load manifest for exclusion governance.
    manifest = {}
    if DEFAULT_MANIFEST.is_file():
        try:
            manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    report["exclusions"] = check_exclusions(manifest)

    # Run the regression harness unless skipped.
    if not skip_regression:
        cmd = [sys.executable, str(HARNESS)]
        proc = subprocess.run(
            cmd, cwd=str(SOFTWARE_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        rc = proc.returncode
        # The harness prints its JSON report on stdout; stderr may carry
        # unrelated tooling noise (npx/npm/git warnings), so parse stdout only.
        out = proc.stdout or ""
        try:
            harness_result = json.loads(out)
        except json.JSONDecodeError:
            # Fall back to the last balanced JSON object in the stream.
            start = out.find("{")
            end = out.rfind("}")
            harness_result = {"raw": out[-2000:]}
            if start != -1 and end != -1 and end > start:
                try:
                    harness_result = json.loads(out[start:end + 1])
                except json.JSONDecodeError:
                    pass
        report["backend"] = harness_result.get("backend") or {}
        report["frontend"] = harness_result.get("frontend")
        report["typescript"] = harness_result.get("typescript")
        report["build"] = harness_result.get("build")
        report["harness_returncode"] = rc
    else:
        report["backend"] = {"sha": provenance["sha"]}
        report["frontend"] = {"sha": provenance["sha"]}
        report["typescript"] = {"returncode": None}
        report["build"] = {"returncode": None}
        report["harness_returncode"] = None

    # Same-SHA integrity.
    report["same_sha"] = check_same_sha(report)

    # Mandatory gates.
    report["gates"] = check_gates(
        report["backend"], report.get("frontend"), report.get("typescript"), report.get("build")
    )

    # Working-tree integrity.
    working_tree_ok = not report["provenance"]["dirty"] or audit_only

    # Version contract.
    version_ok = report["version_contract"]["consistent"]

    # Exclusions.
    exclusions_ok = report["exclusions"]["all_explained"]

    # Overall verdict: PASS / FAIL / BLOCKED.
    # - PASS    = all required gates pass and evidence is present.
    # - FAIL    = evidence exists and violates a gate.
    # - BLOCKED = required evidence/prerequisite unavailable (e.g. the
    #             mandatory regression/build stages were not executed, or
    #             provenance/version-contract could not be read).
    verdict_parts = []
    if report["same_sha"]["all_match"]:
        verdict_parts.append("same-SHA OK")
    else:
        verdict_parts.append("same-SHA FAIL")
    if report["gates"]["all_pass"]:
        verdict_parts.append("gates PASS")
    else:
        verdict_parts.append("gates FAIL")
    if version_ok:
        verdict_parts.append("version OK")
    else:
        verdict_parts.append("version FAIL")
    if exclusions_ok:
        verdict_parts.append("exclusions OK")
    else:
        verdict_parts.append("exclusions FAIL")
    if working_tree_ok:
        verdict_parts.append("tree OK")
    else:
        verdict_parts.append("tree FAIL")

    # Detect BLOCKED conditions: mandatory evidence not produced.
    blocked_reasons = []
    if skip_regression:
        blocked_reasons.append("regression evidence missing (--skip-regression used)")
    if report.get("harness_returncode") is None and not skip_regression:
        blocked_reasons.append("harness did not run to completion")
    if report.get("backend", {}).get("result") is None:
        blocked_reasons.append("backend result absent")
    if report.get("build") is not None and report["build"].get("returncode") is None \
            and not skip_regression:
        blocked_reasons.append("mandatory build stage not executed")
    if not report.get("provenance", {}).get("sha"):
        blocked_reasons.append("required provenance unavailable")

    gates_blocked = not report["gates"].get("all_pass") and any(
        v is None for v in report["gates"].get("gates", {}).values()
    )
    if gates_blocked:
        blocked_reasons.append("gate result missing (incomplete evidence)")

    evidence_missing = report["same_sha"].get("all_match") is not True or not report.get(
        "version_contract", {}
    ).get("observed")
    if evidence_missing:
        blocked_reasons.append("required evidence not fully available")

    if blocked_reasons:
        overall = "BLOCKED"
    elif (
        report["same_sha"]["all_match"]
        and report["gates"]["all_pass"]
        and version_ok
        and exclusions_ok
        and working_tree_ok
    ):
        overall = "PASS"
    else:
        overall = "FAIL"
    report["overall_verdict"] = overall
    report["blocked_reasons"] = blocked_reasons
    report["verdict_detail"] = "; ".join(verdict_parts)

    # Write machine-readable result.
    result_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical AMCA Studio certification gate")
    parser.add_argument("--audit-only", action="store_true",
                        help="Report dirty-tree state without failing on it")
    parser.add_argument("--skip-regression", action="store_true",
                        help="Skip running the regression harness (provenance only)")
    parser.add_argument("--result", default=str(DEFAULT_RESULT), help="Result output path")
    args = parser.parse_args()

    report = build_certification(args.audit_only, args.skip_regression, Path(args.result))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
