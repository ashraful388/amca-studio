#!/usr/bin/env python3
"""I2L version transition executor CLI (ZCode).

Deterministic, atomic 0.9.0 -> 1.0.0 transition. Default dry-run; write mode
requires --apply AND --authorize-bump (the Manager-authorized step on the
final pre-bump canonical SHA).

Exact command after the final pre-bump canonical SHA is approved:
  python scripts/apply_release_version_transition.py \\
      --expected-source-sha <full pre-bump SHA> --target 1.0.0 --apply --authorize-bump

Usage:
  python scripts/apply_release_version_transition.py [--target 1.0.0]
      [--start 0.9.0] [--expected-source-sha SHA] [--apply] [--authorize-bump]
      [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.version_transition import apply_transition  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic version transition executor")
    parser.add_argument("--target", default="1.0.0")
    parser.add_argument("--start", default="0.9.0")
    parser.add_argument("--expected-source-sha", default=None,
                        help="Pre-bump canonical SHA the tree must be at")
    parser.add_argument("--apply", action="store_true",
                        help="Perform the atomic write (default: dry-run)")
    parser.add_argument("--authorize-bump", action="store_true",
                        help="Required alongside --apply; the Manager-authorized bump")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if args.apply and not args.authorize_bump:
        result = {
            "schema": "amca-version-transition-plan/1",
            "overall": "BLOCKED",
            "reasons": [
                "write mode requires --authorize-bump (Manager-authorized "
                "bump on the final pre-bump canonical SHA)"
            ],
        }
        rendered = json.dumps(result, indent=2)
        print(rendered)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        return 1

    result = apply_transition(
        target=args.target,
        start_version=args.start,
        expected_source_sha=args.expected_source_sha,
        apply=args.apply,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    overall = result["overall"]
    return 0 if overall in ("DRY_RUN", "APPLIED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
