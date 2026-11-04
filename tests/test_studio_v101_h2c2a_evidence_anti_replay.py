"""H2C.2A evidence anti-replay and stale fail-closed tests.

Milestone: ZCODE-AMCA-V1.0.1-H2C.2A-EVIDENCE-ANTI-REPLAY-AND-STALE-FAIL-CLOSED-REPAIR-01
Worker: ZCode. Proves the H1H final validator rejects replayed/stale/
mismatched evidence and accepts only a coherent same-source,
same-artifact evidence chain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
WORKTREE_SRC = str(SOFTWARE_ROOT / "src")
sys.path.insert(0, WORKTREE_SRC)

CURRENT_SHA = "22b3268e10cfe428ec5959c6ab52d086bd79872a"
SUPERSEDED_SHA = "405c3dfe6fb6ccafca6cedde4528c01efadf83e0"
SUPERSEDED_B71_SHA = "b71f513d808543536ecbb9a4012d66a847e654c2"
DIST = SOFTWARE_ROOT / "dist"

ARTIFACT_HASHES = {
    "exe": "b072d69a05271d58850233afde0fd90fa544337a1f166ef03719c5d14e20f5e8",
    "msi": "60da557f97082a4a3c59fc0c507b708f4417257b7ecbe9a458901ccff3c65b96",
    "portable": "feff399ffa3ddc07d883d94d5a05020a2293513b5b3932ddf24fd8f109e7dc6a",
}
SUPERSEDED_B71_HASHES = {
    "exe": "3c8d60d244382f10ccd3c14b91cf58799403963a61986b58b063dc2b87d7e0dd",
    "msi": "fce395da572c550beb635f5a9299e4ba4cc553be43a8bba4b1f45d6d03641868",
    "portable": "369efc37201970209c1e2db7f82366268ecc9b8825b953801d322367a645ea59",
}
ARTIFACT_NAMES = {
    "exe": "amca-studio-v1.0.1-windows-x64-setup.exe",
    "msi": "amca-studio-v1.0.1-windows-x64.msi",
    "portable": "amca-studio-v1.0.1-windows-x64-portable.zip",
}


def _load(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rv = _load("h2c2a_rv", SCRIPTS / "run_v101_release_validation.py")
ag = _load("h2c2a_ag", SCRIPTS / "run_ps2_final_release_validation.py")


def model_evidence(source_sha: str = CURRENT_SHA,
                   artifact_hashes: dict | None = None,
                   run_id: str = "a55dbf902cde4842b6c1db1eeb04085a",
                   fresh: str = "true",
                   mock: object = "NO") -> dict:
    return {
        "schema": "amca-v101-real-model-evidence/1",
        "release_version": "1.0.1",
        "source_sha": source_sha,
        "artifact_sha256": dict(artifact_hashes or ARTIFACT_HASHES),
        "build_bound": True,
        "MODEL": "qwen3:1.7b",
        "model_id": "qwen3:1.7b",
        "OLLAMA_MODELS": r"D:\ChatGPT\AMCA\ollama\models",
        "ollama_models_path": r"D:\ChatGPT\AMCA\ollama\models",
        "RUN_ID": run_id,
        "run_id": run_id,
        "MOCK_OR_REPLAY_USED": mock,
        "mocked": False,
        "MODEL_RESPONSE_RECEIVED": "YES",
        "model_response_received": "YES",
        "RUN_STATUS_COMPLETE": "YES",
        "run_status_complete": "YES",
        "fresh_readiness_verified": fresh,
        "exact_response": "AMCA v1.0.1 physical test successful.",
        "adapter_id": "ollama:qwen3:1.7b",
        "provider": "ollama",
    }


def upgrade_evidence(source_sha: str = CURRENT_SHA,
                     hashes: dict | None = None,
                     overall: str = "PASS") -> dict:
    h = dict(hashes or ARTIFACT_HASHES)
    return {
        "schema": "amca-v101-upgrade-path-evidence/1",
        "release_version": "1.0.1",
        "source_sha": source_sha,
        "overall": overall,
        "paths": {
            role: {"artifact": {"source_sha": source_sha, "sha256": h[role],
                                "path": f"D:/ChatGPT/AMCA/AMCA-h2a-rc/software/dist/{ARTIFACT_NAMES[role]}",
                                "size_bytes": 100, "version": "1.0.1"},
                   "state": "PASS",
                   **({"execution_record": {
                       "msi_sha256": h["msi"], "install_result": "PASS",
                       "uninstall_result": "PASS", "upgrade_result": "PASS"}}
                      if role == "msi" else {})}
            for role in ("exe", "msi", "portable")
        },
    }


def runner_inputs(**overrides) -> dict:
    inputs = {
        "release_version": "1.0.1",
        "source_sha": CURRENT_SHA,
        "artifacts": {
            role: {"path": str(DIST / ARTIFACT_NAMES[role]),
                   "sha256": sha, "size_bytes": size}
            for role, sha, size in (
                ("exe", ARTIFACT_HASHES["exe"], 23480033),
                ("msi", ARTIFACT_HASHES["msi"], 24436736),
                ("portable", ARTIFACT_HASHES["portable"], 24095388))
        },
        "model_evidence": model_evidence(),
        "migration_evidence": upgrade_evidence(),
        "msi_execution": {"msi_sha256": ARTIFACT_HASHES["msi"],
                          "size_bytes": 24436736,
                          "product_name": "AMCA Studio",
                          "product_version": "1.0.1",
                          "install_result": "PASS",
                          "silent_install_result": "PASS",
                          "installed_payload_verification": "PASS",
                          "packaged_backend_health": "PASS",
                          "desktop_launch": "PASS",
                          "uninstall_result": "PASS",
                          "upgrade_result": "PASS",
                          "residual_installation_check": "PASS"},
        "release_manifest": {"version": "1.0.1",
                             "provenance": {"source_git_sha": CURRENT_SHA},
                             "artifacts": [
                                 {"package_name": ARTIFACT_NAMES[r],
                                  "sha256": ARTIFACT_HASHES[r],
                                  "size_bytes": {"exe": 23480033,
                                                 "msi": 24436736,
                                                 "portable": 24095388}[r]}
                                 for r in ("exe", "msi", "portable")]},
        "sha256sums_path": str(DIST / "SHA256SUMS.txt"),
        "compliance_evidence": r"D:\ChatGPT\AMCA\.agent-tmp\smoke\h2c2d-compliance-evidence.json",
    }
    inputs.update(overrides)
    return inputs


def passing_steps() -> dict:
    return {name: (lambda: ("PASS", ["ok"])) for name in rv.CHAIN_ORDER}


# --- Part A: build-bound real-model evidence ---------------------------------

def test_build_bound_model_evidence_accepts_current_candidate():
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(), CURRENT_SHA, ARTIFACT_HASHES)
    assert v == "PASS", reasons


def test_superseded_405c3dfe_model_evidence_is_rejected():
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(source_sha=SUPERSEDED_SHA), CURRENT_SHA,
        ARTIFACT_HASHES)
    assert v == "BLOCKED"
    assert any("superseded" in r for r in reasons)


def test_b71_superseded_candidate_replay_is_rejected():
    """H2C.2D Part C: the b71f513d-era candidate set is superseded; its
    evidence must be rejected against the current candidate."""
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(source_sha=SUPERSEDED_B71_SHA),
        CURRENT_SHA, ARTIFACT_HASHES)
    assert v == "BLOCKED"
    assert any("source SHA" in r for r in reasons)
    v2, r2 = rv.validate_model_evidence_build_bound(
        model_evidence(artifact_hashes=dict(SUPERSEDED_B71_HASHES)),
        CURRENT_SHA, ARTIFACT_HASHES)
    assert v2 == "BLOCKED"
    assert any("artifact hash mismatch" in r for r in r2)


def test_wrong_source_sha_model_evidence_is_rejected():
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(source_sha="d" * 64), CURRENT_SHA, ARTIFACT_HASHES)
    assert v == "BLOCKED"
    assert any("source SHA" in r for r in reasons)


def test_wrong_artifact_sha_model_evidence_is_rejected():
    bad = dict(ARTIFACT_HASHES, msi="9" * 64)
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(artifact_hashes=bad), CURRENT_SHA, ARTIFACT_HASHES)
    assert v == "BLOCKED"
    assert any("msi" in r for r in reasons)


def test_missing_run_model_path_identity_is_rejected():
    doc = model_evidence()
    for field in ("run_id", "model_id", "ollama_models_path"):
        doc.pop(field)
        doc[field.upper() if field == "run_id" else field] = None
    v, reasons = rv.validate_model_evidence_build_bound(doc, CURRENT_SHA,
                                                        ARTIFACT_HASHES)
    assert v == "BLOCKED"


def test_stale_readiness_is_rejected():
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(fresh="false"), CURRENT_SHA, ARTIFACT_HASHES)
    assert v == "BLOCKED"
    assert any("readiness" in r for r in reasons)


def test_ambiguous_mock_state_is_rejected():
    v, reasons = rv.validate_model_evidence_build_bound(
        model_evidence(mock=None), CURRENT_SHA, ARTIFACT_HASHES)
    assert v == "BLOCKED"
    assert any("mock" in r for r in reasons)
    v2, _ = rv.validate_model_evidence_build_bound(
        model_evidence(mock="YES"), CURRENT_SHA, ARTIFACT_HASHES)
    assert v2 == "BLOCKED"


def test_runner_model_gate_rejects_superseded_evidence_end_to_end():
    report = rv.run_validation(runner_inputs(
        model_evidence=model_evidence(source_sha=SUPERSEDED_SHA)),
        steps=passing_steps())
    assert report["V101_MODEL_PHYSICAL_EVIDENCE"] == "BLOCKED"
    assert any("superseded" in str(b) for b in report["blockers"])
    assert report["V101_PACKAGE_VALIDATION"] == "BLOCKED"


# --- Part B: certification/migration/source cross-check ----------------------

def test_crosscheck_accepts_coherent_chain():
    steps = rv.default_steps(runner_inputs())
    v, reasons = steps["cert_migration_crosscheck"]()
    assert v == "PASS", reasons


def test_crosscheck_rejects_cert_migration_source_mismatch():
    steps = rv.default_steps(runner_inputs(
        migration_evidence=upgrade_evidence(source_sha=SUPERSEDED_SHA),
        aggregate={"schema": "amca-ps2-final-release-evidence/1",
                   "source_sha": CURRENT_SHA}))
    v, reasons = steps["cert_migration_crosscheck"]()
    assert v == "BLOCKED"
    assert any("migration evidence" in r for r in reasons)


def test_crosscheck_rejects_migration_superseded_artifact_set():
    stale_hashes = dict(ARTIFACT_HASHES, exe="4" * 64)
    steps = rv.default_steps(runner_inputs(
        migration_evidence=upgrade_evidence(hashes=stale_hashes)))
    v, reasons = steps["cert_migration_crosscheck"]()
    assert v == "BLOCKED"
    assert any("superseded" in r or "exe" in r for r in reasons)


def test_crosscheck_rejects_aggregate_bound_to_other_source():
    steps = rv.default_steps(runner_inputs(
        aggregate={"schema": "amca-ps2-final-release-evidence/1",
                   "source_sha": "e" * 64}))
    v, reasons = steps["cert_migration_crosscheck"]()
    assert v == "BLOCKED"
    assert any("aggregate" in r for r in reasons)


def test_crosscheck_rejects_missing_candidate_identity():
    steps = rv.default_steps(runner_inputs(source_sha=""))
    v, reasons = steps["cert_migration_crosscheck"]()
    assert v == "BLOCKED"
    assert any("identity missing" in r for r in reasons)


def test_crosscheck_is_in_fail_closed_chain():
    assert "cert_migration_crosscheck" in rv.CHAIN_ORDER


# --- Part C: STALE fails closed ----------------------------------------------

def test_stale_family_fails_closed_in_aggregate():
    families = {name: {"state": "PASS"} for name in ag.FAMILIES}
    assert ag.aggregate(families) == "PS2_RELEASE_READY"
    families["dependency_sbom_compliance"] = {"state": "STALE"}
    assert ag.aggregate(families) == "PS2_RELEASE_BLOCKED"


def test_stale_never_surfaces_as_ready_with_review():
    families = {name: {"state": "PASS"} for name in ag.FAMILIES}
    for stale_family in ag.FAMILIES:
        variant = {name: {"state": "PASS"} for name in ag.FAMILIES}
        variant[stale_family]["state"] = "STALE"
        assert ag.aggregate(variant) == "PS2_RELEASE_BLOCKED", stale_family


# --- coherent chain ----------------------------------------------------------

def test_coherent_same_source_chain_passes_end_to_end(tmp_path):
    """Real bytes + real b71f513d-bound evidence: the full orchestrator
    passes with the model gate and the crosscheck gate green."""
    doc = ag.build_aggregate(
        portable=DIST / ARTIFACT_NAMES["portable"],
        installer=DIST / ARTIFACT_NAMES["exe"],
        source_sha=CURRENT_SHA,
        msi=DIST / ARTIFACT_NAMES["msi"])
    assert doc["aggregate_verdict"] in ("PS2_RELEASE_READY",
                                        "PS2_RELEASE_READY_WITH_REVIEW")
    agg_path = tmp_path / "i2r-aggregate.json"
    agg_path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    prov_path = Path(r"D:\ChatGPT\AMCA\software\dist\release-evidence\mpl-asset-provenance.json")
    inputs = runner_inputs(aggregate_path=str(agg_path),
                           provenance_path=str(prov_path))
    report = rv.run_validation(inputs)
    assert report["V101_MODEL_PHYSICAL_EVIDENCE"] == "PASS"
    assert report["chain"]["cert_migration_crosscheck"]["verdict"] == "PASS"
    assert report["chain"]["i2u"]["verdict"] == "PASS"
    assert report["V101_PACKAGE_VALIDATION"] in ("PASS", "PASS_WITH_REVIEW")
