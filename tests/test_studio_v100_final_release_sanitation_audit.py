"""Final release sanitation audit tests (I2K).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2K-FINAL-PRIVACY-SECRETS-AND-USER-DATA-RELEASE-SANITATION-AUDIT-01
Worker: OpenCode PS-2. Tests only; no production-code changes.

Covers the scanner contract: obvious API keys / bearer tokens / private-key
blocks detected; safe fingerprints never treated as raw secrets; synthetic
fixtures accepted; placeholder assignments accepted; user paths flagged;
database fixtures scanned; package mode behaves; verdict semantics
(PASS/REVIEW/FAIL) and CLI exit codes hold.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "run_release_sanitation_audit.py"
)
_spec = importlib.util.spec_from_file_location("sanitation_audit", SCRIPT)
assert _spec is not None and _spec.loader is not None
audit = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = audit  # required: dataclasses resolve via sys.modules
_spec.loader.exec_module(audit)


def test_obvious_api_key_detected(tmp_path: Path):
    target = tmp_path / "doc.md"
    target.write_text("config:\n  key: sk-proj-abcdefghij1234567890\n",
                      encoding="utf-8")
    findings = audit.scan_text(target.read_text(encoding="utf-8"), "doc.md")
    assert any(f.rule == "openai-key" and f.severity == "FAIL"
               for f in findings)


def test_bearer_token_detected(tmp_path: Path):
    findings = audit.scan_text(
        "auth: Bearer abcdefghijklmnop123456\n", "doc.md")
    assert any(f.rule == "bearer-token" and f.severity == "FAIL"
               for f in findings)


def test_private_key_block_detected():
    findings = audit.scan_text(
        "key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n", "doc.md")
    assert any(f.rule == "private-key-block" and f.severity == "FAIL"
               for f in findings)


def test_safe_fingerprint_not_treated_as_raw_secret():
    findings = audit.scan_text(
        "credential_ref: model-hub.provider.abc\n"
        "key_fingerprint: sha256:8a20f622122a0bae\n"
        "approval: OP-APP-20260902-01\n",
        "doc.md")
    assert not [f for f in findings if f.severity == "FAIL"]


def test_placeholder_assignments_accepted():
    findings = audit.scan_text(
        "password: PLACEHOLDER\napi_key = <YOUR_KEY>\n"
        "token = test-value\nsecret=[REDACTED]\n",
        "config.txt")
    assert not [f for f in findings if f.severity == "FAIL"]


def test_user_paths_flagged_for_review():
    findings = audit.scan_text(
        "state at C:\\Users\\someone\\amca\\db.sqlite3\n", "doc.md")
    assert any(f.rule == "windows-user-path" and f.severity == "REVIEW"
               for f in findings)
    clean = audit.scan_text("state in the chosen folder\n", "doc.md")
    assert clean == []


def test_synthetic_fixture_accepted(tmp_path: Path):
    db = tmp_path / "fixture.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, "
                    "project_id TEXT, content TEXT)")
        con.execute("INSERT INTO memories VALUES ('mem-1', 'proj-a', "
                    "'The alpha workspace prefers local models.')")
        con.commit()
    finally:
        con.close()
    assert audit.scan_database(db, "fixture.db") == []


def test_fixture_email_reviewed(tmp_path: Path):
    db = tmp_path / "fixture.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO t VALUES ('a', 'contact real.person@example.com')")
        con.commit()
    finally:
        con.close()
    findings = audit.scan_database(db, "fixture.db")
    assert any(f.rule == "email-in-fixture" and f.severity == "REVIEW"
               for f in findings)


def test_audit_tree_verdict_semantics(tmp_path: Path):
    root = tmp_path / "tree"
    (root / "docs" / "release").mkdir(parents=True)
    (root / "docs" / "release" / "notes.md").write_text(
        "All local. Fingerprint sha256:abcd1234.\n", encoding="utf-8")
    assert audit.audit_tree(root).overall == "PASS"
    (root / "docs" / "release" / "leak.md").write_text(
        "key: sk-proj-abcdefghij1234567890\n", encoding="utf-8")
    result = audit.audit_tree(root)
    assert result.overall == "FAIL"
    assert any(f.severity == "FAIL" for f in result.findings)
    (root / "docs" / "release" / "leak.md").unlink()
    (root / "docs" / "release" / "odd.md").write_text(
        "state at C:\\Users\\someone\\x\n", encoding="utf-8")
    assert audit.audit_tree(root).overall == "REVIEW"


def test_package_inspection_modes(tmp_path: Path):
    import zipfile

    package = tmp_path / "portable.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", '{"version": "0.9.0"}')
    result = audit.inspect_package(package)
    assert result.overall == "PASS"
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("notes.md", "key: sk-proj-abcdefghij1234567890\n")
    assert audit.inspect_package(evil).overall == "FAIL"
    missing = audit.inspect_package(tmp_path / "missing.zip")
    assert missing.overall == "FAIL"


def test_cli_exit_codes(tmp_path: Path):
    root = tmp_path / "tree"
    (root / "docs" / "release").mkdir(parents=True)
    (root / "docs" / "release" / "notes.md").write_text("clean\n",
                                                        encoding="utf-8")
    out = tmp_path / "result.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root),
         "--output", str(out)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["overall"] == "PASS"
    (root / "docs" / "release" / "leak.md").write_text(
        "-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root),
         "--output", str(out)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1
    assert json.loads(out.read_text(encoding="utf-8"))["overall"] == "FAIL"
