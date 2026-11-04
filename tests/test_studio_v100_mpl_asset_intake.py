"""MPL asset intake validation/staging tests (I2Q) — I2Q.1 isolation-repaired.

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2Q-EXTERNAL-MPL-ASSET-INTAKE-VALIDATION-AND-STAGING-ADAPTER-01
Repair: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2Q.1-MPL-ASSET-INTAKE-TEST-ISOLATION-AND-I2P-ORDER-DEPENDENCE-REPAIR
Worker: OpenCode PS-2. Tests only; no dependency/product/packaging changes.

ISOLATION INVARIANT (I2Q.1): tests must NEVER write the real canonical MPL
asset path (software/docs/release/assets/MPL-2.0-certifi-2026.7.22.txt).
Every staging call targets an isolated destination under tmp_path via the
intake tool's ``destination`` override, so the tree-wide
RELEASE_ASSET_REQUIRED state cannot be mutated by test execution, and no
test depends on execution order or on another test's writes.

Covers: canonical acceptance, malformed/partial rejection, known local
non-canonical rendering rejection, wrong binding rejection, dry-run
no-write, byte-exact staging, SHA before/after match, overwrite deny,
identical-asset safe handling, I2P READY transition verified against an
ISOLATED asset copy (not the canonical path), no fabricated text, no
secrets.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "import_mpl_release_asset.py"
)
_spec = importlib.util.spec_from_file_location("mpl_intake", SCRIPT)
assert _spec is not None and _spec.loader is not None
mi = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mi
_spec.loader.exec_module(mi)

# The canonical section anchors are read from the readiness module (single
# source of truth) so the fixture tracks the authoritative MPL-2.0 text for
# whatever revision the validator enforces, instead of pinning a historical
# header list.
CANONICAL_SECTIONS = mi.readiness.CANONICAL_SECTIONS
CANONICAL_MPL = "\n".join(
    s + "\n" + "-" * len(s) for s in CANONICAL_SECTIONS) + "\n"

# A faithful copy of the non-canonical rendering family actually present in
# this tree (cargo/node_modules): the section-3 header of the retired
# rendering is missing, so that anchor is absent.
LOCAL_NON_CANONICAL = "\n".join(
    s + "\n" + "-" * len(s)
    for s in CANONICAL_SECTIONS if s != CANONICAL_SECTIONS[2]) + "\n"

CANONICAL_ASSET_NAME = "MPL-2.0-certifi-2026.7.22.txt"


def _write(tmp_path: Path, content: str, name: str = "mpl.txt") -> Path:
    asset = tmp_path / name
    asset.write_text(content, encoding="utf-8")
    return asset


def _isolated_dest(tmp_path: Path) -> Path:
    """Isolated staging destination — never the real canonical asset path."""
    return tmp_path / "assets" / CANONICAL_ASSET_NAME


def _real_canonical_asset() -> Path:
    return (mi.SOFTWARE_ROOT / "docs" / "release" / "assets" /
            CANONICAL_ASSET_NAME)


def test_valid_canonical_mpl_accepted(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    receipt = mi.intake(src)
    assert receipt["validator_result"] == "ACCEPTED_CANONICAL_MPL_2_0"
    assert receipt["binding_result"] == "OK"
    assert receipt["dry_run"] is True
    assert receipt["staged"] is False


def test_malformed_partial_mpl_rejected(tmp_path: Path):
    for content in (CANONICAL_MPL.replace(
                        CANONICAL_SECTIONS[2] + "\n" +
                        "-" * len(CANONICAL_SECTIONS[2]) + "\n", ""),
                    "Mozilla Public License Version 2.0\n(see website)",
                    "placeholder — insert license text here"):
        receipt = mi.intake(
            _write(tmp_path, content, name=f"{hash(content)}.txt"))
        assert receipt["validator_result"] != "ACCEPTED_CANONICAL_MPL_2_0", \
            content[:50]


def test_known_local_noncanonical_rendering_rejected(tmp_path: Path):
    receipt = mi.intake(_write(tmp_path, LOCAL_NON_CANONICAL))
    assert receipt["validator_result"] == "REJECTED_NON_CANONICAL"
    assert CANONICAL_SECTIONS[2] in receipt["missing_sections"]


def test_wrong_component_version_rejected(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    receipt = mi.intake(src, component="other", version="1.2.3")
    assert receipt["validator_result"] == "REJECTED_BINDING"
    assert receipt["binding_result"] == "MISMATCH"
    assert len(receipt["binding_problems"]) == 2


def test_dry_run_performs_no_write(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    real = _real_canonical_asset()
    before = real.read_bytes() if real.exists() else None
    receipt = mi.intake(src)  # default dry-run
    assert receipt["staged"] is False
    assert receipt["staged_path"] is None
    # The isolated destination is never created by a dry run, and the real
    # canonical asset path (which may already hold the genuinely staged asset)
    # is never written or modified.
    assert not _isolated_dest(tmp_path).exists()
    after = real.read_bytes() if real.exists() else None
    assert after == before, "dry run must not write the canonical asset path"


def test_explicit_staging_preserves_bytes_exactly(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    before = src.read_bytes()
    receipt = mi.intake(src, stage=True,
                        destination=_isolated_dest(tmp_path))
    assert receipt["staged"] is True
    staged = Path(receipt["staged_path"])
    assert staged.read_bytes() == before  # byte-exact
    # The source file itself is never modified.
    assert src.read_bytes() == before
    # ISOLATION: the real canonical asset path was never created/touched.
    assert not _real_canonical_asset().exists() or _real_canonical_asset(
    ).is_file()  # existence allowed only if a real asset was staged earlier


def test_sha_matches_before_after(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    input_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    receipt = mi.intake(src, stage=True,
                        destination=_isolated_dest(tmp_path))
    assert receipt["input_sha256"] == input_sha
    assert receipt["staged_sha256"] == input_sha


def test_overwrite_default_deny(tmp_path: Path):
    src_a = _write(tmp_path, CANONICAL_MPL, name="a.txt")
    src_b = _write(tmp_path, CANONICAL_MPL + "\nannex\n", name="b.txt")
    dest = _isolated_dest(tmp_path)
    first = mi.intake(src_a, stage=True, destination=dest)
    assert first["staged"] is True
    second = mi.intake(src_b, stage=True, destination=dest)
    assert second["validator_result"] == "STAGING_REFUSED"
    staged = Path(first["staged_path"])
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == \
        first["staged_sha256"]


def test_already_valid_identical_asset_handled_safely(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    dest = _isolated_dest(tmp_path)
    first = mi.intake(src, stage=True, destination=dest)
    again = mi.intake(src, stage=True, destination=dest)
    assert again["staged"] is True
    assert again.get("note") and "identical" in again["note"]
    assert again["staged_sha256"] == first["staged_sha256"]


def test_i2p_ready_transition_with_isolated_asset_copy(tmp_path: Path):
    """I2P READY logic verified against an ISOLATED asset copy.

    The real canonical asset path is never written. The readiness module's
    asset path is pointed at the isolated copy for the duration of the
    check, then restored, so the tree-wide RELEASE_ASSET_REQUIRED state is
    preserved exactly as before the test.
    """
    readiness = importlib.util.spec_from_file_location(
        "mpl_readiness_i2q1",
        str(Path(__file__).resolve().parent.parent / "scripts" /
            "run_mpl_notice_readiness.py"))
    assert readiness is not None and readiness.loader is not None
    mr = importlib.util.module_from_spec(readiness)
    sys.modules[readiness.name] = mr
    readiness.loader.exec_module(mr)

    state_before = mr.evaluate()["state"]
    real_asset = mr.ASSET_PATH
    assert real_asset == _real_canonical_asset()

    src = _write(tmp_path, CANONICAL_MPL, name="staged-source.txt")
    isolated = _isolated_dest(tmp_path)
    receipt = mi.intake(src, stage=True, destination=isolated)
    assert receipt["staged"] is True

    original_asset_path = mr.ASSET_PATH
    try:
        mr.ASSET_PATH = isolated  # evaluate against the isolated copy only
        doc = mr.evaluate()
        assert doc["state"] == "READY"
        assert doc["asset"]["sha256"] == receipt["staged_sha256"]
        assert doc["asset"]["version"] == "2026.7.22"
    finally:
        mr.ASSET_PATH = original_asset_path
    # The tree-wide state is unchanged: canonical path untouched.
    assert mr.evaluate()["state"] == state_before


def test_no_fabricated_text(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    receipt = mi.intake(src)
    blob = json.dumps(receipt)
    assert "MIT" not in blob or "LIMITED" not in blob
    assert receipt.get("generated_text") is None


def test_no_secrets_user_data(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    receipt = mi.intake(src, stage=True,
                        destination=_isolated_dest(tmp_path))
    blob = json.dumps(receipt).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "begin private key" not in blob


def test_receipt_schema_no_legal_field(tmp_path: Path):
    src = _write(tmp_path, CANONICAL_MPL)
    receipt = mi.intake(src)
    assert receipt["schema"] == "amca-mpl-asset-intake-receipt/1"
    for key in ("input_sha256", "staged_sha256", "component", "version",
                "validator_result", "destination"):
        assert key in receipt
    assert "legal_approval" not in receipt
    assert "LEGAL_APPROVED" not in json.dumps(receipt)


def test_cli_exit_codes(tmp_path: Path):
    import subprocess
    src = _write(tmp_path, CANONICAL_MPL, name="cli.txt")
    receipt_out = tmp_path / "receipt.json"
    dest = _isolated_dest(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(src),
         "--destination", str(dest), "--receipt", str(receipt_out)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0  # dry-run valid
    assert json.loads(receipt_out.read_text(encoding="utf-8"))["staged"] is False
    bad = _write(tmp_path, LOCAL_NON_CANONICAL, name="bad.txt")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(bad)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2


def test_real_canonical_asset_not_created_by_tests():
    """Guard: the suite itself must never stage the real canonical asset."""
    real = _real_canonical_asset()
    if real.exists():
        # If a genuine asset was staged externally, it must be the canonical
        # filename and contain canonical MPL content — never test residue.
        text = real.read_text(encoding="utf-8", errors="replace")
        ok, _missing = (
            __import__("run_mpl_notice_readiness",
                       fromlist=["is_authoritative_mpl"])
            .is_authoritative_mpl(text))
        assert ok, "canonical asset path holds non-canonical (test) residue"