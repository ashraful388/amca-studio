"""Final validation command manifest tests (I3A).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I3A-FINAL-BUILD-VALIDATION-RUNBOOK-AND-MACHINE-READABLE-COMMAND-MANIFEST-01
Worker: OpenCode PS-2. Tests only. Verifies the dependency-ordered manifest
references existing tools and commands, preserves the required flow and
critical rules, and emits no secrets.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parent.parent / "docs" / "release"
    / "AMCA-Studio-v1.0.0-PS2-Final-Validation-Command-Manifest.json")
RUNBOOK = (
    Path(__file__).resolve().parent.parent / "docs" / "release"
    / "AMCA-Studio-v1.0.0-PS2-Final-Build-Validation-Runbook.md")
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _manifest() -> dict:
    assert MANIFEST.is_file()
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_schema_and_steps():
    manifest = _manifest()
    assert manifest["schema"] == "amca-ps2-final-validation-command-manifest/1"
    assert len(manifest["steps"]) >= 8
    sequences = [s["sequence"] for s in manifest["steps"]]
    assert sequences == sorted(sequences)  # dependency-ordered


def test_commands_reference_existing_tools():
    manifest = _manifest()
    for step in manifest["steps"]:
        tool = step.get("tool", "")
        if tool in ("caller-supplied artifacts",):
            continue
        script = SCRIPTS / tool
        assert script.is_file(), f"manifest references missing tool: {tool}"
        for chained in step.get("tool_chain", []):
            assert (SCRIPTS / chained).is_file(), \
                f"manifest references missing chained tool: {chained}"


def test_dependency_chain_captured():
    manifest = _manifest()
    steps = {s["sequence"]: s for s in manifest["steps"]}
    # I2Y acceptance first, no prerequisites.
    assert steps[1]["tool"] == "run_ps2_post_transition_acceptance.py"
    assert steps[1]["prerequisites"] == []
    # Final-package compliance requires the build (step 2).
    compliance = next(s for s in manifest["steps"]
                      if s["tool"] == "run_final_package_compliance_verification.py")
    assert 2 in compliance["prerequisites"]
    # Aggregation (I2R) requires MPL intake/provenance (3), compliance (4),
    # sanitation (5).
    aggregate = next(s for s in manifest["steps"]
                     if s["tool"] == "run_ps2_final_release_validation.py")
    assert set(aggregate["prerequisites"]) >= {3, 4, 5}
    # Promotion input (I2U) requires the aggregate (6).
    promo = next(s for s in manifest["steps"]
                 if s["tool"] == "build_ps2_promotion_input.py")
    assert aggregate["sequence"] in promo["prerequisites"]
    # Shipping docs (I2S) come only after promotion evidence / certification.
    export = next(s for s in manifest["steps"]
                  if s["tool"] == "export_final_release_documentation.py")
    assert promo["sequence"] in export["prerequisites"]


def test_expected_evidence_schemas_present():
    manifest = _manifest()
    schemas = {s["evidence_schema"] for s in manifest["steps"]
               if s.get("evidence_schema")}
    for expected in ("amca-ps2-post-transition-acceptance/1",
                     "amca-mpl-asset-intake-receipt/1",
                     "amca-mpl-release-asset-provenance/1",
                     "amca-final-package-compliance/1",
                     "amca-release-sanitation-audit/1",
                     "amca-dependency-compliance-audit/1",
                     "amca-ps2-final-release-evidence/1",
                     "amca-ps2-promotion-input/1",
                     "amca-final-release-documentation-export/1"):
        assert any(expected in s for s in schemas), f"missing: {expected}"


def test_external_mpl_branch_represented():
    manifest = _manifest()
    branch = manifest["external_mpl_branch"]
    assert "asset_available" in branch and "asset_unavailable" in branch
    assert "do not fabricate" in json.dumps(manifest).casefold() or \
        "Do not fabricate the asset" in manifest["external_mpl_branch"][
            "asset_unavailable"] or \
        "never" in json.dumps(manifest).casefold()


def test_critical_rules_enforced():
    manifest = _manifest()
    rules = " ".join(manifest["critical_rules"]).casefold()
    for rule in ("v0.9 package fallback", "worker-specific paths",
                 "final-package evidence", "final artifact hashes",
                 "shipping-document finalization", "pass_with_review",
                 "mpl provenance"):
        assert rule in rules, f"critical rule missing: {rule}"


def test_explicit_artifact_paths_required():
    manifest = _manifest()
    for step in manifest["steps"]:
        if step["phase"] == "post-build-validation" or \
                step["sequence"] in (6, 7):
            blob = json.dumps(step).casefold()
            assert "explicit" in blob or "caller-supplied" in blob, \
                f"step {step['sequence']} lacks explicit-path requirement"


def test_pass_with_review_semantics_preserved():
    manifest = _manifest()
    blob = json.dumps(manifest).casefold()
    assert "ps2_input_ready_with_review" in blob
    assert "never" in blob or "must never" in blob


def test_read_only_flags_recorded():
    manifest = _manifest()
    for step in manifest["steps"]:
        assert isinstance(step["read_only"], bool)
    export = next(s for s in manifest["steps"]
                  if s["tool"] == "export_final_release_documentation.py")
    assert export["read_only"] is False  # it writes to the staging dir
    acceptance = manifest["steps"][0]
    assert acceptance["read_only"] is True


def test_runbook_present_and_operational():
    assert RUNBOOK.is_file()
    text = RUNBOOK.read_text(encoding="utf-8")
    for marker in ("Phase A", "run_ps2_post_transition_acceptance.py",
                   "build_ps2_promotion_input.py",
                   "export_final_release_documentation.py",
                   "EXTERNAL_ASSET_PENDING"):
        assert marker in text, f"runbook missing: {marker}"


def test_no_secrets_private_data():
    blob = json.dumps(_manifest()).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "begin private key" not in blob