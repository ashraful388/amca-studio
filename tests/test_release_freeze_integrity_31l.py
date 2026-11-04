"""Work Order 31L: Release Freeze Integrity Audit Test Suite.

Work Order: ANTIGRAVITY-AMCA-RELEASE-FREEZE-INTEGRITY-AUDIT-31L
Validates:
1. All 3 release freeze audit documents exist and contain required coverage.
2. Tagging Invariant: TAG_CHANGED = NO (Zero v1.2 tags exist).
3. Distribution Invariant: OFFICIAL_DIST_CHANGED = NO.
4. Dependency Lock Invariant: Cargo.lock and package-lock.json are present.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31l_freeze_documents_exist_and_complete():
    """Verify all 3 required 31L freeze audit documents are present and complete."""
    freeze_dir = SOFTWARE_DIR / "docs" / "release" / "freeze"
    assert freeze_dir.is_dir(), f"Missing directory: {freeze_dir}"

    required_docs = {
        "RELEASE_FREEZE_INTEGRITY_REPORT_31L.md": [
            "Source Freeze & Repository Cleanliness",
            "Release Boundary Audit",
            "Dependency Drift Audit & Lock State",
            "Summary Conclusion",
        ],
        "EXTERNAL_RELEASE_BLOCKER_REGISTER_31L.md": [
            "Active External Release Blockers",
            "Commercial Code-Signing Certificate",
            "Commercial Legal Approval",
            "Engineering Standby Protocol",
        ],
        "AMCA_RELEASE_FREEZE_SNAPSHOT_31L.md": [
            "Technical Release Identity Snapshot",
            "Certified Commercial Feature Set",
            "Known Pending External Items",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = freeze_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 500, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31l_tag_and_dist_invariants():
    """Verify strictly that no tags or dist files have been modified."""
    res = subprocess.run(["git", "tag", "-l"], cwd=str(SOFTWARE_DIR), capture_output=True, text=True)
    assert res.returncode == 0
    tags = res.stdout.strip().splitlines()
    for tag in tags:
        assert not tag.startswith("amca-studio-v1.2"), f"Invariant violated: Tag '{tag}' exists prematurely!"

    dist_dir = SOFTWARE_DIR / "dist"
    if dist_dir.exists():
        for item in dist_dir.rglob("*"):
            assert "1.2.0" not in item.name, f"Unexpected v1.2.0 artifact in dist: {item.name}"


def test_31l_dependency_locks_present():
    """Verify Cargo.lock and package-lock.json exist."""
    cargo_lock = SOFTWARE_DIR / "desktop-ui" / "src-tauri" / "Cargo.lock"
    pkg_lock = SOFTWARE_DIR / "desktop-ui" / "package-lock.json"

    assert cargo_lock.is_file(), "Cargo.lock is missing"
    assert pkg_lock.is_file(), "package-lock.json is missing"
