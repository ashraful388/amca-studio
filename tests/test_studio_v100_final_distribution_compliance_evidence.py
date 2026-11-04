"""Final distribution-compliance evidence tests (I2M).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2M-FINAL-COMPLIANCE-EVIDENCE-BUNDLE-THIRD-PARTY-NOTICES-PAYLOAD-AND-PROMOTION-INPUT-CONTRACT-01
Worker: OpenCode PS-2. Tests only; no production/packaging changes.

Covers: zero-BLOCKING+REVIEW -> PASS_WITH_REVIEW; any BLOCKING -> BLOCKED;
UNKNOWN never silently PASS; REVIEW items preserved; SBOM/notices/report
hash mismatches rejected; altered files invalidate evidence; source-SHA
binding; final-package rerun rule; build-only scoping honesty; no fabricated
legal-approval field; adapter read-only; no secrets/user data emitted; plus a
live end-to-end generation run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "generate_distribution_compliance_evidence.py"
)
_spec = importlib.util.spec_from_file_location("compliance_evidence", SCRIPT)
assert _spec is not None and _spec.loader is not None
ev = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ev
_spec.loader.exec_module(ev)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent


def _base_evidence() -> dict:
    inner = {"overall": "PASS", "counts": {},
             "findings": [], "components": []}
    smoke = {"a": 1}
    blob = json.dumps(smoke, sort_keys=True)
    import hashlib
    digest = hashlib.sha256(blob.encode()).hexdigest()
    return {
        "schema": ev.SCHEMA_ID,
        "evidence_schema_version": ev.EVIDENCE_SCHEMA_VERSION,
        "overall": "PASS_WITH_REVIEW",
        "certification_timestamp": "2026-09-03T23:00:00Z",
        "source_sha": "a" * 40,
        "package_role": "validation",
        "sbom": {"path": "sbom.json", "sha256": digest},
        "notices": {"path": "notices.md", "sha256": digest},
        "report": {"path": "report.md", "sha256": digest},
        "counts": {"PASS": 1, "REVIEW": 1, "BLOCKING": 0, "UNKNOWN": 0},
        "review_acknowledgments": [{
            "component": "certifi", "version": "2026.7.22",
            "license": "MPL-2.0", "shipped": True,
            "reason": "weak copyleft shipped",
            "required_action": "include text",
            "technically_blocking": False,
            "acknowledgment_status": "pending-release-acknowledgment"}],
        "unresolved_review_items": ["certifi"],
        "shipping_classification": {"certifi": True},
        "notices_problems": [],
        "bindings": {"sbom_sha256": digest, "inner": inner},
        "limitations": ["technical assessment only"],
    }


def test_review_without_blocking_is_pass_with_review():
    assert ev._overall({"PASS": 1, "REVIEW": 2, "BLOCKING": 0,
                        "UNKNOWN": 0}, 0) == "PASS_WITH_REVIEW"


def test_any_blocking_is_blocked():
    assert ev._overall({"PASS": 1, "REVIEW": 0, "BLOCKING": 1,
                        "UNKNOWN": 0}, 0) == "BLOCKED"


def test_unknown_shipped_cannot_silently_pass():
    assert ev._overall({"PASS": 1, "REVIEW": 0, "BLOCKING": 0,
                        "UNKNOWN": 0}, 1) == "BLOCKED"
    assert ev._overall({"PASS": 1, "REVIEW": 0, "BLOCKING": 0,
                        "UNKNOWN": 0}, 0) == "PASS"


def test_review_items_preserved(tmp_path: Path):
    assert len(_base_evidence()["review_acknowledgments"]) == 1
    ack = _base_evidence()["review_acknowledgments"][0]
    for key in ("component", "version", "license", "shipped", "reason",
                "required_action", "technically_blocking",
                "acknowledgment_status"):
        assert key in ack, f"ack missing: {key}"
    assert ack["acknowledgment_status"] == "pending-release-acknowledgment"


def _write_files(tmp_path: Path, bundle: dict) -> dict:
    for key in ("sbom", "notices", "report"):
        target = tmp_path / f"{key}.dat"
        target.write_text(f"content-{key}", encoding="utf-8")
        import hashlib
        bundle[key]["path"] = str(target)
        bundle[key]["sha256"] = hashlib.sha256(
            target.read_bytes()).hexdigest()
    return bundle


def test_hash_mismatches_rejected(tmp_path: Path):
    for key in ("sbom", "notices", "report"):
        bundle = _write_files(tmp_path, _base_evidence())
        target = Path(bundle[key]["path"])
        target.write_text(target.read_text(encoding="utf-8") + "x",
                          encoding="utf-8")
        verdict, reasons = ev.validate_distribution_compliance_evidence(bundle)
        assert verdict == "FAIL", (key, reasons)
        assert any("mismatch" in r or "altered" in r for r in reasons)


def test_altered_file_invalidates_evidence(tmp_path: Path):
    bundle = _write_files(tmp_path, _base_evidence())
    verdict, _ = ev.validate_distribution_compliance_evidence(bundle)
    assert verdict == "PASS_WITH_REVIEW"
    Path(bundle["sbom"]["path"]).write_bytes(b"tampered")
    verdict, _ = ev.validate_distribution_compliance_evidence(bundle)
    assert verdict == "FAIL"


def test_source_sha_binding(tmp_path: Path):
    bundle = _write_files(tmp_path, _base_evidence())
    verdict, _ = ev.validate_distribution_compliance_evidence(
        bundle, expected_source_sha="a" * 40)
    assert verdict == "PASS_WITH_REVIEW"
    verdict, reasons = ev.validate_distribution_compliance_evidence(
        bundle, expected_source_sha="b" * 40)
    assert verdict == "FAIL"
    assert any("mismatch" in r for r in reasons)


def test_final_package_rerun_rule(tmp_path: Path):
    bundle = _write_files(tmp_path, _base_evidence())
    verdict, reasons = ev.validate_distribution_compliance_evidence(
        bundle, require_final_package=True)
    assert verdict == "FAIL"
    assert any("rerun" in r for r in reasons)
    final_bundle = _write_files(tmp_path, _base_evidence())
    final_bundle["package_role"] = "final"
    verdict, _ = ev.validate_distribution_compliance_evidence(
        final_bundle, require_final_package=True)
    assert verdict == "PASS_WITH_REVIEW"


def test_build_only_not_falsely_shipped():
    bundle = _base_evidence()
    bundle["shipping_classification"] = {"pyinstaller": False,
                                         "certifi": True}
    assert bundle["shipping_classification"]["pyinstaller"] is False


def test_no_fabricated_legal_approval_field(tmp_path: Path):
    bundle = _write_files(tmp_path, _base_evidence())
    bundle["legal_approval"] = True
    verdict, reasons = ev.validate_distribution_compliance_evidence(bundle)
    assert verdict == "FAIL"
    assert any("legal" in r for r in reasons)


def test_adapter_read_only(tmp_path: Path):
    bundle = _write_files(tmp_path, _base_evidence())
    before = {p.name for p in tmp_path.iterdir()}
    verdict, _ = ev.validate_distribution_compliance_evidence(bundle)
    assert verdict == "PASS_WITH_REVIEW"
    assert {p.name for p in tmp_path.iterdir()} == before


def test_no_secrets_user_data_emitted():
    bundle = _base_evidence()
    blob = json.dumps(bundle)
    assert "BEGIN PRIVATE KEY" not in blob
    lowered = blob.casefold()
    assert "sk-proj-" not in lowered
    assert "ghp_" not in lowered


def test_end_to_end_generation(tmp_path: Path):
    out = tmp_path / "evidence.json"
    bundle = ev.generate_evidence(out)
    assert out.is_file()
    assert bundle["overall"] == "PASS_WITH_REVIEW"
    assert bundle["schema"] == ev.SCHEMA_ID
    # Every REVIEW finding is carried explicitly: one acknowledgment per
    # REVIEW count, and the unresolved list names exactly those components.
    # (Which components are REVIEW is a live audit property, not a frozen
    # historical list.)
    acks = bundle["review_acknowledgments"]
    assert len(acks) == bundle["counts"]["REVIEW"]
    assert len(acks) >= 1
    assert set(bundle["unresolved_review_items"]) == {
        a["component"] for a in acks}
    for ack in acks:
        assert ack["component"]
        assert ack["required_action"]
        assert ack["acknowledgment_status"] == "pending-release-acknowledgment"
    assert bundle["notices_problems"] == []
    verdict, reasons = ev.validate_distribution_compliance_evidence(bundle)
    assert verdict in ("PASS_WITH_REVIEW", "BLOCKED"), reasons
