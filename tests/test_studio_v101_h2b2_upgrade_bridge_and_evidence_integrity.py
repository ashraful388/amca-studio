"""H2B.2 upgrade-bridge and final-evidence integrity tests.

Milestone: ZCODE-AMCA-V1.0.1-H2B.2-UPGRADE-BRIDGE-AND-FINAL-EVIDENCE-INTEGRITY-REPAIR-02
Worker: ZCode.

Covers the four ordered repair parts:
A. Legacy v1.0.0 -> v1.0.1 upgrade bridge: fail-closed detection (genuine
   shipped v1.0.0 only), false-positive protection, safe handling of running
   legacy processes, closed app-owned removal set, user-data preservation.
B. The corrected stale packaging identity test (that module runs in the
   battery; the source assertion is additionally pinned here).
C. Canonical v1.0.1 product-identity binding + post-promotion tag completion
   (missing/wrong-SHA/v1.0.0-reuse all BLOCK; tag never required pre-promotion).
D. Test accounting: canonical/scoped suites labelled separately; honest
   UNIQUE_TESTS / REPEATED_TEST_EXECUTIONS / TOTAL_TEST_EXECUTIONS.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
WORKTREE_SRC = str(SOFTWARE_ROOT / "src")
# Always test THIS worktree's implementation (see H2A suite note).
sys.path.insert(0, WORKTREE_SRC)

HELPER = (SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "installer"
          / "amca-legacy-v100-bridge.ps1")
STOP_HELPER = (SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "installer"
               / "amca-stop-target-processes.ps1")
HOOKS = SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "hooks.nsh"
NSI_TEMPLATE = SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "nsis" / "installer.nsi"
REG_TEST_ROOT = "Software\\amca-h2b2-bridge-test"
CSC_CANDIDATES = (
    r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
)
SOURCE_SHA = "c" * 64


def powershell(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=180,
    )


# ---------------------------------------------------------------------------
# Part A - decision core (pure, platform independent)
# ---------------------------------------------------------------------------

def test_genuine_v100_signal_activates_bridge():
    from amca.upgrade_bridge import (
        BridgeDecision, LegacyInstallSignal, decide_legacy_bridge,
        user_data_preservation_violations,
    )

    signal = LegacyInstallSignal(
        display_name="AMCA", display_version="1.0.0",
        install_location="C:\\Users\\u\\AppData\\Local\\AMCA",
        uninstall_string="C:\\Users\\u\\AppData\\Local\\AMCA\\uninstall.exe",
        main_binary_present=True,
        main_binary_product_name="AMCA",
        main_binary_file_version="1.0.0.0",
    )
    decision = decide_legacy_bridge(signal)
    assert decision.activated
    assert decision.detected
    # Closed, app-owned removal set
    assert len(decision.removal_targets) == 4
    assert any(t.startswith("install-location:") for t in decision.removal_targets)
    assert any(t.startswith("registry-key:") and t.endswith("\\AMCA")
               for t in decision.removal_targets)
    assert not user_data_preservation_violations(decision)


def test_absent_install_is_a_noop():
    from amca.upgrade_bridge import LegacyInstallSignal, decide_legacy_bridge

    decision = decide_legacy_bridge(LegacyInstallSignal.absent())
    assert not decision.activated
    assert not decision.detected
    assert decision.removal_targets == ()
    assert "absent" in decision.reason


def test_unrelated_amca_named_entry_does_not_activate():
    """An unrelated application merely named AMCA must never be bridged."""
    from amca.upgrade_bridge import LegacyInstallSignal, decide_legacy_bridge

    wrong_version = LegacyInstallSignal(
        display_name="AMCA", display_version="2.0.0",
        install_location="C:\\SomeApp\\AMCA",
        main_binary_present=True,
        main_binary_product_name="AMCA",
        main_binary_file_version="2.0.0.0",
    )
    decision = decide_legacy_bridge(wrong_version)
    assert not decision.activated
    assert decision.removal_targets == ()

    wrong_name = LegacyInstallSignal(
        display_name="AMCA Something Else", display_version="1.0.0",
        install_location="C:\\SomeApp\\AMCA",
        main_binary_present=True,
        main_binary_product_name="AMCA",
        main_binary_file_version="1.0.0.0",
    )
    assert not decide_legacy_bridge(wrong_name).activated


def test_missing_or_mismatched_binary_identity_does_not_activate():
    from amca.upgrade_bridge import LegacyInstallSignal, decide_legacy_bridge

    missing = LegacyInstallSignal(
        display_name="AMCA", display_version="1.0.0",
        install_location="C:\\AMCA", main_binary_present=False,
    )
    assert not decide_legacy_bridge(missing).activated

    foreign_binary = LegacyInstallSignal(
        display_name="AMCA", display_version="1.0.0",
        install_location="C:\\AMCA", main_binary_present=True,
        main_binary_product_name="NotAMCA",
        main_binary_file_version="1.0.0.0",
    )
    assert not decide_legacy_bridge(foreign_binary).activated

    wrong_exe_version = LegacyInstallSignal(
        display_name="AMCA", display_version="1.0.0",
        install_location="C:\\AMCA", main_binary_present=True,
        main_binary_product_name="AMCA",
        main_binary_file_version="1.2.0.0",
    )
    assert not decide_legacy_bridge(wrong_exe_version).activated


def test_user_data_preservation_contract_is_enforced():
    from amca.upgrade_bridge import (
        BridgeDecision, USER_DATA_IDENTITY, decide_legacy_bridge,
        user_data_preservation_violations, LegacyInstallSignal,
    )

    genuine = decide_legacy_bridge(LegacyInstallSignal(
        display_name="AMCA", display_version="1.0.0",
        install_location="C:\\Users\\u\\AppData\\Local\\AMCA",
        main_binary_present=True, main_binary_product_name="AMCA",
        main_binary_file_version="1.0.0.0",
    ))
    for target in genuine.removal_targets:
        assert USER_DATA_IDENTITY not in target

    # A future edit that lets a user-data path into the removal set must be
    # caught by the contract checker (defence in depth).
    violating = BridgeDecision(
        activated=True, reason="synthetic",
        removal_targets=(f"install-location:C:\\Users\\u\\AppData\\Roaming\\{USER_DATA_IDENTITY}",),
        legacy_install_location="C:\\Users\\u\\AppData\\Local\\AMCA",
        detected=True,
    )
    assert user_data_preservation_violations(violating)

    outside = BridgeDecision(
        activated=True, reason="synthetic",
        removal_targets=("install-location:C:\\Elsewhere\\AMCA",),
        legacy_install_location="C:\\Users\\u\\AppData\\Local\\AMCA",
        detected=True,
    )
    assert user_data_preservation_violations(outside)


# ---------------------------------------------------------------------------
# Part A - real PowerShell helper against real registry + real binaries
# ---------------------------------------------------------------------------

class LegacyBridgeHelperTest(unittest.TestCase):
    """Windows-only: exercises amca-legacy-v100-bridge.ps1 for real.

    Registry fixtures live under HKCU\\Software\\amca-h2b2-bridge-test and are
    removed afterwards; shortcut removal targets and the legacy install
    directory are sandboxed into a temp dir, so the real machine state is
    never touched.
    """

    maxDiff = None

    def setUp(self):
        if sys.platform != "win32":
            self.skipTest("legacy bridge helper is Windows-only")
        self.csc = next((c for c in CSC_CANDIDATES if Path(c).is_file()), None)
        if not self.csc:
            self.skipTest("csc.exe not available; cannot compile identity stubs")
        self.tmp = Path(tempfile.mkdtemp(prefix="amca-h2b2-bridge-"))
        self.legacy_dir = self.tmp / "legacy" / "AMCA"
        self.legacy_dir.mkdir(parents=True)
        self.audit = self.tmp / "audit.jsonl"
        self.key = f"{REG_TEST_ROOT}\\{self._testMethodName}"
        self.stub = self.compile_stub(self.legacy_dir / "amca-desktop.exe",
                                      "AMCA", "1.0.0")

    def tearDown(self):
        powershell(f"if (Test-Path 'HKCU:\\{REG_TEST_ROOT}') "
                   f"{{ Remove-Item -Path 'HKCU:\\{REG_TEST_ROOT}' -Recurse -Force }}")
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile_stub(self, out_path: Path, product_name: str, file_version: str) -> Path:
        src = out_path.with_suffix(".cs")
        src.write_text(
            f'[assembly: System.Reflection.AssemblyProduct("{product_name}")]\n'
            f'[assembly: System.Reflection.AssemblyFileVersion("{file_version}")]\n'
            "class Stub {\n"
            "  static void Main(string[] a) {\n"
            "    if (a.Length > 0) { System.Threading.Thread.Sleep(120000); }\n"
            "  }\n"
            "}\n", encoding="utf-8")
        subprocess.run([self.csc, "/nologo", "/target:exe", f"/out:{out_path}", str(src)],
                       check=True, capture_output=True, timeout=120)
        return out_path

    def write_registry(self, *, display_name="AMCA", display_version="1.0.0",
                       uninstall_string=None):
        loc = str(self.legacy_dir)
        uninstall_string = uninstall_string or str(self.legacy_dir / "uninstall.exe")
        powershell(
            f"New-Item -Path 'HKCU:\\{self.key}' -Force | Out-Null; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'DisplayName' -Value '{display_name}'; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'DisplayVersion' -Value '{display_version}'; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'InstallLocation' -Value '{loc}'; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'UninstallString' -Value '{uninstall_string}'"
        )

    def write_uninstall_stub(self):
        stub = self.legacy_dir / "uninstall.cmd"
        stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        self.write_registry(uninstall_string=str(stub))
        return stub

    def run_bridge(self, *extra):
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(HELPER), "-LegacyKey", self.key,
             "-AuditPath", str(self.audit), *extra],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [l for l in result.stdout.splitlines() if l.startswith("BRIDGE_DECISION:")]
        self.assertTrue(lines, f"helper must emit a decision line: {result.stdout}")
        return json.loads(lines[-1][len("BRIDGE_DECISION:"):])

    def sandbox_shortcuts(self):
        live = self.tmp / "AMCA.lnk"
        sibling = self.tmp / "AMCA Studio.lnk"
        for p in (live, sibling):
            p.write_bytes(b"fake-lnk")
        return live, sibling

    def test_clean_machine_absent_key_execute_is_a_noop(self):
        decision = self.run_bridge("-Execute")
        assert not decision["legacy_detected"]
        assert not decision["activated"]
        assert decision["removal_targets"] == []
        assert decision["user_data_touched"] is False

    def test_genuine_dry_run_activates_without_side_effects(self):
        self.write_uninstall_stub()
        decision = self.run_bridge()
        assert decision["activated"]
        assert decision["legacy_detected"]
        # decision-only: nothing was touched
        assert self.stub.is_file()
        assert self.legacy_dir.is_dir()
        assert powershell(f"Test-Path 'HKCU:\\{self.key}'").stdout.strip() == "True"

    def test_genuine_execute_migrates_legacy_install_with_running_process(self):
        self.write_uninstall_stub()
        live, sibling = self.sandbox_shortcuts()
        proc = subprocess.Popen(
            [str(self.stub), "serve"], creationflags=0x08000000,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)

        decision = self.run_bridge(
            "-Execute", "-StopHelperPath", str(STOP_HELPER),
            "-GraceSeconds", "2", "-ForceWaitSeconds", "2",
            # sandboxed legacy shortcut target only; the sibling is NOT in
            # the removal list, so the helper must never touch it
            "-ShortcutTargets", str(live))

        assert decision["activated"] and decision["executed"]
        assert decision["user_data_touched"] is False
        # legacy process stopped safely by the bridge (not by this test)
        probe = powershell(
            f"if (Get-Process -Id {proc.pid} -ErrorAction SilentlyContinue) "
            "{ exit 0 } else { exit 1 }")
        self.assertEqual(
            probe.returncode, 1,
            f"running legacy process must be stopped; decision={decision}; "
            f"stderr={probe.stderr}")
        # legacy install removed, registry key removed, exact shortcut removed
        self.assertFalse(self.legacy_dir.exists(), "legacy install location must be removed")
        self.assertEqual(
            powershell(f"Test-Path 'HKCU:\\{self.key}'").stdout.strip(), "False")
        self.assertFalse(live.exists(), "legacy AMCA.lnk must be removed")
        self.assertTrue(sibling.exists(), "AMCA Studio.lnk must never be touched")
        audit = self.audit.read_text(encoding="utf-8", errors="replace")
        self.assertIn("legacy_v100_upgrade_bridge", audit)

    def test_quoted_registry_paths_still_corroborate(self):
        """Regression (H2C Stage 6 physical finding): the shipped v1.0.0 NSIS
        wrote InstallLocation/UninstallString with literal wrapping quotes;
        the bridge must normalize them before the existence check."""
        loc = str(self.legacy_dir)
        stub = self.legacy_dir / "uninstall.cmd"
        stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        powershell(
            f"New-Item -Path 'HKCU:\\{self.key}' -Force | Out-Null; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'DisplayName' -Value 'AMCA'; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'DisplayVersion' -Value '1.0.0'; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'InstallLocation' -Value '\"{loc}\"'; "
            f"Set-ItemProperty -Path 'HKCU:\\{self.key}' -Name 'UninstallString' -Value '\"{stub}\"'"
        )
        decision = self.run_bridge()
        assert decision["activated"], decision["reasons"]
        assert decision["legacy_install_location"] == loc

    def test_unrelated_version_does_not_activate_even_on_execute(self):
        self.write_registry(display_version="2.0.0")
        decision = self.run_bridge("-Execute")
        assert not decision["activated"]
        # fail-closed: nothing touched
        assert self.legacy_dir.is_dir()
        assert self.stub.is_file()
        assert powershell(f"Test-Path 'HKCU:\\{self.key}'").stdout.strip() == "True"

    def test_unrelated_amca_named_entry_does_not_activate(self):
        self.write_registry(display_name="AMCA Something Else")
        decision = self.run_bridge("-Execute")
        assert not decision["activated"]
        assert self.legacy_dir.is_dir()

    def test_missing_main_binary_does_not_activate(self):
        self.write_registry()
        self.stub.unlink()
        decision = self.run_bridge("-Execute")
        assert not decision["activated"]
        assert self.legacy_dir.is_dir()

    def test_binary_productname_identity_is_enforced(self):
        self.write_registry()
        foreign_dir = self.tmp / "foreign"
        foreign_dir.mkdir(parents=True, exist_ok=True)
        self.compile_stub(foreign_dir / "amca-desktop.exe", "NotAMCA", "1.0.0")
        self.stub.unlink()
        import shutil
        shutil.copy(foreign_dir / "amca-desktop.exe", self.legacy_dir / "amca-desktop.exe")
        decision = self.run_bridge("-Execute")
        assert not decision["activated"]
        assert self.legacy_dir.is_dir()


# ---------------------------------------------------------------------------
# Part B - corrected stale identity test source
# ---------------------------------------------------------------------------

def test_stale_packaging_test_now_encodes_authorized_identity():
    text = (SOFTWARE_ROOT / "tests" / "test_studio_packaging_and_distribution.py"
            ).read_text(encoding="utf-8")
    assert 'conf.get("productName"), "AMCA Studio"' in text
    assert 'conf.get("productName"), "AMCA")' not in text


def test_active_release_tests_no_longer_encode_stale_productname():
    """The v1.0.0 version-transition executor fixture is the only allowed
    'AMCA' productName: it models the historical 0.9.0 -> 1.0.0 transition
    (when the product really was named AMCA) and must stay untouched. This
    sweep test file is itself excluded (it contains the pattern as a literal)."""
    offenders = []
    skip = {"test_studio_v100_version_transition_executor.py",
            "test_studio_v101_h2b2_upgrade_bridge_and_evidence_integrity.py"}
    for path in (SOFTWARE_ROOT / "tests").glob("test_*.py"):
        if path.name in skip:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if '"productName": "AMCA"' in text or "productName', 'AMCA'" in text:
            offenders.append(path.name)
    assert offenders == []


# ---------------------------------------------------------------------------
# Part C - canonical product identity binding
# ---------------------------------------------------------------------------

def write_manifest(tmp: Path, *, version: str, identity, artifact_count: int):
    """Write a release manifest fixture with physically real artifacts."""
    dist = tmp / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    names = [
        f"amca-studio-v{version}-windows-x64-setup.exe",
        f"amca-studio-v{version}-windows-x64-portable.zip",
        f"AMCA Studio_{version}_x64_en-US.msi",
    ][:artifact_count]
    artifacts = []
    for name in names:
        data = name.encode("utf-8") + b"\x00" * 128
        (dist / name).write_bytes(data)
        artifacts.append({
            "package_name": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest = {
        "product_name": "AMCA Studio",
        "version": version,
        "release_type": "FUNCTIONALLY_VERIFIED_BUILD",
        "artifacts": artifacts,
        "provenance": {"release_gate_state": "ELIGIBLE", "source_git_sha": SOURCE_SHA},
    }
    if identity is not None:
        manifest["product_identity"] = identity
    path = dist / f"release_manifest_v{version}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, dist


def evaluate_with_manifest(tmp: Path, version: str, identity, artifact_count: int):
    from amca.final_promotion import evaluate_evidence

    manifest_path, dist = write_manifest(tmp, version=version, identity=identity,
                                         artifact_count=artifact_count)
    smoke = {"schema": "amca-windows-runtime-smoke-result/1", "artifacts": {}}
    smoke["artifacts"]["installer"] = {"overall": "PASS"}
    smoke["artifacts"]["portable"] = {"overall": "PASS"}
    if artifact_count >= 3:
        smoke["artifacts"]["msi"] = {"overall": "PASS", "source_sha": SOURCE_SHA}
    smoke_path = tmp / "smoke.json"
    smoke_path.write_text(json.dumps(smoke), encoding="utf-8")
    ps1 = tmp / "ps1.json"
    ps1.write_text(json.dumps({"overall_verdict": "PASS",
                               "provenance": {"sha": SOURCE_SHA}}), encoding="utf-8")
    ps2 = tmp / "ps2.json"
    ps2.write_text(json.dumps({"overall_verdict": "PASS",
                               "certified_sha": SOURCE_SHA}), encoding="utf-8")
    return evaluate_evidence(
        ps1_path=ps1, ps2_path=ps2, smoke_path=smoke_path,
        manifest_path=manifest_path, dist_dir=dist,
        source_provenance={"source_git_sha": SOURCE_SHA, "dirty_tree": False},
    )


CANONICAL = {
    "product_name": "AMCA Studio",
    "version": "1.0.1",
    "identifier": "ai.amca.desktop",
    "desktop_binary": "amca-desktop.exe",
    "backend_binary": "amca-backend.exe",
}


def test_v101_missing_identity_binding_blocks():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        bundle = evaluate_with_manifest(tmp, "1.0.1", identity=None, artifact_count=3)
        assert bundle.product_identity.verdict == "BLOCKED"
        assert bundle.promotion_state == "BLOCKED"
        assert any("product_identity" in r for r in bundle.reasons)
        assert bundle.to_document()["product_identity"]["verdict"] == "BLOCKED"


def test_v101_wrong_product_name_is_rejected():
    import tempfile
    identity = dict(CANONICAL, product_name="AMCA")
    with tempfile.TemporaryDirectory() as td:
        bundle = evaluate_with_manifest(Path(td), "1.0.1", identity=identity, artifact_count=3)
        assert bundle.product_identity.verdict == "REJECTED"
        assert "product_name" in bundle.product_identity.detail
        assert bundle.promotion_state == "REJECTED"


def test_v101_wrong_identifier_is_rejected():
    import tempfile
    identity = dict(CANONICAL, identifier="ai.other.desktop")
    with tempfile.TemporaryDirectory() as td:
        bundle = evaluate_with_manifest(Path(td), "1.0.1", identity=identity, artifact_count=3)
        assert bundle.product_identity.verdict == "REJECTED"
        assert "identifier" in bundle.product_identity.detail


def test_v101_wrong_version_and_binaries_are_rejected():
    import tempfile
    for key, value in (("version", "1.0.0"), ("desktop_binary", "app.exe"),
                       ("backend_binary", "backend.exe")):
        identity = dict(CANONICAL, **{key: value})
        with tempfile.TemporaryDirectory() as td:
            bundle = evaluate_with_manifest(Path(td), "1.0.1", identity=identity,
                                            artifact_count=3)
            assert bundle.product_identity.verdict == "REJECTED", key
            assert key in bundle.product_identity.detail


def test_v101_canonical_identity_passes_and_reaches_ready():
    import tempfile

    from amca import __version__ as live_version

    with tempfile.TemporaryDirectory() as td:
        # The version-contract gate ties the manifest version to the live
        # tree, so the canonical identity must carry the live version.
        identity = dict(CANONICAL, version=live_version)
        bundle = evaluate_with_manifest(Path(td), live_version, identity=identity,
                                        artifact_count=3)
        assert bundle.product_identity.verdict == "PASS"
        assert bundle.promotion_state == "READY_FOR_PROMOTION"


def test_build_time_identity_block_matches_canonical():
    from amca.distribution_build import build_product_identity_block
    from amca.final_promotion import CANONICAL_PRODUCT_IDENTITY_V1_0_1

    block = build_product_identity_block(version="1.0.1", software_root=SOFTWARE_ROOT)
    assert block == CANONICAL_PRODUCT_IDENTITY_V1_0_1
    assert block["product_name"] == "AMCA Studio"
    assert block["identifier"] == "ai.amca.desktop"
    assert block["desktop_binary"] == "amca-desktop.exe"
    assert block["backend_binary"] == "amca-backend.exe"


def test_v100_historical_manifest_keeps_contract_and_is_not_blocked_by_identity():
    """v1.0.0 manifests carry no product_identity block and must remain valid
    under their historical contract (explicit NOT_APPLICABLE, non-blocking)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        bundle = evaluate_with_manifest(Path(td), "1.0.0", identity=None,
                                        artifact_count=2)
        assert bundle.product_identity.verdict == "NOT_APPLICABLE"
        assert bundle.msi_smoke.verdict == "NOT_APPLICABLE"
        # the only REJECTED gate is the expected version-contract mismatch
        # (the worktree is now 1.0.1); the identity gate never blocks v1.0.0
        assert bundle.promotion_state == "REJECTED"
        assert all("product_identity" not in r for r in bundle.reasons)


# ---------------------------------------------------------------------------
# Part C - post-promotion tag completion
# ---------------------------------------------------------------------------

def fake_resolver(tags: dict):
    return lambda tag: tags.get(tag)


def test_completion_is_post_promotion_only_and_needs_no_tag_before():
    from amca.final_promotion import verify_release_completion

    def must_not_resolve(tag):
        raise AssertionError("tag must not be resolved before promotion")

    result = verify_release_completion(
        promotion_state="READY_FOR_PROMOTION",
        expected_final_sha=SOURCE_SHA,
        tag_resolver=must_not_resolve,
    )
    assert result["overall"] == "BLOCKED"
    assert "post-promotion" in " ".join(result["reasons"])


def test_promotion_dry_run_has_no_tag_requirement():
    """The pre-promotion workflow must not require a tag (no circular dependency)."""
    import tempfile

    from amca.final_promotion import (
        EvidenceBundle, ST_READY, promote_final_release,
    )
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        artifacts = {}
        for role in ("windows-installer", "windows-portable", "sha256sums",
                     "release-manifest", "release-notes", "known-limitations",
                     "certification-summary"):
            p = tmp / f"{role}.bin"
            p.write_bytes(role.encode("utf-8"))
            artifacts[role] = p
        evidence = EvidenceBundle(promotion_state=ST_READY, final_release_version="1.0.0")
        result = promote_final_release(evidence, artifacts=artifacts, dry_run=True)
        assert result["overall"] == "READY_FOR_PROMOTION"
        assert "release_tag" not in result
        assert not any("tag" in r.lower() for r in result["reasons"])


def test_missing_v101_tag_after_promotion_blocks_completion():
    from amca.final_promotion import verify_release_completion

    result = verify_release_completion(
        promotion_state="PROMOTED", expected_final_sha=SOURCE_SHA,
        tag_resolver=fake_resolver({}),
    )
    assert result["overall"] == "BLOCKED"
    assert result["tag_sha"] is None


def test_wrong_sha_v101_tag_blocks_completion():
    from amca.final_promotion import verify_release_completion

    result = verify_release_completion(
        promotion_state="PROMOTED", expected_final_sha=SOURCE_SHA,
        tag_resolver=fake_resolver({"amca-studio-v1.0.1": "a" * 64}),
    )
    assert result["overall"] == "BLOCKED"
    assert result["tag_sha"] == "a" * 64


def test_correct_tag_on_certified_sha_passes():
    from amca.final_promotion import verify_release_completion

    result = verify_release_completion(
        promotion_state="PROMOTED", expected_final_sha=SOURCE_SHA,
        v100_immutable_sha="3d225e31dfc409521a7dc2259f52489754efbbfc",
        tag_resolver=fake_resolver({
            "amca-studio-v1.0.1": SOURCE_SHA,
            "amca-studio-v1.0.0": "3d225e31dfc409521a7dc2259f52489754efbbfc",
        }),
    )
    assert result["overall"] == "PASS"
    assert result["v100_tag_immutable"] == "PASS"


def test_moved_or_reused_v100_tag_blocks_completion():
    from amca.final_promotion import verify_release_completion

    result = verify_release_completion(
        promotion_state="PROMOTED", expected_final_sha=SOURCE_SHA,
        v100_immutable_sha="3d225e31dfc409521a7dc2259f52489754efbbfc",
        tag_resolver=fake_resolver({
            "amca-studio-v1.0.1": SOURCE_SHA,
            # the v1.0.0 tag was moved/re-pointed at the new release
            "amca-studio-v1.0.0": SOURCE_SHA,
        }),
    )
    assert result["overall"] == "BLOCKED"


def test_v100_tag_immutable_baseline_matches_certified_v100_source():
    """The completion gate's v1.0.0 immutability reference equals the
    certified v1.0.0 source SHA recorded by the shipped I2S evidence."""
    import subprocess as sp
    out = sp.run(
        ["git", "rev-parse", "amca-studio-v1.0.0^{commit}"],
        cwd=str(SOFTWARE_ROOT), capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == "3d225e31dfc409521a7dc2259f52489754efbbfc"


# ---------------------------------------------------------------------------
# Part D - test accounting normalization
# ---------------------------------------------------------------------------

def test_accounting_labels_canonical_and_scoped_frontends_separately():
    from amca.final_promotion import SuiteAccounting, build_test_accounting

    canonical_ids = frozenset({f"fe-{i}" for i in range(670)})
    scoped_ids = frozenset({f"fe-{i}" for i in range(573)})  # subset: overlap
    backend_ids = frozenset({f"be-{i}" for i in range(86)})
    accounting = build_test_accounting([
        SuiteAccounting("canonical_frontend", 670, 670, canonical_ids),
        SuiteAccounting("scoped_frontend", 573, 573, scoped_ids),
        SuiteAccounting("backend_canonical", 86, 86, backend_ids),
    ])
    labels = {s["label"] for s in accounting["suites"]}
    assert {"canonical_frontend", "scoped_frontend"} <= labels
    assert accounting["overlapping_suites_labelled_separately"] is True
    # unique = union of distinct identities, never the sum of suites
    assert accounting["unique_tests"] == 670 + 86
    assert accounting["total_test_executions"] == 670 + 573 + 86
    assert accounting["repeated_test_executions"] == 573
    assert accounting["unique_tests_status"] == "RESOLVED"


def test_accounting_refuses_to_invent_unique_totals_without_identity():
    from amca.final_promotion import SuiteAccounting, build_test_accounting

    accounting = build_test_accounting([
        SuiteAccounting("canonical_frontend", 670, 670),
        SuiteAccounting("scoped_frontend", 573, 573),
    ])
    assert accounting["unique_tests"] is None
    assert accounting["unique_tests_status"] == "REQUIRES_TEST_IDENTITY"
    assert accounting["total_test_executions"] == 1243
    assert accounting["repeated_test_executions"] is None


def test_evidence_document_carries_normalized_accounting():
    from amca.final_promotion import EvidenceBundle, SuiteAccounting, build_test_accounting

    accounting = build_test_accounting([
        SuiteAccounting("scoped_frontend", 573, 573,
                        frozenset({f"fe-{i}" for i in range(573)})),
    ])
    doc = EvidenceBundle(test_accounting=accounting).to_document()
    assert doc["test_accounting"]["unique_tests"] == 573
    assert doc["test_accounting"]["suites"][0]["label"] == "scoped_frontend"


# ---------------------------------------------------------------------------
# NSIS/bridge structural contracts
# ---------------------------------------------------------------------------

def test_hooks_run_bridge_before_install_processes_and_pass_execute():
    text = HOOKS.read_text(encoding="utf-8")
    preinstall = text.split("!macro NSIS_HOOK_PREINSTALL", 1)[1]
    assert "!insertmacro AMCA_LEGACY_V100_BRIDGE" in preinstall
    assert "!insertmacro AMCA_STOP_INSTALLED_PROCESSES" in preinstall
    assert preinstall.index("AMCA_LEGACY_V100_BRIDGE") < preinstall.index(
        "AMCA_STOP_INSTALLED_PROCESSES")
    assert "-Execute" in text
    assert "amca-legacy-v100-bridge.ps1" in text


def test_helper_removal_scope_is_closed_and_user_data_is_never_touched():
    text = HELPER.read_text(encoding="utf-8")
    # user-data identity is a named parameter and guards install-dir removal
    assert "$UserDataIdentity" in text
    assert "-notlike" in text and "*$UserDataIdentity*" in text
    # No executable line may reference "AMCA Studio.lnk" or the user-data
    # identity path: the removal set stays the exact legacy "AMCA.lnk" files.
    executable = [l for l in text.splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    assert not any("AMCA Studio.lnk" in l for l in executable)
    assert any("AMCA.lnk" in l for l in executable)  # legacy shortcut targets exist
    # the template still compiles the hook into the installer
    assert "!ifmacrodef NSIS_HOOK_PREINSTALL" in NSI_TEMPLATE.read_text(encoding="utf-8")


def test_stop_helper_is_unchanged_and_never_deletes():
    text = STOP_HELPER.read_text(encoding="utf-8")
    for forbidden in ("Remove-Item", "Clear-Content", ".Delete("):
        assert forbidden not in text, forbidden


def test_canonical_identity_constants_are_exact():
    from amca.final_promotion import (
        CANONICAL_PRODUCT_IDENTITY_V1_0_1, RELEASE_TAG_V1_0_0, RELEASE_TAG_V1_0_1,
    )

    assert CANONICAL_PRODUCT_IDENTITY_V1_0_1 == {
        "product_name": "AMCA Studio",
        "version": "1.0.1",
        "identifier": "ai.amca.desktop",
        "desktop_binary": "amca-desktop.exe",
        "backend_binary": "amca-backend.exe",
    }
    assert RELEASE_TAG_V1_0_1 == "amca-studio-v1.0.1"
    assert RELEASE_TAG_V1_0_0 == "amca-studio-v1.0.0"
