#!/usr/bin/env python3
"""I2S immutable shipping-document export + post-certification finalization.

OpenCode PS-2. Release-document generation/evidence only. No source-version,
product, packaging, or promotion changes.

Generates the shipping release-document bundle FROM already-certified source
documents plus final certification evidence, WITHOUT modifying the certified
source tree:

  python scripts/export_final_release_documentation.py
      --staging DIR --certified-source-sha SHA
      [--certification-evidence PATH] [--portable PATH] [--installer PATH]
      [--msi PATH] [--release-version 1.0.0|1.0.1]
      [--policy unsigned-disclosure=include|exclude] [--stage]
      [--output PATH]

Invariants:
  - Dry-run by default: no writes unless --stage with an explicit staging
    directory; never writes into the canonical source tree; never copies to
    software/dist.
  - No final certification evidence for the certified source SHA ⇒ BLOCKED;
    the final release notes shipping artifact is generated only when final
    evidence permits; no premature release claim.
  - Source documents are read-only inputs; the candidate release notes file
    is never edited in place ("candidate" wording is not rewritten in the
    source tree — the shipping copy finalizes wording in the export).
  - Every exported document is bound to: certified source SHA, release
    version, source-document hash, exported-document hash, and the final
    certification-evidence hash where relevant.
  - Policy-aware: unsigned-Windows disclosure included/excluded per policy;
    Linux remains explicitly not included; compliance remains
    PASS_WITH_REVIEW (never legal approval).

Result schema: amca-final-release-documentation-export/1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "amca-final-release-documentation-export/1"

RELEASE_DIR = SOFTWARE_ROOT / "docs" / "release"
SOURCE_DOCS = {
    "release_notes": RELEASE_DIR / "AMCA-Studio-v1.0.0-Release-Notes-Candidate.md",
    "known_limitations": RELEASE_DIR / "AMCA-Studio-v1.0.0-Known-Limitations.md",
    "installation": RELEASE_DIR / "AMCA-Studio-v1.0.0-Installation-and-First-Launch-Guide.md",
    "upgrade_recovery": RELEASE_DIR / "AMCA-Studio-v1.0.0-Upgrade-and-Recovery-Guide.md",
    "troubleshooting": RELEASE_DIR / "AMCA-Studio-v1.0.0-Troubleshooting-Guide.md",
    "third_party_notices": RELEASE_DIR / "AMCA-Studio-v1.0.0-Third-Party-Notices-Candidate.md",
    "sbom": RELEASE_DIR / "AMCA-Studio-v1.0.0-SBOM.json",
    "compliance_summary": RELEASE_DIR / (
        "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json"),
    "payload_manifest": RELEASE_DIR / (
        "AMCA-Studio-v1.0.0-Third-Party-Compliance-Payload-Manifest.json"),
}

CANDIDATE_MARKERS = ("candidate", "not released", "not included in v1.0.0")

# H1E surface 7: v1.0.1 three-artifact shipping binding contract.
V101_ARTIFACT_SUFFIX = {"exe": ".exe", "msi": ".msi", "portable": ".zip"}
V100_KNOWN_HASHES = {
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1",
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454",
}


def build_artifact_bindings(
    exe: Optional[Path], msi: Optional[Path], portable: Optional[Path],
    release_version: str,
) -> tuple:
    """Validate + bind the three Windows artifacts for a v1.0.1 shipping
    export. Returns (bindings, reasons); reasons non-empty => BLOCKED.
    All three artifacts are mandatory, must be real files under the D:
    working root, named for v1.0.1 (no fallback to v1.0.0 names), and
    their historical v1.0.0 hashes are rejected as v1.0.1 bindings."""
    reasons: List[str] = []
    bindings: List[Dict[str, Any]] = []
    for role, path in (("exe", exe), ("msi", msi), ("portable", portable)):
        if path is None:
            reasons.append(
                f"v1.0.1 shipping export requires the {role.upper() if role == 'msi' else role} "
                f"artifact; missing ({role} not supplied)")
            continue
        norm = str(path).replace("\\", "/")
        if not norm.startswith(("D:/", "d:/")):
            reasons.append(
                f"{role}: v1.0.1 current-release artifacts must live under "
                f"the D: working root (got {norm!r})")
            continue
        if not path.is_file():
            reasons.append(f"{role}: artifact file missing: {norm}")
            continue
        if not norm.lower().endswith(V101_ARTIFACT_SUFFIX[role]):
            reasons.append(
                f"{role}: path must end with "
                f"{V101_ARTIFACT_SUFFIX[role]}")
            continue
        if f"v{release_version}" not in path.name:
            reasons.append(
                f"{role}: artifact name must carry the v{release_version} "
                "version tag (no fallback to v1.0.0 names)")
            continue
        digest = sha256_file(path)
        if digest in V100_KNOWN_HASHES:
            reasons.append(
                f"{role}: hash is a historical v1.0.0 artifact — v1.0.0 "
                "bytes cannot be bound as v1.0.1 shipping evidence")
            continue
        bindings.append({
            "role": role, "name": path.name, "path": norm,
            "sha256": digest, "size_bytes": path.stat().st_size,
            "release_version": release_version,
        })
    if reasons:
        return [], reasons
    return bindings, []


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_final_certification(
    evidence_path: Optional[Path], certified_source_sha: Optional[str],
) -> Dict[str, Any]:
    """Validate final certification evidence for the certified source SHA."""
    info: Dict[str, Any] = {
        "present": False, "source_sha": None, "final": False,
        "detail": "", "sha256": None,
    }
    if evidence_path is None or not evidence_path.is_file():
        info["detail"] = "final certification evidence not supplied"
        return info
    info["sha256"] = sha256_file(evidence_path)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        info["detail"] = f"certification evidence unreadable: {exc}"
        return info
    info["present"] = True
    info["source_sha"] = evidence.get("source_sha")
    role = evidence.get("package_role")
    overall = evidence.get("overall")
    if certified_source_sha and evidence.get("source_sha") != (
            certified_source_sha):
        info["detail"] = (
            f"certification evidence bound to source "
            f"{evidence.get('source_sha')}, expected {certified_source_sha}")
        return info
    if role != "final":
        info["detail"] = (
            f"certification evidence is validation-role ({role}); final "
            "release certification required")
        return info
    if overall not in ("PASS", "PASS_WITH_REVIEW"):
        info["detail"] = f"certification overall is {overall!r}"
        return info
    info["final"] = True
    info["overall"] = overall
    info["review_acknowledgments"] = evidence.get(
        "review_acknowledgments", [])
    info["detail"] = "final certification evidence valid"
    return info


def finalize_release_notes(source_text: str, certification: Dict[str, Any],
                           unsigned_disclosure: Optional[str]) -> str:
    """Generate shipping release notes from the certified candidate source.

    The candidate file itself is never modified; finality wording exists only
    in the exported copy, and only when final evidence permits.
    """
    text = source_text
    text = text.replace(
        "**Status:** CANDIDATE — v1.0.0 is **not released**.",
        "**Status:** RELEASED — AMCA Studio v1.0.0.")
    text = re.sub(r"v1\.0\.0 is \*\*not released\*\*\.?",
                  "v1.0.0 was certified and released from this exact source "
                  "lineage.", text)
    text = text.replace("## What is new in the v1.0.0 candidate",
                        "## What is new in AMCA Studio v1.0.0")
    text = text.replace(
        "| v1.0.0 | **Candidate — not released** |",
        "| v1.0.0 | **Released** |")
    header = [
        "",
        "## Release certification",
        "",
        f"- Certified source SHA: {certification.get('source_sha')}",
        f"- Certification evidence SHA-256: {certification.get('sha256')}",
        f"- Certification verdict: {certification.get('overall')}",
        "",
    ]
    if certification.get("overall") == "PASS_WITH_REVIEW":
        header.append(
            "This release ships with documented, policy-acknowledged "
            "compliance REVIEW items (see THIRD-PARTY-NOTICES and the "
            "compliance evidence). No legal approval is implied.")
        header.append("")
    if unsigned_disclosure == "include":
        header.extend([
            "## Windows binaries are unsigned",
            "",
            "The Windows installer and portable package are not "
            "Authenticode-signed in this release. Verify the published "
            "SHA-256 checksums before running.",
            "",
        ])
    text = text.rstrip() + "\n" + "\n".join(header)
    # Linux exclusion is preserved verbatim from the certified source; the
    # policy cannot add Linux support here.
    return text


def export_bundle(
    staging: Path,
    certified_source_sha: str,
    certification_evidence: Path,
    portable: Optional[Path] = None,
    installer: Optional[Path] = None,
    policy: Optional[Dict[str, str]] = None,
    stage: bool = False,
    release_version: str = "1.0.0",
    msi: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the export result. Dry-run by default; read-only on sources."""
    policy = policy or {}
    unsigned = policy.get("unsigned-disclosure", "include")
    vtag = f"v{release_version}"
    result: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release_version": release_version,
        "certified_source_sha": certified_source_sha,
        "staging": str(staging),
        "stage": stage,
        "certification": {},
        "documents": [],
        "manifest_sha256": None,
        "overall": "BLOCKED",
        "reasons": [],
        "policy": {"unsigned-disclosure": unsigned},
    }
    if release_version.startswith("1.0.1"):
        bindings, breasons = build_artifact_bindings(
            installer, msi, portable, release_version)
        if breasons:
            result["reasons"].extend(breasons)
            return result
        result["artifacts"] = bindings
        result["release_state"] = "WINDOWS_ONLY"
    certification = check_final_certification(certification_evidence,
                                              certified_source_sha)
    result["certification"] = certification
    if not certification.get("final"):
        result["reasons"].append(
            f"final certification unavailable: {certification.get('detail')}")
        return result

    generated: List[Dict[str, Any]] = []

    def _source_doc(key: str) -> Path:
        if release_version == "1.0.0":
            return SOURCE_DOCS[key]
        return Path(str(SOURCE_DOCS[key]).replace("v1.0.0", vtag))

    # Final release notes (only generated document with finalized wording).
    notes_src = _source_doc("release_notes")
    if not notes_src.is_file():
        result["reasons"].append("release-notes candidate missing from "
                                 "certified source tree")
        return result
    notes_out = finalize_release_notes(
        notes_src.read_text(encoding="utf-8"), certification, unsigned)
    generated.append({"key": "final_release_notes",
                      "name": f"AMCA-Studio-{vtag}-Release-Notes.md",
                      "content": notes_out,
                      "source": str(notes_src),
                      "source_sha256": sha256_file(notes_src)})
    # Verbatim copies of the remaining certified source documents.
    for key in ("known_limitations", "installation", "upgrade_recovery",
                "troubleshooting", "third_party_notices", "sbom",
                "compliance_summary", "payload_manifest"):
        src = _source_doc(key)
        if not src.is_file():
            result["reasons"].append(f"certified source document missing: {key}")
            continue
        generated.append({
            "key": key, "name": src.name,
            "content": src.read_text(encoding="utf-8", errors="strict"),
            "source": str(src), "source_sha256": sha256_file(src)})
    # Certification summary (generated).
    summary = {
        "schema": "amca-certification-summary/1",
        "release_version": release_version,
        "certified_source_sha": certified_source_sha,
        "certification": {k: v for k, v in certification.items()
                          if k != "review_acknowledgments"},
        "review_acknowledgments": certification.get(
            "review_acknowledgments", []),
        "linux_included": False,
        "compliance_state": certification.get("overall"),
        "note": "PASS_WITH_REVIEW is preserved; no legal approval implied.",
    }
    generated.append({
        "key": "certification_summary", "name":
        f"AMCA-Studio-{vtag}-Certification-Summary.json",
        "content": json.dumps(summary, indent=2, sort_keys=True) + "\n",
        "source": str(certification_evidence),
        "source_sha256": certification["sha256"]})

    entries: List[Dict[str, Any]] = []
    for item in generated:
        data = item["content"].encode("utf-8")
        entries.append({
            "document": item["name"], "key": item["key"],
            "sha256": sha256_bytes(data), "size_bytes": len(data),
            "source_document": item["source"],
            "source_document_sha256": item["source_sha256"],
            "certified_source_sha": certified_source_sha,
            "release_version": release_version,
            "certification_evidence_sha256": certification.get("sha256"),
        })
    manifest = {
        "schema": "amca-shipping-documentation-manifest/1",
        "release_version": release_version,
        "certified_source_sha": certified_source_sha,
        "certification_evidence_sha256": certification.get("sha256"),
        "policy": result["policy"],
        "documents": entries,
    }
    if release_version.startswith("1.0.1"):
        manifest["artifacts"] = result["artifacts"]
        manifest["release_state"] = "WINDOWS_ONLY"
    manifest_content = (json.dumps(manifest, indent=2, sort_keys=True)
                        + "\n").encode("utf-8")
    result["documents"] = entries
    result["manifest_sha256"] = sha256_bytes(manifest_content)

    if stage:
        if staging.resolve() in (SOFTWARE_ROOT.resolve(),) or \
                SOFTWARE_ROOT.resolve() in staging.resolve().parents:
            result["reasons"].append(
                "refusing to write the export into the canonical source tree")
            return result
        staging.mkdir(parents=True, exist_ok=True)
        for item in generated:
            target = staging / item["name"]
            if target.exists() and target.read_bytes() != \
                    item["content"].encode("utf-8"):
                result["reasons"].append(
                    f"existing staging file differs; not overwritten: "
                    f"{item['name']}")
                return result
            target.write_bytes(item["content"].encode("utf-8"))
        (staging / f"AMCA-Studio-{vtag}-Shipping-Documentation-Manifest.json"
         ).write_bytes(manifest_content)
        result["staged"] = True
    else:
        result["staged"] = False
    result["overall"] = "PASS_WITH_REVIEW" if certification.get(
        "overall") == "PASS_WITH_REVIEW" else "PASS"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Immutable shipping-document export (I2S)")
    parser.add_argument("--staging", default=None,
                        help="Caller-supplied staging directory (enables write)")
    parser.add_argument("--certified-source-sha", required=True)
    parser.add_argument("--certification-evidence", required=True)
    parser.add_argument("--portable", default=None)
    parser.add_argument("--installer", default=None)
    parser.add_argument("--msi", default=None,
                        help="v1.0.1 MSI artifact (H1E surface 7)")
    parser.add_argument("--release-version", default="1.0.0",
                        help="1.0.0 (historical) or 1.0.1 (three-artifact)")
    parser.add_argument("--policy", action="append", default=[],
                        help="key=value, e.g. unsigned-disclosure=exclude")
    parser.add_argument("--stage", action="store_true",
                        help="Actually write the export (default dry-run)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    policy = {}
    for pair in args.policy:
        key, _, value = pair.partition("=")
        policy[key.strip()] = value.strip()
    result = export_bundle(
        Path(args.staging) if args.staging else
        Path(tempdir_guard()), args.certified_source_sha,
        Path(args.certification_evidence),
        portable=Path(args.portable) if args.portable else None,
        installer=Path(args.installer) if args.installer else None,
        policy=policy, stage=args.stage,
        release_version=args.release_version,
        msi=Path(args.msi) if args.msi else None)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["overall"] in ("PASS", "PASS_WITH_REVIEW") else 1


def tempdir_guard() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="i2s-export-dryrun-")


if __name__ == "__main__":
    raise SystemExit(main())
