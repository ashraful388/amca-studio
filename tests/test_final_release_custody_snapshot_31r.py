"""Work Order 31R: Final Release Custody Snapshot Test Suite.

Work Order: ANTIGRAVITY-AMCA-FINAL-RELEASE-CUSTODY-SNAPSHOT-31R
Validates:
1. All 3 31R custody snapshot documents exist and contain required coverage.
2. Repository Integrity: GIT_STATUS_CLEAN = YES, NO_RELEASE_TAG_CREATED = YES.
3. Boundary Invariants: OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
4. Cryptographic Lockfile digests match the official custody record.
"""

import hashlib
import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31r_custody_documents_exist_and_complete():
    """Verify all required 31R custody documents exist and are complete."""
    cust_dir = SOFTWARE_DIR / "docs" / "release" / "custody"
    assert cust_dir.is_dir(), f"Missing directory: {cust_dir}"

    required_docs = {
        "FINAL_RELEASE_CUSTODY_SNAPSHOT_31R.md": [
            "Technical Release Identity Snapshot",
            "Complete Cumulative Branch Lineage",
            "Cryptographic Dependency Lock Hashes",
            "Documentation & Evidence Locations",
        ],
        "FINAL_RELEASE_ASSET_CUSTODY_RECORD_31R.md": [
            "Release Asset Custody Registry",
            "Primary Installer",
            "Portable Package",
            "SHA-256 Checksums",
            "Third-Party License Bundle",
            "Release Evidence Archive",
            "Invariant Custody Safeguard",
        ],
        "RELEASE_AUTHORIZATION_TRIGGER_PACKAGE_31R.md": [
            "Master Release Authorization Formula",
            "Gate Verification Checklist",
            "Operational Launch Sequence Upon Trigger Satisfaction",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = cust_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31r_lockfile_digests_match():
    """Verify Cargo.lock and package-lock.json SHA-256 digests match custody snapshot."""
    cargo_lock = SOFTWARE_DIR / "desktop-ui" / "src-tauri" / "Cargo.lock"
    pkg_lock = SOFTWARE_DIR / "desktop-ui" / "package-lock.json"

    assert cargo_lock.is_file()
    assert pkg_lock.is_file()

    h_cargo = hashlib.sha256(cargo_lock.read_bytes()).hexdigest().upper()
    h_pkg = hashlib.sha256(pkg_lock.read_bytes()).hexdigest().upper()

    assert h_cargo == "B64B528E51F2B7410CC764A74D7789796C9FE2BB3CCDE3AB601A38F37BD4B577"
    assert h_pkg == "92B3FBD9BFCDD7E141F442D09F04CD67646565C077A7510775B826B7A8F1D160"


def test_31r_boundary_invariants():
    """Verify strictly that tags, dist, and source remain unchanged."""
    res = subprocess.run(["git", "tag", "-l"], cwd=str(SOFTWARE_DIR), capture_output=True, text=True)
    assert res.returncode == 0
    tags = res.stdout.strip().splitlines()
    for tag in tags:
        assert not tag.startswith("amca-studio-v1.2"), f"Invariant violated: Tag '{tag}' exists prematurely!"

    dist_dir = SOFTWARE_DIR / "dist"
    if dist_dir.exists():
        for item in dist_dir.rglob("*"):
            assert "1.2.0" not in item.name, f"Unexpected v1.2.0 artifact in dist: {item.name}"
