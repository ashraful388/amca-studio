"""Dependency license/SBOM/distribution compliance tests (I2L).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2L-THIRD-PARTY-DEPENDENCY-LICENSE-SBOM-AND-DISTRIBUTION-COMPLIANCE-AUDIT-01
Worker: OpenCode PS-2. Tests only; no production/dependency changes.

Covers: inventory generation, build/runtime classification, UNKNOWN
governance (never fabricated, never silently safe), attribution requirement
detection, SBOM schema validity, duplicate handling, package/runtime
evidence binding, blocking findings preventing PASS, dev/test-only scoping
honesty, and no secret/user-data emission.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "run_dependency_compliance_audit.py"
)
_spec = importlib.util.spec_from_file_location("dependency_audit", SCRIPT)
assert _spec is not None and _spec.loader is not None
da = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = da
_spec.loader.exec_module(da)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent


def _component(name, **kwargs):
    base = {"name": name, "version": "1.0", "ecosystem": "python",
            "scope": "runtime", "license": "MIT",
            "license_source": "test", "in_distributed_artifact": True,
            "source_ref": "test", "notes": []}
    base.update(kwargs)
    return da.Component(**base)


def test_dependency_inventory_generated():
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=False)
    assert len(result["components"]) >= 60
    names = {c["name"].lower() for c in result["components"]}
    for expected in ("pypdf", "react", "tauri", "openai"):
        assert expected in names, f"missing inventoried dep: {expected}"


def test_build_runtime_classification_preserved():
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=False)
    by_name = {c["name"].lower(): c for c in result["components"]}
    assert by_name["pyinstaller"]["scope"] == "build-only"
    assert by_name["pyinstaller"]["in_distributed_artifact"] is False
    assert by_name["pypdf"]["scope"] == "runtime"
    assert by_name["react"]["scope"] == "runtime"


def test_unknown_licenses_remain_unknown():
    assert da.normalize_license("") == "UNKNOWN"
    assert da.normalize_license("Some Custom EULA Text Here") == "UNKNOWN"
    comp = _component("mystery", license="UNKNOWN",
                      in_distributed_artifact=True)
    finding = da.classify_component(comp)
    assert finding.severity == "REVIEW"
    comp2 = _component("mystery-dev", license="UNKNOWN",
                       in_distributed_artifact=False, scope="dev-only")
    assert da.classify_component(comp2).severity == "UNKNOWN"


def test_license_identifier_never_fabricated():
    for raw in ("", "Proprietary — contact vendor", "SEE LICENSE IN EULA.txt"):
        assert da.normalize_license(raw) == "UNKNOWN"
    assert da.normalize_license("MIT") == "MIT"
    assert da.normalize_license("Apache License, Version 2.0") == "Apache-2.0"
    assert da.normalize_license("MIT OR Apache-2.0") == "Apache-2.0+MIT"


def test_missing_attribution_requirement_detected():
    comp = _component("lib", license="Apache-2.0")
    finding = da.classify_component(comp)
    assert finding.severity == "PASS"
    assert "attribution" in finding.obligations
    assert "license text" in finding.obligations


def test_sbom_json_schema_valid(tmp_path: Path):
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=False)
    sbom_path = da.emit_sbom(result, tmp_path / "sbom.json")
    doc = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert doc["sbom_schema"] == "amca-sbom-candidate/1"
    assert len(doc["components"]) == len(result["components"])
    for entry in doc["components"]:
        for key in ("name", "version", "ecosystem", "classification",
                    "license", "license_source", "in_distributed_artifact",
                    "source_ref"):
            assert key in entry, f"SBOM entry missing: {key}"


def test_duplicate_dependency_version_handling(tmp_path: Path):
    root = tmp_path / "tree"
    venv = root / ".venv" / "Lib" / "site-packages"
    venv.mkdir(parents=True)
    for dist, meta in (
        ("foo-1.0.dist-info", "Name: foo\nVersion: 1.0\nLicense: MIT\n"),
        ("foo-2.0.dist-info", "Name: foo\nVersion: 2.0\nLicense: MIT\n"),
    ):
        (venv / dist).mkdir()
        (venv / dist / "METADATA").write_text(meta, encoding="utf-8")
    (root / "packaging").mkdir()
    (root / "packaging" / "requirements-build.txt").write_text(
        "foo==2.0\n", encoding="utf-8")
    comps = da.collect_python(root)
    foos = [c for c in comps if c.name == "foo"]
    assert len(foos) == 2  # both recorded; audit merges deterministically
    result = da.audit_dependencies(root, inspect_package=False)
    names = [c["name"] for c in result["components"] if c["name"] == "foo"]
    assert len(names) == 1


def test_package_runtime_evidence_binding():
    """Package consistency requires a caller-supplied portable (I2W rule:
    no stale default v0.9 artifact is discovered silently)."""
    portable = SOFTWARE_ROOT / "dist" / (
        "amca-studio-v0.9.0-windows-x64-portable.zip")
    if not portable.is_file():
        pytest.skip("validation portable not present in this tree")
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=True,
                                   portable=portable)
    pkg = result["package_consistency"]
    assert pkg.get("portable_present") is True
    assert pkg.get("member_count", 0) > 0
    assert len(pkg.get("opaque_binaries", [])) >= 1
    # Without a caller-supplied package, consistency is not evaluated and
    # no stale binary is silently selected.
    none_result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=True,
                                        portable=None)
    assert none_result["package_consistency"]["portable_present"] is False
    assert "caller-supplied" in \
        none_result["package_consistency"].get("note", "")


def test_blocking_finding_prevents_overall_pass():
    comp = _component("gpllib", license="GPL-3.0-only",
                      in_distributed_artifact=True)
    finding = da.classify_component(comp)
    assert finding.severity == "BLOCKING"
    assert "manual legal review" in finding.obligations


def test_dev_only_dependencies_not_falsely_marked_shipped():
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=False)
    devs = [c for c in result["components"] if c["scope"] == "dev-only"]
    assert devs, "expected dev-only components in inventory"
    for comp in devs:
        assert comp["in_distributed_artifact"] is not True


def test_no_secrets_user_data_emitted(tmp_path: Path):
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=False)
    out = tmp_path / "result.json"
    out.write_text(json.dumps(result), encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    da.emit_sbom(result, sbom)
    notices = tmp_path / "notices.md"
    da.emit_notices(result, notices)
    for path in (out, sbom, notices):
        blob = path.read_text(encoding="utf-8")
        assert "BEGIN PRIVATE KEY" not in blob
        lowered = blob.casefold()
        assert "sk-proj-" not in lowered
    assert "ghp_" not in out.read_text(encoding="utf-8")
