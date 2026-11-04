#!/usr/bin/env python3
"""I2K final policy-to-promotion decision matrix runner (ZCode). Read-only.

Exercises the required decision scenarios (A-F) through the I2J policy
contract combined with technical/overwrite layers, without modifying any
component. Emits a machine-readable matrix. Exit 0 when every scenario
matches its expected outcome.

Usage:
  python scripts/run_final_release_policy_promotion_matrix.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.policy_promotion_matrix import run_decision_matrix  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Final policy-to-promotion decision matrix (read-only)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = run_decision_matrix()
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["expected_outcomes_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
