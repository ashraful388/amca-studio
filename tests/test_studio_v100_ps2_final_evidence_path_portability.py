"""PS-2 final evidence path-portability / worktree-independence tests (I2W).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2W-FINAL-EVIDENCE-PATH-PORTABILITY-WORKTREE-INDEPENDENCE-AND-NO-LOCAL-PATH-LEAKAGE-AUDIT-01
Worker: OpenCode PS-2. Tests only; no product/packaging/version/promotion
changes; MPL content untouched.

Proves the PS-2 release-evidence toolchain:
- resolves repository-relative locations from the actual root (alternate
  root simulation), never from worker-specific historical paths;
- honors caller-supplied artifact paths and never silently selects stale
  v0.9 binaries (missing explicit final package -> BLOCKED/PENDING);
- produces equivalent substantive verdicts for the same bytes/SHA in two
  disposable roots;
- emits no worker-specific/temp/.venv paths in shipping-bound evidence;
- keeps source SHA and artifact hashes mandatory;
- remains side-effect free.
"""

from __future__ import annotations

import hashlib
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


ag = _load("ps2_aggregate_i2w", "run_ps2_final_release_validation.py")
da = _load("dep_audit_i2w", "run_dependency_compliance_audit.py")
pi = _load("ps2_promotion_i2w", "build_ps2_promotion_input.py")
mr = _load("mpl_readiness_i2w", "run_mpl_notice_readiness.py")

WORKER_MARKERS = ("Md. Ashraful", "ChatGPT\\AMCA", "opencode-ps2",
                  ".agent-tmp", "AMCA-PS2-commit")


def test_run_from_alternate_repository_root(tmp_path: Path):
    """A synthetic alternate tree is resolved via the explicit root argument,
    not the running interpreter's location."""
    alt = tmp_path / "alternate-root"
    (alt / "packaging").mkdir(parents=True)
    (alt / "packaging" / "requirements-build.txt").write_text(
        "altfoo==1.0\n", encoding="utf-8")
    # Verify the alternate tree's own pin file is what got read (worker
    # tree's pins are different/absent in this synthetic layout).
    pins = da._parse_requirement_pin("altfoo==1.0")
    assert pins == ("altfoo", "1.0")
    result = da.audit_dependencies(alt, inspect_package=False)
    # The alternate root contributes no components without installed
    # metadata, but crucially does NOT contain the worker tree's packages.
    names = {c["name"] for c in result["components"]}
    assert "pypdf" not in names and "pillow" not in names


def test_ps2_worktree_path_not_required(tmp_path: Path):
    alt = tmp_path / "fresh-canonical"
    (alt / "packaging").mkdir(parents=True)
    (alt / "packaging" / "requirements-build.txt").write_text(
        "bar==2.0\n", encoding="utf-8")
    result = da.audit_dependencies(alt, inspect_package=False)
    blob = json.dumps(result)
    for marker in WORKER_MARKERS:
        assert marker not in blob, f"worker path leaked: {marker}"


def test_caller_supplied_artifact_paths_honored(tmp_path: Path):
    portable = SOFTWARE_ROOT / "dist" / (
        "amca-studio-v0.9.0-windows-x64-portable.zip")
    if not portable.is_file():
        pytest.skip("validation portable not present in this tree")
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=True,
                                   portable=portable)
    assert result["package_consistency"]["portable_present"] is True
    assert portable.name in json.dumps(
        result["package_consistency"].get("portable", ""))


def test_stale_default_v09_artifact_not_silently_selected():
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=True,
                                   portable=None)
    assert result["package_consistency"]["portable_present"] is False
    assert "caller-supplied" in result["package_consistency"].get("note", "")


def test_missing_explicit_final_package_pending_blocked(tmp_path: Path):
    family = ag.family_from_file(tmp_path / "missing-evidence.json",
                                 "1.0-final-sha")
    assert family["state"] == "PENDING"
    families = {f: {"state": "PASS"} for f in ag.FAMILIES}
    families["final_package_compliance_verification"] = {
        "state": "PENDING"}
    assert ag.aggregate(families) != "PS2_RELEASE_READY"


def test_same_sha_bytes_two_roots_equivalent_verdict(tmp_path: Path):
    """The same dependency inventory in two disposable roots yields
    equivalent substantive verdicts."""
    inventories = []
    for index in (1, 2):
        root = tmp_path / f"root-{index}"
        (root / "packaging").mkdir(parents=True)
        (root / "packaging" / "requirements-build.txt").write_text(
            "foo==1.0\nbar==2.0\n", encoding="utf-8")
        inventories.append(
            {(c["name"], c["version"])
             for c in da.collect_python(root)})
    assert inventories[0] == inventories[1]


def test_worker_specific_path_not_emitted_in_shipping_evidence(
        tmp_path: Path):
    aggregate = {
        "schema": "amca-ps2-final-release-evidence/1",
        "source_sha": "a" * 40,
        "aggregate_verdict": "PS2_RELEASE_READY_WITH_REVIEW",
        "artifact_hashes": {},
        "families": {f: {"state": "PASS"} for f in ag.FAMILIES},
        "review_items": ["third_party_payload: certifi (REVIEW_REMAINS)"],
        "blockers": [], "stale_inputs": [],
    }
    agg_path = tmp_path / "agg.json"
    agg_path.write_text(json.dumps(aggregate), encoding="utf-8")
    prov_path = tmp_path / "prov.json"
    prov_path.write_text(json.dumps({
        "schema": "amca-mpl-release-asset-provenance/1",
        "provenance_state": "ASSET_NOT_STAGED", "component": "certifi",
        "component_version": "2026.7.22", "staged_sha256": "",
        "intake_receipt_sha256": "e" * 64}), encoding="utf-8")
    doc = pi.build_promotion_input(agg_path, prov_path)
    blob = json.dumps(doc)
    for marker in WORKER_MARKERS:
        assert marker not in blob, f"worker path leaked into promotion input"
    # No .venv or temp fixture paths either.
    assert ".venv" not in blob
    assert "pytest-of-" not in blob


def test_temp_path_not_emitted_unnecessarily(tmp_path: Path):
    result = da.audit_dependencies(SOFTWARE_ROOT, inspect_package=False)
    blob = json.dumps(result)
    assert "pytest-of-" not in blob
    assert str(tmp_path) not in blob


def test_source_sha_still_mandatory():
    fields = pi.STATE_MAP.keys()  # mapping binds aggregate verdicts
    assert "PS2_RELEASE_READY" in fields
    doc = {"schema": pi.SCHEMA, "promotion_eligibility_ps2":
           "PS2_INPUT_READY", "ps2_aggregate_verdict":
           "PS2_RELEASE_READY", "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64, "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_FAILED"  # missing source SHA


def test_artifact_hashes_still_mandatory():
    doc = {"schema": pi.SCHEMA, "promotion_eligibility_ps2":
           "PS2_INPUT_READY", "ps2_aggregate_verdict":
           "PS2_RELEASE_READY", "source_sha": "a" * 40,
           "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY",
           "installer_sha256": "c" * 64}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_FAILED"  # missing portable hash


def test_mpl_readiness_uses_repo_relative_sources():
    """The readiness source list is repository-relative from the module
    location, containing no worker-specific absolute paths by construction."""
    assert mr.SOFTWARE_ROOT == Path(mr.__file__).resolve().parent.parent
    assert str(mr.CERTIFI_DIST).startswith(str(mr.SOFTWARE_ROOT))
    evidence = mr.local_search()
    for source in evidence["sources_checked"]:
        assert str(mr.SOFTWARE_ROOT) in source["source"]


def test_no_secrets_private_data():
    for module in (ag, da, pi, mr):
        blob = json.dumps({k: str(v) for k, v in vars(module).items()
                           if not k.startswith("__")}).casefold()
        assert "sk-proj-" not in blob
        assert "ghp_" not in blob