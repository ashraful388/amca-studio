"""I2L tests: deterministic version transition executor and atomicity dry-run.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2L-DETERMINISTIC-V1.0.0-VERSION-TRANSITION-EXECUTOR-AND-ATOMICITY-DRY-RUN-01

All transitions run against synthetic trees in tmp_path. The live canonical
tree is never modified (also verified by a snapshot test).
"""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest

from amca.version_transition import (
    HISTORICAL_PROTECTED_PREFIXES,
    apply_transition,
    build_plan,
)

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
RUNNER = SOFTWARE_ROOT / "scripts" / "apply_release_version_transition.py"


def _make_tree(tmp_path: Path, version: str = "0.9.0") -> Path:
    tree = tmp_path / "software"
    for rel in ("src/amca", "desktop-ui/src-tauri", "scripts",
                "docs/release", "dist/archive"):
        (tree / rel).mkdir(parents=True, exist_ok=True)
    (tree / "pyproject.toml").write_text(
        f'[project]\nname = "amca"\nversion = "{version}"\n', encoding="utf-8")
    (tree / "src" / "amca" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    (tree / "desktop-ui" / "package.json").write_text(
        json.dumps({"name": "amca-desktop-ui", "version": version}, indent=2),
        encoding="utf-8")
    (tree / "desktop-ui" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"version": version, "productName": "AMCA"}, indent=2),
        encoding="utf-8")
    (tree / "desktop-ui" / "src-tauri" / "Cargo.toml").write_text(
        f'[package]\nname = "amca-desktop"\nversion = "{version}"\n',
        encoding="utf-8")
    (tree / "scripts" / "promote_final_release.py").write_text(
        f'# inventory\nINSTALLER = "amca-studio-v{version}-windows-x64-setup.exe"\n'
        f'PORTABLE = "amca-studio-v{version}-windows-x64-portable.zip"\n'
        f'MANIFEST = "release_manifest_v{version}.json"\n', encoding="utf-8")
    (tree / "src" / "amca" / "distribution_build.py").write_text(
        f'def pipe(version: str = "{version}"): ...\n', encoding="utf-8")
    (tree / "src" / "amca" / "final_release.py").write_text(
        f'INSTALLER = "amca-studio-v{version}-windows-x64-setup.exe"\n'
        f'if (version == "{version}"):\n    pass\n', encoding="utf-8")
    (tree / "src" / "amca" / "release_packaging.py").write_text(
        f'CFG = {{"version": "{version}"}}\n'
        f"KEY = ('version', '{version}')\n", encoding="utf-8")
    (tree / "src" / "amca" / "distribution_smoke.py").write_text(
        f'MANIFEST = "release_manifest_v{version}.json"\n', encoding="utf-8")
    (tree / "src" / "amca" / "final_promotion.py").write_text(
        f'MANIFEST = "release_manifest_v{version}.json"\n', encoding="utf-8")
    # Historical records that must remain byte-identical.
    (tree / "docs" / "release" / "AMCA-Studio-v0.9.0-Release-Notes.md").write_text(
        "# v0.9.0 historical release notes\n", encoding="utf-8")
    (tree / "docs" / "release" / "AMCA-Studio-v0.7.0-GA-Report.md").write_text(
        "# v0.7.0 historical GA\n", encoding="utf-8")
    (tree / "dist" / "archive" / "old-package.zip").write_bytes(b"PK\x03\x04")
    return tree


def _tree_hashes(tree: Path) -> dict:
    return {
        p.relative_to(tree).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(tree.rglob("*")) if p.is_file()
    }


def _read(tree: Path, rel: str) -> str:
    return (tree / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dry-run / full transition / atomicity
# ---------------------------------------------------------------------------

def test_dry_run_changes_nothing(tmp_path):
    tree = _make_tree(tmp_path)
    before = _tree_hashes(tree)
    result = apply_transition(tree, apply=False)
    assert result["overall"] == "DRY_RUN"
    assert result["plan"] is not None
    assert _tree_hashes(tree) == before, "dry-run must not modify files"


def test_full_transition_reaches_target_version(tmp_path):
    tree = _make_tree(tmp_path)
    result = apply_transition(tree, apply=True)
    assert result["overall"] == "APPLIED"
    assert '"1.0.0"' in _read(tree, "pyproject.toml")
    assert '__version__ = "1.0.0"' in _read(tree, "src/amca/__init__.py")
    pkg = json.loads(_read(tree, "desktop-ui/package.json"))
    assert pkg["version"] == "1.0.0"
    tauri = json.loads(_read(tree, "desktop-ui/src-tauri/tauri.conf.json"))
    assert tauri["version"] == "1.0.0"
    assert 'version = "1.0.0"' in _read(tree, "desktop-ui/src-tauri/Cargo.toml")
    # Code references moved too.
    assert "amca-studio-v1.0.0-windows-x64-setup.exe" in _read(
        tree, "scripts/promote_final_release.py")
    assert "release_manifest_v1.0.0.json" in _read(
        tree, "src/amca/final_promotion.py")


def test_partial_transition_impossible_and_rollback_works(tmp_path):
    """An injected failure mid-run rolls back every earlier write."""
    tree = _make_tree(tmp_path)
    before = _tree_hashes(tree)
    # Inject failure at the last planned file (found from the plan).
    plan = build_plan(tree)
    last_file = plan.files[-1]["file"]
    result = apply_transition(tree, apply=True, inject_failure_at=last_file)
    assert result["rolled_back"] is True
    assert result["applied"] is False
    assert result["overall"] == "FAILED_ROLLED_BACK"
    after = _tree_hashes(tree)
    # Files edited BEFORE the failure must have been restored.
    for entry in plan.files[:-1]:
        assert after[entry["file"]] == before[entry["file"]], \
            f"{entry['file']} was left half-modified"
    assert after == before, "rollback must restore the exact pre-run state"


def test_rollback_on_unexpected_error(tmp_path):
    tree = _make_tree(tmp_path)
    before = _tree_hashes(tree)
    result = apply_transition(tree, apply=True, inject_failure_at="pyproject.toml")
    assert result["rolled_back"] is True
    assert _tree_hashes(tree) == before


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def test_wrong_starting_version_rejected(tmp_path):
    tree = _make_tree(tmp_path, version="0.6.2")
    before = _tree_hashes(tree)
    result = apply_transition(tree, apply=False)
    assert result["overall"] == "BLOCKED"
    assert any("0.9.0" in r for r in result["reasons"])
    assert _tree_hashes(tree) == before


def test_wrong_expected_sha_rejected(tmp_path, monkeypatch):
    tree = _make_tree(tmp_path)
    monkeypatch.setattr(
        "amca.version_transition._git", lambda *a: "b" * 40)
    result = apply_transition(
        expected_source_sha="a" * 40, apply=False)
    assert result["overall"] == "BLOCKED"
    assert any("does not match" in r for r in result["reasons"])


# ---------------------------------------------------------------------------
# Historical protection
# ---------------------------------------------------------------------------

def test_historical_records_remain_byte_identical(tmp_path):
    tree = _make_tree(tmp_path)
    before = _tree_hashes(tree)
    apply_transition(tree, apply=True)
    after = _tree_hashes(tree)
    for rel, digest in before.items():
        if rel.startswith(("docs/release/AMCA-Studio-v0.9.0",
                           "docs/release/AMCA-Studio-v0.7.0",
                           "dist/archive/")):
            assert after[rel] == digest, f"historical file changed: {rel}"


def test_executor_refuses_unduplicated_historical_files(tmp_path):
    """The plan touches ONLY inventoried files; no docs/archive path appears."""
    tree = _make_tree(tmp_path)
    plan = build_plan(tree)
    for entry in plan.files:
        rel = entry["file"]
        for prefix in HISTORICAL_PROTECTED_PREFIXES:
            assert not rel.startswith(prefix), \
                f"plan touches historical path {rel}"


def test_stale_active_reference_detected_after_partial_transition(tmp_path):
    """If a boundary lags at 0.9.0 post-transition, the I2F validator flags
    it (the executor's post-validation exposes the disagreement)."""
    tree = _make_tree(tmp_path)
    apply_transition(tree, apply=True)
    # Simulate a stale leftover by reverting one file.
    (tree / "desktop-ui" / "package.json").write_text(
        json.dumps({"name": "amca-desktop-ui", "version": "0.9.0"}),
        encoding="utf-8")
    from amca.version_transition import _read_matrix_from
    from amca.version_freeze_preflight import active_version_agreement
    agreement = active_version_agreement(_read_matrix_from(tree, "1.0.0"))
    assert agreement["agrees"] is False


# ---------------------------------------------------------------------------
# Agreement + artifact naming simulation
# ---------------------------------------------------------------------------

def test_python_npm_tauri_cargo_agree_after_transition(tmp_path):
    tree = _make_tree(tmp_path)
    apply_transition(tree, apply=True)
    from amca.version_transition import _read_matrix_from
    matrix = _read_matrix_from(tree, "1.0.0")
    versions = {v["version"] for v in matrix.values()}
    assert versions == {"1.0.0"}


def test_final_artifact_naming_simulation():
    result = apply_transition(_make_tree(Path(tempfile_dir())), apply=False)
    sim = result["artifact_name_simulation"]
    assert sim["installer"] == "amca-studio-v1.0.0-windows-x64-setup.exe"
    assert sim["portable"] == "amca-studio-v1.0.0-windows-x64-portable.zip"
    assert sim["manifest"] == "release_manifest_v1.0.0.json"


def tempfile_dir() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="amca-i2l-sim-")


# ---------------------------------------------------------------------------
# Idempotency + guardrails
# ---------------------------------------------------------------------------

def test_transition_rerun_is_explicitly_rejected(tmp_path):
    tree = _make_tree(tmp_path)
    apply_transition(tree, apply=True)
    # A rerun fails the starting-version precondition (tree is now 1.0.0).
    result = apply_transition(tree, apply=False)
    assert result["overall"] == "BLOCKED"
    assert any("0.9.0" in r for r in result["reasons"])


def test_no_tag_created_and_no_promotion(tmp_path):
    tree = _make_tree(tmp_path)
    tags_before = subprocess.run(
        ["git", "tag", "--list"], cwd=str(REPO_ROOT),
        capture_output=True, text=True).stdout
    apply_transition(tree, apply=True)
    tags_after = subprocess.run(
        ["git", "tag", "--list"], cwd=str(REPO_ROOT),
        capture_output=True, text=True).stdout
    # The executor must never create, move, or delete a release tag; a tag
    # may only be created by the explicit post-certification tagging step.
    assert tags_after == tags_before, "version transition must not touch git tags"
    result = apply_transition(tree, apply=False)
    text = json.dumps(result).lower()
    assert "promote" not in text or result.get("overall") != "PROMOTED"


def test_no_secrets_emitted(tmp_path):
    result = apply_transition(_make_tree(tmp_path), apply=False)
    text = json.dumps(result).lower()
    for forbidden in ("password", "private key", "pfx", "api_key"):
        assert forbidden not in text
    assert result["secrets_emitted"] is False


# ---------------------------------------------------------------------------
# Live tree guard + runner contract
# ---------------------------------------------------------------------------

def test_live_canonical_tree_unchanged_and_still_0_9_0():
    """I2L must not modify the live canonical version or any file the
    executor could touch. Scoped to the inventoried files + version-bearing
    records because other workers actively commit to the shared tree. The
    live version is whatever the tree currently declares (asserted for
    internal agreement, never a frozen literal)."""
    from amca.version_freeze_preflight import (
        ACTIVE_CODE_REFERENCES, ACTIVE_VERSION_DECLARATIONS,
        active_version_agreement)
    agreement = active_version_agreement()
    assert agreement["current_version"] == CURRENT_ACTIVE_VERSION
    assert agreement["agrees"] is True

    watched = [loc.file for loc in ACTIVE_VERSION_DECLARATIONS]
    watched += [loc.file for loc in ACTIVE_CODE_REFERENCES]
    watched += ["dist/release_manifest_v0.9.0.json", "dist/SHA256SUMS.txt"]

    def hashes():
        out = {}
        for rel in watched:
            p = SOFTWARE_ROOT / rel
            if p.is_file():
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    before = hashes()
    result = apply_transition(apply=False)
    # The live tree is not at the executor's declared start version, so the
    # honest outcome is BLOCKED; either way no live file may change.
    assert result["overall"] in ("DRY_RUN", "BLOCKED")
    after = hashes()
    assert before == after, "dry-run modified a live file"


def test_runner_write_mode_requires_authorization(tmp_path):
    out = tmp_path / "r.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--apply", "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "BLOCKED"
    assert any("authorize-bump" in r for r in data["reasons"])
    assert proc.returncode != 0


def test_runner_dry_run_contract(tmp_path):
    out = tmp_path / "dry.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["target_version"] == "1.0.0"
    # The executor's declared starting precondition is 0.9.0; the live tree
    # may already be past it, in which case the honest outcome is BLOCKED.
    assert data["start_version"] == "0.9.0"
    if CURRENT_ACTIVE_VERSION == data["start_version"]:
        assert data["overall"] == "DRY_RUN"
        assert data["dry_run"] is True
        assert proc.returncode == 0
    else:
        assert data["overall"] == "BLOCKED"
        assert any(CURRENT_ACTIVE_VERSION in r for r in data["reasons"])
        assert proc.returncode != 0
    assert data["artifact_name_simulation"]["installer"] == \
        "amca-studio-v1.0.0-windows-x64-setup.exe"
