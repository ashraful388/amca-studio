"""AMCA Studio v1.0.0 real-package upgrade smoke tests (I2E).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2E-REAL-PACKAGE-UPGRADE-SMOKE-AND-EXISTING-USER-DATA-CERTIFICATION-01
Worker: OpenCode PS-2. Test/infrastructure only; no production-code changes.

Extends I2D (database-level migration certification) to the packaged
backend: a REAL amca-backend executable starts against a DISPOSABLE COPY of a
representative v0.9/pre-C1 user state, runs the approved startup migrations
(including the repaired ConversationStore migration), preserves data and
workspace isolation, and leaves the source fixture byte-identical.

Harness-level tests inject a mock launcher (real loopback HTTP health
endpoint); exactly one integration test boots the genuine packaged backend
(plus one corrupt-state integration case).

Rerun path (final release): set AMCA_I2E_BACKEND_EXE to the rebuilt final
canonical package and run this file unchanged.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import test_studio_v100_upgrade_migration_restore_certification as i2d

RUNNER_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "run_upgrade_smoke.py"
)
_spec = importlib.util.spec_from_file_location("run_upgrade_smoke", RUNNER_PATH)
assert _spec is not None and _spec.loader is not None
_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runner)

BACKEND_EXE = (
    Path(__file__).resolve().parent.parent / "backend-dist" / "amca-backend.exe"
)


def _resolve_backend() -> Path:
    """Packaged backend under test.

    Override with AMCA_I2E_BACKEND_EXE to exercise any other genuine build
    (e.g. an isolated rebuild, or the eventual final canonical package) with
    zero test changes — this is the documented rerun path for final release.
    """
    override = os.environ.get("AMCA_I2E_BACKEND_EXE")
    return Path(override) if override else BACKEND_EXE
MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent / "dist" / "release_manifest_v0.9.0.json"
)

WS_A = i2d.WS_A
WS_B = i2d.WS_B
PROJ_A = i2d.PROJ_A
PROJ_B = i2d.PROJ_B
TS = i2d.TS


# --------------------------------------------------------------------------
# Fixture: representative v0.9 user state, conversations rewound to pre-C1
# --------------------------------------------------------------------------

def _rewind_conversations_to_pre_c1(db_path: Path) -> None:
    """Restore the pre-C1 conversations shape inside an otherwise seeded DB.

    The I2D seed necessarily migrates on open; rewinding re-creates the exact
    legacy tables/rows so the PACKAGED backend (not the test process) performs
    the repaired migration at startup.
    """
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
             PROJ_A, None, "idle", TS, TS, None),
        )
        con.execute(
            "INSERT INTO desktop_conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("conv-legacy-b", "Legacy beta chat", "solo", '["agent-9"]',
             PROJ_B, None, "idle", TS, TS, None),
        )
        con.execute(
            "INSERT INTO desktop_conversation_messages VALUES (?,?,?,?,?,?,?,?,?)",
            ("msg-legacy-a", "conv-legacy-a", 1, "user",
             "Legacy hello from alpha.", None, None, PROJ_A, TS),
        )
        con.commit()
    finally:
        con.close()


def _stabilize_db(db_path: Path) -> None:
    """Checkpoint WAL sidecars into the DB file so its bytes are stable.

    Store closes do not guarantee a checkpoint; without this, the first
    later open (e.g. the runner's backup read) can checkpoint pending frames
    into the file, changing bytes without changing logical content and
    tripping the source-unchanged comparison. Sidecars themselves are
    transient and excluded from snapshots, so only the checkpoint matters.
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
    finally:
        con.close()


def _build_source_fixture(root: Path) -> Path:
    """Materialize the canonical source fixture (never mutated afterwards)."""
    fixture = root / "source_fixture"
    db_path = fixture / "v090_canonical.db"
    payloads = fixture / "payloads"
    i2d._create_v090_legacy_db(db_path)
    stores = i2d._open_all_stores(db_path, payloads)
    try:
        i2d._seed_current_shape_records(stores)
    finally:
        i2d._close_all_stores(stores)
    _rewind_conversations_to_pre_c1(db_path)
    _stabilize_db(db_path)
    manifest = {
        "fixture": "OPENCODE-PS2-I2E",
        "app_version": "0.9.0",
        "upgrade_target": "v1.0 candidate schema via packaged backend",
        "workspaces": [WS_A, WS_B],
        "projects": [PROJ_A, PROJ_B],
    }
    (fixture / "fixture_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    # Sanity: conversations are genuinely pre-C1 in the source fixture.
    cols = [r[1] for r in sqlite3.connect(str(db_path)).execute(
        "PRAGMA table_info(desktop_conversations)")]
    assert "workspace_project_id" not in cols
    return fixture


# --------------------------------------------------------------------------
# Mock launcher (real loopback HTTP health, fake process)
# --------------------------------------------------------------------------

class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            body = json.dumps(
                {"status": "ok", "protocol_version": "mock-1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


class _FakeProcess:
    def __init__(self, server: http.server.HTTPServer) -> None:
        self._server = server

    def terminate(self) -> None:
        self._server.shutdown()

    def kill(self) -> None:
        self._server.shutdown()

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int | None:
        return None


def _mock_launcher(
    backend_exe: Path, database: Path, data_root: Path, port: int
):
    server = http.server.HTTPServer(("127.0.0.1", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return _FakeProcess(server)


def _dead_launcher(backend_exe: Path, database: Path, data_root: Path, port: int):
    raise OSError("mock: backend executable crashed on launch")


def _silent_launcher(backend_exe: Path, database: Path, data_root: Path, port: int):
    class _Dead:
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 1

        def poll(self) -> int:
            return 1

    return _Dead()


def _big_stub(path: Path, size: int = 11_000_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\0")
    return path


# --------------------------------------------------------------------------
# Harness mechanics (mocked launch)
# --------------------------------------------------------------------------

def test_disposable_workspace_enforced(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    repo_dir = Path(__file__).resolve().parent  # inside the repository
    res = _runner.run_upgrade_smoke(
        backend, fixture, workspace=repo_dir, launcher=_mock_launcher)
    assert res["overall"] == "FAIL"
    assert any("disposable" in r for r in res["reasons"])
    home = Path.home()
    res = _runner.run_upgrade_smoke(
        backend, fixture, workspace=home / "amca-i2e-probe",
        launcher=_mock_launcher)
    assert res["overall"] == "FAIL"
    assert any("disposable" in r for r in res["reasons"])


def test_missing_backend_rejected(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    res = _runner.run_upgrade_smoke(
        tmp_path / "nope" / "amca-backend.exe", fixture,
        launcher=_mock_launcher)
    assert res["overall"] == "FAIL"
    assert any("not found" in r for r in res["reasons"])


def test_stub_backend_rejected_by_payload_floor(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    stub = tmp_path / "stub.exe"
    stub.write_bytes(b"MZ" + b"\0" * 1024)
    res = _runner.run_upgrade_smoke(stub, fixture, launcher=_mock_launcher)
    assert res["overall"] == "FAIL"
    assert any("payload floor" in r for r in res["reasons"])


def test_corrupt_fixture_rejected(tmp_path: Path):
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _runner.run_upgrade_smoke(backend, empty, launcher=_mock_launcher)
    assert res["overall"] == "FAIL"
    assert any("manifest" in r for r in res["reasons"])
    no_db = tmp_path / "no-db"
    no_db.mkdir()
    (no_db / "fixture_manifest.json").write_text("{}", encoding="utf-8")
    res = _runner.run_upgrade_smoke(backend, no_db, launcher=_mock_launcher)
    assert res["overall"] == "FAIL"
    assert any("v090_canonical.db" in r for r in res["reasons"])


def test_mock_upgrade_pass_and_source_untouched(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    before = _runner.snapshot_files(fixture)
    res = _runner.run_upgrade_smoke(
        backend, fixture, launcher=_mock_launcher, timeout=30.0)
    assert res["overall"] == "PASS", res["reasons"]
    assert res["schema"] == "amca-upgrade-smoke-result/1"
    assert res["migration"]["backend_healthy"] is True
    assert res["integrity"]["findings"] == []
    assert res["fixture"]["source_unchanged"] is True
    assert _runner.snapshot_files(fixture) == before


def test_package_hash_capture(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    expected = hashlib.sha256(backend.read_bytes()).hexdigest()
    res = _runner.run_upgrade_smoke(
        backend, fixture, launcher=_mock_launcher, timeout=30.0)
    assert res["overall"] == "PASS", res["reasons"]
    assert res["provenance"]["sha256"] == expected
    assert res["provenance"]["size_bytes"] == backend.stat().st_size


def test_launch_failure_propagates(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    before = _runner.snapshot_files(fixture)
    res = _runner.run_upgrade_smoke(
        backend, fixture, launcher=_dead_launcher, timeout=10.0)
    assert res["overall"] == "FAIL"
    assert any("launch" in r for r in res["reasons"])
    assert _runner.snapshot_files(fixture) == before


def test_health_timeout_propagates(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    res = _runner.run_upgrade_smoke(
        backend, fixture, launcher=_silent_launcher, timeout=2.0)
    assert res["overall"] == "FAIL"
    assert any("healthy" in r for r in res["reasons"])


def test_explicit_verdict_semantics(tmp_path: Path):
    fixture = _build_source_fixture(tmp_path)
    backend = _big_stub(tmp_path / "backend" / "amca-backend.exe")
    ok_result = _runner.run_upgrade_smoke(
        backend, fixture, launcher=_mock_launcher, timeout=30.0)
    assert set(ok_result) >= {
        "schema", "overall", "reasons", "backend", "fixture",
        "migration", "integrity", "provenance",
    }
    assert ok_result["overall"] in ("PASS", "FAIL", "BLOCKED")
    assert isinstance(ok_result["reasons"], list)


def test_cli_contract_failure_paths(tmp_path: Path):
    missing_out = tmp_path / "missing.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--backend",
         str(tmp_path / "missing.exe"), "--fixture", str(tmp_path),
         "--output", str(missing_out)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    payload = json.loads(missing_out.read_text(encoding="utf-8"))
    assert payload["overall"] == "FAIL"


# --------------------------------------------------------------------------
# Integration: genuine packaged backend
# --------------------------------------------------------------------------

needs_backend = pytest.mark.skipif(
    not _resolve_backend().is_file(), reason="packaged backend not present")


def _open_copy_db(copy_dir: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(copy_dir / "v090_canonical.db"))
    con.row_factory = sqlite3.Row
    return con


@needs_backend
def test_real_packaged_backend_upgrades_pre_c1_state(tmp_path: Path):
    backend = _resolve_backend()
    fixture = _build_source_fixture(tmp_path)
    source_before = _runner.snapshot_files(fixture)
    workspace = tmp_path / "disposable-ws"
    out = tmp_path / "result.json"
    manifest = MANIFEST_PATH if MANIFEST_PATH.is_file() else None
    proc = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--backend", str(backend),
         "--fixture", str(fixture), "--workspace", str(workspace),
         "--output", str(out)]
        + (["--manifest", str(manifest)] if manifest else []),
        capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["overall"] == "PASS", result["reasons"]
    assert result["migration"]["backend_healthy"] is True
    assert result["provenance"]["size_bytes"] == backend.stat().st_size
    assert result["provenance"]["sha256"] == hashlib.sha256(
        backend.read_bytes()).hexdigest()
    assert result["fixture"]["source_unchanged"] is True
    assert _runner.snapshot_files(fixture) == source_before

    copy_dir = workspace / "fixture-copy"
    con = _open_copy_db(copy_dir)
    try:
        cols = [r[1] for r in con.execute(
            "PRAGMA table_info(desktop_conversations)")]
        assert "workspace_project_id" in cols  # repaired migration ran
        conv = con.execute(
            "SELECT * FROM desktop_conversations "
            "WHERE conversation_id = 'conv-legacy-a'").fetchone()
        assert conv is not None and conv["mode"] == "joint"
        assert conv["title"] == "Legacy chat"
        msgs = con.execute(
            "SELECT COUNT(*) FROM desktop_conversation_messages "
            "WHERE conversation_id = 'conv-legacy-a'").fetchone()[0]
        assert msgs == 1
        mem = con.execute(
            "SELECT * FROM studio_project_memories "
            "WHERE memory_id = 'mem-legacy-a'").fetchone()
        assert mem is not None and mem["project_id"] == PROJ_A
        beta = con.execute(
            "SELECT COUNT(*) FROM studio_project_memories "
            "WHERE project_id = ?", (PROJ_B,)).fetchone()[0]
        assert beta >= 1
        cross = con.execute(
            "SELECT COUNT(*) FROM studio_project_memories "
            "WHERE project_id = ? AND memory_id LIKE 'mem-legacy-b'",
            (PROJ_A,)).fetchone()[0]
        assert cross == 0
    finally:
        con.close()


@needs_backend
def test_real_backend_rejects_corrupt_state(tmp_path: Path):
    backend = _resolve_backend()
    fixture = _build_source_fixture(tmp_path)
    (fixture / "v090_canonical.db").write_bytes(b"\x00not-a-database" * 64)
    workspace = tmp_path / "disposable-ws-bad"
    out = tmp_path / "bad-result.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--backend", str(backend),
         "--fixture", str(fixture), "--workspace", str(workspace),
         "--output", str(out), "--timeout", "30"],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode != 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["overall"] in ("FAIL", "BLOCKED")
    assert result["reasons"], "corrupt state must fail explicitly"
