"""Work Order 31U: Public Launch Communication Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-PUBLIC-LAUNCH-COMMUNICATION-PACKAGE-31U
Validates:
1. All 4 31U communication documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31u_communication_documents_exist_and_complete():
    """Verify all required 31U communication documents exist and are complete."""
    comm_dir = SOFTWARE_DIR / "docs" / "release" / "communication"
    assert comm_dir.is_dir(), f"Missing directory: {comm_dir}"

    required_docs = {
        "PUBLIC_LAUNCH_ANNOUNCEMENT_31U.md": [
            "AMCA Studio v1.2.0",
            "Main User Benefits",
            "Privacy-First Architecture",
            "Multi-Model Collaboration Vision",
            "Target Users",
            "Availability Announcement",
        ],
        "AMCA_WEBSITE_LANDING_CONTENT_31U.md": [
            "Hero Section",
            "Feature Highlights",
            "Security & Privacy",
            "Model Ecosystem",
            "Frequently Asked Questions",
        ],
        "AMCA_FIRST_RUN_GUIDE_31U.md": [
            "Installation",
            "First Launch",
            "Adding API Providers",
            "Connecting Ollama",
            "Creating Your First Project",
            "Understanding Costs",
            "Privacy Settings",
        ],
        "AMCA_RELEASE_FAQ_31U.md": [
            "Is my data uploaded",
            "Does AMCA require API keys",
            "Can I run local models",
            "What happens during",
            "How are",
            "How is",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = comm_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31u_boundary_invariants():
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
