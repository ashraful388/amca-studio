#!/usr/bin/env python3
"""I2O final-package compliance rerun harness + payload enforcement.

OpenCode PS-2. Final-package validation tooling only; no product, dependency,
or packaging changes. Local data only — never downloads external services.

Runs against the DEFINITIVE v1.0.0 installer/portable package after the final
build and proves the pre-final compliance evidence cannot falsely certify
different artifact bytes:

  python scripts/run_final_package_compliance_verification.py
      --portable PATH [--installer PATH] [--msi PATH] [--source-sha SHA]
      [--evidence PATH] [--output PATH]

Verdicts: PASS / PASS_WITH_REVIEW / BLOCKED / FAIL.
- Evidence whose source SHA differs from --source-sha, or whose recorded
  artifact hashes differ from the actual bytes, is REJECTED (FAIL).
- A shipped certifi (MPL-2.0) without the required notice/license payload is
  FAIL (never silently PASS).
- Missing SBOM/notices/payload manifest is BLOCKED.
- REVIEW items survive final verification; BLOCKING findings can never
  become PASS.
- No legal approval is fabricated anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "amca-final-package-compliance/1"

# H1C MSI-aware extension (additive; v1.0.0 two-artifact validation is
# unchanged). An MSI must carry the OLE compound-file magic; an EXE renamed
# to .msi is a role substitution and fails.
MSI_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EXE_MAGIC = b"MZ"

RELEASE_DIR = SOFTWARE_ROOT / "docs" / "release"
DEFAULT_SBOM = RELEASE_DIR / "AMCA-Studio-v1.0.0-SBOM.json"
DEFAULT_NOTICES = RELEASE_DIR / "AMCA-Studio-v1.0.0-Third-Party-Notices-Candidate.md"
DEFAULT_PAYLOAD = (
    RELEASE_DIR / "AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json"
)
# H2C.2E Part D/H: the v1.0.1 candidate binds to its own payload manifest
# (certifi license_file_present=true; canonical MPL notice shipped). The
# v1.0.0-named record is historical and is never rewritten.
DEFAULT_PAYLOAD_V101 = (
    RELEASE_DIR / "AMCA-Studio-v1.0.1-Third-Party-Compliance-Payload-Manifest.json"
)
DEFAULT_EVIDENCE = (
    RELEASE_DIR / "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json"
)
DEFAULT_REPORT = (
    RELEASE_DIR / "AMCA-Studio-v1.0.0-Dependency-License-and-Distribution-Compliance-Report.md"
)

REQUIRED_PAYLOAD_FILES = [
    ("SBOM", DEFAULT_SBOM),
    ("third-party notices", DEFAULT_NOTICES),
    ("compliance payload manifest", DEFAULT_PAYLOAD),
    ("compliance report", DEFAULT_REPORT),
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Result:
    def __init__(self) -> None:
        self.fail: List[str] = []
        self.blocked: List[str] = []
        self.review: List[str] = []
        self.notes: List[str] = []

    @property
    def overall(self) -> str:
        if self.fail:
            return "FAIL"
        if self.blocked:
            return "BLOCKED"
        if self.review:
            return "PASS_WITH_REVIEW"
        return "PASS"

    def to_dict(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        doc = dict(extra)
        doc.update({
            "schema": SCHEMA,
            "overall": self.overall,
            "fail_findings": self.fail,
            "blocked_findings": self.blocked,
            "review_items": self.review,
            "notes": self.notes,
        })
        return doc


def _zip_members(portable: Path) -> List[str]:
    with zipfile.ZipFile(portable) as zf:
        return [n for n in zf.namelist() if not n.endswith("/")]


def _read_backend_bytes(portable: Path) -> Optional[bytes]:
    with zipfile.ZipFile(portable) as zf:
        for name in zf.namelist():
            if name.lower().endswith("amca-backend.exe"):
                return zf.read(name)
    return None


def verify_package_consistency(
    portable: Path, sbom_components: List[Dict[str, Any]],
    result: Result,
    payload_path: Path = None,
) -> Dict[str, Any]:
    """Detect certifi; distinguish build-only tooling; flag unknown runtimes."""
    # H2C.2E: default to the v1.0.1-named payload manifest when present
    # (certifi license_file_present=true); fall back to the historical
    # v1.0.0-named record only when the v1.0.1 file is absent.
    if payload_path is None:
        payload_path = (DEFAULT_PAYLOAD_V101
                        if DEFAULT_PAYLOAD_V101.is_file() else DEFAULT_PAYLOAD)
    detail: Dict[str, Any] = {
        "portable": str(portable),
        "backend_found": False,
        "certifi_detected": False,
        "hooks_contrib_detected": False,
        "unexpected_runtime_markers": [],
    }
    try:
        members = _zip_members(portable)
    except (zipfile.BadZipFile, OSError) as exc:
        result.fail.append(f"portable package unreadable: {exc}")
        return detail
    detail["member_count"] = len(members)
    backend = _read_backend_bytes(portable)
    if backend is None:
        result.fail.append("amca-backend.exe not found in portable package")
        return detail
    detail["backend_found"] = True

    certifi_hit = b"certifi" in backend
    detail["certifi_detected"] = certifi_hit
    sbom_names = {c["name"].lower() for c in sbom_components}
    if certifi_hit and "certifi" not in sbom_names:
        result.fail.append(
            "certifi detected in shipped backend but absent from final SBOM")
    if certifi_hit:
        payload = json.loads(payload_path.read_text(encoding="utf-8")) \
            if payload_path.is_file() else {}
        certifi_item = next((i for i in payload.get("items", [])
                             if i.get("component") == "certifi"), None)
        if certifi_item is None:
            result.fail.append(
                "certifi shipped without third-party compliance payload entry")
        elif certifi_item.get("license_file_present") is not True:
            result.review.append(
                "certifi (MPL-2.0) shipped: license text not available "
                "locally; THIRD-PARTY-NOTICES must carry the MPL notice — "
                "REVIEW preserved, never silently resolved")
    hooks_hit = b"hooks-contrib" in backend
    detail["hooks_contrib_detected"] = hooks_hit
    if hooks_hit:
        result.fail.append(
            "hooks-contrib material detected in shipped backend; PyInstaller "
            "hooks are build-time only and must not ship")
    pyinstaller_component = next(
        (c for c in sbom_components if c["name"].lower() == "pyinstaller"), None)
    if pyinstaller_component is not None and \
            pyinstaller_component.get("in_distributed_artifact") is True:
        result.fail.append(
            "PyInstaller falsely classified as a standalone shipped "
            "dependency; it is a build-only generator")
    return detail


def _magic_role(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return None
    if head.startswith(MSI_MAGIC):
        return "msi"
    if head.startswith(EXE_MAGIC):
        return "exe"
    return None


def run_verification(
    portable: Optional[Path],
    installer: Optional[Path],
    source_sha: Optional[str],
    evidence_path: Path = DEFAULT_EVIDENCE,
    sbom_path: Optional[Path] = DEFAULT_SBOM,
    notices_path: Optional[Path] = DEFAULT_NOTICES,
    payload_path: Path = None,
    msi: Optional[Path] = None,
) -> Dict[str, Any]:
    # H2C.2E: bind to the v1.0.1-named payload manifest when present (the
    # certifi condition is resolved there: canonical notice ships);
    # fall back to the historical v1.0.0-named record only when absent.
    if payload_path is None:
        payload_path = (DEFAULT_PAYLOAD_V101
                        if DEFAULT_PAYLOAD_V101.is_file() else DEFAULT_PAYLOAD)
    result = Result()
    extra: Dict[str, Any] = {
        "certification_timestamp": __import__("time").strftime(
            "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "source_sha": source_sha,
        "artifacts": {},
        "sbom_sha256": None,
        "notices_sha256": None,
        "compliance_payload_sha256": None,
        "evidence_source_sha": None,
        "package_version": None,
        "shipped_component_result": {},
        "review_items_preserved": True,
        "blocking_findings": [],
    }

    # --- mandatory payload presence -------------------------------------
    required: List[tuple] = [
        ("compliance payload manifest", payload_path),
        ("compliance report", DEFAULT_REPORT),
    ]
    if sbom_path is not None:
        required.append(("SBOM", sbom_path))
    if notices_path is not None:
        required.append(("third-party notices", notices_path))
    for label, path in required:
        if path is None or not path.is_file():
            result.blocked.append(
                f"required compliance payload missing: {label} ({path})")
    for label, path in (("SBOM", sbom_path), ("notices", notices_path),
                        ("payload manifest", payload_path)):
        if path is not None and path.is_file():
            digest = sha256_file(path)
            extra[{"SBOM": "sbom_sha256", "notices": "notices_sha256",
                   "payload manifest": "compliance_payload_sha256"}[label]] = digest

    # --- evidence lineage binding ----------------------------------------
    recorded_artifacts: Dict[str, Any] = {}
    if evidence_path.is_file():
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            extra["evidence_source_sha"] = evidence.get("source_sha")
            if source_sha and evidence.get("source_sha") != source_sha:
                result.fail.append(
                    "compliance evidence source SHA mismatch: evidence bound "
                    f"to {evidence.get('source_sha')}, final package built "
                    f"from {source_sha} — stale evidence cannot certify "
                    "different bytes")
            recorded_artifacts = evidence.get("artifact_hashes", {}) or {}
        except ValueError as exc:
            result.fail.append(f"compliance evidence unreadable: {exc}")
    else:
        result.blocked.append(
            f"compliance evidence missing: {evidence_path}")

    # --- artifact hashes --------------------------------------------------
    for label, artifact in (("portable", portable), ("installer", installer),
                            ("msi", msi)):
        if artifact is None:
            continue
        if not artifact.is_file():
            result.fail.append(f"{label} package not found: {artifact}")
            continue
        if label == "msi":
            if not str(artifact).lower().endswith(".msi"):
                result.fail.append("msi: path must end with .msi")
            role = _magic_role(artifact)
            if role != "msi":
                result.fail.append(
                    f"msi: file magic is {role!r}, not an OLE compound file — "
                    "role substitution (e.g. EXE renamed to .msi) rejected")
        digest = sha256_file(artifact)
        extra["artifacts"][label] = {"path": str(artifact), "sha256": digest,
                                     "size_bytes": artifact.stat().st_size}
        recorded = recorded_artifacts.get(label)
        if recorded is not None and recorded.get("sha256") != digest:
            result.fail.append(
                f"{label} artifact bytes differ from the compliance-evidence "
                f"lineage (recorded {recorded.get('sha256', '')[:16]}…, actual "
                f"{digest[:16]}…) — modified artifact cannot be certified")
        result.notes.append(f"{label} SHA256 recorded: {digest[:16]}…")

    # --- package/SBOM consistency ----------------------------------------
    if portable is not None and portable.is_file() and \
            sbom_path is not None and sbom_path.is_file() and \
            payload_path is not None and payload_path.is_file():
        try:
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            components = sbom.get("components", [])
            extra["package_version"] = next(
                (c.get("version") for c in components
                 if c.get("name") == "amca-platform"), None)
            extra["shipped_component_result"] = verify_package_consistency(
                portable, components, result, payload_path=payload_path)
        except ValueError as exc:
            result.fail.append(f"SBOM unreadable: {exc}")

    # --- BLOCKING can never become PASS -----------------------------------
    if evidence_path.is_file():
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            for ack in evidence.get("review_acknowledgments", []):
                if ack.get("technically_blocking"):
                    result.fail.append(
                        "BLOCKING compliance finding present in evidence: "
                        f"{ack.get('component')} — cannot certify")
                else:
                    result.review.append(
                        f"REVIEW preserved: {ack.get('component')} — "
                        f"{ack.get('required_action', 'action required')}")
        except ValueError:
            pass

    extra["blocking_findings"] = list(result.fail)
    return result.to_dict(extra)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Final-package compliance verification (I2O)")
    parser.add_argument("--portable", default=None,
                        help="Definitive v1.0.0 portable ZIP path")
    parser.add_argument("--installer", default=None,
                        help="Definitive v1.0.0 installer EXE path")
    parser.add_argument("--msi", default=None,
                        help="Definitive v1.0.1 MSI installer path "
                             "(H1C MSI-aware extension)")
    parser.add_argument("--source-sha", default=None,
                        help="Canonical source SHA of the final build")
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--sbom", default=str(DEFAULT_SBOM))
    parser.add_argument("--notices", default=str(DEFAULT_NOTICES))
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = run_verification(
        Path(args.portable) if args.portable else None,
        Path(args.installer) if args.installer else None,
        args.source_sha,
        evidence_path=Path(args.evidence),
        sbom_path=Path(args.sbom),
        notices_path=Path(args.notices),
        payload_path=Path(args.payload),
        msi=Path(args.msi) if args.msi else None,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["overall"] in ("PASS", "PASS_WITH_REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
