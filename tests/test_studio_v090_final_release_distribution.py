"""Unit tests for AMCA Studio v0.9.0 Final Release Distribution Package.

Milestone: ZCODE-AMCA-STUDIO-V0.9.0-FINAL-RELEASE-PREP-AND-DISTRIBUTION-PACKAGE-01

The v0.9.0 manifest is a frozen historical release record: these tests verify
that it is internally consistent, that every artifact it declares is a real
physical file (nothing fabricated), and that no final-release promotion has
happened. No assertion is tied to a specific artifact count or to the live
tree version.
"""

from pathlib import Path
import hashlib
import json
import re

from amca.final_release import FinalReleaseManager


def _load_manifest(repo_root: Path):
    manager = FinalReleaseManager(repo_root=repo_root)
    return manager, manager.load_manifest()


def test_final_release_manifest_structure_and_version():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager, manifest = _load_manifest(repo_root)

    assert manifest["product_name"] == "AMCA Studio"
    # The manifest declares the version encoded in its own filename.
    filename_version = re.search(r"release_manifest_v([0-9.]+)\.json", manager.manifest_path.name).group(1)
    assert manifest["version"] == filename_version
    assert manifest["product_identity"]["version"] == manifest["version"]
    # I2B: pipeline manifests are FUNCTIONALLY_VERIFIED_BUILD until canonical
    # certification PASS on the same SHA unlocks the final release.
    assert manifest["release_type"] == "FUNCTIONALLY_VERIFIED_BUILD"
    assert manifest["release_status"] == "NOT_FINAL_RELEASE_CERTIFIED"
    assert manifest["certification_status"] == "BLOCKED"

    # Every declared artifact must be a physically verifiable file, and every
    # skipped artifact must carry an explicit reason (nothing silently dropped
    # or fabricated).
    assert len(manifest["artifacts"]) >= 1
    for art in manifest["artifacts"]:
        physical = manager.dist_dir / art["package_name"]
        assert physical.is_file(), f"declared artifact {art['package_name']} must exist"
        assert len(art["sha256"]) == 64
        assert art["size_bytes"] == physical.stat().st_size
        assert hashlib.sha256(physical.read_bytes()).hexdigest() == art["sha256"]
        assert art["version"] == manifest["version"]
    for skipped in manifest.get("skipped_artifacts", []):
        assert skipped.get("artifact") and skipped.get("reason")


def test_final_release_artifacts_and_checksums():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager, manifest = _load_manifest(repo_root)
    res = manager.validate_final_release()

    # The manager reports exactly the artifact set the manifest declares.
    assert res.artifact_count == len(manifest["artifacts"])
    # The manifest's own artifacts are all physically verifiable.
    assert res.artifact_count >= 1
    for art in manifest["artifacts"]:
        physical = manager.dist_dir / art["package_name"]
        assert physical.is_file()
        assert hashlib.sha256(physical.read_bytes()).hexdigest() == art["sha256"]
    # No promotion happened: without canonical certification PASS the release
    # is honestly not ready.
    assert res.is_ready_for_release is False


def test_final_release_documentation_freeze():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager = FinalReleaseManager(repo_root=repo_root)
    res = manager.validate_final_release()

    assert res.documentation_verified is True

    # Ensure release notes have GA designation and zero RC markers
    notes_path = repo_root / "software" / "docs" / "release" / "AMCA-Studio-v0.9.0-Release-Notes.md"
    content = notes_path.read_text(encoding="utf-8")
    assert "General Availability (v0.9.0 GA)" in content


def test_final_release_governance_invariants():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager = FinalReleaseManager(repo_root=repo_root)
    res = manager.validate_final_release()

    assert res.governance_contract == "LOCAL_ONLY_FIRST_HUMAN_GOVERNED_EXECUTION"


def test_final_release_not_ready_until_canonical_certification():
    """I2B: physically verified build artifacts alone are NOT releasable.

    is_ready_for_release requires certification_status
    CERTIFIED_FOR_FINAL_RELEASE, which the pipeline only records when
    canonical certification PASS + same SHA + clean tree gate the build.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager = FinalReleaseManager(repo_root=repo_root)
    res = manager.validate_final_release()

    assert res.version == "0.9.0"
    assert res.release_type == "FUNCTIONALLY_VERIFIED_BUILD"
    assert res.certification_status == "BLOCKED"
    assert res.is_ready_for_release is False
    assert any("Release gate state" in d for d in res.details)

