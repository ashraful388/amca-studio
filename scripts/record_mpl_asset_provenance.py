#!/usr/bin/env python3
"""I2T external MPL asset provenance receipt + final compliance handoff.

OpenCode PS-2. External-asset provenance / release evidence only. No
fabricated license content, no web downloads, no dependency/packaging/
promotion logic changes.

Consumes the I2Q intake receipt (does NOT reimplement canonical-MPL
validation) and adds provenance/handoff evidence so that, once the official
MPL-2.0 text is supplied, AMCA records where it came from, what exact bytes
were accepted, and which release component it satisfies.

  python scripts/record_mpl_asset_provenance.py
      --intake-receipt PATH --source-description TEXT
      [--source-uri URI] [--original-filename NAME] [--output PATH]

Provenance states:
  PROVENANCE_READY          — intake accepted, asset staged, hashes bound
  SOURCE_REFERENCE_MISSING  — everything valid except the caller-supplied
                              source description/URI
  CONTENT_INVALID           — I2Q validator did not accept the content
  HASH_MISMATCH             — staged bytes no longer match the intake input
  ASSET_NOT_STAGED          — dry-run receipt / asset never written

The handoff adapter (`provenance_handoff`) is read-only and supplies the
validated MPL asset hash, component/version, provenance receipt hash, asset
state, and source reference (if supplied) to I2R/I2O. No legal-approval
field exists anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

SCHEMA = "amca-mpl-release-asset-provenance/1"

REQUIRED_BINDING = {"component": "certifi", "version": "2026.7.22"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_provenance(
    intake_receipt_path: Path,
    source_description: Optional[str],
    source_uri: Optional[str] = None,
    original_filename: Optional[str] = None,
    supplied_timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the provenance receipt from an I2Q intake receipt (read-only
    on the intake receipt and on any staged asset)."""
    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "component": REQUIRED_BINDING["component"],
        "component_version": REQUIRED_BINDING["version"],
        "supplied_source_description": source_description,
        "supplied_source_uri": source_uri,
        "original_filename": original_filename,
        "input_sha256": None,
        "staged_sha256": None,
        "canonical_mpl_validator_verdict": None,
        "staging_destination": None,
        "intake_receipt_path": str(intake_receipt_path),
        "intake_receipt_sha256": None,
        "supplied_timestamp": supplied_timestamp,
        "provenance_state": "ASSET_NOT_STAGED",
        "legal_boundary": (
            "Provenance/handoff evidence only; not legal advice; no legal "
            "approval is claimed or implied."),
    }
    if not intake_receipt_path.is_file():
        doc["provenance_state"] = "ASSET_NOT_STAGED"
        doc["detail"] = "intake receipt not found"
        return doc
    doc["intake_receipt_sha256"] = sha256_file(intake_receipt_path)
    try:
        receipt = json.loads(intake_receipt_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        doc["provenance_state"] = "CONTENT_INVALID"
        doc["detail"] = f"intake receipt unreadable: {exc}"
        return doc
    verdict = receipt.get("validator_result")
    doc["canonical_mpl_validator_verdict"] = verdict
    doc["input_sha256"] = receipt.get("input_sha256")
    doc["staged_sha256"] = receipt.get("staged_sha256")
    doc["staging_destination"] = receipt.get("destination")
    if verdict != "ACCEPTED_CANONICAL_MPL_2_0":
        doc["provenance_state"] = "CONTENT_INVALID"
        doc["detail"] = f"I2Q intake verdict: {verdict!r}"
        return doc
    if not receipt.get("staged"):
        doc["provenance_state"] = "ASSET_NOT_STAGED"
        doc["detail"] = "I2Q intake was a dry-run; asset was never staged"
        return doc
    staged_path = receipt.get("staged_path")
    staged_sha = receipt.get("staged_sha256")
    if not staged_path or not Path(staged_path).is_file():
        doc["provenance_state"] = "ASSET_NOT_STAGED"
        doc["detail"] = "staged asset file no longer present"
        return doc
    actual = sha256_file(Path(staged_path))
    if actual != staged_sha:
        doc["provenance_state"] = "HASH_MISMATCH"
        doc["detail"] = (
            f"staged bytes changed after intake (recorded {staged_sha}, "
            f"actual {actual})")
        return doc
    # Receipt tamper check: the recorded input hash must match the original
    # input file bytes if that file is still reachable at its recorded path.
    input_path = receipt.get("input_path")
    if input_path and Path(input_path).is_file():
        if sha256_file(Path(input_path)) != receipt.get("input_sha256"):
            doc["provenance_state"] = "HASH_MISMATCH"
            doc["detail"] = (
                "intake receipt tampering detected: recorded input hash does "
                "not match the supplied source file bytes")
            return doc
    if receipt.get("component") != REQUIRED_BINDING["component"] or \
            receipt.get("version") != REQUIRED_BINDING["version"]:
        doc["provenance_state"] = "CONTENT_INVALID"
        doc["detail"] = "component/version binding mismatch in intake receipt"
        return doc
    # Content and hashes are sound; the only remaining question is whether
    # the caller documented the source.
    if not source_description or not str(source_description).strip():
        doc["provenance_state"] = "SOURCE_REFERENCE_MISSING"
        doc["detail"] = (
            "caller-supplied source description is required for the "
            "provenance chain; supply it, do not invent a URI or publisher")
        return doc
    doc["provenance_state"] = "PROVENANCE_READY"
    doc["detail"] = "asset validated, staged, hash-bound, and provenance recorded"
    return doc


def provenance_handoff(
    provenance: Dict[str, Any],
) -> Dict[str, Any]:
    """Read-only adapter for I2R/I2O. Never writes or promotes anything."""
    return {
        "schema": "amca-mpl-provenance-handoff/1",
        "asset_state": provenance.get("provenance_state"),
        "component": provenance.get("component"),
        "component_version": provenance.get("component_version"),
        "validated_mpl_asset_sha256": (
            provenance.get("staged_sha256")
            if provenance.get("provenance_state") == "PROVENANCE_READY"
            else None),
        "provenance_receipt_sha256": provenance.get("intake_receipt_sha256"),
        "staging_destination": provenance.get("staging_destination"),
        "source_reference": provenance.get("supplied_source_uri")
        or provenance.get("supplied_source_description"),
        "read_only": True,
    }


def validate_provenance(provenance: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Deterministic validation of a provenance document."""
    required = ["schema", "component", "component_version", "input_sha256",
                "staged_sha256", "canonical_mpl_validator_verdict",
                "provenance_state", "intake_receipt_sha256"]
    missing = [f for f in required if f not in provenance]
    if missing:
        return "BLOCKED", [f"missing provenance field: {f}" for f in missing]
    if provenance["schema"] != SCHEMA:
        return "BLOCKED", [f"unsupported schema: {provenance['schema']!r}"]
    if (provenance["component"], provenance["component_version"]) != (
            REQUIRED_BINDING["component"], REQUIRED_BINDING["version"]):
        return "CONTENT_INVALID", ["component/version binding mismatch"]
    if "legal_approval" in provenance or "LEGAL_APPROVED" in json.dumps(
            provenance):
        return "CONTENT_INVALID", ["fabricated legal-approval field present"]
    state = provenance["provenance_state"]
    if state == "PROVENANCE_READY":
        return "PASS", ["provenance chain complete for the MPL asset"]
    if state == "SOURCE_REFERENCE_MISSING":
        return "BLOCKED", ["source reference missing; asset provenance "
                           "cannot be completed without it"]
    if state == "HASH_MISMATCH":
        return "FAIL", ["staged asset bytes changed after intake"]
    return "BLOCKED", [f"provenance state: {state}"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MPL release asset provenance receipt (I2T)")
    parser.add_argument("--intake-receipt", required=True,
                        help="I2Q intake receipt JSON")
    parser.add_argument("--source-description", default=None,
                        help="Caller-supplied source description")
    parser.add_argument("--source-uri", default=None,
                        help="Caller-supplied source URI/reference (optional)")
    parser.add_argument("--original-filename", default=None)
    parser.add_argument("--supplied-timestamp", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    doc = build_provenance(
        Path(args.intake_receipt), args.source_description,
        source_uri=args.source_uri,
        original_filename=args.original_filename,
        supplied_timestamp=args.supplied_timestamp)
    rendered = json.dumps(doc, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if doc["provenance_state"] == "PROVENANCE_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
