#!/usr/bin/env python3
"""H1C v1.0.1 three-artifact binding + MSI validation schema (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1C-MSI-ARTIFACT-BINDING-AND-VALIDATION-SCHEMA-IMPLEMENTATION-01
Tooling/schema/tests only. Builds nothing, installs nothing, promotes
nothing. Does not modify v1.0.0 evidence.

A Windows v1.0.1 release must bind THREE mutually distinct artifacts —
exe_sha256, msi_sha256, portable_sha256 — each with an explicit physical
path + size, all bound to one exact certified source SHA and one release
version. Missing MSI evidence yields an honest BLOCKED, never a silent skip.

Backward compatibility: historical v1.0.0 evidence remains a valid
two-artifact (exe + portable) record; MSI is never retroactively required
for v1.0.0.

Known v1.0.0 artifact hashes are explicitly rejected as v1.0.1 evidence.
Canonical v1.0.1 artifact paths must live under the D: working root; C:
paths are rejected.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "amca-v101-artifact-binding/1"

WORKING_ROOT = "D:/ChatGPT/AMCA"

ARTIFACT_ROLES = ("exe", "msi", "portable")

# Historical v1.0.0 artifact hashes — must never be reused as v1.0.1
# evidence (lineage sentinel rejection).
V100_KNOWN_HASHES = {
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1":
        "v1.0.0 installer",
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454":
        "v1.0.0 portable",
}

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# File magic signatures (first bytes) used to reject role substitution,
# e.g. an EXE renamed to .msi.
MAGIC_SIGNATURES = {
    "exe": (b"MZ",),
    "msi": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),  # OLE compound file
    "portable": (b"PK\x03\x04", b"PK\x05\x06"),    # ZIP
}

ROLE_SUFFIX = {"exe": (".exe",), "msi": (".msi",), "portable": (".zip",)}

# MSI-specific validation contract fields (evidence schema for the future
# MSI smoke run; H1C defines the contract, does not execute it).
MSI_CONTRACT_FIELDS = [
    "msi_sha256",
    "size_bytes",
    "product_version",
    "product_name",
    "silent_install_result",
    "installed_payload_verification",
    "packaged_backend_health",
    "desktop_launch",
    "install_result",
    "uninstall_result",
    "residual_installation_check",
]


def detect_magic(path: Path) -> Optional[str]:
    """Return 'exe' | 'msi' | 'portable' | None from file magic bytes."""
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return None
    for role, signatures in MAGIC_SIGNATURES.items():
        if any(head.startswith(sig) for sig in signatures):
            return role
    return None


def _normalize(path_str: str) -> str:
    return str(path_str).replace("\\", "/")


def validate_artifact_binding(
    binding: Dict[str, Any],
    verify_files: bool = False,
) -> Tuple[str, List[str]]:
    """Validate a v1.0.1 (or historical v1.0.0) artifact binding document.

    Returns (verdict, reasons) with verdict in PASS | BLOCKED.
    """
    reasons: List[str] = []
    version = str(binding.get("release_version", ""))
    source_sha = str(binding.get("source_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        return "BLOCKED", ["missing/invalid certified source SHA"]
    artifacts = binding.get("artifacts") or {}
    if not isinstance(artifacts, dict) or not artifacts:
        return "BLOCKED", ["no artifacts bound"]

    seen_hashes: Dict[str, str] = {}
    for role in ARTIFACT_ROLES:
        entry = artifacts.get(role)
        if entry is None:
            if role == "msi" and version == "1.0.0":
                continue  # historical two-artifact record stays valid
            reasons.append(f"missing mandatory artifact binding: {role}")
            continue
        sha = str(entry.get("sha256", ""))
        if not _SHA_RE.fullmatch(sha):
            reasons.append(f"{role}: sha256 must be a full 64-hex digest")
        path = _normalize(str(entry.get("path", "")))
        if not path:
            reasons.append(f"{role}: explicit physical path required")
        if not isinstance(entry.get("size_bytes"), int) or \
                entry.get("size_bytes", 0) <= 0:
            reasons.append(f"{role}: positive size_bytes required")
        entry_version = str(entry.get("version", ""))
        if entry_version and entry_version != version:
            reasons.append(
                f"{role}: version {entry_version!r} != release "
                f"{version!r}")
        if not path.startswith(("D:/", "d:/")):
            reasons.append(
                f"{role}: canonical v1.0.1 artifacts must live under the D: "
                f"working root (got {path!r})")
        suffixes = ROLE_SUFFIX[role]
        if path and not path.lower().endswith(suffixes):
            reasons.append(f"{role}: path must end with {suffixes}")
        if version.startswith("1.0.1") and sha in V100_KNOWN_HASHES:
            reasons.append(
                f"{role}: hash is historical {V100_KNOWN_HASHES[sha]} — "
                "v1.0.0 artifacts may not be reused as v1.0.1 evidence")
        if sha in seen_hashes:
            reasons.append(
                f"artifact roles must be mutually distinct: {role} duplicates "
                f"{seen_hashes[sha]}")
        seen_hashes[sha] = role
        if verify_files:
            file_path = Path(path)
            if not file_path.is_file():
                reasons.append(f"{role}: artifact file missing: {path}")
                continue
            actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual != sha:
                reasons.append(f"{role}: physical bytes differ from binding")
            magic = detect_magic(file_path)
            if magic != role:
                reasons.append(
                    f"{role}: file magic is {magic!r} — role substitution "
                    "rejected (e.g. EXE renamed to .msi)")
    if reasons:
        return "BLOCKED", reasons
    return "PASS", ["three-artifact binding valid" if version != "1.0.0"
                    else "historical two-artifact binding valid"]


def verify_lineage(
    binding: Dict[str, Any],
    release_manifest: Dict[str, Any],
    promotion_input: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    """Certified source SHA <-> artifacts <-> manifest <-> promotion input.

    Any artifact pointing at another SHA/version blocks the release.
    """
    reasons: List[str] = []
    source_sha = str(binding.get("source_sha", ""))
    version = str(binding.get("release_version", ""))
    manifest_sha = str(release_manifest.get("provenance", {}).get(
        "source_git_sha", release_manifest.get("source_git_sha", "")))
    if manifest_sha and manifest_sha != source_sha:
        reasons.append(
            f"release manifest source {manifest_sha[:12]} != certified "
            f"{source_sha[:12]}")
    manifest_version = str(release_manifest.get("version", ""))
    if manifest_version and manifest_version != version:
        reasons.append(
            f"release manifest version {manifest_version!r} != binding "
            f"{version!r}")
    for art in release_manifest.get("artifacts", []):
        role = ("exe" if art.get("package_type") == "installer" and
                str(art.get("package_name", "")).endswith(".exe") else
                ("msi" if str(art.get("package_name", "")).endswith(".msi")
                 else "portable"))
        bound = (binding.get("artifacts") or {}).get(role)
        if bound and str(art.get("sha256")) != str(bound.get("sha256")):
            reasons.append(
                f"manifest {role} hash differs from binding")
    if promotion_input is not None:
        if str(promotion_input.get("source_sha", "")) != source_sha:
            reasons.append("promotion input source SHA mismatch")
        for role in ARTIFACT_ROLES:
            key = f"{role}_sha256"
            bound = (binding.get("artifacts") or {}).get(role)
            if bound and promotion_input.get(key) and \
                    str(promotion_input[key]) != str(bound.get("sha256")):
                reasons.append(f"promotion input {key} mismatch")
    if reasons:
        return "BLOCKED", reasons
    return "PASS", ["lineage consistent"]


def msi_validation_contract() -> Dict[str, Any]:
    """The MSI evidence field contract the future MSI smoke run must emit."""
    return {
        "schema": "amca-msi-validation-contract/1",
        "required_fields": MSI_CONTRACT_FIELDS,
        "command_semantics": {
            "install": "msiexec /i <MSI> /qn TARGETDIR=<disposable> "
            "/l*v <log>",
            "uninstall": "msiexec /x <MSI> /qn",
        },
        "rules": [
            "honest BLOCKED when the MSI execution environment is "
            "unavailable; never a silent skip",
            "installed payload verification must confirm amca-backend.exe "
            "and amca-desktop.exe presence with real-payload floors",
            "residual_installation_check must confirm no installed files "
            "remain after uninstall",
            "no elevation required for per-user install",
        ],
    }


def validate_shipping_index(
    index: Dict[str, Any],
    binding: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """Final v1.0.1 shipping index/manifest must reference all three
    bound artifacts exactly (role, sha256, path, size, version, source)."""
    reasons: List[str] = []
    version = str(index.get("release_version", ""))
    if version != str(binding.get("release_version", "")):
        reasons.append("shipping index version != binding version")
    if str(index.get("source_sha", "")) != str(binding.get("source_sha", "")):
        reasons.append("shipping index source SHA != certified binding SHA")
    entries = index.get("artifacts") or []
    by_role: Dict[str, Dict[str, Any]] = {}
    for art in entries:
        role = str(art.get("role", ""))
        if role in by_role:
            reasons.append(f"shipping index duplicates artifact role: {role}")
        by_role[role] = art
    bound = binding.get("artifacts") or {}
    for role in ARTIFACT_ROLES:
        entry = by_role.get(role)
        if entry is None:
            reasons.append(
                f"shipping index missing {role.upper()} artifact reference "
                "(missing MSI is an honest BLOCKED, never a silent skip)"
                if role == "msi" else
                f"shipping index missing {role} artifact reference")
            continue
        ref = bound.get(role)
        if ref is None:
            reasons.append(f"shipping index {role} not present in binding")
            continue
        if str(entry.get("sha256", "")) != str(ref.get("sha256", "")):
            reasons.append(f"shipping index {role} hash != binding hash")
        if _normalize(str(entry.get("path", ""))) != \
                _normalize(str(ref.get("path", ""))):
            reasons.append(f"shipping index {role} path != binding path")
        if entry.get("size_bytes") != ref.get("size_bytes"):
            reasons.append(f"shipping index {role} size != binding size")
    if reasons:
        return "BLOCKED", reasons
    return "PASS", ["shipping index binds all three artifacts"]


def _manifest_role(package_name: str) -> Optional[str]:
    name = str(package_name).lower()
    if name.endswith(".exe"):
        return "exe"
    if name.endswith(".msi"):
        return "msi"
    if name.endswith(".zip"):
        return "portable"
    return None


def validate_release_manifest(
    manifest: Dict[str, Any],
    binding: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """H1D contract: the v1.0.1 release manifest must enumerate ALL THREE
    bound artifacts, bound to the same certified source SHA and version.
    Omitting MSI is an honest BLOCKED. Historical v1.0.0 manifests remain
    valid two-artifact records."""
    reasons: List[str] = []
    version = str(binding.get("release_version", ""))
    if str(manifest.get("version", "")) != version:
        reasons.append("release manifest version != binding release version")
    msha = str(manifest.get("provenance", {}).get(
        "source_git_sha", manifest.get("source_git_sha", "")))
    if msha != str(binding.get("source_sha", "")):
        reasons.append(
            f"release manifest bound to source {msha[:12]!r}, not the "
            f"certified {str(binding.get('source_sha', ''))[:12]!r}")
    bound = binding.get("artifacts") or {}
    seen: Dict[str, Dict[str, Any]] = {}
    for art in manifest.get("artifacts", []):
        role = _manifest_role(art.get("package_name", ""))
        if role is None:
            reasons.append(
                f"unknown artifact in release manifest: "
                f"{art.get('package_name')!r}")
            continue
        if role in seen:
            reasons.append(f"duplicate {role} entry in release manifest")
        seen[role] = art
        ref = bound.get(role)
        if ref and str(art.get("sha256", "")) != str(ref.get("sha256", "")):
            reasons.append(
                f"release manifest {role} hash != binding hash")
    for role in ARTIFACT_ROLES:
        if role == "msi" and version == "1.0.0":
            continue
        if role not in seen:
            label = "MSI" if role == "msi" else role
            reasons.append(
                f"release manifest omits {label} artifact (mandatory for "
                f"v{version})")
    if reasons:
        return "BLOCKED", reasons
    return "PASS", ["release manifest enumerates all bound artifacts"]


def validate_sha256sums(
    sums_text: str,
    binding: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """H1D contract: SHA256SUMS.txt must list every bound artifact file
    with its exact hash. A v1.0.1 sums file that omits the MSI (or lists
    the EXE hash for the MSI name) is BLOCKED."""
    reasons: List[str] = []
    version = str(binding.get("release_version", ""))
    entries: Dict[str, str] = {}
    for line in sums_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            reasons.append(f"unparseable SHA256SUMS line: {line!r}")
            continue
        digest, name = parts[0].lower(), parts[1].strip().lstrip("*")
        entries[_normalize(name).rsplit("/", 1)[-1]] = digest
    bound = binding.get("artifacts") or {}
    for role in ARTIFACT_ROLES:
        if role == "msi" and version == "1.0.0":
            continue
        ref = bound.get(role)
        if ref is None:
            continue
        fname = _normalize(str(ref.get("path", ""))).rsplit("/", 1)[-1]
        label = "MSI" if role == "msi" else role
        if fname not in entries:
            reasons.append(
                f"SHA256SUMS omits {label} artifact: {fname} (mandatory for "
                f"v{version})")
        elif str(entries[fname]) != str(ref.get("sha256", "")):
            reasons.append(f"SHA256SUMS {role} hash != binding hash")
    exe_ref, msi_ref = bound.get("exe"), bound.get("msi")
    if exe_ref and msi_ref:
        msi_name = _normalize(
            str(msi_ref.get("path", ""))).rsplit("/", 1)[-1]
        if entries.get(msi_name) == str(exe_ref.get("sha256", "")):
            reasons.append(
                "SHA256SUMS MSI entry carries the EXE hash — EXE/ZIP "
                "substitution for MSI rejected")
    if reasons:
        return "BLOCKED", reasons
    return "PASS", ["SHA256SUMS binds all artifacts exactly"]


def main() -> int:
    parser = __import__("argparse").ArgumentParser(
        description="v1.0.1 artifact binding validation (H1C)")
    parser.add_argument("--binding", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--release-manifest", default=None)
    parser.add_argument("--sha256sums", default=None)
    parser.add_argument("--promotion-input", default=None)
    parser.add_argument("--shipping-index", default=None)
    parser.add_argument("--verify-files", action="store_true")
    args = parser.parse_args()
    binding = json.loads(Path(args.binding).read_text(encoding="utf-8"))
    verdict, reasons = validate_artifact_binding(
        binding, verify_files=args.verify_files)
    detail: Dict[str, Any] = {"schema": SCHEMA, "verdict": verdict,
                              "reasons": reasons}
    if verdict == "PASS" and args.manifest:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        promo = (json.loads(Path(args.promotion_input).read_text(
            encoding="utf-8")) if args.promotion_input else None)
        lverdict, lreasons = verify_lineage(binding, manifest, promo)
        detail["lineage"] = {"verdict": lverdict, "reasons": lreasons}
        if lverdict != "PASS":
            detail["verdict"] = "BLOCKED"
    if verdict == "PASS" and args.shipping_index:
        index = json.loads(Path(args.shipping_index).read_text(
            encoding="utf-8"))
        everdict, ereasons = validate_shipping_index(index, binding)
        detail["shipping_index"] = {"verdict": everdict, "reasons": ereasons}
        if everdict != "PASS":
            detail["verdict"] = "BLOCKED"
    if verdict == "PASS" and args.release_manifest:
        rmanifest = json.loads(Path(args.release_manifest).read_text(
            encoding="utf-8"))
        mverdict, mreasons = validate_release_manifest(rmanifest, binding)
        detail["release_manifest"] = {"verdict": mverdict,
                                      "reasons": mreasons}
        if mverdict != "PASS":
            detail["verdict"] = "BLOCKED"
    if verdict == "PASS" and args.sha256sums:
        sums = Path(args.sha256sums).read_text(encoding="utf-8")
        sverdict, sreasons = validate_sha256sums(sums, binding)
        detail["sha256sums"] = {"verdict": sverdict, "reasons": sreasons}
        if sverdict != "PASS":
            detail["verdict"] = "BLOCKED"
    print(json.dumps(detail, indent=2))
    return 0 if detail["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
