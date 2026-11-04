#!/usr/bin/env python3
"""I2I final-release orchestration rehearsal runner (ZCode).

Read-only dry-run: locates, parses, SHA-binds, and reports every mandatory
release-evidence family. Never copies or promotes anything.

Usage:
  python scripts/run_final_release_orchestration_rehearsal.py [--output PATH]
      [--source-sha SHA] [--signing-policy OPTIONAL|REQUIRED]

Exit code 0 when every family is READY (fully qualified for the final run);
non-zero while any family is PENDING/STALE/MISMATCH/BLOCKED.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.orchestration_rehearsal import rehearse_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Final-release orchestration rehearsal (read-only)")
    parser.add_argument("--source-sha", default=None)
    parser.add_argument("--signing-policy", default="OPTIONAL",
                        choices=["OPTIONAL", "REQUIRED"])
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = rehearse_evidence(source_sha=args.source_sha,
                               signing_policy=args.signing_policy)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    summary = result["summary"]
    ready = not (summary["pending"] or summary["stale"]
                 or summary["mismatch"] or summary["blocked"])
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
