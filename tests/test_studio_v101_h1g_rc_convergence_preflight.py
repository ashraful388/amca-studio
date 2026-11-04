"""H1G RC convergence preflight tests (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1G-POST-H1-CONVERGENCE-AND-THREE-ARTIFACT-RC-PREFLIGHT-01
Preparation/sentinel only. Verifies the deterministic integration-order
manifest, the hard post-merge gate (H1A evidence must bind the EXACT
H1.1 SHA — 9b315f6e is not final), the RC three-artifact contract, the
read-only conflict sentinel, the honest prebuild state, and the minimal
post-convergence validation set.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
RELEASE_DOCS = SOFTWARE_ROOT / "docs" / "release"
MANIFEST_JSON = RELEASE_DOCS / (
    "AMCA-Studio-v1.0.1-H1G-RC-Convergence-Preflight-Manifest.json")

spec = importlib.util.spec_from_file_location(
    "h1g_pf", SCRIPTS / "v101_rc_convergence_preflight.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

EXPECTED_LINEAGE = [
    ("H1B", "e99c8826"), ("H1C", "6e6b2e08"), ("H1D", "be1bf538"),
    ("H1E", "05d884e8"), ("H1F", "8cc8a827"),
]


def _full_gate_state(**overrides) -> dict:
    state = {
        "h1a_acceptance_sha": "ff" * 20,
        "h1_1_implementation_sha": "ff" * 20,
        "model_health_states_intact": True,
        "u1_u7_persisted_rules_intact": True,
        "no_provider_specific_hacks": True,
        "three_artifact_schemas_intact": True,
        "no_v100_artifact_reuse": True,
        "canonical_root_d_only": True,
    }
    state.update(overrides)
    return state


# --- deterministic integration order ---------------------------------------

def test_integration_order_exact_and_chained():
    m = pf.integration_order_manifest()
    order = m["integration_order"]
    assert [(o["milestone"], o["commit"]) for o in order] == \
        EXPECTED_LINEAGE
    assert order[0]["prerequisite"] is None
    for prev, nxt in zip(order, order[1:]):
        assert nxt["prerequisite"] == prev["commit"]
    assert all(o["status"] == "ACCEPTED/CLOSED" for o in order)
    assert "certified ZCode H1.1 source" in m["rc_composition"][0]


def test_manifest_json_matches_tool_output():
    assert MANIFEST_JSON.is_file(), "H1G preflight manifest missing"
    doc = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert doc["integration_order"] == pf.integration_order_manifest()
    assert doc["rc_artifact_contract"] == pf.rc_artifact_contract()
    assert doc["expected_prebuild_state"] == pf.expected_prebuild_state()


# --- hard post-merge gate ----------------------------------------------------

def test_gate_passes_only_with_full_evidence():
    verdict, _ = pf.post_merge_gate(_full_gate_state())
    assert verdict == "PASS"


def test_gate_blocks_missing_h1a_evidence():
    verdict, reasons = pf.post_merge_gate(
        _full_gate_state(h1a_acceptance_sha=None))
    assert verdict == "BLOCKED"
    assert any("H1A" in r or "acceptance" in r for r in reasons)


def test_gate_blocks_certification_of_wrong_sha():
    # 9b315f6e is the interim H1, NOT final H1.1: acceptance bound to a
    # different SHA than the RC carries must BLOCK.
    verdict, reasons = pf.post_merge_gate(
        _full_gate_state(h1a_acceptance_sha="9b315f6e" + "0" * 34,
                         h1_1_implementation_sha="ff" * 20))
    assert verdict == "BLOCKED"
    assert any("EXACT" in r for r in reasons)


def test_gate_blocks_any_unmet_condition():
    for field in ("model_health_states_intact",
                  "u1_u7_persisted_rules_intact",
                  "no_provider_specific_hacks",
                  "three_artifact_schemas_intact",
                  "no_v100_artifact_reuse", "canonical_root_d_only"):
        verdict, _ = pf.post_merge_gate(_full_gate_state(**{field: False}))
        assert verdict == "BLOCKED", f"gate soft-passed on {field}"
    verdict, _ = pf.post_merge_gate(_full_gate_state(
        model_health_states=["READY", "AUTH_REQUIRED"]))
    assert verdict == "BLOCKED"


# --- RC artifact contract ------------------------------------------------------

def test_rc_artifact_contract_three_named_artifacts():
    c = pf.rc_artifact_contract()
    names = {a["role"]: a["name"] for a in c["artifacts"]}
    assert names["exe"] == "amca-studio-v1.0.1-windows-x64-setup.exe"
    assert names["portable"] == \
        "amca-studio-v1.0.1-windows-x64-portable.zip"
    assert names["msi"].endswith(".msi") and "v1.0.1" in names["msi"]
    assert c["canonical_root"] == "D:/ChatGPT/AMCA"
    blob = json.dumps(c)
    assert "f9111c3f" in blob and "41efd240" in blob
    assert "MSI_EXECUTION_PENDING" in blob


# --- conflict sentinel (read-only) ---------------------------------------------

def test_conflict_forecast_dryrun_against_interim_h1():
    report = pf.conflict_forecast("9b315f6e")
    assert report["errors"] == []
    assert report["file_overlap"] == []
    assert report["merge_clean"] is True
    assert report["schema_conflict"] is False
    assert report["version_conflict"] is False


def test_overlap_analysis_classifies_conflicts():
    ours = {"software/scripts/v101_artifact_binding.py",
            "software/docs/release/plan.md",
            "software/src/amca/version_transition.py"}
    theirs = {"software/scripts/v101_artifact_binding.py",
              "software/src/amca/version_transition.py",
              "software/src/amca/service.py"}
    r = pf.analyze_overlap(theirs, ours)
    assert r["file_overlap"] == [
        "software/scripts/v101_artifact_binding.py",
        "software/src/amca/version_transition.py"]
    assert r["schema_conflict"] is True
    assert r["version_conflict"] is True
    assert r["semantic_conflict"] is True
    clean = pf.analyze_overlap({"software/src/amca/service.py"}, ours)
    assert clean["file_overlap"] == []
    assert clean["semantic_conflict"] is False


def test_conflict_forecast_bad_sha_is_honest_error():
    report = pf.conflict_forecast("deadbeef" * 5)
    assert report["errors"], "invalid SHA must surface an honest error"


# --- prebuild state + minimal validation set -----------------------------------

def test_prebuild_state_is_honest():
    s = pf.expected_prebuild_state()
    assert s["msi_execution"] == "MSI_EXECUTION_PENDING"
    assert s["exe_execution"] == "PENDING_FUTURE_BUILD"
    assert s["portable_execution"] == "PENDING_FUTURE_BUILD"
    assert s["promotion"] == "BLOCKED"
    assert s["tag"].startswith("NONE")
    assert s["packaging"] == "NOT AUTHORIZED at preflight"
    assert "MODEL_RESPONSE_RECEIVED=YES" in s["model_response_evidence"]


def test_minimal_validation_set_contents():
    v = pf.minimal_validation_set()
    blob = " ".join(v["commands"])
    assert "H1A acceptance sentinel" in blob
    assert "test_studio_v101_h1g_rc_convergence_preflight.py" in blob
    assert "npm run typecheck" in blob and "npm run build" in blob
    assert "test_collaboration.py" in blob
    assert "canonical_integration_sentinel" in blob
    assert "redundant" in v["explicitly_excluded"]


def test_cli_manifest_mode_exit_zero():
    proc = subprocess.run(
        [sys.executable,
         str(SCRIPTS / "v101_rc_convergence_preflight.py"),
         "--manifest", "--prebuild-state"],
        capture_output=True, text=True, cwd=SOFTWARE_ROOT)
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert doc["integration_order"]["schema"] == pf.SCHEMA
