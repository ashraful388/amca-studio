#!/usr/bin/env python3
"""I2G Windows signing audit runner (ZCode).

Inspects the real distribution artifacts' Authenticode signatures and emits
a machine-readable audit (PASS / REVIEW / BLOCKED). Exit 0 on PASS only.

Usage:
  python scripts/run_windows_signing_audit.py [--dist DIR] [--version 0.9.0]
      [--signing-configured] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.signing_readiness import run_signing_audit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AMCA Windows signing audit")
    parser.add_argument("--dist", default=None, help="Distribution directory")
    parser.add_argument("--version", default="0.9.0")
    parser.add_argument("--signing-configured", action="store_true",
                        help="Declare that signing is configured/required for release")
    parser.add_argument("--output", default=None, help="Write JSON result here")
    args = parser.parse_args()

    result = run_signing_audit(
        dist_dir=Path(args.dist) if args.dist else None,
        version=args.version,
        signing_configured=args.signing_configured,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
