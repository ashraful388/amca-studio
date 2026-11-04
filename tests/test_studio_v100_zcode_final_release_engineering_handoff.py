"""I2M post-merge sentinel: ZCode final release-engineering handoff.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2M-I2L-I2F1A-FINAL-RELEASE-ENGINEERING-DELTA-HANDOFF-AND-CANONICAL-INTEGRATION-SENTINEL-01

Verifies, after PS-1 integrates the ZCode I2L -> I2F.1A delta, that the
accepted release-engineering functionality survived integration intact and
that no side effects (version bump, tag, build/sign/promote) occurred.
"""

from pathlib import Path
import hashlib
import json
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# I3B.2 test-structure stabilization: version-aware expectations
# ---------------------------------------------------------------------------
# These helpers let the version-pinning tests validate the CURRENT active
# state. Before the authorized transition they validate 0.9.0; after the
# Manager-authorized transition they validate 1.0.0 with only narrow,
# explicitly enumerated expectation changes (I1.26A contract).
from amca.version_freeze_preflight import active_version_agreement as _ava

CURRENT_ACTIVE_VERSION = _ava()["current_version"]
PRE_TRANSITION_VERSION = "0.9.0"
POST_TRANSITION_VERSION = "1.0.0"
TRANSITION_DONE = CURRENT_ACTIVE_VERSION == POST_TRANSITION_VERSION
SOFTWARE_ROOT = REPO_ROOT / "software"
MANIFEST = (SOFTWARE_ROOT / "docs" / "integration"
            / "AMCA-Studio-v1.0.0-ZCode-Final-Release-Engineering-Delta-Manifest.json")

HANDOFF_READY_MARKER = "ZCODE_RELEASE_ENGINEERING_HANDOFF_READY"

#: Delta files intentionally repaired after the recorded endpoint. The frozen
#: delta manifest still documents the historical endpoint bytes; these files
#: received version-aware test maintenance afterwards (32F recovery), so their
#: hashes no longer match the endpoint record. Enumerated explicitly so no
#: other file may silently drift.
POST_ENDPOINT_TEST_UPDATES = {
    "software/tests/test_studio_v100_final_version_freeze_preflight.py",
    "software/tests/test_studio_v100_version_transition_executor.py",
}


def _load_manifest() -> dict:
    assert MANIFEST.is_file(), "delta manifest missing"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_exactly_one_version_transition_executor_exists():
    executors = list((SOFTWARE_ROOT / "scripts").glob(
        "apply_release_version_transition*.py"))
    module = SOFTWARE_ROOT / "src" / "amca" / "version_transition.py"
    assert len(executors) == 1, f"expected one executor CLI, got {executors}"
    assert module.is_file()
    # No duplicate competing implementation.
    impls = list((SOFTWARE_ROOT / "src" / "amca").glob("*transition*.py"))
    assert len(impls) == 1


def test_dry_run_default_intact(tmp_path):
    """The executor CLI defaults to dry-run and reports it."""
    out = tmp_path / "dry.json"
    proc = subprocess.run(
        [sys_executable(),
         str(SOFTWARE_ROOT / "scripts" / "apply_release_version_transition.py"),
         "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["dry_run"] is True
    # Post-release: the authorized I3B.4 transition is already applied, so the
    # dry-run correctly reports BLOCKED instead of a pending DRY_RUN plan.
    assert data["overall"] == "BLOCKED"
    assert data["start_version"] == "0.9.0"
    assert data["target_version"] == "1.0.0"
    # Post-release the transition is already applied: BLOCKED exits nonzero.
    assert proc.returncode != 0


def test_write_mode_requires_apply_and_authorize_bump(tmp_path):
    out = tmp_path / "auth.json"
    proc = subprocess.run(
        [sys_executable(),
         str(SOFTWARE_ROOT / "scripts" / "apply_release_version_transition.py"),
         "--apply", "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "BLOCKED"
    assert any("authorize-bump" in r for r in data["reasons"])


def test_expected_source_sha_enforcement_intact(tmp_path, monkeypatch):
    import amca.version_transition as vt

    calls = {}
    real_git = vt._git

    def fake_git(*args):
        calls["sha"] = real_git("rev-parse", "HEAD")
        if args and args[0] == "rev-parse":
            return "f" * 40  # force a mismatch vs the expected 'a'*40
        return real_git(*args)

    monkeypatch.setattr(vt, "_git", fake_git)
    monkeypatch.setattr(vt, "SOFTWARE_ROOT", SOFTWARE_ROOT)
    result = vt.apply_transition(
        expected_source_sha="a" * 40, apply=False)
    assert result["overall"] == "BLOCKED"
    assert any("does not match" in r for r in result["reasons"])


def test_rollback_atomicity_contracts_present():
    """The executor source still contains the rollback/atomicity machinery."""
    src = (SOFTWARE_ROOT / "src" / "amca" / "version_transition.py").read_text(
        encoding="utf-8")
    for token in ("snapshots", "rolled_back", "FAILED_ROLLED_BACK",
                  "inject_failure_at"):
        assert token in src, f"atomicity contract missing: {token}"


def test_historical_protections_remain_present():
    src = (SOFTWARE_ROOT / "src" / "amca" / "version_transition.py").read_text(
        encoding="utf-8")
    for token in ("HISTORICAL_PROTECTED_PREFIXES", "dist/archive/",
                  "docs/release/AMCA-Studio-v0.9.0-"):
        assert token in src, f"historical protection missing: {token}"


def _load_ga_check():
    """Load _ga_declaration_check from the I2F preflight guard by path."""
    import importlib.util

    guard = (SOFTWARE_ROOT / "tests"
             / "test_studio_v100_final_version_freeze_preflight.py")
    spec = importlib.util.spec_from_file_location("i2f_guard", guard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._ga_declaration_check


def test_standalone_ga_detection_works():
    check = _load_ga_check()
    assert check("AMCA Studio v1.0.0 GA") is True
    assert check("AMCA Studio v1.0.0 is GA") is True


def test_substring_ga_false_positives_remain_eliminated():
    check = _load_ga_check()
    for line in ("AMCA Studio v1.0.0 AGGREGATE adapter",
                 "AMCA Studio v1.0.0 GATE review",
                 "AMCA Studio v1.0.0 GAMMA test",
                 "AMCA Studio v1.0.0 MEGABYTE payload"):
        assert check(line) is False, line


def test_milestone_ids_do_not_trigger_ga_detection():
    check = _load_ga_check()
    assert check(
        "OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2U-PS2-AGGREGATE-TO-FINAL-"
        "PROMOTION-ADAPTER-01") is False


def test_active_version_is_released_1_0_0():
    """Post-release guard (ZCODE-AMCA-PATH-MIGRATION-AND-V1.0.0-CLEAN-INSTALL-01):
    the authorized I3B.4 transition applied 1.0.0 and the release shipped.
    The LIVE version has since moved on; the historical 1.0.0 release remains
    tagged at its certified SHA (asserted separately)."""
    import re

    from amca.version_freeze_preflight import active_version_agreement

    agreement = active_version_agreement()
    assert agreement["agrees"] is True
    assert re.fullmatch(r"\d+\.\d+\.\d+", agreement["current_version"])
    # The 1.0.0 release milestone is preserved: its tag still exists.
    proc = subprocess.run(
        ["git", "rev-parse", "amca-studio-v1.0.0^{commit}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert proc.stdout.strip() == "3d225e31dfc409521a7dc2259f52489754efbbfc"


def test_release_tag_exists_at_certified_sha():
    """Post-release guard: the certified release tag exists and is immutable
    at the certified source SHA."""
    proc = subprocess.run(
        ["git", "rev-parse", "amca-studio-v1.0.0^{commit}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert proc.stdout.strip() == "3d225e31dfc409521a7dc2259f52489754efbbfc"


def test_no_package_sign_promotion_side_effects():
    """No new build/sign/promote outputs since the accepted endpoint: the
    dist release-record files are unchanged and no promotion marker exists."""
    from amca.distribution_smoke import sha256_file

    manifest = _load_manifest()
    endpoint = manifest["endpoint_sha"]
    for rel in manifest["files"]:
        p = REPO_ROOT / rel["path"]
        if p.is_file() and rel["path"].startswith("software/dist/"):
            assert sha256_file(p) == rel["sha256"], (
                f"dist output changed post-endpoint: {rel['path']}")
    promote_marker = SOFTWARE_ROOT / "dist" / "PROMOTED"
    assert not promote_marker.exists()


def test_delta_files_match_expected_hashes():
    manifest = _load_manifest()
    checked = 0
    for rel in manifest["files"]:
        p = REPO_ROOT / rel["path"]
        assert p.is_file(), f"delta file missing: {rel['path']}"
        if rel["path"] in POST_ENDPOINT_TEST_UPDATES:
            # Intentionally repaired after the recorded endpoint (version-
            # aware test maintenance); existence is verified, and the repair
            # is explicitly enumerated rather than silently accepted.
            checked += 1
            continue
        assert hashlib.sha256(p.read_bytes()).hexdigest() == rel["sha256"], \
            f"hash mismatch: {rel['path']}"
        assert p.stat().st_size == rel["size"], f"size mismatch: {rel['path']}"
        checked += 1
    assert checked == len(manifest["files"]) == 14


def test_no_worker_specific_paths_or_secrets():
    manifest = _load_manifest()
    text = json.dumps(manifest).lower()
    for worker_path in ("c:/users/md", "c:\\users\\md", "appdata",
                        ".venv", "password", "private key"):
        assert worker_path not in text


def test_handoff_ready_state():
    """Overall sentinel verdict."""
    manifest = _load_manifest()
    assert manifest["schema"] == "amca-zcode-release-engineering-delta-manifest/1"
    assert manifest["endpoint_sha"] == "6b49f476"
    assert manifest["milestone_sequence"][-1] == "I2F.1A"
    print(HANDOFF_READY_MARKER)


def sys_executable() -> str:
    import sys

    return sys.executable
