#!/usr/bin/env python3
"""I2U PS-2 aggregate -> final promotion input adapter.

OpenCode PS-2. Final evidence compatibility / read-only adapter. No
promotion-gate, packaging, version, or product changes. Consumes the
accepted I2R aggregate evidence (amca-ps2-final-release-evidence/1) and the
I2T MPL provenance receipt (amca-mpl-release-asset-provenance/1) WITHOUT
duplicating their validation logic, and emits one deterministic promotion
input contract (amca-ps2-promotion-input/1) for ZCode's release
orchestration/promotion layer.

  python scripts/build_ps2_promotion_input.py
      --aggregate PATH --provenance PATH [--expected-source-sha SHA]
      [--expected-portable-sha SHA] [--expected-installer-sha SHA]
      [--output PATH]

State mapping (never upgraded, never softened):
  PS2_RELEASE_READY               -> PS2_INPUT_READY
  PS2_RELEASE_READY_WITH_REVIEW   -> PS2_INPUT_READY_WITH_REVIEW
  PS2_RELEASE_BLOCKED             -> PS2_INPUT_BLOCKED
  PS2_RELEASE_FAILED              -> PS2_INPUT_FAILED

MPL requirements for final eligibility: I2P READY, I2T PROVENANCE_READY,
and the staged MPL asset hash must match the hash consumed by the final
compliance evidence. An asset without complete provenance is BLOCKED, never
silently valid. Same-lineage requirements reject v0.9 validation evidence,
different-source-SHA aggregates, package-byte mismatches, stale MPL
provenance, and evidence tied to a different installer/portable build.

Strictly read-only: no copying, staging, promotion, source mutation, or
policy decision. No legal-approval field exists.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

SCHEMA = "amca-ps2-promotion-input/1"

# Historical v1.0.0 artifact hashes — may never be reused as v1.0.1
# promotion evidence (H1C lineage rule).
V101_REJECTED_HASHES = {
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1",
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454",
}

STATE_MAP = {
    "PS2_RELEASE_READY": "PS2_INPUT_READY",
    "PS2_RELEASE_READY_WITH_REVIEW": "PS2_INPUT_READY_WITH_REVIEW",
    "PS2_RELEASE_BLOCKED": "PS2_INPUT_BLOCKED",
    "PS2_RELEASE_FAILED": "PS2_INPUT_FAILED",
}

REQUIRED_AGGREGATE_FIELDS = ["schema", "aggregate_verdict", "source_sha",
                             "families", "review_items", "blockers"]
REQUIRED_PROVENANCE_FIELDS = ["schema", "provenance_state", "component",
                              "component_version", "staged_sha256"]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_family_state(aggregate: Dict[str, Any], family: str,
                           expected: str) -> Optional[str]:
    entry = aggregate.get("families", {}).get(family)
    if entry is None:
        return "PENDING"
    return entry.get("state")


def _fail(reasons: List[str], message: str) -> Dict[str, Any]:
    reasons.append(message)
    return {"overall": "PS2_INPUT_FAILED"}


def build_promotion_input(
    aggregate_path: Path,
    provenance_path: Path,
    expected_source_sha: Optional[str] = None,
    expected_portable_sha: Optional[str] = None,
    expected_installer_sha: Optional[str] = None,
    expected_msi_sha: Optional[str] = None,
    release_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Combine I2R + I2T evidence into the promotion input (read-only)."""
    reasons: List[str] = []
    base: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release_version": release_version,
        "source_sha": None,
        "installer_sha256": None,
        "msi_sha256": None,
        "portable_sha256": None,
        "ps2_aggregate_verdict": None,
        "compliance_state": None,
        "migration_state": None,
        "sanitation_state": None,
        "documentation_integrity_state": None,
        "sbom_payload_state": None,
        "mpl_readiness": None,
        "mpl_provenance_state": None,
        "mpl_provenance_receipt_sha256": None,
        "validated_mpl_asset_sha256": None,
        "review_items": [],
        "blockers": [],
        "stale_mismatch_findings": [],
        "final_package_rerun_performed": None,
        "promotion_eligibility_ps2": "PS2_INPUT_BLOCKED",
        "legal_boundary": (
            "PS-2 technical evidence perspective only; not legal advice; "
            "policy acknowledgment cannot alter the technical verdict; no "
            "legal approval is claimed."),
    }

    def finish(verdict: Optional[str] = None) -> Dict[str, Any]:
        doc = dict(base)
        if verdict:
            doc["promotion_eligibility_ps2"] = verdict
        doc["reasons"] = reasons
        return doc

    # --- inputs present ----------------------------------------------------
    for label, path in (("I2R aggregate", aggregate_path),
                        ("I2T provenance", provenance_path)):
        if not path.is_file():
            reasons.append(f"mandatory input missing: {label} ({path})")
            return finish("PS2_INPUT_BLOCKED")
    try:
        aggregate = _read_json(aggregate_path)
        provenance = _read_json(provenance_path)
    except ValueError as exc:
        reasons.append(f"evidence unreadable: {exc}")
        return finish("PS2_INPUT_FAILED")

    for field in REQUIRED_AGGREGATE_FIELDS:
        if field not in aggregate:
            reasons.append(f"I2R aggregate missing field: {field}")
    for field in REQUIRED_PROVENANCE_FIELDS:
        if field not in provenance:
            reasons.append(f"I2T provenance missing field: {field}")
    if reasons:
        return finish("PS2_INPUT_BLOCKED")
    if aggregate.get("schema") != "amca-ps2-final-release-evidence/1":
        reasons.append(f"unsupported aggregate schema: {aggregate.get('schema')!r}")
        return finish("PS2_INPUT_FAILED")
    if provenance.get("schema") != "amca-mpl-release-asset-provenance/1":
        reasons.append(
            f"unsupported provenance schema: {provenance.get('schema')!r}")
        return finish("PS2_INPUT_FAILED")

    # --- same-lineage binding ----------------------------------------------
    source_sha = aggregate.get("source_sha")
    base["source_sha"] = source_sha
    if expected_source_sha and source_sha != expected_source_sha:
        reasons.append(
            f"source SHA mismatch: aggregate bound to {source_sha}, expected "
            f"{expected_source_sha} (v0.9/other-lineage evidence cannot "
            "certify v1.0)")
        return finish("PS2_INPUT_FAILED")
    artifacts = aggregate.get("artifact_hashes", {})
    portable_sha = (artifacts.get("portable") or {}).get("sha256")
    installer_sha = (artifacts.get("installer") or {}).get("sha256")
    msi_sha = (artifacts.get("msi") or {}).get("sha256")
    base["portable_sha256"] = portable_sha
    base["installer_sha256"] = installer_sha
    base["msi_sha256"] = msi_sha
    if expected_portable_sha and portable_sha != expected_portable_sha:
        reasons.append("portable package bytes differ from the expected "
                       "final build")
        return finish("PS2_INPUT_FAILED")
    if expected_installer_sha and installer_sha != expected_installer_sha:
        reasons.append("installer bytes differ from the expected final build")
        return finish("PS2_INPUT_FAILED")
    if expected_msi_sha and msi_sha != expected_msi_sha:
        reasons.append("MSI bytes differ from the expected final build")
        return finish("PS2_INPUT_FAILED")
    release_version = str(base.get("release_version", ""))
    if release_version.startswith("1.0.1") and msi_sha is None:
        reasons.append(
            "v1.0.1 promotion input requires MSI evidence; missing MSI is "
            "an honest BLOCKED, never a silent skip")
        return finish("PS2_INPUT_BLOCKED")
    if release_version.startswith("1.0.1") and msi_sha in V101_REJECTED_HASHES:
        reasons.append("historical v1.0.0 artifact hash reused as v1.0.1 MSI "
                       "evidence")
        return finish("PS2_INPUT_FAILED")

    # --- family states ------------------------------------------------------
    families = aggregate.get("families", {})
    base["migration_state"] = families.get(
        "migration_upgrade_certification", {}).get("state")
    base["compliance_state"] = families.get(
        "dependency_sbom_compliance", {}).get("state")
    base["sanitation_state"] = families.get(
        "sanitation_privacy_audit", {}).get("state")
    base["documentation_integrity_state"] = families.get(
        "user_documentation_integrity", {}).get("state")
    base["sbom_payload_state"] = families.get(
        "third_party_payload_completeness", {}).get("state")
    base["review_items"] = list(aggregate.get("review_items", []))
    base["blockers"] = list(aggregate.get("blockers", []))

    # --- MPL requirements ----------------------------------------------------
    provenance_state = provenance.get("provenance_state")
    base["mpl_provenance_state"] = provenance_state
    base["mpl_provenance_receipt_sha256"] = provenance.get(
        "intake_receipt_sha256")
    mpl_family = families.get("mpl_readiness_intake_state", {})
    base["mpl_readiness"] = mpl_family.get("state")
    if provenance_state != "PROVENANCE_READY":
        reasons.append(
            f"MPL asset provenance is {provenance_state!r}; complete "
            "provenance (I2P READY + PROVENANCE_READY) is required for "
            "final eligibility")
        if base["ps2_aggregate_verdict"] is None:
            return finish("PS2_INPUT_BLOCKED")
    validated_mpl = provenance.get("staged_sha256")
    if provenance_state == "PROVENANCE_READY":
        base["validated_mpl_asset_sha256"] = validated_mpl
        # The staged MPL asset hash must match what final compliance consumed.
        mpl_evidence_sha = mpl_family.get("evidence_sha256")
        if mpl_evidence_sha and validated_mpl and \
                mpl_evidence_sha != validated_mpl:
            reasons.append(
                "staged MPL asset hash does not match the hash consumed by "
                "the final compliance evidence")
            return finish("PS2_INPUT_FAILED")

    # --- aggregate verdict mapping -------------------------------------------
    verdict = aggregate.get("aggregate_verdict")
    base["ps2_aggregate_verdict"] = verdict
    mapped = STATE_MAP.get(verdict)
    if mapped is None:
        reasons.append(f"unknown aggregate verdict: {verdict!r}")
        return finish("PS2_INPUT_FAILED")
    base["final_package_rerun_performed"] = bool(
        families.get("final_package_compliance_verification", {}).get("suite"))
    base["stale_mismatch_findings"] = list(aggregate.get("stale_inputs", []))
    # FAIL aggregate -> FAILED; anything FAILing later stays FAILED;
    # READY_WITH_REVIEW is never softened to READY.
    return finish(mapped)


def validate_promotion_input(doc: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Read-only validation of a promotion-input document (drift-hardened).

    Verifies the eligibility verdict is consistent with the aggregate
    verdict, so parser/schema drift cannot soften READY_WITH_REVIEW to
    READY or resurrect a FAILED/BLOCKED aggregate as ready.
    """
    reasons: List[str] = []
    if doc.get("schema") != SCHEMA:
        return "PS2_INPUT_FAILED", ["unsupported promotion-input schema"]
    if "legal_approval" in doc or "LEGAL_APPROVED" in json.dumps(doc):
        return "PS2_INPUT_FAILED", ["fabricated legal-approval field present"]
    verdict = doc.get("promotion_eligibility_ps2")
    if verdict not in STATE_MAP.values():
        return "PS2_INPUT_FAILED", [f"unknown eligibility: {verdict!r}"]
    # Consistency binding: eligibility must match the mapped aggregate
    # verdict. A document whose recorded aggregate verdict disagrees with
    # its claimed eligibility (e.g. None/missing after field drift) fails.
    recorded = doc.get("ps2_aggregate_verdict")
    expected = STATE_MAP.get(recorded)
    if expected is None:
        return "PS2_INPUT_FAILED", [
            f"aggregate verdict missing/unknown: {recorded!r}"]
    if verdict != expected:
        return "PS2_INPUT_FAILED", [
            f"eligibility drift detected: aggregate {recorded!r} maps to "
            f"{expected}, document claims {verdict!r}"]
    if doc.get("source_sha") is None:
        return "PS2_INPUT_FAILED", ["source SHA is mandatory"]
    if doc.get("portable_sha256") is None or doc.get("installer_sha256") is None:
        return "PS2_INPUT_FAILED", ["installer/portable hashes are mandatory"]
    version = str(doc.get("release_version", ""))
    if version.startswith("1.0.1"):
        if doc.get("msi_sha256") is None:
            return "PS2_INPUT_BLOCKED", [
                "v1.0.1 promotion cannot reach READY without MSI evidence"]
        if (doc.get("msi_sha256") in V101_REJECTED_HASHES
                or doc.get("installer_sha256") in V101_REJECTED_HASHES
                or doc.get("portable_sha256") in V101_REJECTED_HASHES):
            return "PS2_INPUT_FAILED", [
                "historical v1.0.0 artifact hash reused as v1.0.1 evidence"]
    if doc.get("mpl_readiness") != "PASS" or doc.get(
            "mpl_provenance_state") != "PROVENANCE_READY":
        return "PS2_INPUT_BLOCKED", [
            "MPL asset/provenance is not READY; final eligibility blocked"]
    return verdict, [f"promotion input {verdict}"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PS-2 aggregate -> final promotion input adapter (I2U)")
    parser.add_argument("--aggregate", required=True,
                        help="I2R aggregate evidence JSON")
    parser.add_argument("--provenance", required=True,
                        help="I2T MPL provenance JSON")
    parser.add_argument("--expected-source-sha", default=None)
    parser.add_argument("--expected-portable-sha", default=None)
    parser.add_argument("--expected-installer-sha", default=None)
    parser.add_argument("--expected-msi-sha", default=None,
                        help="v1.0.1 MSI hash binding (H1C)")
    parser.add_argument("--release-version", default="1.0.0",
                        help="target release version (v1.0.1 requires MSI)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    doc = build_promotion_input(
        Path(args.aggregate), Path(args.provenance),
        expected_source_sha=args.expected_source_sha,
        expected_portable_sha=args.expected_portable_sha,
        expected_installer_sha=args.expected_installer_sha,
        expected_msi_sha=args.expected_msi_sha,
        release_version=args.release_version)
    rendered = json.dumps(doc, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if doc["promotion_eligibility_ps2"] in (
        "PS2_INPUT_READY", "PS2_INPUT_READY_WITH_REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
