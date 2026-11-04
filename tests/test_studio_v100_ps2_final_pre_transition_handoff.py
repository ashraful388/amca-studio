"""PS-2 final pre-transition handoff sentinel (I2Z).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2Z-FINAL-PS2-ENDPOINT-FREEZE-DELTA-MANIFEST-AND-TRANSITION-HANDOFF-01
Worker: OpenCode PS-2. Tests only. Runnable by PS-1 after integrating the
I2X/I2Y delta to verify the final pre-transition endpoint is complete and
correct.

Verdict: PS2_PRE_TRANSITION_READY when all checks pass.
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

ENDPOINT = "af2d5e6d"
DELTA_COMMITS = {"5dc7f3de (I2X)", "af2d5e6d (I2Y)"}


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    path = INTEGRATION_DIR / (
        "AMCA-Studio-v1.0.0-PS2-Final-Pre-Transition-Delta-Manifest.json")
    assert path.is_file(), "pre-transition delta manifest missing"
    return json.loads(path.read_text(encoding="utf-8"))


def test_i2v_i2w_i2y_present():
    for name in (
        "docs/release/AMCA-Studio-v1.0.0-PS2-Final-Evidence-Schema-Drift-"
        "Sentinel.md",
        "docs/release/AMCA-Studio-v1.0.0-PS2-Final-Evidence-Path-"
        "Portability-and-Worktree-Independence-Report.md",
        "docs/release/AMCA-Studio-v1.0.0-PS2-Post-Transition-Fast-"
        "Acceptance-Contract.md",
        "scripts/run_ps2_post_transition_acceptance.py",
        "tests/test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py",
        "tests/test_studio_v100_ps2_final_evidence_path_portability.py",
        "tests/test_studio_v100_ps2_post_transition_acceptance.py",
    ):
        assert (REPO_ROOT / "software" / name).is_file(), f"missing: {name}"


def test_delta_manifest_hashes_correct():
    manifest = _manifest()
    assert manifest["schema"] == \
        "amca-ps2-final-pre-transition-delta-manifest/1"
    assert manifest["ps2_endpoint"] == ENDPOINT
    assert set(manifest["remaining_delta_commits"]) == DELTA_COMMITS
    for entry in manifest["files"]:
        path = REPO_ROOT / entry["path"]
        assert path.is_file(), f"delta file missing: {entry['path']}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            entry["sha256"], f"hash mismatch: {entry['path']}"
        assert path.stat().st_size == entry["size_bytes"]
    assert len(manifest["files"]) == 6


def test_stale_v09_fallback_remains_removed():
    for name in ("run_dependency_compliance_audit.py",
                 "generate_distribution_compliance_evidence.py",
                 "run_ps2_final_release_validation.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "amca-studio-v0.9.0-windows-x64-portable.zip" not in source, \
            f"{name} still hard-codes the v0.9 portable filename"


def test_schemas_remain_pinned():
    sentinel = (SOFTWARE_ROOT / "tests" /
                "test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py"
                ).read_text(encoding="utf-8")
    for schema in ("amca-migration-certification-evidence/1",
                   "amca-final-package-compliance/1",
                   "amca-mpl-notice-readiness/1",
                   "amca-mpl-asset-intake-receipt/1",
                   "amca-mpl-release-asset-provenance/1",
                   "amca-ps2-final-release-evidence/1",
                   "amca-ps2-promotion-input/1"):
        assert schema in sentinel, f"schema unpinned: {schema}"


def test_pass_with_review_preserved():
    pi = _load("ps2_promotion_i2z", "build_ps2_promotion_input.py")
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] == \
        "PS2_INPUT_READY_WITH_REVIEW"
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] != "PS2_INPUT_READY"


def test_mpl_provenance_mandatory():
    pi = _load("ps2_promotion_i2z2", "build_ps2_promotion_input.py")
    doc = {"schema": pi.SCHEMA,
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64,
           "mpl_readiness": "RELEASE_ASSET_REQUIRED",
           "mpl_provenance_state": "ASSET_NOT_STAGED"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_BLOCKED"


def test_post_transition_runner_present():
    runner = SCRIPTS / "run_ps2_post_transition_acceptance.py"
    assert runner.is_file()
    text = runner.read_text(encoding="utf-8")
    assert "amca-ps2-post-transition-acceptance/1" in text
    assert "READY_WITH_EXTERNAL_ASSET_PENDING" in text


def test_no_duplicate_promotion_adapter():
    adapters = list(SCRIPTS.glob("*promotion_input*.py"))
    assert len(adapters) == 1
    assert adapters[0].name == "build_ps2_promotion_input.py"


def test_no_worker_specific_paths():
    for name in ("run_ps2_post_transition_acceptance.py",
                 "import_mpl_release_asset.py",
                 "record_mpl_asset_provenance.py",
                 "build_ps2_promotion_input.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        for marker in ("Md. Ashraful", "opencode-ps2-worktree",
                       "AMCA-PS2-commit", "D:\\ChatGPT"):
            assert marker not in text, f"worker path in {name}: {marker}"


def test_no_secrets():
    for name in ("run_ps2_post_transition_acceptance.py",
                 "import_mpl_release_asset.py",
                 "record_mpl_asset_provenance.py",
                 "build_ps2_promotion_input.py"):
        lowered = (SCRIPTS / name).read_text(
            encoding="utf-8", errors="ignore").casefold()
        assert "sk-proj-" not in lowered
        assert "ghp_" not in lowered


def test_handoff_doc_present_with_prerequisites():
    path = INTEGRATION_DIR / (
        "AMCA-Studio-v1.0.0-PS2-Final-Pre-Transition-Handoff.md")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert ENDPOINT in text
    assert "PS2_PRE_TRANSITION_READY" in text
    assert "run_ps2_post_transition_acceptance.py" in text
    assert "import_mpl_release_asset.py" in text