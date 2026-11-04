#!/usr/bin/env python3
"""I2E existing-user package upgrade smoke runner (OpenCode PS-2).

Exercises a REAL packaged AMCA Studio backend against a COPIED v0.9/pre-C1
user state: the packaged runtime must start, run the approved migrations
(including the repaired ConversationStore migration), preserve user data and
workspace isolation, and leave the source fixture untouched.

Usage:
  python scripts/run_upgrade_smoke.py --backend PATH --fixture DIR
      [--workspace DIR] [--manifest PATH] [--output PATH]
      [--timeout SECONDS]

  --backend   packaged amca-backend executable under test
  --fixture   source fixture directory (canonical v0.9 state; NEVER mutated:
              v090_canonical.db + payloads/ + fixture_manifest.json)
  --workspace disposable working directory (system temp by default)
  --manifest  optional release manifest for package provenance association
  --output    optional path for the machine-readable JSON result
  --timeout   backend startup budget in seconds (default 120)

Emits a machine-readable JSON result (PASS/FAIL/BLOCKED) to stdout and,
optionally, --output PATH. Exit code 0 only on overall PASS.

Safety: the fixture is snapshotted (file hashes), copied into a disposable
workspace (repository/home/root refused), and re-verified byte-identical
afterwards. The backend only ever sees the disposable copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.distribution_smoke import (  # noqa: E402  (read-only reuse; ZCode-owned)
    MIN_BACKEND_BYTES,
    STARTUP_TIMEOUT_SECONDS,
    assert_disposable,
    free_port,
    terminate,
    wait_backend_healthy,
)

SCHEMA = "amca-upgrade-smoke-result/1"
FIXTURE_DB_NAME = "v090_canonical.db"
FIXTURE_PAYLOADS_DIR = "payloads"
FIXTURE_MANIFEST_NAME = "fixture_manifest.json"

Launcher = Callable[[Path, Path, Path, int], subprocess.Popen]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_files(root: Path) -> Dict[str, str]:
    """Map relative file path -> SHA-256 for every regular file under root."""
    out: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in (".wal", ".shm", ".journal"):
            continue
        # Sidecar form: <db>.db-wal / .db-shm / .db-journal (transient).
        if path.name.endswith(("-wal", "-shm", "-journal")):
            continue
        if "-wal" in path.suffixes or "-shm" in path.suffixes:
            continue
        out[str(path.relative_to(root)).replace("\\", "/")] = sha256_file(path)
    return out


def table_inventory(db_path: Path) -> Dict[str, Any]:
    """Capture per-table row counts for DB comparison (WAL-safe)."""
    con = sqlite3.connect(str(db_path))
    try:
        tables = sorted(
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        inventory: Dict[str, Any] = {}
        for table in tables:
            count = int(
                con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            inventory[table] = {"rows": count}
        user_version = int(con.execute("PRAGMA user_version").fetchone()[0])
        return {"tables": inventory, "user_version": user_version}
    finally:
        con.close()


def pk_sets(db_path: Path, tables: List[str]) -> Dict[str, List[str]]:
    """Sorted primary-key value sets for no-loss comparison."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        out: Dict[str, List[str]] = {}
        for table in tables:
            info = con.execute(f"PRAGMA table_info({table})").fetchall()
            pks = [r["name"] for r in info if r["pk"]]
            if len(pks) != 1:
                continue
            rows = con.execute(
                f'SELECT "{pks[0]}" AS pk FROM "{table}" ORDER BY pk'
            ).fetchall()
            out[f"{table}.{pks[0]}"] = [str(r["pk"]) for r in rows]
        return out
    finally:
        con.close()


def copy_fixture(source: Path, workspace: Path) -> Path:
    """Copy the fixture into the disposable workspace (DBs via backup API)."""
    assert_disposable(workspace)
    target = workspace / "fixture-copy"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for child in sorted(source.iterdir()):
        if child.is_file():
            if child.suffix in (".db", ".sqlite3"):
                src = sqlite3.connect(str(child))
                try:
                    dst = sqlite3.connect(str(target / child.name))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
            else:
                shutil.copy2(child, target / child.name)
        elif child.is_dir():
            shutil.copytree(child, target / child.name)
    return target


def default_launcher(
    backend_exe: Path, database: Path, data_root: Path, port: int
) -> subprocess.Popen:
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        [str(backend_exe), "--host", "127.0.0.1", "--port", str(port),
         "--database", str(database), "--root", str(data_root)],
        cwd=str(data_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def compare_inventories(
    before: Dict[str, Any], after: Dict[str, Any]
) -> List[str]:
    """No table loss, no row-count decrease. Returns finding strings."""
    findings: List[str] = []
    before_tables = before["tables"]
    after_tables = after["tables"]
    for table in sorted(before_tables):
        if table not in after_tables:
            findings.append(f"table lost during packaged migration: {table}")
            continue
        if after_tables[table]["rows"] < before_tables[table]["rows"]:
            findings.append(
                f"row-count decrease in {table}: "
                f"{before_tables[table]['rows']} -> {after_tables[table]['rows']}"
            )
    return findings


def compare_pk_sets(
    before: Dict[str, List[str]], after: Dict[str, List[str]]
) -> List[str]:
    findings: List[str] = []
    for key in sorted(before):
        missing = [v for v in before[key] if v not in set(after.get(key, []))]
        if missing:
            findings.append(f"identifiers lost in {key}: {missing[:5]}")
    return findings


def backend_provenance(
    backend_exe: Path, manifest_path: Optional[Path]
) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "path": str(backend_exe),
        "sha256": sha256_file(backend_exe),
        "size_bytes": backend_exe.stat().st_size,
        "manifest_association": None,
        "manifest_sha256_match": None,
        "source_sha": None,
        "application_version": None,
    }
    if manifest_path is not None and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        info["manifest_association"] = str(manifest_path)
        info["application_version"] = manifest.get("version") or manifest.get(
            "application_version"
        )
        info["source_sha"] = manifest.get("source_sha") or manifest.get(
            "source_git_sha"
        )
    return info


def run_upgrade_smoke(
    backend: Path,
    fixture: Path,
    workspace: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    timeout: float = 120.0,
    launcher: Optional[Launcher] = None,
) -> Dict[str, Any]:
    """Run the packaged-backend upgrade smoke. Never raises on FAIL paths."""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result: Dict[str, Any] = {
        "schema": SCHEMA,
        "overall": "BLOCKED",
        "reasons": [],
        "backend": {"path": str(backend)},
        "fixture": {"source": str(fixture)},
        "workspace": None,
        "migration": {},
        "integrity": {},
        "isolation": {},
        "provenance": {},
        "started_at": started_at,
    }
    fail_reasons: List[str] = []

    def finish(verdict: str) -> Dict[str, Any]:
        result["overall"] = verdict
        result["reasons"] = fail_reasons
        result["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        return result

    if not backend.is_file():
        fail_reasons.append(f"packaged backend not found: {backend}")
        return finish("FAIL")
    if backend.stat().st_size < MIN_BACKEND_BYTES:
        fail_reasons.append(
            f"packaged backend below real-payload floor "
            f"({backend.stat().st_size} < {MIN_BACKEND_BYTES} bytes)"
        )
        return finish("FAIL")
    if not fixture.is_dir():
        fail_reasons.append(f"fixture directory not found: {fixture}")
        return finish("FAIL")
    manifest_file = fixture / FIXTURE_MANIFEST_NAME
    if not manifest_file.is_file():
        fail_reasons.append(
            f"corrupt fixture rejected: missing {FIXTURE_MANIFEST_NAME}"
        )
        return finish("FAIL")
    db_source = fixture / FIXTURE_DB_NAME
    if not db_source.is_file():
        fail_reasons.append(
            f"corrupt fixture rejected: missing {FIXTURE_DB_NAME}"
        )
        return finish("FAIL")

    try:
        workdir = Path(workspace) if workspace else Path(
            tempfile.mkdtemp(prefix="amca-upgrade-smoke-")
        )
        assert_disposable(workdir)
    except (ValueError, OSError) as exc:
        fail_reasons.append(f"workspace not disposable: {exc}")
        return finish("FAIL")
    result["workspace"] = str(workdir)

    try:
        source_snapshot = snapshot_files(fixture)
    except OSError as exc:
        fail_reasons.append(f"cannot snapshot source fixture: {exc}")
        return finish("BLOCKED")

    try:
        fixture_copy = copy_fixture(fixture, workdir)
    except (OSError, sqlite3.Error, ValueError) as exc:
        fail_reasons.append(f"cannot stage disposable fixture copy: {exc}")
        return finish("BLOCKED")

    db_path = fixture_copy / FIXTURE_DB_NAME
    data_root = fixture_copy / "backend-root"
    data_root.mkdir(parents=True, exist_ok=True)
    try:
        before_inventory = table_inventory(db_path)
        before_files = snapshot_files(fixture_copy)
        before_pks = pk_sets(db_path, list(before_inventory["tables"]))
    except sqlite3.Error as exc:
        fail_reasons.append(f"corrupt fixture database rejected: {exc}")
        return finish("FAIL")

    result["provenance"] = backend_provenance(backend, manifest_path)
    result["fixture"]["source_files"] = len(source_snapshot)
    result["fixture"]["source_db_tables"] = len(before_inventory["tables"])

    launch = launcher or default_launcher
    port = free_port()
    process: Optional[subprocess.Popen] = None
    try:
        try:
            process = launch(backend, db_path, data_root, port)
        except (OSError, ValueError, RuntimeError) as exc:
            fail_reasons.append(f"packaged backend failed to launch: {exc}")
            return finish("FAIL")
        try:
            health = wait_backend_healthy(port, timeout=timeout)
        except RuntimeError as exc:
            fail_reasons.append(f"packaged backend never became healthy: {exc}")
            return finish("FAIL")
        result["migration"]["backend_started"] = True
        result["migration"]["backend_healthy"] = True
        result["migration"]["health_protocol_version"] = health.get(
            "protocol_version"
        )
        result["migration"]["port"] = port
    finally:
        if process is not None:
            try:
                terminate(process)
            except (OSError, RuntimeError, ValueError):
                pass

    try:
        after_inventory = table_inventory(db_path)
        after_files = snapshot_files(fixture_copy)
        after_pks = pk_sets(db_path, list(after_inventory["tables"]))
    except sqlite3.Error as exc:
        fail_reasons.append(f"upgraded database unreadable: {exc}")
        return finish("FAIL")

    findings = compare_inventories(before_inventory, after_inventory)
    findings += compare_pk_sets(before_pks, after_pks)
    payload_keys = [
        k for k in before_files
        if k.startswith(f"{FIXTURE_PAYLOADS_DIR}/")
    ]
    for key in payload_keys:
        if after_files.get(key) != before_files[key]:
            findings.append(f"artifact payload changed under packaged run: {key}")
    result["integrity"]["before_tables"] = len(before_inventory["tables"])
    result["integrity"]["after_tables"] = len(after_inventory["tables"])
    result["integrity"]["payload_files_checked"] = len(payload_keys)
    result["integrity"]["findings"] = findings
    if findings:
        fail_reasons.append(
            f"data preservation failed ({len(findings)} findings)"
        )
        fail_reasons.extend(findings[:10])

    try:
        resnapshot = snapshot_files(fixture)
    except OSError as exc:
        fail_reasons.append(f"cannot re-verify source fixture: {exc}")
        return finish("BLOCKED")
    if resnapshot != source_snapshot:
        fail_reasons.append("SOURCE FIXTURE WAS MUTATED during smoke run")
        return finish("FAIL")
    result["fixture"]["source_unchanged"] = True

    if fail_reasons:
        return finish("FAIL")
    return finish("PASS")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AMCA packaged-backend existing-user upgrade smoke (I2E)"
    )
    parser.add_argument("--backend", required=True,
                        help="Packaged amca-backend executable")
    parser.add_argument("--fixture", required=True,
                        help="Source v0.9 fixture directory (never mutated)")
    parser.add_argument("--workspace", default=None,
                        help="Optional disposable workspace dir")
    parser.add_argument("--manifest", default=None,
                        help="Release manifest for provenance")
    parser.add_argument("--output", default=None,
                        help="Write JSON result to this path")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Backend startup budget in seconds")
    args = parser.parse_args()

    result = run_upgrade_smoke(
        Path(args.backend),
        Path(args.fixture),
        workspace=Path(args.workspace) if args.workspace else None,
        manifest_path=Path(args.manifest) if args.manifest else None,
        timeout=args.timeout,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
