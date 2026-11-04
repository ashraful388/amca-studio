"""Final user-documentation bundle consistency tests (I2H).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2H-FINAL-USER-DOCUMENTATION-BUNDLE-AND-CAPABILITY-BOUNDARY-FREEZE-01
Worker: OpenCode PS-2. Tests only; no production-code changes.

Validates the frozen user-facing release documents: no final-release
certification claims before final promotion, Linux honesty, no autonomous/
telemetry overclaims, upgrade guidance consistent with I2G, matrix/limitations
agreement, no embedded secrets, consistent version terminology, installer/
portable instructions present, v0.9.0 upgrade terminology intact, and a
documentation manifest whose hashes match the files on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent.parent / "docs" / "release"

GUIDES = {
    "install": "AMCA-Studio-v1.0.0-Installation-and-First-Launch-Guide.md",
    "limitations": "AMCA-Studio-v1.0.0-Known-Limitations.md",
    "matrix": "AMCA-Studio-v1.0.0-User-Capability-Matrix.md",
    "troubleshooting": "AMCA-Studio-v1.0.0-Troubleshooting-Guide.md",
    "upgrade": "AMCA-Studio-v1.0.0-Upgrade-and-Recovery-Guide.md",
    "contract": "AMCA-Studio-v1.0.0-Migration-Certification-Evidence-Contract.md",
}
MANIFEST_NAME = "AMCA-Studio-v1.0.0-Documentation-Manifest.json"


def _read(name: str) -> str:
    path = RELEASE_DIR / name
    assert path.is_file(), f"release document missing: {name}"
    return path.read_text(encoding="utf-8")


def _flat(name: str) -> str:
    return " ".join(_read(name).replace("*", "").split()).casefold()


def test_all_bundle_documents_exist():
    for key, name in GUIDES.items():
        assert (RELEASE_DIR / name).is_file(), f"missing bundle doc: {key}"


def test_no_final_release_certification_claim():
    for key in ("install", "limitations", "matrix", "troubleshooting", "upgrade"):
        text = _flat(GUIDES[key])
        for forbidden in (
            "final release certified",
            "shipping build certified",
            "certified for final release",
            "final promotion complete",
            "generally available",
        ):
            assert forbidden not in text, f"{key} claims finality: {forbidden!r}"


def test_linux_not_falsely_supported():
    matrix = _flat(GUIDES["matrix"])
    assert "linux" in matrix
    assert "not included in v1.0.0" in matrix
    limitations = _flat(GUIDES["limitations"])
    assert "linux" in limitations
    for key in ("install", "troubleshooting", "upgrade"):
        assert "linux certified" not in _flat(GUIDES[key])


def test_no_unsupported_autonomous_behavior_claims():
    for key in ("limitations", "matrix"):
        text = _flat(GUIDES[key])
        assert "never silently substituted" in text or "no ranking" in text or \
            "explicit" in text
    for key in GUIDES:
        text = _flat(GUIDES[key])
        for forbidden in (
            "fully autonomous",
            "agent swarm deployed",
            "marketplace available",
            "plugin store available",
        ):
            assert forbidden not in text, f"{key} overclaims: {forbidden!r}"


def test_no_contradicted_telemetry_claims():
    for key in ("limitations", "install", "troubleshooting"):
        text = _flat(GUIDES[key])
        assert "no telemetry" in text or "zero telemetry" in text or \
            "never uploaded" in text or "no cloud" in text
    for key in GUIDES:
        text = _flat(GUIDES[key])
        assert "telemetry uploaded" not in text
        assert "automatic cloud telemetry" not in text or "no automatic" in text


def test_upgrade_guidance_consistent_with_i2g():
    upgrade = _flat(GUIDES["upgrade"])
    install = _flat(GUIDES["install"])
    troubleshooting = _flat(GUIDES["troubleshooting"])
    for shared in ("non-destructive", "pre-c1", "backup", "workspace"):
        assert shared in upgrade, f"upgrade guide missing: {shared}"
        assert shared in install or shared in troubleshooting, \
            f"bundle inconsistent on: {shared}"
    assert "target_exists_overwrite_forbidden" in troubleshooting or \
        "refused by default" in troubleshooting


def test_capability_matrix_and_limitations_agree():
    matrix = _flat(GUIDES["matrix"])
    limitations = _flat(GUIDES["limitations"])
    for term in ("linux", "marketplace", "telemetry", "downgrade"):
        assert term in matrix, f"matrix silent on: {term}"
        assert term in limitations, f"limitations silent on: {term}"
    assert "not included in v1.0.0" in matrix
    assert "future" in matrix


def test_no_secrets_tokens_api_keys_embedded():
    for key, name in GUIDES.items():
        text = _read(name)
        assert "BEGIN PRIVATE KEY" not in text, f"{key} leaks a key"
        lowered = text.casefold()
        assert "sk-proj-" not in lowered, f"{key} leaks a secret"
        assert "ghp_" not in lowered, f"{key} leaks a secret"
        assert "xoxb-" not in lowered, f"{key} leaks a secret"


def test_version_terminology_consistent():
    for key in ("install", "limitations", "matrix", "troubleshooting", "upgrade"):
        text = _read(GUIDES[key])
        assert "v1.0.0" in text, f"{key} missing version terminology"
        assert "v0.9.0" in text or key == "install", \
            f"{key} missing historical v0.9.0 terminology"


def test_installer_portable_instructions_present():
    install = _flat(GUIDES["install"])
    for item in ("setup.exe", "portable", "/s ", "sha-256", "uninstall"):
        assert item in install, f"install guide missing: {item}"
    troubleshooting = _flat(GUIDES["troubleshooting"])
    assert "installer" in troubleshooting
    assert "portable" in troubleshooting


def test_historical_upgrade_terminology_intact():
    upgrade = _flat(GUIDES["upgrade"])
    assert "v0.9.0" in upgrade
    assert "pre-c1" in upgrade
    matrix = _flat(GUIDES["matrix"])
    assert "v0.9.0" in matrix or "v0.9" in matrix


def test_documentation_manifest_hashes_match():
    manifest_path = RELEASE_DIR / MANIFEST_NAME
    assert manifest_path.is_file(), "documentation manifest missing"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("schema") == "amca-release-documentation-manifest/1"
    assert len(manifest.get("documents", [])) >= 6
    for entry in manifest["documents"]:
        path = RELEASE_DIR / entry["document"]
        assert path.is_file(), f"manifested doc missing: {entry['document']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], \
            f"manifest hash mismatch (stale manifest?): {entry['document']}"
    assert len(manifest.get("pending", [])) >= 1
    for entry in manifest["pending"]:
        assert "status" in entry
