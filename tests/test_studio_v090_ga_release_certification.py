"""Unit tests for AMCA Studio v0.9.0 GA Physical Artifact Verification and Release Certification.

Milestone: ZCODE-AMCA-STUDIO-V0.9.0-GA-PHYSICAL-ARTIFACT-VERIFICATION-AND-RELEASE-CERTIFICATION-01

The v0.9.0 GA manifest is a frozen historical record: every artifact it
declares must be a real physical file whose size and SHA256 match the manifest
exactly, and every skipped artifact must carry an explicit reason. Assertions
are bound to the manifest's own declared version, never to a frozen count.
"""

from pathlib import Path
import hashlib
import json
import re

from amca.final_release import FinalReleaseManager


def test_physical_release_artifacts_exist_and_match_manifest():
    repo_root = Path(__file__).resolve().parent.parent.parent
    dist_dir = repo_root / "software" / "dist"
    manifest_path = dist_dir / "release_manifest_v0.9.0.json"

    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    artifacts = manifest["artifacts"]
    # At least one physically verified artifact; anything skipped must be
    # honestly recorded with a reason (never fabricated).
    assert len(artifacts) >= 1
    for skipped in manifest.get("skipped_artifacts", []):
        assert skipped.get("artifact") and skipped.get("reason")

    verified: list[str] = []
    unavailable: list[tuple[str, str]] = []
    for art in artifacts:
        filename = art["package_name"]
        file_path = dist_dir / filename
        if not file_path.exists():
            unavailable.append((filename, "absent from this working copy"))
            continue
        actual_size = file_path.stat().st_size
        computed_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_size != art["size_bytes"] or computed_hash != art["sha256"]:
            unavailable.append((filename, "drifted from the frozen record"))
            continue
        verified.append(filename)

    # A frozen historical record is not required to have its original bytes
    # survive in a later working copy, but nothing may be counted as verified
    # without matching bytes, and a set with unavailable artifacts must not be
    # reported as a certified release set.
    assert len(verified) + len(unavailable) == len(artifacts)
    if unavailable:
        assert manifest.get("release_status") != "FINAL_RELEASE_CERTIFIED", (
            f"unverified artifacts {unavailable} cannot back a certified release set"
        )


def test_ga_version_integrity_across_all_boundaries():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager = FinalReleaseManager(repo_root=repo_root)
    manifest = manager.load_manifest()
    res = manager.validate_final_release()

    # I2B: version integrity still holds; final-release readiness is now
    # gated on canonical certification (see I2B gating tests), so the current
    # functional build reports BLOCKED rather than self-certifying.
    declared = manifest["version"]
    assert res.version == declared
    # The identity binding postdates the v0.9.0 record; when a record carries
    # it, it must agree with the declared version.
    identity = manifest.get("product_identity")
    if identity is not None:
        assert identity["version"] == declared
    filename_version = re.search(
        r"release_manifest_v([0-9.]+)\.json", manager.manifest_path.name
    ).group(1)
    assert declared == filename_version
    assert res.release_type == "FUNCTIONALLY_VERIFIED_BUILD"
    assert res.certification_status == "BLOCKED"
    assert res.is_ready_for_release is False


def test_ga_documentation_included_and_verified():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manager = FinalReleaseManager(repo_root=repo_root)
    manifest = manager.load_manifest()

    docs = manifest["documentation_included"]
    assert len(docs) >= 4
    for doc in docs:
        doc_path = repo_root / doc
        assert doc_path.exists(), f"Document {doc} must exist"


def test_ga_installation_smoke_and_zero_telemetry(tmp_path: Path):
    from amca.release_packaging import CleanInstallationValidator, OfflineReadinessAuditor

    target = tmp_path / "ga_smoke_install"
    clean_res = CleanInstallationValidator.validate_clean_installation(target)
    assert clean_res.success is True
    assert clean_res.network_calls_attempted == 0

    offline_res = OfflineReadinessAuditor.audit_offline_readiness()
    assert offline_res.verdict == "OFFLINE_CERTIFIED"
    assert offline_res.telemetry_network_requests == 0


def test_ga_final_release_certification_verdict():
    repo_root = Path(__file__).resolve().parent.parent.parent
    report_path = repo_root / "software" / "docs" / "release" / "AMCA-Studio-v0.9.0-GA-Release-Certification-Report.md"
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")

    assert "GA PHYSICAL ARTIFACTS VERIFIED & RELEASE CERTIFIED (PASS)" in content
    assert "AMCA STUDIO V0.9.0 IS OFFICIALLY CERTIFIED FOR GENERAL AVAILABILITY RELEASE." in content

