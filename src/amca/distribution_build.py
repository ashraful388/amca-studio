"""AMCA Studio v1.0.0 Functional Distribution Build Pipeline (I2A).

Milestone: AG-AMCA-STUDIO-V1.0.0-I2A-FUNCTIONAL-DISTRIBUTION-BUILD-PIPELINE-AND-STUB-ELIMINATION-01

Replaces the simulated distribution descriptors that previously fabricated
SHA256 checksums from name/version strings and invented file sizes. Every
artifact recorded here is a physical file on disk whose SHA256 checksum and
size are computed from its actual bytes.

Responsibilities:
1. Real Distribution Build Entry Point:
   - Builds the Windows NSIS installer and portable zip from the real Tauri
     and PyInstaller toolchains (build_desktop.ps1 / build_backend.ps1).
2. Physical Artifact Verification:
   - Rejects fake/minimal packages by structural inspection before checksums
     are recorded (MZ header + size floor for installers, non-empty content
     inventory for archives).
   - Computes SHA256 checksums and sizes from physical bytes only.
3. Manifest Generation:
   - Emits software/dist/release_manifest_v<version>.json plus SHA256SUMS.txt
     in the coreutils checksum-file format.
4. Artifact Publication:
   - Copies verified artifacts into a target distribution directory
     (default: D:/ChatGPT/AMCA/software/dist).

Platform honesty: on Windows this pipeline produces the Windows artifacts and
skips the Linux tarball with an explicit BLOCKED reason. It never fabricates a
Linux artifact from a machine that cannot build one.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Installers below this size are treated as stub/minimal fakes. The smallest
#: real historical NSIS installer for AMCA is ~12.5 MB; a real installer always
#: carries the Tauri webview bootstrap plus the backend executable (~21 MB).
MIN_INSTALLER_BYTES = 5_000_000

#: Portable archives below this size cannot carry the two real executables
#: (backend ~21 MB, desktop ~10 MB) and are therefore stubs.
MIN_PORTABLE_BYTES = 5_000_000

WINDOWS_DIST_TARGET = Path(
    "D:/ChatGPT/AMCA/software/dist"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute the SHA256 hex digest of a physical file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactDescriptor:
    """A distribution artifact verified against its physical file."""

    package_name: str
    target_platform: str
    package_type: str  # "installer" | "portable_archive"
    version: str
    sha256_checksum: str
    file_size_bytes: int
    source_path: str
    release_notes_ref: str = "software/docs/release/AMCA-Studio-v0.9.0-Release-Notes.md"

    def to_manifest_dict(self) -> Dict[str, Any]:
        return {
            "package_name": self.package_name,
            "target_platform": self.target_platform,
            "package_type": self.package_type,
            "version": self.version,
            "sha256": self.sha256_checksum,
            "size_bytes": self.file_size_bytes,
        }


@dataclass
class BuildPipelineResult:
    """Outcome of a full distribution build pipeline run."""

    version: str
    platform_name: str
    installer: Optional[ArtifactDescriptor] = None
    msi: Optional[ArtifactDescriptor] = None
    portable: Optional[ArtifactDescriptor] = None
    skipped: List[Dict[str, str]] = field(default_factory=list)
    rejections: List[str] = field(default_factory=list)
    manifest_path: Optional[str] = None
    checksums_path: Optional[str] = None
    published_to: Optional[str] = None
    success: bool = False
    release_gate_state: Optional[str] = None
    final_release_certified: bool = False
    details: List[str] = field(default_factory=list)


class DistributionBuildError(RuntimeError):
    """Raised when the real build toolchain fails or produces a fake artifact."""


class StubArtifactRejected(DistributionBuildError):
    """Raised when a candidate artifact fails the fake/minimal-package check."""


def inspect_archive_entries(path: Path) -> List[str]:
    """Return entry names for zip/tar.gz archives; empty list for non-archives."""
    name = path.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                return z.namelist()
        if name.endswith((".tar.gz", ".tgz")):
            import tarfile

            with tarfile.open(path) as t:
                return t.getnames()
    except Exception:
        return []
    return []


def reject_fake_or_minimal_package(path: Path, package_type: str) -> None:
    """Structurally reject fake/minimal packages before any checksum is recorded.

    Raises StubArtifactRejected with the physical evidence when the file is a
    stub; returns silently when the artifact looks shippable.
    """
    size = path.stat().st_size

    if package_type == "installer":
        if not path.name.lower().endswith(".exe"):
            raise StubArtifactRejected(f"{path.name}: installer must be an .exe")
        with path.open("rb") as f:
            magic = f.read(2)
        if magic != b"MZ":
            raise StubArtifactRejected(f"{path.name}: not a Windows PE executable (magic={magic!r})")
        if size < MIN_INSTALLER_BYTES:
            raise StubArtifactRejected(
                f"{path.name}: installer is {size} bytes, below the {MIN_INSTALLER_BYTES}-byte "
                "floor for a real AMCA installer (stub/minimal package rejected)"
            )
        return

    if package_type == "windows-msi":
        # H2A S8 (role-aware authenticity): an MSI is an OLE compound file
        # whose magic is D0 CF 11 E0. A renamed EXE carrying .msi fails.
        if not path.name.lower().endswith(".msi"):
            raise StubArtifactRejected(f"{path.name}: MSI artifact must be an .msi")
        with path.open("rb") as f:
            magic = f.read(8)
        ole_magic = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
        if magic != ole_magic:
            raise StubArtifactRejected(
                f"{path.name}: not an OLE compound file (MSI) — magic={magic!r}; "
                "a renamed EXE or fabricated file is rejected"
            )
        if size < MIN_INSTALLER_BYTES:
            raise StubArtifactRejected(
                f"{path.name}: MSI is {size} bytes, below the {MIN_INSTALLER_BYTES}-byte "
                "floor for a real AMCA installer (stub/minimal package rejected)"
            )
        return

    if package_type == "portable_archive":
        entries = inspect_archive_entries(path)
        if not entries:
            raise StubArtifactRejected(f"{path.name}: archive unreadable or empty")
        executables = [e for e in entries if e.lower().endswith(".exe")]
        if not executables:
            raise StubArtifactRejected(
                f"{path.name}: portable archive contains no executables "
                f"({len(entries)} entries: text-only stub detected)"
            )
        if size < MIN_PORTABLE_BYTES:
            raise StubArtifactRejected(
                f"{path.name}: portable archive is {size} bytes, below the {MIN_PORTABLE_BYTES}-byte "
                "floor for a real AMCA portable package (stub/minimal package rejected)"
            )
        return

    raise StubArtifactRejected(f"{path.name}: unknown package_type {package_type!r}")


def build_physical_descriptor(
    source: Path,
    package_name: str,
    target_platform: str,
    package_type: str,
    version: str,
) -> ArtifactDescriptor:
    """Verify a physical artifact and compute its checksum from real bytes."""
    if not source.is_file():
        raise DistributionBuildError(f"Physical artifact not found: {source}")
    reject_fake_or_minimal_package(source, package_type)
    return ArtifactDescriptor(
        package_name=package_name,
        target_platform=target_platform,
        package_type=package_type,
        version=version,
        sha256_checksum=sha256_file(source),
        file_size_bytes=source.stat().st_size,
        source_path=str(source),
    )


def find_latest_installer(bundle_dir: Path, version: Optional[str] = None) -> Optional[Path]:
    """Return the newest NSIS setup exe produced by the Tauri build.

    When version is given, only an installer whose Tauri bundle name embeds
    that exact version is accepted — relabeling a different-version binary is
    treated as a fake package and never done.
    """
    nsis_dir = bundle_dir / "nsis"
    if not nsis_dir.is_dir():
        return None
    if version is not None:
        # H2A.2: productName is now "AMCA Studio", so Tauri names the NSIS
        # bundle "AMCA Studio_<version>_x64-setup.exe".
        exact = nsis_dir / f"AMCA Studio_{version}_x64-setup.exe"
        if exact.is_file():
            return exact
        return None
    candidates = sorted(
        (p for p in nsis_dir.glob("*-setup.exe") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def msi_expected_name(version: str) -> str:
    return f"AMCA Studio_{version}_x64_en-US.msi"


def find_latest_msi(bundle_dir: Path, version: Optional[str] = None) -> Optional[Path]:
    """Locate the newest Tauri-produced MSI in bundle_dir/msi.

    Only a file whose name carries the requested version is accepted; a
    different-version binary is never relabeled.
    """

    msi_dir = bundle_dir / "msi"
    if not msi_dir.is_dir():
        return None
    candidates = sorted(
        (item for item in msi_dir.glob("*.msi") if item.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for item in candidates:
        if version is None or version in item.name:
            return item
    return None


def build_portable_zip(
    backend_exe: Path,
    desktop_exe: Path,
    output_zip: Path,
    product_dir_name: str,
    extra_files: Optional[Dict[str, Path]] = None,
) -> Path:
    """Package the two real executables into the portable zip layout.

    extra_files maps arcnames inside the product directory to source paths
    (H2C.2: ships THIRD-PARTY-NOTICES so the portable artifact carries the
    canonical MPL-2.0 notice, matching the installer/MSI resources).
    """
    if not backend_exe.is_file():
        raise DistributionBuildError(f"Backend executable missing: {backend_exe}")
    if not desktop_exe.is_file():
        raise DistributionBuildError(f"Desktop executable missing: {desktop_exe}")

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(backend_exe, arcname=f"{product_dir_name}/amca-backend.exe")
        z.write(desktop_exe, arcname=f"{product_dir_name}/amca-desktop.exe")
        for arcname, src in (extra_files or {}).items():
            if not src.is_file():
                raise DistributionBuildError(f"Extra package file missing: {src}")
            z.write(src, arcname=f"{product_dir_name}/{arcname}")
    return output_zip


def run_real_builds(
    software_root: Path,
    *,
    version: str = "1.0.0",
    skip_installer: bool = False,
) -> Dict[str, Optional[Path]]:
    """Run the real PyInstaller + Tauri build scripts.

    Returns paths for 'backend_exe', 'desktop_exe', 'installer' (any of which
    may be None when intentionally skipped).
    """
    scripts = software_root / "packaging"
    result: Dict[str, Optional[Path]] = {
        "backend_exe": None,
        "desktop_exe": None,
        "installer": None,
    }

    backend_dist = software_root / "backend-dist" / "amca-backend.exe"
    desktop_exe = (
        software_root / "desktop-ui" / "src-tauri" / "target" / "release" / "amca-desktop.exe"
    )
    bundle_dir = (
        software_root / "desktop-ui" / "src-tauri" / "target" / "release" / "bundle"
    )

    if sys.platform != "win32":
        raise DistributionBuildError(
            "Real Windows packaging requires Windows; this environment must "
            "report PASS/BLOCKED honestly instead of fabricating artifacts."
        )

    # 1. Backend executable via PyInstaller — always rebuilt from current
    # source so the packaged backend matches the tree being released.
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(scripts / "build_backend.ps1")],
        cwd=str(software_root),
    )
    if proc.returncode != 0:
        raise DistributionBuildError(f"build_backend.ps1 failed with exit code {proc.returncode}")
    if not backend_dist.is_file():
        raise DistributionBuildError(f"Backend executable missing after build: {backend_dist}")
    result["backend_exe"] = backend_dist

    # 2. Installer + desktop exe via the real Tauri pipeline.
    if not skip_installer:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(scripts / "build_desktop.ps1"), "-Bundle", "both"],
            cwd=str(software_root),
        )
        if proc.returncode != 0:
            raise DistributionBuildError(f"build_desktop.ps1 failed with exit code {proc.returncode}")

    if desktop_exe.is_file():
        result["desktop_exe"] = desktop_exe
    if not skip_installer:
        installer = find_latest_installer(bundle_dir, version=version)
        if installer is None:
            raise DistributionBuildError(
                f"Tauri build produced no NSIS setup exe for version {version} "
                f"(expected AMCA_{version}_x64-setup.exe in {bundle_dir / 'nsis'}); "
                "a different-version binary is never relabeled."
            )
        result["installer"] = installer
        # H2A S1: the v1.0.1 pipeline also produces the MSI bundle
        msi = find_latest_msi(bundle_dir, version=version)
        if msi is None:
            raise DistributionBuildError(
                f"Tauri build produced no MSI for version {version} "
                f"(expected {msi_expected_name(version)} in {bundle_dir / 'msi'}); "
                "a different-version binary is never relabeled."
            )
        result["msi"] = msi

    return result


def build_product_identity_block(*, version: str, software_root: Path) -> Dict[str, str]:
    """Bind the exact product identity into the release manifest (H2B.2 Part C).

    productName/identifier are read from the actual tauri.conf.json that
    produced the binaries (single source of truth), the desktop binary name
    from the src-tauri Cargo package, and the backend binary from the bundler
    resource mapping. final_promotion's product_identity gate enforces these
    exact values against the canonical v1.0.1 identity contract, so the
    manifest records reality instead of aspirational constants.
    """
    conf = json.loads(
        (software_root / "desktop-ui" / "src-tauri" / "tauri.conf.json")
        .read_text(encoding="utf-8")
    )
    cargo_name = "amca-desktop"
    cargo = software_root / "desktop-ui" / "src-tauri" / "Cargo.toml"
    if cargo.is_file():
        for line in cargo.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("name =") and "amca-desktop" in stripped:
                cargo_name = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return {
        "product_name": str(conf.get("productName", "")),
        "version": str(version),
        "identifier": str(conf.get("identifier", "")),
        "desktop_binary": f"{cargo_name}.exe",
        "backend_binary": "amca-backend.exe",
    }


def run_distribution_pipeline(
    software_root: Optional[Path] = None,
    *,
    version: str = "1.0.0",
    publish_target: Optional[Path] = WINDOWS_DIST_TARGET,
    skip_installer: bool = False,
    run_builds: bool = True,
    certification_evidence: Optional[Dict[str, Any]] = None,
    evidence_path: Optional[Path] = None,
    sbom_path: Optional[Path] = None,
    notices_path: Optional[Path] = None,
    payload_snapshot: Optional[Path] = None,
) -> BuildPipelineResult:
    """Execute the full I2A/I2B functional distribution build pipeline.

    run_builds=False reuses existing toolchain outputs (used by tests and by
    re-verification runs); artifact verification and checksum computation are
    always physical.

    I2B: certification_evidence (canonical certification JSON) or evidence_path
    supplies the release-gate input. When neither is given the manifest is
    produced with release_status NOT_FINAL_RELEASE_CERTIFIED — physical
    verification alone can never emit CERTIFIED_FOR_FINAL_RELEASE.

    payload_snapshot (22A): directory of an immutable canonical release-
    payload snapshot (amca-backend.exe, amca-desktop.exe,
    payload-snapshot.json as produced by packaging/release_payload_custody.py).
    When supplied in reuse mode, the portable archive is built from exactly
    these locked bytes instead of whatever mutable files happen to remain in
    backend-dist/ or desktop-ui/src-tauri/target/release/.
    """
    root = software_root or Path(__file__).resolve().parents[2]
    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    result = BuildPipelineResult(
        version=version,
        platform_name=f"{platform.system().lower()}-{platform.machine().lower()}",
    )
    result.details.append(f"Pipeline start {_utc_now()} on {result.platform_name}")

    installer_src: Optional[Path] = None
    msi_src: Optional[Path] = None
    if run_builds:
        built = run_real_builds(root, version=version, skip_installer=skip_installer)
        if built.get("backend_exe"):
            result.details.append(f"Backend executable: {built['backend_exe']}")
        if built.get("installer"):
            installer_src = built["installer"]
            result.details.append(f"NSIS installer: {installer_src}")
        if built.get("msi"):
            msi_src = built["msi"]
            result.details.append(f"MSI installer: {msi_src}")
    else:
        # Reuse mode: locate existing toolchain outputs without rebuilding.
        backend = root / "backend-dist" / "amca-backend.exe"
        desktop = (
            root / "desktop-ui" / "src-tauri" / "target" / "release" / "amca-desktop.exe"
        )
        if not backend.is_file() or not desktop.is_file():
            raise DistributionBuildError(
                "Reuse mode requires existing backend-dist/amca-backend.exe and "
                "desktop-ui/src-tauri/target/release/amca-desktop.exe"
            )
        result.details.append("Reuse mode: existing toolchain outputs located")
        if payload_snapshot is not None:
            # 22A: the portable archive must consume the immutable canonical
            # payload snapshot, never mutable build-tree files.
            snapshot_dir = Path(payload_snapshot)
            snap_backend = snapshot_dir / "amca-backend.exe"
            snap_desktop = snapshot_dir / "amca-desktop.exe"
            if not snap_backend.is_file() or not snap_desktop.is_file():
                raise DistributionBuildError(
                    "payload_snapshot must contain amca-backend.exe and "
                    "amca-desktop.exe (22A canonical payload snapshot)")
            if not (snapshot_dir / "payload-snapshot.json").is_file():
                raise DistributionBuildError(
                    "payload_snapshot is missing payload-snapshot.json; "
                    "refusing to consume an unverified payload (22A)")
            backend = snap_backend
            desktop = snap_desktop
            result.details.append(
                "Portable payload: canonical snapshot consumed "
                "(22A immutable release-payload snapshot)")
        installer_src = find_latest_installer(
            root / "desktop-ui" / "src-tauri" / "target" / "release" / "bundle",
            version=version,
        )
        msi_src = find_latest_msi(
            root / "desktop-ui" / "src-tauri" / "target" / "release" / "bundle",
            version=version,
        )

    # --- Installer (Windows only; never fabricated on other platforms) ---
    if sys.platform == "win32" and installer_src is not None:
        installer_name = f"amca-studio-v{version}-windows-x64-setup.exe"
        try:
            installer_stage = dist_dir / installer_name
            shutil.copy2(installer_src, installer_stage)
            result.installer = build_physical_descriptor(
                installer_stage, installer_name, "win32-x64", "installer", version
            )
            result.details.append(
                f"Installer verified physically: {installer_name} "
                f"({result.installer.file_size_bytes} bytes)"
            )
        except StubArtifactRejected as exc:
            result.rejections.append(str(exc))
            raise
        # H2A S1: the MSI installer is a first-class v1.0.1 artifact.
        if msi_src is not None:
            msi_name = f"amca-studio-v{version}-windows-x64.msi"
            msi_stage = dist_dir / msi_name
            shutil.copy2(msi_src, msi_stage)
            result.msi = build_physical_descriptor(
                msi_stage, msi_name, "win32-x64", "windows-msi", version
            )
            result.details.append(
                f"MSI verified physically: {msi_name} "
                f"({result.msi.file_size_bytes} bytes)"
            )
        else:
            result.skipped.append({
                "artifact": f"amca-studio-v{version}-windows-x64.msi",
                "reason": (
                    "Windows MSI installer requires a Windows build environment; "
                    "artifact intentionally not fabricated on this platform."
                ),
            })
    else:
        result.skipped.append({
            "artifact": f"amca-studio-v{version}-windows-x64-setup.exe",
            "reason": (
                "Windows NSIS installer requires a Windows build environment; "
                "artifact intentionally not fabricated on this platform."
            ),
        })
        result.skipped.append({
            "artifact": f"amca-studio-v{version}-windows-x64.msi",
            "reason": (
                "Windows MSI installer requires a Windows build environment; "
                "artifact intentionally not fabricated on this platform."
            ),
        })

    # --- Portable zip (Windows only; never fabricated on other platforms) ---
    if sys.platform == "win32":
        if payload_snapshot is not None:
            # 22A: portable payload comes from the immutable canonical
            # snapshot (set in reuse mode above); never from the mutable
            # build tree.
            pass
        else:
            backend = root / "backend-dist" / "amca-backend.exe"
            desktop = (
                root / "desktop-ui" / "src-tauri" / "target" / "release" / "amca-desktop.exe"
            )
        portable_name = f"amca-studio-v{version}-windows-x64-portable.zip"
        portable_stage = dist_dir / portable_name
        build_portable_zip(backend, desktop, portable_stage, portable_name[:-4],
                           extra_files={
                               # H2C.2: ship the canonical MPL-2.0 notice so
                               # the portable artifact matches installer/MSI
                               "THIRD-PARTY-NOTICES/MPL-2.0-certifi-2026.7.22.txt":
                                   root / "docs" / "release" / "assets"
                                   / "MPL-2.0-certifi-2026.7.22.txt",
                           })
        result.portable = build_physical_descriptor(
            portable_stage, portable_name, "win32-x64", "portable_archive", version
        )
        result.details.append(
            f"Portable archive built and verified physically: {portable_name} "
            f"({result.portable.file_size_bytes} bytes, "
            f"{len(inspect_archive_entries(portable_stage))} entries)"
        )
    else:
        result.skipped.append({
            "artifact": f"amca-studio-v{version}-windows-x64-portable.zip",
            "reason": (
                "Windows portable package requires Windows executables; "
                "artifact intentionally not fabricated on this platform."
            ),
        })

    # --- Linux tarball: honest skip on a Windows host ---
    if sys.platform != "linux":
        result.skipped.append({
            "artifact": f"amca-studio-v{version}-linux-x64.tar.gz",
            "reason": (
                "BLOCKED: Linux binaries cannot be produced on this host; "
                "the previous fake 160-byte version.txt tarball was eliminated. "
                "Linux builds must run on a Linux environment and will be "
                "verified physically there."
            ),
        })

    # --- Manifest + SHA256SUMS from physical descriptors only ---
    artifacts = [a for a in (result.installer, result.msi, result.portable) if a is not None]
    if not artifacts:
        raise DistributionBuildError(
            "No artifacts verified on this platform; refusing to emit a manifest."
        )

    # I2B: release gating. Physical verification never self-certifies a final
    # release; final certification requires canonical certification evidence
    # (same-SHA + clean tree + verdict PASS) consumed from OpenCode's gate.
    # 21A Defect A: the pipeline consumes that evidence with publication-
    # scoped packaging provenance — its own generated outputs under dist/
    # (SHA256SUMS.txt, release manifest, staged artifacts, SBOM, notices) do
    # not retroactively invalidate an otherwise clean certification; every
    # other tracked or untracked change still blocks via DIRTY_TREE.
    from amca.release_gating import build_provenance_record, evaluate_release_gate

    gate = evaluate_release_gate(
        certification_evidence=certification_evidence,
        evidence_path=evidence_path,
        software_root=root,
        scoped_publication_provenance=True,
    )
    result.release_gate_state = gate.state
    result.final_release_certified = gate.final_release_certified
    result.details.append(
        f"Release gate: {gate.state} ({'; '.join(gate.reasons)})"
    )

    # --- Physical verification pass: descriptors vs bytes staged on disk ---
    for art in artifacts:
        staged = dist_dir / art.package_name
        if sha256_file(staged) != art.sha256_checksum:
            raise DistributionBuildError(
                f"Physical verification failed for {art.package_name}: "
                "descriptor checksum does not match staged bytes"
            )
    result.success = True

    manifest = {
        "product_name": "AMCA Studio",
        "product_identity": build_product_identity_block(version=version, software_root=root),
        "version": version,
        "release_type": "FUNCTIONALLY_VERIFIED_BUILD",
        "release_status": (
            "ELIGIBLE_FOR_FINAL_RELEASE"
            if gate.final_release_certified
            else "NOT_FINAL_RELEASE_CERTIFIED"
        ),
        "build_date": _utc_now()[:10],
        "pipeline": "ZCODE-AMCA-STUDIO-V1.0.1-H2A-RC-CONVERGENCE-AND-THREE-ARTIFACT-IMPLEMENTATION-01 (I2A lineage)",
        "verification": "physical_sha256_from_bytes",
        "platform": result.platform_name,
        "license": "Apache-2.0",
        "governance_contract": "LOCAL_ONLY_FIRST_HUMAN_GOVERNED_EXECUTION",
        "documentation_included": [
            "software/docs/release/AMCA-Studio-v0.9.0-Release-Notes.md",
            "software/docs/release/AMCA-Studio-v0.9.0-Release-Certification-Report.md",
            "software/docs/release/AMCA-Studio-v0.9.0-RC1-Certification-Report.md",
            "software/docs/release/AMCA-Studio-v0.9.0-Final-Release-Report.md",
            "software/docs/release/AMCA-Studio-v0.9.0-GA-Release-Certification-Report.md",
        ],
        "artifacts": [a.to_manifest_dict() for a in artifacts],
        "skipped_artifacts": result.skipped,
        "provenance": build_provenance_record(
            version=version,
            gate=gate,
            artifacts=[a.to_manifest_dict() for a in artifacts],
            skipped=result.skipped,
        ),
    }
    manifest["certification_status"] = (
        "CERTIFIED_FOR_FINAL_RELEASE" if gate.final_release_certified else "BLOCKED"
    )
    # 21A Defect B: the release manifest naturally binds the generated SBOM
    # and third-party-notices files so the physical compliance gate verifies
    # them as declared release components (gate requirement: truthy manifest
    # declaration + physical file present in dist).
    if sbom_path is not None and Path(sbom_path).is_file():
        manifest["sbom"] = Path(sbom_path).name
    if notices_path is not None and Path(notices_path).is_file():
        manifest["notices"] = Path(notices_path).name
    manifest_path = dist_dir / f"release_manifest_v{version}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result.manifest_path = str(manifest_path)

    sums_path = dist_dir / "SHA256SUMS.txt"
    lines = [
        f"{a.sha256_checksum}  {a.package_name}" for a in artifacts
    ]
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result.checksums_path = str(sums_path)
    result.details.append(f"Manifest written: {manifest_path.name}")
    result.details.append(f"SHA256SUMS written: {sums_path.name}")

    # --- Post-write re-verification: manifest hashes vs bytes on disk ---
    for art in artifacts:
        on_disk = dist_dir / art.package_name
        if sha256_file(on_disk) != art.sha256_checksum:
            result.success = False
            raise DistributionBuildError(
                f"Post-write verification failed for {art.package_name}: "
                "manifest checksum does not match physical bytes"
            )
    result.details.append("Post-write physical re-verification passed")

    # --- Publication to the manager-designated distribution directory ---
    if publish_target is not None:
        publish_target.mkdir(parents=True, exist_ok=True)
        for art in artifacts:
            shutil.copy2(dist_dir / art.package_name, publish_target / art.package_name)
        shutil.copy2(manifest_path, publish_target / manifest_path.name)
        shutil.copy2(sums_path, publish_target / sums_path.name)
        result.published_to = str(publish_target)
        result.details.append(f"Artifacts published to {publish_target}")

    return result
