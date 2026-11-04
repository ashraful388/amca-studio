#!/usr/bin/env python3
"""I2Y post-version-transition PS-2 fast acceptance suite.

OpenCode PS-2. Tests/docs/orchestration only. No version bump, product,
packaging, promotion, signing, or release-tag changes. Read-only with
respect to product/package files.

One compact acceptance command to run immediately after ZCode's eventual
0.9.0 -> 1.0.0 metadata-transition commit and BEFORE the expensive
definitive package build. It answers: did the version transition preserve
the complete PS-2 evidence chain and leave it ready for final-package
generation?

  python scripts/run_ps2_post_transition_acceptance.py
      [--expected-version 1.0.0] [--output PATH]

Overall states:
  READY_FOR_FINAL_BUILD              — chain intact; only artifact work left
  READY_WITH_EXTERNAL_ASSET_PENDING  — chain intact; authoritative MPL-2.0
                                       text still must be supplied (the
                                       legitimate current state)
  BLOCKED                            — schema/portability/contract checks
                                       failed (transition broke something)
  FAILED                             — a hard family failure occurred

Package-dependent verification is PENDING (never FAIL) while physical v1.0
binaries do not exist; old v0.9 evidence stays STALE; no stale v0.9 artifact
is ever auto-selected; a missing real MPL asset still yields
RELEASE_ASSET_REQUIRED; PASS_WITH_REVIEW semantics remain intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "amca-ps2-post-transition-acceptance/1"

RELEASE_DIR = SOFTWARE_ROOT / "docs" / "release"
MANIFEST_DOCS = RELEASE_DIR / "AMCA-Studio-v1.0.0-Documentation-Manifest.json"
COMPLIANCE_EVIDENCE = RELEASE_DIR / (
    "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json")
COMPLIANCE_REPORT = RELEASE_DIR / (
    "AMCA-Studio-v1.0.0-Dependency-License-and-Distribution-Compliance-"
    "Report.md")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(name: str, path: Path):
    spec = __import__("importlib.util", fromlist=["spec_from_file_location"])
    spec = spec.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = spec.module_from_spec if False else __import__(
        "importlib.util", fromlist=["module_from_spec"]
    ).module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source_version(root: Path) -> str:
    """Read the active source version from pyproject.toml (no writes)."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return "UNKNOWN"
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            return stripped.split("=", 1)[1].strip().strip("\"'")
    return "UNKNOWN"


def check_schema_drift(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """I2V sentinel verdict (fast subset: schemas + guards present)."""
    sentinel = root / "tests" / (
        "test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py")
    if not sentinel.is_file():
        return {"verdict": "BLOCKED", "detail": "schema sentinel missing"}
    text = sentinel.read_text(encoding="utf-8")
    schemas = [
        "amca-migration-certification-evidence/1",
        "amca-final-package-compliance/1",
        "amca-mpl-notice-readiness/1",
        "amca-mpl-asset-intake-receipt/1",
        "amca-mpl-release-asset-provenance/1",
        "amca-ps2-final-release-evidence/1",
        "amca-ps2-promotion-input/1",
    ]
    missing = [s for s in schemas if s not in text]
    if missing:
        return {"verdict": "BLOCKED",
                "detail": f"sentinel missing schemas: {missing}"}
    return {"verdict": "PASS", "detail": "all 7 evidence schemas pinned"}


def check_portability(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """I2W sentinel verdict (no v0.9 auto-selection in the fixed scripts)."""
    sentinel = root / "tests" / (
        "test_studio_v100_ps2_final_evidence_path_portability.py")
    scripts = root / "scripts"
    if not sentinel.is_file():
        return {"verdict": "BLOCKED", "detail": "portability sentinel missing"}
    for name in ("run_dependency_compliance_audit.py",
                 "generate_distribution_compliance_evidence.py",
                 "run_ps2_final_release_validation.py"):
        source = (scripts / name).read_text(encoding="utf-8")
        if "amca-studio-v0.9.0-windows-x64-portable.zip" in source:
            return {"verdict": "FAILED",
                    "detail": f"stale v0.9 fallback present in {name}"}
    return {"verdict": "PASS", "detail": "no stale v0.9 auto-selection"}


def check_migration_repair(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """Migration repair sentinel: I2D.1 ordering intact in source."""
    src = root / "src" / "amca" / "chat_history.py"
    if not src.is_file():
        return {"verdict": "BLOCKED", "detail": "chat_history.py missing"}
    source = src.read_text(encoding="utf-8")
    alter_pos = source.find(
        "ALTER TABLE desktop_conversations ADD COLUMN workspace_project_id")
    migrate_pos = source.find("self._migrate_conversation_modes(connection)")
    if 0 <= alter_pos < migrate_pos:
        return {"verdict": "PASS", "detail": "I2D.1 ordering intact"}
    return {"verdict": "FAILED", "detail": "I2D.1 repair regressed"}


def check_mpl_state() -> Dict[str, Any]:
    """I2P readiness: RELEASE_ASSET_REQUIRED is the legitimate pending state."""
    try:
        mr = _load("mpl_readiness_i2y",
                   Path(__file__).resolve().parent /
                   "run_mpl_notice_readiness.py")
    except Exception as exc:  # noqa: BLE001
        return {"state": "BLOCKED", "detail": f"validator unavailable: {exc}"}
    doc = mr.evaluate()
    state_map = {"READY": "READY", "RELEASE_ASSET_REQUIRED":
                 "EXTERNAL_ASSET_PENDING", "MISMATCH": "BLOCKED",
                 "BLOCKED": "BLOCKED"}
    return {"state": state_map.get(doc.get("state"), "BLOCKED"),
            "detail": doc.get("state"),
            "asset_sha256": (doc.get("asset") or {}).get("sha256")}


def check_stale_evidence_state(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """Old v0.9 validation evidence must be STALE, never final-ready."""
    compliance = root / "docs" / "release" / (
        "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json")
    if not compliance.is_file():
        return {"state": "PENDING", "detail": "no compliance evidence yet"}
    try:
        doc = json.loads(compliance.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"state": "BLOCKED", "detail": f"evidence unreadable: {exc}"}
    if doc.get("package_role") != "final":
        return {"state": "STALE",
                "detail": f"evidence is {doc.get('package_role')!r}-role "
                "from the v0.9 validation lineage; final-package evidence "
                "must be generated after the definitive build"}
    return {"state": "PASS", "detail": "final-role evidence present"}


def check_package_evidence_state(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """No physical v1.0 binaries yet => PENDING, never FAIL."""
    dist = root / "dist"
    v09 = dist / "amca-studio-v0.9.0-windows-x64-portable.zip"
    final_markers = sorted(p.name for p in dist.glob("*.zip")
                           if "v1.0.0" in p.name) if dist.is_dir() else []
    if final_markers:
        return {"state": "PENDING",
                "detail": ("v1.0.0 package present; run the I2O "
                           "final-package verification for full validation"),
                "final_packages": final_markers}
    if v09.is_file():
        return {"state": "PENDING",
                "detail": ("only v0.9 validation binaries present; final "
                           "v1.0.0 package not built yet (stale v0.9 "
                           "artifacts are never auto-selected)")}
    return {"state": "PENDING", "detail": "no package artifacts present"}


def check_documentation_integrity(root: Path = SOFTWARE_ROOT
                                  ) -> Dict[str, Any]:
    manifest = root / "docs" / "release" / (
        "AMCA-Studio-v1.0.0-Documentation-Manifest.json")
    if not manifest.is_file():
        return {"state": "BLOCKED", "detail": "documentation manifest missing"}
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"state": "FAIL", "detail": f"manifest unreadable: {exc}"}
    problems: List[str] = []
    checked = 0
    for entry in doc.get("documents", []):
        path = root / "docs" / "release" / entry.get("document", "")
        if not path.is_file():
            problems.append(f"missing: {entry.get('document')}")
            continue
        checked += 1
        if sha256_file(path) != entry.get("sha256"):
            problems.append(f"hash mismatch: {entry.get('document')}")
    if any("hash mismatch" in p for p in problems):
        return {"state": "FAIL", "detail": "; ".join(problems[:5])}
    if problems:
        return {"state": "BLOCKED", "detail": "; ".join(problems[:5])}
    return {"state": "PASS", "detail": f"{checked} documents hash-verified"}


def check_promotion_guards(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """I2U guards: READY_WITH_REVIEW mapping + MPL provenance mandatory."""
    try:
        pi = _load("ps2_promotion_i2y",
                   Path(__file__).resolve().parent /
                   "build_ps2_promotion_input.py")
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "BLOCKED", "detail": f"adapter unavailable: {exc}"}
    problems: List[str] = []
    if pi.STATE_MAP.get("PS2_RELEASE_READY_WITH_REVIEW") != \
            "PS2_INPUT_READY_WITH_REVIEW":
        problems.append("READY_WITH_REVIEW mapping drifted")
    # READY_WITH_REVIEW aggregate with valid MPL provenance must stay
    # READY_WITH_REVIEW (never softened to READY).
    doc = {"schema": pi.SCHEMA,
           "promotion_eligibility_ps2": "PS2_INPUT_READY_WITH_REVIEW",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY_WITH_REVIEW",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64, "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY"}
    verdict, _ = pi.validate_promotion_input(doc)
    if verdict != "PS2_INPUT_READY_WITH_REVIEW":
        problems.append("eligibility softened vs aggregate")
    # A policy acknowledgment must not soften the technical verdict.
    acknowledged = dict(doc, policy_acknowledgments=["certifi"])
    verdict, _ = pi.validate_promotion_input(acknowledged)
    if verdict != "PS2_INPUT_READY_WITH_REVIEW":
        problems.append("policy acknowledgment altered technical verdict")
    # MPL provenance must remain mandatory: without it, BLOCKED.
    doc_block = dict(doc)
    doc_block["mpl_provenance_state"] = "ASSET_NOT_STAGED"
    doc_block["mpl_readiness"] = "BLOCKED"
    verdict, _ = pi.validate_promotion_input(doc_block)
    if verdict != "PS2_INPUT_BLOCKED":
        problems.append("MPL provenance not mandatory")
    if problems:
        return {"verdict": "FAILED", "detail": "; ".join(problems)}
    return {"verdict": "PASS", "detail": "promotion guards intact"}


def run_acceptance(expected_version: str = "1.0.0",
                   root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """Run all fast-acceptance checks read-only. Never raises on FAIL paths."""
    checks: Dict[str, Any] = {
        "schema_drift": check_schema_drift(root),
        "portability": check_portability(root),
        "migration_contract": check_migration_repair(root),
        "mpl_state": check_mpl_state(),
        "stale_evidence": check_stale_evidence_state(root),
        "package_evidence": check_package_evidence_state(root),
        "documentation_integrity": check_documentation_integrity(root),
        "promotion_guards": check_promotion_guards(root),
    }
    version = source_version(root)
    version_matches = version == expected_version

    failed = [k for k, v in checks.items() if v.get("verdict") == "FAILED"
              or v.get("state") == "FAIL"]
    blocked = [k for k, v in checks.items() if v.get("verdict") == "BLOCKED"
               or v.get("state") == "BLOCKED"]
    mpl_pending = checks["mpl_state"].get("state") == \
        "EXTERNAL_ASSET_PENDING"

    if failed:
        overall = "FAILED"
    elif blocked or not version_matches:
        overall = "BLOCKED"
    elif mpl_pending:
        overall = "READY_WITH_EXTERNAL_ASSET_PENDING"
    else:
        overall = "READY_FOR_FINAL_BUILD"
    try:
        spec = __import__("importlib.util", fromlist=[
            "spec_from_file_location"]).spec_from_file_location(
            "git_sha_i2y", None) if False else None
    except Exception:  # noqa: BLE001
        spec = None
    source_sha = None
    try:
        proc = __import__("subprocess").run(
            ["git", "rev-parse", "HEAD"], cwd=str(root),
            capture_output=True, text=True, timeout=30)
        sha = proc.stdout.strip()
        source_sha = sha if len(sha) == 40 else None
    except (OSError, ValueError):
        pass
    return {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_sha": source_sha,
        "source_version": version,
        "expected_version": expected_version,
        "version_transition_applied": version_matches,
        "checks": checks,
        "overall": overall,
        "legal_boundary": (
            "Fast technical acceptance only; not legal advice; "
            "PASS_WITH_REVIEW semantics preserved; no legal approval."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PS-2 post-transition fast acceptance (I2Y)")
    parser.add_argument("--expected-version", default="1.0.0")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    doc = run_acceptance(expected_version=args.expected_version)
    rendered = json.dumps(doc, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if doc["overall"] in ("READY_FOR_FINAL_BUILD",
                                   "READY_WITH_EXTERNAL_ASSET_PENDING") else 1


if __name__ == "__main__":
    raise SystemExit(main())
