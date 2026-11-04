#!/usr/bin/env python3
"""I2D distribution runtime smoke runner (ZCode).

Runs the Windows runtime smoke for genuine installer/portable artifacts.

Usage:
  python scripts/run_distribution_smoke.py --artifact PATH [--mode auto|installer|portable]
                                           [--workspace DIR] [--output PATH]
                                           [--manifest PATH]

Emits a machine-readable JSON result (PASS/FAIL/BLOCKED) to stdout and,
optionally, --output PATH. Exit code 0 only on overall PASS.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.distribution_smoke import run_smoke  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AMCA distribution runtime smoke")
    parser.add_argument("--artifact", required=True, help="Path to setup .exe or portable .zip")
    parser.add_argument("--mode", default="auto", choices=["auto", "installer", "portable"])
    parser.add_argument("--workspace", default=None, help="Optional disposable workspace dir")
    parser.add_argument("--manifest", default=None, help="Release manifest for provenance")
    parser.add_argument("--output", default=None, help="Write JSON result to this path")
    args = parser.parse_args()

    artifact = Path(args.artifact)
    if not artifact.is_file():
        print(json.dumps({
            "schema": "amca-distribution-smoke-result/1",
            "overall": "FAIL",
            "reasons": [f"artifact not found: {artifact}"],
        }, indent=2))
        return 1

    result = run_smoke(
        artifact,
        mode=args.mode,
        workspace=Path(args.workspace) if args.workspace else None,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
