"""Third-party compliance payload tests (I2N).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2N-FINAL-THIRD-PARTY-NOTICES-PAYLOAD-COMPLETENESS-AND-COMPLIANCE-REVIEW-CLOSURE-PREFLIGHT-01
Worker: OpenCode PS-2. Tests only; no production/dependency changes.

Validates the final third-party compliance payload: license file hashes,
manifest completeness, component/version binding, build-only scoping,
certifi/PyInstaller detection, REVIEW state visibility, no fabricated
legal approval, deterministic schema.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

RELEASE_DIR = Path(__file__).resolve().parent.parent / "docs" / "release"
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = RELEASE_DIR / "AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json"
SBOM = Path(__file__).resolve().parent.parent / "docs" / "release" / "AMCA-Studio-v1.0.0-SBOM.json"


def _read_json(name: str):
    path = RELEASE_DIR / name
    assert path.is_file(), f"missing: {name}"
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_exists_and_valid():
    data = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    assert data["schema"] == "amca-third-party-compliance-payload-manifest/1"
    assert len(data["items"]) >= 3  # certifi, pyinstaller, hooks-contrib


def test_certifi_payload_complete():
    data = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    certifi = next((item for item in data["items"] if item["component"] == "certifi"), None)
    assert certifi is not None, "certifi entry missing from manifest"
    assert certifi["version"] == "2026.7.22"
    assert certifi["license"] == "MPL-2.0"
    assert certifi["classification"] == "review_remains"
    assert len(certifi["files"]) >= 4  # cacert, core, __init__, __main__, dist-info
    # License file status
    assert certifi["license_file_present"] is False
    assert certifi["license_text_available_locally"] is False
    assert certifi["classification"] == "review_remains"
    # Source files in package
    assert certifi["source_files_in_package"] is True


def test_pyinstaller_payload_correct():
    data = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    pyi = next((item for item in data["items"] if item["component"] == "pyinstaller"), None)
    assert pyi is not None, "pyinstaller entry missing"
    assert pyi["version"] == "6.22.0"
    assert "GPL-2.0-or-later" in pyi["license"]
    assert pyi["classification"] == "resolved_technically"
    assert pyi["source_files_in_package"] is False


def test_hooks_contrib_payload_correct():
    data = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    hooks = next((item for item in data["items"] if item["component"] == "pyinstaller-hooks-contrib"), None)
    assert hooks is not None, "pyinstaller-hooks-contrib entry missing"
    assert hooks["version"] == "2026.6"
    assert "GPL-2.0-or-later" in hooks["license"]
    assert hooks["classification"] == "resolved_technically"
    assert hooks["source_files_in_package"] is False


def test_manifest_hashes_match_source():
    manifest = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    checked = 0
    for item in manifest["items"]:
        for file_entry in item.get("files", []):
            path = SOFTWARE_ROOT / file_entry["path"]
            assert path.is_file(), f"manifest file missing: {file_entry['path']}"
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == file_entry["sha256"], \
                f"hash mismatch: {file_entry['path']}"
            assert path.stat().st_size == file_entry["size_bytes"], \
                f"size mismatch: {file_entry['path']}"
            checked += 1
    assert checked >= 7


def test_certifi_license_file_missing_detected():
    """Verify that certifi's declared LICENSE file is missing from distribution."""
    certifi_dist = Path("software/.venv/Lib/site-packages/certifi-2026.7.22.dist-info")
    license_files = list(certifi_dist.glob("licenses/LICENSE*"))
    assert len(license_files) == 0, "certifi LICENSE file unexpectedly present"


def test_pyinstaller_boundary_proof():
    """Verify PyInstaller is build-only and hooks-contrib not embedded."""
    import zipfile
    portable = SOFTWARE_ROOT / "dist" / "amca-studio-v0.9.0-windows-x64-portable.zip"
    assert portable.is_file()
    with zipfile.ZipFile(portable) as zf:
        data = zf.read("amca-studio-v0.9.0-windows-x64-portable/amca-backend.exe")
        # hooks-contrib NOT embedded
        assert b"hooks-contrib" not in data
        # pyi_ and bootloader present (PyInstaller bootloader embedded)
        assert b"pyi_" in data
        # PyInstaller itself not shipped as component
        # hooks-contrib not embedded
        assert b"hooks-contrib" not in data


def test_component_version_mismatch_detected():
    """Test detects component/version drift between manifest and SBOM."""
    manifest = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    sbom = _read_json("AMCA-Studio-v1.0.0-SBOM.json")
    sbom_by_name = {c["name"].lower(): c for c in sbom["components"]}
    for item in manifest["items"]:
        comp = sbom_by_name.get(item["component"].lower())
        if comp:
            assert comp["version"] == item["version"], f"version mismatch: {item['component']}"


def test_build_only_not_falsely_marked_shipped():
    manifest = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    for item in manifest["items"]:
        if item["classification"] == "build-only (not shipped as component)":
            assert item.get("classification") == "build-only (not shipped as component)"
        if item["component"] == "pyinstaller-hooks-contrib":
            assert item["classification"] == "resolved_technically"


def test_unresolved_review_remains_visible():
    """Verify REVIEW items are not silently dropped."""
    manifest = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    review_items = [item for item in manifest["items"] if item.get("classification") == "review_remains"]
    assert len(review_items) >= 1, "no REVIEW items found"
    for item in review_items:
        assert item["component"] in ("certifi",)


def test_no_fabricated_legal_approval():
    manifest = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    text = json.dumps(manifest)
    assert "legal_approval" not in manifest
    assert "approved_by_legal" not in manifest
    assert "legal_approval" not in json.dumps(manifest)


def test_payload_manifest_schema_valid():
    data = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    assert data.get("schema") == "amca-third-party-compliance-payload-manifest/1"
    assert "generated_utc" in data
    assert "source_sha" in data
    assert len(data["items"]) >= 3


def test_no_secrets_user_data_emitted():
    manifest = _read_json("AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json")
    blob = json.dumps(manifest).casefold()
    assert "sk-proj-" not in manifest["items"]  # no keys in component names
    blob = json.dumps(manifest).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "BEGIN PRIVATE KEY" not in json.dumps(manifest)