#!/usr/bin/env python3
"""I2R PS-2 final release evidence aggregator + post-build validation.

OpenCode PS-2. Evidence aggregation/validation orchestration only; no
production, packaging, promotion, or version changes. Read-only with respect
to product/package files. Invokes existing PS-2 validators rather than
reimplementing them.

  python scripts/run_ps2_final_release_validation.py
      --portable PATH [--installer PATH] [--source-sha SHA]
      [--skip-heavy] [--output PATH]

Aggregates the PS-2 evidence families into amca-ps2-final-release-evidence/1
with per-family states PASS / PASS_WITH_REVIEW / BLOCKED / FAIL / STALE /
PENDING and an aggregate verdict:

  PS2_RELEASE_READY | PS2_RELEASE_READY_WITH_REVIEW |
  PS2_RELEASE_BLOCKED | PS2_RELEASE_FAILED

Rules (no hidden inference):
  - MPL asset absent (I2P RELEASE_ASSET_REQUIRED/BLOCKED) -> aggregate
    PS2_RELEASE_BLOCKED with reason RELEASE_ASSET_REQUIRED.
  - Evidence bound to a different source SHA -> family STALE.
  - Any FAIL family -> PS2_RELEASE_FAILED (FAIL cannot aggregate to ready).
  - Any BLOCKED family (other than via FAIL) -> PS2_RELEASE_BLOCKED
    (BLOCKED cannot aggregate to ready).
  - Known REVIEW items (certifi/MPL-2.0) remain visible;
    PS2_RELEASE_READY_WITH_REVIEW is the best achievable with reviews.
  - No legal-approval field is ever emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

SCHEMA = "amca-ps2-final-release-evidence/1"

RELEASE_DIR = SOFTWARE_ROOT / "docs" / "release"
# NOTE (I2W portability): no default v0.9 package path is hard-coded. The
# portable/installer paths are caller-supplied arguments; when absent, the
# package-compliance family reports PENDING/BLOCKED rather than silently
# selecting stale binaries.
PORTABLE = None
EVIDENCE_COMPLIANCE = RELEASE_DIR / (
    "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json")
EVIDENCE_COMPLIANCE_V101 = RELEASE_DIR / (
    "AMCA-Studio-v1.0.1-Distribution-Compliance-Evidence.json")
# H2C.1: the v1.0.1 candidate binds to the v1.0.1-named compliance
# evidence when present; the v1.0.0-named record is historical and is
# never rewritten. Narrow repair authorized by the H2C.1 order
# (PS-2 inactive; ZCode owns the evidence contract, not its history).
def evidence_compliance_for_version():
    if EVIDENCE_COMPLIANCE_V101.is_file():
        return EVIDENCE_COMPLIANCE_V101
    return EVIDENCE_COMPLIANCE
REPORT_MIGRATION = SOFTWARE_ROOT / "docs" / "integration" / (
    "AMCA-Studio-v1.0.0-Upgrade-Migration-Backup-Restore-Certification-"
    "Report.md")
REPORT_SMOKE = RELEASE_DIR / (
    "AMCA-Studio-v1.0.0-Existing-User-Package-Upgrade-Smoke-Report.md")
REPORT_SANITATION = RELEASE_DIR / (
    "AMCA-Studio-v1.0.0-Privacy-Secrets-and-User-Data-Sanitation-Audit.md")
MANIFEST_DOCS = RELEASE_DIR / "AMCA-Studio-v1.0.0-Documentation-Manifest.json"
SBOM = RELEASE_DIR / "AMCA-Studio-v1.0.0-SBOM.json"
PAYLOAD_MANIFEST = RELEASE_DIR / (
    "AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
# H2C.2E: the v1.0.1 candidate binds to its own payload manifest;
# the v1.0.0-named record is historical and is never rewritten.
PAYLOAD_MANIFEST_V101 = RELEASE_DIR / (
    "AMCA-Studio-v1.0.1-Third-Party-Compliance-Payload-Manifest.json")


def payload_manifest_for_version() -> Path:
    if PAYLOAD_MANIFEST_V101.is_file():
        return PAYLOAD_MANIFEST_V101
    return PAYLOAD_MANIFEST

MIGRATION_SUITE = "test_studio_v100_upgrade_migration_restore_certification.py"

FAMILIES = [
    "migration_upgrade_certification",
    "final_package_migration_smoke",
    "sanitation_privacy_audit",
    "user_documentation_integrity",
    "dependency_sbom_compliance",
    "third_party_payload_completeness",
    "final_package_compliance_verification",
    "mpl_readiness_intake_state",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_pytest_suite(module: str, timeout: int = 900) -> Dict[str, Any]:
    """Invoke an existing PS-2 pytest suite; record the real outcome."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", f"tests/{module}", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(SOFTWARE_ROOT), capture_output=True, text=True,
        timeout=timeout)
    return {
        "module": module,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "tail": (proc.stdout + proc.stderr)[-400:],
    }


def family_from_file(path: Path, source_sha: Optional[str],
                     evidence_source_key: str = "source_sha") -> Dict[str, Any]:
    """Evidence-file family: PENDING if missing, STALE if other lineage."""
    if not path.is_file():
        return {"state": "PENDING", "path": None, "sha256": None,
                "detail": f"evidence file missing: {path}"}
    sha = sha256_file(path)
    state = "PASS"
    detail = "evidence present"
    # Only JSON documents carry a machine-checkable source binding; report
    # files are prose and are hash-bound via the documentation manifest.
    if path.suffix.lower() == ".json":
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            bound = doc.get(evidence_source_key)
            if source_sha and bound and bound != source_sha:
                state = "STALE"
                detail = (f"evidence bound to source {bound}, final build "
                          f"{source_sha}")
        except ValueError as exc:
            state = "FAIL"
            detail = f"evidence unreadable: {exc}"
    return {"state": state, "path": str(path), "sha256": sha,
            "detail": detail}


def check_migration_family(source_sha: Optional[str], heavy: bool
                           ) -> Dict[str, Any]:
    base = family_from_file(REPORT_MIGRATION, source_sha)
    suite: Dict[str, Any] = {}
    if heavy and base["state"] == "PASS":
        suite = run_pytest_suite(MIGRATION_SUITE)
        if not suite["passed"]:
            base["state"] = "FAIL"
            base["detail"] = f"migration suite failed: {suite['tail'][-160:]}"
    base["suite"] = suite
    return base


def check_docs_integrity_family() -> Dict[str, Any]:
    """Documentation manifest hash integrity (I2H/I2N lineage)."""
    if not MANIFEST_DOCS.is_file():
        return {"state": "PENDING", "path": str(MANIFEST_DOCS),
                "sha256": None, "detail": "documentation manifest missing"}
    sha = sha256_file(MANIFEST_DOCS)
    try:
        manifest = json.loads(MANIFEST_DOCS.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"state": "FAIL", "path": str(MANIFEST_DOCS), "sha256": sha,
                "detail": f"manifest unreadable: {exc}"}
    problems: List[str] = []
    checked = 0
    for entry in manifest.get("documents", []):
        doc_path = RELEASE_DIR / entry.get("document", "")
        if not doc_path.is_file():
            problems.append(f"manifested doc missing: {entry.get('document')}")
            continue
        checked += 1
        if sha256_file(doc_path) != entry.get("sha256"):
            problems.append(
                f"documentation hash mismatch: {entry.get('document')}")
    if any("hash mismatch" in p for p in problems):
        return {"state": "FAIL", "path": str(MANIFEST_DOCS), "sha256": sha,
                "detail": "; ".join(problems[:5]), "docs_checked": checked}
    if problems:
        return {"state": "BLOCKED", "path": str(MANIFEST_DOCS), "sha256": sha,
                "detail": "; ".join(problems[:5]), "docs_checked": checked}
    return {"state": "PASS", "path": str(MANIFEST_DOCS), "sha256": sha,
            "detail": f"{checked} documents hash-verified",
            "docs_checked": checked}


def check_payload_completeness_family() -> Dict[str, Any]:
    """Third-party payload manifest: certifi entry + review states visible."""
    PAYLOAD_MANIFEST = payload_manifest_for_version()
    if not PAYLOAD_MANIFEST.is_file():
        return {"state": "PENDING", "path": str(PAYLOAD_MANIFEST),
                "sha256": None, "detail": "payload manifest missing"}
    sha = sha256_file(PAYLOAD_MANIFEST)
    try:
        payload = json.loads(PAYLOAD_MANIFEST.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"state": "FAIL", "path": str(PAYLOAD_MANIFEST), "sha256": sha,
                "detail": f"payload manifest unreadable: {exc}"}
    items = payload.get("items", [])
    certifi = next((i for i in items
                    if i.get("component") == "certifi"), None)
    if certifi is None:
        return {"state": "FAIL", "path": str(PAYLOAD_MANIFEST), "sha256": sha,
                "detail": "certifi payload entry missing"}
    review_items = [i.get("component") for i in items
                    if i.get("classification") == "review_remains"]
    return {"state": "PASS_WITH_REVIEW" if review_items else "PASS",
            "path": str(PAYLOAD_MANIFEST), "sha256": sha,
            "detail": "payload manifest complete",
            "review_items": review_items}


def check_mpl_family() -> Dict[str, Any]:
    """I2P readiness: RELEASE_ASSET_REQUIRED blocks release (per contract)."""
    try:
        mr = _load("mpl_readiness_i2r",
                   SCRIPTS / "run_mpl_notice_readiness.py")
    except Exception as exc:  # noqa: BLE001
        return {"state": "BLOCKED", "path": None, "sha256": None,
                "detail": f"readiness validator unavailable: {exc}"}
    doc = mr.evaluate()
    state_map = {"READY": "PASS", "RELEASE_ASSET_REQUIRED": "BLOCKED",
                 "MISMATCH": "FAIL", "BLOCKED": "BLOCKED"}
    family: Dict[str, Any] = {
        "state": state_map.get(doc.get("state"), "BLOCKED"),
        "path": (doc.get("asset") or {}).get("path"),
        "sha256": (doc.get("asset") or {}).get("sha256"),
        "detail": doc.get("state"),
    }
    if doc.get("state") == "RELEASE_ASSET_REQUIRED":
        family["release_reason"] = "RELEASE_ASSET_REQUIRED"
    return family


def check_package_compliance_family(
    portable: Optional[Path], installer: Optional[Path],
    source_sha: Optional[str], heavy: bool,
    msi: Optional[Path] = None,
) -> Dict[str, Any]:
    """I2O verification: exact-byte/source binding + payload enforcement."""
    EVIDENCE_COMPLIANCE = evidence_compliance_for_version()
    if not EVIDENCE_COMPLIANCE.is_file():
        return {"state": "PENDING", "path": str(EVIDENCE_COMPLIANCE),
                "sha256": None, "detail": "compliance evidence missing"}
    sha = sha256_file(EVIDENCE_COMPLIANCE)
    try:
        fv = _load("final_pkg_verify_i2r",
                   SCRIPTS / "run_final_package_compliance_verification.py")
    except Exception as exc:  # noqa: BLE001
        return {"state": "BLOCKED", "path": str(EVIDENCE_COMPLIANCE),
                "sha256": sha, "detail": f"validator unavailable: {exc}"}
    effective_portable = portable
    result = fv.run_verification(
        effective_portable, installer, source_sha,
        evidence_path=EVIDENCE_COMPLIANCE, msi=msi)
    state_map = {"PASS": "PASS", "PASS_WITH_REVIEW": "PASS_WITH_REVIEW",
                 "BLOCKED": "BLOCKED", "FAIL": "FAIL"}
    state = state_map.get(result["overall"], "FAIL")
    # Heavy mode: re-verify by running the I2O test suite for depth.
    suite: Dict[str, Any] = {}
    if heavy and state in ("PASS", "PASS_WITH_REVIEW"):
        suite = run_pytest_suite(
            "test_studio_v100_final_package_compliance_verification.py")
        if not suite["passed"]:
            state = "FAIL"
    return {"state": state, "path": str(EVIDENCE_COMPLIANCE), "sha256": sha,
            "detail": f"I2O verdict {result['overall']}",
            "suite": suite,
            "review_items": result.get("review_items", []),
            "fail_findings": result.get("fail_findings", []),
            "blocked_findings": result.get("blocked_findings", [])}


def aggregate(families: Dict[str, Dict[str, Any]]) -> str:
    states = [families[f]["state"] for f in FAMILIES]
    if any(s == "FAIL" for s in states):
        return "PS2_RELEASE_FAILED"
    # H2C.2A Part C: STALE fails closed for release-critical evidence —
    # stale-bound evidence must never surface as READY_WITH_REVIEW.
    if any(s == "STALE" for s in states):
        return "PS2_RELEASE_BLOCKED"
    if any(s == "BLOCKED" for s in states):
        return "PS2_RELEASE_BLOCKED"
    if any(s in ("PASS_WITH_REVIEW", "PENDING") for s in states):
        return "PS2_RELEASE_READY_WITH_REVIEW"
    return "PS2_RELEASE_READY"


def build_aggregate(
    portable: Optional[Path] = None,
    installer: Optional[Path] = None,
    source_sha: Optional[str] = None,
    heavy: bool = False,
    msi: Optional[Path] = None,
    release_version: str = "1.0.1",
) -> Dict[str, Any]:
    """Run all families read-only and produce the aggregate evidence doc.

    H2C.2E Part E: the aggregate identifies the release it certifies as
    AMCA Studio 1.0.1 (the active candidate); the retired 0.9.0 label may
    never reappear in v1.0.1 release evidence (regression-tested).
    """
    families: Dict[str, Dict[str, Any]] = {}
    families["migration_upgrade_certification"] = check_migration_family(
        source_sha, heavy)
    smoke = family_from_file(REPORT_SMOKE, source_sha)
    smoke["detail"] = smoke["detail"] + (
        "; packaged smoke reruns against the final build per I2E rerun rule")
    families["final_package_migration_smoke"] = smoke
    families["sanitation_privacy_audit"] = family_from_file(
        REPORT_SANITATION, source_sha)
    families["user_documentation_integrity"] = check_docs_integrity_family()
    dep = family_from_file(evidence_compliance_for_version(), source_sha)
    if SBOM.is_file():
        dep["sbom_sha256"] = sha256_file(SBOM)
    families["dependency_sbom_compliance"] = dep
    families["third_party_payload_completeness"] = (
        check_payload_completeness_family())
    families["final_package_compliance_verification"] = (
        check_package_compliance_family(portable, installer, source_sha,
                                        heavy, msi=msi))
    families["mpl_readiness_intake_state"] = check_mpl_family()

    verdict = aggregate(families)
    blockers: List[str] = []
    for name in FAMILIES:
        family = families[name]
        if family["state"] == "BLOCKED":
            blockers.append(
                f"{name}: {family.get('release_reason', family['detail'])}")
    review_items: List[str] = []
    for name in FAMILIES:
        for item in families[name].get("review_items", []):
            review_items.append(f"{name}: {item}")
        for item in families[name].get("review_items", []) if isinstance(
                families[name].get("review_items"), list) else []:
            pass
        if name == "third_party_payload_completeness":
            for item in family.get("review_items", []):
                review_items.append(f"third_party_payload: {item} "
                                    "(REVIEW_REMAINS)")

    artifact_hashes: Dict[str, Any] = {}
    for label, artifact in (("portable", portable), ("installer", installer),
                            ("msi", msi)):
        if artifact is not None and artifact.is_file():
            artifact_hashes[label] = {
                "path": str(artifact), "sha256": sha256_file(artifact),
                "size_bytes": artifact.stat().st_size}

    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release_version": release_version,  # H2C.2E: canonical 1.0.1, never 0.9.0
        "source_sha": source_sha,
        "artifact_hashes": artifact_hashes,
        "families": {f: {
            "state": families[f]["state"],
            "evidence_path": families[f].get("path"),
            "evidence_sha256": families[f].get("sha256"),
            "detail": families[f].get("detail"),
        } for f in FAMILIES},
        "review_items": review_items,
        "blockers": blockers,
        "stale_inputs": [f for f in FAMILIES
                         if families[f]["state"] == "STALE"],
        "aggregate_verdict": verdict,
        "legal_boundary": (
            "Technical aggregation only; not legal advice; no legal approval "
            "implied by READY states."),
    }
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PS-2 final release evidence aggregator (I2R)")
    parser.add_argument("--portable", default=None)
    parser.add_argument("--installer", default=None)
    parser.add_argument("--msi", default=None,
                        help="v1.0.1 MSI installer (H1C MSI-aware extension)")
    parser.add_argument("--source-sha", default=None)
    parser.add_argument("--skip-heavy", action="store_true", default=True,
                        help="Skip pytest re-runs (evidence-file mode)")
    parser.add_argument("--run-heavy", dest="skip_heavy",
                        action="store_false")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    doc = build_aggregate(
        Path(args.portable) if args.portable else None,
        Path(args.installer) if args.installer else None,
        args.source_sha, heavy=not args.skip_heavy,
        msi=Path(args.msi) if args.msi else None)
    rendered = json.dumps(doc, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if doc["aggregate_verdict"] in (
        "PS2_RELEASE_READY", "PS2_RELEASE_READY_WITH_REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
