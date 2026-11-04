"""ZC-AMCA-V0.6.1-P1-INSTALLER-PROCESS-SHUTDOWN-FIX-01 targeted tests.

Process-level tests for the installer shutdown contract. They exercise the
real NSIS helper script (amca-stop-target-processes.ps1) against real stub
processes so the identification rules are proven, not assumed:

1. a backend under the target install directory is identified and stopped
   (including its spawned child tree);
2. an identically named backend in ANOTHER directory (a portable copy) is
   never touched;
3. an orphaned backend (desktop already gone) is still recognized and stopped;
4. with nothing running the helper is a clean no-op;
5. the helper and the NSIS hooks never delete anything (user-data safety).
"""

import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_SOFTWARE = Path(__file__).resolve().parents[1]
HELPER = REPO_SOFTWARE / "desktop-ui" / "src-tauri" / "installer" / "amca-stop-target-processes.ps1"
HOOKS = REPO_SOFTWARE / "desktop-ui" / "src-tauri" / "hooks.nsh"

CREATE_NO_WINDOW = 0x08000000
# Long-lived stand-in payload: ping keeps a child process alive far longer
# than any assertion window, so "still alive" cannot pass by accident.
PING_ARGS = ["/c", "ping", "-n", "30", "127.0.0.1"]


def powershell(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=90,
    )


def pid_alive(pid: int) -> bool:
    probe = powershell(f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}")
    return probe.returncode == 0


def count_amca_processes_under(directory: Path) -> int:
    script = (
        "@(Get-CimInstance Win32_Process -Filter \"Name LIKE 'amca%'\" | "
        f"Where-Object {{ $_.ExecutablePath -like '{directory}\\*' }}).Count"
    )
    probe = powershell(script)
    return int(probe.stdout.strip() or "0")


def child_pids_of(pid: int) -> list:
    script = (
        f"@(Get-CimInstance Win32_Process -Filter \"ParentProcessId = {pid}\")"
        "| ForEach-Object { $_.ProcessId }"
    )
    for _ in range(10):
        probe = powershell(script)
        found = [int(line) for line in probe.stdout.split() if line.strip().isdigit()]
        if found:
            return found
        time.sleep(0.3)
    return []


class InstallerProcessShutdownTest(unittest.TestCase):
    """Windows-only because the installer and the helper are Windows-only."""

    maxDiff = None

    def setUp(self):
        if not sys_platform_windows():
            self.skipTest("installer shutdown contract is Windows-only")
        self.tmp = Path(tempfile.mkdtemp(prefix="amca-p1-shutdown-"))
        self.install_dir = self.tmp / "install"
        self.portable_dir = self.tmp / "portable"
        self.install_dir.mkdir()
        self.portable_dir.mkdir()
        self.audit = self.tmp / "audit.jsonl"
        self.procs = []
        # A real executable copied under an AMCA name inside a fake install
        # directory: image path is the only identity the helper may trust.
        self.backend_stub = self.install_dir / "amca-backend.exe"
        self.desktop_stub = self.install_dir / "amca-desktop.exe"
        shutil.copy(r"C:\Windows\System32\cmd.exe", self.backend_stub)
        shutil.copy(r"C:\Windows\System32\cmd.exe", self.desktop_stub)

    def tearDown(self):
        for proc in self.procs:
            try:
                proc.kill()
            except OSError:
                pass
        for proc in self.procs:
            try:
                proc.wait(timeout=10)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spawn(self, image: Path):
        proc = subprocess.Popen(
            [str(image), *PING_ARGS],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.procs.append(proc)
        # Give the process a moment to register in the process table.
        time.sleep(0.5)
        return proc

    def run_helper(self, install_dir: Path):
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(HELPER),
                "-InstallDir",
                str(install_dir),
                "-GraceSeconds",
                "3",
                "-ForceWaitSeconds",
                "3",
                "-AuditPath",
                str(self.audit),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_target_install_backend_and_child_tree_are_stopped(self):
        backend = self.spawn(self.backend_stub)
        children = child_pids_of(backend.pid)
        self.assertTrue(children, "stub backend must have a live child for the tree test")

        result = self.run_helper(self.install_dir)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(pid_alive(backend.pid), "installed backend must be terminated")
        for child in children:
            self.assertFalse(pid_alive(child), f"backend child {child} must be terminated")
        self.assertEqual(count_amca_processes_under(self.install_dir), 0)

    def test_identically_named_portable_backend_is_excluded(self):
        portable_stub = self.portable_dir / "amca-backend.exe"
        shutil.copy(r"C:\Windows\System32\cmd.exe", portable_stub)
        portable = self.spawn(portable_stub)
        installed = self.spawn(self.backend_stub)

        result = self.run_helper(self.install_dir)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(pid_alive(portable.pid), "portable backend must survive")
        self.assertFalse(pid_alive(installed.pid), "installed backend must still be stopped")

    def test_orphaned_backend_is_recognized_without_desktop(self):
        backend = self.spawn(self.backend_stub)

        result = self.run_helper(self.install_dir)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(pid_alive(backend.pid), "orphaned installed backend must be stopped")
        self.assertIn("forced", result.stdout.lower())

    def test_noop_when_nothing_runs(self):
        result = self.run_helper(self.install_dir)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no installed AMCA processes", result.stdout)
        self.assertEqual(count_amca_processes_under(self.install_dir), 0)

    def test_audit_trail_records_the_shutdown(self):
        self.spawn(self.backend_stub)
        self.run_helper(self.install_dir)
        self.assertTrue(self.audit.is_file(), "helper must append to the lifecycle audit")
        content = self.audit.read_text(encoding="utf-8", errors="replace")
        self.assertIn("installer_process_shutdown", content)

    def test_helper_and_hooks_never_delete_files(self):
        helper_text = HELPER.read_text(encoding="utf-8")
        for forbidden in ("Remove-Item", "Clear-Content", ".Delete(", "rd ", "del "):
            self.assertNotIn(forbidden, helper_text, f"helper must not delete: {forbidden}")
        hooks_text = HOOKS.read_text(encoding="utf-8")
        self.assertNotIn("RMDir", hooks_text, "hooks must not delete directories")
        self.assertNotIn('Delete "', hooks_text, "hooks must not delete files")


def sys_platform_windows() -> bool:
    import sys

    return sys.platform == "win32"


if __name__ == "__main__":
    unittest.main()
