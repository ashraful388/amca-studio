"""Release payload custody: canonical payload snapshot + cross-format identity.

Work order ZCODE-AMCA-V113-CLEAN-PAYLOAD-RECOVERY-RELEASE-22A.

The v1.1.2 custody failure shipped two different backend builds (the
installers embedded the build_desktop.ps1 internal backend while the
portable, assembled later from a rebuilt backend-dist, carried a different
PyInstaller output). 22A makes payload custody structural:

1. One immutable canonical payload snapshot per release build. After the
   single authorized build cycle the MSI payload is extracted read-only
   (msiexec administrative extraction) and locked into the snapshot with
   recorded hashes. The portable distribution must consume exactly these
   bytes (run_distribution_pipeline(payload_snapshot=...)).
2. Cross-format payload identity is a hard release gate BEFORE collection
   and again BEFORE tagging.

Desktop binary identity under Tauri: the bundler patches a documented
3-byte bundle-type marker into the embedded copy of the main binary and
restores the raw copy on disk (tauri-utils platform.rs, __TAURI_BUNDLE_TYPE,
"modified by binary patching during build"; raw value UNK, MSI bundles are
patched to MSI, NSIS bundles to NSS). Desktop payload identity is therefore
exact-byte identity EXCEPT that single documented marker; the backend
(PyInstaller onefile, no marker mechanism) must be exactly identical.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

BUNDLE_MARKER_PREFIX = b"__TAURI_BUNDLE_TYPE_VAR_"
BUNDLE_MARKER_VALUES = (b"UNK", b"MSI", b"NSS")
BACKEND_NAME = "amca-backend.exe"
DESKTOP_NAME = "amca-desktop.exe"
SNAPSHOT_MANIFEST_NAME = "payload-snapshot.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_bundle_marker(data: bytes) -> Optional[int]:
    """Offset of the 3 bundle-type value bytes after the marker prefix.

    Returns None when the prefix does not occur exactly once (the marker
    check is only meaningful for binaries that carry the tauri marker).
    """
    first = data.find(BUNDLE_MARKER_PREFIX)
    if first == -1:
        return None
    if data.find(BUNDLE_MARKER_PREFIX, first + 1) != -1:
        return None
    return first + len(BUNDLE_MARKER_PREFIX)


@dataclass
class PayloadIdentity:
    """Result of comparing two payload copies for release identity."""

    equal: bool
    marker_only_diff: bool
    diff_ranges: List[Dict[str, int]]
    reason: str

    @property
    def acceptable(self) -> bool:
        return self.equal or self.marker_only_diff

    def to_dict(self) -> Dict:
        return {
            "equal": self.equal,
            "marker_only_diff": self.marker_only_diff,
            "acceptable": self.acceptable,
            "diff_ranges": self.diff_ranges,
            "reason": self.reason,
        }


def _diff_ranges(a: bytes, b: bytes) -> List[Dict[str, int]]:
    if len(a) != len(b):
        return [{"start": 0, "end": max(len(a), len(b)) - 1,
                 "reason": "size mismatch"}]
    ranges: List[Dict[str, int]] = []
    start = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            if start is None:
                start = i
        elif start is not None:
            ranges.append({"start": start, "end": i - 1})
            start = None
    if start is not None:
        ranges.append({"start": start, "end": len(a) - 1})
    return ranges


def compare_payload(a: bytes, b: bytes) -> PayloadIdentity:
    """Compare two copies of a distribution payload binary.

    Byte-identical copies are equal. Otherwise the only acceptable
    difference is the documented Tauri bundle-type marker: exactly one
    differing range of up to 3 bytes located immediately after the
    __TAURI_BUNDLE_TYPE_VAR_ prefix in both binaries, with both values in
    the documented set. Any other difference is a custody violation.
    """
    if a == b:
        return PayloadIdentity(True, True, [],
                               "byte-identical")
    if len(a) != len(b):
        return PayloadIdentity(False, False,
                               [{"start": 0, "end": max(len(a), len(b)) - 1}],
                               f"size mismatch: {len(a)} vs {len(b)} bytes")
    ranges = _diff_ranges(a, b)
    marker_a = find_bundle_marker(a)
    marker_b = find_bundle_marker(b)
    if marker_a is None or marker_b is None:
        return PayloadIdentity(
            False, False, ranges,
            "binaries differ and at least one lacks a unique tauri bundle "
            "marker; difference is not the documented bundle-type patch")
    marker_range = {"start": min(marker_a, marker_b),
                    "end": max(marker_a, marker_b) + 2}
    other = [r for r in ranges
             if r["start"] < marker_range["start"]
             or r["end"] > marker_range["end"]]
    if other:
        return PayloadIdentity(
            False, False, ranges,
            f"payload differs outside the documented bundle-type marker at "
            f"{len(other)} range(s); custody violation")
    val_a = a[marker_a:marker_a + 3]
    val_b = b[marker_b:marker_b + 3]
    if (val_a not in BUNDLE_MARKER_VALUES
            or val_b not in BUNDLE_MARKER_VALUES
            or val_a == val_b):
        return PayloadIdentity(
            False, False, ranges,
            f"bundle marker values not documented variants: "
            f"{val_a!r} vs {val_b!r}")
    return PayloadIdentity(
        False, True, [marker_range],
        f"identical except the documented tauri bundle-type marker: "
        f"{val_a.decode()} vs {val_b.decode()}")


def compare_payload_files(path_a: Path, path_b: Path) -> PayloadIdentity:
    return compare_payload(Path(path_a).read_bytes(),
                           Path(path_b).read_bytes())


def extract_msi_payload(msi_path: Path, dest: Path) -> Path:
    """Read-only administrative extraction of an MSI payload to dest.

    msiexec /a unpacks the installer contents without executing or
    registering anything; the release artifact itself is never modified.
    Returns the directory containing the extracted program files.
    """
    import shutil

    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    proc = subprocess.run(
        ["msiexec", "/a", str(msi_path), "/qn",
         f"TARGETDIR={dest}"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"msiexec administrative extraction failed rc={proc.returncode}: "
            f"{proc.stderr[-500:]}")
    programs = None
    for candidate in sorted(dest.rglob(DESKTOP_NAME)):
        if (candidate.parent / BACKEND_NAME).is_file():
            programs = candidate.parent
            break
    if programs is None:
        raise FileNotFoundError(
            f"extracted MSI payload at {dest} does not contain "
            f"{BACKEND_NAME} alongside {DESKTOP_NAME}")
    return programs


def create_payload_snapshot(
    msi_path: Path,
    snapshot_dir: Path,
    source_note: str = "",
) -> Dict:
    """Extract the MSI payload and lock it as the canonical snapshot.

    Returns the snapshot manifest (hashes of every locked file plus the
    source artifact hash). The snapshot directory is the only payload the
    portable distribution may consume.
    """
    snapshot_dir = Path(snapshot_dir)
    programs = extract_msi_payload(msi_path, snapshot_dir / "_extract")
    entries = {}
    for name in (BACKEND_NAME, DESKTOP_NAME):
        src = programs / name
        locked = snapshot_dir / name
        locked.write_bytes(src.read_bytes())
        entries[name] = {
            "sha256": sha256_file(locked),
            "size_bytes": locked.stat().st_size,
        }
    manifest = {
        "schema": "amca-release-payload-snapshot/1",
        "source_msi": str(Path(msi_path).name),
        "source_msi_sha256": sha256_file(Path(msi_path)),
        "source_note": source_note,
        "payload": entries,
    }
    (snapshot_dir / SNAPSHOT_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify_installer_custody(
    snapshot_dir: Path,
    backend_dist_backend: Path,
    backend_dist_desktop: Optional[Path] = None,
) -> Dict:
    """Custody precheck before official collection.

    Verifies that the canonical snapshot payload is the same backend the
    single build cycle left in backend-dist (exact bytes) and the same
    desktop binary (documented bundle-type marker aside). Blocking: any
    non-marker difference.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest = json.loads(
        (snapshot_dir / SNAPSHOT_MANIFEST_NAME).read_text(encoding="utf-8"))
    report: Dict = {
        "custody_gate": "22A-installer-payload-precheck",
        "snapshot_manifest": manifest,
        "checks": [],
        "verdict": "PASS",
    }
    backend_cmp = compare_payload_files(
        snapshot_dir / BACKEND_NAME, Path(backend_dist_backend))
    report["checks"].append({
        "check": "backend exact identity (snapshot vs backend-dist)",
        "result": backend_cmp.to_dict(),
    })
    if not backend_cmp.equal:
        report["verdict"] = "FAIL"
    if backend_dist_desktop is not None and Path(backend_dist_desktop).is_file():
        desktop_cmp = compare_payload_files(
            snapshot_dir / DESKTOP_NAME, Path(backend_dist_desktop))
        report["checks"].append({
            "check": ("desktop identity (snapshot vs on-disk; tauri "
                      "bundle-type marker allowed)"),
            "result": desktop_cmp.to_dict(),
        })
        if not desktop_cmp.acceptable:
            report["verdict"] = "FAIL"
    if report["verdict"] == "FAIL":
        report["action"] = "STOP before official collection"
    return report


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="22A release payload custody tool (fail-closed)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot",
                          help="extract MSI payload and lock the snapshot")
    snap.add_argument("--msi", required=True)
    snap.add_argument("--snapshot-dir", required=True)
    snap.add_argument("--source-note", default="")
    snap.add_argument("--backend-dist", default=None,
                      help="run the custody precheck against backend-dist "
                           "after creating the snapshot")
    snap.add_argument("--desktop", default=None)
    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        manifest = create_payload_snapshot(
            Path(args.msi), Path(args.snapshot_dir), args.source_note)
        print(json.dumps(manifest, indent=2))
        rc = 0
        if args.backend_dist:
            report = verify_installer_custody(
                Path(args.snapshot_dir), Path(args.backend_dist),
                Path(args.desktop) if args.desktop else None)
            print(json.dumps(report, indent=2))
            rc = 0 if report["verdict"] == "PASS" else 1
        return rc
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
