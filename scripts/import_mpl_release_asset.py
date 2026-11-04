#!/usr/bin/env python3
"""I2Q external MPL asset intake, validation, and staging adapter.

OpenCode PS-2. Release compliance tooling only. No fabricated license
content; the authoritative text must come from an external/official source.

  python scripts/import_mpl_release_asset.py --input PATH
      [--component certifi] [--version 2026.7.22] [--stage] [--overwrite]
      [--receipt PATH]

Behavior:
  - validates the supplied file against I2P canonical MPL-2.0 rules
    (all 12 section anchors incl. "3. Infringement Waiver");
  - rejects placeholders/partial/non-canonical renderings;
  - requires explicit component/version binding (--component/--version
    must match the shipped certifi exactly);
  - defaults to DRY-RUN: nothing is written without --stage;
  - with --stage, copies the file byte-exactly to the I2P asset
    destination (software/docs/release/assets/MPL-2.0-<component>-<version>.txt),
    refusing to overwrite unless --overwrite is explicit;
  - never modifies the supplied source file;
  - emits a machine-readable receipt (input/staged SHA256, component,
    version, validator result, destination, timestamp) with NO
    legal-approval field.

Exit codes: 0 staged/valid, 2 validation rejected, 3 staging refused
(would overwrite / destination conflict), 4 I/O error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_mpl_notice_readiness as readiness  # noqa: E402 (sibling, PS-2)

SCHEMA = "amca-mpl-asset-intake-receipt/1"

REQUIRED_BINDING = {"component": "certifi", "version": "2026.7.22"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_binding(component: str, version: str) -> List[str]:
    problems: List[str] = []
    if component != REQUIRED_BINDING["component"]:
        problems.append(
            f"component binding mismatch: expected "
            f"{REQUIRED_BINDING['component']!r}, got {component!r}")
    if version != REQUIRED_BINDING["version"]:
        problems.append(
            f"version binding mismatch: expected "
            f"{REQUIRED_BINDING['version']!r}, got {version!r}")
    return problems


def intake(
    input_path: Path,
    component: str = REQUIRED_BINDING["component"],
    version: str = REQUIRED_BINDING["version"],
    stage: bool = False,
    overwrite: bool = False,
    destination: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate and (optionally) stage the external MPL asset. Read-only
    on the source file; writes only the staged destination with --stage.

    ``destination`` overrides the canonical asset path — used by test
    isolation so tests never write the real canonical asset location.
    """
    receipt: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_path": str(input_path),
        "input_sha256": None,
        "staged": False,
        "staged_path": None,
        "staged_sha256": None,
        "component": component,
        "version": version,
        "validator_result": None,
        "binding_result": "MISMATCH",
        "dry_run": not stage,
        "destination": None,
    }
    if not input_path.is_file():
        receipt["validator_result"] = "INPUT_NOT_FOUND"
        receipt["error"] = f"input file not found: {input_path}"
        return receipt
    receipt["input_sha256"] = sha256_file(input_path)
    try:
        text = input_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, ValueError) as exc:
        receipt["validator_result"] = "UNREADABLE"
        receipt["error"] = f"input file unreadable as UTF-8 text: {exc}"
        return receipt
    canonical, missing = readiness.is_authoritative_mpl(text)
    if not canonical:
        receipt["validator_result"] = "REJECTED_NON_CANONICAL"
        receipt["missing_sections"] = missing
        return receipt
    # Placeholder/partial rejection: canonical anchors must not be padded
    # with placeholder markers.
    lowered = text.casefold()
    if "placeholder" in lowered or "insert license text here" in lowered:
        receipt["validator_result"] = "REJECTED_PLACEHOLDER"
        return receipt
    binding_problems = validate_binding(component, version)
    if binding_problems:
        receipt["validator_result"] = "REJECTED_BINDING"
        receipt["binding_problems"] = binding_problems
        return receipt
    receipt["binding_result"] = "OK"
    receipt["validator_result"] = "ACCEPTED_CANONICAL_MPL_2_0"

    destination = destination or (
        SOFTWARE_ROOT / "docs" / "release" / "assets" /
        f"MPL-2.0-{component}-{version}.txt")
    receipt["destination"] = str(destination)
    if not stage:
        return receipt
    if destination.exists():
        existing = sha256_file(destination)
        if existing == receipt["input_sha256"]:
            receipt["staged"] = True
            receipt["staged_path"] = str(destination)
            receipt["staged_sha256"] = existing
            receipt["note"] = ("destination already contains identical "
                               "bytes; handled safely, no rewrite")
            return receipt
        if not overwrite:
            receipt["validator_result"] = "STAGING_REFUSED"
            receipt["error"] = (
                f"destination exists with different bytes: {destination}; "
                "pass --overwrite explicitly to replace")
            return receipt
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, destination)  # byte-exact copy
    staged_sha = sha256_file(destination)
    if staged_sha != receipt["input_sha256"]:
        receipt["staged"] = False
        receipt["validator_result"] = "STAGING_VERIFY_FAILED"
        receipt["error"] = "staged bytes differ from input; refusing"
        return receipt
    receipt["staged"] = True
    receipt["staged_path"] = str(destination)
    receipt["staged_sha256"] = staged_sha
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MPL release asset intake/validation/staging (I2Q)")
    parser.add_argument("--input", required=True,
                        help="Caller-supplied authoritative MPL-2.0 text")
    parser.add_argument("--component", default=REQUIRED_BINDING["component"])
    parser.add_argument("--version", default=REQUIRED_BINDING["version"])
    parser.add_argument("--stage", action="store_true",
                        help="Actually stage the asset (default: dry-run)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Allow replacing an existing staged asset")
    parser.add_argument("--destination", default=None,
                        help="Override the canonical asset destination "
                        "(test-isolation/alternate staging)")
    parser.add_argument("--receipt", default=None,
                        help="Write the JSON receipt to this path")
    args = parser.parse_args()

    receipt = intake(Path(args.input), component=args.component,
                     version=args.version, stage=args.stage,
                     overwrite=args.overwrite,
                     destination=Path(args.destination)
                     if args.destination else None)
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    print(rendered)
    if args.receipt:
        Path(args.receipt).write_text(rendered, encoding="utf-8")
    if receipt.get("validator_result") == "ACCEPTED_CANONICAL_MPL_2_0" and \
            (not args.stage or receipt.get("staged")):
        return 0
    if str(receipt.get("validator_result", "")).startswith("STAGING_REFUSED"):
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
