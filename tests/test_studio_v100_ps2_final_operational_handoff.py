"""PS-2 I3A/I3B final operational handoff sentinel (I3C).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I3C-I3A-I3B-FINAL-OPERATIONAL-DELTA-HANDOFF-AND-CANONICAL-INTEGRATION-SENTINEL-01
Worker: OpenCode PS-2. Tests only. Runnable by PS-1 after integrating the
I3A/I3B delta to verify the final PS-2 operational safeguards are exactly
integrated.

Verdict: PS2_OPERATIONAL_HANDOFF_READY when all checks pass.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SOFTWARE_ROOT.parent
INTEGRATION_DIR = SOFTWARE_ROOT / "docs" / "integration"
SCRIPTS = SOFTWARE_ROOT / "scripts"

ENDPOINT = "cf8ebf31"
DELTA_COMMITS = {"172b1a41 (I3A)", "cf8ebf31 (I3B)"}


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    path = INTEGRATION_DIR / (
        "AMCA-Studio-v1.0.0-PS2-I3A-I3B-Final-Operational-Delta-Manifest.json")
    assert path.is_file(), "operational delta manifest missing"
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_schema_and_endpoints():
    manifest = _manifest()
    assert manifest["schema"] == "amca-ps2-i3a-i3b-operational-delta-manifest/1"
    assert manifest["base_sha"] == "af2d5e6d"
    assert manifest["ps2_operational_endpoint"] == ENDPOINT
    assert set(manifest["remaining_delta_commits"]) == DELTA_COMMITS
    assert len(manifest["files"]) == 9


def test_delta_file_hashes_match():
    manifest = _manifest()
    for entry in manifest["files"]:
        path = REPO_ROOT / entry["path"]
        assert path.is_file(), f"delta file missing: {entry['path']}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            entry["sha256"], f"hash mismatch: {entry['path']}"
        assert path.stat().st_size == entry["size_bytes"]


def test_i3a_command_manifest_present():
    path = SOFTWARE_ROOT / "docs" / "release" / (
        "AMCA-Studio-v1.0.0-PS2-Final-Validation-Command-Manifest.json")
    assert path.is_file()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "amca-ps2-final-validation-command-manifest/1"
    assert len(manifest["steps"]) >= 8


def test_i3a_runbook_present():
    path = SOFTWARE_ROOT / "docs" / "release" / (
        "AMCA-Studio-v1.0.0-PS2-Final-Build-Validation-Runbook.md")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Phase A" in text and "Phase F" in text


def test_i3b_authenticity_guard_present():
    path = SCRIPTS / "evidence_authenticity_guard.py"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "amca-evidence-authenticity-guard/1" in text
    for classification in ("TEST_FIXTURE", "SYNTHETIC_SIMULATION",
                           "HISTORICAL_EVIDENCE", "FINAL_EVIDENCE_ELIGIBLE"):
        assert classification in text


def test_command_manifest_references_only_accepted_tools():
    manifest = _manifest()
    validation_manifest = json.loads(
        (SOFTWARE_ROOT / "docs" / "release" /
         "AMCA-Studio-v1.0.0-PS2-Final-Validation-Command-Manifest.json"
         ).read_text(encoding="utf-8"))
    for step in validation_manifest["steps"]:
        tool = step.get("tool", "")
        if tool in ("caller-supplied artifacts",):
            continue
        assert (SCRIPTS / tool).is_file(), f"unknown tool: {tool}"
        for chained in step.get("tool_chain", []):
            assert (SCRIPTS / chained).is_file(), f"unknown chained: {chained}"


def test_no_duplicate_validators_introduced():
    validators = {
        "run_final_package_compliance_verification.py",
        "run_ps2_final_release_validation.py",
        "build_ps2_promotion_input.py",
        "evidence_authenticity_guard.py",
    }
    present = {p.name for p in SCRIPTS.glob("*.py")
               if p.name in validators}
    for name in validators:
        count = sum(1 for p in SCRIPTS.glob("*.py") if p.name == name)
        assert count == 1, f"duplicate validator: {name} (x{count})"
    assert present == validators


def test_synthetic_evidence_remains_quarantined():
    guard = _load("auth_guard_i3c", "evidence_authenticity_guard.py")
    doc = {
        "schema": "amca-ps2-promotion-input/1",
        "source_sha": "a" * 40, "portable_sha256": "b" * 64,
        "installer_sha256": "c" * 64,
        "ps2_aggregate_verdict": "PS2_RELEASE_READY",
        "mpl_readiness": "PASS",
        "mpl_provenance_state": "PROVENANCE_READY",
        "promotion_eligibility_ps2": "PS2_INPUT_READY",
        "mode": "simulation",
    }
    verdict, _ = guard.final_eligibility_verdict(doc)
    assert verdict == "QUARANTINED"


def test_final_evidence_eligibility_strict():
    guard = _load("auth_guard_i3c2", "evidence_authenticity_guard.py")
    clean = {
        "schema": "amca-ps2-promotion-input/1",
        "source_sha": "a" * 40, "portable_sha256": "b" * 64,
        "installer_sha256": "c" * 64,
        "ps2_aggregate_verdict": "PS2_RELEASE_READY",
        "mpl_readiness": "PASS",
        "mpl_provenance_state": "PROVENANCE_READY",
        "promotion_eligibility_ps2": "PS2_INPUT_READY",
        "final_run_recorded": True,
    }
    assert guard.final_eligibility_verdict(clean)[0] == "ACCEPTED"
    drifted = dict(clean, source_sha="v0.9-old")
    assert guard.final_eligibility_verdict(drifted)[0] == "QUARANTINED"


def test_pass_with_review_preserved():
    pi = _load("ps2_promotion_i3c", "build_ps2_promotion_input.py")
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] == \
        "PS2_INPUT_READY_WITH_REVIEW"


def test_mpl_provenance_remains_mandatory():
    pi = _load("ps2_promotion_i3c2", "build_ps2_promotion_input.py")
    doc = {"schema": pi.SCHEMA,
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64,
           "mpl_readiness": "RELEASE_ASSET_REQUIRED",
           "mpl_provenance_state": "ASSET_NOT_STAGED"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_BLOCKED"


def test_no_stale_v09_package_fallback():
    for name in ("run_dependency_compliance_audit.py",
                 "generate_distribution_compliance_evidence.py",
                 "run_ps2_final_release_validation.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "amca-studio-v0.9.0-windows-x64-portable.zip" not in source, \
            f"stale fallback in {name}"


def test_no_worker_paths_or_secrets():
    for name in ("evidence_authenticity_guard.py",
                 "run_ps2_final_release_validation.py",
                 "build_ps2_promotion_input.py",
                 "run_final_package_compliance_verification.py"):
        lowered = (SCRIPTS / name).read_text(
            encoding="utf-8", errors="ignore").casefold()
        for marker in ("md. ashraful", "opencode-ps2-worktree",
                       "amca-ps2-commit", "sk-proj-", "ghp_"):
            assert marker not in lowered, f"{name}: {marker}"


def test_handoff_doc_present():
    path = INTEGRATION_DIR / (
        "AMCA-Studio-v1.0.0-PS2-Final-Operational-Handoff.md")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert ENDPOINT in text
    assert "PS2_OPERATIONAL_HANDOFF_READY" in text
    assert "I3A" in text and "I3B" in text