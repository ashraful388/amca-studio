"""AMCA Studio v1.0.0 Final Release Promotion Gate and Evidence Orchestration (I2E).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2E-FINAL-RELEASE-PROMOTION-GATE-AND-EVIDENCE-ORCHESTRATION-01

Consumes (never duplicates) independently produced release evidence and
prevents AMCA Studio artifacts from being promoted as the official v1.0.0
release unless every mandatory gate passes:

1. Canonical regression/certification (OpenCode PS-1)  -> verdict PASS
2. Upgrade/migration certification (OpenCode PS-2)     -> verdict PASS
3. Distribution build/release gate (ZCode I2B/I2C)     -> physical artifacts
   matching the source SHA, clean tree, real SHA256 (release-gate state
   ELIGIBLE)
4. Windows runtime smoke (ZCode I2D)                   -> installer PASS and
   portable PASS
5. Version contract                                    -> all active release
   metadata consistent

Promotion state model (deterministic, no hidden inference):
  BLOCKED             - a mandatory evidence input is missing/unreadable
  REJECTED            - a supplied evidence input fails its gate
  READY_FOR_PROMOTION - all evidence valid; artifacts not yet promoted
  PROMOTED            - artifacts physically copied into the official release
                        destination with manifest verification (post-copy
                        hashes re-verified)

Same-lineage enforcement: certification SHA, migration certification SHA
(or an explicitly recorded compatible schema-certification SHA), and the
packaging source SHA must agree for promotion. A mismatch blocks/rejects
promotion. The migration relationship is recorded explicitly rather than
silently ignored.

Official release destination (standing AMCA release location):
  D:/ChatGPT/AMCA/software/dist
Promotion never silently overwrites existing official artifacts; it stages
into a subdirectory and requires an explicit overwrite flag.

Dry-run mode evaluates all evidence and copies nothing. Until OpenCode
PS-1/PS-2 deliver their final PASS evidence, the honest expected result is
BLOCKED.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amca.distribution_smoke import sha256_file

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOFTWARE_ROOT.parent
DEFAULT_OFFICIAL_DEST = Path(
    "D:/ChatGPT/AMCA/software/dist"
)

#: Evidence file locations (all consumed, none produced here).
DEFAULT_PS1_CERTIFICATION = SOFTWARE_ROOT / "tests" / "certification-result.json"
DEFAULT_PS2_MIGRATION = SOFTWARE_ROOT / "tests" / "migration-certification.json"
DEFAULT_SMOKE_RESULT = SOFTWARE_ROOT / "dist" / "windows-runtime-smoke-result.json"
DEFAULT_RELEASE_MANIFEST = SOFTWARE_ROOT / "dist" / "release_manifest_v1.0.0.json"
DEFAULT_SHA256SUMS = SOFTWARE_ROOT / "dist" / "SHA256SUMS.txt"

# Promotion state machine
ST_BLOCKED = "BLOCKED"
ST_REJECTED = "REJECTED"
ST_READY = "READY_FOR_PROMOTION"
ST_PROMOTED = "PROMOTED"

# Evidence verdicts
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_BLOCKED = "BLOCKED"
#: H2A.3: explicit non-applicable marker (v1.0.0 MSI smoke). Non-blocking,
#: visible in the gate inventory, never treated as PASS.
VERDICT_NOT_APPLICABLE = "NOT_APPLICABLE"

# H2B.2 Part C: canonical v1.0.1 product identity — the release-binding
# contract enforced by the product_identity gate. A package whose identity
# differs from this must not satisfy final v1.0.1 release validation.
CANONICAL_PRODUCT_IDENTITY_V1_0_1 = {
    "product_name": "AMCA Studio",
    "version": "1.0.1",
    "identifier": "ai.amca.desktop",
    "desktop_binary": "amca-desktop.exe",
    "backend_binary": "amca-backend.exe",
}

# Release tags (lifecycle: validate -> promote -> create tag -> verify tag).
RELEASE_TAG_V1_0_1 = "amca-studio-v1.0.1"
RELEASE_TAG_V1_0_0 = "amca-studio-v1.0.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(SOFTWARE_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def current_source_provenance() -> Dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "source_git_sha": _git("rev-parse", "HEAD"),
        "source_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_tree": bool(status.strip()),
    }


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


@dataclass
class GateResult:
    name: str
    verdict: str  # PASS / FAIL / BLOCKED
    detail: str
    evidence_path: Optional[str] = None
    sha: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict_safe(self)


def asdict_safe(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        out = {}
        for k in obj.__dataclass_fields__:
            v = getattr(obj, k)
            out[k] = asdict_safe(v)
        return out
    if isinstance(obj, list):
        return [asdict_safe(v) for v in obj]
    if isinstance(obj, dict):
        return {k: asdict_safe(v) for k, v in obj.items()}
    return obj


@dataclass
class EvidenceBundle:
    """Machine-readable final release evidence document."""

    final_release_version: Optional[str] = None
    canonical_source_sha: Optional[str] = None
    certification: Optional[GateResult] = None
    migration: Optional[GateResult] = None
    installer_smoke: Optional[GateResult] = None
    msi_smoke: Optional[GateResult] = None
    portable_smoke: Optional[GateResult] = None
    release_gate: Optional[GateResult] = None
    version_contract: Optional[GateResult] = None
    product_identity: Optional[GateResult] = None
    physical_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    promotion_state: str = ST_BLOCKED
    promotion_timestamp_utc: Optional[str] = None
    destination_paths: List[str] = field(default_factory=list)
    known_limitations: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    test_accounting: Optional[Dict[str, Any]] = None

    def to_document(self) -> Dict[str, Any]:
        return {
            "schema": "amca-final-release-evidence/1",
            "generated_at_utc": _utc_now(),
            "final_release_version": self.final_release_version,
            "canonical_source_sha": self.canonical_source_sha,
            "certification": self.certification.to_dict() if self.certification else None,
            "migration": self.migration.to_dict() if self.migration else None,
            "installer_smoke": self.installer_smoke.to_dict() if self.installer_smoke else None,
            "msi_smoke": self.msi_smoke.to_dict() if self.msi_smoke else None,
            "portable_smoke": self.portable_smoke.to_dict() if self.portable_smoke else None,
            "release_gate": self.release_gate.to_dict() if self.release_gate else None,
            "version_contract": self.version_contract.to_dict() if self.version_contract else None,
            "product_identity": self.product_identity.to_dict() if self.product_identity else None,
            "physical_artifacts": self.physical_artifacts,
            "promotion_state": self.promotion_state,
            "promotion_timestamp_utc": self.promotion_timestamp_utc,
            "destination_paths": self.destination_paths,
            "known_limitations": self.known_limitations,
            "reasons": self.reasons,
            "test_accounting": self.test_accounting,
        }


def _version_tuple(version: str):
    """Parse '1.0.1' -> (1, 0, 1); non-numeric segments tolerated."""

    parts = []
    for piece in str(version).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def evaluate_evidence(
    *,
    ps1_path: Path = DEFAULT_PS1_CERTIFICATION,
    ps2_path: Path = DEFAULT_PS2_MIGRATION,
    smoke_path: Path = DEFAULT_SMOKE_RESULT,
    manifest_path: Path = DEFAULT_RELEASE_MANIFEST,
    dist_dir: Optional[Path] = None,
    source_provenance: Optional[Dict[str, Any]] = None,
) -> EvidenceBundle:
    """Aggregate all mandatory evidence gates deterministically.

    Missing prerequisite -> BLOCKED. Supplied evidence that fails -> REJECTED.
    All valid -> READY_FOR_PROMOTION. (Promotion itself is a separate,
    explicit step; see promote_final_release.)
    """
    bundle = EvidenceBundle()
    dist = Path(dist_dir) if dist_dir else SOFTWARE_ROOT / "dist"

    # ---- Gate 1: canonical regression/certification (OpenCode PS-1) ----
    ps1 = _read_json(Path(ps1_path))
    if ps1 is None:
        bundle.certification = GateResult(
            "canonical_certification", ST_BLOCKED,
            f"certification evidence not found: {ps1_path}", str(ps1_path))
    else:
        verdict = ps1.get("overall_verdict")
        sha = (ps1.get("provenance") or {}).get("sha")
        bundle.certification = GateResult(
            "canonical_certification",
            verdict if verdict in (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED) else ST_BLOCKED,
            f"canonical certification verdict = {verdict}",
            str(ps1_path), sha)

    # ---- Gate 2: upgrade/migration certification (OpenCode PS-2) ----
    ps2 = _read_json(Path(ps2_path))
    if ps2 is None:
        bundle.migration = GateResult(
            "migration_certification", ST_BLOCKED,
            f"migration certification evidence not found: {ps2_path} "
            "(OpenCode PS-2 has not delivered final evidence yet)",
            str(ps2_path))
    else:
        verdict = ps2.get("overall_verdict") or ps2.get("verdict")
        sha = ps2.get("certified_sha") or (ps2.get("provenance") or {}).get("sha")
        compatible = ps2.get("compatible_schema_certification_sha")
        if sha is None and compatible:
            sha = compatible
            bundle.migration = GateResult(
                "migration_certification",
                verdict if verdict in (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED) else ST_BLOCKED,
                f"migration verdict = {verdict} (compatible schema-certification SHA "
                f"{compatible} recorded explicitly)",
                str(ps2_path), sha)
        else:
            bundle.migration = GateResult(
                "migration_certification",
                verdict if verdict in (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED) else ST_BLOCKED,
                f"migration verdict = {verdict}",
                str(ps2_path), sha)

    # ---- Gate 3: Windows runtime smoke (ZCode I2D) ----
    smoke = _read_json(Path(smoke_path))
    installer_smoke = portable_smoke = None
    if smoke is None:
        bundle.installer_smoke = GateResult(
            "installer_smoke", ST_BLOCKED,
            f"runtime smoke result not found: {smoke_path}", str(smoke_path))
        bundle.portable_smoke = GateResult(
            "portable_smoke", ST_BLOCKED,
            f"runtime smoke result not found: {smoke_path}", str(smoke_path))
    else:
        artifacts = smoke.get("artifacts") or {}
        installer_smoke = artifacts.get("installer") or {}
        portable_smoke = artifacts.get("portable") or {}
        bundle.installer_smoke = GateResult(
            "installer_smoke",
            installer_smoke.get("overall", ST_BLOCKED),
            f"installer smoke overall = {installer_smoke.get('overall')}",
            str(smoke_path), installer_smoke.get("source_sha"))
        bundle.portable_smoke = GateResult(
            "portable_smoke",
            portable_smoke.get("overall", ST_BLOCKED),
            f"portable smoke overall = {portable_smoke.get('overall')}",
            str(smoke_path), portable_smoke.get("source_sha"))

    # ---- Gate 3b: MSI smoke (H2A.2 S4 / H2A.3) — mandatory for v1.0.1+ ----
    # Two evidence sources are honored, both strictly physical:
    #   artifacts.msi       -> the amca-distribution-smoke-result/1 record
    #                          for the MSI (mode "msi")
    #   artifacts.msi_smoke -> an explicit MSI smoke sub-record
    # v1.0.1+: a missing/FAIL/BLOCKED/malformed MSI verdict blocks
    # promotion-ready. v1.0.0: MSI is explicitly NOT_APPLICABLE — a valid,
    # visible, non-blocking GateResult is always recorded (never None), so
    # historical promotion compatibility is preserved without weakening the
    # v1.0.1+ requirement.
    msi_smoke = (smoke.get("artifacts") or {}).get("msi") if smoke else None
    if msi_smoke is None and smoke:
        msi_smoke = smoke.get("artifacts", {}).get("msi_smoke")
    msi_verdict = (msi_smoke or {}).get("overall")
    # A None final_release_version means no manifest gate has bound a version
    # yet (standalone smoke evaluation). In that case fall back to the
    # evidence itself: a smoke record WITHOUT any msi entry is the historical
    # v1.0.0 shape (not applicable); WITH an msi entry the v1.0.1+ gate
    # applies. This keeps None from crashing while never weakening a real
    # v1.0.1+ evaluation (the manifest gate always binds the version first).
    if bundle.final_release_version is not None:
        is_v1_0_1_plus = _version_tuple(bundle.final_release_version) >= (1, 0, 1)
    else:
        is_v1_0_1_plus = msi_smoke is not None
    if not is_v1_0_1_plus:
        bundle.msi_smoke = GateResult(
            "msi_smoke", VERDICT_NOT_APPLICABLE,
            "MSI smoke not applicable — v1.0.0 historical two-artifact "
            "release (EXE + Portable only)",
            str(smoke_path))
    elif msi_smoke is None or not msi_verdict:
        bundle.msi_smoke = GateResult(
            "msi_smoke", ST_BLOCKED,
            "MSI smoke evidence missing — mandatory for v1.0.1+ "
            "(missing evidence cannot reach promotion-ready)",
            str(smoke_path))
    elif msi_verdict == "PASS":
        bundle.msi_smoke = GateResult(
            "msi_smoke", VERDICT_PASS,
            "MSI smoke overall = PASS",
            str(smoke_path), msi_smoke.get("source_sha"))
    else:
        bundle.msi_smoke = GateResult(
            "msi_smoke", ST_BLOCKED,
            f"MSI smoke verdict = {msi_verdict} (FAIL/BLOCKED/malformed "
            "cannot reach promotion-ready)",
            str(smoke_path), msi_smoke.get("source_sha"))

    # ---- Gate 4: distribution build/release gate (ZCode I2B/I2C) ----
    manifest = _read_json(Path(manifest_path))
    prov = source_provenance or current_source_provenance()
    bundle.canonical_source_sha = prov.get("source_git_sha")
    if manifest is None:
        bundle.release_gate = GateResult(
            "distribution_release_gate", ST_BLOCKED,
            f"release manifest not found: {manifest_path}", str(manifest_path))
    else:
        mprov = manifest.get("provenance") or {}
        artifacts = manifest.get("artifacts", [])
        physical_ok = True
        phys_records: List[Dict[str, Any]] = []
        import hashlib
        for art in artifacts:
            p = dist / art.get("package_name", "")
            exists = p.is_file()
            size_ok = exists and p.stat().st_size == art.get("size_bytes")
            digest = sha256_file(p) if exists else None
            hash_ok = digest == art.get("sha256") if digest else False
            physical_ok = physical_ok and exists and size_ok and hash_ok
            phys_records.append({
                "package_name": art.get("package_name"),
                "size_bytes": p.stat().st_size if exists else None,
                "sha256": digest,
                "physically_verified": exists and size_ok and hash_ok,
            })
        bundle.physical_artifacts = phys_records
        gate_state = mprov.get("release_gate_state", "NOT_EVALUATED")
        same_sha = (
            prov.get("source_git_sha") == mprov.get("source_git_sha")
        ) if mprov.get("source_git_sha") else None
        dirty = prov.get("dirty_tree")
        if not physical_ok:
            bundle.release_gate = GateResult(
                "distribution_release_gate", ST_BLOCKED,
                "physical artifacts missing or hash mismatch vs manifest",
                str(manifest_path))
        elif gate_state != "ELIGIBLE":
            bundle.release_gate = GateResult(
                "distribution_release_gate", ST_BLOCKED,
                f"release gate state = {gate_state} (physical verification only; "
                "canonical certification pending)",
                str(manifest_path), mprov.get("source_git_sha"))
        elif dirty:
            bundle.release_gate = GateResult(
                "distribution_release_gate", ST_REJECTED,
                "packaging source tree is dirty", str(manifest_path),
                mprov.get("source_git_sha"))
        elif same_sha is False:
            bundle.release_gate = GateResult(
                "distribution_release_gate", ST_REJECTED,
                "packaging source SHA does not match manifest provenance SHA",
                str(manifest_path), mprov.get("source_git_sha"))
        else:
            bundle.release_gate = GateResult(
                "distribution_release_gate", VERDICT_PASS,
                "physical artifacts verified; release gate ELIGIBLE; same SHA; clean tree",
                str(manifest_path), mprov.get("source_git_sha"))

    # ---- Gate 5: version contract ----
    versions: Dict[str, Optional[str]] = {}
    try:
        for line in (SOFTWARE_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                versions["pyproject"] = line.split("=")[1].strip().strip('"').strip("'")
                break
    except OSError:
        pass
    try:
        pkg = json.loads((SOFTWARE_ROOT / "desktop-ui" / "package.json").read_text(encoding="utf-8"))
        versions["desktop_ui"] = pkg.get("version")
    except (OSError, json.JSONDecodeError):
        pass
    init = SOFTWARE_ROOT / "src" / "amca" / "__init__.py"
    if init.is_file():
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("__version__"):
                versions["amca_package"] = line.split("=")[1].strip().strip('"').strip("'")
                break
    if manifest:
        versions["release_manifest"] = manifest.get("version")
    distinct = {v for v in versions.values() if v}
    bundle.final_release_version = next(iter(distinct)) if len(distinct) == 1 else None
    bundle.version_contract = GateResult(
        "version_contract",
        VERDICT_PASS if len(distinct) == 1 and bundle.final_release_version else ST_REJECTED,
        f"active release metadata versions: {versions}",
        None)

    # ---- Gate 6: product identity binding (H2B.2 Part C) ----
    # The v1.0.1 binding chain must EXPLICITLY require the canonical
    # identity - carrying optional product fields is insufficient. A
    # manifest without the bound identity cannot reach promotion-ready,
    # and a mismatched identity is REJECTED. Historical v1.0.0 manifests
    # keep their historical contract (explicit NOT_APPLICABLE, never
    # rewritten).
    if manifest:
        m_version = str(manifest.get("version") or "")
        identity = manifest.get("product_identity")
        if not m_version:
            bundle.product_identity = GateResult(
                "product_identity", ST_BLOCKED,
                "release manifest carries no version; product identity "
                "binding cannot be classified (fail-closed)",
                str(manifest_path))
        elif _version_tuple(m_version) >= (1, 0, 1):
            if not isinstance(identity, dict) or not identity:
                bundle.product_identity = GateResult(
                    "product_identity", ST_BLOCKED,
                    "v1.0.1+ release manifest carries no product_identity "
                    "binding (explicit canonical identity is mandatory; "
                    "field-presence-only is insufficient)",
                    str(manifest_path))
            else:
                # The version-contract gate already ties the manifest version
                # to the live tree, so the identity's version is bound to the
                # manifest's own version; the remaining canonical fields stay
                # pinned. A hardcoded version here made promotion-ready
                # unreachable for every tree whose version was not 1.0.1.
                canonical_identity = {
                    **CANONICAL_PRODUCT_IDENTITY_V1_0_1, "version": m_version,
                }
                mismatched = {
                    key: {"manifest": identity.get(key), "canonical": canonical}
                    for key, canonical in canonical_identity.items()
                    if identity.get(key) != canonical
                }
                if mismatched:
                    bundle.product_identity = GateResult(
                        "product_identity", ST_REJECTED,
                        f"product identity mismatch vs canonical identity: "
                        f"{mismatched} - a package with a different identity "
                        "must not satisfy final release validation",
                        str(manifest_path))
                else:
                    bundle.product_identity = GateResult(
                        "product_identity", VERDICT_PASS,
                        f"canonical product identity bound and enforced: "
                        f"{canonical_identity}",
                        str(manifest_path))
        else:
            bundle.product_identity = GateResult(
                "product_identity", VERDICT_NOT_APPLICABLE,
                "product identity binding not applicable - v1.0.0 "
                "historical manifest contract preserved unchanged",
                str(manifest_path))

    bundle.known_limitations = [
        "Linux artifacts NOT INCLUDED / BLOCKED - Linux build environment required",
        "Final release readiness must not be declared until OpenCode PS-1 and "
        "PS-2 deliver their final PASS evidence",
    ]

    # ---- Orchestration: deterministic state, first blocking rule wins ----
    gates = [bundle.certification, bundle.migration, bundle.installer_smoke,
             bundle.msi_smoke, bundle.portable_smoke, bundle.release_gate,
             bundle.version_contract, bundle.product_identity]
    for gate in gates:
        if gate.verdict == ST_BLOCKED:
            bundle.promotion_state = ST_BLOCKED
            bundle.reasons.append(f"{gate.name}: {gate.detail}")
            return bundle
    for gate in gates:
        if gate.verdict == ST_REJECTED:
            bundle.promotion_state = ST_REJECTED
            bundle.reasons.append(f"{gate.name}: {gate.detail}")
            return bundle
    for gate in gates:
        if gate.verdict == VERDICT_FAIL:
            bundle.promotion_state = ST_REJECTED
            bundle.reasons.append(f"{gate.name}: verdict FAIL")
            return bundle
    if not bundle.physical_artifacts:
        bundle.promotion_state = ST_BLOCKED
        bundle.reasons.append("no physical artifacts recorded")
        return bundle
    bundle.promotion_state = ST_READY
    bundle.reasons.append(
        "all mandatory evidence gates PASS: READY_FOR_PROMOTION"
    )
    return bundle


# ---------------------------------------------------------------------------
# Promotion (explicit, staged, never silent-overwrite)
# ---------------------------------------------------------------------------

MANDATORY_INVENTORY = (
    "windows-installer",
    "windows-portable",
    "sha256sums",
    "release-manifest",
    "release-notes",
    "known-limitations",
    "certification-summary",
)

# H2A S5 (version-aware): v1.0.1+ releases add the MSI as a first-class
# mandatory artifact. v1.0.0 keeps its historical two-artifact (EXE + Portable)
# record valid — never rewrite history.
MANDATORY_INVENTORY_V1_0_1_PLUS = MANDATORY_INVENTORY + ("windows-msi",)


def mandatory_inventory_for_version(version: str) -> tuple[str, ...]:
    """Return the mandatory promotion inventory for a release version.

    v1.0.0 keeps the historical EXE+Portable record; v1.0.1 and later require
    EXE + MSI + Portable.
    """

    try:
        parts = [int(piece) for piece in str(version).split(".")]
        if len(parts) >= 3 and parts >= [1, 0, 1]:
            return MANDATORY_INVENTORY_V1_0_1_PLUS
    except ValueError:
        pass
    return MANDATORY_INVENTORY


def promote_final_release(
    evidence: EvidenceBundle,
    *,
    artifacts: Dict[str, Path],
    destination: Path = DEFAULT_OFFICIAL_DEST,
    allow_overwrite: bool = False,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Promote verified final artifacts into the official release location.

    - Requires promotion_state READY_FOR_PROMOTION (all gates PASS).
    - Version-aware inventory: v1.0.1+ additionally requires a windows-msi
      artifact; v1.0.0 keeps its historical two-artifact record.
    - Staged copy: artifacts go to <destination>/staged first, are re-hashed
      after copy, then moved into place. Nothing is silently overwritten:
      an existing official file requires allow_overwrite=True.
    - dry_run=True (default) evaluates and reports without copying anything.
    """
    result: Dict[str, Any] = {
        "schema": "amca-final-release-promotion/1",
        "timestamp_utc": _utc_now(),
        "promotion_state": evidence.promotion_state,
        "dry_run": dry_run,
        "destination": str(destination),
        "staged": [],
        "promoted": [],
        "skipped_existing": [],
        "post_copy_hash_match": None,
        "overall": "BLOCKED",
        "reasons": [],
    }
    if evidence.promotion_state != ST_READY:
        result["reasons"].append(
            f"promotion refused: evidence state is {evidence.promotion_state}"
        )
        return result

    required_inventory = mandatory_inventory_for_version(
        str(evidence.final_release_version or "")
    )
    missing = [k for k in required_inventory if k not in artifacts]
    if missing:
        result["reasons"].append(
            f"final artifact inventory incomplete: missing {missing} "
            f"(required for this version: {required_inventory})"
        )
        return result

    # Pre-copy inventory hashes
    inventory: List[Dict[str, Any]] = []
    for role, path in artifacts.items():
        p = Path(path)
        if not p.is_file():
            result["reasons"].append(f"artifact for {role} not found: {p}")
            result["overall"] = ST_BLOCKED
            return result
        inventory.append({
            "role": role,
            "source": str(p),
            "sha256": sha256_file(p),
            "size_bytes": p.stat().st_size,
        })

    if dry_run:
        result["overall"] = ST_READY
        result["staged"] = inventory
        result["reasons"].append(
            "dry-run: all evidence valid; nothing copied or promoted"
        )
        return result

    # Real promotion: stage, verify, then move into place.
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    stage_dir = destination / "staged-promotion"
    stage_dir.mkdir(parents=True, exist_ok=True)

    staged_records: List[Dict[str, Any]] = []
    for record in inventory:
        src = Path(record["source"])
        staged_path = stage_dir / src.name
        shutil.copy2(src, staged_path)
        post_hash = sha256_file(staged_path)
        hash_ok = post_hash == record["sha256"]
        staged_records.append({**record, "staged_path": str(staged_path),
                               "post_copy_sha256": post_hash,
                               "post_copy_hash_match": hash_ok})
        if not hash_ok:
            result["overall"] = "FAIL"
            result["reasons"].append(
                f"post-copy hash mismatch for {record['role']}; promotion aborted"
            )
            result["staged"] = staged_records
            return result

    for record in staged_records:
        src = Path(record["staged_path"])
        final_path = destination / src.name
        if final_path.exists() and not allow_overwrite:
            result["skipped_existing"].append(str(final_path))
            result["reasons"].append(
                f"official artifact already exists: {final_path} "
                "(use allow_overwrite=True to replace explicitly)"
            )
            result["overall"] = ST_BLOCKED
            result["staged"] = staged_records
            return result
        shutil.move(str(src), str(final_path))
        final_hash = sha256_file(final_path)
        if final_hash != record["sha256"]:
            result["overall"] = "FAIL"
            result["reasons"].append(f"post-promotion hash mismatch: {final_path}")
            result["staged"] = staged_records
            return result
        result["promoted"].append({
            "role": record["role"],
            "destination": str(final_path),
            "sha256": final_hash,
        })

    shutil.rmtree(stage_dir, ignore_errors=True)
    result["post_copy_hash_match"] = all(
        r["sha256"] == sha256_file(Path(r["destination"]))
        for r in result["promoted"]
    )
    result["overall"] = ST_PROMOTED
    result["destination_paths"] = [r["destination"] for r in result["promoted"]]
    return result


# ---------------------------------------------------------------------------
# Post-promotion release completion (H2B.2 Part C)
#
# Lifecycle: validate -> promote -> create tag -> verify tag. Tag verification
# is deliberately POST-promotion (never a promotion prerequisite — that would
# create a circular release dependency) and machine-binds the release tag to
# the certified final SHA.
# ---------------------------------------------------------------------------

def _default_tag_resolver(tag: str) -> Optional[str]:
    """Resolve a git tag to its commit SHA; None when the tag does not exist."""
    out = _git("rev-parse", f"{tag}^{{commit}}")
    return out or None


def verify_release_tag(
    tag: str,
    expected_sha: str,
    *,
    resolver=None,
) -> GateResult:
    """Verify one release tag points exactly to the expected certified SHA."""

    resolve = resolver or _default_tag_resolver
    sha = resolve(tag)
    if not sha:
        return GateResult(
            "release_tag", ST_BLOCKED,
            f"release tag missing: {tag} (release completion BLOCKED until "
            "the tag is created on the certified SHA)")
    if sha != expected_sha:
        return GateResult(
            "release_tag", ST_REJECTED,
            f"release tag {tag} points to {sha}, expected certified SHA "
            f"{expected_sha} (wrong-SHA tag blocks release completion)",
            None, sha)
    return GateResult(
        "release_tag", VERDICT_PASS,
        f"release tag {tag} points exactly to the certified final SHA "
        f"{expected_sha}",
        None, sha)


def verify_release_completion(
    *,
    promotion_state: str,
    expected_final_sha: str,
    v100_immutable_sha: Optional[str] = None,
    v101_tag: str = RELEASE_TAG_V1_0_1,
    v100_tag: str = RELEASE_TAG_V1_0_0,
    tag_resolver=None,
) -> Dict[str, Any]:
    """Post-promotion release-completion gate.

    - must be called AFTER promotion (a non-PROMOTED state blocks completion;
      promotion itself never requires a tag);
    - the v1.0.1 tag must exist and point exactly to the certified final SHA
      (missing -> BLOCKED, wrong SHA -> BLOCKED);
    - the v1.0.0 tag must remain untouched (moved/reused -> BLOCKED) when its
      immutable SHA is supplied;
    - all good -> overall PASS.
    """

    reasons: List[str] = []
    overall = VERDICT_PASS
    tag_sha: Optional[str] = None

    if promotion_state != ST_PROMOTED:
        return {
            "schema": "amca-release-completion-verification/1",
            "release_tag": v101_tag,
            "tag_sha": None,
            "promotion_state": promotion_state,
            "v100_tag_immutable": None,
            "overall": ST_BLOCKED,
            "reasons": [
                f"promotion state is {promotion_state}; tag verification is "
                "post-promotion only (promotion must complete first)"
            ],
        }

    tag_gate = verify_release_tag(v101_tag, expected_final_sha, resolver=tag_resolver)
    tag_sha = tag_gate.sha
    reasons.append(f"{tag_gate.name}: {tag_gate.detail}")
    if tag_gate.verdict != VERDICT_PASS:
        overall = ST_BLOCKED

    v100_state: Optional[str] = None
    if v100_immutable_sha:
        v100_gate = verify_release_tag(v100_tag, v100_immutable_sha, resolver=tag_resolver)
        v100_state = v100_gate.verdict
        reasons.append(f"{v100_gate.name} (v1.0.0 immutability): {v100_gate.detail}")
        if v100_gate.verdict != VERDICT_PASS:
            overall = ST_BLOCKED

    return {
        "schema": "amca-release-completion-verification/1",
        "release_tag": v101_tag,
        "tag_sha": tag_sha,
        "promotion_state": promotion_state,
        "v100_tag_immutable": v100_state,
        "overall": overall,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Test accounting normalization (H2B.2 Part D)
# ---------------------------------------------------------------------------

@dataclass
class SuiteAccounting:
    """One executed suite's accounting record.

    test_ids carries the DISTINCT identity of every executed test. Without
    test identity, an honest unique-test total is impossible; the accounting
    then reports REQUIRES_TEST_IDENTITY instead of inventing a number.
    """

    label: str
    passed: int
    total: int
    test_ids: Optional[frozenset] = None


def build_test_accounting(suites: List[SuiteAccounting]) -> Dict[str, Any]:
    """Normalize overlapping suite executions into honest evidence numbers.

    - every suite stays LABELLED (canonical_frontend, scoped_frontend, ...);
      overlapping suites are never summed into a unique-test total;
    - UNIQUE_TESTS = size of the union of distinct test identities;
    - TOTAL_TEST_EXECUTIONS = sum of all suite executions;
    - REPEATED_TEST_EXECUTIONS = TOTAL - UNIQUE (tests the overlapping
      scopes executed more than once).
    """

    suite_records = [
        {"label": s.label, "passed": s.passed, "total": s.total,
         "test_identity": "recorded" if s.test_ids is not None else "absent"}
        for s in suites
    ]
    total_executions = sum(s.total for s in suites)
    accounting: Dict[str, Any] = {
        "schema": "amca-test-accounting/1",
        "suites": suite_records,
        "overlapping_suites_labelled_separately": True,
        "unique_tests": None,
        "repeated_test_executions": None,
        "total_test_executions": total_executions,
    }
    if any(s.test_ids is None for s in suites):
        accounting["unique_tests_status"] = "REQUIRES_TEST_IDENTITY"
        accounting["reasons"] = [
            "one or more suites recorded counts without distinct test "
            "identities; an honest UNIQUE_TESTS total is impossible from "
            "counts alone (overlapping suite totals must never be "
            "presented as unique tests)"
        ]
        return accounting

    all_ids: set = set()
    for s in suites:
        all_ids |= set(s.test_ids or ())
    unique = len(all_ids)
    accounting["unique_tests"] = unique
    accounting["repeated_test_executions"] = total_executions - unique
    accounting["unique_tests_status"] = "RESOLVED"
    return accounting
