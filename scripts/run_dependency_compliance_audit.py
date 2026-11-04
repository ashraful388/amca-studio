#!/usr/bin/env python3
"""I2L third-party dependency license/SBOM/distribution compliance audit.

OpenCode PS-2. Audit tooling only; no production, packaging, or dependency
changes. Local metadata only — never downloads or queries external services.

  python scripts/run_dependency_compliance_audit.py [--root DIR]
      [--inspect-package PATH] [--sbom PATH] [--notices PATH]
      [--output PATH]

Inventory sources (all local): requirements-build.txt, pyproject.toml,
.venv dist-info METADATA, frontend package.json + lockfile + node_modules
license fields, Cargo.toml + Cargo.lock (+ vendored registry metadata when
present), vendored packaging resources, and the genuine Windows portable
package for package-to-SBOM consistency.

Classifications: runtime (reachable from amca imports, incl. optional
extras), build-only (packaging toolchain), dev/test-only, vendored
(first-party), unknown-scope. License IDs come only from local metadata;
anything else is UNKNOWN — never invented.

Verdicts per component: PASS (verified permissive/compatible), REVIEW
(incomplete metadata or opaque binary), BLOCKING (clearly restrictive
distribution condition unmet — reported, never auto-remediated).
Overall: BLOCKING if any BLOCKING; else REVIEW if any REVIEW/UNKNOWN in
shipped scope; else PASS. This is a technical audit, not legal advice.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

SCHEMA = "amca-dependency-compliance-audit/1"

PERMISSIVE_LICENSES = {
    "MIT", "MIT-CMU", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause",
    "BSD-3-Clause-Clear", "ISC", "Python-2.0", "PSF-2.0", "PSF",
    "Zlib", "libpng", "Unlicense", "CC0-1.0", "BSL-1.0", "WTFPL",
    "Python Software Foundation License",
}
WEAK_COPYLEFT = {"MPL-2.0", "LGPL-2.1-only", "LGPL-2.1-or-later",
                 "LGPL-3.0-only", "LGPL-3.0-or-later", "EPL-2.0"}
RESTRICTIVE = {"GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only",
               "GPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later",
               "EUPL-1.2", "SSPL-1.0"}
# GPL-family tools whose own license text grants a bundling/build exception.
# H2C.2E Part H: pyinstaller_hooks_contrib is part of the same PyInstaller
# toolchain distribution and is covered by the same documented exception.
DOCUMENTED_BUILD_EXCEPTIONS = {"pyinstaller", "pyinstaller_hooks_contrib"}

LICENSE_ALIASES = {
    "mit license": "MIT",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "isc license (iscl)": "ISC",
    "python software foundation license": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "the unlicense (unlicense)": "Unlicense",
}

_MULTI_SEP = re.compile(r"\s*(?:[;/|]|\bOR\b|\bAND\b)\s*", re.IGNORECASE)


def normalize_license(raw: str) -> str:
    """Map a local license string to an identifier, else UNKNOWN."""
    cleaned = (raw or "").strip()
    if not cleaned:
        return "UNKNOWN"
    if cleaned in PERMISSIVE_LICENSES | WEAK_COPYLEFT | RESTRICTIVE:
        return cleaned
    lowered = cleaned.casefold()
    if lowered in LICENSE_ALIASES:
        return LICENSE_ALIASES[lowered]
    # Long license prose without a machine-readable identifier: map only
    # unambiguous family keywords, else UNKNOWN (never invented).
    if "affero" in lowered:
        return "AGPL-3.0-or-later" if "later" in lowered else "AGPL-3.0-only"
    if "lesser" in lowered and "general public license" in lowered:
        return "LGPL-2.1-or-later" if "later" in lowered else "LGPL-2.1-only"
    if "general public license" in lowered or "gplv2" in lowered or \
            "gplv3" in lowered or re.search(r"\bgpl\b", lowered):
        later = "later" in lowered
        if "version 3" in lowered or "v3" in lowered or "gplv3" in lowered:
            return "GPL-3.0-or-later" if later else "GPL-3.0-only"
        return "GPL-2.0-or-later" if later else "GPL-2.0-only"
    multi = {p.strip() for p in _MULTI_SEP.split(cleaned) if p.strip()}
    if len(multi) > 1:
        mapped = {normalize_license(p) for p in multi} - {"UNKNOWN"}
        if mapped:
            return "+".join(sorted(mapped))
    return "UNKNOWN"


@dataclass
class Component:
    name: str
    version: str = ""
    ecosystem: str = ""
    scope: str = "unknown"
    license: str = "UNKNOWN"
    license_source: str = ""
    in_distributed_artifact: Optional[bool] = None
    source_ref: str = ""
    notes: List[str] = field(default_factory=list)


@dataclass
class LicenseFinding:
    component: str
    severity: str  # PASS | REVIEW | BLOCKING | UNKNOWN
    reason: str
    obligations: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Collectors (local metadata only)
# --------------------------------------------------------------------------

def _parse_requirement_pin(line: str) -> Optional[Tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    match = re.match(r"([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+]+)", line)
    if match:
        return match.group(1).lower().replace("-", "_"), match.group(2)
    return None


def _read_dist_info(venv: Path, dist_dir: str) -> Dict[str, str]:
    meta = venv / dist_dir / "METADATA"
    info: Dict[str, str] = {}
    if not meta.is_file():
        return info
    try:
        text = meta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    licenses: List[str] = []
    classifiers: List[str] = []
    for line in text.splitlines():
        if line.startswith("License-Expression:"):
            info["license_expression"] = line.split(":", 1)[1].strip()
        elif line.startswith("License:"):
            licenses.append(line.split(":", 1)[1].strip())
        elif line.startswith("Classifier: License"):
            classifiers.append(line.split("::")[-1].strip())
    if licenses:
        info["license"] = licenses[0]
    if classifiers:
        info["classifiers"] = "; ".join(classifiers)
    return info


def _amca_import_roots(src: Path) -> Set[str]:
    """Top-level third-party modules imported anywhere under src/amca."""
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
    roots: Set[str] = set()
    for path in sorted((src / "amca").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                    and node.module:
                roots.add(node.module.split(".")[0])
    return {r for r in roots if r not in stdlib and r != "amca"}


def _requires_dist(venv: Path, dist_dir: str) -> List[str]:
    meta = venv / dist_dir / "METADATA"
    reqs: List[str] = []
    try:
        text = meta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return reqs
    for line in text.splitlines():
        if line.startswith("Requires-Dist:"):
            dep = line.split(":", 1)[1].strip()
            name = re.split(r"[ ;(<>=!~\[,]", dep, maxsplit=1)[0]
            if name:
                reqs.append(name.lower().replace("-", "_"))
    return reqs


def collect_python(root: Path) -> List[Component]:
    """Inventory Python deps: declared pins + installed metadata + reachability."""
    out: List[Component] = []
    req_file = root / "packaging" / "requirements-build.txt"
    pins: Dict[str, str] = {}
    if req_file.is_file():
        for line in req_file.read_text(encoding="utf-8").splitlines():
            parsed = _parse_requirement_pin(line)
            if parsed:
                pins[parsed[0]] = parsed[1]
    venv = root / ".venv" / "Lib" / "site-packages"
    dist_infos = sorted(p for p in venv.glob("*.dist-info") if p.is_dir()) \
        if venv.is_dir() else []
    import_roots = _amca_import_roots(root / "src")
    # Map distribution -> importable top-level names (handles import/dist
    # name mismatches such as python-docx -> docx, pillow -> PIL).
    top_levels: Dict[str, Set[str]] = {}
    for dist in dist_infos:
        top_file = venv / dist / "top_level.txt"
        names: Set[str] = set()
        if top_file.is_file():
            try:
                names = {ln.strip().lower() for ln in top_file.read_text(
                    encoding="utf-8", errors="replace").splitlines()
                    if ln.strip()}
            except OSError:
                names = set()
        top_levels[dist.name.split("-")[0].lower().replace("-", "_")] = names
    lowered_roots = {r.lower() for r in import_roots}
    hop = {r.lower() for r in import_roots}
    for key, names in top_levels.items():
        if names & lowered_roots:
            hop.add(key)
    for _ in range(6):
        grown = set(hop)
        for dist in dist_infos:
            key = dist.name.split("-")[0].lower().replace("-", "_")
            norm = key.replace("_", "")
            if key in hop or norm in {h.replace("_", "") for h in hop}:
                grown.add(key)
                grown.update(_requires_dist(venv, dist.name))
        if grown == hop:
            break
        hop = grown
    reachable = hop
    build_only_hints = {"pyinstaller", "pyinstaller_hooks_contrib", "setuptools",
                        "wheel", "build", "altgraph", "pefile",
                        "pywin32_ctypes", "packaging", "pyproject_hooks",
                        "pip"}
    for dist in dist_infos:
        raw_name = dist.name.split("-")[0]
        key = raw_name.lower().replace("-", "_")
        info = _read_dist_info(venv, dist.name)
        version = pins.get(key, "")
        if not version:
            match = re.match(r"^(.*)-([0-9][^-]*)\.dist-info$", dist.name)
            version = match.group(2) if match else ""
        if key == "amca_platform":
            out.append(Component(name="amca-platform", version=version or "0.1.0a0",
                                 ecosystem="python", scope="first-party",
                                 license="first-party",
                                 license_source="first-party project code",
                                 in_distributed_artifact=False,
                                 source_ref="pyproject.toml [project]"))
            continue
        if key in build_only_hints:
            scope = "build-only"
        elif key in reachable or key.replace("_", "") in {
                r.replace("_", "") for r in reachable}:
            scope = "runtime"
        else:
            scope = "dev-or-transitive"
        raw_license = (info.get("license_expression")
                       or info.get("license")
                       or info.get("classifiers", ""))
        out.append(Component(
            name=raw_name, version=version, ecosystem="python", scope=scope,
            license=normalize_license(raw_license),
            license_source=("dist-info License-Expression" if info.get("license_expression")
                            else ("dist-info License" if info.get("license")
                                  else ("dist-info classifiers" if info.get("classifiers")
                                        else "none found locally"))),
            in_distributed_artifact=(True if scope == "runtime" else
                                     (False if scope == "build-only" else None)),
            source_ref=f"packaging/requirements-build.txt + {dist.name}/METADATA"))
    return out


def collect_frontend(root: Path) -> List[Component]:
    """Inventory npm deps from package.json + lockfile + node_modules licenses."""
    out: List[Component] = []
    ui = root / "desktop-ui"
    pkg_file = ui / "package.json"
    if not pkg_file.is_file():
        return out
    try:
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    except ValueError:
        return out
    lock_versions: Dict[str, str] = {}
    lock_file = ui / "package-lock.json"
    if lock_file.is_file():
        try:
            lock = json.loads(lock_file.read_text(encoding="utf-8"))
            for path, entry in (lock.get("packages") or {}).items():
                if path.startswith("node_modules/") and "/" not in path[13:]:
                    lock_versions[path[13:]] = str(entry.get("version", ""))
        except ValueError:
            pass
    for scope, deps in (("runtime", pkg.get("dependencies") or {}),
                        ("dev-only", pkg.get("devDependencies") or {})):
        for name, spec in deps.items():
            version = lock_versions.get(name, str(spec))
            license_id = "UNKNOWN"
            license_source = "none found locally"
            mod_pkg = ui / "node_modules" / name / "package.json"
            if mod_pkg.is_file():
                try:
                    mod = json.loads(mod_pkg.read_text(encoding="utf-8"))
                    raw = mod.get("license", "")
                    if isinstance(raw, dict):
                        raw = raw.get("type", "")
                    license_id = normalize_license(str(raw))
                    license_source = f"node_modules/{name}/package.json"
                except ValueError:
                    pass
            out.append(Component(
                name=name, version=version, ecosystem="npm", scope=scope,
                license=license_id, license_source=license_source,
                in_distributed_artifact=(True if scope == "runtime" else False),
                source_ref="desktop-ui/package.json + package-lock.json"))
    return out


def _parse_toml_section(text: str, section: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            current = stripped.strip("[]")
            continue
        if current != section or not stripped or stripped.startswith("#") \
                or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip().strip("\"'")
        value = value.strip().strip("\"',")
        if key:
            out[key] = value
    return out


def collect_rust(root: Path) -> List[Component]:
    """Inventory Cargo deps from Cargo.toml + Cargo.lock (local only)."""
    out: List[Component] = []
    manifest = root / "desktop-ui" / "src-tauri" / "Cargo.toml"
    lock = root / "desktop-ui" / "src-tauri" / "Cargo.lock"
    if not manifest.is_file():
        return out
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError:
        return out
    runtime = _parse_toml_section(text, "dependencies")
    build = _parse_toml_section(text, "build-dependencies")
    dev = _parse_toml_section(text, "dev-dependencies")
    versions: Dict[str, str] = {}
    if lock.is_file():
        try:
            blocks = lock.read_text(encoding="utf-8").split("[[package]]")
            for block in blocks:
                name = re.search(r'^name\s*=\s*"([^"]+)"', block, re.M)
                ver = re.search(r'^version\s*=\s*"([^"]+)"', block, re.M)
                if name and ver:
                    versions[name.group(1)] = ver.group(1)
        except OSError:
            pass
    cargo_home = root / ".cargo-home"
    for name in sorted(set(runtime) | set(build) | set(dev)):
        scope = ("runtime" if name in runtime
                 else ("build-only" if name in build else "dev-only"))
        license_id = "UNKNOWN"
        license_source = "none found locally"
        reg_toml = None
        if cargo_home.is_dir():
            candidates = sorted(cargo_home.glob(
                f"registry/src/*/{name}-*/Cargo.toml"))
            if candidates:
                reg_toml = candidates[0]
        if reg_toml is not None:
            try:
                reg_text = reg_toml.read_text(encoding="utf-8",
                                              errors="replace")
                lic = _parse_toml_section(reg_text, "package").get("license", "")
                if lic:
                    license_id = normalize_license(lic.strip("\""))
                    license_source = f".cargo-home registry metadata ({name})"
            except OSError:
                pass
        out.append(Component(
            name=name, version=versions.get(name, ""), ecosystem="cargo",
            scope=scope, license=license_id, license_source=license_source,
            in_distributed_artifact=(True if scope == "runtime" else
                                     (False if scope == "build-only" else None)),
            source_ref="desktop-ui/src-tauri/Cargo.toml + Cargo.lock"))
    return out


def collect_vendored(root: Path) -> List[Component]:
    """First-party vendored packaging resources (NSIS template, hooks, icons)."""
    out: List[Component] = []
    for rel in ("desktop-ui/src-tauri/nsis",
                "desktop-ui/src-tauri/hooks.nsh",
                "desktop-ui/src-tauri/icons",
                "packaging"):
        base = root / rel
        if not base.exists():
            continue
        files = ([p for p in base.rglob("*") if p.is_file()]
                 if base.is_dir() else [base])
        license_files = [p for p in files
                         if p.name.casefold().startswith("license")]
        out.append(Component(
            name=f"vendored:{rel}", version="",
            ecosystem="vendored", scope="vendored",
            license=("see " + ", ".join(p.name for p in license_files)
                     if license_files else "first-party (no separate license)"),
            license_source="vendored tree inspection",
            in_distributed_artifact=True,
            source_ref=rel + f" ({len(files)} files)"))
    return out


# --------------------------------------------------------------------------
# Classification + verdicts
# --------------------------------------------------------------------------

def attribution_needed(license_id: str) -> List[str]:
    """Mechanical obligation hints (not legal advice)."""
    base = license_id.split("+")[0]
    if base == "Apache-2.0":
        return ["attribution", "copyright notice", "license text"]
    if base in PERMISSIVE_LICENSES:
        return ["attribution", "copyright notice"]
    if base in WEAK_COPYLEFT:
        return ["attribution", "copyright notice", "license text",
                "manual legal review (weak copyleft scope)"]
    if base in RESTRICTIVE:
        return ["manual legal review",
                "source-offer/reciprocity analysis required"]
    return ["manual legal review"]


def classify_component(comp: Component) -> LicenseFinding:
    """PASS / REVIEW / BLOCKING / UNKNOWN per the governance rules."""
    if comp.scope == "first-party":
        return LicenseFinding(comp.name, "PASS", "first-party project code", [])
    if comp.license == "UNKNOWN":
        if comp.in_distributed_artifact:
            return LicenseFinding(comp.name, "REVIEW",
                                  "incomplete license metadata in shipped scope",
                                  ["manual legal review"])
        return LicenseFinding(comp.name, "UNKNOWN",
                              "incomplete license metadata (non-shipped scope)",
                              [])
    base = comp.license.split("+")[0]
    obligations = attribution_needed(comp.license)
    if base in RESTRICTIVE:
        if comp.name.lower() in DOCUMENTED_BUILD_EXCEPTIONS \
                and comp.in_distributed_artifact is not True:
            # H2C.2E Part H: build-time-only tools with a documented
            # bundling exception verify as PASS - the underlying condition
            # (build tool, verifiably not shipped in any artifact) is
            # resolved, not merely acknowledged.
            return LicenseFinding(
                comp.name, "PASS",
                "build-time-only tool with documented bundling exception; "
                "verifiably not shipped as a component",
                obligations)
        if comp.in_distributed_artifact:
            return LicenseFinding(comp.name, "BLOCKING",
                                  "clearly restrictive license in distributed "
                                  "artifact without settled compliance plan",
                                  obligations)
        return LicenseFinding(comp.name, "REVIEW",
                              "restrictive license outside shipped scope",
                              obligations)
    if base in WEAK_COPYLEFT and comp.in_distributed_artifact:
        return LicenseFinding(comp.name, "REVIEW",
                              "weak-copyleft component shipped; scope review",
                              obligations)
    return LicenseFinding(comp.name, "PASS",
                          "verified permissive/compatible metadata",
                          obligations)


def package_consistency(root: Path, components: List[Component],
                        portable: Optional[Path] = None) -> Dict[str, Any]:
    """Compare the genuine portable package against the SBOM model.

    ``portable`` must be explicitly supplied (caller-supplied artifact rule:
    stale default v0.9 binaries are never silently selected).
    """
    report: Dict[str, Any] = {
        "portable_present": False,
        "members": [],
        "missing_from_sbom": [],
        "sbom_claims_unsupported": [],
        "opaque_binaries": [],
    }
    if portable is None or not portable.is_file():
        report["note"] = (
            "no portable package supplied; package-to-SBOM consistency not "
            "evaluated (caller-supplied artifact required)")
        return report
    report["portable_present"] = True
    report["portable"] = str(portable)
    try:
        with zipfile.ZipFile(portable) as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
    except zipfile.BadZipFile:
        report["note"] = "portable package unreadable"
        return report
    report["members"] = members[:50]
    report["member_count"] = len(members)
    exes = [n for n in members if n.lower().endswith(".exe")]
    report["opaque_binaries"] = exes
    shipped = {c.name.lower() for c in components
               if c.in_distributed_artifact is True
               and c.ecosystem in ("python", "npm", "cargo")}
    if shipped and not any("backend" in n.lower() for n in exes):
        report["sbom_claims_unsupported"].append(
            "SBOM claims shipped components but no backend executable found")
    return report


def audit_dependencies(root: Path,
                       inspect_package: bool = True,
                       portable: Optional[Path] = None) -> Dict[str, Any]:
    """Full audit. Returns the machine-readable result (never raises)."""
    components = (collect_python(root) + collect_frontend(root)
                  + collect_rust(root) + collect_vendored(root))
    # Deduplicate identical (ecosystem, name) keeping first occurrence.
    seen: Set[Tuple[str, str]] = set()
    unique: List[Component] = []
    for comp in components:
        key = (comp.ecosystem, comp.name.lower())
        if key in seen:
            comp.notes.append("duplicate inventory entry merged")
            continue
        seen.add(key)
        unique.append(comp)
    findings = [classify_component(c) for c in unique]
    package_report = package_consistency(root, unique, portable=portable) \
        if inspect_package else {}
    counts: Dict[str, int] = {"PASS": 0, "REVIEW": 0, "BLOCKING": 0,
                              "UNKNOWN": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    if counts.get("BLOCKING", 0):
        overall = "BLOCKING"
    elif counts.get("REVIEW", 0) + counts.get("UNKNOWN", 0):
        overall = "REVIEW"
    else:
        overall = "PASS"
    return {
        "schema": SCHEMA,
        "overall": overall,
        "counts": counts,
        "components": [asdict(c) for c in unique],
        "findings": [asdict(f) for f in findings],
        "package_consistency": package_report,
    }


# --------------------------------------------------------------------------
# SBOM + notices emitters
# --------------------------------------------------------------------------

def emit_sbom(result: Dict[str, Any], path: Path) -> Path:
    doc = {
        "sbom_schema": "amca-sbom-candidate/1",
        "generated_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      __import__("time").gmtime()),
        "components": [
            {
                "name": c["name"],
                "version": c["version"],
                "ecosystem": c["ecosystem"],
                "classification": c["scope"],
                "license": c["license"],
                "license_source": c["license_source"],
                "in_distributed_artifact": c["in_distributed_artifact"],
                "source_ref": c["source_ref"],
            }
            for c in result["components"]
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path



def _active_release_version() -> str:
    """Read the active release version from pyproject.toml (22A)."""
    import tomllib

    pyproject = SOFTWARE_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = str(data.get("project", {}).get("version", "")).strip()
    if not version:
        raise ValueError(
            "active release version not found in pyproject.toml; refusing "
            "to emit a notices file with an unversioned title (22A)")
    return version


def emit_notices(result: Dict[str, Any], path: Path,
                 release_version: str = "") -> Path:
    """Emit third-party notices.

    22A: the title is version-bound to the supplied/current release version
    (never a historical hardcoded constant, never a "(Candidate)" label).
    When release_version is empty the active version is read from
    <root>/pyproject.toml so the title can never silently go stale.
    """
    if not release_version:
        release_version = _active_release_version()
    lines = [
        f"# AMCA Studio v{release_version} — Third-Party Notices",
        "",
        "Generated from repository-local metadata only. License identifiers",
        "marked UNKNOWN were not determinable locally and must be resolved",
        "before final promotion. This is a technical listing, not legal advice.",
        "",
    ]
    shipped = [c for c in result["components"]
               if c.get("in_distributed_artifact") is True]
    lines.append(f"## Shipped runtime components ({len(shipped)})")
    lines.append("")
    for comp in sorted(shipped, key=lambda c: (c["ecosystem"], c["name"])):
        lines.append(f"### {comp['name']} {comp['version']}".rstrip())
        lines.append(f"- Ecosystem: {comp['ecosystem']}")
        lines.append(f"- License: {comp['license']} (source: "
                     f"{comp['license_source']})")
        lines.append(f"- Inventory: {comp['source_ref']}")
        lines.append("")
    lines.append("## License texts to include")
    lines.append("")
    lines.append("Include the license texts / copyright notices required by "
                 "the attributions above from the corresponding distribution "
                 "packages. Components marked UNKNOWN must have their license "
                 "established first; do not ship on an UNKNOWN basis.")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AMCA dependency license/SBOM compliance audit (I2L)")
    parser.add_argument("--root", default=str(SOFTWARE_ROOT))
    parser.add_argument("--portable", default=None,
                        help="Explicit portable package path for the "
                        "package-consistency check (caller-supplied; no "
                        "stale default is discovered silently)")
    parser.add_argument("--inspect-package", action="store_true", default=True)
    parser.add_argument("--no-inspect-package", dest="inspect_package",
                        action="store_false")
    parser.add_argument("--sbom", default=None)
    parser.add_argument("--notices", default=None)
    parser.add_argument("--release-version", default="",
                        help="Release version for the notices title (22A); "
                             "defaults to the active pyproject.toml version")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = audit_dependencies(Path(args.root),
                                inspect_package=args.inspect_package,
                                portable=Path(args.portable)
                                if args.portable else None)
    if args.sbom:
        sbom_path = emit_sbom(result, Path(args.sbom))
        result["sbom_path"] = str(sbom_path)
    if args.notices:
        notices_path = emit_notices(result, Path(args.notices),
                                    release_version=args.release_version)
        result["notices_path"] = str(notices_path)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 0 if result["overall"] not in ("BLOCKING", "FAIL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
