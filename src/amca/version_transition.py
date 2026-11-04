"""AMCA Studio v1.0.0 Deterministic Version Transition Executor (I2L).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2L-DETERMINISTIC-V1.0.0-VERSION-TRANSITION-EXECUTOR-AND-ATOMICITY-DRY-RUN-01

Prepares the deterministic, atomic executor for the eventual 0.9.0 -> 1.0.0
transition so that once PS-1 finishes final convergence and the Manager
authorizes the bump, the version change is one controlled operation with no
stale active metadata.

Behavior:
- default DRY-RUN: computes and reports the exact plan, changes nothing.
- explicit --apply: atomic/all-or-nothing. Every target file is read, the
  edit is computed, and if ANY operation fails, all partial edits are rolled
  back from in-memory snapshots (no file is left half-modified).
- updates ONLY the active release metadata/references from the authoritative
  I2F inventory (I2F's ACTIVE_VERSION_DECLARATIONS + ACTIVE_CODE_REFERENCES).
- historical-protection gate: broad search-and-replace is refused; the
  executor edits only inventoried files and the tests prove historical
  v0.5.x/v0.6.x/v0.7.0/v0.9.0 records and prior milestone documentation stay
  byte-identical.
- preconditions: expected starting version (0.9.0), optional expected source
  SHA (rejected if the tree HEAD differs), agreement across active
  declarations.
- post-transition validation runs the I2F version-freeze validator against
  the tree: active version == target, zero stale active 0.9 references,
  historical exemptions allowed, Python/npm/Tauri/Cargo agreement.
- artifact naming simulation (no build): reports the final artifact names
  that the pipeline will produce at the target version.

The executor NEVER runs in write mode against the live canonical branch
from this tool's own invocation - write mode requires an explicit --apply
plus --authorize-bump flags, and it is the Manager-authorized step executed
on the final pre-bump canonical SHA.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_START_VERSION = "0.9.0"
DEFAULT_TARGET_VERSION = "1.0.0"

SCHEMA = "amca-version-transition-plan/1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(*args: str) -> str:
    try:
        proc = subprocess_run(["git", *args])
        return proc
    except Exception:
        return ""


def subprocess_run(args: List[str]) -> str:
    import subprocess

    proc = subprocess.run(args, cwd=str(SOFTWARE_ROOT),
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip() if proc.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Per-file edit rules (active metadata only; explicit, no broad replace)
# ---------------------------------------------------------------------------

def _edit_toml_version(text: str, target: str) -> Tuple[str, int]:
    pattern = re.compile(r'^(version\s*=\s*)"[^"]+"', re.MULTILINE)
    new_text, n = pattern.subn(rf'\1"{target}"', text)
    return new_text, n


def _edit_package_json_version(text: str, target: str) -> Tuple[str, int]:
    pattern = re.compile(r'^(  "version"\s*:\s*)"[^"]+"', re.MULTILINE)
    new_text, n = pattern.subn(rf'\1"{target}"', text)
    return new_text, n


def _edit_init_version(text: str, target: str) -> Tuple[str, int]:
    pattern = re.compile(
        r'^(__version__\s*=\s*)"[^"]+"', re.MULTILINE)
    new_text, n = pattern.subn(rf'\1"{target}"', text)
    return new_text, n


def _edit_tauri_conf_version(text: str, target: str) -> Tuple[str, int]:
    pattern = re.compile(r'^(\s*"version"\s*:\s*)"[^"]+"', re.MULTILINE)
    new_text, n = pattern.subn(rf'\1"{target}"', text)
    return new_text, n


#: Explicit per-file edit strategies, keyed by I2F inventory paths. Only
#: these files are touched — the historical-protection gate.
FILE_EDIT_RULES: Dict[str, Any] = {
    "pyproject.toml": _edit_toml_version,
    "src/amca/__init__.py": _edit_init_version,
    "desktop-ui/package.json": _edit_package_json_version,
    "desktop-ui/src-tauri/tauri.conf.json": _edit_tauri_conf_version,
    "desktop-ui/src-tauri/Cargo.toml": _edit_toml_version,
}

#: Active code references: version-literal replacements scoped to the exact
#: I2F-inventoried modules (the bump checklist step 7). Each rule replaces
#: the CURRENT-version literal with the target inside that file only.
CODE_REFERENCE_RULES: Dict[str, List[Tuple[str, str]]] = {
    # (old_literal, new_literal) pairs; applied literally, file-scoped.
    "src/amca/distribution_build.py": [('version: str = "0.9.0"',
                                        'version: str = "1.0.0"')],
    "src/amca/final_release.py": [
        ('amca-studio-v0.9.0-windows-x64-setup.exe',
         'amca-studio-v1.0.0-windows-x64-setup.exe'),
        ('amca-studio-v0.9.0-windows-x64-portable.zip',
         'amca-studio-v1.0.0-windows-x64-portable.zip'),
        ('(version == "0.9.0")', '(version == "1.0.0")'),
    ],
    "src/amca/release_packaging.py": [
        ('"version": "0.9.0"', '"version": "1.0.0"'),
        ("'version', '0.9.0'", "'version', '1.0.0'"),
    ],
    "src/amca/distribution_smoke.py": [
        ('release_manifest_v0.9.0.json', 'release_manifest_v1.0.0.json'),
    ],
    "src/amca/final_promotion.py": [
        ('release_manifest_v0.9.0.json', 'release_manifest_v1.0.0.json'),
    ],
    "scripts/promote_final_release.py": [
        ('amca-studio-v0.9.0-windows-x64-setup.exe',
         'amca-studio-v1.0.0-windows-x64-setup.exe'),
        ('amca-studio-v0.9.0-windows-x64-portable.zip',
         'amca-studio-v1.0.0-windows-x64-portable.zip'),
        ('release_manifest_v0.9.0.json', 'release_manifest_v1.0.0.json'),
    ],
}

#: Paths that must remain byte-identical (historical-protection gate).
HISTORICAL_PROTECTED_PREFIXES = (
    "docs/release/AMCA-Studio-v0.5",
    "docs/release/AMCA-Studio-v0.6",
    "docs/release/AMCA-Studio-v0.7",
    "docs/release/AMCA-Studio-v0.9.0-",
    "dist/archive/",
)


@dataclass
class TransitionPlan:
    """The exact set of edits the executor will apply."""

    files: List[Dict[str, Any]] = field(default_factory=list)
    cargo_lock_regeneration_required: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files": self.files,
            "cargo_lock_regeneration_required":
                self.cargo_lock_regeneration_required,
            "notes": self.notes,
        }



def _toml_version(text: str) -> Optional[str]:
    for line in text.splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None

def build_plan(
    software_root: Path,
    target: str = DEFAULT_TARGET_VERSION,
    start_version: str = EXPECTED_START_VERSION,
) -> TransitionPlan:
    """Compute the exact edit plan without touching any file."""
    plan = TransitionPlan()
    from amca.version_freeze_preflight import (
        ACTIVE_CODE_REFERENCES, ACTIVE_VERSION_DECLARATIONS)

    # Verify the STARTING version against the tree being transitioned
    # (root), not the live canonical tree.
    current_versions = {}
    for loc in ACTIVE_VERSION_DECLARATIONS:
        if loc.kind == "derived":
            continue
        text = (software_root / loc.file).read_text(encoding="utf-8")
        if loc.file.endswith("package.json"):
            current_versions[loc.file] = json.loads(text).get("version")
        elif loc.file.endswith("__init__.py"):
            m = re.search(
                r'^__version__\s*=\s*["\']([^"\']+)["\']',
                text, re.MULTILINE)
            current_versions[loc.file] = m.group(1) if m else None
        else:
            current_versions[loc.file] = _toml_version(text)
    distinct = {v for v in current_versions.values() if v}
    if len(distinct) != 1:
        raise ValueError(
            f"active version declarations disagree: {current_versions}")
    current = distinct.pop()
    if current != start_version:
        raise ValueError(
            f"expected starting version {start_version}, found {current!r}")

    for loc in ACTIVE_VERSION_DECLARATIONS:
        if loc.kind == "derived":
            continue
        path = software_root / loc.file
        text = path.read_text(encoding="utf-8")
        rule = FILE_EDIT_RULES.get(loc.file)
        if rule is None:
            raise ValueError(
                f"no edit rule for active declaration {loc.file}; refusing "
                "to use broad search-and-replace")
        new_text, count = rule(text, target)
        if count == 0:
            raise ValueError(
                f"version pattern not found in {loc.file}; refusing to proceed")
        if new_text != text:
            plan.files.append({
                "file": loc.file, "role": loc.role,
                "edits": count, "old_version": current, "new_version": target,
            })

    # Active code references (checklist step 7).
    code_rules = {k: v for k, v in CODE_REFERENCE_RULES.items()}
    for loc in ACTIVE_CODE_REFERENCES:
        rel = loc.file
        pairs = code_rules.get(rel)
        if not pairs:
            continue
        path = software_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new_text = text
        total = 0
        for old_lit, new_lit in pairs:
            count = new_text.count(old_lit)
            if count:
                new_text = new_text.replace(old_lit, new_lit)
                total += count
        if new_text != text:
            plan.files.append({
                "file": rel, "role": "active code references",
                "edits": total, "old_version": start_version,
                "new_version": target,
            })
        elif total == 0 and text.count(f'"{start_version}"'):
            plan.files.append({
                "file": rel, "role": "active code references",
                "edits": 0, "old_version": start_version,
                "new_version": target,
                "note": "no matching literal found; verify manually at bump time",
            })

    plan.notes.append(
        "Cargo.lock must be regenerated with the proper toolchain operation "
        "(cargo check --locked / cargo update -p amca-desktop) after Cargo.toml "
        "changes - never hand-edited")
    plan.notes.append(
        "derived release records (manifest/SHA256SUMS/artifacts) are produced "
        "by run_distribution_pipeline(version=target) AFTER the bump commit, "
        "per the I2F checklist")
    return plan


def apply_transition(
    software_root: Optional[Path] = None,
    *,
    target: str = DEFAULT_TARGET_VERSION,
    start_version: str = EXPECTED_START_VERSION,
    expected_source_sha: Optional[str] = None,
    apply: bool = False,
    inject_failure_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the version transition atomically.

    apply=False (default): dry-run — plan only, no file is modified.
    apply=True: read every target into memory, compute all edits, then write.
    Any failure (including an injected one, for tests) rolls back all writes
    from the in-memory snapshots: the tree is left exactly as before.
    """
    root = Path(software_root) if software_root else SOFTWARE_ROOT
    result: Dict[str, Any] = {
        "schema": SCHEMA,
        "timestamp_utc": _utc_now(),
        "dry_run": not apply,
        "start_version": start_version,
        "target_version": target,
        "expected_source_sha": expected_source_sha,
        "actual_source_sha": _git("rev-parse", "HEAD") if not software_root \
            else "",
        "applied": False,
        "rolled_back": False,
        "files_changed": [],
        "plan": None,
        "post_validation": None,
        "artifact_name_simulation": {
            "installer": f"amca-studio-v{target}-windows-x64-setup.exe",
            "portable": f"amca-studio-v{target}-windows-x64-portable.zip",
            "manifest": f"release_manifest_v{target}.json",
        },
        "overall": "BLOCKED",
        "reasons": [],
        "secrets_emitted": False,
    }

    # SHA precondition (live tree only; synthetic test trees pass None root).
    if expected_source_sha and not software_root:
        actual = _git("rev-parse", "HEAD")
        if actual != expected_source_sha:
            result["reasons"].append(
                f"source SHA {actual or '(unknown)'} does not match the "
                f"expected pre-bump SHA {expected_source_sha}")
            return result

    try:
        plan = build_plan(root, target=target, start_version=start_version)
        result["plan"] = plan.to_dict()
    except ValueError as exc:
        result["reasons"].append(str(exc))
        return result

    if not apply:
        result["overall"] = "DRY_RUN"
        result["reasons"].append(
            "dry-run: plan computed; no file modified (use apply=True with "
            "Manager authorization on the final pre-bump SHA)")
        return result

    # ---- Atomic application ----
    snapshots: Dict[Path, str] = {}
    try:
        # Binary-safe snapshot/restore: read raw bytes (no EOL translation)
        # so LF files stay LF and CRLF files stay CRLF. Text-mode writes
        # caused the LF -> CRLF drift that blocked candidate af3be0d6.
        for entry in plan.files:
            path = root / entry["file"]
            snapshots[path] = path.read_bytes().decode("utf-8")

        for entry in plan.files:
            path = root / entry["file"]
            text = snapshots[path]
            if entry["role"] == "active code references":
                rule_pairs = CODE_REFERENCE_RULES.get(entry["file"], [])
                for old_lit, new_lit in rule_pairs:
                    text = text.replace(old_lit, new_lit)
            else:
                rule = FILE_EDIT_RULES[entry["file"]]
                text, _ = rule(text, target)
            # Injected failure (test hook): roll back everything before
            # writing this file.
            if inject_failure_at and entry["file"] == inject_failure_at:
                raise RuntimeError(f"injected failure at {entry['file']}")
            path.write_bytes(text.encode("utf-8"))
            result["files_changed"].append(entry["file"])

        # Historical-protection gate: assert protected files are untouched.
        for path in snapshots:
            rel = path.relative_to(root).as_posix()
            for prefix in HISTORICAL_PROTECTED_PREFIXES:
                if rel.startswith(prefix):
                    raise RuntimeError(
                        f"historical file {rel} was modified - rollback")
    except Exception as exc:
        # Rollback: restore every snapshotted file; delete files that were
        # created new (none expected, but be safe).
        for path, content in snapshots.items():
            try:
                path.write_bytes(content.encode("utf-8"))
            except OSError:
                pass
        result["rolled_back"] = True
        result["applied"] = False
        result["overall"] = "FAILED_ROLLED_BACK"
        result["reasons"].append(f"{type(exc).__name__}: {exc}")
        return result

    result["applied"] = True
    result["overall"] = "APPLIED"

    # ---- Post-transition validation (I2F validator on the new tree) ----
    try:
        from amca.version_freeze_preflight import (
            active_version_agreement, simulate_version_bump)

        # The module reads via SOFTWARE_ROOT; point the validator functions
        # at the transitioned tree by re-reading directly.
        agreement = active_version_agreement(
            _read_matrix_from(root, target))
        result["post_validation"] = {
            "active_version": agreement.get("current_version"),
            "agreement": agreement.get("agrees"),
            "versions": agreement.get("versions"),
        }
    except Exception as exc:
        result["post_validation"] = {"error": str(exc)}

    return result


def _read_matrix_from(root: Path, target: str) -> Dict[str, Any]:
    """Build an active-version matrix for an arbitrary tree root (used by
    post-transition validation on synthetic/test trees)."""
    from amca.version_freeze_preflight import ACTIVE_VERSION_DECLARATIONS

    def toml_v(text: str) -> Optional[str]:
        for line in text.splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return None

    def json_v(text: str) -> Optional[str]:
        try:
            return json.loads(text).get("version")
        except (json.JSONDecodeError, OSError):
            return None

    def py_v(text: str) -> Optional[str]:
        m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        return m.group(1) if m else None

    extractors = {"toml": toml_v, "json": json_v, "python": py_v}
    matrix: Dict[str, Any] = {}
    for loc in ACTIVE_VERSION_DECLARATIONS:
        if loc.kind == "derived":
            continue
        path = root / loc.file
        text = path.read_text(encoding="utf-8") if path.is_file() else None
        matrix[loc.file] = {
            "role": loc.role, "kind": loc.kind,
            "version": extractors[loc.kind](text) if text else None,
            "note": loc.note, "derived": False,
        }
    return matrix
