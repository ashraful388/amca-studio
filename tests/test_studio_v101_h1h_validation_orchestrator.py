"""H1H real-artifact validation orchestrator tests (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1H-REAL-ARTIFACT-VALIDATION-ORCHESTRATOR-AND-FAILFAST-GATE-01
Tooling only: builds nothing, installs nothing, promotes nothing.
Proves the fail-fast gate stops the chain immediately for every listed
rejection, valid inputs proceed through the ordered chain, downstream
validators are not invoked after a BLOCKED gate, and v1.0.0 historical
validation is unaffected.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
TMP = SOFTWARE_ROOT / "dist" / "h1h-tmp"

MSI_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SOURCE_SHA = "ab" * 20
V100_INSTALLER_SHA = (
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1")

spec = importlib.util.spec_from_file_location(
    "h1h_orch", SCRIPTS / "run_v101_release_validation.py")
orch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orch)

spec2 = importlib.util.spec_from_file_location(
    "h1h_ab", SCRIPTS / "v101_artifact_binding.py")
ab = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ab)

spec3 = importlib.util.spec_from_file_location(
    "h1h_mc", SCRIPTS / "generate_migration_certification_evidence.py")
mc = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(mc)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def inputs():
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    exe = TMP / "amca-studio-v1.0.1-windows-x64-setup.exe"
    exe.write_bytes(b"MZ" + b"x" * 500)
    msi = TMP / "amca-studio-v1.0.1-windows-x64.msi"
    msi.write_bytes(MSI_MAGIC + b"y" * 500)
    zip_ = TMP / "amca-studio-v1.0.1-windows-x64-portable.zip"
    zip_.write_bytes(b"PK\x03\x04" + b"z" * 500)
    record = {f: "PASS" for f in ab.MSI_CONTRACT_FIELDS}
    record["msi_sha256"] = _sha(msi)
    record["size_bytes"] = msi.stat().st_size
    doc = {
        "release_version": "1.0.1",
        "source_sha": SOURCE_SHA,
        "artifacts": {
            "exe": {"path": str(exe), "sha256": _sha(exe)},
            "msi": {"path": str(msi), "sha256": _sha(msi)},
            "portable": {"path": str(zip_), "sha256": _sha(zip_)},
        },
        "msi_execution": record,
        "model_evidence": {"MODEL_RESPONSE_RECEIVED": "YES",
                           "source": "physical-desktop-run",
                           "exact_response":
                               "AMCA v1.0.1 physical test successful.",
                           # H2C.2A: evidence must be bound to the exact
                           # candidate (source + all three artifact hashes)
                           "source_sha": SOURCE_SHA,
                           "artifact_sha256": {"exe": _sha(exe),
                                               "msi": _sha(msi),
                                               "portable": _sha(zip_)},
                           "model_id": "qwen3:1.7b",
                           "ollama_models_path":
                               r"D:\ChatGPT\AMCA\ollama\models",
                           "run_id": "h2c2a-orchestrator-fixture-run",
                           "fresh_readiness_verified": "true",
                           "mock_or_replay_used": "NO",
                           "RUN_STATUS_COMPLETE": "YES"},
        "release_manifest": {
            "version": "1.0.1",
            "provenance": {"source_git_sha": SOURCE_SHA},
            "artifacts": [
                {"package_name": exe.name, "sha256": _sha(exe)},
                {"package_name": msi.name, "sha256": _sha(msi)},
                {"package_name": zip_.name, "sha256": _sha(zip_)}],
        },
        "sha256sums_text": (
            f"{_sha(exe)}  {exe.name}\n{_sha(msi)}  {msi.name}\n"
            f"{_sha(zip_)}  {zip_.name}\n"),
        "migration_evidence": mc.build_upgrade_path_evidence(
            exe=exe, msi=msi, portable=zip_, source_sha=SOURCE_SHA,
            msi_execution={**record, "install_result": "PASS",
                           "uninstall_result": "PASS",
                           "upgrade_result": "PASS"}),
    }
    yield doc
    shutil.rmtree(TMP, ignore_errors=True)


def _recording_steps(invoked, results=None):
    def make(name):
        def step():
            invoked.append(name)
            return (results or {}).get(name, ("PASS", ["stub"]))
        return step
    return {name: make(name) for name in orch.CHAIN_ORDER}


# --- proof 1: valid inputs proceed into the ordered chain --------------------

def test_valid_inputs_run_full_chain_in_order(inputs):
    invoked = []
    report = orch.run_validation(inputs,
                                 steps=_recording_steps(invoked))
    assert report["V101_FAILFAST_GATE"] == "PASS"
    assert invoked == orch.CHAIN_ORDER
    assert report["V101_PACKAGE_VALIDATION"] == "PASS"
    assert report["V101_PROMOTION_INPUT"] == "READY"
    assert report["V101_THREE_ARTIFACT_BINDING"] == "PASS"
    assert report["V101_MSI_EXECUTION"] == "PASS"
    assert report["V101_MODEL_PHYSICAL_EVIDENCE"] == "PASS"
    assert report["promotion_performed"] is False
    assert report["tag_performed"] is False


# --- proofs 2-9 + 10: fail-fast stops immediately, downstream never runs ----

def _assert_stops(inputs, mutate, needle):
    invoked = []
    mutate(inputs)
    report = orch.run_validation(inputs,
                                 steps=_recording_steps(invoked))
    assert report["V101_FAILFAST_GATE"] == "BLOCKED"
    assert invoked == [], "downstream validators ran after fail-fast"
    assert any(needle.casefold() in b.casefold()
               for b in report["blockers"]), report["blockers"]


def test_missing_msi_stops_immediately(inputs):
    _assert_stops(inputs, lambda i: i["artifacts"].pop("msi"), "msi")


def test_fake_msi_stops_immediately(inputs):
    def mutate(i):
        p = Path(i["artifacts"]["msi"]["path"])
        p.write_bytes(b"MZ" + b"not an msi")
        i["artifacts"]["msi"]["sha256"] = _sha(p)
    _assert_stops(inputs, mutate, "magic")


def test_v100_artifact_stops_immediately(inputs):
    _assert_stops(inputs,
                  lambda i: i["artifacts"]["exe"].update(
                      {"sha256": V100_INSTALLER_SHA}),
                  "v1.0.0")


def test_wrong_source_sha_stops_immediately(inputs):
    _assert_stops(inputs, lambda i: i["release_manifest"]["provenance"].update(
        {"source_git_sha": "cd" * 20}), "source sha disagreement")


def test_c_drive_path_stops_immediately(inputs):
    _assert_stops(inputs, lambda i: i["artifacts"]["exe"].update(
        {"path": "C:/Windows/Temp/setup.exe"}), "canonical root")


def test_missing_model_evidence_stops_immediately(inputs):
    _assert_stops(inputs, lambda i: i.pop("model_evidence"),
                  "MODEL_RESPONSE_RECEIVED")


def test_mocked_model_evidence_stops(inputs):
    _assert_stops(inputs, lambda i: i["model_evidence"].update(
        {"source": "mock-adapter"}), "mocked")


def test_msi_execution_pending_stops(inputs):
    _assert_stops(inputs, lambda i: i.update(
        {"msi_execution": {"state": "MSI_EXECUTION_PENDING"}}),
                  "MSI_EXECUTION_PENDING")


def test_manifest_missing_role_stops(inputs):
    def mutate(i):
        i["release_manifest"]["artifacts"] = [
            a for a in i["release_manifest"]["artifacts"]
            if not a["package_name"].endswith(".msi")]
    _assert_stops(inputs, mutate, "release manifest missing artifact role")


# --- proof 11: v1.0.0 historical validation unaffected -----------------------

def test_orchestrator_refuses_non_v101_versions(inputs):
    _assert_stops(inputs, lambda i: i.update({"release_version": "1.0.0"}),
                  "!= '1.0.1'")


# --- chain-level gates use the real accepted validators ----------------------

def test_real_binding_and_manifest_steps_pass(inputs):
    steps = orch.default_steps(inputs)
    assert steps["binding"]()[0] == "PASS"
    assert steps["release_manifest"]()[0] == "PASS"
    assert steps["sha256sums"]()[0] == "PASS"
    assert steps["msi_execution"]()[0] == "PASS"
    assert steps["migration"]()[0] == "PASS"
    assert steps["i2s"]()[0] == "PASS"


def test_msi_execution_contract_fields_enforced(inputs):
    invoked = []
    inputs["msi_execution"].pop("residual_installation_check")
    steps = _recording_steps(invoked)
    real_msi = orch.default_steps(inputs)["msi_execution"]
    steps["msi_execution"] = lambda: (
        invoked.append("msi_execution"), real_msi())[1]
    report = orch.run_validation(inputs, steps=steps)
    assert report["V101_FAILFAST_GATE"] == "PASS"  # record present
    assert report["V101_MSI_EXECUTION"] == "BLOCKED"
    assert report["V101_PACKAGE_VALIDATION"] == "BLOCKED"
    assert invoked == orch.CHAIN_ORDER[:4]  # stops at msi_execution


def test_output_fields_complete(inputs):
    report = orch.run_validation(inputs,
                                 steps=_recording_steps([]))
    for field in ("V101_FAILFAST_GATE", "V101_THREE_ARTIFACT_BINDING",
                  "V101_MSI_EXECUTION", "V101_MODEL_PHYSICAL_EVIDENCE",
                  "V101_PACKAGE_VALIDATION", "V101_PROMOTION_INPUT"):
        assert field in report
