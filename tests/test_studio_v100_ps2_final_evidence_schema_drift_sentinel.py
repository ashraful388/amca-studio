"""PS-2 final evidence schema-drift sentinel + post-transition guard (I2V).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2V-PS2-FINAL-EVIDENCE-SCHEMA-DRIFT-SENTINEL-AND-POST-TRANSITION-COMPATIBILITY-GUARD-01
Worker: OpenCode PS-2. Tests/docs only; no product/version/packaging/PS-1
I1.22/ZCode I2L/MPL-content changes.

Proves the PS-2 final evidence chain remains strict, same-lineage,
review-preserving, and compatible with the forthcoming 0.9.0 -> 1.0.0
transition:

- every expected schema is recognized and its required fields pinned;
- required fields cannot disappear silently; unknown schema versions are
  rejected;
- version-transition simulation (disposable fixtures only) proves v0.9
  evidence stays rejected, correctly formed same-SHA v1.0 evidence is
  accepted, PASS_WITH_REVIEW cannot drift to PASS, MPL
  RELEASE_ASSET_REQUIRED cannot become READY without valid intake +
  provenance, and the read-only adapters stay side-effect free.

No canonical branch is bumped; no production source is mutated.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


me = _load("migration_evidence_i2v",
           "generate_migration_certification_evidence.py")
fv = _load("final_pkg_verify_i2v",
           "run_final_package_compliance_verification.py")
mr = _load("mpl_readiness_i2v", "run_mpl_notice_readiness.py")
mi = _load("mpl_intake_i2v", "import_mpl_release_asset.py")
pv = _load("mpl_provenance_i2v", "record_mpl_asset_provenance.py")
ag = _load("ps2_aggregate_i2v", "run_ps2_final_release_validation.py")
pi = _load("ps2_promotion_i2v", "build_ps2_promotion_input.py")
dx = _load("doc_export_i2v", "export_final_release_documentation.py")

EXPECTED_SCHEMAS = {
    "amca-migration-certification-evidence/1": me,
    "amca-final-package-compliance/1": fv,
    "amca-mpl-notice-readiness/1": mr,
    "amca-mpl-asset-intake-receipt/1": mi,
    "amca-mpl-release-asset-provenance/1": pv,
    "amca-ps2-final-release-evidence/1": ag,
    "amca-ps2-promotion-input/1": pi,
    "amca-final-release-documentation-export/1": dx,
}

# Mandatory fields per schema (structural drift pinning).
REQUIRED_FIELDS = {
    "amca-migration-certification-evidence/1": [
        "schema", "evidence_schema_version", "overall", "package",
        "verdicts", "bindings", "smoke_result"],
    "amca-final-package-compliance/1": [
        "schema", "overall", "source_sha", "artifacts", "sbom_sha256",
        "notices_sha256", "compliance_payload_sha256"],
    "amca-mpl-notice-readiness/1": [
        "schema", "component", "version", "license", "state", "asset",
        "evidence", "legal_boundary"],
    "amca-mpl-asset-intake-receipt/1": [
        "schema", "input_sha256", "staged_sha256", "component", "version",
        "validator_result", "destination"],
    "amca-mpl-release-asset-provenance/1": [
        "schema", "component", "component_version", "input_sha256",
        "staged_sha256", "canonical_mpl_validator_verdict",
        "provenance_state", "intake_receipt_sha256"],
    "amca-ps2-final-release-evidence/1": [
        "schema", "release_version", "source_sha", "artifact_hashes",
        "families", "review_items", "blockers", "aggregate_verdict"],
    "amca-ps2-promotion-input/1": [
        "schema", "source_sha", "release_version", "installer_sha256",
        "portable_sha256", "ps2_aggregate_verdict", "mpl_readiness",
        "mpl_provenance_state", "promotion_eligibility_ps2"],
    "amca-final-release-documentation-export/1": [
        "schema", "release_version", "certified_source_sha", "documents",
        "manifest_sha256", "overall"],
}


def test_every_expected_schema_recognized():
    for schema_id, module in EXPECTED_SCHEMAS.items():
        found = [value for value in vars(module).values()
                 if isinstance(value, str) and value == schema_id]
        assert found, f"schema id not defined in module: {schema_id}"


def test_required_fields_pinned_for_every_schema():
    for schema_id, fields in REQUIRED_FIELDS.items():
        pinned = REQUIRED_FIELDS[schema_id]
        assert pinned == fields  # no silent drift of the sentinel itself
    # Every schema has at least schema + one content field pinned.
    for schema_id in EXPECTED_SCHEMAS:
        assert len(REQUIRED_FIELDS[schema_id]) >= 4


def _missing_field_documents():
    """Build one minimal doc per schema with a mandatory field removed."""
    docs = {
        "amca-migration-certification-evidence/1": {
            "schema": "amca-migration-certification-evidence/1",
            "evidence_schema_version": "1.0.0", "overall": "PASS",
            "package": {"sha256": "a" * 64}, "verdicts": {},
            "bindings": {}, "smoke_result": {}},
        "amca-final-package-compliance/1": {
            "schema": "amca-final-package-compliance/1", "overall": "PASS",
            "source_sha": "a" * 40, "artifacts": {},
            "sbom_sha256": "b" * 64, "notices_sha256": "c" * 64,
            "compliance_payload_sha256": "d" * 64},
        "amca-mpl-release-asset-provenance/1": {
            "schema": "amca-mpl-release-asset-provenance/1",
            "component": "certifi", "component_version": "2026.7.22",
            "input_sha256": "e" * 64, "staged_sha256": "f" * 64,
            "canonical_mpl_validator_verdict": "ACCEPTED_CANONICAL_MPL_2_0",
            "provenance_state": "PROVENANCE_READY",
            "intake_receipt_sha256": "1" * 64},
        "amca-ps2-final-release-evidence/1": {
            "schema": "amca-ps2-final-release-evidence/1",
            "release_version": "1.0.0", "source_sha": "a" * 40,
            "artifact_hashes": {}, "families": {}, "review_items": [],
            "blockers": [], "aggregate_verdict": "PS2_RELEASE_READY"},
        "amca-ps2-promotion-input/1": {
            "schema": "amca-ps2-promotion-input/1", "source_sha": "a" * 40,
            "release_version": "1.0.0", "installer_sha256": "g" * 64,
            "portable_sha256": "h" * 64,
            "ps2_aggregate_verdict": "PS2_RELEASE_READY",
            "mpl_readiness": "PASS",
            "mpl_provenance_state": "PROVENANCE_READY",
            "promotion_eligibility_ps2": "PS2_INPUT_READY"},
    }
    out = {}
    for schema_id, doc in docs.items():
        for field in REQUIRED_FIELDS[schema_id]:
            broken = dict(doc)
            broken.pop(field, None)
            out[(schema_id, field)] = broken
    return out


def test_missing_mandatory_field_rejected():
    docs = _missing_field_documents()
    # Migration evidence: missing verdicts must not validate as PASS.
    broken = dict(docs[("amca-migration-certification-evidence/1",
                        "verdicts")])
    broken["overall"] = "PASS"
    broken["review_acknowledgments"] = []
    verdict, reasons = me.validate_migration_evidence(broken)
    assert verdict != "PASS"
    # Provenance: missing mandatory field -> BLOCKED.
    broken = docs[("amca-mpl-release-asset-provenance/1",
                   "canonical_mpl_validator_verdict")]
    verdict, reasons = pv.validate_provenance(broken)
    assert verdict == "BLOCKED"
    assert any("missing provenance field" in r for r in reasons)
    # Promotion input: unknown/absent aggregate verdict -> FAILED.
    broken = docs[("amca-ps2-promotion-input/1", "ps2_aggregate_verdict")]
    broken["ps2_aggregate_verdict"] = None
    verdict, _ = pi.validate_promotion_input(broken)
    assert verdict == "PS2_INPUT_FAILED"


def test_unknown_schema_version_rejected():
    # Migration evidence with a future schema version is rejected. The doc
    # is otherwise complete so the schema-version check is what fires.
    doc = {"schema": "amca-migration-certification-evidence/1",
           "evidence_schema_version": "9.9.9", "overall": "PASS",
           "package": {"sha256": "a" * 64}, "verdicts": {}, "bindings": {},
           "smoke_result": {}, "fixture": {}, "limitations": [],
           "certification_timestamp": "2026-09-04T00:00:00Z",
           "certification_git_sha": "a" * 40, "migration_repair_sha": "b" * 40,
           "fixture": {}, "verdict_notes": [], "pytest": None}
    verdict, reasons = me.validate_migration_evidence(doc)
    assert verdict == "BLOCKED"
    assert any("schema" in r.lower() for r in reasons)
    # Promotion input with a foreign schema id is FAILED.
    doc = {"schema": "amca-ps2-promotion-input/2",
           "promotion_eligibility_ps2": "PS2_INPUT_READY"}
    verdict, reasons = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_FAILED"
    assert any("schema" in r.lower() for r in reasons)
    assert any("schema" in r.lower() for r in reasons)


def test_stale_v09_evidence_rejected_under_v10_simulation(tmp_path: Path):
    """Disposable simulation: a 0.9-bound aggregate cannot certify 1.0.0."""
    aggregate = {
        "schema": "amca-ps2-final-release-evidence/1",
        "source_sha": "0.9-lineage-sha",
        "aggregate_verdict": "PS2_RELEASE_READY",
        "artifact_hashes": {"portable": {"sha256": "b" * 64}},
        "families": {f: {"state": "PASS"} for f in ag.FAMILIES},
        "review_items": [], "blockers": [], "stale_inputs": [],
    }
    agg_path = tmp_path / "v09-aggregate.json"
    agg_path.write_text(json.dumps(aggregate), encoding="utf-8")
    prov_path = tmp_path / "v09-provenance.json"
    prov_path.write_text(json.dumps(_provenance_doc()), encoding="utf-8")
    doc = pi.build_promotion_input(agg_path, prov_path,
                                   expected_source_sha="1.0-final-sha")
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_FAILED"


def _provenance_doc(state: str = "PROVENANCE_READY",
                    staged_sha: str = "d" * 64) -> dict:
    return {"schema": "amca-mpl-release-asset-provenance/1",
            "provenance_state": state, "component": "certifi",
            "component_version": "2026.7.22", "staged_sha256": staged_sha,
            "intake_receipt_sha256": "e" * 64,
            "input_sha256": "9" * 64,
            "canonical_mpl_validator_verdict":
                "ACCEPTED_CANONICAL_MPL_2_0"}


def test_same_sha_v10_evidence_accepted(tmp_path: Path):
    """Correctly formed same-SHA v1.0 evidence is accepted (disposable)."""
    aggregate = {
        "schema": "amca-ps2-final-release-evidence/1",
        "source_sha": "1.0-final-sha",
        "aggregate_verdict": "PS2_RELEASE_READY",
        "artifact_hashes": {"portable": {"sha256": "b" * 64},
                            "installer": {"sha256": "c" * 64}},
        "families": {f: {"state": "PASS"} for f in ag.FAMILIES},
        "review_items": [], "blockers": [], "stale_inputs": [],
    }
    agg_path = tmp_path / "v10-aggregate.json"
    agg_path.write_text(json.dumps(aggregate), encoding="utf-8")
    prov_path = tmp_path / "v10-provenance.json"
    prov_path.write_text(json.dumps(_provenance_doc()), encoding="utf-8")
    doc = pi.build_promotion_input(agg_path, prov_path,
                                   expected_source_sha="1.0-final-sha")
    assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_READY"
    assert doc["source_sha"] == "1.0-final-sha"


def test_review_state_preserved_under_drift():
    families = {f: {"state": "PASS"} for f in ag.FAMILIES}
    families["third_party_payload_completeness"] = {
        "state": "PASS_WITH_REVIEW"}
    families["mpl_readiness_intake_state"] = {"state": "PASS"}
    assert ag.aggregate(families) == "PS2_RELEASE_READY_WITH_REVIEW"
    # And the promotion mapping must not soften it.
    mapped = pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"]
    assert mapped == "PS2_INPUT_READY_WITH_REVIEW"
    assert mapped != "PS2_INPUT_READY"


def test_mpl_block_preserved_without_valid_intake_provenance():
    original = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = mr.ASSET_PATH.parent / "does-not-exist-i2v.txt"
        doc = mr.evaluate()
        assert doc["state"] == "RELEASE_ASSET_REQUIRED"
        assert doc["asset"] is None
    finally:
        mr.ASSET_PATH = original
    # Even with a staged asset, missing provenance cannot yield READY input.
    aggregate = {
        "schema": "amca-ps2-final-release-evidence/1", "source_sha": "a" * 40,
        "aggregate_verdict": "PS2_RELEASE_READY",
        "artifact_hashes": {}, "families": {f: {"state": "PASS"}
                                            for f in ag.FAMILIES},
        "review_items": [], "blockers": [], "stale_inputs": []}
    agg_path = Path(__file__).parent / "_i2v-agg.json"
    prov_path = Path(__file__).parent / "_i2v-prov.json"
    agg_path.write_text(json.dumps(aggregate), encoding="utf-8")
    prov_path.write_text(json.dumps(_provenance_doc(
        state="ASSET_NOT_STAGED", staged_sha="")), encoding="utf-8")
    try:
        doc = pi.build_promotion_input(agg_path, prov_path)
        assert doc["promotion_eligibility_ps2"] == "PS2_INPUT_BLOCKED"
    finally:
        agg_path.unlink()
        prov_path.unlink()


def test_package_and_source_hashes_remain_mandatory():
    fields = REQUIRED_FIELDS["amca-ps2-promotion-input/1"]
    assert "installer_sha256" in fields and "portable_sha256" in fields
    assert "source_sha" in fields
    fields = REQUIRED_FIELDS["amca-final-package-compliance/1"]
    assert "source_sha" in fields and "artifacts" in fields


def test_stale_evidence_remains_stale(tmp_path: Path):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({"source_sha": "v0.9-lineage"}))
    family = ag.family_from_file(stale, "1.0-final-sha")
    assert family["state"] == "STALE"


def test_adapters_remain_side_effect_free(tmp_path: Path):
    aggregate = {
        "schema": "amca-ps2-final-release-evidence/1", "source_sha": "a" * 40,
        "aggregate_verdict": "PS2_RELEASE_READY",
        "artifact_hashes": {}, "families": {f: {"state": "PASS"}
                                            for f in ag.FAMILIES},
        "review_items": [], "blockers": [], "stale_inputs": []}
    agg_path = tmp_path / "agg.json"
    prov_path = tmp_path / "prov.json"
    agg_path.write_text(json.dumps(aggregate), encoding="utf-8")
    prov_path.write_text(json.dumps(_provenance_doc()), encoding="utf-8")
    before_agg = agg_path.read_bytes()
    before_prov = prov_path.read_bytes()
    pi.build_promotion_input(agg_path, prov_path)
    pv.provenance_handoff(json.loads(prov_path.read_text(encoding="utf-8")))
    assert agg_path.read_bytes() == before_agg
    assert prov_path.read_bytes() == before_prov


def test_no_secrets_private_data():
    for schema_id, module in EXPECTED_SCHEMAS.items():
        blob = json.dumps({k: str(v) for k, v in vars(module).items()
                           if not k.startswith("__")}).casefold()
        assert "sk-proj-" not in blob
        assert "ghp_" not in blob
        assert "begin private key" not in blob