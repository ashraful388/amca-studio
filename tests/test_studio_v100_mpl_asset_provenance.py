"""MPL release asset provenance + final compliance handoff tests (I2T).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2T-EXTERNAL-MPL-ASSET-PROVENANCE-RECEIPT-AND-FINAL-COMPLIANCE-HANDOFF-01
Worker: OpenCode PS-2. Tests only; no fabricated license content; no
dependency/packaging/promotion changes; canonical release asset path never
modified; adapter strictly read-only.

Covers: valid I2Q intake + provenance metadata -> ready; missing source
reference explicit; wrong hash rejected; different staged bytes rejected;
wrong component/version rejected; non-canonical asset cannot gain
provenance-ready; dry-run/no-stage remains non-ready; receipt tampering
detected; no source URI invented; no legal approval fabricated; adapter
read-only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

def _load(name: str, filename: str):
    path = Path(__file__).resolve().parent.parent / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mi = _load("mpl_intake_i2t", "import_mpl_release_asset.py")
pv = _load("mpl_provenance_i2t", "record_mpl_asset_provenance.py")

# Canonical section anchors come from the readiness module (single source of
# truth) so the fixture tracks the authoritative MPL-2.0 revision the
# validator enforces rather than a historical header list.
CANONICAL_SECTIONS = mi.readiness.CANONICAL_SECTIONS
CANONICAL_MPL = "\n".join(
    s + "\n" + "-" * len(s) for s in CANONICAL_SECTIONS) + "\n"

LOCAL_NON_CANONICAL = (
    "Mozilla Public License Version 2.0\n"
    "1. Definitions\n2. License Grants and Conditions\n"
    "3. Responsibilities\n5. Termination\n6. Disclaimer of Warranty\n"
    "Exhibit A\n")


def _run_intake(tmp_path: Path, content: str = CANONICAL_MPL,
                stage: bool = True, component: str = "certifi",
                version: str = "2026.7.22"):
    src = tmp_path / "source-mpl.txt"
    src.write_text(content, encoding="utf-8")
    dest = tmp_path / "assets" / f"MPL-2.0-{component}-{version}.txt"
    receipt = mi.intake(src, component=component, version=version,
                        stage=stage, destination=dest)
    receipt_path = tmp_path / "intake-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt, receipt_path


def test_valid_intake_plus_provenance_ready(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path)
    doc = pv.build_provenance(
        receipt_path, source_description="Official Mozilla MPL-2.0 text "
        "supplied by the Manager",
        source_uri="https://www.mozilla.org/media/MPL/2.0/index.txt",
        original_filename="index.txt")
    assert doc["provenance_state"] == "PROVENANCE_READY"
    assert doc["component"] == "certifi"
    assert doc["component_version"] == "2026.7.22"
    assert doc["input_sha256"] == receipt["input_sha256"]
    assert doc["staged_sha256"] == receipt["staged_sha256"]
    assert doc["canonical_mpl_validator_verdict"] == \
        "ACCEPTED_CANONICAL_MPL_2_0"
    assert doc["supplied_source_uri"].startswith("https://")
    verdict, reasons = pv.validate_provenance(doc)
    assert verdict == "PASS", reasons


def test_missing_source_reference_explicit(tmp_path: Path):
    _, receipt_path = _run_intake(tmp_path)
    doc = pv.build_provenance(receipt_path, source_description=None)
    assert doc["provenance_state"] == "SOURCE_REFERENCE_MISSING"
    assert "do not invent" in doc["detail"]
    verdict, _ = pv.validate_provenance(doc)
    assert verdict == "BLOCKED"


def test_wrong_hash_rejected(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path)
    # Tamper with the staged asset after intake.
    staged = Path(receipt["staged_path"])
    staged.write_text(staged.read_text(encoding="utf-8") + "\nappendix\n",
                      encoding="utf-8")
    doc = pv.build_provenance(receipt_path, source_description="official")
    assert doc["provenance_state"] == "HASH_MISMATCH"
    verdict, _ = pv.validate_provenance(doc)
    assert verdict == "FAIL"


def test_different_staged_bytes_rejected(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path)
    receipt["staged_sha256"] = "0" * 64  # forged binding
    forged = tmp_path / "forged-receipt.json"
    forged.write_text(json.dumps(receipt), encoding="utf-8")
    doc = pv.build_provenance(forged, source_description="official")
    assert doc["provenance_state"] == "HASH_MISMATCH"


def test_wrong_component_version_rejected(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path, component="other",
                                        version="9.9.9")
    doc = pv.build_provenance(receipt_path, source_description="official")
    assert doc["provenance_state"] == "CONTENT_INVALID"
    assert doc["canonical_mpl_validator_verdict"] == "REJECTED_BINDING"


def test_noncanonical_asset_cannot_gain_provenance_ready(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path, content=LOCAL_NON_CANONICAL)
    assert receipt["validator_result"] == "REJECTED_NON_CANONICAL"
    doc = pv.build_provenance(receipt_path, source_description="official")
    assert doc["provenance_state"] == "CONTENT_INVALID"
    verdict, _ = pv.validate_provenance(doc)
    assert verdict != "PASS"


def test_dry_run_no_stage_remains_non_ready(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path, stage=False)
    assert receipt["staged"] is False
    doc = pv.build_provenance(receipt_path, source_description="official")
    assert doc["provenance_state"] == "ASSET_NOT_STAGED"


def test_receipt_tampering_detected(tmp_path: Path):
    _, receipt_path = _run_intake(tmp_path)
    good = json.loads(receipt_path.read_text(encoding="utf-8"))
    good["input_sha256"] = "1" * 64
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(good), encoding="utf-8")
    doc = pv.build_provenance(tampered, source_description="official")
    # The input file on disk no longer matches the (forged) recorded hash
    # binding, so the state cannot be PROVENANCE_READY.
    assert doc["provenance_state"] != "PROVENANCE_READY"


def test_no_source_uri_invented(tmp_path: Path):
    _, receipt_path = _run_intake(tmp_path)
    doc = pv.build_provenance(receipt_path, source_description=None)
    assert doc["supplied_source_uri"] is None
    blob = json.dumps(doc)
    assert "mozilla.org" not in blob


def test_no_legal_approval_fabricated(tmp_path: Path):
    _, receipt_path = _run_intake(tmp_path)
    doc = pv.build_provenance(receipt_path, source_description="official")
    # Strip filesystem paths first: pytest tmp dir names can embed the test
    # id (which contains this test's own name), not document content. The
    # JSON body escapes backslashes, so replace the escaped form too.
    blob = json.dumps(doc)
    for key in ("input_path", "staging_destination", "intake_receipt_path"):
        value = doc.get(key)
        if value:
            blob = blob.replace(str(value), "<path>")
            blob = blob.replace(
                json.dumps(str(value))[1:-1], "<path>")
    scanned = blob.replace(
        "no legal approval is claimed or implied", "")
    assert "legal_approval" not in scanned
    assert "LEGAL_APPROVED" not in scanned
    for key in doc:
        assert key not in ("legal_approval", "LEGAL_APPROVED")


def test_canonical_release_asset_not_modified_by_tests(tmp_path: Path):
    real = (mi.SOFTWARE_ROOT / "docs" / "release" / "assets" /
            "MPL-2.0-certifi-2026.7.22.txt")
    before = hashlib.sha256(real.read_bytes()).hexdigest() \
        if real.is_file() else None
    _run_intake(tmp_path)  # stages into tmp_path only
    after = hashlib.sha256(real.read_bytes()).hexdigest() \
        if real.is_file() else None
    assert before == after


def test_adapter_read_only(tmp_path: Path):
    receipt, receipt_path = _run_intake(tmp_path)
    doc = pv.build_provenance(receipt_path, source_description="official")
    handoff = pv.provenance_handoff(doc)
    assert handoff["read_only"] is True
    assert handoff["validated_mpl_asset_sha256"] == doc["staged_sha256"]
    assert handoff["asset_state"] == "PROVENANCE_READY"
    assert handoff["component_version"] == "2026.7.22"
    assert handoff["source_reference"]
    # The adapter created/modified nothing in the staging area.
    staged = Path(doc["staging_destination"])
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == \
        doc["staged_sha256"]


def test_handoff_not_ready_when_source_missing(tmp_path: Path):
    _, receipt_path = _run_intake(tmp_path)
    doc = pv.build_provenance(receipt_path, source_description=None)
    handoff = pv.provenance_handoff(doc)
    assert handoff["asset_state"] == "SOURCE_REFERENCE_MISSING"
    assert handoff["validated_mpl_asset_sha256"] is None