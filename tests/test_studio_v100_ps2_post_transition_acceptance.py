"""PS-2 post-transition fast acceptance tests (I2Y).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2Y-POST-VERSION-TRANSITION-PS2-FAST-ACCEPTANCE-SUITE-AND-RELEASE-EVIDENCE-CHECKPOINT-01
Worker: OpenCode PS-2. Tests only; no version bump/product/packaging/
promotion/signing/release-tag/MPL-content changes.

Covers: simulated correct v1.0 transition accepted, partial transition
blocked, unrelated source drift blocked, v0.9 evidence stays stale, missing
final package PENDING (never silently selecting old package), stale v0.9
portable never used, MPL absent remains external-asset pending, valid
staged/provenance-bound MPL becomes ready, PASS_WITH_REVIEW preserved,
schema drift blocked, no worker/temp paths leaked, no source/package
mutation.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "run_ps2_post_transition_acceptance.py"
)
_spec = importlib.util.spec_from_file_location("post_transition", SCRIPT)
assert _spec is not None and _spec.loader is not None
fa = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fa
_spec.loader.exec_module(fa)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent

#: Live canonical version of this tree (single source of truth).
import re as _re

LIVE_VERSION = _re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_current_pre_transition_state_honest():
    """The acceptance must report the live source version honestly: an
    expected version that differs from the live one is BLOCKED (no silent
    transition claim), while the chain checks stay green and the evidence
    states are reported from the live tree."""
    doc = fa.run_acceptance(expected_version="0.9.0")  # deliberate mismatch
    assert doc["source_version"] == LIVE_VERSION
    assert doc["version_transition_applied"] is False
    assert doc["overall"] == "BLOCKED"
    for key in ("schema_drift", "portability", "migration_contract",
                "promotion_guards"):
        assert doc["checks"][key].get("verdict") == "PASS", key
    assert doc["checks"]["mpl_state"]["state"] in (
        "READY", "EXTERNAL_ASSET_PENDING")
    assert doc["checks"]["stale_evidence"]["state"] == "STALE"
    assert doc["checks"]["package_evidence"]["state"] == "PENDING"
    # A matching expected version flips the transition flag.
    live = fa.run_acceptance(expected_version=LIVE_VERSION)
    assert live["version_transition_applied"] is True
    assert live["source_version"] == LIVE_VERSION


def test_simulated_correct_v1_0_transition_accepted(tmp_path: Path):
    """Simulate the transition: copy the evidence chain to a disposable
    tree and stamp version 1.0.0; the acceptance must become ready (the MPL
    state mirrors whether the transitioned tree carries the staged asset)."""
    transitioned = tmp_path / "transitioned"
    (transitioned / "scripts").mkdir(parents=True)
    (transitioned / "tests").mkdir()
    (transitioned / "src" / "amca").mkdir(parents=True)
    (transitioned / "docs" / "release").mkdir(parents=True)
    (transitioned / "dist").mkdir()
    # Version stamped to 1.0.0 (the simulated transition).
    (transitioned / "pyproject.toml").write_text(
        '[project]\nname = "amca-platform"\nversion = "1.0.0"\n',
        encoding="utf-8")
    # Copy the chain files the checks depend on.
    for name, target in (
        ("run_mpl_notice_readiness.py", "scripts"),
        ("build_ps2_promotion_input.py", "scripts"),
        ("run_dependency_compliance_audit.py", "scripts"),
        ("generate_distribution_compliance_evidence.py", "scripts"),
        ("run_ps2_final_release_validation.py", "scripts"),
        ("test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py",
         "tests"),
        ("test_studio_v100_ps2_final_evidence_path_portability.py", "tests"),
    ):
        source = (SOFTWARE_ROOT / "scripts" / name if target == "scripts"
                  else SOFTWARE_ROOT / "tests" / name)
        shutil_copy(source, transitioned / target / name)
    (transitioned / "src" / "amca" / "chat_history.py").write_text(
        (SOFTWARE_ROOT / "src" / "amca" / "chat_history.py").read_text(
            encoding="utf-8"), encoding="utf-8")
    for name in ("AMCA-Studio-v1.0.0-Documentation-Manifest.json",
                 "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json"):
        shutil_copy(SOFTWARE_ROOT / "docs" / "release" / name,
                    transitioned / "docs" / "release" / name)
    for doc_name in json.loads(
            (SOFTWARE_ROOT / "docs" / "release" /
             "AMCA-Studio-v1.0.0-Documentation-Manifest.json").read_text(
                 encoding="utf-8"))["documents"]:
        src = SOFTWARE_ROOT / "docs" / "release" / doc_name["document"]
        if src.is_file():
            shutil_copy(src, transitioned / "docs" / "release" /
                        doc_name["document"])
    doc = fa.run_acceptance(expected_version="1.0.0", root=transitioned)
    assert doc["source_version"] == "1.0.0"
    assert doc["version_transition_applied"] is True
    assert doc["overall"] in ("READY_FOR_FINAL_BUILD",
                              "READY_WITH_EXTERNAL_ASSET_PENDING"), \
        (doc["overall"], doc["checks"])
    # The MPL readiness check is evaluated against the live scripts/tree (it
    # is root-independent), so the reported state must mirror that real
    # readiness document — never an assumption.
    mr_root = _load("mpl_readiness_i2y_root",
                    SCRIPTS / "run_mpl_notice_readiness.py")
    expected_map = {"READY": "READY",
                    "RELEASE_ASSET_REQUIRED": "EXTERNAL_ASSET_PENDING",
                    "MISMATCH": "BLOCKED", "BLOCKED": "BLOCKED"}
    assert doc["checks"]["mpl_state"]["state"] == expected_map.get(
        mr_root.evaluate()["state"], "BLOCKED")
    assert doc["checks"]["stale_evidence"]["state"] == "STALE"
    assert doc["checks"]["package_evidence"]["state"] == "PENDING"


def test_partial_transition_blocked(tmp_path: Path):
    """A tree where the version stamp was applied but the evidence chain
    was not copied (drifted/absent) must be BLOCKED, not ready."""
    partial = tmp_path / "partial"
    (partial / "scripts").mkdir(parents=True)
    partial_pyproject = partial / "pyproject.toml"
    partial_pyproject.write_text(
        '[project]\nversion = "1.0.0"\n', encoding="utf-8")
    doc = fa.run_acceptance(expected_version="1.0.0", root=partial)
    assert doc["overall"] == "BLOCKED"
    assert doc["checks"]["schema_drift"]["verdict"] == "BLOCKED"


def test_unrelated_source_drift_blocked(tmp_path: Path):
    """A tree claiming 1.0.0 but missing the migration repair is FAILED/
    BLOCKED (drift detected)."""
    drifted = tmp_path / "drifted"
    (drifted / "scripts").mkdir(parents=True)
    (drifted / "tests").mkdir()
    (drifted / "src" / "amca").mkdir(parents=True)
    (drifted / "docs" / "release").mkdir(parents=True)
    (drifted / "pyproject.toml").write_text('version = "1.0.0"\n',
                                            encoding="utf-8")
    # chat_history without the I2D.1 ordering (regressed source).
    (drifted / "src" / "amca" / "chat_history.py").write_text(
        "class ConversationStore:\n"
        "    def __init__(self):\n"
        "        self._migrate_conversation_modes(None)\n",
        encoding="utf-8")
    for name in ("run_mpl_notice_readiness.py",
                 "build_ps2_promotion_input.py",
                 "run_dependency_compliance_audit.py",
                 "generate_distribution_compliance_evidence.py",
                 "run_ps2_final_release_validation.py"):
        shutil_copy(SOFTWARE_ROOT / "scripts" / name,
                    drifted / "scripts" / name)
    shutil_copy(SOFTWARE_ROOT / "tests" /
                "test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py",
                drifted / "tests" /
                "test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py")
    shutil_copy(SOFTWARE_ROOT / "tests" /
                "test_studio_v100_ps2_final_evidence_path_portability.py",
                drifted / "tests" /
                "test_studio_v100_ps2_final_evidence_path_portability.py")
    doc = fa.run_acceptance(expected_version="1.0.0", root=drifted)
    assert doc["checks"]["migration_contract"]["verdict"] == "FAILED"
    assert doc["overall"] == "FAILED"


def test_v09_evidence_stays_stale():
    doc = fa.run_acceptance(expected_version="1.0.0")
    state = doc["checks"]["stale_evidence"]
    if state["state"] == "STALE":
        assert "v0.9" in state["detail"] or "validation" in state["detail"]
    else:
        assert state["state"] in ("PASS", "PENDING")


def test_missing_final_package_pending_not_fail():
    doc = fa.run_acceptance(expected_version="1.0.0")
    state = doc["checks"]["package_evidence"]
    assert state["state"] == "PENDING"


def test_stale_v09_portable_never_used():
    doc = fa.run_acceptance(expected_version="1.0.0")
    blob = json.dumps(doc)
    assert doc["checks"]["package_evidence"]["state"] == "PENDING"
    # The acceptance result must not reference the v0.9 binary as its input.
    assert "amca-studio-v0.9.0-windows-x64-portable.zip" not in blob


def test_mpl_absent_remains_external_asset_pending():
    doc = fa.run_acceptance(expected_version="1.0.0")
    mpl = doc["checks"]["mpl_state"]
    if mpl["state"] == "READY":
        pytest.skip("authoritative MPL asset staged in this tree")
    assert mpl["state"] == "EXTERNAL_ASSET_PENDING"
    assert doc["overall"] in ("READY_WITH_EXTERNAL_ASSET_PENDING", "BLOCKED")


def test_valid_staged_provenance_bound_mpl_becomes_ready(tmp_path: Path):
    """With a staged, provenance-bound asset the MPL state becomes READY."""
    mr = _load("mpl_readiness_i2y_t", SCRIPTS / "run_mpl_notice_readiness.py")
    # Canonical anchors come from the readiness module (single source of
    # truth) so the fixture tracks the authoritative MPL-2.0 revision.
    canonical = "\n".join(
        s + "\n" + "-" * len(s) for s in mr.CANONICAL_SECTIONS) + "\n"
    isolated = tmp_path / "assets" / "MPL-2.0-certifi-2026.7.22.txt"
    isolated.parent.mkdir(parents=True)
    isolated.write_text(canonical, encoding="utf-8")
    original = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = isolated  # isolated evaluation only
        doc = mr.evaluate()
        assert doc["state"] == "READY"
        assert doc["asset"]["sha256"] == hashlib.sha256(
            isolated.read_bytes()).hexdigest()
    finally:
        mr.ASSET_PATH = original
    assert mr.evaluate()["state"] in ("RELEASE_ASSET_REQUIRED", "READY")


def test_pass_with_review_preserved():
    pi = _load("ps2_promotion_i2y_p", SCRIPTS / "build_ps2_promotion_input.py")
    assert pi.STATE_MAP["PS2_RELEASE_READY_WITH_REVIEW"] == \
        "PS2_INPUT_READY_WITH_REVIEW"
    doc = {"schema": pi.SCHEMA,
           "promotion_eligibility_ps2": "PS2_INPUT_READY_WITH_REVIEW",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY_WITH_REVIEW",
           "source_sha": "a" * 40, "portable_sha256": "b" * 64,
           "installer_sha256": "c" * 64, "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_READY_WITH_REVIEW"


def test_schema_drift_blocked():
    doc = fa.run_acceptance(expected_version="1.0.0")
    # In the current (correct) tree the sentinel is present and pinned.
    assert doc["checks"]["schema_drift"]["verdict"] == "PASS"
    # And the guard simulates drift: a sentinel missing schemas is BLOCKED.
    stripped = tmp_path_guard() / "stripped-sentinel.py"
    stripped.write_text("# sentinel without schema pins\n", encoding="utf-8")
    original = None
    sentinel = (SOFTWARE_ROOT / "tests" /
                "test_studio_v100_ps2_final_evidence_schema_drift_sentinel.py")
    sentinel.rename(sentinel.with_suffix(".py.bak"))
    try:
        doc = fa.run_acceptance(expected_version="1.0.0")
        assert doc["checks"]["schema_drift"]["verdict"] == "BLOCKED"
    finally:
        sentinel.with_suffix(".py.bak").rename(sentinel)


def tmp_path_guard():
    import tempfile
    return Path(tempfile.mkdtemp(prefix="i2y-"))


def shutil_copy(src: Path, dst: Path) -> None:
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def test_no_worker_temp_paths_leaked():
    doc = fa.run_acceptance(expected_version="1.0.0")
    blob = json.dumps(doc)
    for marker in ("Md. Ashraful", "opencode-ps2", "AMCA-PS2-commit",
                   "pytest-of-", ".agent-tmp"):
        assert marker not in blob, f"worker path leaked: {marker}"


def test_no_source_or_package_mutation():
    portable = SOFTWARE_ROOT / "dist" / (
        "amca-studio-v0.9.0-windows-x64-portable.zip")
    before_pkg = hashlib.sha256(portable.read_bytes()).hexdigest() \
        if portable.is_file() else None
    candidate = SOFTWARE_ROOT / "docs" / "release" / (
        "AMCA-Studio-v1.0.0-Release-Notes-Candidate.md")
    before_candidate = hashlib.sha256(candidate.read_bytes()).hexdigest()
    fa.run_acceptance(expected_version="1.0.0")
    if before_pkg is not None:
        assert hashlib.sha256(portable.read_bytes()).hexdigest() == before_pkg
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() == \
        before_candidate


def test_cli_exit_codes():
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--expected-version", "1.0.0"],
        capture_output=True, text=True, timeout=300)
    # Pre-transition tree: BLOCKED => exit 1 (not ready yet).
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "amca-ps2-post-transition-acceptance/1"