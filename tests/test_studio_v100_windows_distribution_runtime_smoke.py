"""I2D tests: Windows distribution runtime smoke automation.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2D-WINDOWS-INSTALLER-PORTABLE-RUNTIME-SMOKE-AUTOMATION-01

Harness-level unit tests use synthetic/mock artifacts; the integration test
exercises the real built binaries when they are present on this machine.
"""

from pathlib import Path
import json
import subprocess
import sys
import zipfile

import pytest

from amca.distribution_smoke import (
    assert_disposable,
    discover_executables,
    extract_portable,
    inspect_artifact_provenance,
    make_disposable_workspace,
    run_smoke,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
DIST_DIR = SOFTWARE_ROOT / "dist"
RUNNER = SOFTWARE_ROOT / "scripts" / "run_distribution_smoke.py"

INSTALLER = DIST_DIR / "amca-studio-v0.9.0-windows-x64-setup.exe"
PORTABLE = DIST_DIR / "amca-studio-v0.9.0-windows-x64-portable.zip"


# ---------------------------------------------------------------------------
# Disposable-location enforcement
# ---------------------------------------------------------------------------

def test_harness_refuses_repository_locations():
    with pytest.raises(ValueError, match="non-disposable"):
        assert_disposable(SOFTWARE_ROOT)
    with pytest.raises(ValueError, match="non-disposable"):
        assert_disposable(REPO_ROOT.parent)


def test_harness_refuses_user_home():
    from pathlib import Path as _P

    home = _P.home()
    with pytest.raises(ValueError, match="non-disposable"):
        assert_disposable(home)
    with pytest.raises(ValueError, match="non-disposable"):
        assert_disposable(home / "Documents")


def test_harness_accepts_generated_disposable_workspace():
    ws = make_disposable_workspace()
    try:
        assert ws.exists()
        assert_disposable(ws)  # must not raise
    finally:
        import shutil

        shutil.rmtree(ws, ignore_errors=True)


# ---------------------------------------------------------------------------
# Portable extraction integrity + executable discovery
# ---------------------------------------------------------------------------

def _make_synthetic_portable(tmp_path: Path, backend_mb: int = 11) -> Path:
    zip_path = tmp_path / "portable-test.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr(
            "pkg/amca-backend.exe",
            b"MZ" + b"B" * (backend_mb * 1000 * 1000),
        )
        z.writestr("pkg/amca-desktop.exe", b"MZ" + b"D" * 6_000_000)
    return zip_path


def test_portable_extraction_and_discovery(tmp_path):
    artifact = _make_synthetic_portable(tmp_path)
    extracted = extract_portable(artifact, tmp_path)
    exes = discover_executables(extracted)
    assert exes["backend"].name == "amca-backend.exe"
    assert exes["desktop"].name == "amca-desktop.exe"


def test_portable_with_missing_desktop_fails_discovery(tmp_path):
    zip_path = tmp_path / "backend-only.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("pkg/amca-backend.exe", b"MZ" + b"B" * 11_000_000)
    extracted = extract_portable(zip_path, tmp_path)
    with pytest.raises(FileNotFoundError, match="amca-desktop.exe"):
        discover_executables(extracted)


def test_portable_with_stub_sized_backend_fails(tmp_path):
    zip_path = tmp_path / "stub.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("pkg/amca-backend.exe", b"MZ" + b"tiny")
        z.writestr("pkg/amca-desktop.exe", b"MZ" + b"D" * 6_000_000)
    extracted = extract_portable(zip_path, tmp_path)
    with pytest.raises(ValueError, match="below real-payload floor"):
        discover_executables(extracted)


def test_portable_with_unsafe_paths_rejected(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("../escape.exe", b"MZ")
    with pytest.raises(ValueError, match="unsafe paths"):
        extract_portable(zip_path, tmp_path)


# ---------------------------------------------------------------------------
# Artifact provenance verification
# ---------------------------------------------------------------------------

def test_artifact_provenance_hashes_and_manifest_match(tmp_path):
    artifact = _make_synthetic_portable(tmp_path)
    manifest = tmp_path / "manifest.json"
    import hashlib

    data = artifact.read_bytes()
    manifest.write_text(json.dumps({
        "version": "0.9.0",
        "provenance": {"source_git_sha": "a" * 40},
        "artifacts": [{
            "package_name": artifact.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }],
    }), encoding="utf-8")

    prov = inspect_artifact_provenance(artifact, manifest)
    assert prov.manifest_sha256_match is True
    assert prov.version == "0.9.0"
    assert prov.source_sha == "a" * 40


def test_artifact_hash_mismatch_is_recorded(tmp_path):
    artifact = _make_synthetic_portable(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "0.9.0",
        "artifacts": [{
            "package_name": artifact.name,
            "sha256": "0" * 64,
            "size_bytes": 1,
        }],
    }), encoding="utf-8")

    prov = inspect_artifact_provenance(artifact, manifest)
    assert prov.manifest_sha256_match is False
    # The smoke must FAIL an artifact that does not match its manifest.
    result = run_smoke(
        artifact, mode="portable", workspace=tmp_path / "ws", manifest_path=manifest
    )
    assert result["overall"] == "FAIL"
    assert any("manifest" in r for r in result["reasons"])


def test_smoke_result_identifies_exact_artifact(tmp_path):
    artifact = _make_synthetic_portable(tmp_path)
    result = run_smoke(
        artifact, mode="portable", workspace=tmp_path / "ws"
    )
    prov = result["artifact_provenance"]
    assert prov["artifact_path"] == str(artifact)
    assert len(prov["sha256"]) == 64


# ---------------------------------------------------------------------------
# Failure propagation (harness level, synthetic binaries)
# ---------------------------------------------------------------------------

def test_smoke_fails_when_backend_never_becomes_healthy(tmp_path, monkeypatch):
    """A desktop exe that launches fine but a backend that dies instantly
    must FAIL with honest failure propagation."""
    import amca.distribution_smoke as smoke_mod

    # Backend stub: exits immediately (broken build).
    backend = tmp_path / "amca-backend.exe"
    desktop = tmp_path / "amca-desktop.exe"
    backend.write_bytes(b"MZ" + b"B" * 11_000_000)
    desktop.write_bytes(b"MZ" + b"D" * 6_000_000)

    called = {}
    real_run_backend = smoke_mod.run_backend_smoke

    def fake_run_backend(backend_exe, workspace):
        called["attempted"] = True
        return {
            "started": False, "healthy": False,
            "local_state_initialized": False,
            "database_created": False,
            "reason": "backend exited during startup (stub simulation)",
        }

    # Simulate at the orchestration layer: portable smoke with a dying backend.
    monkeypatch.setattr(smoke_mod, "run_backend_smoke", fake_run_backend)
    zip_path = tmp_path / "broken.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("pkg/amca-backend.exe", backend.read_bytes())
        z.writestr("pkg/amca-desktop.exe", desktop.read_bytes())

    result = run_smoke(zip_path, mode="portable", workspace=tmp_path / "ws")
    assert called.get("attempted") is True
    assert result["backend_initialization"]["healthy"] is False
    # Backend unhealthy => overall FAIL, never PASS.
    assert result["overall"] == "FAIL"


def test_smoke_fails_when_desktop_exits_immediately(tmp_path, monkeypatch):
    import amca.distribution_smoke as smoke_mod

    zip_path = tmp_path / "crashy.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("pkg/amca-backend.exe", b"MZ" + b"B" * 11_000_000)
        z.writestr("pkg/amca-desktop.exe", b"MZ" + b"D" * 6_000_000)

    real_backend = smoke_mod.run_backend_smoke
    monkeypatch.setattr(
        smoke_mod,
        "run_backend_smoke",
        lambda b, w: {"started": True, "healthy": True,
                      "local_state_initialized": True,
                      "database_created": True},
    )
    monkeypatch.setattr(
        smoke_mod, "PROCESS_SETTLE_SECONDS", 0.2
    )

    def dying_popen(*args, **kwargs):
        class DeadProcess:
            pid = 0
            def poll(self):
                return 1
            @property
            def returncode(self):
                return 1
        return DeadProcess()

    monkeypatch.setattr(smoke_mod.subprocess, "Popen", dying_popen)
    result = run_smoke(zip_path, mode="portable", workspace=tmp_path / "ws")
    assert result["overall"] == "FAIL"
    assert any("exited immediately" in r for r in result["reasons"])


# ---------------------------------------------------------------------------
# Runner integration
# ---------------------------------------------------------------------------

def test_runner_emits_machine_readable_result(tmp_path):
    artifact = _make_synthetic_portable(tmp_path)
    out = tmp_path / "result.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--artifact", str(artifact),
         "--mode", "portable", "--workspace", str(tmp_path / "ws"),
         "--output", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["schema"] == "amca-distribution-smoke-result/1"
    for key in ("artifact_path", "sha256", "timestamp_utc", "mode",
                "installation_or_extraction", "launch",
                "backend_initialization", "cleanup", "overall"):
        assert key in result["artifact_provenance"] or key in result, key
    # Backend init from a synthetic exe will fail -> FAIL, and runner exit != 0
    assert result["overall"] == "FAIL"
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Real-binary integration mode (genuine built artifacts on this machine)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_real_portable_smoke_with_built_binaries():
    if not PORTABLE.is_file():
        pytest.skip("no built portable artifact on this machine")
    result = run_smoke(PORTABLE, mode="portable")
    assert result["overall"] == "PASS", json.dumps(result["reasons"], indent=1)
    assert result["backend_initialization"]["healthy"] is True
    assert result["backend_initialization"]["database_created"] is True
    assert result["launch"]["stayed_alive"] is True
    assert result["cleanup"]["workspace_removed"] is True


@pytest.mark.integration
def test_real_installer_smoke_with_built_binaries():
    if not INSTALLER.is_file():
        pytest.skip("no built installer artifact on this machine")
    result = run_smoke(INSTALLER, mode="installer")
    assert result["overall"] in ("PASS", "BLOCKED")
    if result["overall"] == "BLOCKED":
        # Elevation/interactive consent unavailable in automated context is
        # an honest BLOCKED, documented, not a silent skip.
        assert "elevation" in " ".join(result["reasons"]).lower() or \
               "consent" in " ".join(result["reasons"]).lower() or \
               "blocked" in " ".join(result["reasons"]).lower()
    else:
        assert result["backend_initialization"]["healthy"] is True
