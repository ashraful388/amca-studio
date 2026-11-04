"""I2B guard tests: distribution provenance and release gating.

Milestone: AG-AMCA-STUDIO-V1.0.0-I2B-DISTRIBUTION-PROVENANCE-RELEASE-GATING-AND-BUILD-HYGIENE-01

Guarantees that a physically valid package built from a non-certified
branch/SHA can never be mislabeled as an official final AMCA Studio release.
"""

from pathlib import Path
import json

import pytest

from amca.distribution_build import find_latest_installer, run_distribution_pipeline
from amca.release_gating import (
    DEFAULT_EVIDENCE_PATH,
    evaluate_release_gate,
    load_certification_evidence,
)

import re

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
DIST_DIR = SOFTWARE_ROOT / "dist"

#: Live canonical version (single source of truth), so the pipeline tests
#: always package the version this tree actually declares.
LIVE_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)

CURRENT_SHA_CANDIDATES = ["a" * 40, "b" * 40]


def _evidence(verdict: str, sha: str) -> dict:
    return {
        "overall_verdict": verdict,
        "provenance": {"sha": sha, "branch": "canonical/main", "clean": True, "dirty": False},
        "blocked_reasons": ["regression evidence missing"] if verdict != "PASS" else [],
    }


def _prov(sha: str, dirty: bool, branch: str = "antigravity/i2b") -> dict:
    return {"sha": sha, "branch": branch, "dirty": dirty, "clean": not dirty}


# ---------------------------------------------------------------------------
# 1. Physical verification alone never certifies a final release
# ---------------------------------------------------------------------------

def test_valid_artifact_without_certification_is_not_final_release():
    gate = evaluate_release_gate(
        certification_evidence=None, packaging_provenance=_prov("c" * 40, dirty=False)
    )
    assert gate.state == "NOT_EVALUATED"
    assert gate.final_release_certified is False
    assert any("physical" in r.lower() for r in gate.reasons)


def test_missing_evidence_file_is_not_evaluated(tmp_path: Path):
    gate = evaluate_release_gate(evidence_path=tmp_path / "nope.json")
    assert gate.state == "NOT_EVALUATED"
    assert gate.final_release_certified is False


def test_malformed_evidence_is_invalid(tmp_path: Path):
    bad = tmp_path / "evidence.json"
    bad.write_text("{not json", encoding="utf-8")
    gate = evaluate_release_gate(evidence_path=bad)
    assert gate.state == "EVIDENCE_INVALID"
    assert gate.final_release_certified is False


# ---------------------------------------------------------------------------
# 2. Verdict gating
# ---------------------------------------------------------------------------

def test_certification_fail_is_not_releasable():
    gate = evaluate_release_gate(
        certification_evidence=_evidence("FAIL", "a" * 40),
        packaging_provenance=_prov("a" * 40, dirty=False),
    )
    assert gate.state == "VERDICT_FAIL"
    assert gate.final_release_certified is False


def test_certification_blocked_is_not_releasable():
    gate = evaluate_release_gate(
        certification_evidence=_evidence("BLOCKED", "a" * 40),
        packaging_provenance=_prov("a" * 40, dirty=False),
    )
    assert gate.state == "VERDICT_BLOCKED"
    assert gate.final_release_certified is False
    assert any("BLOCKED" in r for r in gate.reasons)


def test_certification_pass_with_mismatched_sha_is_blocked():
    gate = evaluate_release_gate(
        certification_evidence=_evidence("PASS", "a" * 40),
        packaging_provenance=_prov("b" * 40, dirty=False),
    )
    assert gate.state == "SHA_MISMATCH"
    assert gate.final_release_certified is False


def test_certification_pass_same_sha_dirty_tree_is_blocked():
    gate = evaluate_release_gate(
        certification_evidence=_evidence("PASS", "a" * 40),
        packaging_provenance=_prov("a" * 40, dirty=True),
    )
    assert gate.state == "DIRTY_TREE"
    assert gate.final_release_certified is False
    assert any("dirty" in r.lower() for r in gate.reasons)


def test_certification_pass_same_sha_clean_tree_is_eligible():
    gate = evaluate_release_gate(
        certification_evidence=_evidence("PASS", "a" * 40),
        packaging_provenance=_prov("a" * 40, dirty=False),
    )
    assert gate.state == "ELIGIBLE"
    assert gate.final_release_certified is True


# ---------------------------------------------------------------------------
# 3. Evidence consumption (OpenCode gate output format)
# ---------------------------------------------------------------------------

def test_load_certification_evidence_from_real_gate_file():
    """The real gate file (currently BLOCKED) must load and be honored."""
    if not DEFAULT_EVIDENCE_PATH.exists():
        pytest.skip("no certification result file on this tree")
    evidence = load_certification_evidence()
    assert evidence["overall_verdict"] in ("PASS", "FAIL", "BLOCKED")


def test_load_certification_evidence_rejects_unknown_verdict(tmp_path: Path):
    odd = tmp_path / "evidence.json"
    odd.write_text(json.dumps({"overall_verdict": "MAYBE"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown certification verdict"):
        load_certification_evidence(odd)


def test_current_real_evidence_cannot_certify():
    """If the live evidence is BLOCKED/FAIL (or absent), pipeline cannot certify."""
    gate = evaluate_release_gate(evidence_path=DEFAULT_EVIDENCE_PATH)
    assert gate.final_release_certified is False


# ---------------------------------------------------------------------------
# 4. Manifest provenance via the real pipeline (reuse mode)
# ---------------------------------------------------------------------------

def test_pipeline_manifest_records_provenance_and_gate(tmp_path: Path):
    result = run_distribution_pipeline(
        SOFTWARE_ROOT,
        version=LIVE_VERSION,
        publish_target=tmp_path / "out",
        run_builds=False,
    )
    assert result.success is True
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))

    prov = manifest["provenance"]
    for key in (
        "source_git_sha",
        "source_branch",
        "dirty_tree",
        "build_timestamp_utc",
        "application_version",
        "certification_verdict",
        "certification_sha",
        "release_gate_state",
        "final_release_certified",
        "physical_artifacts",
    ):
        assert key in prov, f"manifest provenance missing {key}"

    assert len(prov["physical_artifacts"]) == 2
    for art in prov["physical_artifacts"]:
        assert len(art["sha256"]) == 64
        assert art["size_bytes"] > 0

    # The tree is dirty during test runs and the live gate evidence is not a
    # final PASS on this SHA: the manifest must disclose both honestly.
    assert prov["dirty_tree"] is True
    assert manifest["release_status"] == "NOT_FINAL_RELEASE_CERTIFIED"
    assert manifest["certification_status"] == "BLOCKED"
    assert manifest["release_type"] == "FUNCTIONALLY_VERIFIED_BUILD"


def test_pipeline_rejects_fake_certification(tmp_path: Path):
    """A forged PASS evidence with a foreign SHA must never certify."""
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(_evidence("PASS", "d" * 40)), encoding="utf-8")
    result = run_distribution_pipeline(
        SOFTWARE_ROOT,
        version=LIVE_VERSION,
        publish_target=tmp_path / "out2",
        run_builds=False,
        evidence_path=forged,
    )
    assert result.final_release_certified is False
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["certification_status"] == "BLOCKED"
    assert manifest["provenance"]["release_gate_state"] == "SHA_MISMATCH"


# ---------------------------------------------------------------------------
# 5. Hygiene: historical artifacts isolated from the active release set
# ---------------------------------------------------------------------------

def test_dist_root_contains_only_active_release_set():
    # Hygiene guarantee: every binary in the dist root is declared by some
    # release record - no unrecorded package may sit in the active set. The
    # frozen historical records are themselves part of that evidence, and the
    # live release set must be declared by the live manifest.
    manifests = sorted(DIST_DIR.glob("release_manifest_v*.json"))
    assert manifests, "at least one release manifest must exist"
    declared: set[str] = set()
    for manifest_path in manifests:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared |= {a["package_name"] for a in doc.get("artifacts", [])}
    allowed = declared | {p.name for p in manifests} | {
        "SHA256SUMS.txt", "windows-runtime-smoke-result.json", "archive",
    }
    present = {p.name for p in DIST_DIR.iterdir()}
    assert present <= allowed, f"unrecorded files in active dist: {present - allowed}"

    live_manifest = DIST_DIR / f"release_manifest_v{LIVE_VERSION}.json"
    assert live_manifest.is_file(), "the live release manifest must be present"
    live = json.loads(live_manifest.read_text(encoding="utf-8"))
    live_artifacts = {a["package_name"] for a in live.get("artifacts", [])}
    assert live_artifacts <= present, (
        f"live release set incomplete: {live_artifacts - present}"
    )

    archive = DIST_DIR / "archive"
    if archive.exists():
        assert (archive / "historical").is_dir() and any(
            (archive / "historical").iterdir()
        ), "historical evidence must be preserved"


def test_installer_lookup_is_version_pinned():
    """Old-version installers in the bundle dir must never be relabeled."""
    bundle = SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "target" / "release" / "bundle"
    chosen = find_latest_installer(bundle, version=LIVE_VERSION)
    assert chosen is not None
    # H2A.2: the Tauri bundle name is "AMCA Studio_<version>_x64-setup.exe".
    assert chosen.name == f"AMCA Studio_{LIVE_VERSION}_x64-setup.exe"
    # A different version must never be returned for a pinned lookup.
    assert find_latest_installer(bundle, version="0.9.0-nonexistent") is None


def test_build_cache_no_longer_tracked_in_git():
    """desktop-ui/src-tauri/target/ must be untracked (I2B hygiene)."""
    import subprocess

    proc = subprocess.run(
        ["git", "ls-files", "desktop-ui/src-tauri/target/"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.stdout.strip() == "", (
        "generated build cache must not be tracked; "
        f"got {len(proc.stdout.splitlines())} tracked files"
    )
