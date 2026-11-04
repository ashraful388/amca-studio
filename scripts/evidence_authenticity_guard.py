#!/usr/bin/env python3
"""I3B final-evidence authenticity guard + fixture quarantine helper.

OpenCode PS-2. Release-evidence integrity audit/tests/docs only. No
product/version/packaging/signing/promotion-policy/PS-1 I1.24/ZCode
I2F.1A/I2L/runtime/MPL-content/release-tag changes.

Narrow helper that classifies PS-2 evidence documents and decides whether a
document may be treated as FINAL_EVIDENCE_ELIGIBLE. It reuses the lineage
fields the accepted tools already emit (source_sha, package_role, artifact
hashes, validation-mode markers) and adds an explicit quarantine check for
synthetic/test/simulation markers. It does NOT duplicate hash/lineage
validation logic — callers (I2R/I2U) still enforce the byte-level bindings.

Classification: TEST_FIXTURE | SYNTHETIC_SIMULATION | HISTORICAL_EVIDENCE |
FINAL_EVIDENCE_ELIGIBLE | UNRECOGNIZED.

Quarantine rule: evidence is quarantined (NOT final-eligible) when it:
  - was produced under a test/fixture/simulation environment marker;
  - resides at a canonical final-evidence path without an explicit
    final-run record;
  - binds to a different source SHA or artifact hashes;
  - carries validation/simulation role instead of final role.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "amca-evidence-authenticity-guard/1"

CLASSIFICATIONS = ("TEST_FIXTURE", "SYNTHETIC_SIMULATION",
                   "HISTORICAL_EVIDENCE", "FINAL_EVIDENCE_ELIGIBLE",
                   "UNRECOGNIZED")

# Markers that quarantine evidence regardless of other fields.
QUARANTINE_MARKERS = (
    "test_studio_v100",  # generated under a pytest module run
    "pytest-of-",        # pytest tmp_path fixture root
    "synthetic", "simulation", "negative-control", "dry-run",
    "tmp_path", ".agent-tmp",
)

# Canonical final evidence locations (repository-relative). Evidence at
# these paths is final-eligible ONLY when produced by an explicit final run
# (final role + matching lineage) — not by tests or copies.
CANONICAL_FINAL_EVIDENCE = [
    "software/docs/release/AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json",
    "software/docs/release/ps2-final-release-evidence.json",
    "software/docs/release/ps2-promotion-input.json",
    "software/docs/release/mpl-asset-provenance.json",
    "software/docs/release/final-package-compliance-result.json",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(document: Dict[str, Any], path: Optional[str] = None
             ) -> Tuple[str, List[str]]:
    """Classify an evidence document. Returns (classification, reasons).

    Never mutates the document or the filesystem.
    """
    reasons: List[str] = []
    blob = json.dumps(document, sort_keys=True, default=str)

    # 1. Explicit synthetic/test/simulation markers quarantine first.
    for marker in QUARANTINE_MARKERS:
        if marker.casefold() in blob.casefold():
            reasons.append(f"quarantine marker present: {marker!r}")
    role = document.get("package_role") or document.get("mode")
    if role in ("validation", "test", "dry-run", "simulation"):
        reasons.append(f"non-final role: {role!r}")
    if document.get("fixture") is True or \
            document.get("is_fixture") is True:
        reasons.append("explicitly marked as fixture")

    # 2. Historical evidence: bound to a source SHA that is not the final
    #    certified one is HISTORICAL (stale) unless quarantined above.
    if not reasons:
        source = document.get("source_sha")
        if source and str(source).startswith("v0.9"):
            reasons.append(f"historical v0.9 lineage: {source}")

    if reasons:
        if any("v0.9" in r for r in reasons):
            return "HISTORICAL_EVIDENCE", reasons
        if any("simulation" in r or "marker present" in r for r in reasons):
            return "SYNTHETIC_SIMULATION", reasons
        return "TEST_FIXTURE", reasons

    # 3. Canonical-path rule: evidence sitting at a canonical final-evidence
    #    location must carry an explicit final-run record to be eligible.
    if path and any(path.replace("\\", "/").endswith(loc)
                    for loc in CANONICAL_FINAL_EVIDENCE):
        final_run = document.get("final_run_recorded")
        if not final_run:
            reasons.append(
                "resides at a canonical final-evidence path without an "
                "explicit final-run record")
            return "TEST_FIXTURE", reasons

    # 4. Final eligibility requires complete lineage binding.
    if document.get("schema") == "amca-ps2-promotion-input/1":
        if not document.get("source_sha") or not document.get(
                "portable_sha256") or not document.get("installer_sha256"):
            reasons.append("promotion input lacks complete lineage binding")
            return "UNRECOGNIZED", reasons
    return "FINAL_EVIDENCE_ELIGIBLE", reasons


def final_eligibility_verdict(
    document: Dict[str, Any],
    expected_source_sha: Optional[str] = None,
    expected_portable_sha: Optional[str] = None,
    expected_installer_sha: Optional[str] = None,
    path: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Deterministic quarantine verdict: QUARANTINED or ACCEPTED.

    Synthetic/test/historical evidence is proven INCAPABLE of satisfying
    final promotion readiness; genuine same-lineage evidence with no
    synthetic markers is accepted.
    """
    classification, reasons = classify(document, path=path)
    if classification != "FINAL_EVIDENCE_ELIGIBLE":
        return "QUARANTINED", [f"{classification}: {r}" for r in reasons]
    lineage: List[str] = []
    if expected_source_sha and document.get("source_sha") != \
            expected_source_sha:
        lineage.append(
            f"source SHA mismatch: {document.get('source_sha')} vs "
            f"{expected_source_sha}")
    artifacts = document.get("artifact_hashes") or {}
    if expected_portable_sha and (artifacts.get("portable") or {}).get(
            "sha256") != expected_portable_sha:
        lineage.append("portable hash mismatch vs final binary")
    if expected_installer_sha and (artifacts.get("installer") or {}).get(
            "sha256") != expected_installer_sha:
        lineage.append("installer hash mismatch vs final binary")
    if lineage:
        return "QUARANTINED", lineage
    return "ACCEPTED", ["genuine same-lineage final evidence"]


def quarantine_report(documents: List[Tuple[Optional[str], Dict[str, Any]]]
                      ) -> Dict[str, Any]:
    """Build a quarantine report for a set of evidence documents."""
    entries: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {c: 0 for c in CLASSIFICATIONS}
    for path, document in documents:
        classification, reasons = classify(document, path=path)
        counts[classification] = counts.get(classification, 0) + 1
        entries.append({"path": path, "classification": classification,
                        "reasons": reasons})
    quarantined = counts["TEST_FIXTURE"] + counts["SYNTHETIC_SIMULATION"] + \
        counts["HISTORICAL_EVIDENCE"] + counts["UNRECOGNIZED"]
    return {
        "schema": SCHEMA,
        "entries": entries,
        "counts": counts,
        "quarantined": quarantined,
        "final_eligible": counts["FINAL_EVIDENCE_ELIGIBLE"],
    }
