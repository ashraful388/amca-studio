#!/usr/bin/env python3
"""H1I acceptance sentinel for ZCode's upcoming v1.0.1 three-artifact
implementation (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1I-ZCODE-THREE-ARTIFACT-IMPLEMENTATION-ACCEPTANCE-SENTINEL-01
Read-only acceptance preparation. Modifies no ZCode-owned production/build
code, builds nothing, promotes nothing. Tests the OBSERVABLE CONTRACT of
the eight ZCode surfaces without assuming exact implementation function
names: each check looks for the role-aware behavior the work order
requires and returns an honest BLOCKED (never a crash, never a soft
pass) while the implementation is absent.

Run against ZCode's implementation SHA (checkout or worktree root):
  python scripts/v101_zcode_three_artifact_acceptance_sentinel.py \
      [--repo-root PATH] [--output report.json]

Returns per-surface matrix + overall:
  H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE=PASS|BLOCKED

Historical compatibility: every check is v1.0.1-scoped; v1.0.0 remains a
valid EXE + Portable two-artifact lineage and is never retroactively
required to carry an MSI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "amca-v101-zcode-acceptance-sentinel/1"

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# (surface id, milestone label, file, regexes that must ALL match the
#  observable source contract, human-readable requirement)
CONTRACT_CHECKS = [
    ("S1-build-descriptors",
     "distribution_build.py: EXE + MSI + Portable descriptors; manifest "
     "and SHA256SUMS include all three",
     "software/src/amca/distribution_build.py",
     [r"msi", r"SHA256SUMS"],
     'msi package-type/descriptor handling present'),
    ("S2-packaging-names",
     "release_packaging.py: required v1.0.1 names include .exe, .msi and "
     "portable .zip",
     "software/src/amca/release_packaging.py",
     [r"\.msi"],
     'v1.0.1 required-name list includes an .msi artifact'),
    ("S3-promote-inventory-map",
     "promote_final_release.py: windows-msi role mandatory for v1.0.1",
     "software/scripts/promote_final_release.py",
     [r"windows-msi"],
     'promotion artifact map includes a windows-msi role'),
    ("S4-promotion-gate",
     "final_promotion.py: MSI smoke is a mandatory promotion gate; no "
     "READY_FOR_PROMOTION without MSI PASS",
     "software/src/amca/final_promotion.py",
     [r"msi_smoke", r"gates\s*=[\s\S]{0,400}msi_smoke"],
     'msi_smoke gate created AND included in the gate list'),
    ("S5-mandatory-inventory",
     "MANDATORY_INVENTORY requires all three Windows artifacts",
     "software/src/amca/final_promotion.py",
     [r"MANDATORY_INVENTORY[\s\S]{0,400}windows-msi"],
     'windows-msi present inside MANDATORY_INVENTORY'),
    ("S6-closeout-count",
     "release_finalization.py: v1.0.1 artifact count = 3",
     "software/src/amca/release_finalization.py",
     [r"expected_public_artifact_count|artifacts_count",
      r"get\(key, 3\)|artifacts_count[^\n]*3"],
     'close-out declares three artifacts for v1.0.1 (version-aware '
     'close-out contract; H2B.3 semantic-drift resolution keeps the '
     'requirement, matching the authorized H2A implementation shape)'),
    ("S7-msi-smoke-runner",
     "distribution_smoke.py: MSI install / backend health / desktop "
     "launch / uninstall / residual-installation check",
     "software/src/amca/distribution_smoke.py",
     [r"msiexec", r"uninstall", r"residual"],
     'MSI lifecycle smoke steps present (install, uninstall, residual)'),
    ("S8-authenticity",
     "installer/package authenticity: EXE MZ, MSI real OLE compound "
     "file, Portable valid ZIP; EXE renamed .msi must fail",
     "software/src/amca/distribution_build.py",
     [r"0xcf|OLE|MSI_MAGIC|d0cf11e0"],
     'role-aware magic validation includes the OLE compound-file '
     'signature for MSI'),
]


def _read(repo_root: Path, rel: str) -> Optional[str]:
    path = repo_root / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def run_sentinel(repo_root: Path) -> Dict[str, Any]:
    matrix: List[Dict[str, Any]] = []
    blockers: List[str] = []
    for surface, requirement, rel, patterns, label in CONTRACT_CHECKS:
        text = _read(repo_root, rel)
        entry: Dict[str, Any] = {"surface": surface, "file": rel,
                                 "requirement": requirement}
        if text is None:
            entry["verdict"] = "BLOCKED"
            entry["reason"] = f"target file missing: {rel}"
        else:
            missing = [p for p in patterns
                       if not re.search(p, text, re.IGNORECASE)]
            if missing:
                entry["verdict"] = "BLOCKED"
                entry["reason"] = (
                    f"observable contract not met ({label}); unmatched "
                    f"patterns: {missing}")
            else:
                entry["verdict"] = "PASS"
                entry["reason"] = "observable contract satisfied"
        if entry["verdict"] != "PASS":
            blockers.append(f"{surface}: {entry['reason']}")
        matrix.append(entry)
    overall = "BLOCKED" if blockers else "PASS"
    return {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_root": str(repo_root),
        "H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE": overall,
        "matrix": matrix,
        "blockers": blockers,
        "historical_rule": ("v1.0.0 remains valid EXE+Portable; MSI is "
                            "never retroactively required for v1.0.0"),
        "behavioral_gate": ("run scripts/run_v101_release_validation.py "
                            "(H1H) against the real built artifacts for "
                            "the executable fail-fast + chain verdicts"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ZCode three-artifact implementation acceptance "
                    "sentinel (H1I)")
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = run_sentinel(Path(args.repo_root))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if report["H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE"] == "PASS" \
        else 1


if __name__ == "__main__":
    raise SystemExit(main())
