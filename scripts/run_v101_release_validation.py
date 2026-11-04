#!/usr/bin/env python3
"""H1H v1.0.1 real-artifact validation orchestrator + fail-fast gate.

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1H-REAL-ARTIFACT-VALIDATION-ORCHESTRATOR-AND-FAILFAST-GATE-01
PS-2 tooling only. Builds nothing, installs nothing, promotes nothing,
tags nothing. Does not modify ZCode runtime/build logic and does not
merge into ZCode's active H1.1 branch.

One deterministic command that consumes the final v1.0.1 release inputs
and ORCHESTRATES (never reimplements) the accepted PS-2 validators in
order:

  1 three-artifact binding      v101_artifact_binding.validate_artifact_binding
  2 release manifest            v101_artifact_binding.validate_release_manifest
  3 SHA256SUMS                  v101_artifact_binding.validate_sha256sums
  4 MSI execution contract      v101_artifact_binding.msi_validation_contract
  5 migration/upgrade evidence  generate_migration_certification_evidence.validate_upgrade_path_evidence
  6 I2O final package compliance run_final_package_compliance_verification.run_verification
  7 I2R aggregate               run_ps2_final_release_validation.build_aggregate
  8 I2U promotion input         build_ps2_promotion_input.validate_promotion_input
  9 I2S shipping export         export_final_release_documentation.build_artifact_bindings
 10 final readiness aggregate   (this module)

Fail-fast BEFORE any expensive step rejects: missing artifact; wrong
extension/role; EXE without MZ; MSI without OLE; ZIP without PK;
v1.0.0 historical hashes; version != 1.0.1; source-SHA disagreement;
C: paths; missing MSI execution record; MODEL_RESPONSE_RECEIVED != YES;
mocked/replayed physical evidence; release manifest missing any role.
Any failure stops downstream validation with exact blockers.

Machine-readable output fields:
  V101_FAILFAST_GATE, V101_THREE_ARTIFACT_BINDING, V101_MSI_EXECUTION,
  V101_MODEL_PHYSICAL_EVIDENCE, V101_PACKAGE_VALIDATION,
  V101_PROMOTION_INPUT + blockers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import v101_artifact_binding as ab  # noqa: E402
import build_ps2_promotion_input as pi  # noqa: E402
import generate_migration_certification_evidence as mc  # noqa: E402

SCHEMA = "amca-v101-release-validation-run/1"
TARGET_VERSION = "1.0.1"
CANONICAL_PREFIXES = ("D:/", "d:/")

MAGIC = {"exe": b"MZ", "msi": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
         "portable": b"PK"}
SUFFIX = {"exe": ".exe", "msi": ".msi", "portable": ".zip"}
MOCK_MARKERS = ("mock", "replay", "test_adapter", "fake", "stub")


def _norm(path: str) -> str:
    return str(path).replace("\\", "/")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head(path: Path, n: int = 8) -> bytes:
    with path.open("rb") as handle:
        return handle.read(n)


def normalize_model_evidence(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Adapter for physical-use evidence. Accepts the current report
    format and is designed to consume PS-1's future H1A.3 format without
    modification: unknown fields pass through; the required keys are
    read by name. Mocked/test-adapter/replayed evidence is never
    normalized into a PASS."""
    out = dict(doc)
    out["model_response_received"] = str(
        doc.get("MODEL_RESPONSE_RECEIVED", doc.get(
            "model_response_received", ""))).upper()
    out["mocked"] = bool(doc.get("mocked", False)) or any(
        m in str(doc.get(k, "")).casefold()
        for k in ("source", "provider", "adapter", "evidence_source",
                  "transport")
        for m in MOCK_MARKERS)
    return out


# H2C.2A Part A: builds whose evidence can no longer certify the current
# candidate. The general rule (source/artifact identity must match the
# candidate) covers every case; this set names the already-superseded build
# so the rejection reason is explicit.
SUPERSEDED_SOURCE_SHAS = {
    "405c3dfe6fb6ccafca6cedde4528c01efadf83e0",
}


def validate_model_evidence_build_bound(
    model_doc: Dict[str, Any],
    expected_source_sha: str,
    expected_artifact_hashes: Dict[str, str],
) -> Tuple[str, List[str]]:
    """H2C.2A Part A: real-model evidence must be bound to the EXACT
    candidate. Rejects (fail-closed): missing source/artifact/run identity,
    source SHA mismatch, evidence from a superseded build, artifact hash
    mismatch, absent readiness freshness, and ambiguous or positive
    mock/replay state."""
    reasons: List[str] = []
    doc = model_doc or {}
    sha = str(doc.get("source_sha", "")).lower()
    if not sha:
        reasons.append("model evidence missing source_sha (build identity)")
    elif sha in SUPERSEDED_SOURCE_SHAS:
        reasons.append(
            f"model evidence belongs to the superseded build {sha[:12]}")
    elif expected_source_sha and sha != str(expected_source_sha).lower():
        reasons.append(
            f"model evidence source SHA {sha[:12]} != candidate "
            f"{str(expected_source_sha)[:12]}")
    binding = doc.get("artifact_sha256")
    if not isinstance(binding, dict) or not binding:
        reasons.append(
            "model evidence missing artifact_sha256 binding (exe/msi/portable "
            "hashes of the exact candidate required)")
    else:
        for role, want in (expected_artifact_hashes or {}).items():
            got = str(binding.get(role, "")).lower()
            want_l = str(want).lower()
            if want_l and got != want_l:
                reasons.append(
                    f"model evidence artifact hash mismatch ({role}): "
                    f"{got[:12]} != candidate {want_l[:12]}")
    for field in ("run_id", "model_id", "ollama_models_path"):
        if not doc.get(field):
            reasons.append(f"model evidence missing {field} (run/model/path identity)")
    if str(doc.get("fresh_readiness_verified", "")).lower() not in ("true", "yes"):
        reasons.append(
            "fresh_readiness_verified not true (stale/inherited readiness "
            "rejected)")
    mrr = str(doc.get("model_response_received",
                      doc.get("MODEL_RESPONSE_RECEIVED", ""))).upper()
    if mrr != "YES":
        reasons.append("MODEL_RESPONSE_RECEIVED != YES")
    rsc = str(doc.get("run_status_complete",
                      doc.get("RUN_STATUS_COMPLETE", ""))).upper()
    if rsc not in ("YES", "TRUE"):
        reasons.append("RUN_STATUS_COMPLETE not complete")
    mor = doc.get("mock_or_replay_used", doc.get("MOCK_OR_REPLAY_USED"))
    if mor is None or str(mor).strip().upper() not in ("NO", "FALSE"):
        reasons.append("mock/replay state ambiguous or positive")
    if doc.get("mocked"):
        reasons.append("mocked/replayed evidence rejected")
    return ("PASS" if not reasons else "BLOCKED"), reasons


def fail_fast_gate(inputs: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Cheap, deterministic rejections before any expensive validation."""
    blockers: List[str] = []
    if str(inputs.get("release_version", TARGET_VERSION)) != TARGET_VERSION:
        blockers.append(
            f"release version {inputs.get('release_version')!r} != "
            f"{TARGET_VERSION!r}")
    arts = inputs.get("artifacts") or {}
    for role in ("exe", "msi", "portable"):
        entry = arts.get(role)
        if entry is None:
            blockers.append(f"missing artifact input: {role.upper() if role == 'msi' else role}")
            continue
        path = Path(_norm(entry.get("path", "")))
        norm = _norm(str(path))
        if not norm.startswith(CANONICAL_PREFIXES):
            blockers.append(
                f"{role}: C:-drive/other path rejected; canonical root is "
                f"D:\\ChatGPT\\AMCA (got {norm!r})")
            continue
        if not norm.lower().endswith(SUFFIX[role]):
            blockers.append(f"{role}: wrong extension for role {SUFFIX[role]}")
            continue
        if not path.is_file():
            blockers.append(f"{role}: artifact file missing: {norm}")
            continue
        declared = str(entry.get("sha256", "")).lower()
        if declared in ab.V100_KNOWN_HASHES:
            blockers.append(
                f"{role}: historical v1.0.0 artifact bytes cannot be v1.0.1 "
                "evidence")
            continue
        head = _head(path)
        if not head.startswith(MAGIC[role]):
            blockers.append(
                f"{role}: file magic is not a real {role.upper()} signature "
                "(role substitution/fake artifact rejected)")
            continue
        actual = _sha256(path)
        if declared != actual:
            blockers.append(
                f"{role}: declared sha256 != physical bytes")
            continue
    manifest = inputs.get("release_manifest") or {}
    msha = str(manifest.get("provenance", {}).get(
        "source_git_sha", manifest.get("source_git_sha", "")))
    if msha != str(inputs.get("source_sha", "")):
        blockers.append(
            "source SHA disagreement: release manifest bound to "
            f"{msha[:12]!r}, certified input {str(inputs.get('source_sha', ''))[:12]!r}")
    mig = inputs.get("migration_evidence") or {}
    if mig and str(mig.get("source_sha", "")) != str(
            inputs.get("source_sha", "")):
        blockers.append("source SHA disagreement in migration evidence")
    record = inputs.get("msi_execution")
    if record is None:
        blockers.append(
            "missing MSI execution-evidence record "
            "(MSI_EXECUTION_PENDING is not promotable)")
    elif str(record.get("state", "")).upper() == mc.MSI_PENDING_STATE:
        blockers.append("MSI execution still MSI_EXECUTION_PENDING")
    model = normalize_model_evidence(inputs.get("model_evidence") or {})
    if model["model_response_received"] != "YES":
        blockers.append("MODEL_RESPONSE_RECEIVED != YES")
    if model["mocked"]:
        blockers.append(
            "mocked/replayed/test-adapter physical evidence rejected")
    # H2C.2A Part A: build-bound anti-replay check on the real-model
    # evidence (superseded builds, wrong SHAs, missing identity, ambiguous
    # mock state all rejected).
    bound_v, bound_r = validate_model_evidence_build_bound(
        inputs.get("model_evidence") or {}, str(inputs.get("source_sha", "")),
        {role: str(e.get("sha256", ""))
         for role, e in (inputs.get("artifacts") or {}).items()})
    if bound_v != "PASS":
        blockers.extend(f"build-bound model evidence: {r}" for r in bound_r)
    roles_in_manifest = {
        ("exe" if str(a.get("package_name", "")).lower().endswith(".exe")
         else "msi" if str(a.get("package_name", "")).lower().endswith(".msi")
         else "portable")
        for a in manifest.get("artifacts", [])}
    for role in ("exe", "msi", "portable"):
        if role not in roles_in_manifest:
            blockers.append(
                f"release manifest missing artifact role: "
                f"{role.upper() if role == 'msi' else role}")
    if blockers:
        return "BLOCKED", blockers
    return "PASS", ["fail-fast gate satisfied"]


def _binding_doc(inputs: Dict[str, Any]) -> Dict[str, Any]:
    arts = {}
    for role, entry in (inputs.get("artifacts") or {}).items():
        e = dict(entry)
        e["path"] = _norm(e.get("path", ""))
        e["version"] = TARGET_VERSION
        p = Path(e["path"])
        if "size_bytes" not in e and p.is_file():
            e["size_bytes"] = p.stat().st_size
        arts[role] = e
    return {"schema": ab.SCHEMA, "release_version": TARGET_VERSION,
            "source_sha": inputs.get("source_sha"), "artifacts": arts}


def default_steps(inputs: Dict[str, Any]) -> Dict[str, Callable]:
    """Real adapters over the accepted validators (orchestration only)."""

    def binding():
        return ab.validate_artifact_binding(_binding_doc(inputs),
                                            verify_files=True)

    def release_manifest():
        return ab.validate_release_manifest(
            inputs.get("release_manifest") or {}, _binding_doc(inputs))

    def sha256sums():
        sums = inputs.get("sha256sums_text")
        if sums is None and inputs.get("sha256sums_path"):
            sums = Path(_norm(inputs["sha256sums_path"])).read_text(
                encoding="utf-8")
        if sums is None:
            return "BLOCKED", ["SHA256SUMS input not supplied"]
        return ab.validate_sha256sums(sums, _binding_doc(inputs))

    def msi_execution():
        record = inputs.get("msi_execution") or {}
        required = set(ab.MSI_CONTRACT_FIELDS)
        missing = sorted(f for f in required if f not in record)
        if missing:
            return "BLOCKED", [
                f"MSI execution record missing contract fields: {missing}"]
        msi_entry = (inputs.get("artifacts") or {}).get("msi") or {}
        if str(record.get("msi_sha256")) != str(msi_entry.get("sha256")):
            return "BLOCKED", [
                "MSI execution record bound to different bytes"]
        for key in ("install_result", "silent_install_result",
                    "uninstall_result"):
            if str(record.get(key, "")).upper() not in ("PASS", "0"):
                return "BLOCKED", [f"MSI execution {key} not PASS"]
        return "PASS", ["MSI execution contract satisfied"]

    def migration():
        mig = inputs.get("migration_evidence")
        if mig is None:
            return "BLOCKED", ["migration/upgrade evidence not supplied"]
        return mc.validate_upgrade_path_evidence(mig)

    def i2o():
        fv = _load("run_final_package_compliance_verification.py")
        a = inputs.get("artifacts") or {}
        result = fv.run_verification(
            Path(_norm(a["portable"]["path"])) if "portable" in a else None,
            Path(_norm(a["exe"]["path"])) if "exe" in a else None,
            inputs.get("source_sha"),
            **({"evidence_path": Path(_norm(inputs["compliance_evidence"]))}
               if inputs.get("compliance_evidence") else {}),
            msi=Path(_norm(a["msi"]["path"])) if "msi" in a else None)
        v = result["overall"]
        return ("PASS" if v in ("PASS", "PASS_WITH_REVIEW") else "BLOCKED"
                ), [f"I2O {v}"] + result["fail_findings"] \
            + result["blocked_findings"]

    def i2r():
        ag = _load("run_ps2_final_release_validation.py")
        doc = ag.build_aggregate(
            portable=Path(_norm((inputs["artifacts"]["portable"]["path"]))),
            installer=Path(_norm((inputs["artifacts"]["exe"]["path"]))),
            source_sha=inputs.get("source_sha"),
            msi=Path(_norm((inputs["artifacts"]["msi"]["path"]))))
        v = doc["aggregate_verdict"]
        return ("PASS" if v in ("PS2_RELEASE_READY",
                                "PS2_RELEASE_READY_WITH_REVIEW")
                else "BLOCKED"), [f"I2R {v}"] + doc["blockers"]

    def i2u():
        if not (inputs.get("aggregate_path")
                and inputs.get("provenance_path")):
            return "BLOCKED", [
                "I2U requires --aggregate and --provenance evidence paths"]
        doc = pi.build_promotion_input(
            Path(_norm(inputs["aggregate_path"])),
            Path(_norm(inputs["provenance_path"])),
            expected_source_sha=inputs.get("source_sha"),
            expected_installer_sha=str(
                (inputs["artifacts"]["exe"]).get("sha256")),
            expected_msi_sha=str((inputs["artifacts"]["msi"]).get("sha256")),
            expected_portable_sha=str(
                (inputs["artifacts"]["portable"]).get("sha256")),
            release_version=TARGET_VERSION)
        verdict, reasons = pi.validate_promotion_input(doc)
        return ("PASS" if verdict in ("PS2_INPUT_READY",
                                      "PS2_INPUT_READY_WITH_REVIEW")
                else "BLOCKED"), [f"I2U {verdict}"] + list(reasons)

    def i2s():
        ex = _load("export_final_release_documentation.py")
        a = inputs.get("artifacts") or {}
        bindings, reasons = ex.build_artifact_bindings(
            Path(_norm(a["exe"]["path"])), Path(_norm(a["msi"]["path"])),
            Path(_norm(a["portable"]["path"])), TARGET_VERSION)
        return ("PASS" if not reasons else "BLOCKED"), reasons or \
            [f"I2S binds all three artifacts ({len(bindings)})"]

    def cert_migration_crosscheck():
        """H2C.2A Part B: certification/migration/source agreement is
        machine-enforced. Individually-PASS documents that disagree
        on candidate identity are rejected."""
        reasons = []
        expected = str(inputs.get("source_sha", "")).lower()
        if not expected:
            return "BLOCKED", ["candidate/source identity missing"]
        mig = inputs.get("migration_evidence") or {}
        mig_sha = str(mig.get("source_sha", "")).lower()
        if not mig_sha:
            reasons.append("migration evidence missing source identity")
        elif mig_sha != expected:
            reasons.append(
                f"migration evidence bound to {mig_sha[:12]}, candidate "
                f"is {expected[:12]} (superseded/mismatched)")
        for role in ("exe", "msi", "portable"):
            art = ((mig.get("paths") or {}).get(role) or {}).get("artifact") or {}
            want = str(((inputs.get("artifacts") or {}).get(role) or {}).get("sha256", "")).lower()
            got = str(art.get("sha256", "")).lower()
            if want and got and got != want:
                reasons.append(
                    f"migration evidence {role} artifact hash "
                    f"{got[:12]} != candidate {want[:12]} (superseded set)")
        agg = inputs.get("aggregate") or {}
        agg_sha = str(agg.get("source_sha", "")).lower()
        if agg_sha and agg_sha != expected:
            reasons.append(
                f"certification aggregate bound to {agg_sha[:12]} != "
                f"candidate {expected[:12]}")
        for role in ("exe", "msi", "portable"):
            got = str((((agg.get("artifact_hashes") or {}).get(role) or {}).get("sha256", ""))).lower()
            want = str(((inputs.get("artifacts") or {}).get(role) or {}).get("sha256", "")).lower()
            if got and want and got != want:
                reasons.append(
                    f"certification aggregate {role} hash {got[:12]} != "
                    f"candidate {want[:12]}")
        cert = inputs.get("certification") or {}
        cert_sha = str((cert.get("provenance") or {}).get("sha", cert.get("certified_sha", ""))).lower()
        if cert_sha and cert_sha != expected:
            reasons.append(
                f"certification bound to {cert_sha[:12]} != candidate {expected[:12]}")
        return ("PASS" if not reasons else "BLOCKED"), reasons

    return {"binding": binding, "release_manifest": release_manifest,
            "sha256sums": sha256sums, "msi_execution": msi_execution,
            "migration": migration, "cert_migration_crosscheck": cert_migration_crosscheck,
            "i2o": i2o, "i2r": i2r, "i2u": i2u, "i2s": i2s}


def _load(script: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        script[:-3], SCRIPTS / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHAIN_ORDER = ["binding", "release_manifest", "sha256sums",
               "msi_execution", "migration", "cert_migration_crosscheck",
               "i2o", "i2r", "i2u", "i2s"]


def run_validation(inputs: Dict[str, Any],
                   steps: Optional[Dict[str, Callable]] = None,
                   invoked: Optional[List[str]] = None) -> Dict[str, Any]:
    """Full orchestrated run. Fail-fast short-circuits the chain."""
    steps = steps or default_steps(inputs)
    invoked = invoked if invoked is not None else []
    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release_version": TARGET_VERSION,
        "source_sha": inputs.get("source_sha"),
        "V101_FAILFAST_GATE": "BLOCKED",
        "V101_THREE_ARTIFACT_BINDING": "NOT_RUN",
        "V101_MSI_EXECUTION": "NOT_RUN",
        "V101_MODEL_PHYSICAL_EVIDENCE": "NOT_RUN",
        "V101_PACKAGE_VALIDATION": "BLOCKED",
        "V101_PROMOTION_INPUT": "BLOCKED",
        "blockers": [],
        "chain": {},
        "promotion_performed": False,
        "tag_performed": False,
    }
    ff_verdict, ff_reasons = fail_fast_gate(inputs)
    report["V101_FAILFAST_GATE"] = ff_verdict
    model = normalize_model_evidence(inputs.get("model_evidence") or {})
    bound_v, bound_r = validate_model_evidence_build_bound(
        inputs.get("model_evidence") or {}, str(inputs.get("source_sha", "")),
        {role: str(e.get("sha256", ""))
         for role, e in (inputs.get("artifacts") or {}).items()})
    report["V101_MODEL_PHYSICAL_EVIDENCE"] = (
        "PASS" if model["model_response_received"] == "YES"
        and not model["mocked"] and bound_v == "PASS" else "BLOCKED")
    if bound_v != "PASS":
        report["blockers"].extend(
            f"build-bound model evidence: {r}" for r in bound_r)
    if ff_verdict != "PASS":
        report["blockers"] = list(ff_reasons)
        return report
    for name in CHAIN_ORDER:
        invoked.append(name)
        verdict, reasons = steps[name]()
        report["chain"][name] = {"verdict": verdict,
                                 "reasons": list(reasons)}
        if name == "binding":
            report["V101_THREE_ARTIFACT_BINDING"] = verdict
        if name == "msi_execution":
            report["V101_MSI_EXECUTION"] = verdict
        if name == "i2u":
            report["V101_PROMOTION_INPUT"] = (
                "READY" if verdict == "PASS" else "BLOCKED")
        if verdict != "PASS":
            report["blockers"] = [f"{name}: {r}" for r in reasons]
            report["V101_PACKAGE_VALIDATION"] = "BLOCKED"
            return report
    review = any("PASS_WITH_REVIEW" in json.dumps(report["chain"][s])
                 for s in ("i2o", "i2r", "i2u"))
    report["V101_PACKAGE_VALIDATION"] = (
        "PASS_WITH_REVIEW" if review else "PASS")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v1.0.1 one-command real-artifact validation (H1H)")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--exe-sha256", required=True)
    parser.add_argument("--msi", required=True)
    parser.add_argument("--msi-sha256", required=True)
    parser.add_argument("--portable", required=True)
    parser.add_argument("--portable-sha256", required=True)
    parser.add_argument("--msi-execution-record", required=True,
                        help="JSON record covering msi_validation_contract()")
    parser.add_argument("--model-evidence", required=True,
                        help="JSON physical-use evidence "
                             "(MODEL_RESPONSE_RECEIVED=YES; H1A.3-ready)")
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--sha256sums", required=True)
    parser.add_argument("--migration-evidence", required=True)
    parser.add_argument("--compliance-evidence", default=None)
    parser.add_argument("--aggregate", default=None)
    parser.add_argument("--provenance", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    def _j(p):
        return json.loads(Path(p).read_text(encoding="utf-8"))

    inputs = {
        "release_version": TARGET_VERSION,
        "source_sha": args.source_sha,
        "artifacts": {
            "exe": {"path": args.exe, "sha256": args.exe_sha256},
            "msi": {"path": args.msi, "sha256": args.msi_sha256},
            "portable": {"path": args.portable,
                         "sha256": args.portable_sha256},
        },
        "msi_execution": _j(args.msi_execution_record),
        "model_evidence": _j(args.model_evidence),
        "release_manifest": _j(args.release_manifest),
        "sha256sums_path": args.sha256sums,
        "migration_evidence": _j(args.migration_evidence),
        "compliance_evidence": args.compliance_evidence,
        "aggregate_path": args.aggregate,
        "provenance_path": args.provenance,
    }
    report = run_validation(inputs)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if report["V101_PACKAGE_VALIDATION"] in (
        "PASS", "PASS_WITH_REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
