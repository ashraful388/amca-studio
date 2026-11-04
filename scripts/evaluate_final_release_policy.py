#!/usr/bin/env python3
"""I2J final-release policy evaluator (ZCode). Read-only.

Answers: "Are all non-technical release decisions explicitly resolved?"
Copies, builds, signs, tags, and promotes nothing.

Usage:
  python scripts/evaluate_final_release_policy.py --decision policy.json
  python scripts/evaluate_final_release_policy.py                 (no decision:
      reports DECISION_REQUIRED with every undecided item)

Exit code 0 only on POLICY_READY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.release_policy import (  # noqa: E402
    RELEASE_DESTINATION,
    ReleasePolicyDecision,
    evaluate_policy,
    separate_technical_from_policy,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Final-release policy evaluator (read-only)")
    parser.add_argument("--decision", default=None,
                        help="Path to a policy decision JSON file")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    decision = None
    if args.decision and Path(args.decision).is_file():
        raw = json.loads(Path(args.decision).read_text(encoding="utf-8"))
        decision = ReleasePolicyDecision(
            release_version=raw.get("release_version"),
            target_source_sha=raw.get("target_source_sha"),
            signing_policy=raw.get("signing_policy"),
            unsigned_disclosure_acknowledged=raw.get(
                "unsigned_disclosure_acknowledged"),
            compliance_review_acknowledged=raw.get(
                "compliance_review_acknowledged"),
            linux_exclusion_acknowledged=raw.get(
                "linux_exclusion_acknowledged"),
            overwrite_authorized=raw.get("overwrite_authorized"),
            destination=raw.get("destination", RELEASE_DESTINATION),
            decision_timestamp_utc=raw.get("decision_timestamp_utc"),
            decision_provenance=raw.get("decision_provenance", "policy file"),
        )

    outcome = evaluate_policy(decision)
    document = outcome.to_document(decision)
    document["separation"] = separate_technical_from_policy([], outcome)
    rendered = json.dumps(document, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if outcome.overall_policy_state == "POLICY_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
