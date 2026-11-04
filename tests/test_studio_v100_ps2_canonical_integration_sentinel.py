"""PS-2 canonical-integration sentinel + release-doc completeness gate (I2J).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2J-PS2-CANONICAL-INTEGRATION-SENTINEL-AND-RELEASE-DOC-COMPLETENESS-GATE-01
Worker: OpenCode PS-2. Tests/docs only. No branch merges, no production-code
changes (chat_history.py is inspected read-only and exercised behaviorally).

Purpose: prove the entire accepted PS-2 migration/documentation chain
(I2D -> I2D.1 repair -> I2E -> I2F -> I2G -> I2H -> I2I) is present and
consistent in a canonical tree, so PS-1 has an objective post-merge
acceptance check and no release-critical PS-2 work can be silently omitted
during cross-stream convergence.

Reusable entry point (documented for PS-1 post-merge use):

    python -m pytest tests/test_studio_v100_ps2_canonical_integration_sentinel.py -q

or programmatically::

    from test_studio_v100_ps2_canonical_integration_sentinel import evaluate_tree
    evaluate_tree(Path("<merged-tree>/software"))  # -> {"overall": ...}

Verdicts: PASS (all contracts present and consistent), FAIL (contradictory /
incorrect content), BLOCKED (expected inputs not yet integrated).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SOFTWARE_ROOT.parent
SRC = SOFTWARE_ROOT / "src" / "amca"
TESTS = SOFTWARE_ROOT / "tests"
DOCS_RELEASE = SOFTWARE_ROOT / "docs" / "release"
DOCS_INTEGRATION = SOFTWARE_ROOT / "docs" / "integration"

# Milestone -> files that must exist with the accepted outcome inside.
CHAIN: Dict[str, List[str]] = {
    "I2D migration certification": [
        "tests/test_studio_v100_upgrade_migration_restore_certification.py",
        "docs/integration/AMCA-Studio-v1.0.0-Upgrade-Migration-Backup-Restore-Certification-Report.md",
    ],
    "I2D.1 ConversationStore repair": [
        "src/amca/chat_history.py",
    ],
    "I2E packaged upgrade smoke": [
        "scripts/run_upgrade_smoke.py",
        "tests/test_studio_v100_real_package_upgrade_smoke.py",
        "docs/release/AMCA-Studio-v1.0.0-Existing-User-Package-Upgrade-Smoke-Report.md",
    ],
    "I2F evidence contract": [
        "scripts/generate_migration_certification_evidence.py",
        "tests/test_studio_v100_migration_certification_evidence.py",
        "docs/release/AMCA-Studio-v1.0.0-Migration-Certification-Evidence-Contract.md",
    ],
    "I2G upgrade/recovery guide": [
        "docs/release/AMCA-Studio-v1.0.0-Upgrade-and-Recovery-Guide.md",
        "tests/test_studio_v100_upgrade_release_documentation.py",
    ],
    "I2H user documentation bundle": [
        "docs/release/AMCA-Studio-v1.0.0-Installation-and-First-Launch-Guide.md",
        "docs/release/AMCA-Studio-v1.0.0-Known-Limitations.md",
        "docs/release/AMCA-Studio-v1.0.0-User-Capability-Matrix.md",
        "docs/release/AMCA-Studio-v1.0.0-Troubleshooting-Guide.md",
        "docs/release/AMCA-Studio-v1.0.0-Documentation-Manifest.json",
        "tests/test_studio_v100_final_user_documentation_bundle.py",
    ],
    "I2I release notes freeze": [
        "docs/release/AMCA-Studio-v1.0.0-Release-Notes-Candidate.md",
        "docs/release/AMCA-Studio-v1.0.0-Release-Evidence-Index.md",
        "tests/test_studio_v100_release_notes_and_lineage_freeze.py",
    ],
}

MANIFEST = "docs/release/AMCA-Studio-v1.0.0-Documentation-Manifest.json"


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _flat(root: Path, rel: str) -> str:
    return " ".join(_read(root, rel).replace("*", "").split()).casefold()


Check = Tuple[str, bool, str]


def check_chain_presence(root: Path = SOFTWARE_ROOT) -> List[Check]:
    """Every accepted PS-2 outcome file exists with its substance inside."""
    results: List[Check] = []
    for milestone, rels in CHAIN.items():
        for rel in rels:
            path = root / rel
            if not path.is_file():
                results.append((f"presence:{milestone}:{rel}", False,
                                "file missing from canonical tree"))
                continue
            results.append((f"presence:{milestone}:{rel}", True, "present"))
    markers = {
        "tests/test_studio_v100_upgrade_migration_restore_certification.py":
            "test_pre_c1_conversations_shape_migration",
        "scripts/run_upgrade_smoke.py": "amca-upgrade-smoke-result/1",
        "scripts/generate_migration_certification_evidence.py":
            "require_final_package",
        "docs/release/AMCA-Studio-v1.0.0-Upgrade-and-Recovery-Guide.md":
            "does not implement automatic rollback",
        "docs/release/AMCA-Studio-v1.0.0-Release-Notes-Candidate.md":
            "not released",
    }
    for rel, marker in markers.items():
        path = root / rel
        if not path.is_file():
            continue  # already reported above
        ok = marker in path.read_text(encoding="utf-8")
        results.append((f"contract:{rel}", ok,
                        "accepted outcome present" if ok
                        else f"missing accepted marker: {marker!r}"))
    return results


def check_migration_repair_guard(root: Path = SOFTWARE_ROOT) -> List[Check]:
    """Read-only guard: the repaired ordering and index restore are present,
    and a pre-C1 database actually migrates through the tree's code."""
    results: List[Check] = []
    src_file = root / "src" / "amca" / "chat_history.py"
    if not src_file.is_file():
        return [("repair-guard:source-present", False, "chat_history.py missing")]
    source = src_file.read_text(encoding="utf-8")
    alter_pos = source.find(
        "ALTER TABLE desktop_conversations ADD COLUMN workspace_project_id")
    migrate_pos = source.find("self._migrate_conversation_modes(connection)")
    order_ok = 0 <= alter_pos < migrate_pos
    results.append(("repair-guard:alter-before-rebuild", order_ok,
                    "I2D.1 ordering present" if order_ok
                    else "repair ordering absent (I2D-001 shape)"))
    for index in ("idx_desktop_conversations_updated",
                  "idx_desktop_conversations_workspace"):
        results.append((f"repair-guard:index-{index}", index in source,
                        "index restore present" if index in source
                        else "index restore missing"))
    # Behavioral proof through the tree's own code (disposable temp DB;
    # best-effort cleanup: Windows file locks must never fail the verdict).
    tmp = Path(tempfile.mkdtemp(prefix="i2j-sentinel-"))
    try:
        import sys
        sys.path.insert(0, str(root / "src"))
        sys.path.insert(0, str(root / "tests"))
        import test_studio_v100_upgrade_migration_restore_certification as i2d
        from amca.chat_history import ConversationStore

        db_path = tmp / "pre-c1.db"
        i2d._create_v090_legacy_db(db_path)
        store = ConversationStore(db_path)
        try:
            legacy = store.get("conv-legacy-a")
            ok = (legacy is not None
                  and legacy["conversation"]["mode"] == "joint")
            results.append(("repair-guard:pre-c1-migrates", ok,
                            "pre-C1 rows migrate" if ok
                            else "pre-C1 migration failed"))
            probe = __import__("sqlite3").connect(str(db_path))
            try:
                cols = [r[1] for r in probe.execute(
                    "PRAGMA table_info(desktop_conversations)")]
            finally:
                probe.close()
            results.append(("repair-guard:column-present",
                            "workspace_project_id" in cols,
                            "column added by migration"))
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # noqa: BLE001 - sentinel must report, not raise
        results.append(("repair-guard:pre-c1-migrates", False,
                        f"behavioral check errored: {type(exc).__name__}: {exc}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def check_document_manifest(root: Path = SOFTWARE_ROOT) -> List[Check]:
    """Manifest exists, hashes match bytes, pending stays pending."""
    results: List[Check] = []
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        return [("manifest:present", False, "documentation manifest missing")]
    results.append(("manifest:present", True, "present"))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return results + [("manifest:parseable", False, f"invalid JSON: {exc}")]
    for entry in manifest.get("documents", []):
        path = root / "docs" / "release" / entry.get("document", "")
        if not path.is_file():
            results.append((f"manifest:file:{entry.get('document')}", False,
                            "manifested document missing"))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        ok = actual == entry.get("sha256")
        results.append((f"manifest:hash:{entry.get('document')}", ok,
                        "hash matches" if ok else "STALE manifest hash"))
    pending = manifest.get("pending", [])
    results.append(("manifest:pending-governed", len(pending) >= 1,
                    f"{len(pending)} items still pending" if pending
                    else "no pending items marked"))
    for entry in pending:
        ok = "status" in entry and "sha256" not in entry
        results.append((f"manifest:pending:{entry.get('document')}", ok,
                        "pending without hash" if ok
                        else "pending entry must not carry a hash"))
    return results


def check_cross_document_consistency(
        root: Path = SOFTWARE_ROOT) -> List[Check]:
    """Paired documents agree; history and release-state claims hold."""
    results: List[Check] = []

    def has(rel: str, term: str) -> bool:
        path = root / rel
        return path.is_file() and term in _flat(root, rel)

    pairs = [
        ("Known Limitations <-> Capability Matrix",
         "docs/release/AMCA-Studio-v1.0.0-Known-Limitations.md",
         "docs/release/AMCA-Studio-v1.0.0-User-Capability-Matrix.md",
         ("linux", "telemetry", "downgrade", "marketplace")),
        ("Upgrade Guide <-> Evidence Contract",
         "docs/release/AMCA-Studio-v1.0.0-Upgrade-and-Recovery-Guide.md",
         "docs/release/AMCA-Studio-v1.0.0-Migration-Certification-Evidence-Contract.md",
         ("rerun", "pre-c1", "migration")),
        ("Release Notes <-> Known Limitations",
         "docs/release/AMCA-Studio-v1.0.0-Release-Notes-Candidate.md",
         "docs/release/AMCA-Studio-v1.0.0-Known-Limitations.md",
         ("downgrade", "telemetry", "linux", "filesystem")),
    ]
    for label, left, right, terms in pairs:
        if not (root / left).is_file() or not (root / right).is_file():
            results.append((f"consistency:{label}", False,
                            "document missing from tree"))
            continue
        missing = [t for t in terms
                   if not (has(left, t) and has(right, t))]
        results.append((f"consistency:{label}", not missing,
                        "agree" if not missing
                        else f"diverge on: {missing}"))
    notes = _flat(root, "docs/release/AMCA-Studio-v1.0.0-Release-Notes-Candidate.md") \
        if (root / "docs/release/AMCA-Studio-v1.0.0-Release-Notes-Candidate.md").is_file() \
        else ""
    results.append(("consistency:history-v090",
                    "v0.9.0" in notes and "pre-c1" in notes,
                    "historical terminology intact"))
    results.append(("consistency:no-final-claim",
                    "not released" in notes or "candidate" in notes,
                    "no premature final claim"))
    return results


def evaluate_tree(root: Path = SOFTWARE_ROOT) -> Dict[str, Any]:
    """Evaluate a canonical tree. Returns overall PASS/FAIL/BLOCKED + checks."""
    suites = (
        check_chain_presence(root)
        + check_migration_repair_guard(root)
        + check_document_manifest(root)
        + check_cross_document_consistency(root)
    )
    checks = [{"name": n, "ok": bool(ok), "detail": d}
              for n, ok, d in suites]
    if any("missing from canonical tree" in c["detail"]
           or "missing from tree" in c["detail"]
           or "documentation manifest missing" in c["detail"]
           for c in checks if not c["ok"]):
        overall = "BLOCKED"
    elif any(not c["ok"] for c in checks):
        overall = "FAIL"
    else:
        overall = "PASS"
    return {"overall": overall, "checks": checks,
            "passed": sum(1 for c in checks if c["ok"]),
            "total": len(checks)}


# --------------------------------------------------------------------------
# Pytest entry points (run against the current tree)
# --------------------------------------------------------------------------

def test_ps2_chain_presence():
    missing = [c for c in check_chain_presence() if not c[1]]
    assert not missing, f"PS-2 chain files missing/contract-broken: {missing}"


def test_migration_repair_guard():
    failed = [c for c in check_migration_repair_guard() if not c[1]]
    assert not failed, f"migration repair guard failed: {failed}"


def test_document_manifest_integrity():
    failed = [c for c in check_document_manifest() if not c[1]]
    assert not failed, f"manifest integrity failed: {failed}"


def test_cross_document_consistency():
    failed = [c for c in check_cross_document_consistency() if not c[1]]
    assert not failed, f"consistency failed: {failed}"


def test_pending_final_artifacts_governed():
    manifest = json.loads((SOFTWARE_ROOT / MANIFEST).read_text(
        encoding="utf-8"))
    assert len(manifest.get("pending", [])) >= 1
    for entry in manifest["pending"]:
        assert "sha256" not in entry
        assert entry.get("status", "").startswith("pending")


def test_sentinel_overall_pass():
    result = evaluate_tree()
    assert result["overall"] == "PASS", \
        [c for c in result["checks"] if not c["ok"]]
    assert result["total"] >= 40
