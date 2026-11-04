"""21A regression guards: release-tooling recovery for AMCA Studio v1.1.2.

Work order ZCODE-AMCA-V112-END-TO-END-RECOVERY-BUILD-21A (base 9672e1fb).

Defect A (the v1.1.1 DIRTY_TREE failure mode): the release pipeline's own
generated publication outputs under software/dist/ must not retroactively
poison the packaging-dirty determination when it consumes canonical
certification evidence. The exemption is scoped to the certification
evaluation path only — real source/test/config/packaging mutations, other
tracked dist files, and hand-authored dist files must still block.

Defect B (the v1.1.1 MISSING_SBOM / MISSING_NOTICES failure modes): the
release manifest must naturally declare physically existing SBOM and
third-party notices files handed to run_distribution_pipeline.
"""

import json
import os
import subprocess
from pathlib import Path

from amca.distribution_build import run_distribution_pipeline
from amca.release_gating import (
    current_git_provenance,
    evaluate_release_gate,
    publication_scoped_git_provenance,
)

VERSION = "1.1.2"


def _evidence(verdict: str, sha: str) -> dict:
    return {
        "overall_verdict": verdict,
        "provenance": {
            "sha": sha,
            "branch": "zcode/v112-end-to-end-recovery-21a",
            "clean": True,
            "dirty": False,
        },
        "blocked_reasons": [],
    }


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "-c", "user.name=amca-21a-test",
         "-c", "user.email=21a@test.local", "commit", "-m", message)


def _init_repo(tmp_path: Path):
    """A minimal AMCA-shaped git repo with a frozen source commit."""
    repo = tmp_path / "repo"
    software = repo / "software"
    (software / "src" / "amca").mkdir(parents=True)
    (software / "dist").mkdir(parents=True)
    # mirror the repository's build-output ignore rules
    (repo / ".gitignore").write_text(
        "backend-dist/\ndesktop-ui/src-tauri/target/\n.venv/\n", encoding="utf-8"
    )
    (software / "src" / "amca" / "release_gating.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (software / "dist" / "SHA256SUMS.txt").write_text(
        "0" * 64 + "  seed.bin\n", encoding="utf-8"
    )
    # a second tracked dist file: the exact tracked exemption set must stay
    # limited to the checksum list the pipeline regenerates every run
    (software / "dist" / "release_manifest_v1.0.1.json").write_text(
        "{}\n", encoding="utf-8"
    )
    _git(repo, "init")
    _git(repo, "add", ".")
    _commit(repo, "frozen source")
    return repo, software


def _release_tree(tmp_path: Path):
    """Frozen repo plus a committed reuse-mode toolchain output set."""
    repo, software = _init_repo(tmp_path)
    backend = software / "backend-dist"
    backend.mkdir(exist_ok=True)
    (backend / "amca-backend.exe").write_bytes(os.urandom(3 * 1024 * 1024))
    release = software / "desktop-ui" / "src-tauri" / "target" / "release"
    release.mkdir(parents=True, exist_ok=True)
    (release / "amca-desktop.exe").write_bytes(os.urandom(3 * 1024 * 1024))
    nsis = release / "bundle" / "nsis"
    nsis.mkdir(parents=True)
    (nsis / f"AMCA Studio_{VERSION}_x64-setup.exe").write_bytes(
        b"MZ" + os.urandom(5 * 1024 * 1024)
    )
    (software / "desktop-ui" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"productName": "AMCA Studio",
                    "identifier": "app.amca.studio.local"}),
        encoding="utf-8",
    )
    assets = software / "docs" / "release" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "MPL-2.0-certifi-2026.7.22.txt").write_text(
        "MPL-2.0 certifi notice", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _commit(repo, "toolchain metadata")
    return repo, software


# ---------------------------------------------------------------------------
# Defect A: publication-scoped packaging provenance
# ---------------------------------------------------------------------------

def test_generated_publication_outputs_do_not_dirty_scoped_provenance(tmp_path):
    repo, software = _init_repo(tmp_path)
    # exactly what the pipeline itself leaves behind after a run
    (software / "dist" / "SHA256SUMS.txt").write_text(
        "1" * 64 + "  a.exe\n", encoding="utf-8"
    )
    (software / "dist" / f"release_manifest_v{VERSION}.json").write_text(
        "{}", encoding="utf-8"
    )
    (software / "dist" / f"amca-studio-v{VERSION}-windows-x64-setup.exe"
     ).write_bytes(b"MZ")
    (software / "dist" / "sbom.json").write_text("{}", encoding="utf-8")
    (software / "dist" / "THIRD-PARTY-NOTICES.txt").write_text(
        "notice", encoding="utf-8"
    )

    scoped = publication_scoped_git_provenance(software)
    strict = current_git_provenance(software)

    assert scoped["dirty"] is False
    assert scoped["clean"] is True
    # the exemption is scoped, not a global ignore: the unscoped
    # determination still calls the same tree dirty — the exact v1.1.1
    # DIRTY_TREE poisoning vector
    assert strict["dirty"] is True


def test_hand_authored_dist_file_still_blocks_scoped_provenance(tmp_path):
    repo, software = _init_repo(tmp_path)
    (software / "dist" / "KNOWN-LIMITATIONS.md").write_text(
        "hand authored", encoding="utf-8"
    )
    scoped = publication_scoped_git_provenance(software)
    assert scoped["dirty"] is True


def test_non_checksum_tracked_dist_modification_still_blocks(tmp_path):
    repo, software = _init_repo(tmp_path)
    (software / "dist" / "release_manifest_v1.0.1.json").write_text(
        '{"tampered": true}', encoding="utf-8"
    )
    scoped = publication_scoped_git_provenance(software)
    assert scoped["dirty"] is True


def test_real_source_and_outside_dist_changes_still_block(tmp_path):
    repo, software = _init_repo(tmp_path)
    (software / "src" / "amca" / "release_gating.py").write_text(
        "x = 2\n", encoding="utf-8"
    )
    assert publication_scoped_git_provenance(software)["dirty"] is True

    (repo / "stray-notes.txt").write_text("rogue", encoding="utf-8")
    assert publication_scoped_git_provenance(software)["dirty"] is True


def test_scoped_gate_certifies_publication_only_dirty_tree(tmp_path):
    repo, software = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    (software / "dist" / "SHA256SUMS.txt").write_text(
        "2" * 64 + "  b.exe\n", encoding="utf-8"
    )
    (software / "dist" / f"release_manifest_v{VERSION}.json").write_text(
        "{}", encoding="utf-8"
    )
    gate = evaluate_release_gate(
        certification_evidence=_evidence("PASS", head),
        software_root=software,
        scoped_publication_provenance=True,
    )
    assert gate.state == "ELIGIBLE"
    assert gate.final_release_certified is True


def test_default_gate_still_blocks_same_tree_via_dirty_tree(tmp_path):
    """Without the explicit scoping opt-in, v1.1.1 gate semantics hold."""
    repo, software = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    (software / "dist" / "SHA256SUMS.txt").write_text(
        "3" * 64 + "  c.exe\n", encoding="utf-8"
    )
    gate = evaluate_release_gate(
        certification_evidence=_evidence("PASS", head),
        software_root=software,
    )
    assert gate.state == "DIRTY_TREE"
    assert gate.final_release_certified is False


def test_scoped_gate_blocks_real_source_dirty_tree(tmp_path):
    repo, software = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    (software / "dist" / "SHA256SUMS.txt").write_text(
        "4" * 64 + "  d.exe\n", encoding="utf-8"
    )
    (software / "src" / "amca" / "release_gating.py").write_text(
        "x = 3\n", encoding="utf-8"
    )
    gate = evaluate_release_gate(
        certification_evidence=_evidence("PASS", head),
        software_root=software,
        scoped_publication_provenance=True,
    )
    assert gate.state == "DIRTY_TREE"
    assert gate.final_release_certified is False


# ---------------------------------------------------------------------------
# Defect B: SBOM / notices manifest binding
# ---------------------------------------------------------------------------

def test_pipeline_end_to_end_certifies_and_binds_sbom_notices(tmp_path):
    """The v1.1.1 failure mode is gone: publication-only dirtiness plus
    existing SBOM/notices material certifies and declares naturally."""
    repo, software = _release_tree(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    dist = software / "dist"
    sbom = dist / f"AMCA-Studio-v{VERSION}-sbom.json"
    sbom.write_text(json.dumps({"components": []}), encoding="utf-8")
    notices = dist / "THIRD-PARTY-NOTICES.txt"
    notices.write_text("MPL-2.0 certifi coverage", encoding="utf-8")

    result = run_distribution_pipeline(
        software,
        version=VERSION,
        publish_target=tmp_path / "published",
        run_builds=False,
        certification_evidence=_evidence("PASS", head),
        sbom_path=sbom,
        notices_path=notices,
    )
    assert result.success is True
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))

    # Defect A: the pipeline's own dist outputs no longer poison the gate
    assert manifest["provenance"]["dirty_tree"] is False
    assert manifest["provenance"]["release_gate_state"] == "ELIGIBLE"
    assert manifest["certification_status"] == "CERTIFIED_FOR_FINAL_RELEASE"
    assert manifest["release_status"] == "ELIGIBLE_FOR_FINAL_RELEASE"
    assert manifest["provenance"]["certification_sha"] == head

    # Defect B: declared files exist physically in dist
    assert manifest["sbom"] == sbom.name
    assert manifest["notices"] == notices.name
    assert (dist / manifest["sbom"]).is_file()
    assert (dist / manifest["notices"]).is_file()


def test_pipeline_omits_declarations_when_material_absent(tmp_path):
    repo, software = _release_tree(tmp_path)
    dist = software / "dist"
    result = run_distribution_pipeline(
        software,
        version=VERSION,
        publish_target=tmp_path / "published",
        run_builds=False,
        sbom_path=dist / "absent-sbom.json",
        notices_path=None,
    )
    assert result.success is True
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert "sbom" not in manifest
    assert "notices" not in manifest
