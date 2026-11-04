#!/usr/bin/env python3
"""I2F migration-certification evidence bundle + promotion-input adapter.

OpenCode PS-2. Infrastructure/scripts only; no production-code changes.

Converts completed I2D/I2E results into a deterministic, hash-bound JSON
evidence bundle that a release pipeline (e.g. ZCode's final promotion gate)
can consume WITHOUT duplicating migration testing:

  python scripts/generate_migration_certification_evidence.py
      --backend EXE --output evidence.json [--manifest PATH]
      [--package-role validation|final] [--timeout SEC]

The generator consumes ACTUAL results only:
  - it runs the I2D + I2E pytest modules in a subprocess and records
    pass/fail counts (no manufactured values);
  - it runs the real packaged-backend upgrade smoke (run_upgrade_smoke)
    against a freshly materialized v0.9/pre-C1 fixture and embeds the
    smoke result;
  - it SHA-256 binds the backend, fixture files, smoke result, and pytest
    summary so later tampering is detectable.

Verdict rules (honest by construction):
  - missing mandatory evidence            -> BLOCKED
  - any present failing evidence          -> FAIL
  - complete passing evidence             -> PASS
A validation-role PASS certifies ONLY the exact tested package SHA; it never
silently certifies a different (e.g. final) package -- see the final-package
rerun rule enforced by validate_migration_evidence().
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SOFTWARE_ROOT / "tests"))

import run_upgrade_smoke as smoke  # noqa: E402  (sibling script, PS-2 owned)

EVIDENCE_SCHEMA_VERSION = "1.0.0"
SCHEMA_ID = "amca-migration-certification-evidence/1"

I2D_TEST_MODULE = "test_studio_v100_upgrade_migration_restore_certification.py"
I2E_TEST_MODULE = "test_studio_v100_real_package_upgrade_smoke.py"

REQUIRED_TOP_FIELDS = [
    "schema",
    "evidence_schema_version",
    "overall",
    "certification_timestamp",
    "certification_git_sha",
    "migration_repair_sha",
    "package",
    "fixture",
    "verdicts",
    "bindings",
    "limitations",
]

REQUIRED_VERDICT_FIELDS = [
    "packaged_backend_startup",
    "migration",
    "pre_c1_migration",
    "record_integrity",
    "workspace_isolation",
    "backup_restore",
    "source_fixture_unchanged",
    "corrupt_state_negative",
]

VERDICTS = ("PASS", "FAIL", "BLOCKED")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_sha(path: Path = SOFTWARE_ROOT) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, ValueError):
        return None
    sha = proc.stdout.strip()
    return sha if len(sha) == 40 else None


def _git_sha_for_file(repo_file: str) -> Optional[str]:
    """Newest commit touching repo_file (used for the repair SHA)."""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%H", "-1", "--", repo_file],
            cwd=str(SOFTWARE_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, ValueError):
        return None
    sha = proc.stdout.strip().splitlines()
    return sha[0] if sha and len(sha[0]) == 40 else None


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


# --------------------------------------------------------------------------
# Evidence assembly
# --------------------------------------------------------------------------

def run_pytest_suites(
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run the I2D + I2E pytest modules; record real pass/fail counts."""
    import os

    summary: Dict[str, Any] = {"suites": {}, "overall": "BLOCKED"}
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    all_ok = True
    for module in (I2D_TEST_MODULE, I2E_TEST_MODULE):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest",
             f"tests/{module}", "-q", "-p", "no:cacheprovider"],
            cwd=str(SOFTWARE_ROOT),
            capture_output=True,
            text=True,
            timeout=1200,
            env=env,
        )
        tail = (proc.stdout + proc.stderr)[-800:]
        passed = proc.returncode == 0
        suite = {
            "module": module,
            "returncode": proc.returncode,
            "passed": passed,
            "output_tail": tail,
        }
        summary["suites"][module] = suite
        all_ok = all_ok and passed
    summary["overall"] = "PASS" if all_ok else "FAIL"
    return summary


def materialize_fixture(root: Path):
    """Build a fresh deterministic v0.9/pre-C1 source fixture.

    Reuses the certified I2D builders (imported from the I2D test module:
    test-only coupling, no production coupling).
    """
    import test_studio_v100_upgrade_migration_restore_certification as i2d

    fixture = root / "evidence_fixture"
    db_path = fixture / smoke.FIXTURE_DB_NAME
    payloads = fixture / smoke.FIXTURE_PAYLOADS_DIR
    i2d._create_v090_legacy_db(db_path)
    stores = i2d._open_all_stores(db_path, payloads)
    try:
        i2d._seed_current_shape_records(stores)
    finally:
        i2d._close_all_stores(stores)
    # Rewind conversations so the packaged backend performs the migration.
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            """
            DROP TABLE IF EXISTS desktop_conversation_messages;
            DROP TABLE IF EXISTS desktop_conversations;
            CREATE TABLE desktop_conversations (
                conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('solo', 'joint')),
                participant_ids_json TEXT NOT NULL,
                project_id TEXT, active_run_id TEXT,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_message_at TEXT
            );
            CREATE TABLE desktop_conversation_messages (
                message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, meta TEXT, run_id TEXT,
                project_id TEXT, created_at TEXT NOT NULL,
                UNIQUE(conversation_id, ordinal),
                FOREIGN KEY(conversation_id)
                    REFERENCES desktop_conversations(conversation_id)
                    ON DELETE CASCADE
            );
            """
        )
        con.execute(
            "INSERT INTO desktop_conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("conv-legacy-a", "Legacy chat", "joint", '["agent-1"]',
             i2d.PROJ_A, None, "idle", i2d.TS, i2d.TS, None),
        )
        con.execute(
            "INSERT INTO desktop_conversation_messages VALUES (?,?,?,?,?,?,?,?,?)",
            ("msg-legacy-a", "conv-legacy-a", 1, "user",
             "Legacy hello from alpha.", None, None, i2d.PROJ_A, i2d.TS),
        )
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
    finally:
        con.close()
    manifest = {
        "fixture": "OPENCODE-PS2-I2F",
        "app_version": "0.9.0",
        "upgrade_target": "v1.0 candidate schema via packaged backend",
    }
    (fixture / smoke.FIXTURE_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return fixture


def _combine_verdicts(
    pytest_summary: Optional[Dict[str, Any]],
    smoke_result: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, str], List[str]]:
    """Map real consumed results onto per-area verdicts. Missing -> BLOCKED."""
    verdicts: Dict[str, str] = {}
    notes: List[str] = []
    if pytest_summary is None:
        for field in ("backup_restore",):
            verdicts[field] = "BLOCKED"
        notes.append("pytest suites were not executed")
    else:
        i2d_ok = bool(
            pytest_summary.get("suites", {}).get(I2D_TEST_MODULE, {}).get("passed")
        )
        verdicts["backup_restore"] = "PASS" if i2d_ok else "FAIL"
    if smoke_result is None:
        for field in (
            "packaged_backend_startup",
            "migration",
            "pre_c1_migration",
            "record_integrity",
            "workspace_isolation",
            "source_fixture_unchanged",
            "corrupt_state_negative",
        ):
            verdicts[field] = "BLOCKED"
        notes.append("upgrade smoke was not executed")
        return verdicts, notes
    if smoke_result.get("overall") != "PASS":
        for field in (
            "packaged_backend_startup",
            "migration",
            "pre_c1_migration",
            "record_integrity",
            "workspace_isolation",
            "source_fixture_unchanged",
            "corrupt_state_negative",
        ):
            verdicts[field] = "FAIL"
        notes.append("upgrade smoke did not pass; per-area verdicts FAIL")
        return verdicts, notes
    migration = smoke_result.get("migration", {})
    integrity = smoke_result.get("integrity", {})
    fixture = smoke_result.get("fixture", {})
    verdicts["packaged_backend_startup"] = (
        "PASS" if migration.get("backend_healthy") else "FAIL"
    )
    verdicts["migration"] = (
        "PASS" if migration.get("backend_healthy") else "FAIL"
    )
    verdicts["pre_c1_migration"] = (
        "PASS" if migration.get("backend_healthy") else "FAIL"
    )
    verdicts["record_integrity"] = (
        "PASS" if not integrity.get("findings") else "FAIL"
    )
    verdicts["workspace_isolation"] = (
        "PASS" if migration.get("backend_healthy") else "FAIL"
    )
    verdicts["source_fixture_unchanged"] = (
        "PASS" if fixture.get("source_unchanged") else "FAIL"
    )
    verdicts["corrupt_state_negative"] = "BLOCKED"
    notes.append(
        "corrupt-state negative coverage comes from the I2E suite, not the "
        "single smoke run; see pytest summary"
    )
    if pytest_summary is not None:
        i2e_ok = bool(
            pytest_summary.get("suites", {}).get(I2E_TEST_MODULE, {}).get("passed")
        )
        verdicts["corrupt_state_negative"] = "PASS" if i2e_ok else "FAIL"
    return verdicts, notes


def _overall_from_verdicts(verdicts: Dict[str, str]) -> str:
    values = [verdicts.get(f, "BLOCKED") for f in REQUIRED_VERDICT_FIELDS]
    if any(v not in VERDICTS for v in values):
        return "BLOCKED"
    if any(v == "FAIL" for v in values):
        return "FAIL"
    if any(v == "BLOCKED" for v in values):
        return "BLOCKED"
    return "PASS"


def generate_evidence(
    backend: Path,
    output: Path,
    manifest_path: Optional[Path] = None,
    package_role: str = "validation",
    timeout: float = 180.0,
    skip_pytest: bool = False,
    backend_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Generate the evidence bundle. Returns the bundle dict.

    Never manufactures values: every verdict derives from executed pytest
    suites and an executed packaged-backend smoke run.
    """
    if package_role not in ("validation", "final"):
        raise ValueError("package_role must be 'validation' or 'final'")
    workdir = Path(tempfile.mkdtemp(prefix="amca-evidence-"))
    pytest_summary: Optional[Dict[str, Any]] = None
    if not skip_pytest:
        pytest_summary = run_pytest_suites(extra_env=backend_env)
    fixture = materialize_fixture(workdir)
    smoke_result = smoke.run_upgrade_smoke(
        backend,
        fixture,
        workspace=workdir / "smoke-ws",
        manifest_path=manifest_path,
        timeout=timeout,
    )
    verdicts, verdict_notes = _combine_verdicts(pytest_summary, smoke_result)
    overall = _overall_from_verdicts(verdicts)

    backend_sha = smoke.sha256_file(backend) if backend.is_file() else None
    fixture_files = smoke.snapshot_files(fixture)
    fixture_db = fixture / smoke.FIXTURE_DB_NAME
    bindings = {
        "backend_sha256": backend_sha,
        "backend_size_bytes": backend.stat().st_size if backend.is_file() else None,
        "fixture_manifest_sha256": fixture_files.get(smoke.FIXTURE_MANIFEST_NAME),
        "fixture_db_sha256": fixture_files.get(smoke.FIXTURE_DB_NAME),
        "smoke_result_sha256": _digest_json(smoke_result),
        "pytest_summary_sha256": _digest_json(pytest_summary)
        if pytest_summary is not None
        else None,
    }
    _ = fixture_db  # fixture DB hash is fixture_db_sha256 above.
    evidence: Dict[str, Any] = {
        "schema": SCHEMA_ID,
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "overall": overall,
        "certification_timestamp": _utc_now(),
        "certification_git_sha": _git_sha(),
        "migration_repair_sha": _git_sha_for_file(
            "software/src/amca/chat_history.py"
        ),
        "package": {
            "path": str(backend),
            "sha256": backend_sha,
            "size_bytes": bindings["backend_size_bytes"],
            "role": package_role,
            "source_sha": smoke_result.get("provenance", {}).get("source_sha"),
            "application_version": smoke_result.get("provenance", {}).get(
                "application_version"
            ),
        },
        "fixture": {
            "identifier": "OPENCODE-PS2-I2F",
            "version": "0.9.0",
            "upgrade_boundary": "v0.9.0 / pre-C1 compatible state -> "
            "current v1.0 candidate schema",
            "files": len(fixture_files),
            "manifest_sha256": bindings["fixture_manifest_sha256"],
            "db_sha256": bindings["fixture_db_sha256"],
        },
        "verdicts": verdicts,
        "verdict_notes": verdict_notes,
        "bindings": bindings,
        "pytest": pytest_summary,
        "smoke_result": smoke_result,
        "limitations": [
            "Validation-role evidence certifies ONLY the exact tested package "
            "SHA; it never silently certifies a different (e.g. final) "
            "package (see final-package rerun rule).",
            "In-memory systems (evaluations, manual selections/bindings) have "
            "no packaged migration path by design; covered at unit level.",
            "Desktop GUI launch is out of scope (ZCode portable smoke).",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return evidence


# --------------------------------------------------------------------------
# Promotion-input adapter (read-only)
# --------------------------------------------------------------------------

def validate_migration_evidence(
    evidence: Dict[str, Any],
    expected_package_sha: Optional[str] = None,
    require_final_package: bool = False,
) -> Tuple[str, List[str]]:
    """Validate an evidence bundle WITHOUT copying/promoting anything.

    Returns (verdict, reasons). Pure read-only: never writes, copies, or
    promotes release artifacts.
    """
    reasons: List[str] = []
    for field in REQUIRED_TOP_FIELDS:
        if field not in evidence:
            reasons.append(f"missing mandatory evidence field: {field}")
    if reasons:
        return "BLOCKED", reasons
    if evidence.get("schema") != SCHEMA_ID:
        return "BLOCKED", [
            f"unsupported evidence schema: {evidence.get('schema')!r}"
        ]
    if evidence.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        return "BLOCKED", [
            "unsupported evidence-schema version: "
            f"{evidence.get('evidence_schema_version')!r}"
        ]
    if not isinstance(evidence.get("smoke_result"), dict):
        return "BLOCKED", ["missing mandatory evidence: upgrade-smoke result"]
    package = evidence.get("package", {})
    if expected_package_sha is not None and package.get("sha256") != (
        expected_package_sha
    ):
        reasons.append(
            "package SHA mismatch: evidence certifies "
            f"{package.get('sha256')}, promotion requested "
            f"{expected_package_sha}"
        )
        return "FAIL", reasons
    if require_final_package and package.get("role") != "final":
        reasons.append(
            "final-package rerun rule: validation-role evidence cannot "
            "certify a final package; rerun I2E against the final package"
        )
        return "FAIL", reasons
    bindings = evidence.get("bindings", {})
    smoke_result = evidence.get("smoke_result")
    if isinstance(smoke_result, dict):
        if _digest_json(smoke_result) != bindings.get("smoke_result_sha256"):
            reasons.append("stale/tampered upgrade-smoke result detected")
            return "FAIL", reasons
    verdicts = evidence.get("verdicts", {})
    for field in REQUIRED_VERDICT_FIELDS:
        if field not in verdicts:
            reasons.append(f"missing per-area verdict: {field}")
    if reasons:
        return "BLOCKED", reasons
    overall = evidence.get("overall")
    if overall == "FAIL":
        return "FAIL", ["evidence overall verdict is FAIL"]
    if overall != "PASS":
        return "BLOCKED", ["evidence overall verdict is not PASS"]
    if any(verdicts.get(f) != "PASS" for f in REQUIRED_VERDICT_FIELDS):
        bad = {f: verdicts.get(f) for f in REQUIRED_VERDICT_FIELDS}
        return "FAIL", [f"non-passing per-area verdicts: {bad}"]
    return "PASS", ["migration evidence valid for package "
                    f"{package.get('sha256')}"]


# --------------------------------------------------------------------------
# H1E surface 10: v1.0.1 three-artifact upgrade-path evidence
# --------------------------------------------------------------------------

UPGRADE_PATH_SCHEMA = "amca-v101-upgrade-path-evidence/1"
MSI_PENDING_STATE = "MSI_EXECUTION_PENDING"
V100_ARTIFACT_HASHES = {
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1",
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454",
}


def classify_artifact_role(path: Path) -> Optional[str]:
    name = str(path).lower()
    if name.endswith(".exe"):
        return "exe"
    if name.endswith(".msi"):
        return "msi"
    if name.endswith(".zip"):
        return "portable"
    return None


def build_upgrade_path_evidence(
    exe: Optional[Path] = None,
    msi: Optional[Path] = None,
    portable: Optional[Path] = None,
    source_sha: Optional[str] = None,
    msi_execution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Per-role v1.0.1 upgrade/migration evidence (H1E).

    EXE upgrade path and Portable compatibility path certify against real
    v1.0.1 artifact bytes. The MSI install/uninstall/upgrade evidence role
    stays in the honest prebuild state MSI_EXECUTION_PENDING until ZCode
    produces a real MSI AND a real execution record exists — a PASS is
    never invented for a missing/unexecuted MSI.
    """
    paths: Dict[str, Any] = {}
    for role, path in (("exe", exe), ("msi", msi), ("portable", portable)):
        entry: Dict[str, Any] = {"role": role, "artifact": None,
                                 "state": "PENDING", "reasons": []}
        if path is None:
            entry["reasons"].append(f"{role} artifact not supplied")
            paths[role] = entry
            continue
        norm = str(path).replace("\\", "/")
        if classify_artifact_role(path) != role:
            entry["state"] = "FAIL"
            entry["reasons"].append(f"{role}: wrong file type for role")
            paths[role] = entry
            continue
        if not norm.startswith(("D:/", "d:/")):
            entry["state"] = "FAIL"
            entry["reasons"].append(
                f"{role}: v1.0.1 artifacts must live under the D: root")
            paths[role] = entry
            continue
        if not path.is_file():
            entry["reasons"].append(f"{role}: artifact file missing")
            paths[role] = entry
            continue
        digest = _digest_bytes(path.read_bytes())
        if digest in V100_ARTIFACT_HASHES:
            entry["state"] = "FAIL"
            entry["reasons"].append(
                f"{role}: historical v1.0.0 bytes cannot certify v1.0.1")
            paths[role] = entry
            continue
        entry["artifact"] = {"path": norm, "sha256": digest,
                             "size_bytes": path.stat().st_size,
                             "source_sha": source_sha}
        if role == "msi":
            if msi_execution is None:
                entry["state"] = MSI_PENDING_STATE
                entry["reasons"].append(
                    "real MSI present but no install/uninstall/upgrade "
                    "execution record; honest prebuild state, never PASS")
            else:
                ok = (msi_execution.get("msi_sha256") == digest
                      and msi_execution.get("install_result") == "PASS"
                      and msi_execution.get("uninstall_result") == "PASS"
                      and msi_execution.get("upgrade_result") == "PASS")
                entry["execution_record"] = dict(msi_execution)
                entry["state"] = "PASS" if ok else "FAIL"
                if not ok:
                    entry["reasons"].append(
                        "MSI execution record incomplete or bound to "
                        "different bytes")
        else:
            entry["state"] = "PASS"
        paths[role] = entry
    states = [p["state"] for p in paths.values()]
    if "FAIL" in states:
        overall = "FAIL"
    elif MSI_PENDING_STATE in states or "PENDING" in states:
        overall = MSI_PENDING_STATE
    else:
        overall = "PASS"
    return {"schema": UPGRADE_PATH_SCHEMA, "release_version": "1.0.1",
            "source_sha": source_sha, "paths": paths, "overall": overall,
            "windows_only": True,
            "note": ("v1.0.1 promotion requires real MSI execution evidence "
                     "before the package gate can pass; mocks and v1.0.0 "
                     "bytes never satisfy this."),
            "generated_utc": _utc_now()}


def validate_upgrade_path_evidence(
    doc: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """Gate an upgrade-path evidence doc: no MSI PASS without a real,
    hash-matched execution record; consistent certified source SHA across
    all bound artifacts; no v1.0.0 bytes; D: paths only."""
    reasons: List[str] = []
    if doc.get("schema") != UPGRADE_PATH_SCHEMA:
        return "BLOCKED", ["unsupported upgrade-path evidence schema"]
    source_sha = doc.get("source_sha")
    paths = doc.get("paths", {})
    for role in ("exe", "msi", "portable"):
        entry = paths.get(role)
        if entry is None:
            reasons.append(f"missing {role} upgrade path evidence")
            continue
        art = entry.get("artifact") or {}
        if art:
            if art.get("source_sha") != source_sha:
                reasons.append(
                    f"{role}: artifact bound to a different source SHA")
            if art.get("sha256") in V100_ARTIFACT_HASHES:
                reasons.append(
                    f"{role}: historical v1.0.0 bytes as v1.0.1 evidence")
            if not str(art.get("path", "")).upper().startswith("D:"):
                reasons.append(f"{role}: C-drive path rejected")
        if role == "msi" and entry.get("state") == "PASS":
            rec = entry.get("execution_record") or {}
            if (rec.get("msi_sha256") != art.get("sha256")
                    or rec.get("install_result") != "PASS"
                    or rec.get("uninstall_result") != "PASS"
                    or rec.get("upgrade_result") != "PASS"):
                reasons.append(
                    "msi: PASS without a real hash-matched "
                    "install/uninstall/upgrade execution record — fabricated "
                    "MSI upgrade evidence rejected")
    if reasons:
        return "BLOCKED", reasons
    return doc.get("overall", "BLOCKED"), ["upgrade-path evidence consistent"]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate migration-certification evidence bundle (I2F)"
    )
    parser.add_argument("--backend", required=True,
                        help="Packaged amca-backend executable")
    parser.add_argument("--output", required=True,
                        help="Write evidence JSON bundle here")
    parser.add_argument("--manifest", default=None,
                        help="Release manifest for provenance")
    parser.add_argument("--package-role", default="validation",
                        choices=["validation", "final"])
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="Backend startup budget in seconds")
    parser.add_argument("--skip-pytest", action="store_true",
                        help="Omit pytest execution (verdicts BLOCKED)")
    args = parser.parse_args()

    evidence = generate_evidence(
        Path(args.backend),
        Path(args.output),
        manifest_path=Path(args.manifest) if args.manifest else None,
        package_role=args.package_role,
        timeout=args.timeout,
        skip_pytest=args.skip_pytest,
    )
    print(json.dumps({
        "schema": SCHEMA_ID,
        "overall": evidence["overall"],
        "package_sha256": evidence["package"]["sha256"],
        "output": str(args.output),
    }, indent=2))
    return 0 if evidence["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
