"""v1.0.1 package validation prep tests (H1B).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1B-PACKAGE-RUNTIME-READINESS-PREP-01
Worker: OpenCode PS-2. Tests only; plan/contract prep — no packaging,
rebuild, promotion, or tagging is authorized or performed here.

Verifies the prepared v1.0.1 validation contract: schema, plan-only
authorization, all three artifact kinds (EXE/MSI/Portable), MSI contract
defined, referenced tools exist, dependency ordering, lineage rules (v1.0.0
evidence historical), model-health + physical-acceptance steps, return
contract, and no forbidden claims.
"""

from __future__ import annotations

import json
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = SOFTWARE_ROOT / "docs" / "release"
SCRIPTS = SOFTWARE_ROOT / "scripts"

MANIFEST = RELEASE_DIR / (
    "AMCA-Studio-v1.0.1-Package-Validation-Command-Manifest.json")
PLAN = RELEASE_DIR / "AMCA-Studio-v1.0.1-Package-Validation-Plan.md"

V100_INSTALLER_SHA = (
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1")
V100_PORTABLE_SHA = (
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454")
V100_SOURCE_SHA = "3d225e31dfc409521a7dc2259f52489754efbbfc"


def _manifest() -> dict:
    assert MANIFEST.is_file(), "v1.0.1 command manifest missing"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_schema_and_plan_only_authorization():
    m = _manifest()
    assert m["schema"] == "amca-v101-package-validation-command-manifest/1"
    assert m["authorization_state"] == "PLAN_ONLY_NOT_AUTHORIZED"
    assert m["target_version"] == "1.0.1"


def test_all_three_artifact_kinds_covered():
    m = _manifest()
    kinds = {a["kind"] for a in m["future_artifact_set"]}
    assert kinds == {"installer_exe", "installer_msi", "portable_zip"}
    names = {a["name_pattern"] for a in m["future_artifact_set"]}
    assert any("setup.exe" in n for n in names)
    assert any(n.endswith(".msi") for n in names)
    assert any("portable.zip" in n for n in names)


def test_msi_contract_defined():
    m = _manifest()
    msi = next(a for a in m["future_artifact_set"]
               if a["kind"] == "installer_msi")
    blob = (msi["contract"] + " " + msi["validator"]).casefold()
    for element in ("msiexec", "/qn", "uninstall", "blocked", "payload"):
        assert element in blob, f"MSI contract missing: {element}"


def test_referenced_tools_exist():
    m = _manifest()
    for step in m["steps"]:
        tool = step.get("tool", "")
        for token in tool.replace(" + ", " ").split():
            if token.endswith(".py"):
                assert (SCRIPTS / token).is_file(), \
                    f"step {step['sequence']} references missing tool: {token}"


def test_dependency_ordering_and_prerequisites():
    m = _manifest()
    seqs = [s["sequence"] for s in m["steps"]]
    assert seqs == sorted(seqs) and seqs[0] == 1
    steps = {s["sequence"]: s for s in m["steps"]}
    for seq, step in steps.items():
        for pre in step["prerequisites"]:
            assert pre < seq, f"step {seq} has non-earlier prerequisite {pre}"
    # Promotion input strictly after aggregation; docs after promotion input.
    assert steps[11]["prerequisites"] == [10]
    assert steps[12]["prerequisites"] == [11]


def test_lineage_rules_v100_historical():
    m = _manifest()
    blob = json.dumps(m["lineage_rules"])
    assert V100_SOURCE_SHA in blob
    assert V100_INSTALLER_SHA in blob
    assert V100_PORTABLE_SHA in blob
    joined = " ".join(m["lineage_rules"]).casefold()
    assert "stale" in joined and "never" in joined


def test_model_health_and_physical_acceptance_present():
    m = _manifest()
    steps = {s["sequence"]: s for s in m["steps"]}
    health = json.dumps(steps[6]).casefold()
    for state in ("ready", "auth_required", "model_not_found",
                  "provider_unavailable", "local_runtime_offline"):
        assert state in health, f"model health state missing: {state}"
    physical = json.dumps(steps[7])
    assert "MODEL_RESPONSE_RECEIVED=YES" in physical
    assert "LOCAL_MODEL_INSTALL_REQUIRED" in physical
    assert "mocked" in physical.casefold()


def test_upgrade_path_includes_v100_to_v101():
    m = _manifest()
    steps = {s["sequence"]: s for s in m["steps"]}
    upgrade = json.dumps(steps[5]).casefold()
    assert "v1.0.0" in upgrade and "v1.0.1" in upgrade
    assert "pre-c1" in upgrade or "v0.9" in upgrade


def test_return_contract_fields():
    m = _manifest()
    fields = " ".join(m["return_contract"]["fields"])
    for name in ("V101_PACKAGE_VALIDATION", "MODEL_HEALTH_GATE",
                 "PHYSICAL_USE_TEST_V101", "V101_PROMOTION_INPUT"):
        assert name in fields, f"return field missing: {name}"


def test_plan_doc_present_and_consistent():
    assert PLAN.is_file()
    text = " ".join(PLAN.read_text(encoding="utf-8").split())
    assert "does not authorize packaging" in text
    assert "msiexec" in text
    assert V100_SOURCE_SHA in text
    assert "MODEL_RESPONSE_RECEIVED=YES" in text
    assert "never silently install" in text


def test_no_forbidden_authorization_or_claims():
    m = _manifest()
    blob = json.dumps(m).casefold()
    assert "authorized_to_package" not in blob
    assert "promotion authorized" not in blob
    assert "legal_approval" not in blob
    assert "released" not in blob or "historical" in blob