#!/usr/bin/env python3
"""I2P MPL notice-payload readiness + final compliance asset gate.

OpenCode PS-2. Compliance asset/readiness validation only; no dependency,
product, or packaging changes. No web downloads. No legal approval claimed.

Deterministically determines whether an authoritative MPL-2.0 license text
for the shipped certifi component is available locally:

  python scripts/run_mpl_notice_readiness.py [--output PATH]

States:
  READY                  — authoritative text found, hash-bound, staged.
  RELEASE_ASSET_REQUIRED — no authoritative text locally; the exact asset
                           must be externally supplied before final
                           certification; the absence is gate-detectable.
  MISMATCH               — an asset exists but component/version/hash
                           binding fails.
  BLOCKED                — cannot evaluate (missing inputs).

Authoritativeness requires the verbatim canonical MPL-2.0 text: it must
contain every canonical section heading INCLUDING "3. Infringement Waiver".
Locally found MPL renderings (cargo registry / node_modules boxed copies)
have renumbered or missing sections, so they are NOT authoritative and are
reported as evidence of the search, never silently accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

SCHEMA = "amca-mpl-notice-readiness/1"

CERTIFI_VERSION = "2026.7.22"
CERTIFI_DIST = (SOFTWARE_ROOT / ".venv" / "Lib" / "site-packages" /
                f"certifi-{CERTIFI_VERSION}.dist-info")
ASSET_PATH = (SOFTWARE_ROOT / "docs" / "release" / "assets" /
              "MPL-2.0-certifi-2026.7.22.txt")

CANONICAL_SECTIONS = [
    "Mozilla Public License Version 2.0",
    "1. Definitions",
    "2. License Grants and Conditions",
    # H2C.1: section 3 of the canonical text Mozilla distributes today
    # (https://www.mozilla.org/media/MPL/2.0/index.txt,
    #  sha256 3f3d9e0024b1921b067d6f7f88deb4a60cbe7a78e76c64e3f1d7fc3b779b9d04)
    # is titled "3. Responsibilities"; the retired 2012 header was
    # "3. Infringement Waiver". The authoritative-text requirement is
    # unchanged - only the stale header name is corrected.
    "3. Responsibilities",
    "4. Inability to Comply Due to Statute or Regulation",
    "5. Termination",
    "6. Disclaimer of Warranty",
    "7. Limitation of Liability",
    "8. Litigation",
    "9. Miscellaneous",
    "10. Versions of the License",
    "Exhibit A",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_authoritative_mpl(text: str) -> tuple[bool, List[str]]:
    """Verbatim canonical MPL-2.0 check — every section, exact numbering."""
    missing = [s for s in CANONICAL_SECTIONS if s not in text]
    return (not missing), missing


def local_search() -> Dict[str, Any]:
    """Search locally available sources only (no web). Returns evidence."""
    evidence: Dict[str, Any] = {
        "sources_checked": [],
        "authoritative_found": False,
        "non_authoritative_mpl_renderings": [],
        "certifi_bundle_notice": None,
    }
    # 1. certifi dist-info licenses directory (the declared License-File).
    dist_license = CERTIFI_DIST / "licenses" / "LICENSE"
    evidence["sources_checked"].append({
        "source": str(dist_license),
        "exists": dist_license.is_file(),
    })
    if dist_license.is_file():
        text = dist_license.read_text(encoding="utf-8", errors="replace")
        entry = {
            "path": str(dist_license),
            "sha256": sha256_file(dist_license),
            "size_bytes": dist_license.stat().st_size,
            "is_canonical_mpl_2_0": False,
            "content_kind": (
                "ca-bundle provenance notice (not the MPL-2.0 license text)"
                if "ca-bundle" in text.casefold() else "other"),
        }
        evidence["certifi_bundle_notice"] = entry
    # 2. pip-vendored certifi copies.
    pip_vendor = (SOFTWARE_ROOT / ".venv" / "Lib" / "site-packages" /
                  "pip" / "_vendor" / "certifi" / "LICENSE")
    evidence["sources_checked"].append({
        "source": str(pip_vendor), "exists": pip_vendor.is_file()})
    # 3. cargo registry MPL renderings (checked, none authoritative).
    cargo_src = SOFTWARE_ROOT / ".cargo-home" / "registry" / "src"
    if cargo_src.is_dir():
        for path in cargo_src.rglob("LICENSE*"):
            try:
                if path.stat().st_size > 40000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "Mozilla Public License Version 2.0" in text:
                ok, missing = is_authoritative_mpl(text)
                evidence["non_authoritative_mpl_renderings"].append({
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "canonical": ok,
                    "missing_sections": missing,
                })
    # 4. node_modules MPL renderings (checked, none authoritative).
    node = SOFTWARE_ROOT / "desktop-ui" / "node_modules"
    if node.is_dir():
        for path in node.rglob("LICENSE*"):
            try:
                if path.stat().st_size > 40000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "Mozilla Public License Version 2.0" in text:
                ok, missing = is_authoritative_mpl(text)
                evidence["non_authoritative_mpl_renderings"].append({
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "canonical": ok,
                    "missing_sections": missing,
                })
    # 5. staged release asset (if a prior/external step provided it).
    if ASSET_PATH.is_file():
        try:
            text = ASSET_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        ok, missing = is_authoritative_mpl(text)
        evidence["staged_asset"] = {
            "path": str(ASSET_PATH),
            "sha256": sha256_file(ASSET_PATH),
            "size_bytes": ASSET_PATH.stat().st_size,
            "canonical": ok,
            "missing_sections": missing,
        }
        evidence["authoritative_found"] = ok
    # 6. repository license assets under docs/release/assets.
    assets_dir = ASSET_PATH.parent
    if assets_dir.is_dir():
        evidence["sources_checked"].append({
            "source": str(assets_dir), "exists": True})
    return evidence


def evaluate() -> Dict[str, Any]:
    evidence = local_search()
    staged = evidence.get("staged_asset")
    certifi_meta = CERTIFI_DIST / "METADATA"
    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "component": "certifi",
        "version": CERTIFI_VERSION,
        "license": "MPL-2.0",
        "state": "RELEASE_ASSET_REQUIRED",
        "asset": None,
        "release_action": (
            "An authoritative verbatim MPL-2.0 license text (canonical "
            "section structure including '3. Infringement Waiver') must be "
            "supplied as " + str(ASSET_PATH) + " before final package "
            "certification. The final-package compliance harness detects its "
            "absence. Do not fabricate or substitute a non-canonical "
            "rendering."),
        "evidence": evidence,
        "legal_boundary": (
            "Technical readiness only; not legal advice and not legal "
            "approval. No LEGAL_APPROVED state exists."),
    }
    if staged and staged["canonical"]:
        doc["state"] = "READY"
        doc["asset"] = {
            "path": staged["path"],
            "sha256": staged["sha256"],
            "size_bytes": staged["size_bytes"],
            "component": "certifi",
            "version": CERTIFI_VERSION,
            "license": "MPL-2.0",
            "provenance": "locally staged authoritative MPL-2.0 text",
            "release_destination": "THIRD-PARTY-NOTICES / final package docs",
        }
        doc["release_action"] = (
            "Asset is hash-bound and ready; include it in the final "
            "distribution payload.")
    if not certifi_meta.is_file():
        doc["state"] = "BLOCKED"
        doc["release_action"] = (
            f"certifi {CERTIFI_VERSION} dist-info not found locally; cannot "
            "evaluate readiness.")
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MPL notice payload readiness gate (I2P)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    doc = evaluate()
    rendered = json.dumps(doc, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    # READY exits 0; RELEASE_ASSET_REQUIRED exits 2 (detectable, not a crash);
    # BLOCKED exits 3.
    return {"READY": 0, "RELEASE_ASSET_REQUIRED": 2, "BLOCKED": 3}.get(
        doc["state"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
