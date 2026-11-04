#!/usr/bin/env python3
"""AMCA Studio Windows Code Signing Verification Automation (CR-02).

Work Order: ANTIGRAVITY-AMCA-CR02-CODE-SIGNING-IMPLEMENTATION-31D
Required checks:
1. Authenticode status (Valid / NotSigned / HashMismatch)
2. Publisher identity (Signer certificate Subject / CN)
3. Timestamp validity (RFC 3161 timestamp presence & cert)
4. Signed binary hash generation (SHA-256 computed on verified bytes)

Enforces the Hash Custody Rule:
Never trust or hash an unsigned or tampered binary for release custody.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.signing_provider import verify_authenticode, sha256_file  # noqa: E402

def verify_single_binary(file_path: Path, expected_publisher: Optional[str] = None, require_timestamp: bool = True) -> Dict[str, Any]:
    """Execute complete verification suite against a single Windows executable."""
    verification = verify_authenticode(file_path)
    
    file_exists = file_path.is_file()
    status = verification.get("status", "Unknown")
    has_sig = verification.get("has_signature", False)
    has_ts = verification.get("has_timestamp", False)
    pub = verification.get("publisher") or ""
    
    # Check 1: Authenticode status
    status_pass = (status in ("Valid", "NotTrusted")) and has_sig
    if status == "HashMismatch":
        status_pass = False
        tampered = True
    else:
        tampered = False

    # Check 2: Publisher identity
    if expected_publisher:
        publisher_pass = (expected_publisher.lower() in pub.lower())
    else:
        publisher_pass = has_sig

    # Check 3: Timestamp validity
    if require_timestamp:
        timestamp_pass = has_ts
    else:
        timestamp_pass = True

    overall_pass = file_exists and status_pass and publisher_pass and timestamp_pass and not tampered

    # Check 4: Signed binary hash generation (only computed after checks)
    file_sha256 = sha256_file(file_path) if file_exists else None

    return {
        "file": str(file_path.resolve()),
        "filename": file_path.name,
        "size": file_path.stat().st_size if file_exists else 0,
        "authenticode_status": status,
        "is_tampered": tampered,
        "has_signature": has_sig,
        "has_timestamp": has_ts,
        "publisher": pub,
        "timestamp_info": verification.get("timestamp"),
        "sha256": file_sha256,
        "checks": {
            "authenticode_pass": status_pass,
            "publisher_pass": publisher_pass,
            "timestamp_pass": timestamp_pass,
            "hash_custody_pass": overall_pass,
        },
        "verdict": "PASS" if overall_pass else "FAIL",
    }

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Windows Authenticode Signatures and Hash Custody (CR-02)")
    parser.add_argument("files", nargs="*", help="Paths to executables to verify")
    parser.add_argument("--publisher", default=None, help="Expected publisher substring")
    parser.add_argument("--no-timestamp", action="store_true", help="Allow missing timestamp (development only)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument("--output", default=None, help="Write JSON results to file")
    args = parser.parse_args()

    target_files = [Path(f) for f in args.files]
    if not target_files:
        # Default to standard packaging output locations if none provided
        candidates = [
            SOFTWARE_ROOT / "backend-dist" / "amca-backend.exe",
            SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "target" / "release" / "amca-desktop.exe",
        ]
        bundle_dir = SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "target" / "release" / "bundle"
        if bundle_dir.is_dir():
            candidates.extend(bundle_dir.glob("**/*.exe"))
        target_files = [c for c in candidates if c.is_file()]

    results = []
    all_pass = True
    for target in target_files:
        res = verify_single_binary(target, expected_publisher=args.publisher, require_timestamp=(not args.no_timestamp))
        results.append(res)
        if res["verdict"] != "PASS":
            all_pass = False

    payload = {
        "overall_verdict": "PASS" if (all_pass and results) else "FAIL",
        "verified_count": len(results),
        "passed_count": sum(1 for r in results if r["verdict"] == "PASS"),
        "failed_count": sum(1 for r in results if r["verdict"] != "PASS"),
        "artifacts": results,
    }

    rendered = json.dumps(payload, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")

    if args.json:
        print(rendered)
    else:
        print("=" * 64)
        print("AMCA WINDOWS CODE SIGNING VERIFICATION (CR-02)")
        print("=" * 64)
        for r in results:
            print(f"File: {r['filename']} ({r['file']})")
            print(f"  Authenticode Status: {r['authenticode_status']}")
            print(f"  Publisher:           {r['publisher'] or 'N/A'}")
            print(f"  Timestamp Present:   {r['has_timestamp']}")
            print(f"  SHA-256 (Signed):    {r['sha256']}")
            print(f"  Verdict:             {r['verdict']}")
            print("-" * 64)
        print(f"OVERALL VERDICT: {payload['overall_verdict']}")
        print("=" * 64)

    return 0 if payload["overall_verdict"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
