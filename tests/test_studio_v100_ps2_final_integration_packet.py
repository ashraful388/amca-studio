"""PS-2 final canonical integration packet tests (I2X).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2X-FINAL-PS2-CANONICAL-INTEGRATION-PACKET-AND-DELTA-INTEGRITY-MANIFEST-01
Worker: OpenCode PS-2. Tests only. Runnable by PS-1 AFTER integrating the
I2V/I2W delta to verify exact, complete, duplicate-free integration.

Covers: remaining-delta contracts present, expected file hashes, no
duplicate competing adapter, no stale v0.9 artifact auto-selection,
schema-drift sentinel present, path-portability sentinel present,
PASS_WITH_REVIEW semantics preserved, MPL provenance mandatory, no
secrets/worker paths.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SOFTWARE_ROOT.parent
INTEGRATION_DIR = SOFTWARE_ROOT / "docs" / "integration"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_delta_manifest_exists_and_valid():
    path = INTEGRATION_DIR / "AMCA-Studio-v1.0.0-PS2-Final-Delta-Manifest.json"
    assert path.is_file()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "amca-ps2-final-delta-manifest/1"
    assert manifest["ps2_endpoint"] == "49ece437"
    assert set(manifest["remaining_delta_commits"]) == {
        "d12faeb7 (I2V)", "49ece437 (I2W)"}
    assert len(manifest["files"]) == 9


def test_delta_files_match_manifest_hashes():
    manifest = json.loads((INTEGRATION_DIR /
                           "AMCA-Studio-v1.0.0-PS2-Final-Delta-Manifest.json"
                           ).read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = REPO_ROOT / entry["path"]
        assert path.is_file(), f"delta file missing: {entry['path']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], \
            f"byte-identity mismatch: {entry['path']}"
        assert path.stat().st_size == entry["size_bytes"]


def test_packet_doc_present():
    path = INTEGRATION_DIR / (
        "AMCA-Studio-v1.0.0-PS2-Final-Canonical-Integration-Packet.md")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "49ece437" in text
    assert "93f19f12..49ece437" in text or "I2U" in text


def test_remaining_i2v_i2w_contracts_present():
    for name in ("AMCA-Studio-v1.0.0-PS2-Final-Evidence-Schema-Drift-Sentinel.md",
                 "AMCA-Studio-v1.0.0-PS2-Final-Evidence-Path-Portability-and-"
                 "Worktree-Independence-Report.md"):
        assert (SOFTWARE_ROOT / "docs" / "release" / name).is_file(), name


def test_no_duplicate_competing_promotion_adapter():
    """Exactly one PS-2 promotion-input adapter may exist."""
    adapters = list(SCRIPTS.glob("*promotion_input*.py")) + \
        list(SCRIPTS.glob("*promotion-input*.py"))
    assert len(adapters) == 1, f"competing adapters: {adapters}"
    assert adapters[0].name == "build_ps2_promotion_input.py"


def test_no_stale_v09_artifact_auto_selection():
    """The three I2W-fixed scripts must contain no automatic v0.9 fallback."""
    for name in ("run_dependency_compliance_audit.py",
                 "generate_distribution_compliance_evidence.py",
                 "run_ps2_final_release_validation.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "amca-studio-v0.9.0-windows-x64-portable.zip" not in source, \
            f"{name} still hard-codes the v0.9 portable filename"
    # And the audit runner requires caller-supplied packages.
    da = _load("dep_audit_i2x", "run_dependency_compliance_audit.py")
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=True,
                                   portable=None)
    assert result["package_consistency"]["portable_present"] is False


def test_schema_drift_sentinel_present():
    sentinel = SOFTWARE_ROOT / "tests" / (
        "test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py")
    assert sentinel.is_file()
    text = sentinel.read_text(encoding="utf-8")
    assert "amca-ps2-promotion-input/1" in text
    assert "PS2_INPUT_READY_WITH_REVIEW" in text or \
        "READY_WITH_REVIEW" in text


def test_path_portability_sentinel_present():
    sentinel = SOFTWARE_ROOT / "tests" / (
        "test_studio_v100_ps2_final_evidence_path_portability.py")
    assert sentinel.is_file()
    text = sentinel.read_text(encoding="utf-8")
    assert "caller" in text.casefold()
    assert "WORKER_MARKERS" in text


def test_pass_with_review_semantics_preserved():
    pi = _load("ps2_promotion_i2x", "build_ps2_promotion_input.py")
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] == \
        "PS2_INPUT_READY_WITH_REVIEW"
    assert pi.STATE_MAP["PS2_RELEASE_READY"] == "PS2_INPUT_READY"
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] != "PS2_INPUT_READY"


def test_mpl_provenance_mandatory():
    pi = _load("ps2_promotion_i2x2", "build_ps2_promotion_input.py")
    doc = {"schema": pi.SCHEMA,
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64,
           "mpl_readiness": "RELEASE_ASSET_REQUIRED",
           "mpl_provenance_state": "ASSET_NOT_STAGED"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_BLOCKED"


def test_no_secrets_or_worker_paths_in_delta():
    manifest = json.loads((INTEGRATION_DIR /
                           "AMCA-Studio-v1.0.0-PS2-Final-Delta-Manifest.json"
                           ).read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = REPO_ROOT / entry["path"]
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.casefold()
        if entry["type"] in ("tool", "source"):
            # Executable content: no credential canary strings at all (those
            # legitimately appear only inside secret-detection tests).
            assert "sk-proj-" not in lowered, entry["path"]
            assert "ghp_" not in lowered, entry["path"]
            for marker in ("Md. Ashraful", "opencode-ps2-worktree",
                           "AMCA-PS2-commit"):
                assert marker not in text, \
                    f"worker marker in {entry['path']}: {marker}"
        else:
            # Docs may quote guard markers they enforce (denial context) or
            # contain credential canaries inside negative-test assertions.
            # What they must never do is embed a real credential value or a
            # real user-machine path outside the documented marker list.
            assert "C:\\Users\\Md. Ashraful Babu" not in text, entry["path"]
            assert "sk-proj-abcdefghij" not in lowered, entry["path"]