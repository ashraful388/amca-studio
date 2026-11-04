"""AMCA Studio v1.0.0 Distribution Provenance and Release Gating (I2B).

Milestone: AG-AMCA-STUDIO-V1.0.0-I2B-DISTRIBUTION-PROVENANCE-RELEASE-GATING-AND-BUILD-HYGIENE-01

Separates physical artifact verification (I2A) from final-release
certification. A physically valid installer/portable package is necessary but
never sufficient for an official final release: final-release certification
additionally requires OpenCode's canonical certification evidence.

Responsibilities:
1. Certification Evidence Consumption (not duplication):
   - Reads the canonical certification result JSON produced by
     scripts/run_canonical_certification.py (OpenCode I1D lineage).
   - Understands verdict PASS/FAIL/BLOCKED and its provenance record.
2. Same-SHA Release Gating:
   - The packaging source SHA must equal the certification SHA for an official
     final release; any mismatch is BLOCKED.
3. Dirty-Tree Enforcement:
   - A final shipping package must not be certified from a dirty source tree;
     audit/test builds run with dirty=true and stay BLOCKED for final release.
4. Provenance Manifests:
   - Every generated release manifest records source SHA, branch, dirty state,
     build timestamp, version, certification verdict/SHA, and the physical
     artifact records.

Verdict model (state machine, first failure wins):
  NOT_EVALUATED      - no certification evidence supplied
  EVIDENCE_INVALID   - evidence file unreadable/malformed
  VERDICT_FAIL       - canonical verdict = FAIL
  VERDICT_BLOCKED    - canonical verdict = BLOCKED
  SHA_MISMATCH       - packaging SHA != certification SHA
  DIRTY_TREE         - packaging tree dirty
  BLOCKED_ELIGIBLE   - physically verified, gating BLOCKED for final release
  ELIGIBLE           - all gates pass; eligible for final release certification
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_PATH = SOFTWARE_ROOT / "tests" / "certification-result.json"

#: Certification verdicts recognized in canonical certification evidence.
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_BLOCKED = "BLOCKED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_git_provenance(software_root: Optional[Path] = None) -> Dict[str, Any]:
    """Collect source provenance (branch, full SHA, dirty state).

    Mirrors scripts/run_canonical_certification.py::git_provenance so both
    layers agree on semantics.
    """
    root = software_root or SOFTWARE_ROOT

    def git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(root),
                capture_output=True, text=True, timeout=30,
            )
            return proc.stdout.strip() if proc.returncode == 0 else ""
        except Exception:
            return ""

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    sha = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    dirty = bool(status)
    return {"branch": branch, "sha": sha, "clean": not dirty, "dirty": dirty}


# Authorized generated publication outputs owned by the release pipeline
# (software/dist). Only these exact names are exempt from the packaging
# dirty-tree determination; real source/test/config/packaging mutations
# anywhere else still block certification.
AUTHORIZED_PUBLICATION_TRACKED = ("software/dist/SHA256SUMS.txt",)
AUTHORIZED_PUBLICATION_UNTRACKED_PREFIX = "software/dist/"
AUTHORIZED_PUBLICATION_UNTRACKED_EXCLUDE = (
    "software/dist/KNOWN-LIMITATIONS.md",
)


def publication_scoped_git_provenance(
    software_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Provenance for certification evaluation: the release pipeline's own
    authorized generated publication outputs under software/dist/ are
    excluded from the dirty determination — the tracked checksum file it
    regenerates every run and the untracked generated release materials it
    stages there (artifacts, release manifests, SBOM, notices). Everything
    else (any tracked or untracked change anywhere else, hand-authored
    files under software/dist such as KNOWN-LIMITATIONS.md, and any other
    tracked modification under software/dist) still marks the tree dirty.
    Mirrors current_git_provenance semantics otherwise.
    """
    root = software_root or SOFTWARE_ROOT

    def git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(root),
                capture_output=True, text=True, timeout=30,
            )
            # raw stdout: porcelain " M" tracked lines carry a leading
            # space that is part of the XY status field — stripping the
            # whole output shifts the field parse by one and would break
            # the tracked exemption matching
            return proc.stdout if proc.returncode == 0 else ""
        except Exception:
            return ""

    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    sha = git("rev-parse", "HEAD").strip()
    status = git("status", "--porcelain")
    kept: List[str] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        raw = line[:2]
        path = line[3:].strip().strip('"')
        normalized = path.replace("\\", "/")
        if raw.strip() == "??":
            if normalized.startswith(AUTHORIZED_PUBLICATION_UNTRACKED_PREFIX):
                if normalized not in AUTHORIZED_PUBLICATION_UNTRACKED_EXCLUDE:
                    continue
        else:
            if normalized in AUTHORIZED_PUBLICATION_TRACKED:
                continue
        kept.append(line)
    dirty = bool(kept)
    return {
        "branch": branch,
        "sha": sha,
        "clean": not dirty,
        "dirty": dirty,
        "exempt_publication_lines": len(status.splitlines()) - len(kept),
    }


@dataclass
class ReleaseGateDecision:
    """Outcome of release gating for one packaging run."""

    state: str  # module docstring state model
    final_release_certified: bool
    reasons: List[str] = field(default_factory=list)
    certification_verdict: Optional[str] = None
    certification_sha: Optional[str] = None
    packaging_sha: Optional[str] = None
    packaging_branch: Optional[str] = None
    packaging_dirty: Optional[bool] = None
    evidence_path: Optional[str] = None


def load_certification_evidence(
    evidence_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load and validate canonical certification evidence (consumer role)."""
    path = Path(evidence_path) if evidence_path else DEFAULT_EVIDENCE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Certification evidence not found: {path}")
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Certification evidence unreadable: {exc}") from exc

    if not isinstance(evidence, dict):
        raise ValueError("Certification evidence must be a JSON object")
    if "overall_verdict" not in evidence:
        raise ValueError("Certification evidence missing overall_verdict")
    verdict = evidence["overall_verdict"]
    if verdict not in (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED):
        raise ValueError(f"Unknown certification verdict: {verdict!r}")
    return evidence


def evaluate_release_gate(
    *,
    certification_evidence: Optional[Dict[str, Any]] = None,
    evidence_path: Optional[Path] = None,
    packaging_provenance: Optional[Dict[str, Any]] = None,
    software_root: Optional[Path] = None,
    scoped_publication_provenance: bool = False,
) -> ReleaseGateDecision:
    """Gate final-release certification on canonical certification evidence.

    Physical artifact verification (I2A) is orthogonal and already performed
    by amca.distribution_build; this function decides whether a physically
    verified build may be treated as the official final release.

    With scoped_publication_provenance=True the packaging-dirty
    determination excludes the release pipeline's own authorized generated
    publication outputs under software/dist/ (see
    publication_scoped_git_provenance); every other tracked or untracked
    change anywhere still blocks certification via DIRTY_TREE.
    """
    root = software_root or SOFTWARE_ROOT
    if scoped_publication_provenance and packaging_provenance is None:
        packaging_provenance = publication_scoped_git_provenance(root)

    if certification_evidence is None and evidence_path is None:
        return ReleaseGateDecision(
            state="NOT_EVALUATED",
            final_release_certified=False,
            reasons=[
                "No canonical certification evidence supplied; physical "
                "verification alone cannot certify a final release"
            ],
            **_provenance_fields(packaging_provenance, root),
        )

    if certification_evidence is None:
        if evidence_path is not None and not Path(evidence_path).is_file():
            return ReleaseGateDecision(
                state="NOT_EVALUATED",
                final_release_certified=False,
                reasons=[
                    f"Certification evidence file not found: {evidence_path}; "
                    "physical verification alone cannot certify a final release"
                ],
                **_provenance_fields(packaging_provenance, root),
            )
        try:
            certification_evidence = load_certification_evidence(evidence_path)
        except (FileNotFoundError, ValueError) as exc:
            return ReleaseGateDecision(
                state="EVIDENCE_INVALID",
                final_release_certified=False,
                reasons=[str(exc)],
                **_provenance_fields(packaging_provenance, root),
            )

    verdict = certification_evidence["overall_verdict"]
    provenance = _evidence_provenance(certification_evidence)
    cert_sha = provenance.get("sha") or ""

    fields = _provenance_fields(packaging_provenance, root)

    if verdict == VERDICT_FAIL:
        return ReleaseGateDecision(
            state="VERDICT_FAIL", final_release_certified=False,
            reasons=["Canonical certification verdict is FAIL; not releasable"],
            certification_verdict=verdict, certification_sha=cert_sha,
            evidence_path=str(evidence_path or DEFAULT_EVIDENCE_PATH),
            **fields,
        )

    if verdict == VERDICT_BLOCKED:
        blocked_reasons = certification_evidence.get("blocked_reasons") or []
        reason_text = "; ".join(str(r) for r in blocked_reasons) or "no reasons recorded"
        return ReleaseGateDecision(
            state="VERDICT_BLOCKED", final_release_certified=False,
            reasons=[
                f"Canonical certification verdict is BLOCKED; not releasable ({reason_text})"
            ],
            certification_verdict=verdict, certification_sha=cert_sha,
            evidence_path=str(evidence_path or DEFAULT_EVIDENCE_PATH),
            **fields,
        )

    # verdict == PASS: same-SHA and clean-tree gates apply.
    pkg_sha = fields["packaging_sha"] or ""
    # H2C.2I Part A (narrow authorized tooling change): the certification
    # record may separately attest artifact_source_sha when the certification
    # was issued for the artifact-source tree while the packaging tree
    # additionally contains post-build evidence/tooling commits. In that
    # case the gate verifies the packaging delta against the certified
    # source is shipped-input-free and accepts, recording the packaging
    # HEAD as post-build evidence metadata. Any shipped-build-input change
    # still fails SHA_MISMATCH unchanged.
    artifact_source_sha = str(
        certification_evidence.get("artifact_source_sha") or "").lower() or None
    # Fail closed: a package whose SHA differs from the certified SHA is
    # BLOCKED unless the certification itself attests that the certified SHA
    # is the artifact-source SHA and the packaging delta ships no build input.
    sha_ok = bool(cert_sha) and pkg_sha == cert_sha
    post_build_ok = False
    if (not sha_ok and cert_sha and pkg_sha
            and artifact_source_sha == cert_sha):
        changed = subprocess.run(
            ["git", "diff", "--name-only", cert_sha, pkg_sha],
            cwd=str(software_root or SOFTWARE_ROOT),
            capture_output=True, text=True, timeout=30).stdout
        changed_files = [f.strip() for f in changed.splitlines() if f.strip()]
        shipped_prefixes = ("software/src/", "software/desktop-ui/src/",
                            "software/desktop-ui/src-tauri/src/",
                            "software/desktop-ui/src-tauri/tauri.conf.json",
                            "software/desktop-ui/src-tauri/Cargo.toml",
                            "software/backend-dist/",
                            "software/pyproject.toml")
        shipped_changes = [f for f in changed_files
                           if f.startswith(shipped_prefixes)]
        if not shipped_changes:
            post_build_ok = True
            sha_ok = True
    if not sha_ok:
        return ReleaseGateDecision(
            state="SHA_MISMATCH", final_release_certified=False,
            reasons=[
                f"Packaging source SHA {pkg_sha or '(unknown)'} does not match "
                f"certification SHA {cert_sha or '(unknown)'}; BLOCKED"
            ],
            certification_verdict=verdict, certification_sha=cert_sha,
            evidence_path=str(evidence_path or DEFAULT_EVIDENCE_PATH),
            **fields,
        )
    if post_build_ok:
        # packaging HEAD is a post-build evidence/tooling delta over the
        # certified artifact source - recorded, never treated as the
        # artifact source.
        fields["post_build_evidence_refresh_sha"] = pkg_sha

    if fields["packaging_dirty"]:
        return ReleaseGateDecision(
            state="DIRTY_TREE", final_release_certified=False,
            reasons=[
                "Packaging source tree is dirty; audit/test builds may proceed "
                "but final-release certification remains BLOCKED"
            ],
            certification_verdict=verdict, certification_sha=cert_sha,
            evidence_path=str(evidence_path or DEFAULT_EVIDENCE_PATH),
            **fields,
        )

    return ReleaseGateDecision(
        state="ELIGIBLE",
        final_release_certified=True,
        reasons=[
            "Certification PASS, packaging SHA matches certification SHA, "
            "clean tree: eligible for final release certification"
        ],
        certification_verdict=verdict, certification_sha=cert_sha,
        evidence_path=str(evidence_path or DEFAULT_EVIDENCE_PATH),
        **fields,
    )


def _provenance_fields(
    packaging_provenance: Optional[Dict[str, Any]],
    root: Path,
) -> Dict[str, Any]:
    prov = packaging_provenance or current_git_provenance(root)
    return {
        "packaging_sha": prov.get("sha"),
        "packaging_branch": prov.get("branch"),
        "packaging_dirty": prov.get("dirty"),
    }


def _evidence_provenance(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the certification provenance record from gate evidence."""
    for key in ("provenance", "certification"):
        record = evidence.get(key)
        if isinstance(record, dict) and record.get("sha"):
            return record
    return {}


def build_provenance_record(
    *,
    version: str,
    gate: ReleaseGateDecision,
    artifacts: List[Dict[str, Any]],
    skipped: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Assemble the manifest provenance block required by I2B objective 2."""
    return {
        "source_git_sha": gate.packaging_sha,
        "source_branch": gate.packaging_branch,
        "dirty_tree": gate.packaging_dirty,
        "build_timestamp_utc": _utc_now(),
        "application_version": version,
        "certification_verdict": gate.certification_verdict,
        "certification_sha": gate.certification_sha,
        "release_gate_state": gate.state,
        "final_release_certified": gate.final_release_certified,
        "physical_artifacts": [
            {
                "package_name": a["package_name"],
                "size_bytes": a["size_bytes"],
                "sha256": a["sha256"],
            }
            for a in artifacts
        ],
        "skipped_artifacts": skipped or [],
    }
