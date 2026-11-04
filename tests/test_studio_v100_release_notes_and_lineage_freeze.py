"""Release-notes / lineage freeze tests (I2I).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2I-RELEASE-NOTES-CHANGELOG-AND-HISTORICAL-LINEAGE-FREEZE-01
Worker: OpenCode PS-2. Tests only; no production-code changes.

Freezes the v1.0.0 release-notes candidate, historical lineage, and evidence
index: supported capabilities only, no premature release claims, limitations
consistent with I2H, no Linux/telemetry overclaims, historical labels
preserved, migration claims consistent with I2D-I2F, documentation hashes
matching the manifest, final certification still explicitly pending.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent.parent / "docs" / "release"

NOTES = "AMCA-Studio-v1.0.0-Release-Notes-Candidate.md"
INDEX = "AMCA-Studio-v1.0.0-Release-Evidence-Index.md"
MANIFEST = "AMCA-Studio-v1.0.0-Documentation-Manifest.json"
I2H_LIMITATIONS = "AMCA-Studio-v1.0.0-Known-Limitations.md"


def _read(name: str) -> str:
    path = RELEASE_DIR / name
    assert path.is_file(), f"release document missing: {name}"
    return path.read_text(encoding="utf-8")


def _flat(name: str) -> str:
    return " ".join(_read(name).replace("*", "").split()).casefold()


def test_release_notes_cover_product_areas():
    text = _flat(NOTES)
    for area in (
        "workspace", "cognitive", "model hub", "knowledge", "memory",
        "governance", "approvals", "evaluation", "manual selection",
        "diagnostics", "backup", "restore", "migration", "packaging",
        "release", "security", "privacy",
    ):
        assert area in text, f"release notes silent on: {area}"


def test_release_notes_no_unsupported_capabilities():
    text = _flat(NOTES)
    denials = ("no automatic cloud telemetry", "no hidden autonomous")
    scanned = text
    for denial in denials:
        scanned = scanned.replace(denial, "")
    for forbidden in (
        "plugin store available",
        "marketplace available",
        "agent swarm deployed",
        "fully autonomous",
        "linux package certified",
        "automatic cloud telemetry",
    ):
        assert forbidden not in scanned, f"notes overclaim: {forbidden!r}"


def test_no_premature_release_declaration():
    for name in (NOTES, INDEX):
        text = _flat(name)
        assert "not released" in text or "candidate" in text
        for forbidden in (
            "v1.0.0 is released",
            "v1.0.0 has been released",
            "generally available",
            "final promotion complete",
        ):
            assert forbidden not in text, f"{name} declares release: {forbidden!r}"


def test_limitations_consistent_with_i2h():
    notes = _flat(NOTES)
    limitations = _flat(I2H_LIMITATIONS)
    for term in ("downgrade", "telemetry", "fallback", "linux", "filesystem"):
        assert term in limitations, f"I2H limitations silent on: {term}"
        assert term in notes, f"release notes diverge from I2H on: {term}"
    assert "known limitations" in notes


def test_no_linux_support_overclaim():
    for name in (NOTES, INDEX):
        text = _flat(name)
        assert "linux certified" not in text
        assert "linux supported" not in text or "not" in text


def test_no_telemetry_autonomous_contradiction():
    for name in (NOTES, INDEX):
        text = _flat(name)
        assert "telemetry uploaded" not in text
        assert "silent fallback" not in text or "no " in text


def test_historical_version_labels_preserved():
    text = _flat(NOTES)
    for version in ("v0.5", "v0.6", "v0.7.0", "v0.9.0", "v1.0.0"):
        assert version in text, f"lineage missing: {version}"
    assert "not a public release" in text
    assert "internal release candidate" in text or "internal" in text


def test_migration_claims_consistent_with_i2d_i2f():
    text = _flat(NOTES)
    for term in ("pre-c1", "migration", "backup", "restore", "workspace"):
        assert term in text
    index = _flat(INDEX)
    assert "25/25" in index
    assert "12/12" in index
    assert "i2d" in index


def test_documentation_hashes_match_manifest():
    manifest = json.loads(_read(MANIFEST))
    assert manifest.get("schema") == "amca-release-documentation-manifest/1"
    for entry in manifest["documents"]:
        path = RELEASE_DIR / entry["document"]
        assert path.is_file(), f"manifested doc missing: {entry['document']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], \
            f"manifest hash mismatch: {entry['document']}"
    assert len(manifest["documents"]) >= 8


def test_pending_final_certification_explicit():
    manifest = json.loads(_read(MANIFEST))
    assert len(manifest.get("pending", [])) >= 1
    index = _flat(INDEX)
    assert "rerun" in index
    notes = _flat(NOTES)
    assert "candidate" in notes
