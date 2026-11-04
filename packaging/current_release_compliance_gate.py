"""Current-release compliance gate (20BH).

Tests/tooling only. A release-identity-parameterized compliance gate for the
v1.1.1 final package, preserving the substantive requirements of the immutable
v1.0.0 historical compliance suite (which is NOT modified):

- same-SHA artifact acceptance (declared checksums must match physical bytes);
- tampered/modified artifact rejection (checksum mismatch fails closed);
- missing artifact / missing SBOM / missing notices rejection;
- incomplete compliance payload detection;
- certifi without notice material fails;
- PyInstaller never classified as a shipped dependency;
- REVIEW entries survive; BLOCKING findings can never become PASS;
- no secrets or file contents emitted in the report (names/hashes only);
- fail closed on wrong versions, checksum mismatch, and unclassified
  required artifact types.

Missing real official artifacts are NEVER reported as PASS: the gate requires
every required artifact to exist and verify inside the inspected dist
directory. With synthetic fixtures the same semantics are proven without
requiring real v1.1.1 artifacts. The gate is parameterized by release
identity (version + dist directory) and accepts an explicit future target
(e.g. 1.1.1) without changing current source files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Required artifact identities, derived from repository packaging conventions
# (software/src/amca/release_packaging.py specs + release_manifest precedent).
REQUIRED_ARTIFACT_TYPES = (
    "installer",
    "windows-msi",
    "portable_archive",
    "release_manifest",
    "checksums",
)

ARTIFACT_NAMING = {
    "installer": "amca-studio-v{version}-windows-x64-setup.exe",
    "windows-msi": "amca-studio-v{version}-windows-x64.msi",
    "portable_archive": "amca-studio-v{version}-windows-x64-portable.zip",
    "release_manifest": "release_manifest_v{version}.json",
    "checksums": "SHA256SUMS.txt",
}

# Payload entries the release manifest must declare for the gate to pass.
REQUIRED_MANIFEST_TOP_LEVEL = ("product_name", "version", "verification")


@dataclass
class ReleaseIdentity:
    """Explicit release target; nothing is inferred from current source.

    `required_artifact_types` may be extended by a caller to add new
    contract types; any required type without a registered naming rule
    fails the gate closed (UNCLASSIFIED_REQUIRED_ARTIFACT_TYPE) so a
    required type can never silently disappear from the release contract.
    """

    version: str
    dist_dir: Path
    platform: str = "windows-x64"
    notes: List[str] = field(default_factory=list)
    required_artifact_types: tuple = REQUIRED_ARTIFACT_TYPES


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _finding(code: str, detail: str, blocking: bool = True) -> Dict:
    return {"code": code, "detail": detail, "blocking": blocking}


def run_compliance_gate(identity: ReleaseIdentity) -> Dict:
    """Execute the compliance gate for an explicit release identity.

    Returns a structured report. Verdict is PASS only if zero BLOCKING
    findings exist; every failure mode fails closed.
    """
    findings: List[Dict] = []
    dist = Path(identity.dist_dir)
    unclassified_required: List[str] = []

    # --- artifact presence and naming (wrong version = fail closed) --------
    physical: Dict[str, Path] = {}
    for artifact_type in identity.required_artifact_types:
        if artifact_type not in ARTIFACT_NAMING:
            # A required type with no registered naming rule fails closed:
            # it cannot silently disappear from the release contract.
            unclassified_required.append(artifact_type)
            findings.append(_finding(
                "UNCLASSIFIED_REQUIRED_ARTIFACT_TYPE",
                f"required artifact type {artifact_type!r} has no "
                "registered naming rule; refusing to treat the release as "
                "evidenced",
            ))
            continue
        expected_name = ARTIFACT_NAMING[artifact_type].format(
            version=identity.version
        )
        path = dist / expected_name
        if not path.is_file():
            findings.append(_finding(
                "MISSING_ARTIFACT",
                f"required {artifact_type} not found: {expected_name}",
            ))
        else:
            physical[artifact_type] = path

    # --- manifest content ---------------------------------------------------
    manifest: Optional[dict] = None
    if "release_manifest" in physical:
        try:
            manifest = json.loads(
                physical["release_manifest"].read_text(encoding="utf-8")
            )
        except Exception as exc:
            findings.append(_finding(
                "MANIFEST_UNPARSEABLE",
                f"release manifest is not valid JSON: {type(exc).__name__}",
            ))
        else:
            if manifest.get("version") != identity.version:
                findings.append(_finding(
                    "WRONG_VERSION",
                    f"manifest version {manifest.get('version')!r} != "
                    f"expected {identity.version!r}",
                ))
            for key in REQUIRED_MANIFEST_TOP_LEVEL:
                if key not in manifest:
                    findings.append(_finding(
                        "INCOMPLETE_COMPLIANCE_PAYLOAD",
                        f"manifest missing required field: {key}",
                    ))
            if manifest.get("verification") != "physical_sha256_from_bytes":
                findings.append(_finding(
                    "INCOMPLETE_COMPLIANCE_PAYLOAD",
                    "manifest verification must be physical_sha256_from_bytes",
                ))

    # --- checksums: same-SHA acceptance / tamper rejection ------------------
    sums: Dict[str, str] = {}
    if "checksums" in physical:
        for line in physical["checksums"].read_text(
            encoding="utf-8"
        ).splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                sums[parts[1].strip().lstrip("*")] = parts[0].lower()

    # Metadata containers can't carry a self-referential hash; every
    # payload artifact must be declared and must match physical bytes.
    metadata_types = ("release_manifest", "checksums")
    for artifact_type, path in sorted(physical.items()):
        actual = _sha256(path)
        declared = sums.get(path.name)
        if declared is None and artifact_type not in metadata_types:
            findings.append(_finding(
                "CHECKSUM_NOT_DECLARED",
                f"{path.name} has no SHA256SUMS entry",
            ))
        elif declared is not None and declared != actual:
            findings.append(_finding(
                "CHECKSUM_MISMATCH",
                f"{path.name} declared {declared[:12]} but physical bytes "
                f"hash to {actual[:12]} (tampered or stale artifact)",
            ))
        if manifest and artifact_type not in metadata_types:
            for entry in manifest.get("artifacts", []):
                if entry.get("name") == path.name and entry.get(
                    "sha256"
                ) not in (None, actual):
                    findings.append(_finding(
                        "SAME_SHA_REJECTED",
                        f"{path.name} manifest sha256 != physical bytes",
                    ))

    # --- SBOM and notices ----------------------------------------------------
    if manifest is not None:
        sbom = manifest.get("sbom")
        if not sbom or not (dist / Path(str(sbom)).name).is_file():
            findings.append(_finding(
                "MISSING_SBOM",
                "SBOM missing from manifest or dist",
            ))
        notices = manifest.get("notices") or []
        notice_names = {
            Path(str(n)).name
            for n in (notices if isinstance(notices, list) else [notices])
        }
        if not notice_names or not any(
            (dist / name).is_file() for name in notice_names
        ):
            findings.append(_finding(
                "MISSING_NOTICES",
                "third-party notice material missing from manifest or dist",
            ))
        # certifi-without-notice rule: certifi must be covered by actual
        # notice material (declared entry name or notice file content).
        shipped = {
            str(dep.get("name", "")).lower()
            for dep in manifest.get("shipped_dependencies", [])
        }
        if "certifi" in shipped:
            covered = any("certifi" in name.lower() for name in notice_names)
            if not covered:
                for name in notice_names:
                    path = dist / name
                    if path.is_file():
                        try:
                            if "certifi" in path.read_text(
                                encoding="utf-8", errors="ignore"
                            ).lower():
                                covered = True
                                break
                        except OSError:
                            continue
            if not covered:
                findings.append(_finding(
                    "CERTIFI_WITHOUT_NOTICE",
                    "certifi shipped without dedicated notice material",
                ))
        # PyInstaller must never be a shipped dependency
        if "pyinstaller" in shipped:
            findings.append(_finding(
                "PYINSTALLER_CLASSIFIED_SHIPPED",
                "PyInstaller is a build tool and must never be a shipped "
                "dependency",
            ))
        # fabricated legal approval
        if manifest.get("legal_approval") not in (None, False):
            findings.append(_finding(
                "FABRICATED_APPROVAL",
                "manifest must not carry an inline legal-approval claim",
            ))

    # --- REVIEW survival ------------------------------------------------------
    # Every non-blocking finding still appears in the report; blocking
    # findings force FAIL regardless of any REVIEW status.
    blocking = [f for f in findings if f["blocking"]]
    verdict = "FAIL" if blocking else "PASS"

    return {
        "gate": "current_release_compliance_gate_20bh",
        "release_identity": {
            "version": identity.version,
            "dist_dir": str(identity.dist_dir),
            "platform": identity.platform,
        },
        "required_artifact_types": list(identity.required_artifact_types),
        "unclassified_required_types": unclassified_required,
        "verified_artifacts": sorted(
            path.name for path in physical.values()
        ),
        "findings": findings,
        "review_survives": True,
        "blocking_count": len(blocking),
        # BLOCKING findings can never become PASS; verdict is structural.
        "verdict": verdict,
        "note": (
            "report contains artifact names and hashes only; no file "
            "contents or secrets are emitted"
        ),
    }


def main(argv: List[str]) -> int:
    """Deterministic CLI entrypoint.

    Exit codes: 0 = verdict PASS; 1 = verdict FAIL; 2 = unexpected gate
    error (never a swallowed failure). Usage errors exit 2 via argparse.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Current-release compliance gate (parameterized)"
    )
    parser.add_argument("--version", required=True,
                        help="expected release version, e.g. 1.1.1")
    parser.add_argument("--dist-dir", required=True,
                        help="dist directory to inspect")
    args = parser.parse_args(argv)
    try:
        report = run_compliance_gate(
            ReleaseIdentity(version=args.version, dist_dir=Path(args.dist_dir))
        )
    except Exception as exc:  # unexpected gate error: fail loudly
        print(json.dumps({
            "gate": "current_release_compliance_gate_20bh",
            "verdict": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
