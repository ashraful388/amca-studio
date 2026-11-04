"""Final evidence authenticity + fixture quarantine tests (I3B).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I3B-SYNTHETIC-EVIDENCE-FIXTURE-QUARANTINE-AND-FINAL-EVIDENCE-AUTHENTICITY-GUARD-01
Worker: OpenCode PS-2. Tests only. Proves synthetic/test/historical evidence
cannot satisfy final PS-2 promotion readiness while genuine same-lineage
final evidence remains valid. No source/package mutation; no worker paths or
secrets.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "evidence_authenticity_guard.py"
)
_spec = importlib.util.spec_from_file_location("authenticity_guard", SCRIPT)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ag = _load("ps2_aggregate_i3b", "run_ps2_final_release_validation.py")
pi = _load("ps2_promotion_i3b", "build_ps2_promotion_input.py")


def _final_doc() -> dict:
    return {
        "schema": "amca-ps2-promotion-input/1",
        "source_sha": "a" * 40,
        "release_version": "1.0.0",
        "portable_sha256": "b" * 64,
        "installer_sha256": "c" * 64,
        "ps2_aggregate_verdict": "PS2_RELEASE_READY",
        "mpl_readiness": "PASS",
        "mpl_provenance_state": "PROVENANCE_READY",
        "promotion_eligibility_ps2": "PS2_INPUT_READY",
        "final_run_recorded": True,
    }


def test_synthetic_transition_evidence_rejected(tmp_path: Path):
    doc = _final_doc()
    doc["evidence_note"] = "generated in synthetic transition simulation tree"
    verdict, reasons = guard.final_eligibility_verdict(
        doc, expected_source_sha="a" * 40)
    assert verdict == "QUARANTINED"
    assert any("synthetic" in r or "simulation" in r for r in reasons)


def test_pytest_fixture_evidence_rejected(tmp_path: Path):
    doc = _final_doc()
    doc["input_path"] = \
        r"C:\Users\x\AppData\Local\Temp\pytest-of-user\pytest-1\test_x0\e.json"
    verdict, reasons = guard.final_eligibility_verdict(doc)
    assert verdict == "QUARANTINED"
    assert any("pytest-of-" in r for r in reasons)


def test_historical_v09_evidence_rejected():
    doc = _final_doc()
    doc["source_sha"] = "v0.9-lineage-sha"
    classification, reasons = guard.classify(doc)
    assert classification == "HISTORICAL_EVIDENCE"
    verdict, _ = guard.final_eligibility_verdict(doc)
    assert verdict == "QUARANTINED"


def test_mismatched_source_sha_rejected():
    doc = _final_doc()
    verdict, reasons = guard.final_eligibility_verdict(
        doc, expected_source_sha="f" * 40)
    assert verdict == "QUARANTINED"
    assert any("source SHA mismatch" in r for r in reasons)


def test_fabricated_installer_hash_rejected():
    doc = _final_doc()
    verdict, reasons = guard.final_eligibility_verdict(
        doc, expected_source_sha="a" * 40,
        expected_portable_sha="b" * 64, expected_installer_sha="9" * 64)
    assert verdict == "QUARANTINED"
    assert any("installer hash mismatch" in r for r in reasons)


def test_copied_validation_evidence_cannot_become_final_by_renaming(tmp_path: Path):
    """Validation-role evidence copied to a canonical final path stays
    quarantined — renaming/copying cannot confer final eligibility."""
    validation = {
        "schema": "amca-ps2-promotion-input/1",
        "source_sha": "a" * 40,
        "portable_sha256": "b" * 64,
        "installer_sha256": "c" * 64,
        "package_role": "validation",
        "promotion_eligibility_ps2": "PS2_INPUT_READY_WITH_REVIEW",
    }
    canonical_rel = guard.CANONICAL_FINAL_EVIDENCE[1]
    verdict, reasons = guard.final_eligibility_verdict(
        validation, path=canonical_rel)
    assert verdict == "QUARANTINED"
    assert any("non-final role" in r for r in reasons)


def test_real_shaped_same_lineage_but_marked_synthetic_rejected():
    doc = _final_doc()
    doc["mode"] = "simulation"
    verdict, reasons = guard.final_eligibility_verdict(doc)
    assert verdict == "QUARANTINED"


def test_final_evidence_correct_lineage_accepted():
    verdict, reasons = guard.final_eligibility_verdict(_final_doc())
    assert verdict == "ACCEPTED", reasons
    assert "genuine same-lineage final evidence" in reasons[0]


def test_i2r_i2u_behavior_remains_strict(tmp_path: Path):
    """Simulated-transition aggregate cannot reach ready via I2R/I2U."""
    families = {f: {"state": "PASS"} for f in ag.FAMILIES}
    families["mpl_readiness_intake_state"] = {"state": "BLOCKED"}
    assert ag.aggregate(families) == "PS2_RELEASE_BLOCKED"
    pi_doc = {"schema": pi.SCHEMA,
              "promotion_eligibility_ps2": "PS2_INPUT_READY",
              "ps2_aggregate_verdict": "PS2_RELEASE_BLOCKED",
              "source_sha": "a" * 40, "portable_sha256": "b" * 64,
              "installer_sha256": "c" * 64, "mpl_readiness": "BLOCKED",
              "mpl_provenance_state": "ASSET_NOT_STAGED"}
    verdict, _ = pi.validate_promotion_input(pi_doc)
    assert verdict in ("PS2_INPUT_BLOCKED", "PS2_INPUT_FAILED")


def test_no_source_package_mutation():
    portable = (SOFTWARE_ROOT / "dist" /
                "amca-studio-v0.9.0-windows-x64-portable.zip")
    if portable.is_file():
        before = hashlib.sha256(portable.read_bytes()).hexdigest()
    candidate = (SOFTWARE_ROOT / "docs" / "release" /
                 "AMCA-Studio-v1.0.0-Release-Notes-Candidate.md")
    before_candidate = hashlib.sha256(candidate.read_bytes()).hexdigest()
    guard.final_eligibility_verdict(_final_doc())
    guard.classify(_final_doc(), path="anywhere.json")
    if before_pkg_hash_if_present() is not None:
        pass  # placeholder to keep structure clear
    if portable.is_file():
        assert hashlib.sha256(portable.read_bytes()).hexdigest() == before
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() == \
        before_candidate


def before_pkg_hash_if_present():
    return None


def test_no_worker_paths_or_secrets():
    blob = json.dumps({k: str(v) for k, v in vars(guard).items()
                       if not k.startswith("__")}).casefold()
    assert "md. ashraful" not in blob
    assert "opencode-ps2-worktree" not in blob
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob


def test_quarantine_report_counts():
    docs = [
        ("final.json", _final_doc()),
        ("synthetic.json", dict(_final_doc(), mode="simulation")),
        ("historical.json", dict(_final_doc(), source_sha="v0.9-old")),
    ]
    report = guard.quarantine_report(docs)
    assert report["final_eligible"] == 1
    assert report["quarantined"] == 2
    assert report["counts"]["SYNTHETIC_SIMULATION"] == 1
    assert report["counts"]["HISTORICAL_EVIDENCE"] == 1


def test_guard_schema():
    report = guard.quarantine_report([("x.json", _final_doc())])
    assert report["schema"] == "amca-evidence-authenticity-guard/1"
    assert set(report["counts"]) == set(guard.CLASSIFICATIONS)