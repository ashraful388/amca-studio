"""Version-transition diff sentinel tests (I1.24).

Proves the guard accepts ONLY authorized 0.9.0 -> 1.0.0 version-related changes and
rejects any unrelated source drift or historical-record modification. The sentinel
makes no version change itself; these tests exercise its pure classifier.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location(
    "vtds", str(SCRIPTS / "version_transition_diff_sentinel.py"))
assert spec is not None and spec.loader is not None
vtds = importlib.util.module_from_spec(spec)
sys_injected = False
import sys
sys.modules["vtds"] = vtds
spec.loader.exec_module(vtds)


def _c(status, path, diff=""):
    return {"status": status, "path": path, "diff": diff}


VER_DIFF = "+version = \"1.0.0\"\n-version = \"0.9.0\"\n"


def test_legitimate_full_transition_accepted():
    changes = [
        _c("M", "software/pyproject.toml", VER_DIFF),
        _c("M", "software/src/amca/__init__.py",
           "+__version__ = \"1.0.0\"\n-__version__ = \"0.9.0\"\n"),
        _c("M", "software/desktop-ui/package.json", VER_DIFF),
        _c("M", "software/desktop-ui/src-tauri/tauri.conf.json", VER_DIFF),
        _c("M", "software/desktop-ui/src-tauri/Cargo.toml", VER_DIFF),
        _c("M", "software/desktop-ui/src-tauri/Cargo.lock", "+name = \"amca-desktop\"\n"),
    ]
    results = vtds.classify_changes(changes)
    v = vtds.verdict(results)
    assert v["overall"] == "PASS", v
    assert v["counts"]["EXPECTED_DERIVED_REGENERATION"] == 1
    assert v["counts"]["UNEXPECTED_SOURCE_DRIFT"] == 0


def test_unrelated_python_code_edit_rejected():
    changes = [_c("M", "software/src/amca/service.py",
                  "+def new_business_logic():\n+    return 42\n")]
    v = vtds.verdict(vtds.classify_changes(changes))
    assert v["overall"] == "BLOCKED"
    assert v["counts"]["UNEXPECTED_SOURCE_DRIFT"] == 1


def test_unrelated_frontend_edit_rejected():
    changes = [_c("M", "software/desktop-ui/src/App.tsx",
                  "+const x = 1;\n")]
    v = vtds.verdict(vtds.classify_changes(changes))
    assert v["overall"] == "BLOCKED"


def test_historical_v0_9_reference_unchanged_is_fine():
    # No change to historical records at all -> nothing flagged.
    changes = [_c("M", "software/pyproject.toml", VER_DIFF)]
    results = vtds.classify_changes(changes)
    assert all(r["classification"] != "HISTORICAL_CONTENT_MODIFIED" for r in results)


def test_historical_record_modification_rejected():
    changes = [_c("M", "software/docs/releases/v0.9.0-notes.md",
                  "-shipped 0.9.0\n+shipped 1.0.0\n")]
    v = vtds.verdict(vtds.classify_changes(changes))
    assert v["overall"] == "BLOCKED"
    assert v["counts"]["HISTORICAL_CONTENT_MODIFIED"] == 1


def test_unexpected_new_file_rejected():
    changes = [_c("A", "software/src/amca/sneaky_new_module.py", "+def trap(): ...\n")]
    v = vtds.verdict(vtds.classify_changes(changes))
    assert v["overall"] == "BLOCKED"
    assert v["counts"]["UNEXPECTED_SOURCE_DRIFT"] == 1


def test_expected_cargo_lock_regeneration_allowed():
    changes = [_c("M", "software/desktop-ui/src-tauri/Cargo.lock",
                  "+version = \"0.1.0\"\n")]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "EXPECTED_DERIVED_REGENERATION"
    assert vtds.verdict(results)["overall"] == "PASS"


def test_version_only_release_tool_change_accepted():
    changes = [_c("M", "software/scripts/run_distribution_pipeline.py",
                  "+ARTIFACT = \"amca-studio-v1.0.0-windows-x64-setup.exe\"\n"
                  "-ARTIFACT = \"amca-studio-v0.9.0-windows-x64-setup.exe\"\n")]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"


def test_allowlisted_path_with_arbitrary_edit_rejected():
    # pyproject.toml is allowlisted, but a non-version edit must still be rejected.
    changes = [_c("M", "software/pyproject.toml",
                  "+description = \"totually unrelated rewrite\"\n")]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
    assert vtds.verdict(results)["overall"] == "BLOCKED"


def test_partial_transition_rejected():
    # Only __init__ bumped but pyproject left at 0.9.0 with no diff -> the sentinel
    # cannot approve a partial transition; here we assert a mixed legit+drift set
    # (bumping __init__ while editing service.py) is blocked.
    changes = [
        _c("M", "software/src/amca/__init__.py",
           "+__version__ = \"1.0.0\"\n-__version__ = \"0.9.0\"\n"),
        _c("M", "software/src/amca/kernel.py", "+# drive-by edit\n"),
    ]
    v = vtds.verdict(vtds.classify_changes(changes))
    assert v["overall"] == "BLOCKED"


def test_no_tag_build_promotion_side_effects():
    # A transition that adds a tag file or a built binary is unexpected drift.
    changes = [_c("A", "software/dist/amca-studio-v1.0.0-windows-x64-setup.exe", "")]
    v = vtds.verdict(vtds.classify_changes(changes))
    assert v["overall"] == "BLOCKED"


# ---------------------------------------------------------------------------
# I1.26A: reconciled transition contract + line-ending (EOL) preservation guard
# ---------------------------------------------------------------------------

def _c2(status, path, diff="", pre_bytes=None, post_bytes=None):
    return {"status": status, "path": path, "diff": diff,
            "pre_bytes": pre_bytes, "post_bytes": post_bytes}


FP_VERSION_DIFF = (
    '-DEFAULT_RELEASE_MANIFEST = SOFTWARE_ROOT / "dist" / "release_manifest_v0.9.0.json"\n'
    '+DEFAULT_RELEASE_MANIFEST = SOFTWARE_ROOT / "dist" / "release_manifest_v1.0.0.json"\n')


def test_final_promotion_version_literal_only_passes():
    changes = [_c("M", "software/src/amca/final_promotion.py", FP_VERSION_DIFF)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"
    assert vtds.verdict(results)["overall"] == "PASS"


def test_final_promotion_logic_change_fails():
    logic_diff = (
        '-    if not eligible:\n'
        '+    if not eligible and not force_override:\n'
        '+        return _auto_promote()\n')
    changes = [_c("M", "software/src/amca/final_promotion.py", logic_diff)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
    assert vtds.verdict(results)["overall"] == "BLOCKED"


def test_authorized_version_pinning_test_update_passes():
    diff = ('-EXPECTED_VERSION = "0.9.0"\n+EXPECTED_VERSION = "1.0.0"\n'
            '-ARTIFACT = "amca-studio-v0.9.0-windows-x64-setup.exe"\n'
            '+ARTIFACT = "amca-studio-v1.0.0-windows-x64-setup.exe"\n')
    changes = [_c("M", "software/tests/test_studio_v100_final_version_freeze_preflight.py", diff)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"
    assert vtds.verdict(results)["overall"] == "PASS"


def test_weakened_test_assertion_fails():
    # Removing an assertion is a behavior relaxation, not a version expectation update.
    diff = ('-assert bundle.promotion_state == ST_READY\n'
            '-assert bundle.final_release_version == "0.9.0"\n'
            '+assert bundle.final_release_version == "1.0.0"\n')
    changes = [_c("M", "software/tests/test_studio_v100_final_version_freeze_preflight.py", diff)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
    assert vtds.verdict(results)["overall"] == "BLOCKED"


def test_authorized_derived_manifest_update_passes():
    diff = ('-  "version": "0.9.0",\n+  "version": "1.0.0",\n'
            '-  "installer": "amca-studio-v0.9.0-windows-x64-setup.exe",\n'
            '+  "installer": "amca-studio-v1.0.0-windows-x64-setup.exe",\n')
    changes = [_c("M", "software/docs/integration/AMCA-Studio-v1.0.0-ZCode-Final-Release-Engineering-Delta-Manifest.json", diff)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"
    assert vtds.verdict(results)["overall"] == "PASS"


def test_unrelated_manifest_edit_fails():
    diff = ('-  "policy": "deny-by-default",\n+  "policy": "allow-all",\n')
    changes = [_c("M", "software/docs/integration/AMCA-Studio-v1.0.0-ZCode-Final-Release-Engineering-Delta-Manifest.json", diff)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
    assert vtds.verdict(results)["overall"] == "BLOCKED"


def test_lf_to_crlf_whole_file_rewrite_fails():
    pre = b'__version__ = "1.0.0"\n'
    post = b'__version__ = "1.0.0"\r\n'  # same content, EOL flipped LF->CRLF
    changes = [_c2("M", "software/src/amca/__init__.py",
                   '+__version__ = "1.0.0"\r\n-__version__ = "1.0.0"\n',
                   pre_bytes=pre, post_bytes=post)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
    assert "line-ending" in results[0]["reason"]
    assert vtds.verdict(results)["overall"] == "BLOCKED"


def test_clean_lf_version_transition_passes():
    pre = b'__version__ = "0.9.0"\n'
    post = b'__version__ = "1.0.0"\n'  # LF preserved
    changes = [_c2("M", "software/src/amca/__init__.py",
                   '+__version__ = "1.0.0"\n-__version__ = "0.9.0"\n',
                   pre_bytes=pre, post_bytes=post)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"
    assert vtds.verdict(results)["overall"] == "PASS"


# ---------------------------------------------------------------------------
# I1.26G: derived-manifest hash/size classifier hardening
# ---------------------------------------------------------------------------

DM = "software/docs/integration/AMCA-Studio-v1.0.0-ZCode-Final-Release-Engineering-Delta-Manifest.json"


def test_derived_manifest_sha_refresh_passes():
    diff = ('-      "sha256": "64a3adb1d5f663807087809979e1744b1edc64c986e9924068e5ac04b10f8d4a",\n'
            '+      "sha256": "f964bd6378ff091a0e218f497fe9b9a697b303e4241ea169f29f582b13c62a2f",\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"


def test_derived_manifest_size_refresh_passes():
    diff = ('-      "size": 13639,\n+      "size": 13700,\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"


def test_derived_manifest_hash_and_size_refresh_passes():
    diff = ('-      "sha256": "64a3adb1d5f663807087809979e1744b1edc64c986e9924068e5ac04b10f8d4a",\n'
            '-      "size": 13639,\n'
            '+      "sha256": "f964bd6378ff091a0e218f497fe9b9a697b303e4241ea169f29f582b13c62a2f",\n'
            '+      "size": 13700,\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"


def test_derived_manifest_version_artifact_field_passes():
    diff = ('-  "version": "0.9.0",\n+  "version": "1.0.0",\n'
            '-  "installer": "amca-studio-v0.9.0-windows-x64-setup.exe",\n'
            '+  "installer": "amca-studio-v1.0.0-windows-x64-setup.exe",\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "EXPECTED_VERSION_CHANGE"


def test_derived_manifest_arbitrary_edit_fails():
    diff = ('-      "sha256": "64a3adb1d5f663807087809979e1744b1edc64c986e9924068e5ac04b10f8d4a",\n'
            '+      "note": "manually edited content here",\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
    assert vtds.verdict(results)["overall"] == "BLOCKED"


def test_derived_manifest_policy_semantic_edit_fails():
    diff = ('-  "release_status": "candidate",\n+  "release_status": "final",\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"


def test_derived_manifest_schema_change_fails():
    diff = ('-  "schema": "amca-zcode-delta-manifest/1",\n+  "schema": "amca-zcode-delta-manifest/2",\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"


def test_derived_manifest_entry_removal_fails():
    diff = ('-  "items": [\n-    {"name": "x"}\n-  ]\n')
    results = vtds.classify_changes([_c("M", DM, diff)])
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"


def test_unrelated_json_file_remains_blocked():
    diff = '-  "version": "0.9.0"\n+  "version": "1.0.0"\n'
    changes = [_c("M", "software/docs/integration/AMCA-Studio-v1.0.0-Some-Other-Unrelated-File.json", diff)]
    results = vtds.classify_changes(changes)
    assert results[0]["classification"] == "UNEXPECTED_SOURCE_DRIFT"
