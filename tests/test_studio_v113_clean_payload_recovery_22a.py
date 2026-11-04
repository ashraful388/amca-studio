"""22A regression guards: clean payload custody recovery for AMCA Studio v1.1.3.

Work order ZCODE-AMCA-V113-CLEAN-PAYLOAD-RECOVERY-RELEASE-22A (base 6d311709).

Guards the structural fix for the v1.1.2 custody failure:
- one immutable canonical release-payload snapshot per release build; the
  portable distribution consumes exactly the snapshot bytes, never mutable
  build-tree files;
- cross-format payload identity is marker-aware: the backend must be
  byte-identical; the desktop binary may differ only by the documented
  Tauri bundle-type marker (tauri-utils platform.rs __TAURI_BUNDLE_TYPE,
  "modified by binary patching during build": UNK/MSI/NSS);
- the notices generator emits the actual supplied release version instead
  of a historical hardcoded title.
"""

import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

from amca.distribution_build import run_distribution_pipeline

_SOFTWARE_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, _SOFTWARE_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_custody = _load("release_payload_custody_22a",
                 "packaging/release_payload_custody.py")
BUNDLE_MARKER_PREFIX = _custody.BUNDLE_MARKER_PREFIX
BACKEND_NAME = _custody.BACKEND_NAME
DESKTOP_NAME = _custody.DESKTOP_NAME
SNAPSHOT_MANIFEST_NAME = _custody.SNAPSHOT_MANIFEST_NAME
compare_payload = _custody.compare_payload
find_bundle_marker = _custody.find_bundle_marker
verify_installer_custody = _custody.verify_installer_custody

VERSION = "1.1.3"


def _exe(seed: bytes = b"payload", size: int = 3 * 1024 * 1024) -> bytes:
    # incompressible content so staged artifacts clear the physical size
    # floors regardless of zip/deflate compression
    return os.urandom(size)


def _patch_marker(blob: bytes, value: bytes) -> bytes:
    idx = blob.find(BUNDLE_MARKER_PREFIX + b"UNK")
    assert idx != -1
    return blob[:idx] + BUNDLE_MARKER_PREFIX + value + blob[idx + len(BUNDLE_MARKER_PREFIX) + 3:]


# ---------------------------------------------------------------------------
# Bundle-type marker helpers
# ---------------------------------------------------------------------------

def test_find_bundle_marker_locates_value_offset():
    blob = _exe(b"x")[:100] + BUNDLE_MARKER_PREFIX + b"UNK" + _exe(b"y")[:100]
    off = find_bundle_marker(blob)
    assert off is not None
    assert blob[off:off + 3] == b"UNK"


def test_marker_aware_equality_identical_bytes():
    b = _exe(b"same")
    res = compare_payload(b, b)
    assert res.equal is True and res.acceptable


def test_marker_aware_equality_allows_documented_marker_variants():
    raw = _exe(b"payload")[:0x2000] + BUNDLE_MARKER_PREFIX + b"UNK" + _exe(b"payload")[0x2000:]
    for variant in (b"MSI", b"NSS"):
        patched = _patch_marker(raw, variant)
        res = compare_payload(raw, patched)
        assert res.equal is False
        assert res.marker_only_diff is True
        assert res.acceptable is True, res.reason
        assert "bundle-type marker" in res.reason


def test_marker_aware_equality_rejects_undocumented_marker_value():
    raw = _exe(b"payload")[:0x2000] + BUNDLE_MARKER_PREFIX + b"UNK" + _exe(b"payload")[0x2000:]
    patched = _patch_marker(raw, b"XXX")
    res = compare_payload(raw, patched)
    assert res.acceptable is False


def test_marker_aware_equality_rejects_real_payload_difference():
    raw = _exe(b"payload")
    other = _exe(b"payloaD")
    assert raw != other
    res = compare_payload(raw, other)
    assert res.acceptable is False
    assert res.marker_only_diff is False


def test_marker_aware_equality_rejects_size_mismatch():
    res = compare_payload(_exe(b"a"), _exe(b"a", size=1024))
    assert res.acceptable is False


# ---------------------------------------------------------------------------
# Custody precheck
# ---------------------------------------------------------------------------

def _make_snapshot(tmp_path: Path, backend: bytes, desktop: bytes) -> Path:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / BACKEND_NAME).write_bytes(backend)
    (snap / DESKTOP_NAME).write_bytes(desktop)
    (snap / SNAPSHOT_MANIFEST_NAME).write_text(json.dumps({
        "schema": "amca-release-payload-snapshot/1",
        "payload": {
            BACKEND_NAME: {"sha256": "x", "size_bytes": len(backend)},
            DESKTOP_NAME: {"sha256": "y", "size_bytes": len(desktop)},
        },
    }), encoding="utf-8")
    return snap


def test_custody_precheck_passes_exact_backend_and_marker_desktop(tmp_path):
    base_desktop = _exe(b"desktop")
    raw_desktop = base_desktop[:0x2000] + BUNDLE_MARKER_PREFIX + b"UNK" + base_desktop[0x2000:]
    backend = _exe(b"backend")
    snap = _make_snapshot(tmp_path, backend, _patch_marker(raw_desktop, b"MSI"))
    dist = tmp_path / "backend-dist"
    dist.mkdir()
    (dist / BACKEND_NAME).write_bytes(backend)
    (dist / DESKTOP_NAME).write_bytes(raw_desktop)
    report = verify_installer_custody(snap, dist / BACKEND_NAME, dist / DESKTOP_NAME)
    assert report["verdict"] == "PASS", report


def test_custody_precheck_fails_on_backend_divergence(tmp_path):
    snap = _make_snapshot(tmp_path, _exe(b"backend-A"), _exe(b"desktop"))
    dist = tmp_path / "backend-dist"
    dist.mkdir()
    (dist / BACKEND_NAME).write_bytes(_exe(b"backend-B"))
    report = verify_installer_custody(snap, dist / BACKEND_NAME)
    assert report["verdict"] == "FAIL"
    assert report["action"] == "STOP before official collection"


# ---------------------------------------------------------------------------
# Portable consumes the immutable canonical snapshot
# ---------------------------------------------------------------------------

def _release_tree(tmp_path: Path) -> Path:
    root = tmp_path / "software"
    backend = root / "backend-dist"
    backend.mkdir(parents=True)
    (backend / BACKEND_NAME).write_bytes(_exe(b"build-tree-backend"))
    release = root / "desktop-ui" / "src-tauri" / "target" / "release"
    release.mkdir(parents=True)
    (release / "amca-desktop.exe").write_bytes(_exe(b"build-tree-desktop"))
    nsis = release / "bundle" / "nsis"
    nsis.mkdir(parents=True)
    (nsis / f"AMCA Studio_{VERSION}_x64-setup.exe").write_bytes(
        b"MZ" + _exe(b"nsis", 5 * 1024 * 1024))
    (root / "desktop-ui" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"productName": "AMCA Studio",
                    "identifier": "app.amca.studio.local"}),
        encoding="utf-8")
    assets = root / "docs" / "release" / "assets"
    assets.mkdir(parents=True)
    (assets / "MPL-2.0-certifi-2026.7.22.txt").write_text("MPL notice",
                                                          encoding="utf-8")
    return root


def test_pipeline_portable_consumes_snapshot_bytes(tmp_path):
    root = _release_tree(tmp_path)
    canonical_backend = _exe(b"CANONICAL-BACKEND")
    canonical_desktop = _exe(b"canonical-desktop")
    snap = _make_snapshot(tmp_path, canonical_backend, canonical_desktop)
    result = run_distribution_pipeline(
        root,
        version=VERSION,
        publish_target=tmp_path / "published",
        run_builds=False,
        payload_snapshot=snap,
    )
    assert result.success is True
    assert any("canonical snapshot" in d for d in result.details)
    portable = Path(result.portable.source_path)
    with zipfile.ZipFile(portable) as z:
        inner_backend = z.read(
            f"amca-studio-v{VERSION}-windows-x64-portable/{BACKEND_NAME}")
        inner_desktop = z.read(
            f"amca-studio-v{VERSION}-windows-x64-portable/amca-desktop.exe")
    assert inner_backend == canonical_backend
    assert inner_desktop == canonical_desktop


def test_pipeline_rejects_snapshot_without_manifest(tmp_path):
    root = _release_tree(tmp_path)
    snap = tmp_path / "snapshot"
    snap.mkdir()
    (snap / BACKEND_NAME).write_bytes(_exe(b"backend"))
    (snap / DESKTOP_NAME).write_bytes(_exe(b"desktop"))
    from amca.distribution_build import DistributionBuildError
    import pytest
    with pytest.raises(DistributionBuildError, match="payload-snapshot.json"):
        run_distribution_pipeline(
            root, version=VERSION, publish_target=tmp_path / "published",
            run_builds=False, payload_snapshot=snap)


# ---------------------------------------------------------------------------
# Notices title parameterization (22A phase 2)
# ---------------------------------------------------------------------------

def _load_audit_module():
    return _load("run_dependency_compliance_audit_22a",
                 "scripts/run_dependency_compliance_audit.py")


def test_notices_title_uses_supplied_release_version(tmp_path):
    mod = _load_audit_module()
    result = {"components": [{
        "name": "certifi", "version": "2026.7.22", "ecosystem": "python",
        "license": "MPL-2.0", "license_source": "dist-info License",
        "source_ref": "packaging/requirements.txt",
        "in_distributed_artifact": True,
        "classification": "runtime",
    }]}
    out = tmp_path / "THIRD-PARTY-NOTICES.txt"
    mod.emit_notices(result, out, release_version="1.1.3")
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# AMCA Studio v1.1.3 — Third-Party Notices"
    assert "(Candidate)" not in text
    assert "certifi" in text and "MPL-2.0" in text


def test_notices_title_version_is_version_bound_not_hardcoded(tmp_path):
    mod = _load_audit_module()
    result = {"components": []}
    out = tmp_path / "n.txt"
    mod.emit_notices(result, out, release_version="9.9.9")
    assert out.read_text(encoding="utf-8").splitlines()[0] == \
        "# AMCA Studio v9.9.9 — Third-Party Notices"
