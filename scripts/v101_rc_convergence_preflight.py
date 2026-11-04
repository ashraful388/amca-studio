#!/usr/bin/env python3
"""H1G v1.0.1 RC convergence preflight (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1G-POST-H1-CONVERGENCE-AND-THREE-ARTIFACT-RC-PREFLIGHT-01
Preparation/sentinel only. Merges nothing, builds nothing, packages
nothing, promotes nothing, tags nothing. Does not touch ZCode's H1
branch or checkout.

Provides:
  1. integration_order_manifest() — deterministic H1B->H1F integration
     order + RC composition contract.
  2. rc_artifact_contract() — the three v1.0.1 RC artifact names,
     magics, floors, and binding requirements.
  3. post_merge_gate(state) — hard gate that must pass after the RC
     branch is created (H1A evidence bound to the EXACT H1.1 SHA,
     model-health states, U1-U7 persisted rules, no provider hacks,
     three-artifact schemas, no v1.0.0 reuse, D: canonical root).
  4. conflict_forecast(h11_sha) — read-only git sentinel comparing an
     eventual H1.1 implementation SHA against the PS-2 stack: file
     overlap, semantic/schema/version conflicts, merge-tree simulation.
  5. expected_prebuild_state() — the honest state that must hold after
     convergence and before real artifacts exist.
  6. minimal_validation_set() — smallest post-convergence validation
     commands (no full redundant regression).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "amca-v101-rc-convergence-preflight/1"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LINEAGE = [
    ("H1B", "e99c8826"),
    ("H1C", "6e6b2e08"),
    ("H1D", "be1bf538"),
    ("H1E", "05d884e8"),
    ("H1F", "8cc8a827"),
]
PS2_STACK_BASE = "3671cb01"  # canonical path-migration commit (merge base)

MODEL_HEALTH_STATES = [
    "READY", "AUTH_REQUIRED", "MODEL_NOT_FOUND",
    "PROVIDER_UNAVAILABLE", "LOCAL_RUNTIME_OFFLINE",
]

RC_ARTIFACTS = [
    {"role": "exe",
     "name": "amca-studio-v1.0.1-windows-x64-setup.exe",
     "magic": "MZ", "min_bytes": 5_000_000,
     "validator": "v101_artifact_binding.validate_artifact_binding + "
                  "distribution_build installer floor"},
    {"role": "msi",
     "name": "amca-studio-v1.0.1-windows-x64.msi",
     "magic": "d0cf11e0a1b11ae1 (OLE compound file)", "min_bytes": 5_000_000,
     "validator": "v101_artifact_binding.msi_validation_contract() "
                  "execution evidence (MSI_EXECUTION_PENDING until real)"},
    {"role": "portable",
     "name": "amca-studio-v1.0.1-windows-x64-portable.zip",
     "magic": "PK\\x03\\x04", "min_bytes": 5_000_000,
     "validator": "v101_artifact_binding.validate_artifact_binding + "
                  "real-payload executable presence"},
]

GATE_FIELDS = [
    "h1a_acceptance_sha", "h1_1_implementation_sha",
    "model_health_states_intact", "u1_u7_persisted_rules_intact",
    "no_provider_specific_hacks", "three_artifact_schemas_intact",
    "no_v100_artifact_reuse", "canonical_root_d_only",
]


def integration_order_manifest() -> Dict[str, Any]:
    """Deterministic integration order for the v1.0.1 RC line."""
    return {
        "schema": SCHEMA,
        "rc_composition": [
            "certified ZCode H1.1 source (H1_1_IMPLEMENTATION_SHA)",
            "accepted PS-2 stack H1B->H1F (linear, this manifest)",
            "ZCode three-artifact implementation (surfaces 1-6, 8, 9, 11) "
            "on the RC branch — still to come",
        ],
        "integration_order": [
            {"sequence": i + 1, "milestone": m, "commit": sha,
             "prerequisite": None if i == 0 else LINEAGE[i - 1][1],
             "status": "ACCEPTED/CLOSED"}
            for i, (m, sha) in enumerate(LINEAGE)
        ],
        "rc_creation_steps": [
            {"step": 1, "action": "wait for ZCode H1_1_IMPLEMENTATION_SHA",
             "owner": "ZCode"},
            {"step": 2,
             "action": "PS-1 certifies that exact SHA "
                       "(H1A_ACCEPTANCE_SENTINEL=PASS)",
             "owner": "PS-1"},
            {"step": 3,
             "action": "create branch amca-v1.0.1-rc from the certified "
                       "H1.1 SHA",
             "owner": "ZCode (Manager-authorized)"},
            {"step": 4,
             "action": "merge PS-2 stack (H1B->H1F linear history) into RC",
             "owner": "ZCode (Manager-authorized)"},
            {"step": 5, "action": "run post_merge_gate() — hard gate",
             "owner": "PS-2 tooling"},
            {"step": 6,
             "action": "ZCode implements three-artifact build on RC",
             "owner": "ZCode"},
        ],
        "note": "PS-2 stack history is linear from " + PS2_STACK_BASE +
                "; merge order is fixed by ancestry, not preference.",
    }


def rc_artifact_contract() -> Dict[str, Any]:
    return {
        "schema": "amca-v101-rc-artifact-contract/1",
        "release_version": "1.0.1",
        "canonical_root": "D:/ChatGPT/AMCA",
        "artifacts": RC_ARTIFACTS,
        "rules": [
            "all three artifacts built from ONE certified source SHA",
            "each artifact bound by sha256 + size + explicit D: path in "
            "the release manifest and SHA256SUMS",
            "v1.0.0 hashes f9111c3f.../41efd240... rejected as v1.0.1 "
            "evidence",
            "MSI must be a real OLE compound file with execution evidence; "
            "MSI_EXECUTION_PENDING is not promotable",
        ],
    }


def post_merge_gate(state: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Hard post-merge gate. Every condition must be truthfully met;
    anything missing/unverified yields BLOCKED (never a soft pass)."""
    reasons: List[str] = []
    for field in GATE_FIELDS:
        if field not in state:
            reasons.append(f"gate field missing: {field}")
    if reasons:
        return "BLOCKED", reasons
    h1a = state.get("h1a_acceptance_sha")
    h11 = state.get("h1_1_implementation_sha")
    if not h1a or not h11:
        reasons.append("H1A acceptance evidence and H1.1 implementation "
                       "SHA are both mandatory")
    elif h1a != h11:
        reasons.append(
            f"H1A acceptance bound to {str(h1a)[:12]}, but RC carries "
            f"{str(h11)[:12]} — certification must target the EXACT H1.1 "
            "SHA (9b315f6e is NOT final H1)")
    boolean_checks = {
        "model_health_states_intact": "model-health five-state vocabulary",
        "u1_u7_persisted_rules_intact": "U1-U7 persisted-state rules",
        "no_provider_specific_hacks": "no provider-specific hacks",
        "three_artifact_schemas_intact": "three-artifact schemas",
        "no_v100_artifact_reuse": "no v1.0.0 artifact reuse",
        "canonical_root_d_only": "canonical root D: only",
    }
    for field, label in boolean_checks.items():
        if state.get(field) is not True:
            reasons.append(f"gate not met: {label}")
    states = state.get("model_health_states") or MODEL_HEALTH_STATES
    if sorted(states) != sorted(MODEL_HEALTH_STATES):
        reasons.append("model-health state vocabulary incomplete")
    if reasons:
        return "BLOCKED", reasons
    return "PASS", ["post-merge gate satisfied"]


def _git(*args: str) -> str:
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git failed")
    return proc.stdout


def analyze_overlap(theirs: set, ours: set) -> Dict[str, Any]:
    """Pure overlap analysis: file overlap + schema/version/semantic
    conflict classification for the intersecting files."""
    overlap = sorted(theirs & ours)
    return {
        "file_overlap": overlap,
        "schema_conflict": any(
            f.endswith(".py") or "schema" in f.lower() for f in overlap),
        "version_conflict": any(
            "version" in f.lower() or "release" in f.lower()
            for f in overlap),
        "semantic_conflict": bool(overlap),
    }


def conflict_forecast(h11_sha: str) -> Dict[str, Any]:
    """Read-only sentinel: compare an H1.1 implementation SHA against the
    PS-2 stack. Reports file overlap, semantic/schema/version conflicts,
    and a merge-tree simulation. Resolves nothing; writes nothing."""
    report: Dict[str, Any] = {"schema": "amca-v101-conflict-forecast/1",
                              "h11_sha": h11_sha, "errors": []}
    try:
        base = _git("merge-base", h11_sha, LINEAGE[-1][1]).strip()
        theirs = set(_git("diff", "--name-only", f"{base}..{h11_sha}",
                          ).splitlines())
        ours = set(_git("diff", "--name-only",
                        f"{PS2_STACK_BASE}..{LINEAGE[-1][1]}").splitlines())
        report.update(analyze_overlap(theirs, ours))
        tree = _git("merge-tree", "--write-tree", h11_sha,
                    LINEAGE[-1][1]).strip().splitlines()
        report["merge_tree"] = tree[0] if tree else None
        report["merge_clean"] = len(tree) == 1
    except (RuntimeError, subprocess.SubprocessError) as exc:
        report["errors"].append(str(exc))
    return report


def expected_prebuild_state() -> Dict[str, Any]:
    """The honest state that MUST hold after convergence, before real
    artifacts exist. Any deviation is tampering."""
    return {
        "schema": "amca-v101-prebuild-state/1",
        "release_version": "1.0.1",
        "model_response_evidence": "MODEL_RESPONSE_RECEIVED=YES required "
                                   "from H1.1 physical test before RC "
                                   "convergence; evidence must be present",
        "exe_execution": "PENDING_FUTURE_BUILD",
        "msi_execution": "MSI_EXECUTION_PENDING",
        "portable_execution": "PENDING_FUTURE_BUILD",
        "promotion": "BLOCKED",
        "tag": "NONE — no tag before Manager-authorized release",
        "packaging": "NOT AUTHORIZED at preflight",
    }


def minimal_validation_set() -> Dict[str, Any]:
    """Smallest post-convergence validation set (no full redundant
    regression)."""
    return {
        "schema": "amca-v101-minimal-validation-set/1",
        "commands": [
            "PS-1: H1A acceptance sentinel against the exact "
            "H1_1_IMPLEMENTATION_SHA (R1-R6 + U1-U7 + H1A.2)",
            "python -m pytest tests/test_studio_v101_package_validation_"
            "prep.py tests/test_studio_v101_msi_artifact_binding_schema.py "
            "tests/test_studio_v101_pipeline_gap_audit.py "
            "tests/test_studio_v101_h1e_doc_migration_templates.py "
            "tests/test_studio_v101_h1f_validation_stack.py "
            "tests/test_studio_v101_h1g_rc_convergence_preflight.py -q",
            "cd desktop-ui && npm run typecheck && npm run build "
            "(TypeScript/Vite)",
            "python -m pytest tests/test_collaboration.py "
            "tests/test_studio_v101_h1_model_health_and_fresh_run_repair.py"
            " -q (targeted collaboration/model-health guards)",
            "python -m pytest "
            "tests/test_studio_v100_ps2_canonical_integration_sentinel.py"
            " -q (canonical path/version guards)",
        ],
        "explicitly_excluded": "full redundant v0.9/v1.0.0 regression "
                               "re-runs (unchanged code paths; previously "
                               "green)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v1.0.1 RC convergence preflight (H1G)")
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("--artifact-contract", action="store_true")
    parser.add_argument("--prebuild-state", action="store_true")
    parser.add_argument("--validation-set", action="store_true")
    parser.add_argument("--gate", default=None,
                        help="path to gate-state JSON")
    parser.add_argument("--conflict-forecast", default=None,
                        help="H1.1 implementation SHA to forecast against")
    args = parser.parse_args()
    out: Dict[str, Any] = {}
    if args.manifest:
        out["integration_order"] = integration_order_manifest()
    if args.artifact_contract:
        out["rc_artifact_contract"] = rc_artifact_contract()
    if args.prebuild_state:
        out["expected_prebuild_state"] = expected_prebuild_state()
    if args.validation_set:
        out["minimal_validation_set"] = minimal_validation_set()
    if args.gate:
        state = json.loads(Path(args.gate).read_text(encoding="utf-8"))
        verdict, reasons = post_merge_gate(state)
        out["post_merge_gate"] = {"verdict": verdict, "reasons": reasons}
    if args.conflict_forecast:
        out["conflict_forecast"] = conflict_forecast(args.conflict_forecast)
    if not out:
        parser.error("nothing selected")
    print(json.dumps(out, indent=2, sort_keys=True))
    gate = out.get("post_merge_gate", {})
    return 0 if gate.get("verdict", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
