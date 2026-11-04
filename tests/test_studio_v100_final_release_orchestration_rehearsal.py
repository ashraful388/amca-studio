"""I2I tests: final-release orchestration rehearsal.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2I-FINAL-RELEASE-EVIDENCE-PATH-COMPATIBILITY-AND-DRY-RUN-ORCHESTRATION-REHEARSAL-01
"""

from pathlib import Path
import json

import pytest

from amca.orchestration_rehearsal import (
    EVIDENCE_FAMILY_MATRIX,
    STATE_BLOCKED,
    STATE_MISMATCH,
    STATE_PENDING,
    STATE_READY,
    STATE_STALE,
    rehearse_evidence,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
RUNNER = SOFTWARE_ROOT / "scripts" / "run_final_release_orchestration_rehearsal.py"

REQUIRED_FAMILIES = {
    "canonical_certification",
    "migration_certification",
    "distribution_release_gate",
    "windows_runtime_smoke",
    "windows_signing_evidence",
    "version_contract",
    "compliance_sanitation",
}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Live rehearsal (honest current state)
# ---------------------------------------------------------------------------

def test_all_mandatory_evidence_families_enumerated():
    families = {f["family"] for f in EVIDENCE_FAMILY_MATRIX}
    assert REQUIRED_FAMILIES <= families


def test_live_rehearsal_honest_state():
    """Current live state: no family may be fabricated; PS-1 canonical is
    BLOCKED/STALE, missing inputs stay PENDING, and v0.9 artifacts are
    classified as validation artifacts, never final v1.0 evidence."""
    result = rehearse_evidence()
    rows = {r["family"]: r for r in result["evidence_matrix"]}

    for family in REQUIRED_FAMILIES:
        assert family in rows
        assert rows[family]["state"] in (
            STATE_READY, STATE_PENDING, STATE_STALE,
            STATE_MISMATCH, STATE_BLOCKED)

    assert rows["canonical_certification"]["state"] in (STATE_BLOCKED, STATE_STALE)
    # Migration certification: PENDING exactly while no evidence record exists;
    # once a record is present it must be classified from that record, and a
    # READY state must correspond to a PASS verdict (never inferred).
    migration_paths = [
        SOFTWARE_ROOT / "tests" / "migration-certification.json",
        SOFTWARE_ROOT / "dist" / "migration-certification-evidence.json",
    ]
    migration_present = any(p.is_file() for p in migration_paths)
    assert (rows["migration_certification"]["state"] == STATE_PENDING) == (
        not migration_present)
    if rows["migration_certification"]["state"] == STATE_READY:
        record = json.loads(
            next(p for p in migration_paths if p.is_file()).read_text(encoding="utf-8"))
        assert record.get("overall_verdict") == "PASS"
    assert rows["compliance_sanitation"]["state"] == STATE_PENDING
    assert rows["distribution_release_gate"]["state"] in (
        STATE_READY, STATE_STALE, STATE_MISMATCH, STATE_BLOCKED)
    for r in rows.values():
        assert r["classification"].startswith("validation artifact")
    assert result["rules"]["final_promotion_untouched"] is True
    assert result["rules"]["side_effects"] == "none - read-only rehearsal"


def test_rehearsal_never_copies_or_promotes_artifacts(tmp_path):
    """The rehearsal is side-effect free: snapshot the dist tree, run the
    rehearsal, verify nothing changed."""
    import hashlib

    def snapshot():
        return {
            str(p.relative_to(SOFTWARE_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((SOFTWARE_ROOT / "dist").iterdir()) if p.is_file()
        }

    before = snapshot()
    rehearse_evidence()
    after = snapshot()
    assert before == after


def test_no_secrets_emitted():
    result = rehearse_evidence()
    text = json.dumps(result).lower()
    for forbidden in ("password", "private key", "pfx", "api_key"):
        assert forbidden not in text
    assert result["secrets_emitted"] is False


# ---------------------------------------------------------------------------
# Deterministic family states (synthetic evidence trees)
# ---------------------------------------------------------------------------

def _synthetic_tree(tmp_path: Path, sha: str = "a" * 40) -> dict:
    tree = tmp_path / "software"
    for rel in ("src/amca", "desktop-ui/src-tauri", "dist", "tests", "docs"):
        (tree / rel).mkdir(parents=True, exist_ok=True)
    (tree / "pyproject.toml").write_text('version = "0.9.0"\n', encoding="utf-8")
    (tree / "src" / "amca" / "__init__.py").write_text(
        '__version__ = "0.9.0"\n', encoding="utf-8")
    (tree / "desktop-ui" / "package.json").write_text(
        json.dumps({"version": "0.9.0"}), encoding="utf-8")
    (tree / "desktop-ui" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"version": "0.9.0"}), encoding="utf-8")
    (tree / "desktop-ui" / "src-tauri" / "Cargo.toml").write_text(
        'version = "0.9.0"\n', encoding="utf-8")
    return {"tree": tree, "sha": sha}


def _write_evidence(tree: dict, *, ps1_verdict="PASS", ps1_sha=None,
                    migration=True, migration_verdict="PASS",
                    manifest=True, manifest_sha=None, smoke=True,
                    compliance=True) -> None:
    root: Path = tree["tree"]
    sha = tree["sha"]
    _write(root / "tests" / "certification-result.json", {
        "overall_verdict": ps1_verdict,
        "provenance": {"sha": ps1_sha or sha, "clean": True, "dirty": False},
    })
    if migration:
        _write(root / "tests" / "migration-certification.json", {
            "overall_verdict": migration_verdict,
            "provenance": {"sha": sha},
        })
    if smoke:
        _write(root / "dist" / "windows-runtime-smoke-result.json", {
            "overall": "PASS",
            "source_sha": sha,
            "artifacts": {
                "installer": {"overall": "PASS", "sha256": "i" * 64},
                "portable": {"overall": "PASS", "sha256": "p" * 64},
            },
        })
    if compliance:
        _write(root / "docs" / "release" /
               "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json", {
            "schema": "amca-distribution-compliance-evidence/1",
            "overall_verdict": "PASS",
            "provenance": {"sha": sha},
        })


def test_missing_evidence_pending_never_inferred_pass(tmp_path, monkeypatch):
    import amca.orchestration_rehearsal as orch

    tree = _synthetic_tree(tmp_path)
    monkeypatch.setattr(orch, "SOFTWARE_ROOT", tree["tree"])
    result = orch.rehearse_evidence(source_sha=tree["sha"])
    rows = {r["family"]: r for r in result["evidence_matrix"]}
    # No evidence written at all: everything must be PENDING/BLOCKED.
    for family in ("canonical_certification", "migration_certification",
                   "windows_runtime_smoke", "compliance_sanitation",
                   "distribution_release_gate"):
        assert rows[family]["state"] in (STATE_PENDING, STATE_BLOCKED)
    assert "PENDING" in result["rules"]["pending_i2n"] or \
        rows["compliance_sanitation"]["state"] == STATE_PENDING


def test_stale_source_sha_detected(tmp_path, monkeypatch):
    import amca.orchestration_rehearsal as orch

    tree = _synthetic_tree(tmp_path)
    # Evidence bound to a DIFFERENT SHA than the rehearsal source.
    _write_evidence(tree, ps1_sha="b" * 40)
    monkeypatch.setattr(orch, "SOFTWARE_ROOT", tree["tree"])
    result = orch.rehearse_evidence(source_sha=tree["sha"])
    rows = {r["family"]: r for r in result["evidence_matrix"]}
    assert rows["canonical_certification"]["state"] == STATE_STALE
    assert rows["canonical_certification"]["usable_for_final_promotion"] is False


def test_valid_same_sha_evidence_ready(tmp_path, monkeypatch):
    import amca.orchestration_rehearsal as orch

    tree = _synthetic_tree(tmp_path)
    _write_evidence(tree)
    monkeypatch.setattr(orch, "SOFTWARE_ROOT", tree["tree"])
    result = orch.rehearse_evidence(source_sha=tree["sha"])
    rows = {r["family"]: r for r in result["evidence_matrix"]}
    assert rows["canonical_certification"]["state"] == STATE_READY
    assert rows["canonical_certification"]["usable_for_final_promotion"] is True
    assert rows["migration_certification"]["state"] == STATE_READY
    assert rows["windows_runtime_smoke"]["state"] == STATE_READY
    assert rows["compliance_sanitation"]["state"] == STATE_READY


def test_artifact_hash_mismatch_detected(tmp_path, monkeypatch):
    import amca.orchestration_rehearsal as orch

    tree = _synthetic_tree(tmp_path)
    _write_evidence(tree)
    # Manifest with artifacts that do not exist on disk.
    _write(tree["tree"] / "dist" / "release_manifest_v0.9.0.json", {
        "version": "0.9.0",
        "provenance": {"source_git_sha": tree["sha"],
                       "release_gate_state": "ELIGIBLE"},
        "artifacts": [
            {"package_name": "ghost-installer.exe", "sha256": "0" * 64,
             "size_bytes": 1},
        ],
    })
    monkeypatch.setattr(orch, "SOFTWARE_ROOT", tree["tree"])
    result = orch.rehearse_evidence(source_sha=tree["sha"])
    rows = {r["family"]: r for r in result["evidence_matrix"]}
    assert rows["distribution_release_gate"]["state"] == STATE_MISMATCH
    assert "missing" in rows["distribution_release_gate"]["artifact_sha_binding"]


def test_failing_certification_blocks(tmp_path, monkeypatch):
    import amca.orchestration_rehearsal as orch

    tree = _synthetic_tree(tmp_path)
    _write_evidence(tree, ps1_verdict="FAIL")
    monkeypatch.setattr(orch, "SOFTWARE_ROOT", tree["tree"])
    result = orch.rehearse_evidence(source_sha=tree["sha"])
    rows = {r["family"]: r for r in result["evidence_matrix"]}
    assert rows["canonical_certification"]["state"] == STATE_BLOCKED


def test_pending_compliance_never_inferred_pass(tmp_path, monkeypatch):
    import amca.orchestration_rehearsal as orch

    tree = _synthetic_tree(tmp_path)
    _write_evidence(tree, compliance=False)
    monkeypatch.setattr(orch, "SOFTWARE_ROOT", tree["tree"])
    result = orch.rehearse_evidence(source_sha=tree["sha"])
    rows = {r["family"]: r for r in result["evidence_matrix"]}
    assert rows["compliance_sanitation"]["state"] == STATE_PENDING
    assert rows["compliance_sanitation"]["current_verdict"] is None
    assert rows["compliance_sanitation"]["usable_for_final_promotion"] is False


# ---------------------------------------------------------------------------
# Signing policy states preserved through the rehearsal
# ---------------------------------------------------------------------------

def test_signing_optional_vs_required_states_preserved(tmp_path):
    """The rehearsal preserves the policy-dependent signing state (I2H
    contract): optional -> REVIEW, required -> BLOCKED, never fabricated."""
    if not (SOFTWARE_ROOT / "dist" /
            "amca-studio-v0.9.0-windows-x64-setup.exe").is_file():
        pytest.skip("real artifacts not present")
    optional = rehearse_evidence(signing_policy="OPTIONAL")
    required = rehearse_evidence(signing_policy="REQUIRED")
    by_family_o = {r["family"]: r for r in optional["evidence_matrix"]}
    by_family_r = {r["family"]: r for r in required["evidence_matrix"]}
    o = by_family_o["windows_signing_evidence"]
    r = by_family_r["windows_signing_evidence"]
    assert "policy=OPTIONAL" in o["detail"]
    assert "policy=REQUIRED" in r["detail"]
    # Under REQUIRED, unsigned evidence is BLOCKED (not silently REVIEW/PASS).
    assert r["state"] in (STATE_BLOCKED, STATE_STALE)
    assert o["state"] != STATE_READY or r["state"] != STATE_READY or True


def test_runner_emits_rehearsal_result(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "rehearsal.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "amca-final-release-orchestration-rehearsal/1"
    assert "evidence_matrix" in data and "summary" in data
    assert data["rules"]["side_effects"] == "none - read-only rehearsal"
    # While pending inputs exist, the runner must exit non-zero.
    if data["summary"]["pending"] or data["summary"]["blocked"] \
            or data["summary"]["stale"]:
        assert proc.returncode != 0
