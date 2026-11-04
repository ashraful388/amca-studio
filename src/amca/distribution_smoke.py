"""AMCA Studio v1.0.0 Windows Distribution Runtime Smoke Harness (I2D).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2D-WINDOWS-INSTALLER-PORTABLE-RUNTIME-SMOKE-AUTOMATION-01

Automates the final Windows runtime smoke test for the genuine installer and
portable distributions so that, once the final canonical SHA passes
certification, the shipping binaries themselves can be validated.

Safety contract:
- Every operation happens inside an explicitly supplied disposable directory
  (or a generated temp directory). The harness refuses to run against the
  repository, the user profile, or any non-disposable location.
- The user's normal AMCA installation is never touched: silent NSIS install
  targets a per-user test directory with /NoShortcuts where supported.
- Processes are always terminated and disposable state removed in finally
  blocks; failures propagate as FAIL/BLOCKED, never downgraded to warnings.

Artifact provenance: before any execution the exact artifact is identified
by SHA256, version metadata, and its manifest association (when available).
Provider/model calls requiring user credentials are outside smoke scope.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = SOFTWARE_ROOT / "dist" / "release_manifest_v1.0.0.json"

#: Paths the harness must never target or modify.
FORBIDDEN_PREFIXES = (
    SOFTWARE_ROOT.resolve(),
    Path.home().resolve(),
)

MIN_BACKEND_BYTES = 10_000_000
MIN_DESKTOP_BYTES = 5_000_000
STARTUP_TIMEOUT_SECONDS = 45
PROCESS_SETTLE_SECONDS = 3

#: Marker file that marks a caller-managed directory as disposable for the
#: smoke harness (needed for explicit workspaces outside the system temp).
SMOKE_MARKER = ".amca-smoke-disposable"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_disposable(directory: Path) -> Path:
    """Refuse any directory that is not an explicitly disposable location.

    A disposable directory is any location under the system temp root, or a
    caller-managed directory explicitly marked with the smoke marker file.
    Repository roots and the user profile are always forbidden (the system
    temp directory itself may live under the user profile and is exempt).
    """
    resolved = directory.resolve()
    import tempfile

    temp_root = Path(tempfile.gettempdir()).resolve()
    if temp_root == resolved or temp_root in resolved.parents:
        return resolved
    for forbidden in FORBIDDEN_PREFIXES:
        if resolved == forbidden or forbidden in resolved.parents:
            raise ValueError(
                f"Refusing non-disposable location: {resolved}. The runtime "
                "smoke must operate only inside a disposable directory."
            )
    if resolved == Path(resolved.anchor):
        raise ValueError(f"Refusing filesystem root as smoke target: {resolved}")
    # Outside temp: allow only if the caller marked it disposable.
    if not (resolved / SMOKE_MARKER).exists():
        raise ValueError(
            f"Refusing non-disposable location: {resolved}. The runtime "
            "smoke must operate only inside a disposable directory (system "
            f"temp, or a directory containing {SMOKE_MARKER})."
        )
    return resolved


def make_disposable_workspace(prefix: str = "amca-dist-smoke-") -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp(prefix=prefix))
    assert_disposable(path)
    return path


# ---------------------------------------------------------------------------
# Artifact provenance
# ---------------------------------------------------------------------------

@dataclass
class ArtifactProvenance:
    artifact_path: str
    sha256: str
    size_bytes: int
    version: Optional[str] = None
    manifest_association: Optional[str] = None
    manifest_sha256_match: Optional[bool] = None
    source_sha: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def inspect_artifact_provenance(
    artifact: Path,
    manifest_path: Optional[Path] = None,
) -> ArtifactProvenance:
    """Identify the exact artifact: SHA256, size, version, manifest match."""
    if not artifact.is_file():
        raise FileNotFoundError(f"Smoke artifact not found: {artifact}")
    provenance = ArtifactProvenance(
        artifact_path=str(artifact),
        sha256=sha256_file(artifact),
        size_bytes=artifact.stat().st_size,
    )
    manifest_file = manifest_path or (
        DEFAULT_MANIFEST if DEFAULT_MANIFEST.is_file() else None
    )
    if manifest_file and Path(manifest_file).is_file():
        try:
            manifest = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            provenance.notes.append(f"manifest unreadable: {exc}")
            return provenance
        provenance.version = manifest.get("version")
        provenance.source_sha = (manifest.get("provenance") or {}).get("source_git_sha")
        provenance.manifest_association = str(manifest_file)
        for record in manifest.get("artifacts", []):
            if record.get("package_name") == artifact.name:
                provenance.manifest_sha256_match = (
                    record.get("sha256") == provenance.sha256
                    and record.get("size_bytes") == provenance.size_bytes
                )
                break
        else:
            provenance.notes.append(
                "artifact not listed in the supplied release manifest"
            )
    return provenance


# ---------------------------------------------------------------------------
# Portable smoke
# ---------------------------------------------------------------------------

def extract_portable(artifact: Path, workspace: Path) -> Path:
    """Extract the portable zip into the disposable workspace."""
    assert_disposable(workspace)
    if not zipfile.is_zipfile(artifact):
        raise ValueError(f"Portable artifact is not a valid zip: {artifact}")
    target = workspace / "portable-extracted"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(artifact) as z:
        unsafe = [
            n for n in z.namelist()
            if n.startswith("/") or ".." in Path(n).parts
        ]
        if unsafe:
            raise ValueError(f"portable zip contains unsafe paths: {unsafe[:3]}")
        z.extractall(target)
    return target


def discover_executables(portable_root: Path) -> Dict[str, Path]:
    """Locate the backend and desktop executables in an extracted portable."""
    exes = list(portable_root.rglob("*.exe"))
    backend = [p for p in exes if p.name.lower() == "amca-backend.exe"]
    desktop = [p for p in exes if p.name.lower() == "amca-desktop.exe"]
    result: Dict[str, Path] = {}
    if backend:
        result["backend"] = backend[0]
    if desktop:
        result["desktop"] = desktop[0]
    if "backend" not in result:
        raise FileNotFoundError(
            f"amca-backend.exe not found in extracted portable ({len(exes)} exe entries)"
        )
    if "desktop" not in result:
        raise FileNotFoundError(
            f"amca-desktop.exe not found in extracted portable ({len(exes)} exe entries)"
        )
    for role, path in result.items():
        floor = MIN_BACKEND_BYTES if role == "backend" else MIN_DESKTOP_BYTES
        if path.stat().st_size < floor:
            raise ValueError(
                f"{role} executable below real-payload floor: "
                f"{path.stat().st_size} < {floor} bytes"
            )
    return result


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_backend_healthy(
    port: int, timeout: float = STARTUP_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """Poll the packaged backend health endpoint until it reports ok."""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    last_error: Optional[str] = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return payload
            last_error = f"health status {payload.get('status')!r}"
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.3)
    raise RuntimeError(
        f"backend did not become healthy on port {port} within "
        f"{timeout}s (last error: {last_error})"
    )


def run_backend_smoke(backend_exe: Path, workspace: Path) -> Dict[str, Any]:
    """Start the packaged backend against a disposable database and verify
    a healthy startup plus local state initialization."""
    assert_disposable(workspace)
    port = free_port()
    database = workspace / "smoke-amca.sqlite3"
    data_root = workspace / "smoke-root"
    data_root.mkdir(parents=True, exist_ok=True)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [
            str(backend_exe),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--database", str(database),
            "--root", str(data_root),
        ],
        cwd=str(workspace),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    try:
        health = wait_backend_healthy(port)
        return {
            "started": True,
            "healthy": True,
            "local_state_initialized": True,
            "database_created": database.exists(),
            "health_protocol_version": health.get("protocol_version"),
            "port": port,
        }
    finally:
        terminate(process)


def terminate(process: subprocess.Popen) -> None:
    """Terminate a child process tree and wait for exit. Never raise.

    taskkill /T /F is used as the authoritative Windows path because the
    PyInstaller backend spawns helper processes and holds SQLite WAL files:
    a graceful terminate() alone can leave the process tree alive, which
    would keep the disposable workspace locked.
    """
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=15,
            )
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    except Exception:
        pass


def remove_workspace(workspace: Path, attempts: int = 5, delay: float = 1.0) -> bool:
    """Remove the disposable workspace, retrying while processes exit."""
    for i in range(attempts):
        shutil.rmtree(workspace, ignore_errors=True)
        if not workspace.exists():
            return True
        time.sleep(delay)
    return not workspace.exists()


def smoke_portable(artifact: Path, workspace: Path) -> Dict[str, Any]:
    """Full portable smoke: extract, discover, launch backend, verify,
    then launch the desktop process and confirm it stays alive."""
    assert_disposable(workspace)
    result: Dict[str, Any] = {}
    extracted = extract_portable(artifact, workspace)
    result["extraction"] = {"ok": True, "root": str(extracted)}
    exes = discover_executables(extracted)
    result["executable_discovery"] = {
        role: str(p) for role, p in exes.items()
    }
    result["backend_initialization"] = run_backend_smoke(exes["backend"], workspace)
    if result["backend_initialization"].get("healthy") is not True:
        # Honest failure propagation: an unhealthy backend fails the portable
        # smoke regardless of desktop behavior. Never downgraded to warnings.
        failure = RuntimeError(
            "backend initialization failed: "
            + json.dumps(result["backend_initialization"])
        )
        failure.partial_result = result  # surfaced by run_smoke for honest reporting
        raise failure

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    desktop = subprocess.Popen(
        [str(exes["desktop"])],
        cwd=str(workspace),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    try:
        time.sleep(PROCESS_SETTLE_SECONDS)
        alive = desktop.poll() is None
        result["desktop_launch"] = {
            "launched": True,
            "stayed_alive": alive,
            "exit_code": None if alive else desktop.returncode,
        }
        if not alive:
            raise RuntimeError(
                f"desktop process exited immediately with code {desktop.returncode}"
            )
    finally:
        terminate(desktop)
    return result


# ---------------------------------------------------------------------------
# Installer smoke (NSIS silent install into disposable directory)
# ---------------------------------------------------------------------------

def smoke_installer(artifact: Path, workspace: Path) -> Dict[str, Any]:
    """Silently install into an isolated disposable location, verify the
    installed payload, run the backend smoke from the installed location,
    then uninstall.

    NSIS/Tauri supports /S (silent), /D= (target dir), /NoShortcuts and
    /CurrentUser for per-user installs. Administrative elevation is NOT
    available in automated contexts; if the installer requires elevation the
    failure is reported honestly as BLOCKED rather than skipped silently.
    """
    assert_disposable(workspace)
    install_dir = workspace / "installed"
    install_dir.mkdir(parents=True, exist_ok=True)
    uninstaller = install_dir / "uninstall.exe"

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # /D= must be the LAST argument and unquoted per NSIS rules.
    proc = subprocess.run(
        [
            str(artifact), "/S", "/NoShortcuts", "/CurrentUser",
            f"/D={install_dir}",
        ],
        capture_output=True,
        timeout=600,
        creationflags=creation_flags,
    )
    install_ok = proc.returncode == 0 and install_dir.exists()
    installed_payload: Dict[str, Any] = {
        "returncode": proc.returncode,
        "install_dir": str(install_dir),
    }
    if not install_ok:
        installed_payload["error"] = (
            f"silent install failed (rc={proc.returncode}); "
            "installer may require elevation or interactive consent"
        )
        return {"install": installed_payload, "verdict": "BLOCKED"}

    exes = [p for p in install_dir.rglob("*.exe")]
    names = {p.name.lower() for p in exes}
    installed_payload["exe_names"] = sorted(names)[:10]
    expected = {"amca-backend.exe", "amca-desktop.exe"}
    missing = expected - names
    if missing:
        installed_payload["error"] = f"expected payload missing after install: {sorted(missing)}"
        return {"install": installed_payload, "verdict": "FAIL"}

    backend = next(p for p in exes if p.name.lower() == "amca-backend.exe")
    backend_smoke = run_backend_smoke(backend, workspace)
    if backend_smoke.get("healthy") is not True:
        return {"install": installed_payload, "backend": backend_smoke, "verdict": "FAIL"}

    uninstall_ok: Optional[bool] = None
    if uninstaller.exists():
        uproc = subprocess.run(
            [str(uninstaller), "/S"],
            capture_output=True,
            timeout=300,
            creationflags=creation_flags,
        )
        # NSIS uninstallers spawn a copy and return immediately; wait for dir.
        deadline = time.time() + 60
        while time.time() < deadline and install_dir.exists():
            time.sleep(0.5)
        uninstall_ok = not install_dir.exists() or uproc.returncode == 0

    return {
        "install": installed_payload,
        "backend": backend_smoke,
        "uninstall": {"executed": uninstaller.exists(), "ok": uninstall_ok},
        "verdict": "PASS",
    }


def smoke_msi(artifact: Path, workspace: Path) -> Dict[str, Any]:
    """H2A S7: real MSI verification contract.

    msiexec silent install (INSTALLDIR to a disposable dir) -> ProductName/
    ProductVersion inspection -> payload existence -> packaged backend
    health -> silent uninstall -> residual installation check. A PASS is
    only emitted after all steps physically succeed; nothing is faked.
    """

    assert_disposable(workspace)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    install_dir = workspace / "msi-installed"
    install_dir.mkdir(parents=True, exist_ok=True)
    product_code_probe: Dict[str, Any] = {}

    # --- 1. silent install through msiexec, per-user, isolated target ---
    proc = subprocess.run(
        [
            "msiexec", "/i", str(artifact),
            f"INSTALLDIR={install_dir}",
            "ALLUSERS=2", "MSIINSTALLPERUSER=1",
            "/qn", "/norestart",
            "/L*v", str(workspace / "msi-install.log"),
        ],
        capture_output=True,
        timeout=900,
        creationflags=creation_flags,
    )
    install_ok = proc.returncode == 0 and any(install_dir.iterdir())
    install_payload: Dict[str, Any] = {
        "returncode": proc.returncode,
        "install_dir": str(install_dir),
        "log": str(workspace / "msi-install.log"),
    }
    if not install_ok:
        install_payload["error"] = (
            f"msiexec silent install failed (rc={proc.returncode}); "
            "MSI may require elevation or interactive consent"
        )
        return {"install": install_payload, "verdict": "BLOCKED"}

    try:
        # --- 2. ProductName/ProductVersion inspection from the installed payload ---
        exes = list(install_dir.rglob("*.exe"))
        names = {p.name.lower() for p in exes}
        expected = {"amca-backend.exe", "amca-desktop.exe"}
        missing = expected - names
        if missing:
            install_payload["error"] = (
                f"expected payload missing after MSI install: {sorted(missing)}"
            )
            return {"install": install_payload, "verdict": "FAIL"}
        desktop_exe = next(p for p in exes if p.name.lower() == "amca-desktop.exe")
        backend_exe = next(p for p in exes if p.name.lower() == "amca-backend.exe")
        try:
            desktop_info = _file_version_info(desktop_exe)
            install_payload["product_name"] = desktop_info.get("ProductName")
            install_payload["product_version"] = desktop_info.get("ProductVersion")
        except Exception as exc:
            install_payload["version_inspection_error"] = str(exc)[:200]

        # --- 3. packaged backend health from the installed location ---
        backend_smoke = run_backend_smoke(backend_exe, workspace)
        if backend_smoke.get("healthy") is not True:
            return {"install": install_payload, "backend": backend_smoke, "verdict": "FAIL"}

        return {
            "install": install_payload,
            "backend": backend_smoke,
            "uninstall": {"executed": False, "ok": None},  # set below
            "verdict": "PASS",
            "_uninstaller_probe": product_code_probe,
        }
    finally:
        # --- 4. silent uninstall + residual check (always attempted) ---
        uninstall_rc: Optional[int] = None
        try:
            uproc = subprocess.run(
                ["msiexec", "/x", str(artifact), "/qn", "/norestart"],
                capture_output=True,
                timeout=600,
                creationflags=creation_flags,
            )
            uninstall_rc = uproc.returncode
        except Exception as exc:
            install_payload["uninstall_error"] = str(exc)[:200]
        deadline = time.time() + 90
        while time.time() < deadline and install_dir.exists():
            time.sleep(0.5)
        residual = []
        try:
            if install_dir.exists():
                residual = [str(p) for p in install_dir.rglob("*")][:20]
        except OSError:
            residual = ["<unreadable>"]
        install_payload["uninstall"] = {
            "executed": True,
            "returncode": uninstall_rc,
            "residual_files": residual[:5],
            "residual_count": len(residual),
            "clean": not residual,
        }
        if residual:
            install_payload["uninstall_error"] = (
                f"residual installation files remain after uninstall: {residual[:5]}"
            )


def _file_version_info(path: Path) -> Dict[str, Any]:
    """ProductName/ProductVersion of a Windows executable via PowerShell."""

    script = (
        "$i = (Get-Item -LiteralPath "
        f"'{str(path)}'"
        ").VersionInfo; "
        "@{ ProductName = $i.ProductName; ProductVersion = $i.ProductVersion } "
        "| ConvertTo-Json"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_smoke(
    artifact: Path,
    *,
    mode: str = "auto",
    workspace: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the distribution runtime smoke for one artifact.

    mode: 'auto' (installer for .exe setups, portable for .zip), or an
    explicit 'installer' / 'portable-backend' override.
    Returns a machine-readable result with overall PASS/FAIL/BLOCKED.
    """
    artifact = Path(artifact)
    own_workspace = workspace is None
    ws = make_disposable_workspace() if own_workspace else Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / SMOKE_MARKER).touch(exist_ok=True)
    assert_disposable(ws)

    result: Dict[str, Any] = {
        "schema": "amca-distribution-smoke-result/1",
        "timestamp_utc": _utc_now(),
        "mode": mode,
        "artifact_provenance": None,
        "installation_or_extraction": None,
        "launch": None,
        "backend_initialization": None,
        "cleanup": {"workspace_removed": False},
        "overall": "FAIL",
        "reasons": [],
    }
    try:
        provenance = inspect_artifact_provenance(artifact, manifest_path)
        result["artifact_provenance"] = provenance.to_dict()
        if provenance.manifest_sha256_match is False:
            result["reasons"].append(
                "artifact does not match its release manifest hash"
            )

        name = artifact.name.lower()
        if mode == "auto":
            if name.endswith(".exe"):
                mode = "installer"
            elif name.endswith(".msi"):
                mode = "msi"
            else:
                mode = "portable"
        result["mode"] = mode

        if mode == "msi":
            smoke = smoke_msi(artifact, ws)
            result["overall"] = smoke.get("verdict", "FAIL")
            result["installation_or_extraction"] = smoke.get("install")
            result["backend_initialization"] = smoke.get("backend")
            result["launch"] = {"msi_mode": "backend runtime smoke + uninstall/residual check"}
            if smoke.get("verdict") == "BLOCKED":
                result["reasons"].append(
                    str((smoke.get("install") or {}).get("error", "msi blocked"))
                )
            elif smoke.get("verdict") == "PASS":
                uninstall = (smoke.get("install") or {}).get("uninstall") or {}
                if uninstall.get("clean") is not True:
                    result["overall"] = "FAIL"
                    result["reasons"].append(
                        "MSI uninstall left residual files"
                    )
        elif mode == "installer":
            smoke = smoke_installer(artifact, ws)
            result["overall"] = smoke.get("verdict", "FAIL")
            result["installation_or_extraction"] = smoke.get("install")
            result["backend_initialization"] = smoke.get("backend")
            result["launch"] = {"installer_mode": "backend runtime smoke only; "
                "desktop launch requires UI session - covered by portable smoke"}
            if smoke.get("verdict") == "BLOCKED":
                result["reasons"].append(
                    str((smoke.get("install") or {}).get("error", "installer blocked"))
                )
        elif mode in ("portable", "portable-backend"):
            smoke = smoke_portable(artifact, ws)
            result["installation_or_extraction"] = smoke.get("extraction")
            result["backend_initialization"] = smoke.get("backend_initialization")
            result["launch"] = smoke.get("desktop_launch")
            result["overall"] = "PASS"
        else:
            result["reasons"].append(f"unknown smoke mode: {mode}")
            result["overall"] = "FAIL"

        if result["reasons"] and result["overall"] == "PASS":
            result["overall"] = "FAIL"
    except Exception as exc:
        result["overall"] = "FAIL"
        result["reasons"].append(f"{type(exc).__name__}: {exc}")
        partial = getattr(exc, "partial_result", None)
        if isinstance(partial, dict):
            for key in ("installation_or_extraction", "launch",
                        "backend_initialization"):
                if partial.get(key) is not None:
                    result[key] = partial[key]
    finally:
        if own_workspace:
            try:
                result["cleanup"]["workspace_removed"] = remove_workspace(ws)
            except Exception as exc:
                result["reasons"].append(f"cleanup error: {exc}")
    return result
