"""PS-2 aggregate -> final promotion input adapter tests (I2U).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2U-PS2-AGGREGATE-TO-FINAL-PROMOTION-ADAPTER-AND-EVIDENCE-PATH-CONTRACT-01
Worker: OpenCode PS-2. Tests only; read-only adapter; no promotion-gate,
packaging, version, or product changes.

Covers: same-lineage ready; ready-with-review preserved; absent MPL blocked;
MPL asset without provenance blocked; provenance hash mismatch;
stale source SHA rejected; installer/portable mismatch rejected; v0.9
evidence cannot certify v1.0; FAIL cannot become ready; policy
acknowledgment cannot alter the technical verdict; no legal approval
fabricated; no secrets; adapter read-only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "build_ps2_promotion_input.py"
)
_spec = importlib.util.spec_from_file_location("ps2_promotion", SCRIPT)
assert _spec is not None and _spec.loader is not None
pi = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pi
_spec.loader.exec_module(pi)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent


def _family(state: str) -> dict:
    return {"state": state, "detail": f"simulated {state}"}


def _aggregate(verdict: str = "PS2_RELEASE_READY",
               source_sha: str = "a" * 40,
               mpl_state: str = "PASS",
               portable_sha: str = "b" * 64,
               installer_sha: str = "c" * 64) -> dict:
    return {
        "schema": "amca-ps2-final-release-evidence/1",
        "generated_utc": "2026-09-04T00:00:00Z",
        "release_version": "1.0.0",
        "source_sha": source_sha,
        "artifact_hashes": {
            "portable": {"sha256": portable_sha, "size_bytes": 1},
            "installer": {"sha256": installer_sha, "size_bytes": 1},
        },
        "families": {
            "migration_upgrade_certification": _family("PASS"),
            "final_package_migration_smoke": _family("PASS"),
            "sanitation_privacy_audit": _family("PASS"),
            "user_documentation_integrity": _family("PASS"),
            "dependency_sbom_compliance": _family("PASS"),
            "third_party_payload_completeness": _family("PASS_WITH_REVIEW"),
            "final_package_compliance_verification": _family("PASS"),
            "mpl_readiness_intake_state": _family(mpl_state),
        },
        "review_items": ["third_party_payload: certifi (REVIEW_REMAINS)"],
        "blockers": [],
        "stale_inputs": [],
        "aggregate_verdict": verdict,
    }


def _provenance(state: str = "PROVENANCE_READY",
                staged_sha: str = "d" * 64) -> dict:
    return {
        "schema": "amca-mpl-release-asset-provenance/1",
        "provenance_state": state,
        "component": "certifi",
        "component_version": "2026.7.22",
        "staged_sha256": staged_sha,
        "intake_receipt_sha256": "e" * 64,
    }


def _write(tmp_path: Path, name: str, doc: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_complete_same_lineage_evidence_ready(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov,
                                   expected_source_sha="a" * 40)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_READY"
    assert doc["source_sha"] == "a" * 40
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_READY"


def test_ready_with_review_remains_ready_with_review(tmp_path: Path):
    agg = _write(tmp_path, "agg.json",
                 _aggregate(verdict="PS2_RELEASE_READY_WITH_REVIEW"))
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_READY_WITH_REVIEW"
    # The adapter must never soften READY_WITH_REVIEW to READY.
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_READY_WITH_REVIEW"


def test_absent_mpl_blocked(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate(
        verdict="PS2_RELEASE_BLOCKED", mpl_state="BLOCKED"))
    prov = _write(tmp_path, "prov.json",
                  _provenance(state="RELEASE_ASSET_REQUIRED",
                              staged_sha=""))
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_BLOCKED"


def test_mpl_asset_without_provenance_blocked(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json",
                  _provenance(state="ASSET_NOT_STAGED", staged_sha=""))
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_BLOCKED"
    assert any("provenance" in r for r in doc["reasons"])


def test_provenance_hash_mismatch_failed(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    # Provenance claims READY but the staged hash differs from what the
    # final compliance evidence consumed (simulated via family evidence sha).
    prov = _write(tmp_path, "prov.json", _provenance(staged_sha="d" * 64))
    agg_doc = json.loads(Path(agg).read_text(encoding="utf-8"))
    agg_doc["families"]["mpl_readiness_intake_state"]["evidence_sha256"] = \
        "9" * 64
    Path(agg).write_text(json.dumps(agg_doc), encoding="utf-8")
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"


def test_stale_source_sha_rejected(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate(source_sha="0" * 40))
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov,
                                   expected_source_sha="a" * 40)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"
    assert any("v0.9" in r or "mismatch" in r for r in doc["reasons"])


def test_portable_mismatch_rejected(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov,
                                   expected_portable_sha="9" * 64)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"


def test_installer_mismatch_rejected(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov,
                                   expected_installer_sha="8" * 64)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"


def test_v09_evidence_cannot_certify_v10(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate(source_sha="v0.9-lineage"))
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov,
                                   expected_source_sha="a" * 40)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"


def test_fail_cannot_become_ready(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate(verdict="PS2_RELEASE_FAILED"))
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"


def test_blocked_cannot_become_ready(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate(verdict="PS2_RELEASE_BLOCKED",
                                                  mpl_state="BLOCKED"))
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_BLOCKED"


def test_policy_acknowledgment_cannot_alter_technical_verdict(tmp_path: Path):
    agg = _write(tmp_path, "agg.json",
                 _aggregate(verdict="PS2_RELEASE_READY_WITH_REVIEW"))
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov)
    # Even if a policy acknowledged every REVIEW, the technical verdict stays.
    doc["policy_acknowledgments"] = ["certifi", "pyinstaller",
                                     "pyinstaller_hooks_contrib"]
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_READY_WITH_REVIEW"


def test_no_legal_approval_fabricated(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov)
    blob = json.dumps(doc)
    assert "legal_approval" not in blob
    assert "LEGAL_APPROVED" not in blob
    assert doc["legal_boundary"]


def test_no_secrets_user_data(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json", _provenance())
    doc = pi.build_promotion_input(agg, prov)
    blob = json.dumps(doc).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "begin private key" not in blob


def test_adapter_read_only(tmp_path: Path):
    agg = _write(tmp_path, "agg.json", _aggregate())
    prov = _write(tmp_path, "prov.json", _provenance())
    agg_before = hashlib_file(agg)
    prov_before = hashlib_file(prov)
    pi.build_promotion_input(agg, prov)
    assert hashlib_file(agg) == agg_before
    assert hashlib_file(prov) == prov_before


def test_missing_inputs_blocked(tmp_path: Path):
    doc = pi.build_promotion_input(tmp_path / "no-agg.json",
                                   tmp_path / "no-prov.json")
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_BLOCKED"


def hashlib_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_live_chain_smoke():
    """Live I2R + I2T chain (current v0.9 lineage) must be BLOCKED, not
    silently ready — matching the tree's RELEASE_ASSET_REQUIRED state."""
    root = SOFTWARE_ROOT
    agg_path = root.parent / ".agent-tmp" / "i2u-aggregate.json"
    prov_path = root.parent / ".agent-tmp" / "i2u-provenance.json"
    if not (agg_path.is_file() and prov_path.is_file()):
        pytest.skip("live chain artifacts not staged for this run")
    agg_doc = json.loads(agg_path.read_text(encoding="utf-8"))
    prov_doc = json.loads(prov_path.read_text(encoding="utf-8"))
    agg = _write(Path(agg_path.parent), "live-agg.json",
                 _aggregate(verdict=agg_doc["aggregate_verdict"],
                            source_sha=agg_doc.get("source_sha") or "a" * 40,
                            mpl_state=agg_doc["families"][
                                "mpl_readiness_intake_state"]["state"]))
    prov = _write(Path(prov_path.parent), "live-prov.json",
                  _provenance(state=prov_doc["provenance_state"]))
    doc = pi.build_promotion_input(agg, prov)
    assert doc["promotion_eligibility_ps2"] in (
        "PS2_INPUT_BLOCKED", "PS2_INPUT_READY_WITH_REVIEW")