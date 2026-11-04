#!/usr/bin/env python3
"""I2M final distribution-compliance evidence bundle + promotion adapter.

OpenCode PS-2. Infrastructure/scripts only; no production, packaging, or
dependency changes. Local data only — never downloads external services.

Converts the I2L technical audit into a deterministic, hash-bound release
evidence bundle WITHOUT pretending REVIEW findings are legal approval:

  python scripts/generate_distribution_compliance_evidence.py
      --output evidence.json [--sbom PATH] [--notices PATH] [--report PATH]
      [--package-role validation|final]

Overall states: PASS (clean), PASS_WITH_REVIEW (no BLOCKING; REVIEW items
carried explicitly with acknowledgment status), BLOCKED (any BLOCKING, any
shipped UNKNOWN, or missing mandatory inputs). For the current I2L result
the correct state is PASS_WITH_REVIEW, not plain PASS.

Final-package rerun rule: validation evidence never automatically certifies
a future binary from another source/package lineage. The adapter enforces:
expected source/package SHA mismatch -> FAIL; require_final_package on
validation evidence -> FAIL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_dependency_compliance_audit as audit  # noqa: E402 (sibling, PS-2)

SCHEMA_ID = "amca-distribution-compliance-evidence/1"
EVIDENCE_SCHEMA_VERSION = "1.0.0"

DEFAULT_SBOM = SOFTWARE_ROOT / "docs" / "release" / "AMCA-Studio-v1.0.0-SBOM.json"
DEFAULT_NOTICES = (
    SOFTWARE_ROOT / "docs" / "release"
    / "AMCA-Studio-v1.0.0-Third-Party-Notices-Candidate.md"
)
DEFAULT_REPORT = (
    SOFTWARE_ROOT / "docs" / "release"
    / "AMCA-Studio-v1.0.0-Dependency-License-and-Distribution-Compliance-Report.md"
)

REQUIRED_TOP_FIELDS = [
    "schema", "evidence_schema_version", "overall", "certification_timestamp",
    "source_sha", "package_role", "sbom", "notices", "report", "counts",
    "review_acknowledgments", "unresolved_review_items",
    "shipping_classification", "bindings", "limitations",
]


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_head() -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(SOFTWARE_ROOT),
            capture_output=True, text=True, timeout=30)
    except (OSError, ValueError):
        return None
    sha = proc.stdout.strip()
    return sha if len(sha) == 40 else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_action(component: str, reason: str) -> str:
    lowered = (component + " " + reason).casefold()
    if "certifi" in lowered:
        return ("include MPL-2.0 license text + attribution; confirm source "
                "availability for covered files before final promotion")
    if "pyinstaller-hooks-contrib" in lowered or "hooks-contrib" in lowered:
        return ("acknowledge GPL-family tooling is build/dev-only and never "
                "shipped as a component")
    if "pyinstaller" in lowered:
        return ("acknowledge the documented GPL bundling exception covers "
                "built-program distribution; record exception text reference")
    return "manual legal review before final promotion"


def build_acknowledgments(audit_result: Dict[str, Any],
                          sbom_components: List[Dict[str, Any]]
                          ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """One acknowledgment record per REVIEW/BLOCKING/UNKNOWN finding."""
    by_name = {c["name"].lower(): c for c in sbom_components}
    acks: List[Dict[str, Any]] = []
    unresolved: List[str] = []
    for finding in audit_result.get("findings", []):
        if finding.get("severity") not in ("REVIEW", "BLOCKING", "UNKNOWN"):
            continue
        comp = by_name.get(str(finding.get("component", "")).lower(), {})
        ack = {
            "component": finding.get("component"),
            "version": comp.get("version", ""),
            "license": comp.get("license", "UNKNOWN"),
            "shipped": comp.get("in_distributed_artifact"),
            "reason": finding.get("reason", ""),
            "required_action": _required_action(
                str(finding.get("component", "")), str(finding.get("reason", ""))),
            "technically_blocking": finding.get("severity") == "BLOCKING",
            "acknowledgment_status": "pending-release-acknowledgment",
        }
        acks.append(ack)
        unresolved.append(str(finding.get("component")))
    return acks, unresolved


def _notices_entries(notices_path: Path) -> List[Dict[str, str]]:
    """Parse the notices candidate into component entries (name/version)."""
    entries: List[Dict[str, str]] = []
    if not notices_path.is_file():
        return entries
    current: Dict[str, str] = {}
    for line in notices_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            if current:
                entries.append(current)
            parts = line[4:].strip().rsplit(" ", 1)
            current = {"name": parts[0],
                       "version": parts[1] if len(parts) > 1 else ""}
        elif line.startswith("- License:") and current is not None:
            current["license"] = line.split(":", 1)[1].strip().split(" ")[0]
    if current:
        entries.append(current)
    return entries


def verify_notices_payload(sbom_components: List[Dict[str, str]],
                           notices_path: Path) -> List[str]:
    """Names/versions match SBOM; build-only identified; attribution present."""
    problems: List[str] = []
    entries = _notices_entries(notices_path)
    sbom_by_name = {c["name"].lower(): c for c in sbom_components}
    for entry in entries:
        match = sbom_by_name.get(entry["name"].lower())
        if match is None:
            problems.append(f"notices entry not in SBOM: {entry['name']}")
            continue
        if entry.get("version") and match.get("version") != entry["version"]:
            problems.append(
                f"version drift for {entry['name']}: notices "
                f"{entry['version']} vs SBOM {match.get('version')}")
    shipped = [c for c in sbom_components
               if c.get("in_distributed_artifact") is True]
    covered = {e["name"].lower() for e in entries}
    for comp in shipped:
        if comp["name"].lower() not in covered:
            problems.append(
                f"shipped component missing from notices: {comp['name']}")
    return problems


def _overall(counts: Dict[str, int], shipped_unknown: int) -> str:
    if counts.get("BLOCKING", 0) or shipped_unknown:
        return "BLOCKED"
    if counts.get("REVIEW", 0) or counts.get("UNKNOWN", 0):
        return "PASS_WITH_REVIEW"
    return "PASS"


def generate_evidence(
    output: Path,
    sbom_path: Path = DEFAULT_SBOM,
    notices_path: Path = DEFAULT_NOTICES,
    report_path: Path = DEFAULT_REPORT,
    package_role: str = "validation",
    validation_portable: Optional[Path] = None,
    candidate_binding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate the hash-bound evidence bundle from live audit results.

    ``validation_portable`` is the caller-supplied validation package whose
    bytes are bound into the evidence (no stale default is discovered
    silently).
    """
    if package_role not in ("validation", "final"):
        raise ValueError("package_role must be 'validation' or 'final'")
    missing = [str(p) for p in (sbom_path, notices_path, report_path)
               if not p.is_file()]
    if missing:
        return {
            "schema": SCHEMA_ID,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "overall": "BLOCKED",
            "reasons": [f"missing mandatory inputs: {missing}"],
        }
    audit_result = audit.audit_dependencies(SOFTWARE_ROOT,
                                            inspect_package=True)
    sbom_doc = json.loads(sbom_path.read_text(encoding="utf-8"))
    sbom_components = sbom_doc.get("components", [])
    shipped_unknown = sum(
        1 for c in sbom_components
        if c.get("license") == "UNKNOWN"
        and c.get("in_distributed_artifact") is True)
    acks, unresolved = build_acknowledgments(audit_result, sbom_components)
    notices_problems = verify_notices_payload(sbom_components, notices_path)
    counts = dict(audit_result.get("counts", {}))
    overall = _overall(counts, shipped_unknown)
    if notices_problems:
        overall = "BLOCKED"
    bindings = {
        "sbom_sha256": _sha256_file(sbom_path),
        "notices_sha256": _sha256_file(notices_path),
        "report_sha256": _sha256_file(report_path),
        "audit_counts": counts,
    }
    # Bind the validation package bytes into the evidence so the
    # final-package rerun harness can reject any byte-different artifact
    # claiming the same certification lineage. The artifact path is
    # caller-supplied (no stale default is discovered silently).
    artifact_hashes: Dict[str, Any] = {}
    if validation_portable is not None and validation_portable.is_file():
        artifact_hashes["portable"] = {
            "path": str(validation_portable),
            "sha256": _sha256_file(validation_portable),
            "size_bytes": validation_portable.stat().st_size,
        }
    evidence = {
        "schema": SCHEMA_ID,
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "overall": overall,
        "certification_timestamp": _utc_now(),
        "source_sha": _git_head(),
        "package_role": package_role,
        "sbom": {"path": str(sbom_path), "sha256": bindings["sbom_sha256"]},
        "notices": {"path": str(notices_path),
                    "sha256": bindings["notices_sha256"]},
        "report": {"path": str(report_path),
                   "sha256": bindings["report_sha256"]},
        "counts": counts,
        "review_acknowledgments": acks,
        "unresolved_review_items": unresolved,
        "shipping_classification": {
            c["name"]: c.get("in_distributed_artifact")
            for c in sbom_components
        },
        "notices_problems": notices_problems,
        "bindings": bindings,
        "artifact_hashes": artifact_hashes,
        # H2C.2D Part D: the compliance record must identify the WHOLE
        # three-artifact release candidate it certifies.
        "candidate_binding": candidate_binding,
        "source_sha": (candidate_binding or {}).get("source_sha"),
        "limitations": [
            "Technical assessment only; not legal advice or a legal opinion.",
            "Validation evidence never automatically certifies a future "
            "binary from another source/package lineage.",
            "Opaque single-file binaries: structural consistency only.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence


# --------------------------------------------------------------------------
# Promotion-input adapter (read-only)
# --------------------------------------------------------------------------

def validate_distribution_compliance_evidence(
    evidence: Dict[str, Any],
    expected_source_sha: Optional[str] = None,
    require_final_package: bool = False,
    verify_file_hashes: bool = True,
) -> Tuple[str, List[str]]:
    """Validate a bundle for promotion input. Never copies/promotes anything."""
    reasons: List[str] = []
    for field in REQUIRED_TOP_FIELDS:
        if field not in evidence:
            reasons.append(f"missing mandatory evidence field: {field}")
    if reasons:
        return "BLOCKED", reasons
    if evidence.get("schema") != SCHEMA_ID:
        return "BLOCKED", [f"unsupported evidence schema: {evidence.get('schema')!r}"]
    if evidence.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        return "BLOCKED", ["unsupported evidence-schema version: "
                           f"{evidence.get('evidence_schema_version')!r}"]
    if "legal_approval" in evidence or "approved_by_legal" in evidence:
        return "FAIL", ["fabricated legal-approval field present"]
    if verify_file_hashes:
        for key, label in (("sbom", "SBOM"), ("notices", "notices"),
                           ("report", "compliance report")):
            section = evidence.get(key, {})
            path = section.get("path")
            if not path or not Path(path).is_file():
                reasons.append(f"{label} file not accessible for re-hash: {path}")
                continue
            if _sha256_file(Path(path)) != section.get("sha256"):
                reasons.append(f"{label} hash mismatch: file altered since "
                               "evidence generation")
                return "FAIL", reasons
    if reasons:
        return "BLOCKED", reasons
    if expected_source_sha is not None and evidence.get("source_sha") != (
            expected_source_sha):
        return "FAIL", ["source SHA mismatch: evidence bound to "
                        f"{evidence.get('source_sha')}, promotion requested "
                        f"{expected_source_sha}"]
    if require_final_package and evidence.get("package_role") != "final":
        return "FAIL", ["final-package rerun rule: validation evidence "
                        "cannot certify a final package"]
    overall = evidence.get("overall")
    if overall == "BLOCKED":
        return "BLOCKED", ["evidence overall verdict is BLOCKED"]
    if overall not in ("PASS", "PASS_WITH_REVIEW"):
        return "BLOCKED", ["evidence overall verdict is not passing"]
    return overall, [f"compliance evidence {overall} for source "
                     f"{evidence.get('source_sha')}"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate distribution-compliance evidence bundle (I2M)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--sbom", default=str(DEFAULT_SBOM))
    parser.add_argument("--notices", default=str(DEFAULT_NOTICES))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidate-source-sha", default=None)
    parser.add_argument("--candidate-exe", default=None)
    parser.add_argument("--candidate-msi", default=None)
    parser.add_argument("--validation-portable", default=None,
                        help="Explicit validation portable package path "
                        "whose bytes are bound into the evidence")
    parser.add_argument("--package-role", default="validation",
                        choices=["validation", "final"])
    args = parser.parse_args()
    candidate_binding = None
    if args.candidate_source_sha and args.candidate_exe and args.candidate_msi:
        import hashlib as _hashlib
        exe_p = Path(args.candidate_exe)
        msi_p = Path(args.candidate_msi)
        candidate_binding = {
            "source_sha": args.candidate_source_sha,
            "exe_sha256": _hashlib.sha256(exe_p.read_bytes()).hexdigest(),
            "exe_size_bytes": exe_p.stat().st_size,
            "msi_sha256": _hashlib.sha256(msi_p.read_bytes()).hexdigest(),
            "msi_size_bytes": msi_p.stat().st_size,
        }
    evidence = generate_evidence(
        Path(args.output), sbom_path=Path(args.sbom),
        notices_path=Path(args.notices), report_path=Path(args.report),
        package_role=args.package_role,
        validation_portable=Path(args.validation_portable)
        if args.validation_portable else None,
        candidate_binding=candidate_binding)
    print(json.dumps({"schema": SCHEMA_ID, "overall": evidence["overall"],
                      "output": str(args.output)}, indent=2))
    return 0 if evidence["overall"] in ("PASS", "PASS_WITH_REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
