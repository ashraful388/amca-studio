#!/usr/bin/env python3
"""I2E final release promotion runner (ZCode).

Evaluates the complete release evidence bundle and, only in --promote mode,
copies verified artifacts into the official release destination.

Usage:
  python scripts/promote_final_release.py --dry-run     (default; copies nothing)
  python scripts/promote_final_release.py --promote --allow-overwrite

Exit code 0 only when the evaluated state is READY_FOR_PROMOTION (dry-run)
or PROMOTED (real promotion).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.final_promotion import (  # noqa: E402
    DEFAULT_OFFICIAL_DEST,
    DEFAULT_SHA256SUMS,
    evaluate_evidence,
    promote_final_release,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="AMCA final release promotion gate")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Evaluate evidence without copying (default)")
    parser.add_argument("--promote", action="store_true",
                        help="Perform the real staged promotion (explicit)")
    parser.add_argument("--allow-overwrite", action="store_true",
                        help="Allow replacing existing official artifacts")
    parser.add_argument("--dest", default=str(DEFAULT_OFFICIAL_DEST))
    parser.add_argument("--output", default=None, help="Write evidence JSON here")
    args = parser.parse_args()

    evidence = evaluate_evidence()
    document = evidence.to_document()

    if args.promote:
        dist = SOFTWARE_ROOT / "dist"
        # H2A S3: v1.0.1 lineage inventory — EXE + MSI + Portable (three
        # artifacts) plus the shipping documents. The MSI role is mandatory
        # for v1.0.1+ via mandatory_inventory_for_version.
        artifacts = {
            "windows-installer": dist / "amca-studio-v1.0.1-windows-x64-setup.exe",
            "windows-msi": dist / "amca-studio-v1.0.1-windows-x64.msi",
            "windows-portable": dist / "amca-studio-v1.0.1-windows-x64-portable.zip",
            "sha256sums": dist / "SHA256SUMS.txt",
            "release-manifest": dist / "release_manifest_v1.0.1.json",
            "release-notes": dist / "AMCA-Studio-v1.0.1-Release-Notes.md",
            "known-limitations": dist / "KNOWN-LIMITATIONS.md",
            "certification-summary": dist / "AMCA-Studio-v1.0.1-Release-Certification-Summary.md",
        }
        promotion = promote_final_release(
            evidence,
            artifacts=artifacts,
            destination=Path(args.dest),
            allow_overwrite=args.allow_overwrite,
            dry_run=False,
        )
        document["promotion"] = promotion
        rendered = json.dumps(document, indent=2)
        print(rendered)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        return 0 if promotion["overall"] == "PROMOTED" else 1

    rendered = json.dumps(document, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if document["promotion_state"] == "READY_FOR_PROMOTION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
