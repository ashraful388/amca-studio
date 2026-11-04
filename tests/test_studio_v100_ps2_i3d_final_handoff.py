"""PS-2 I3D final delta handoff sentinel (I3E).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I3E-I3D-FINAL-DELTA-HANDOFF-AND-PS2-PRE-TRANSITION-FREEZE-01
Worker: OpenCode PS-2. Tests only. Runnable by PS-1 after merging the I3D
delta to verify the pre-transition freeze is exact and complete.

Verdict: PS2_PRE_TRANSITION_FROZEN when all checks pass.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SOFTWARE_ROOT.parent
RELEASE_DIR = SOFTWARE_ROOT / "docs" / "release"
SCRIPTS = SOFTWARE_ROOT / "scripts"

ENDPOINT = "a41f2e55"
MANIFEST = SOFTWARE_ROOT / "docs" / "integration" / (
    "AMCA-Studio-v1.0.0-PS2-I3D-Final-Delta-Manifest.json")


def _manifest() -> dict:
    assert MANIFEST.is_file(), "I3D delta manifest missing"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_i3d_delta_manifest_valid():
    manifest = _manifest()
    assert manifest["schema"] == "amca-ps2-i3d-final-delta-manifest/1"
    assert manifest["base_sha"] == "ed000e58"
    assert manifest["ps2_endpoint"] == ENDPOINT
    assert manifest["remaining_delta_commits"] == ["a41f2e55 (I3D)"]
    assert len(manifest["files"]) == 3


def test_i3d_delta_hashes_match():
    manifest = _manifest()
    for entry in manifest["files"]:
        path = REPO_ROOT / entry["path"]
        assert path.is_file(), f"delta file missing: {entry['path']}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            entry["sha256"], f"hash mismatch: {entry['path']}"
        assert path.stat().st_size == entry["size_bytes"]


def test_cli_rehearsal_test_exists_and_covers_real_cli():
    path = SOFTWARE_ROOT / "tests" / (
        "test_studio_v100_ps2_final_validation_cli_rehearsal.py")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "--help" in text  # rehearses the real CLI
    assert "QUARANTINED" in text


def test_corrected_runbook_annotations_present():
    runbook = (RELEASE_DIR /
               "AMCA-Studio-v1.0.0-PS2-Final-Build-Validation-Runbook.md"
               ).read_text(encoding="utf-8")
    assert "amca-ps2-post-transition-acceptance/1" in runbook
    assert "amca-mpl-release-asset-provenance/1" in runbook
    assert "amca-ps2-final-release-evidence/1" in runbook
    assert "amca-ps2-promotion-input/1" in runbook
    assert "amca-final-release-documentation-export/1" in runbook
    assert "steps 4/6/7 consume" in runbook
    assert "FINAL_PORTABLE bytes" in runbook


def test_all_referenced_commands_still_exist():
    manifest = _manifest()
    for entry in manifest["files"]:
        assert (REPO_ROOT / entry["path"]).is_file()
    # And every tool referenced by the I3A command manifest exists.
    validation = json.loads(
        (RELEASE_DIR /
         "AMCA-Studio-v1.0.0-PS2-Final-Validation-Command-Manifest.json"
         ).read_text(encoding="utf-8"))
    for step in validation["steps"]:
        tool = step.get("tool", "")
        if tool in ("caller-supplied artifacts",):
            continue
        assert (SCRIPTS / tool).is_file(), f"missing tool: {tool}"


def test_ordering_remains_correct():
    validation = json.loads(
        (RELEASE_DIR /
         "AMCA-Studio-v1.0.0-PS2-Final-Validation-Command-Manifest.json"
         ).read_text(encoding="utf-8"))
    steps = {s["tool"]: s["sequence"] for s in validation["steps"]}
    assert steps["run_ps2_post_transition_acceptance.py"] < steps[
        "run_final_package_compliance_verification.py"]
    assert steps["run_ps2_final_release_validation.py"] < steps[
        "build_ps2_promotion_input.py"]
    assert steps["build_ps2_promotion_input.py"] < steps[
        "export_final_release_documentation.py"]


def test_no_stale_v09_fallback():
    for name in ("run_dependency_compliance_audit.py",
                 "generate_distribution_compliance_evidence.py",
                 "run_ps2_final_release_validation.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "amca-studio-v0.9.0-windows-x64-portable.zip" not in source, \
            f"stale fallback in {name}"


def test_pass_with_review_preserved():
    spec = importlib.util.spec_from_file_location(
        "ps2_promotion_i3e", str(SCRIPTS / "build_ps2_promotion_input.py"))
    assert spec is not None and spec.loader is not None
    pi = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pi
    spec.loader.exec_module(pi)
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] == \
        "PS2_INPUT_READY_WITH_REVIEW"
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] != "PS2_INPUT_READY"


def test_mpl_provenance_remains_mandatory():
    spec = importlib.util.spec_from_file_location(
        "ps2_promotion_i3e2", str(SCRIPTS / "build_ps2_promotion_input.py"))
    assert spec is not None and spec.loader is not None
    pi = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pi
    spec.loader.exec_module(pi)
    doc = {"schema": pi.SCHEMA,
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64,
           "mpl_readiness": "RELEASE_ASSET_REQUIRED",
           "mpl_provenance_state": "ASSET_NOT_STAGED"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_BLOCKED"


def test_synthetic_evidence_remains_quarantined():
    spec = importlib.util.spec_from_file_location(
        "auth_guard_i3e", str(SCRIPTS / "evidence_authenticity_guard.py"))
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)
    doc = {"schema": "amca-ps2-promotion-input/1",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64,
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY",
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "mode": "simulation"}
    verdict, _ = guard.final_eligibility_verdict(doc)
    assert verdict == "QUARANTINED"


def test_no_worker_paths_or_secrets_in_delta():
    manifest = _manifest()
    for entry in manifest["files"]:
        path = REPO_ROOT / entry["path"]
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.casefold()
        assert "sk-proj-" not in lowered, entry["path"]
        assert "ghp_" not in lowered, entry["path"]
        # Guard-marker tuples inside test files are the documented denial
        # (mirroring the I2W sentinel); strip them before scanning. Real
        # worker identification would appear outside such tuples.
        scanned = lowered
        for quoted in ('("md. ashraful", "opencode-ps2-worktree",',
                       '("md. ashraful", "opencode-ps2",'):
            start = scanned.find(quoted)
            if start >= 0:
                end = scanned.find(")", start)
                scanned = scanned[:start] + scanned[end + 1:]
        assert "md. ashraful" not in scanned, entry["path"]
        assert "opencode-ps2-worktree" not in scanned, entry["path"]
        assert "amca-ps2-commit" not in scanned, entry["path"]


def test_freeze_doc_explicit():
    path = SOFTWARE_ROOT / "docs" / "integration" / (
        "AMCA-Studio-v1.0.0-PS2-Final-Pre-Transition-Freeze.md")
    assert path.is_file()
    text = " ".join(path.read_text(encoding="utf-8").split()).casefold()
    assert "ps2_pre_transition_frozen" in text
    assert "a41f2e55" in text
    assert "no further ps-2 pre-transition engineering is required" in text