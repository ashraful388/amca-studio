"""Shipping documentation export tests (I2S).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2S-IMMUTABLE-SHIPPING-DOCUMENTATION-EXPORT-AND-POST-CERTIFICATION-RELEASE-NOTES-FINALIZATION-CONTRACT-01
Worker: OpenCode PS-2. Tests only; no source/product/packaging changes.

Covers: BLOCKED without final certification; valid final evidence exports;
source tree unchanged; candidate file unchanged; final wording only when
evidence permits; wrong source SHA rejected; stale evidence rejected;
unsigned disclosure per policy; PASS_WITH_REVIEW preserved; Linux exclusion
preserved; manifest hashes match exported bytes; no secrets; exporter never
promotes artifacts.
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
    / "export_final_release_documentation.py"
)
_spec = importlib.util.spec_from_file_location("doc_export", SCRIPT)
assert _spec is not None and _spec.loader is not None
dx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dx
_spec.loader.exec_module(dx)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = SOFTWARE_ROOT / "docs" / "release" / (
    "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json")
CANDIDATE = SOFTWARE_ROOT / "docs" / "release" / (
    "AMCA-Studio-v1.0.0-Release-Notes-Candidate.md")
LIVE_SHA = json.loads(EVIDENCE.read_text(encoding="utf-8"))["source_sha"] \
    if EVIDENCE.is_file() else "0" * 40


def _final_evidence() -> Path:
    """Final-role evidence fixture bound to the live source SHA."""
    e = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    e["package_role"] = "final"
    path = Path(__file__).parent / "_i2s-final-evidence-fixture.json"
    path.write_text(json.dumps(e, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture()
def final_evidence(tmp_path: Path) -> Path:
    e = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    e["package_role"] = "final"
    target = tmp_path / "final-evidence.json"
    target.write_text(json.dumps(e, indent=2, sort_keys=True),
                      encoding="utf-8")
    return target


def test_no_final_certification_blocked(tmp_path: Path):
    result = dx.export_bundle(
        tmp_path / "stage", LIVE_SHA, tmp_path / "missing.json", stage=False)
    assert result["overall"] == "BLOCKED"
    assert "final certification unavailable" in result["reasons"][0]


def test_validation_role_evidence_blocked(final_evidence: Path,
                                          tmp_path: Path):
    e = json.loads(final_evidence.read_text(encoding="utf-8"))
    e["package_role"] = "validation"
    validation = tmp_path / "validation-evidence.json"
    validation.write_text(json.dumps(e), encoding="utf-8")
    result = dx.export_bundle(tmp_path / "stage", LIVE_SHA, validation,
                              stage=False)
    assert result["overall"] == "BLOCKED"
    assert any("validation-role" in r for r in result["reasons"])


def test_valid_final_evidence_export_allowed(final_evidence: Path,
                                             tmp_path: Path):
    result = dx.export_bundle(tmp_path / "stage", LIVE_SHA, final_evidence,
                              stage=False)
    assert result["overall"] == "PASS_WITH_REVIEW"
    keys = [d["key"] for d in result["documents"]]
    for required in ("final_release_notes", "known_limitations",
                     "installation", "upgrade_recovery", "troubleshooting",
                     "third_party_notices", "sbom", "compliance_summary",
                     "certification_summary"):
        assert required in keys, f"missing export doc: {required}"


def test_source_tree_unchanged(final_evidence: Path, tmp_path: Path):
    tracked = [p for p in (SOFTWARE_ROOT / "docs" / "release").glob("*.md")]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
    dx.export_bundle(tmp_path / "stage", LIVE_SHA, final_evidence, stage=True)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
    assert before == after
    # Nothing was written into the canonical source tree.
    assert not (SOFTWARE_ROOT / "docs" / "release" /
                "AMCA-Studio-v1.0.0-Release-Notes.md").exists()


def test_candidate_release_notes_unchanged(final_evidence: Path,
                                           tmp_path: Path):
    before = CANDIDATE.read_bytes()
    dx.export_bundle(tmp_path / "stage", LIVE_SHA, final_evidence, stage=True)
    assert CANDIDATE.read_bytes() == before
    assert "Candidate" in before.decode("utf-8")  # candidate wording intact


def test_final_wording_only_in_export(final_evidence: Path, tmp_path: Path):
    result = dx.export_bundle(tmp_path / "stage", LIVE_SHA, final_evidence,
                              stage=False)
    notes = next(d for d in result["documents"]
                 if d["key"] == "final_release_notes")
    # Exported copy is finalized; the wording can't be checked via hash here,
    # so verify through a real staged export.
    stage_dir = tmp_path / "stage"
    dx.export_bundle(stage_dir, LIVE_SHA, final_evidence, stage=True)
    exported = (stage_dir / "AMCA-Studio-v1.0.0-Release-Notes.md").read_text(
        encoding="utf-8")
    assert "RELEASED" in exported
    assert "Release certification" in exported
    # The candidate source must NOT contain the finalized wording.
    assert "RELEASED — AMCA Studio" not in CANDIDATE.read_text(
        encoding="utf-8")


def test_wrong_source_sha_rejected(final_evidence: Path, tmp_path: Path):
    result = dx.export_bundle(tmp_path / "stage", "f" * 40, final_evidence,
                              stage=False)
    assert result["overall"] == "BLOCKED"
    assert any("expected" in r for r in result["reasons"])


def test_stale_evidence_rejected(final_evidence: Path, tmp_path: Path):
    e = json.loads(final_evidence.read_text(encoding="utf-8"))
    e["source_sha"] = "0" * 40  # evidence from another lineage
    stale = tmp_path / "stale-evidence.json"
    stale.write_text(json.dumps(e), encoding="utf-8")
    result = dx.export_bundle(tmp_path / "stage", LIVE_SHA, stale,
                              stage=False)
    assert result["overall"] == "BLOCKED"


def test_unsigned_disclosure_per_policy(final_evidence: Path, tmp_path: Path):
    include = dx.export_bundle(
        tmp_path / "a", LIVE_SHA, final_evidence, stage=False,
        policy={"unsigned-disclosure": "include"})
    exclude = dx.export_bundle(
        tmp_path / "b", LIVE_SHA, final_evidence, stage=False,
        policy={"unsigned-disclosure": "exclude"})
    notes_i = next(d for d in include["documents"]
                   if d["key"] == "final_release_notes")
    notes_e = next(d for d in exclude["documents"]
                   if d["key"] == "final_release_notes")
    assert notes_i["sha256"] != notes_e["sha256"]
    stage_i = tmp_path / "stage-i"
    dx.export_bundle(stage_i, LIVE_SHA, final_evidence, stage=True,
                     policy={"unsigned-disclosure": "include"})
    exported = (stage_i / "AMCA-Studio-v1.0.0-Release-Notes.md").read_text(
        encoding="utf-8")
    assert "unsigned" in exported.casefold()


def test_compliance_pass_with_review_preserved(final_evidence: Path,
                                               tmp_path: Path):
    stage_dir = tmp_path / "stage"
    dx.export_bundle(stage_dir, LIVE_SHA, final_evidence, stage=True)
    summary = json.loads(
        (stage_dir / "AMCA-Studio-v1.0.0-Certification-Summary.json"
         ).read_text(encoding="utf-8"))
    assert summary["compliance_state"] == "PASS_WITH_REVIEW"
    blob = json.dumps(summary)
    assert "legal_approval" not in blob
    assert "LEGAL_APPROVED" not in blob


def test_linux_exclusion_preserved(final_evidence: Path, tmp_path: Path):
    stage_dir = tmp_path / "stage"
    dx.export_bundle(stage_dir, LIVE_SHA, final_evidence, stage=True)
    limitations = (stage_dir /
                   "AMCA-Studio-v1.0.0-Known-Limitations.md").read_text(
                       encoding="utf-8")
    assert "linux" in limitations.casefold()
    assert "not yet built or certified" in limitations.casefold()
    summary = json.loads(
        (stage_dir / "AMCA-Studio-v1.0.0-Certification-Summary.json"
         ).read_text(encoding="utf-8"))
    assert summary["linux_included"] is False


def test_manifest_hashes_match_exported_bytes(final_evidence: Path,
                                              tmp_path: Path):
    stage_dir = tmp_path / "stage"
    result = dx.export_bundle(stage_dir, LIVE_SHA, final_evidence, stage=True)
    manifest = json.loads(
        (stage_dir / "AMCA-Studio-v1.0.0-Shipping-Documentation-Manifest.json"
         ).read_text(encoding="utf-8"))
    for entry in manifest["documents"]:
        exported = stage_dir / entry["document"]
        assert exported.is_file(), f"manifested doc missing: {entry['document']}"
        assert hashlib.sha256(exported.read_bytes()).hexdigest() == \
            entry["sha256"], f"hash mismatch: {entry['document']}"


def test_no_secrets_user_data(final_evidence: Path, tmp_path: Path):
    stage_dir = tmp_path / "stage"
    result = dx.export_bundle(stage_dir, LIVE_SHA, final_evidence, stage=True)
    blob = (json.dumps(result) + "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in stage_dir.rglob("*") if p.is_file())).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "begin private key" not in blob


def test_exporter_does_not_promote_artifacts(final_evidence: Path,
                                             tmp_path: Path):
    dist_dir = SOFTWARE_ROOT / "dist"
    dist_before = {p.name: p.stat().st_size for p in dist_dir.glob("*")
                   } if dist_dir.is_dir() else {}
    dx.export_bundle(tmp_path / "stage", LIVE_SHA, final_evidence, stage=True)
    dist_after = {p.name: p.stat().st_size for p in dist_dir.glob("*")
                  } if dist_dir.is_dir() else {}
    assert dist_before == dist_after
    # No artifact bytes are copied into the staging directory.
    staged = {p.name for p in (tmp_path / "stage").glob("*")}
    assert not any(name.endswith((".exe", ".zip")) for name in staged)


def test_default_dry_run_no_write(final_evidence: Path, tmp_path: Path):
    stage_dir = tmp_path / "never-created"
    result = dx.export_bundle(stage_dir, LIVE_SHA, final_evidence,
                              stage=False)
    assert result["overall"] == "PASS_WITH_REVIEW"
    assert result["staged"] is False
    assert not stage_dir.exists()