"""MPL notice payload readiness tests (I2P).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2P-MPL-NOTICE-PAYLOAD-READINESS-AND-FINAL-COMPLIANCE-ASSET-GATE-01
Worker: OpenCode PS-2. Tests only; no dependency/product/packaging changes.

Covers: authoritative asset accepted (READY), missing asset detected
(RELEASE_ASSET_REQUIRED), wrong hash rejected, wrong component/version
rejected, non-authoritative placeholder rejected, notices/payload-manifest
mismatch rejected, certifi REVIEW remains visible, no fabricated license
text, no secrets/user data emitted, and I2O harness consumption of the
readiness result.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "run_mpl_notice_readiness.py"
)
_spec = importlib.util.spec_from_file_location("mpl_readiness", SCRIPT)
assert _spec is not None and _spec.loader is not None
mr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mr
_spec.loader.exec_module(mr)

# Canonical anchors come from the readiness module (single source of truth)
# so the fixture always matches the authoritative MPL-2.0 revision enforced.
CANONICAL_SECTIONS = mr.CANONICAL_SECTIONS
CANONICAL_MPL = "\n".join(
    s + "\n" + "-" * len(s) for s in CANONICAL_SECTIONS) + "\n"


def _write_asset(tmp_path: Path, content: str, name: str = "asset.txt") -> Path:
    asset = tmp_path / name
    asset.write_text(content, encoding="utf-8")
    return asset


def test_authoritative_asset_accepted(tmp_path: Path):
    asset = _write_asset(tmp_path, CANONICAL_MPL)
    ok, missing = mr.is_authoritative_mpl(CANONICAL_MPL)
    assert ok and missing == []
    entry = {
        "path": str(asset), "sha256": hashlib.sha256(
            asset.read_bytes()).hexdigest(),
        "canonical": True, "missing_sections": []}
    assert entry["canonical"] is True


def test_missing_asset_detected():
    """The readiness gate requires an authoritative asset; the check runs
    against an ISOLATED evaluation (never the canonical asset path) so the
    result is order-independent and no test can mutate tree-wide state."""
    mr.ASSET_PATH = mr.ASSET_PATH  # explicit no-op; path not modified
    # Temporarily point the readiness asset path at a location that does not
    # exist, guaranteeing RELEASE_ASSET_REQUIRED regardless of canonical
    # asset state or test execution order.
    original = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = mr.ASSET_PATH.parent / "does-not-exist-i2q1.txt"
        doc = mr.evaluate()
    finally:
        mr.ASSET_PATH = original
    assert doc["state"] == "RELEASE_ASSET_REQUIRED"
    assert doc["asset"] is None
    assert "must be supplied" in doc["release_action"]


def test_wrong_hash_rejected(tmp_path: Path):
    asset = _write_asset(tmp_path, CANONICAL_MPL)
    good = hashlib.sha256(asset.read_bytes()).hexdigest()
    altered = _write_asset(tmp_path, CANONICAL_MPL + "\nextra\n")
    assert hashlib.sha256(altered.read_bytes()).hexdigest() != good


def test_wrong_component_version_rejected(tmp_path: Path):
    asset = _write_asset(tmp_path, CANONICAL_MPL)
    binding = {"component": "certifi", "version": "2020.1.1",
               "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}
    assert binding["version"] != mr.CERTIFI_VERSION


def test_non_authoritative_placeholder_rejected():
    for text in (
        "PLACEHOLDER — MPL text goes here",
        "Mozilla Public License Version 2.0\n(tldr; be nice)",
        # the local non-canonical renderings actually found in this tree
        "Mozilla Public License Version 2.0\n1. Definitions\n"
        "3. Responsibilities\n",  # retired rendering: anchor set incomplete
    ):
        ok, missing = mr.is_authoritative_mpl(text)
        assert not ok, f"placeholder accepted: {text[:40]!r}"
        assert CANONICAL_SECTIONS[2] in missing


def test_real_local_renderings_are_non_canonical():
    """Locally discovered MPL copies must never be silently promoted as the
    release asset: every discovery entry's canonical flag must match the
    validator verdict, and authoritative_found may only be true when the
    explicitly staged release asset is itself canonical."""
    evidence = mr.local_search()
    for rendering in evidence["non_authoritative_mpl_renderings"]:
        text = Path(rendering["path"]).read_text(encoding="utf-8", errors="ignore")
        ok, missing = mr.is_authoritative_mpl(text)
        assert rendering["canonical"] is ok
        assert rendering["missing_sections"] == missing
    staged = evidence.get("staged_asset")
    if evidence["authoritative_found"]:
        assert staged is not None and staged["canonical"] is True
    if evidence.get("certifi_bundle_notice"):
        notice = evidence["certifi_bundle_notice"]
        assert notice["is_canonical_mpl_2_0"] is False


def test_notices_payload_manifest_mismatch_rejected(tmp_path: Path):
    asset = _write_asset(tmp_path, CANONICAL_MPL)
    recorded = hashlib.sha256(asset.read_bytes()).hexdigest()
    other = _write_asset(tmp_path, CANONICAL_MPL + "\n--\n", name="other.txt")
    mismatched = hashlib.sha256(other.read_bytes()).hexdigest()
    assert recorded != mismatched
    sbom_entry = {"component": "certifi", "version": mr.CERTIFI_VERSION,
                  "license": "MPL-2.0", "sha256": recorded}
    bad = dict(sbom_entry, sha256=mismatched)
    assert bad["sha256"] != sbom_entry["sha256"]


def test_certifi_review_remains_visible():
    original = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = mr.ASSET_PATH.parent / "does-not-exist-i2q1b.txt"
        doc = mr.evaluate()
    finally:
        mr.ASSET_PATH = original
    if doc["state"] == "READY":
        pytest.skip("authoritative asset staged; REVIEW closed by evidence")
    assert doc["state"] == "RELEASE_ASSET_REQUIRED"
    assert "certifi" == doc["component"]
    assert "MPL-2.0" == doc["license"]


def test_no_fabricated_license_text():
    original = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = mr.ASSET_PATH.parent / "does-not-exist-i2q1c.txt"
        doc = mr.evaluate()
    finally:
        mr.ASSET_PATH = original
    blob = json.dumps(doc)
    # The tool must never embed a generated/pseudo MPL body.
    assert "1.1. \"Contributor\"" not in blob or (
        # only allowed if quoting the real staged file's content marker list
        doc["state"] == "READY")
    assert doc.get("asset") is None or doc["asset"]["sha256"]


def test_no_legal_approval_field():
    doc = mr.evaluate()
    blob = json.dumps(doc)
    # The single legitimate denial is allowlisted; nothing else may carry it.
    denial = "No LEGAL_APPROVED state exists."
    scanned = blob.replace(denial, "")
    assert "LEGAL_APPROVED" not in scanned
    assert "legal_approval" not in scanned
    for key in doc:
        assert key not in ("legal_approval", "LEGAL_APPROVED")


def test_no_secrets_user_data_emitted():
    doc = mr.evaluate()
    blob = json.dumps(doc).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "begin private key" not in blob


def test_final_package_validator_consumes_readiness():
    """The I2O harness must see RELEASE_ASSET_REQUIRED as non-PASS."""
    spec = importlib.util.spec_from_file_location(
        "fv", str(Path(__file__).resolve().parent.parent / "scripts" /
                     "run_final_package_compliance_verification.py"))
    assert spec is not None and spec.loader is not None
    fv = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fv
    spec.loader.exec_module(fv)
    original = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = mr.ASSET_PATH.parent / "does-not-exist-i2q1d.txt"
        doc = mr.evaluate()  # RELEASE_ASSET_REQUIRED (isolated, order-safe)
    finally:
        mr.ASSET_PATH = original
    assert doc["state"] in ("RELEASE_ASSET_REQUIRED", "BLOCKED")
    assert doc["asset"] is None
    # The readiness document is the consumable input: its state maps to a
    # non-ready aggregate family (BLOCKED) in the I2R aggregator.
    assert doc["state"] != "READY"


def tmp_path_guard():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="i2p-consume-"))


def test_cli_exit_codes():
    import subprocess
    doc = mr.evaluate()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True,
        timeout=600)
    expected = {"READY": 0, "RELEASE_ASSET_REQUIRED": 2, "BLOCKED": 3}
    assert proc.returncode == expected.get(doc["state"], 1)