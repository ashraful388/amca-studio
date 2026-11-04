"""I2E tests: final release promotion gate and evidence orchestration.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2E-FINAL-RELEASE-PROMOTION-GATE-AND-EVIDENCE-ORCHESTRATION-01

Deterministic state coverage: BLOCKED / REJECTED / READY_FOR_PROMOTION /
PROMOTED, plus dry-run safety and no-silent-overwrite guarantees.
"""

from pathlib import Path
import hashlib
import json
import zipfile

import pytest

from amca import __version__ as LIVE_VERSION
from amca.final_promotion import (
    CANONICAL_PRODUCT_IDENTITY_V1_0_1,
    DEFAULT_OFFICIAL_DEST,
    EvidenceBundle,
    ST_BLOCKED,
    ST_PROMOTED,
    ST_READY,
    ST_REJECTED,
    evaluate_evidence,
    promote_final_release,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"


def _ps1(verdict: str, sha: str) -> dict:
    return {
        "overall_verdict": verdict,
        "provenance": {"sha": sha, "branch": "canonical", "clean": True, "dirty": False},
    }


def _ps2(verdict: str, sha: str) -> dict:
    return {"overall_verdict": verdict, "certified_sha": sha}


def _smoke(installer: str, portable: str, sha: str, msi: str = "PASS") -> dict:
    # H2A.2 S4: v1.0.1+ bundles carry an MSI smoke record by default; the
    # version-aware msi_smoke gate consumes it. Tests targeting a specific
    # non-MSI gate pass "PASS" here unless they deliberately exercise the
    # MSI gate itself.
    return {
        "schema": "amca-distribution-smoke-result/1",
        "source_sha": sha,
        "artifacts": {
            "installer": {"overall": installer, "source_sha": sha},
            "msi": {"overall": msi, "source_sha": sha},
            "portable": {"overall": portable, "source_sha": sha},
        },
    }


def _manifest(sha: str, version: str = LIVE_VERSION) -> dict:
    # H2B.2 Part C: v1.0.1+ manifests must bind the canonical product
    # identity (the product_identity gate rejects/blocks v1.0.1+ evidence
    # without it). Historical v1.0.0 fixtures keep their old shape.
    manifest = {
        "version": version,
        "provenance": {
            "source_git_sha": sha,
            "release_gate_state": "ELIGIBLE",
            "final_release_certified": True,
        },
        "artifacts": [
            {"package_name": f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe",
             "sha256": hashlib.sha256(b"i" * 64).hexdigest(), "size_bytes": 64},
            {"package_name": f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip",
             "sha256": hashlib.sha256(b"p" * 64).hexdigest(), "size_bytes": 64},
        ],
    }
    try:
        is_v101_plus = [int(p) for p in version.split(".")] >= [1, 0, 1]
    except ValueError:
        is_v101_plus = False
    if is_v101_plus:
        manifest["product_identity"] = dict(CANONICAL_PRODUCT_IDENTITY_V1_0_1,
                                            version=version)
    return manifest


def _fixture_dir(tmp_path: Path, *, ps1="PASS", ps2="PASS", installer="PASS",
                 portable="PASS", sha="a" * 40, manifest_sha=None,
                 physical=False) -> dict:
    """Write a full synthetic evidence set into tmp_path; optionally create
    matching physical artifacts so the physical-hash gate can pass."""
    d = tmp_path / "evidence"
    (d / "dist").mkdir(parents=True, exist_ok=True)
    (d / "tests").mkdir(parents=True, exist_ok=True)
    msha = manifest_sha or sha
    (d / "tests" / "cert.json").write_text(json.dumps(_ps1(ps1, sha)), encoding="utf-8")
    (d / "tests" / "migration.json").write_text(json.dumps(_ps2(ps2, sha)), encoding="utf-8")
    (d / "dist" / "smoke.json").write_text(
        json.dumps(_smoke(installer, portable, sha)), encoding="utf-8")
    (d / "dist" / "manifest.json").write_text(
        json.dumps(_manifest(msha)), encoding="utf-8")
    if physical:
        # Real files whose hashes match the manifest records above.
        (d / "dist" / f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe").write_bytes(b"i" * 64)
        (d / "dist" / f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip").write_bytes(b"p" * 64)
    return {
        "ps1": d / "tests" / "cert.json",
        "ps2": d / "tests" / "migration.json",
        "smoke": d / "dist" / "smoke.json",
        "manifest": d / "dist" / "manifest.json",
        "dist": d / "dist",
        "root": d,
    }


def _eval(fx: dict, sha: str = "a" * 40, dirty: bool = False):
    return evaluate_evidence(
        ps1_path=fx["ps1"], ps2_path=fx["ps2"], smoke_path=fx["smoke"],
        manifest_path=fx["manifest"], dist_dir=fx["dist"],
        source_provenance={"source_git_sha": sha,
                           "source_branch": "test", "dirty_tree": dirty})


# ---------------------------------------------------------------------------
# Deterministic state machine
# ---------------------------------------------------------------------------

def test_missing_canonical_certification_blocked(tmp_path):
    fx = _fixture_dir(tmp_path)
    fx["ps1"].unlink()
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_BLOCKED
    assert bundle.certification.verdict == ST_BLOCKED


def test_canonical_fail_rejected(tmp_path):
    fx = _fixture_dir(tmp_path, ps1="FAIL", physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_REJECTED
    assert bundle.certification.verdict == "FAIL"


def test_migration_blocked_blocks(tmp_path):
    fx = _fixture_dir(tmp_path, ps2="BLOCKED", physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_BLOCKED
    assert bundle.migration.verdict == ST_BLOCKED


def test_migration_fail_rejected(tmp_path):
    fx = _fixture_dir(tmp_path, ps2="FAIL", physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_REJECTED


def test_missing_migration_evidence_blocked(tmp_path):
    fx = _fixture_dir(tmp_path, physical=True)
    fx["ps2"].unlink()
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_BLOCKED
    assert "PS-2" in bundle.migration.detail


def test_migration_compatible_sha_recorded_not_inferred(tmp_path):
    fx = _fixture_dir(tmp_path, physical=True)
    ps2 = {"overall_verdict": "PASS", "compatible_schema_certification_sha": "c" * 40}
    fx["ps2"].write_text(json.dumps(ps2), encoding="utf-8")
    bundle = _eval(fx)
    assert bundle.migration.sha == "c" * 40
    assert "explicitly" in bundle.migration.detail.lower() or \
           "compatible" in bundle.migration.detail.lower()


def test_smoke_fail_rejected(tmp_path):
    fx = _fixture_dir(tmp_path, installer="FAIL", physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_REJECTED
    assert bundle.installer_smoke.verdict == "FAIL"


def test_portable_smoke_blocked_blocks(tmp_path):
    fx = _fixture_dir(tmp_path, portable="BLOCKED", physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_BLOCKED


def test_missing_physical_artifact_blocked(tmp_path):
    fx = _fixture_dir(tmp_path, physical=False)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_BLOCKED
    assert bundle.release_gate.verdict == ST_BLOCKED


def test_bad_physical_hash_rejected(tmp_path):
    fx = _fixture_dir(tmp_path, physical=True)
    # Corrupt the portable payload after manifest was written against b"p"*64.
    (fx["dist"] / f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip").write_bytes(b"X" * 64)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_BLOCKED  # physical gate blocks first
    assert bundle.release_gate.verdict == ST_BLOCKED


def test_sha_mismatch_rejected(tmp_path):
    fx = _fixture_dir(tmp_path, sha="a" * 40, manifest_sha="b" * 40, physical=True)
    bundle = _eval(fx, sha="a" * 40)
    assert bundle.promotion_state in (ST_REJECTED, ST_BLOCKED)
    assert bundle.release_gate.verdict in (ST_REJECTED, ST_BLOCKED)


def test_dirty_tree_rejected(tmp_path):
    fx = _fixture_dir(tmp_path, physical=True)
    bundle = _eval(fx, dirty=True)
    assert bundle.promotion_state == ST_REJECTED
    assert bundle.release_gate.verdict == ST_REJECTED


def test_complete_valid_evidence_ready_for_promotion(tmp_path):
    fx = _fixture_dir(tmp_path, physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_READY, bundle.reasons
    assert bundle.final_release_version == LIVE_VERSION
    assert bundle.canonical_source_sha == "a" * 40
    doc = bundle.to_document()
    for key in ("final_release_version", "canonical_source_sha",
                "certification", "migration", "installer_smoke",
                "portable_smoke", "release_gate", "version_contract",
                "physical_artifacts", "promotion_state",
                "known_limitations"):
        assert key in doc


def test_current_real_evidence_is_honestly_blocked():
    """With the live evidence set (no PS-2 yet, certification BLOCKED), the
    real gate must report BLOCKED — never infer readiness."""
    if not (SOFTWARE_ROOT / "tests" / "certification-result.json").is_file():
        pytest.skip("no live certification evidence")
    bundle = evaluate_evidence()
    assert bundle.promotion_state == ST_BLOCKED


# ---------------------------------------------------------------------------
# Promotion mechanics
# ---------------------------------------------------------------------------

def _ready_bundle(tmp_path: Path) -> EvidenceBundle:
    fx = _fixture_dir(tmp_path, physical=True)
    bundle = _eval(fx)
    assert bundle.promotion_state == ST_READY
    return bundle


def _artifact_set(fx: dict, tmp_path: Path) -> dict:
    # Extra mandatory inventory docs (synthetic, distinct filenames).
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text("deadbeef  installer\n", encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("release notes", encoding="utf-8")
    limits = tmp_path / "limits.md"
    limits.write_text("known limitations", encoding="utf-8")
    cert = tmp_path / "cert.md"
    cert.write_text("certification summary", encoding="utf-8")
    # H2A: the v1.0.1 version-aware inventory requires a windows-msi role.
    msi = fx["dist"] / f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe.copy"
    msi.write_bytes(Path(fx["dist"] / f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe").read_bytes())
    return {
        "windows-installer": fx["dist"] / f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe",
        "windows-msi": msi,
        "windows-portable": fx["dist"] / f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip",
        "sha256sums": sums,
        "release-manifest": fx["manifest"],
        "release-notes": notes,
        "known-limitations": limits,
        "certification-summary": cert,
    }


def test_dry_run_never_copies_files(tmp_path):
    bundle = _ready_bundle(tmp_path)
    dest = tmp_path / "official"
    before = sorted(p.name for p in dest.iterdir()) if dest.exists() else []
    result = promote_final_release(
        bundle, artifacts=_artifact_set(_fixture_dir(tmp_path), tmp_path),
        destination=dest, dry_run=True)
    assert result["overall"] == ST_READY
    assert result["dry_run"] is True
    assert result["promoted"] == []
    after = sorted(p.name for p in dest.iterdir()) if dest.exists() else []
    assert after == before, "dry-run must not touch the destination"


def test_promotion_requires_ready_state(tmp_path):
    bundle = evaluate_evidence()  # live evidence: BLOCKED
    result = promote_final_release(
        bundle, artifacts={}, destination=tmp_path / "official", dry_run=False)
    assert result["overall"] == ST_BLOCKED
    assert result["promoted"] == []


def test_promotion_copies_and_verifies_hashes(tmp_path):
    bundle = _ready_bundle(tmp_path)
    fx = _fixture_dir(tmp_path, physical=True)
    dest = tmp_path / "official"
    result = promote_final_release(
        bundle, artifacts=_artifact_set(fx, tmp_path),
        destination=dest, dry_run=False)
    assert result["overall"] == ST_PROMOTED
    assert result["post_copy_hash_match"] is True
    for record in result["promoted"]:
        assert Path(record["destination"]).is_file()


def test_promotion_never_silently_overwrites(tmp_path):
    bundle = _ready_bundle(tmp_path)
    fx = _fixture_dir(tmp_path, physical=True)
    dest = tmp_path / "official"
    dest.mkdir(parents=True, exist_ok=True)
    sentinel = dest / f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe"
    sentinel.write_text("EXISTING OFFICIAL ARTIFACT", encoding="utf-8")
    result = promote_final_release(
        bundle, artifacts=_artifact_set(fx, tmp_path),
        destination=dest, dry_run=False)
    assert result["overall"] == ST_BLOCKED
    assert sentinel.read_text(encoding="utf-8") == "EXISTING OFFICIAL ARTIFACT"
    assert result["skipped_existing"]

    # Explicit overwrite unlocks promotion.
    result2 = promote_final_release(
        bundle, artifacts=_artifact_set(fx, tmp_path),
        destination=dest, allow_overwrite=True, dry_run=False)
    assert result2["overall"] == ST_PROMOTED


def test_promotion_requires_complete_inventory(tmp_path):
    bundle = _ready_bundle(tmp_path)
    fx = _fixture_dir(tmp_path, physical=True)
    artifacts = _artifact_set(fx, tmp_path)
    artifacts.pop("release-notes")
    result = promote_final_release(
        bundle, artifacts=artifacts, destination=tmp_path / "official",
        dry_run=False)
    assert result["overall"] == ST_BLOCKED
    assert any("release-notes" in r for r in result["reasons"])


def test_official_destination_is_the_standing_release_location():
    assert str(DEFAULT_OFFICIAL_DEST) == r"D:\ChatGPT\AMCA\software\dist"
