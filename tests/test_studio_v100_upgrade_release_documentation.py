"""Release-document consistency tests for the frozen upgrade guide (I2G).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2G-FINAL-UPGRADE-GUIDE-ROLLBACK-SAFETY-AND-MIGRATION-LIMITATIONS-FREEZE-01
Worker: OpenCode PS-2. Tests only; no production-code changes.

Verifies the user-facing Upgrade and Recovery Guide is evidence-consistent,
conservative, and frozen: all required topics present, no unsupported
rollback/final-certification claims, no secrets, no contradiction of the
migration evidence contract, historical v0.9 labels intact.
"""

from __future__ import annotations

from pathlib import Path

GUIDE_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "release"
    / "AMCA-Studio-v1.0.0-Upgrade-and-Recovery-Guide.md"
)
CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "release"
    / "AMCA-Studio-v1.0.0-Migration-Certification-Evidence-Contract.md"
)


def _guide() -> str:
    assert GUIDE_PATH.is_file(), f"upgrade guide missing: {GUIDE_PATH}"
    return GUIDE_PATH.read_text(encoding="utf-8")


def _flat() -> str:
    """Guide text with markdown emphasis and line breaks normalized."""
    return " ".join(_guide().replace("*", "").split()).casefold()


def test_guide_covers_required_topics():
    text = _flat()
    for topic in (
        "upgrading from v0.9.0",
        "automatic migration",
        "backup before upgrade",
        "non-destructive restore",
        "migration fails",
        "pre-c1",
        "workspace",
        "preservation",
        "verify backups",
        "limitations",
        "recovery procedure",
        "downgrade",
    ):
        assert topic in text, f"guide missing required topic: {topic}"


def test_guide_claims_no_unsupported_rollback():
    raw = _guide()
    # The single legitimate denial is allowlisted; everything else claiming
    # rollback support is forbidden.
    denial = "AMCA does not implement automatic rollback"
    assert denial in raw
    scanned = _flat().replace(denial.casefold(), "")
    for forbidden in (
        "automatic rollback",
        "rollback is supported",
        "rollback of a migrated database",
        "safely downgrade",
        "downgrade is supported",
        "revert the migration",
    ):
        assert forbidden not in scanned, (
            f"guide claims unsupported rollback behavior: {forbidden!r}")


def test_guide_claims_no_premature_final_certification():
    text = _flat()
    assert "does not certify any final v1.0 package" in text
    for forbidden in (
        "final v1.0 package certified",
        "shipping build certified",
        "certified for final release",
        "final release certified",
    ):
        assert forbidden not in text, (
            f"guide prematurely claims final certification: {forbidden!r}")


def test_guide_exposes_no_secrets():
    text = _guide()
    assert "BEGIN PRIVATE KEY" not in text
    lowered = text.casefold()
    assert "sk-proj-" not in lowered
    assert "ghp_" not in lowered
    assert "xoxb-" not in lowered


def test_guide_consistent_with_evidence_contract():
    assert CONTRACT_PATH.is_file(), f"evidence contract missing: {CONTRACT_PATH}"
    text = _guide()
    assert "AMCA-Studio-v1.0.0-Migration-Certification-Evidence-Contract.md" in text
    flat = _flat()
    assert "non-destructive" in flat
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "require_final_package" in contract or "final-package rerun rule" in contract
    # The guide must not promise what the contract withholds.
    assert "rerun" in flat


def test_guide_preserves_historical_labels():
    text = _guide()
    flat = _flat()
    assert "v0.9.0" in text
    assert "pre-C1" in text
    assert "preserved as-is" in flat
    assert "v0.9 records rewritten" not in flat
    assert "relabeled to v1.0" not in flat


def test_guide_freezes_limitations_and_downgrade_warning():
    text = _flat()
    for item in (
        "arbitrary downgrade",
        "cross-platform",
        "future schema",
        "filesystem corruption",
    ):
        assert item in text, f"frozen limitation missing: {item}"
    assert "only supported way back" in text
    assert "verified backup/restore path" in text
