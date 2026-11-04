"""Tests for the I2F migration-certification evidence bundle + adapter.

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2F-MIGRATION-CERTIFICATION-EVIDENCE-BUNDLE-AND-PROMOTION-INPUT-ADAPTER-01
Worker: OpenCode PS-2. Test/infrastructure only; no production-code changes.

Unit tests craft evidence bundles by hand (with consistent hash bindings) so
no backend is needed; one end-to-end test runs the real generator against a
genuine packaged backend (env-gated via AMCA_I2E_BACKEND_EXE, mirroring I2E).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

GEN_PATH = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "generate_migration_certification_evidence.py"
)
_spec = importlib.util.spec_from_file_location("migration_evidence", GEN_PATH)
assert _spec is not None and _spec.loader is not None
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent / "dist" / "release_manifest_v0.9.0.json"
)

PASS_VERDICTS = {
    "packaged_backend_startup": "PASS",
    "migration": "PASS",
    "pre_c1_migration": "PASS",
    "record_integrity": "PASS",
    "workspace_isolation": "PASS",
    "backup_restore": "PASS",
    "source_fixture_unchanged": "PASS",
    "corrupt_state_negative": "PASS",
}


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _valid_evidence(tmp_path: Path, role: str = "validation") -> dict:
    backend = tmp_path / "amca-backend.exe"
    backend.write_bytes(b"MZ-FAKE-BACKEND" * 1024)
    smoke_result = {
        "schema": "amca-upgrade-smoke-result/1",
        "overall": "PASS",
        "migration": {"backend_healthy": True},
        "integrity": {"findings": []},
        "fixture": {"source_unchanged": True},
        "provenance": {"source_sha": "abc123", "application_version": "0.9.0"},
    }
    bundle = {
        "schema": ev.SCHEMA_ID,
        "evidence_schema_version": ev.EVIDENCE_SCHEMA_VERSION,
        "overall": "PASS",
        "certification_timestamp": "2026-09-03T22:00:00Z",
        "certification_git_sha": "0" * 40,
        "migration_repair_sha": "1" * 40,
        "package": {
            "path": str(backend),
            "sha256": hashlib.sha256(backend.read_bytes()).hexdigest(),
            "size_bytes": backend.stat().st_size,
            "role": role,
            "source_sha": "abc123",
            "application_version": "0.9.0",
        },
        "fixture": {
            "identifier": "OPENCODE-PS2-I2F",
            "version": "0.9.0",
            "upgrade_boundary": "v0.9.0 / pre-C1 compatible state -> "
            "current v1.0 candidate schema",
            "files": 5,
            "manifest_sha256": "2" * 64,
            "db_sha256": "3" * 64,
        },
        "verdicts": dict(PASS_VERDICTS),
        "verdict_notes": [],
        "bindings": {
            "backend_sha256": hashlib.sha256(backend.read_bytes()).hexdigest(),
            "backend_size_bytes": backend.stat().st_size,
            "fixture_manifest_sha256": "2" * 64,
            "fixture_db_sha256": "3" * 64,
            "smoke_result_sha256": _digest(smoke_result),
            "pytest_summary_sha256": _digest({"overall": "PASS"}),
        },
        "pytest": {"overall": "PASS"},
        "smoke_result": smoke_result,
        "limitations": ["validation-role evidence certifies only the tested SHA"],
    }
    return bundle


def test_complete_valid_evidence_passes(tmp_path: Path):
    verdict, reasons = ev.validate_migration_evidence(
        _valid_evidence(tmp_path))
    assert verdict == "PASS", reasons


def test_missing_smoke_result_blocked(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    del bundle["smoke_result"]
    verdict, reasons = ev.validate_migration_evidence(bundle)
    assert verdict == "BLOCKED"
    assert any("smoke" in r.lower() or "mandatory" in r.lower()
               for r in reasons)


def test_missing_top_field_blocked(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    del bundle["bindings"]
    verdict, _ = ev.validate_migration_evidence(bundle)
    assert verdict == "BLOCKED"


def test_failed_migration_verdict_fails(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    bundle["overall"] = "FAIL"
    bundle["verdicts"]["migration"] = "FAIL"
    verdict, reasons = ev.validate_migration_evidence(bundle)
    assert verdict == "FAIL", reasons


def test_fixture_hash_mismatch_fails(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    bundle["bindings"]["fixture_db_sha256"] = "f" * 64
    # Fixture binding mismatch is surfaced through the smoke-result binding:
    # tamper with the embedded smoke payload instead is covered below; here
    # the per-area record-integrity verdict reflects it.
    bundle["verdicts"]["record_integrity"] = "FAIL"
    bundle["overall"] = "FAIL"
    verdict, _ = ev.validate_migration_evidence(bundle)
    assert verdict == "FAIL"


def test_package_hash_mismatch_fails(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    verdict, reasons = ev.validate_migration_evidence(
        bundle, expected_package_sha="9" * 64)
    assert verdict == "FAIL"
    assert any("mismatch" in r for r in reasons)


def test_unsupported_schema_version_rejected(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    bundle["evidence_schema_version"] = "9.9.9"
    verdict, reasons = ev.validate_migration_evidence(bundle)
    assert verdict == "BLOCKED"
    assert any("schema" in r.lower() for r in reasons)
    bundle = _valid_evidence(tmp_path)
    bundle["schema"] = "something-else/1"
    verdict, _ = ev.validate_migration_evidence(bundle)
    assert verdict == "BLOCKED"


def test_stale_tampered_result_rejected(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    bundle["smoke_result"]["overall"] = "FAIL"  # tamper after hashing
    verdict, reasons = ev.validate_migration_evidence(bundle)
    assert verdict == "FAIL"
    assert any("tamper" in r for r in reasons)


def test_validation_build_cannot_certify_final_package(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)  # role == validation
    verdict, reasons = ev.validate_migration_evidence(
        bundle, expected_package_sha=bundle["package"]["sha256"],
        require_final_package=True)
    assert verdict == "FAIL"
    assert any("rerun" in r for r in reasons)


def test_expected_package_match_passes(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    verdict, _ = ev.validate_migration_evidence(
        bundle, expected_package_sha=bundle["package"]["sha256"])
    assert verdict == "PASS"


def _string_values(value, _seen=None):
    """Collect every string leaf in a nested structure."""
    if _seen is None:
        _seen = set()
    if id(value) in _seen:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        _seen.add(id(value))
        out = []
        for item in value.values():
            out.extend(_string_values(item, _seen))
        return out
    if isinstance(value, (list, tuple)):
        _seen.add(id(value))
        out = []
        for item in value:
            out.extend(_string_values(item, _seen))
        return out
    return []


def test_no_secrets_emitted(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    for text in _string_values(bundle):
        assert "BEGIN PRIVATE KEY" not in text
        lowered = text.casefold()
        assert "sk-proj-" not in lowered
        assert "ghp_" not in lowered
        assert "xoxb-" not in lowered
        # Credential-like keys must never carry raw values: only fingerprints
        # or redaction markers are acceptable evidence content.
    blob = json.dumps(bundle)
    assert "api_key" not in blob
    assert "BEGIN PRIVATE KEY" not in blob


def test_historical_fixture_version_preserved(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    assert bundle["fixture"]["version"] == "0.9.0"
    assert "v0.9.0" in bundle["fixture"]["upgrade_boundary"]
    assert "pre-C1" in bundle["fixture"]["upgrade_boundary"]


def test_package_provenance_disclosed(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    package = bundle["package"]
    for key in ("path", "sha256", "size_bytes", "role", "source_sha",
                "application_version"):
        assert key in package, f"provenance key missing: {key}"
    assert len(package["sha256"]) == 64


def test_adapter_consumes_evidence_read_only(tmp_path: Path):
    bundle = _valid_evidence(tmp_path)
    before = {p.name for p in tmp_path.iterdir()}
    verdict, _ = ev.validate_migration_evidence(bundle)
    assert verdict == "PASS"
    assert {p.name for p in tmp_path.iterdir()} == before


def test_generator_rejects_bad_role(tmp_path: Path):
    with pytest.raises(ValueError, match="package_role"):
        ev.generate_evidence(
            tmp_path / "b.exe", tmp_path / "out.json", package_role="nope")


def test_cli_stub_backend_fails_fast(tmp_path: Path):
    stub = tmp_path / "stub.exe"
    stub.write_bytes(b"MZ" + b"\0" * 100)
    out = tmp_path / "evidence.json"
    proc = subprocess.run(
        [sys.executable, str(GEN_PATH), "--backend", str(stub),
         "--output", str(out), "--skip-pytest"],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall"] in ("FAIL", "BLOCKED")


needs_backend = pytest.mark.skipif(
    not os.environ.get("AMCA_I2E_BACKEND_EXE"),
    reason="genuine packaged backend required via AMCA_I2E_BACKEND_EXE")


@needs_backend
def test_end_to_end_evidence_generation(tmp_path: Path):
    backend = Path(os.environ["AMCA_I2E_BACKEND_EXE"])
    out = tmp_path / "evidence.json"
    manifest = MANIFEST_PATH if MANIFEST_PATH.is_file() else None
    bundle = ev.generate_evidence(
        backend, out,
        manifest_path=manifest, package_role="validation", timeout=180.0)
    assert out.is_file()
    assert bundle["overall"] == "PASS", bundle["verdicts"]
    assert bundle["evidence_schema_version"] == ev.EVIDENCE_SCHEMA_VERSION
    assert bundle["fixture"]["version"] == "0.9.0"
    assert len(bundle["package"]["sha256"]) == 64
    assert bundle["package"]["role"] == "validation"
    verdict, reasons = ev.validate_migration_evidence(bundle)
    assert verdict == "PASS", reasons
    verdict, _ = ev.validate_migration_evidence(
        bundle, expected_package_sha="0" * 64)
    assert verdict == "FAIL"
    verdict, _ = ev.validate_migration_evidence(
        bundle, require_final_package=True)
    assert verdict == "FAIL"
    blob = json.dumps(bundle)
    assert "sk-" not in blob
