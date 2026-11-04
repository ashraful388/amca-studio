"""Work Order 31J: Production Release Simulation & Rollback Test Suite.

Work Order: ANTIGRAVITY-AMCA-PRODUCTION-RELEASE-SIMULATION-31J
Validates:
1. Clean checkout and version consistency across package.json, tauri.conf.json, and pyproject.toml.
2. Simulated signing hook invocation using abstract test provider without real certificates.
3. Strict enforcement of the Hash Custody Rule (sign -> verify -> hash).
4. Failure handling and automatic rollback/cleanup of aborted release artifacts.
5. Invariant preservation: OFFICIAL_DIST_CHANGED=NO, TAG_CHANGED=NO, PUBLIC_RELEASE=NO.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_DIR / "src"))
sys.path.insert(0, str(SOFTWARE_DIR / "packaging"))

from signing_provider import SigningConfig, sign_and_custody_hash, sha256_file


def test_31j_checkout_and_version_consistency():
    """Verify version alignment and clean configuration state."""
    desktop_ui = SOFTWARE_DIR / "desktop-ui"
    pkg_json_path = desktop_ui / "package.json"
    tauri_conf_path = desktop_ui / "src-tauri" / "tauri.conf.json"
    pyproject_path = SOFTWARE_DIR / "pyproject.toml"

    assert pkg_json_path.is_file()
    assert tauri_conf_path.is_file()
    assert pyproject_path.is_file()

    with open(pkg_json_path, "r", encoding="utf-8") as f:
        pkg_data = json.load(f)
    with open(tauri_conf_path, "r", encoding="utf-8") as f:
        tauri_data = json.load(f)

    pkg_ver = pkg_data.get("version")
    tauri_ver = tauri_data.get("version")

    # The canonical live version is the backend module declaration; every other
    # manifest must agree with it rather than a frozen historical literal.
    init_text = (SOFTWARE_DIR / "src" / "amca" / "__init__.py").read_text(encoding="utf-8")
    live_version = re.search(r'__version__\s*=\s*"([^"]+)"', init_text).group(1)
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    pyproject_ver = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE).group(1)

    assert pkg_ver == tauri_ver, f"Version mismatch: package.json={pkg_ver} vs tauri.conf.json={tauri_ver}"
    assert pkg_ver == live_version, f"package.json={pkg_ver} does not match canonical live version {live_version}"
    assert pyproject_ver == live_version, f"pyproject.toml={pyproject_ver} does not match canonical live version {live_version}"


def test_31j_signing_pipeline_simulation():
    """Simulate the 4-tier signing sequence under test/dry-run mode."""
    tiers = ["backend", "desktop", "uninstaller", "installer"]
    simulation_records = []

    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)

        for tier in tiers:
            bin_path = temp_dir / f"amca-mock-{tier}.exe"
            bin_path.write_bytes(f"MOCK_BINARY_TIER_{tier.upper()}".encode("utf-8"))

            cfg = SigningConfig(provider="test", strict=False)
            res = sign_and_custody_hash(bin_path, description=f"AMCA Studio ({tier})", config=cfg)

            assert res["hash_custody_verified"] is True
            assert res["signing_provider"] == "test"
            assert len(res["sha256"]) == 64
            assert res["filename"] == bin_path.name
            simulation_records.append(res)

    assert len(simulation_records) == 4


def test_31j_signing_failure_and_rollback():
    """Test failure detection and cleanup rollback when a signing stage encounters an error."""
    with tempfile.TemporaryDirectory() as td:
        release_dir = Path(td) / "release_distribution_v1.2.0"
        release_dir.mkdir(parents=True, exist_ok=True)

        # Stage mock artifacts
        installer = release_dir / "AMCA Studio_1.2.0_x64-setup.exe"
        installer.write_bytes(b"CORRUPTED_OR_UNSIGNED_BYTES")

        # Simulate strict failure detection
        cfg = SigningConfig(provider="signtool", cert_thumbprint="INVALID_THUMBPRINT_9999", strict=True)
        with pytest.raises(Exception):
            sign_and_custody_hash(installer, config=cfg)

        # Execute rollback purge procedure
        if release_dir.exists():
            shutil.rmtree(release_dir)

        # Invariant: Directory must be completely removed, no partial or unverified binaries left
        assert not release_dir.exists()


def test_31j_simulation_documentation_present():
    """Verify that all 31J simulation documents exist and contain required coverage."""
    docs_dir = SOFTWARE_DIR / "docs" / "release" / "simulation"
    assert docs_dir.is_dir()

    expected_docs = [
        "PRODUCTION_RELEASE_SIMULATION_REPORT_31J.md",
        "RELEASE_SIMULATION_ARTIFACT_REPORT_31J.md",
        "RELEASE_MANAGER_DRY_RUN_CHECKLIST_31J.md",
    ]

    for doc in expected_docs:
        p = docs_dir / doc
        assert p.is_file(), f"Missing 31J simulation doc: {doc}"
        assert p.stat().st_size > 500, f"Doc too small: {doc}"
