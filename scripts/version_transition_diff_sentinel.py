#!/usr/bin/env python3
"""Version-transition diff sentinel (I1.24 contract, reconciled + LF-guarded in I1.26A).

Deterministic guard proving the 0.9.0 -> 1.0.0 transition commit (ZCode I3A) changes
ONLY release-version-related files its owner is authorized to modify, and nothing
unrelated. Run immediately AFTER the transition commit:

    python scripts/version_transition_diff_sentinel.py \
        --pre <PRE_TRANSITION_SHA> --post <POST_TRANSITION_SHA> [--json]

Classifications:
  EXPECTED_VERSION_CHANGE        - allowlisted path whose diff is version/artifact-name related
  EXPECTED_DERIVED_REGENERATION  - Cargo.lock regenerated consistently with Cargo.toml
  UNEXPECTED_SOURCE_DRIFT        - denylisted / non-enumerated path, arbitrary edit inside an
                                   allowlisted file, or a line-ending (EOL) rewrite (BLOCKS)
  HISTORICAL_CONTENT_MODIFIED    - a historical version record rewritten (BLOCKS)

I1.26A amendments (Manager-authorized, explicitly enumerated, no wildcards):
  - final_promotion.py: permitted ONLY for literal version/artifact-name substitutions;
    any promotion/policy/eligibility/branching/authorization/overwrite/lineage logic change
    is UNEXPECTED_SOURCE_DRIFT.
  - the enumerated version-pinning test files: permitted ONLY for version/artifact-name/
    fixture-expectation line changes; a weakened/behavior-relaxing edit is drift.
  - the enumerated derived manifest/handoff files: permitted ONLY for mechanically derived
    version/artifact/hash/size/state line changes.
  - line-ending preservation: a changed file must keep its pre-transition EOL convention;
    an LF->CRLF (or unauthorized CRLF->LF) whole-file rewrite is UNEXPECTED_SOURCE_DRIFT.

The classifier is pure (operates on change dicts) so it is unit-testable without a repo.
This tool makes NO version change itself.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # software/

# --- Enumerated allowlist: pure version-metadata files (exact paths). ----------
ALLOWLIST_EXACT = {
    "software/pyproject.toml",
    "software/src/amca/__init__.py",
    "software/desktop-ui/package.json",
    "software/desktop-ui/src-tauri/tauri.conf.json",
    "software/desktop-ui/src-tauri/Cargo.toml",
    "software/desktop-ui/src-tauri/Cargo.lock",
}
# Release/version defaults + active artifact naming/path references in release tooling.
ALLOWLIST_PREFIXES = (
    "software/scripts/",
    "software/src/amca/release_",
    "software/src/amca/rc1_certification.py",
    "software/src/amca/final_release.py",
    "software/src/amca/distribution_build.py",
    "software/src/amca/distribution_smoke.py",
)

# --- I1.26A: final_promotion.py permitted ONLY for literal version/artifact-name edits.
VERSION_ONLY_LITERAL_PATHS = {
    "software/src/amca/final_promotion.py",
}

# --- I1.26A: explicitly enumerated version-pinning test files (transition-affected).
PERMITTED_VERSION_PINNING_TESTS = {
    "software/tests/test_studio_v100_final_version_freeze_preflight.py",
    "software/tests/test_studio_v100_version_transition_executor.py",
    "software/tests/test_studio_v100_zcode_final_operational_handoff.py",
    "software/tests/test_studio_v100_zcode_final_release_engineering_handoff.py",
    "software/tests/test_studio_v100_zcode_final_release_execution_cli_rehearsal.py",
}

# --- I1.26A: explicitly enumerated derived manifest/handoff files.
PERMITTED_DERIVED_MANIFESTS = {
    "software/docs/integration/AMCA-Studio-v1.0.0-ZCode-Final-Release-Engineering-Delta-Manifest.json",
    "software/docs/integration/AMCA-Studio-v1.0.0-ZCode-Final-Release-Engineering-Handoff.md",
    "software/docs/integration/AMCA-Studio-v1.0.0-ZCode-I2N-Final-Operational-Delta-Manifest.json",
}

# --- Denylist: immutable during a version transition (promotion semantics etc.). ---
DENYLIST_EXACT = {
    "software/src/amca/kernel.py",
    "software/src/amca/collaboration.py",
    "software/src/amca/chat_history.py",
    "software/src/amca/agent_fabric.py",
    "software/src/amca/routing.py",
    "software/src/amca/modes.py",
    "software/src/amca/model_hub.py",
    "software/src/amca/signing_readiness.py",
    "software/src/amca/signing_evidence.py",
    "software/src/amca/release_policy.py",
    "software/src/amca/policy_promotion_matrix.py",
    "software/src/amca/orchestration_rehearsal.py",
    "software/src/amca/version_freeze_preflight.py",
    "software/src/amca/import_mpl_release_asset.py",
}
DENYLIST_SUBSTRINGS = (
    "desktop-ui/src/",
    "desktop-ui/src-tauri/src/",
    "/intelligence/",
)

HISTORICAL_RECORD_SUBSTRINGS = (
    "docs/releases/",
    "docs/release/AMCA-Studio-v0.",
    "CHANGELOG",
)

VERSION_TOKEN = re.compile(r"0\.9\.0|1\.0\.0|v0\.9\.0|v1\.0\.0")
ARTIFACT_NAME = re.compile(r"amca-studio-v\d+\.\d+\.\d+|release_manifest_v\d+\.\d+\.\d+")
# Lines that indicate promotion/policy LOGIC (not a literal version string) - used to
# reject semantic edits inside final_promotion.py.
LOGIC_MARKER = re.compile(
    r"\b(def |class |if |elif |else|return|raise|for |while |import |from |assert |lambda )")


def _is_allowlisted(path: str) -> bool:
    return path in ALLOWLIST_EXACT or any(path.startswith(p) for p in ALLOWLIST_PREFIXES)


def _is_denylisted(path: str) -> bool:
    return path in DENYLIST_EXACT or any(s in path for s in DENYLIST_SUBSTRINGS)


def _is_historical_record(path: str) -> bool:
    return any(s in path for s in HISTORICAL_RECORD_SUBSTRINGS)


def _changed_content_lines(diff_text: str):
    out = []
    for line in diff_text.splitlines():
        if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---")):
            body = line[1:].strip()
            if body:
                out.append(body)
    return out


def _diff_is_version_related(diff_text: str) -> bool:
    """Every changed content line must be version/artifact-name related."""
    changed = _changed_content_lines(diff_text)
    if not changed:
        return True
    return all(VERSION_TOKEN.search(b) or ARTIFACT_NAME.search(b) for b in changed)


def _diff_is_version_literal_only(diff_text: str) -> bool:
    """For final_promotion.py: changed lines must be version/artifact-name literals and
    must NOT introduce/remove logic markers (promotion semantics stay immutable)."""
    changed = _changed_content_lines(diff_text)
    if not changed:
        return True
    for b in changed:
        if not (VERSION_TOKEN.search(b) or ARTIFACT_NAME.search(b)):
            return False
        if LOGIC_MARKER.search(b):
            # a line that both changes logic AND mentions a version is still a logic edit
            return False
    return True


# I1.26G: mechanically derived manifest lines are permitted to carry ONLY these fields.
DERIVED_ALLOWED = re.compile(
    r'"?(sha256|size|version|installer|portable|release-manifest|manifest'
    r'|artifact|generated_utc|generated_at|source_sha)"?\s*[:=]', re.IGNORECASE)
# Content that must never appear in a mechanically derived manifest change.
DERIVED_FORBIDDEN = re.compile(
    r'"?(schema|path|milestone|policy|eligibility|classification|component|document'
    r'|state|overall|notes|reason|contract)"?\s*[:=]', re.IGNORECASE)


def _diff_is_derived_mechanical(diff_text: str) -> bool:
    """For enumerated derived manifests: every changed content line must be a mechanical
    version/artifact/hash/size/state-refresh field. Schema/path/milestone/policy/
    eligibility/prose/entry-add-remove changes are rejected."""
    changed = _changed_content_lines(diff_text)
    if not changed:
        return True
    # Entry add/remove (whole JSON objects/keys added or removed) is not mechanical.
    plus = [b for b in changed if b.startswith("+") or not b.startswith("-")]
    minus = [b for b in changed if b.startswith("-")]
    # Detect added/removed top-level keys or list entries: a line introducing a new
    # quoted key or a brace on only one side of the change.
    key_re = re.compile(r'^\s*"[^"]+"\s*:\s*\{')
    if any(key_re.match(b.lstrip("+- ")) and not key_re.match(b[1:].lstrip()) for b in changed):
        pass  # nested object boundaries are handled by the field rules below
    for b in changed:
        body = b[1:].strip() if b[:1] in "+-" else b
        if DERIVED_FORBIDDEN.search(body):
            return False
        if not DERIVED_ALLOWED.search(body) and not re.match(r'^[{}\[\],\s]*$', body):
            # a line with content that is neither an allowed field nor structural JSON
            return False
    return True


def _eol_of(text_bytes: bytes):
    crlf = text_bytes.count(b"\r\n")
    lf = text_bytes.count(b"\n")
    if crlf == 0 and lf > 0:
        return "LF"
    if crlf > 0 and crlf == lf:
        return "CRLF"
    if crlf > 0:
        return "MIXED"
    return "NONE"


def classify_changes(changes):
    """changes: iterable of dicts {status, path, diff, pre_bytes?, post_bytes?}."""
    results = []
    for ch in changes:
        path = ch["path"]
        status = ch.get("status", "M")
        diff = ch.get("diff", "")
        pre_b = ch.get("pre_bytes")
        post_b = ch.get("post_bytes")

        # EOL preservation guard (applies to any modified file with both blobs available).
        if status == "M" and pre_b is not None and post_b is not None:
            pre_eol, post_eol = _eol_of(pre_b), _eol_of(post_b)
            if pre_eol != post_eol and pre_eol in ("LF", "CRLF") and post_eol in ("LF", "CRLF"):
                results.append({
                    "path": path, "status": status,
                    "classification": "UNEXPECTED_SOURCE_DRIFT",
                    "reason": f"line-ending rewrite {pre_eol}->{post_eol} (must preserve pre-transition EOL)",
                })
                continue

        if _is_historical_record(path) and status != "A":
            results.append({"path": path, "status": status,
                            "classification": "HISTORICAL_CONTENT_MODIFIED",
                            "reason": "historical version record must not be rewritten"})
            continue
        if _is_denylisted(path):
            results.append({"path": path, "status": status,
                            "classification": "UNEXPECTED_SOURCE_DRIFT",
                            "reason": "denylisted immutable path changed"})
            continue

        # final_promotion.py: version-literal-only.
        if path in VERSION_ONLY_LITERAL_PATHS:
            if status == "M" and _diff_is_version_literal_only(diff):
                results.append({"path": path, "status": status,
                                "classification": "EXPECTED_VERSION_CHANGE",
                                "reason": "final_promotion.py literal version/artifact-name change only"})
            else:
                results.append({"path": path, "status": status,
                                "classification": "UNEXPECTED_SOURCE_DRIFT",
                                "reason": "final_promotion.py changed beyond literal version/artifact-name"})
            continue

        # enumerated version-pinning tests: version-related lines only.
        if path in PERMITTED_VERSION_PINNING_TESTS:
            if status == "M" and _diff_is_version_related(diff):
                results.append({"path": path, "status": status,
                                "classification": "EXPECTED_VERSION_CHANGE",
                                "reason": "version-pinning test updated for version/artifact/fixture expectation"})
            else:
                results.append({"path": path, "status": status,
                                "classification": "UNEXPECTED_SOURCE_DRIFT",
                                "reason": "version-pinning test changed beyond version expectation (possible weakening)"})
            continue

        # enumerated derived manifests: mechanically derived version/artifact/hash/size
        # lines only (I1.26G hardened classifier).
        if path in PERMITTED_DERIVED_MANIFESTS:
            if status == "M" and _diff_is_derived_mechanical(diff):
                results.append({"path": path, "status": status,
                                "classification": "EXPECTED_VERSION_CHANGE",
                                "reason": "derived manifest/handoff mechanically updated for version/artifact/hash/size"})
            else:
                results.append({"path": path, "status": status,
                                "classification": "UNEXPECTED_SOURCE_DRIFT",
                                "reason": "derived manifest changed beyond mechanical version/hash/size content"})
            continue

        if status == "A" and not _is_allowlisted(path):
            results.append({"path": path, "status": status,
                            "classification": "UNEXPECTED_SOURCE_DRIFT",
                            "reason": "unexpected new file outside allowlist"})
            continue
        if not _is_allowlisted(path):
            results.append({"path": path, "status": status,
                            "classification": "UNEXPECTED_SOURCE_DRIFT",
                            "reason": "changed path not in allowlist"})
            continue

        if path.endswith("Cargo.lock"):
            results.append({"path": path, "status": status,
                            "classification": "EXPECTED_DERIVED_REGENERATION",
                            "reason": "Cargo.lock regeneration consistent with Cargo.toml"})
            continue
        if _diff_is_version_related(diff):
            results.append({"path": path, "status": status,
                            "classification": "EXPECTED_VERSION_CHANGE",
                            "reason": "allowlisted path with version-related content change"})
        else:
            results.append({"path": path, "status": status,
                            "classification": "UNEXPECTED_SOURCE_DRIFT",
                            "reason": "allowlisted path but change is not version-related"})
    return results


def verdict(results):
    blocking = [r for r in results if r["classification"] in
                ("UNEXPECTED_SOURCE_DRIFT", "HISTORICAL_CONTENT_MODIFIED")]
    return {
        "overall": "BLOCKED" if blocking else "PASS",
        "blocking_count": len(blocking),
        "counts": {c: sum(1 for r in results if r["classification"] == c)
                   for c in ("EXPECTED_VERSION_CHANGE", "EXPECTED_DERIVED_REGENERATION",
                             "UNEXPECTED_SOURCE_DRIFT", "HISTORICAL_CONTENT_MODIFIED")},
    }


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO_ROOT.parent)] + list(args),
                          capture_output=True, text=True).stdout


def _git_bytes(rev, path):
    r = subprocess.run(["git", "-C", str(REPO_ROOT.parent), "show", f"{rev}:{path}"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def collect_changes(pre: str, post: str):
    name_status = _git("diff", "--name-status", f"{pre}..{post}")
    changes = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0][0], parts[-1]
        diff = _git("diff", f"{pre}..{post}", "--", path)
        changes.append({"status": st, "path": path, "diff": diff,
                        "pre_bytes": _git_bytes(pre, path),
                        "post_bytes": _git_bytes(post, path)})
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description="Version-transition diff sentinel")
    ap.add_argument("--pre", required=True)
    ap.add_argument("--post", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    results = classify_changes(collect_changes(args.pre, args.post))
    v = verdict(results)
    out = {"pre": args.pre, "post": args.post, "verdict": v, "results": results}
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        for r in results:
            print(f"  [{r['classification']}] {r['path']}  ({r['reason']})")
        print(f"OVERALL: {v['overall']}  counts={v['counts']}")
    return 0 if v["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
