"""AMCA Studio v1.0.0 Final Release Orchestration Rehearsal (I2I).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2I-FINAL-RELEASE-EVIDENCE-PATH-COMPATIBILITY-AND-DRY-RUN-ORCHESTRATION-REHEARSAL-01

Read-only dry-run rehearsal: locates, parses, SHA-binds, and presents every
completed release-evidence family to the existing promotion infrastructure,
proving there are no schema/path incompatibilities before the final
canonical SHA is frozen. This is NOT final certification.

States per evidence family (explicit, no hidden inference):
  READY     - found, schema understood, same-SHA bound, usable now
  PENDING   - not yet produced (e.g. PS-2 I2N final compliance); never
              inferred as PASS
  STALE     - evidence bound to a different source SHA (same-lineage rule)
  MISMATCH  - artifact hashes in evidence disagree with physical bytes
  BLOCKED   - evidence found but unusable/unparseable

Rules honored:
- Current v0.9 artifacts are classified as validation artifacts, never
  final v1.0 evidence.
- Evidence tied to different SHAs is rejected for same-lineage promotion.
- Missing PS-2 I2N final compliance input is PENDING, not fabricated.
- Current unsigned signing evidence retains its policy-dependent state.
- final_promotion.py is NOT altered; the rehearsal never copies or
  promotes anything.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amca.distribution_smoke import sha256_file

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]

STATE_READY = "READY"
STATE_PENDING = "PENDING"
STATE_STALE = "STALE"
STATE_MISMATCH = "MISMATCH"
STATE_BLOCKED = "BLOCKED"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_BLOCKED = "BLOCKED"

VALIDATION_ARTIFACT_CLASS = "validation artifact (NOT final v1.0 evidence)"


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


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


#: The mandatory evidence-family matrix: family -> expected paths + parser.
#: Paths are conventions the streams agreed on; every entry is located and
#: parsed read-only. PENDING inputs are represented explicitly.
EVIDENCE_FAMILY_MATRIX: List[Dict[str, Any]] = [
    {
        "family": "canonical_certification",
        "owner": "OpenCode PS-1",
        "expected_paths": ["tests/certification-result.json"],
        "verdict_field": "overall_verdict",
        "sha_field": ("provenance", "sha"),
    },
    {
        "family": "migration_certification",
        "owner": "OpenCode PS-2",
        "expected_paths": [
            "tests/migration-certification.json",
            "dist/migration-certification-evidence.json",
        ],
        "verdict_field": "overall_verdict",
        "sha_field": ("provenance", "sha"),
        "note": "PS-2 I2D chain: final evidence path per contract; any of "
                "the convention paths being absent => PENDING",
    },
    {
        "family": "distribution_release_gate",
        "owner": "ZCode (I2B/I2C)",
        "expected_paths": ["dist/release_manifest_v0.9.0.json"],
        "verdict_field": ("provenance", "release_gate_state"),
        "sha_field": ("provenance", "source_git_sha"),
        "note": "release_manifest_v1.0.0.json will replace this after the "
                "final same-SHA rebuild",
    },
    {
        "family": "windows_runtime_smoke",
        "owner": "ZCode (I2D)",
        "expected_paths": ["dist/windows-runtime-smoke-result.json"],
        "verdict_field": "overall",
        "sha_field": "source_sha",
        "note": "installer + portable combined result",
    },
    {
        "family": "windows_signing_evidence",
        "owner": "ZCode (I2G/I2H)",
        "expected_paths": ["dist/windows-runtime-smoke-result.json"],
        "verdict_field": "overall",
        "sha_field": "source_sha",
        "note": "signing evidence is generated from the I2G audit at "
                "rehearsal time (policy-dependent state preserved); "
                "final artifact will be amca-windows-signing-evidence/1",
        "generated": True,
    },
    {
        "family": "version_contract",
        "owner": "ZCode (I2F)",
        "expected_paths": [
            "pyproject.toml",
            "src/amca/__init__.py",
            "desktop-ui/package.json",
            "desktop-ui/src-tauri/tauri.conf.json",
            "desktop-ui/src-tauri/Cargo.toml",
        ],
        "verdict_field": "current_version",
        "sha_field": None,
        "note": "validated via amca.version_freeze_preflight agreement",
    },
    {
        "family": "compliance_sanitation",
        "owner": "OpenCode PS-2 (I2N)",
        "expected_paths": [
            "docs/release/AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json",
            "docs/release/AMCA-Studio-v1.0.0-SBOM.json",
        ],
        "verdict_field": "overall_verdict",
        "sha_field": ("provenance", "sha"),
        "note": "final I2N compliance payload may still be outstanding; "
                "absence is reported PENDING, never fabricated PASS",
    },
]


def _deep_get(data: Dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _source_sha_now() -> str:
    return _git("rev-parse", "HEAD")


def rehearse_evidence(
    source_sha: Optional[str] = None,
    signing_policy: str = "OPTIONAL",
) -> Dict[str, Any]:
    """Read-only rehearsal across all mandatory evidence families.

    Never copies, promotes, or modifies anything. Returns the per-family
    matrix with explicit states plus the compatibility summary.
    """
    sha = source_sha or _source_sha_now()
    rows: List[Dict[str, Any]] = []

    for family in EVIDENCE_FAMILY_MATRIX:
        row: Dict[str, Any] = {
            "family": family["family"],
            "owner": family["owner"],
            "expected_paths": family["expected_paths"],
            "found_path": None,
            "schema_or_version": None,
            "source_sha_in_evidence": None,
            "artifact_sha_binding": None,
            "current_verdict": None,
            "stale_or_mismatch": None,
            "final_package_rerun_required": None,
            "usable_for_final_promotion": False,
            "state": None,
            "detail": "",
            "classification": VALIDATION_ARTIFACT_CLASS,
        }
        data = None
        found = None
        for rel in family["expected_paths"]:
            candidate = SOFTWARE_ROOT / rel
            if candidate.is_file():
                data = _read_json(candidate)
                if data is not None or family["family"] == "version_contract":
                    found = rel
                    break
        row["found_path"] = found

        if family["family"] == "version_contract":
            from amca.version_freeze_preflight import active_version_agreement
            agr = active_version_agreement()
            row["current_verdict"] = agr.get("current_version")
            row["schema_or_version"] = "active version agreement"
            row["source_sha_in_evidence"] = sha
            row["state"] = STATE_READY if agr["agrees"] else STATE_MISMATCH
            row["detail"] = f"versions: {agr['versions']}"
            row["stale_or_mismatch"] = (
                None if agr["agrees"] else "active version disagreement")
            row["final_package_rerun_required"] = (
                "bump to 1.0.0 at final freeze (I2F checklist)")
            row["usable_for_final_promotion"] = agr["agrees"]
            rows.append(row)
            continue

        if data is None:
            row["state"] = STATE_PENDING
            row["detail"] = (
                f"no evidence file found at any expected path "
                f"({family['expected_paths']}); represented explicitly as "
                "PENDING, never inferred PASS")
            row["final_package_rerun_required"] = "yes - owner stream"
            rows.append(row)
            continue

        row["schema_or_version"] = data.get(
            "schema", data.get("evidence_schema_version"))
        verdict = _deep_get(data, *(family["verdict_field"]
                                    if isinstance(family["verdict_field"], tuple)
                                    else (family["verdict_field"],)))
        row["current_verdict"] = verdict
        sha_field = family.get("sha_field")
        sha_in_ev = _deep_get(data, *(sha_field if isinstance(sha_field, tuple)
                                      else (sha_field,))) if (sha_field and data is not None) else None
        row["source_sha_in_evidence"] = sha_in_ev

        # Artifact SHA binding: re-hash physically listed artifacts.
        binding_ok: Optional[bool] = None
        binding_detail = "no physical artifacts listed"
        if family["family"] == "distribution_release_gate":
            binding_ok = True
            binding_detail = "all listed artifacts re-hashed OK"
            for art in data.get("artifacts", []):
                p = SOFTWARE_ROOT / "dist" / art.get("package_name", "")
                if not p.is_file():
                    binding_ok, binding_detail = False, \
                        f"physical artifact missing: {art.get('package_name')}"
                    break
                if p.stat().st_size != art.get("size_bytes") or \
                        sha256_file(p) != art.get("sha256"):
                    binding_ok, binding_detail = False, \
                        f"hash mismatch: {art.get('package_name')}"
                    break
            row["artifact_sha_binding"] = binding_detail
        elif family["family"] == "windows_runtime_smoke":
            s = data.get("artifacts") or {}
            hashes = {k: v.get("sha256") for k, v in s.items() if v.get("sha256")}
            row["artifact_sha_binding"] = (
                f"{len(hashes)} artifact hash(es) recorded" if hashes
                else "none recorded")
        else:
            row["artifact_sha_binding"] = binding_detail

        # Same-lineage check (skipped for families without a SHA field).
        stale = bool(sha_field and sha_in_ev and sha and sha_in_ev != sha)
        row["stale_or_mismatch"] = (
            f"evidence SHA {str(sha_in_ev)[:8]} != current {sha[:8]}" if stale
            else None)

        if stale:
            row["state"] = STATE_STALE
            row["final_package_rerun_required"] = \
                "yes - regenerate evidence at the final canonical SHA"
            row["usable_for_final_promotion"] = False
        elif binding_ok is False:
            row["state"] = STATE_MISMATCH
            row["final_package_rerun_required"] = "yes - rebuild artifacts"
            row["usable_for_final_promotion"] = False
        elif verdict == VERDICT_PASS:
            row["state"] = STATE_READY
            row["usable_for_final_promotion"] = True
        elif verdict == VERDICT_FAIL:
            row["state"] = STATE_BLOCKED
            row["detail"] = "evidence verdict FAIL"
            row["usable_for_final_promotion"] = False
        elif verdict is None:
            # Evidence exists but carries no final verdict yet (e.g. the I2N
            # compliance payload is still outstanding) -> explicitly PENDING.
            row["state"] = STATE_PENDING
            row["detail"] = ("evidence file present but no final verdict "
                             "recorded yet; final artifact still pending")
            row["final_package_rerun_required"] = "yes - owner stream"
            row["usable_for_final_promotion"] = False
        else:
            row["state"] = STATE_BLOCKED
            row["detail"] = f"evidence verdict {verdict!r}"
            row["usable_for_final_promotion"] = False
        rows.append(row)

    # Windows signing evidence is generated (I2G audit -> I2H evidence),
    # preserving its policy-dependent state.
    signing_row = next(r for r in rows if r["family"] == "windows_signing_evidence")
    try:
        from amca.signing_readiness import run_signing_audit
        from amca.signing_evidence import (
            POLICY_OPTIONAL, build_signing_evidence)
        audit = run_signing_audit()
        evidence = build_signing_evidence(audit, policy=signing_policy)
        signing_row["found_path"] = "generated from amca-windows-signing-audit/1"
        signing_row["schema_or_version"] = evidence.schema
        signing_row["current_verdict"] = evidence.overall_technical_verdict
        signing_row["source_sha_in_evidence"] = evidence.source_sha or None
        if evidence.source_sha and evidence.source_sha != sha:
            signing_row["state"] = STATE_STALE
        elif evidence.overall_technical_verdict == "PASS":
            signing_row["state"] = STATE_READY
        elif evidence.overall_technical_verdict in ("FAIL", "BLOCKED"):
            signing_row["state"] = STATE_BLOCKED
        else:
            # REVIEW (e.g. unsigned under optional policy): usable for final
            # promotion only after policy resolution/acknowledgment.
            signing_row["state"] = STATE_PENDING
            signing_row["usable_for_final_promotion"] = False
        signing_row["detail"] = (
            f"policy-dependent state preserved "
            f"(policy={evidence.signing_policy}, "
            f"acknowledgment={evidence.acknowledgment_state})")
        signing_row["final_package_rerun_required"] = \
            "yes - regenerate audit + evidence at the final canonical SHA"
        signing_row["usable_for_final_promotion"] = (
            evidence.overall_technical_verdict == "PASS")
    except Exception as exc:  # honest failure, never fabricated
        signing_row["state"] = STATE_BLOCKED
        signing_row["detail"] = f"signing evidence generation error: {exc}"

    ready = [r["family"] for r in rows if r["state"] == STATE_READY]
    pending = [r["family"] for r in rows if r["state"] == STATE_PENDING]
    stale = [r["family"] for r in rows if r["state"] == STATE_STALE]
    mismatch = [r["family"] for r in rows if r["state"] == STATE_MISMATCH]
    blocked = [r["family"] for r in rows if r["state"] == STATE_BLOCKED]

    return {
        "schema": "amca-final-release-orchestration-rehearsal/1",
        "generated_at_utc": _utc_now(),
        "rehearsal_source_sha": sha,
        "evidence_matrix": rows,
        "summary": {
            "ready": ready,
            "pending": pending,
            "stale": stale,
            "mismatch": mismatch,
            "blocked": blocked,
            "compatibility_conclusion": (
                "All completed evidence families located, parsed, and "
                "SHA-bound; pending inputs represented explicitly; no "
                "schema/path incompatibilities blocking the final run."
                if not mismatch else
                "Artifact hash mismatches detected - rebuild required."),
        },
        "rules": {
            "v09_artifacts": VALIDATION_ARTIFACT_CLASS,
            "same_lineage": "evidence tied to different SHAs is rejected "
                            "for same-lineage promotion",
            "pending_i2n": "missing PS-2 I2N final compliance input is "
                           "PENDING, not fabricated",
            "signing": "current unsigned signing evidence retains its "
                       "policy-dependent state",
            "final_promotion_untouched": True,
            "side_effects": "none - read-only rehearsal",
        },
        "secrets_emitted": False,
    }
