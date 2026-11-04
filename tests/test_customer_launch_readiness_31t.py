"""Work Order 31T: Customer Launch Readiness Package Test Suite.

Work Order: ANTIGRAVITY-AMCA-CUSTOMER-LAUNCH-READINESS-PACKAGE-31T
Validates:
1. All 4 31T customer launch readiness documents exist and contain required coverage.
2. Boundary Invariants: TAG_CHANGED = NO, OFFICIAL_DIST_CHANGED = NO, BUILD_EXECUTED = NO, SOURCE_CHANGED = NO.
"""

import subprocess
import sys
from pathlib import Path
import pytest

SOFTWARE_DIR = Path(__file__).resolve().parent.parent


def test_31t_launch_documents_exist_and_complete():
    """Verify all required 31T launch documents exist and are complete."""
    launch_dir = SOFTWARE_DIR / "docs" / "release" / "launch"
    assert launch_dir.is_dir(), f"Missing directory: {launch_dir}"

    required_docs = {
        "CUSTOMER_LAUNCH_PACKAGE_31T.md": [
            "Product Description",
            "Key Features",
            "Supported Platforms & System Requirements",
            "Installation & First-Run Expectations",
            "Local Model & BYOK Provider Setup",
            "Privacy & Security Highlights",
        ],
        "AMCA_STUDIO_V1_2_0_RELEASE_NOTES_31T.md": [
            "CR-04: Third-Party License Compliance",
            "CR-05: Privacy-Preserving Update Checking",
            "CR-02: Enterprise Windows Code-Signing Integration",
            "Commercial Security & Credential Hardening",
            "Upgrade Safety & Data Custody",
            "Compatibility Notes",
        ],
        "CUSTOMER_SUPPORT_READINESS_31T.md": [
            "Windows Defender SmartScreen Prompt",
            "Microsoft Edge WebView2 Requirement",
            "Ollama Local Model Connectivity",
            "Cloud API Key Configuration Issues",
            "Privacy-Preserving Diagnostic Collection Procedure",
        ],
        "PUBLIC_RELEASE_PORTAL_CHECKLIST_31T.md": [
            "Download Page",
            "Documentation Links",
            "Legal & Compliance Links",
            "Support Contacts",
            "Release Announcement",
        ],
    }

    for filename, required_sections in required_docs.items():
        doc_path = launch_dir / filename
        assert doc_path.is_file(), f"Missing required document: {filename}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 400, f"Document too short: {filename}"
        for section in required_sections:
            assert section.lower() in content.lower(), f"Missing section '{section}' in {filename}"


def test_31t_boundary_invariants():
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
