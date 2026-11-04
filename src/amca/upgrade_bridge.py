"""Legacy AMCA v1.0.0 -> AMCA Studio v1.0.1 upgrade bridge decision core.

Milestone: ZCODE-AMCA-V1.0.1-H2B.2-UPGRADE-BRIDGE-AND-FINAL-EVIDENCE-INTEGRITY-REPAIR-02
Worker: ZCode. Pure, dependency-free decision logic shared by the NSIS/PowerShell
bridge helper; every rule here is mirrored there and pinned by tests.

Why this exists: the shipped, immutable AMCA v1.0.0 NSIS installer was built
from certified source 3d225e31 where tauri.conf.json productName="AMCA", so it
registered the uninstall identity:

    uninstall key   Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\AMCA
    DisplayName     AMCA
    DisplayVersion  1.0.0

The v1.0.1 release identity is "AMCA Studio", so the stock Tauri template's
previous-install detection (SHCTX "${UNINSTKEY}") cannot see the legacy
installation and would install side-by-side (two products, two uninstall
entries, competing desktop/backend instances, port 8765 conflicts).

The bridge activates ONLY for the known shipped v1.0.0 identity. Every
corroboration signal must match (fail-closed): display name, display version,
an existing install location containing the expected binaries, and the main
binary's embedded ProductName/file version. Anything else — an unrelated
application merely named AMCA, a different version, a portable copy, or an
absent install — is left untouched with no side effects.

User data and the ai.amca.desktop identity are NEVER removal targets: the
removal set is confined to (1) the legacy install location recorded in the
registry, (2) the legacy uninstall registry key, and (3) the exact legacy
"AMCA" Start Menu / desktop shortcut names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

LEGACY_DISPLAY_NAME = "AMCA"
LEGACY_DISPLAY_VERSION = "1.0.0"
LEGACY_BINARY_PRODUCT_NAME = "AMCA"
LEGACY_UNINSTALL_KEY = (
    "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\AMCA"
)
LEGACY_MAIN_BINARY = "amca-desktop.exe"
LEGACY_BACKEND_BINARY = "amca-backend.exe"
LEGACY_START_MENU_SHORTCUT = "AMCA.lnk"
LEGACY_DESKTOP_SHORTCUT = "AMCA.lnk"

#: The bundle identifier is user-data identity and must never be a removal
#: target; its presence in any removal target fails the preservation contract.
USER_DATA_IDENTITY = "ai.amca.desktop"

REASON_ABSENT = "legacy AMCA v1.0.0 install absent - no migration side effects"
REASON_NOT_GENUINE = (
    "registry entry does not corroborate the known shipped AMCA v1.0.0 "
    "identity - bridge not activated, nothing touched"
)


@dataclass(frozen=True)
class LegacyInstallSignal:
    """Registry + filesystem observation of a candidate legacy installation."""

    display_name: Optional[str] = None
    display_version: Optional[str] = None
    publisher: Optional[str] = None
    install_location: Optional[str] = None
    uninstall_string: Optional[str] = None
    main_binary_present: bool = False
    main_binary_product_name: Optional[str] = None
    main_binary_file_version: Optional[str] = None

    @classmethod
    def absent(cls) -> "LegacyInstallSignal":
        return cls()


@dataclass(frozen=True)
class BridgeDecision:
    activated: bool
    reason: str
    removal_targets: Tuple[str, ...] = field(default=())
    legacy_install_location: Optional[str] = None
    detected: bool = False


def _version_prefix_matches(actual: Optional[str], expected: str) -> bool:
    """Accept '1.0.0' and '1.0.0.0' style strings; reject anything else."""
    if not actual:
        return False
    parts = [p for p in str(actual).strip().split(".")]
    expected_parts = expected.split(".")
    if len(parts) < len(expected_parts):
        return False
    return parts[: len(expected_parts)] == expected_parts


def removal_targets_for(install_location: str) -> Tuple[str, ...]:
    """The complete, closed removal set for a genuine legacy install.

    Exactly three kinds of targets, all app-owned, none user data:
    install location, uninstall registry key, exact legacy shortcuts.
    """

    return (
        f"install-location:{install_location}",
        f"registry-key:{LEGACY_UNINSTALL_KEY}",
        f"shortcut:StartMenu\\Programs\\{LEGACY_START_MENU_SHORTCUT}",
        f"shortcut:Desktop\\{LEGACY_DESKTOP_SHORTCUT}",
    )


def user_data_preservation_violations(decision: BridgeDecision) -> Tuple[str, ...]:
    """Fail the contract if any removal target leaves the legacy app scope.

    A target violates preservation when it (a) references the user-data
    identity/identifier anywhere, or (b) is a filesystem path outside the
    legacy install location (registry keys and the exact legacy shortcuts
    are app-owned by definition).
    """

    violations: list = []
    location = decision.legacy_install_location
    for target in decision.removal_targets:
        if USER_DATA_IDENTITY in target:
            violations.append(f"removal target references user-data identity: {target}")
            continue
        if target.startswith("install-location:"):
            path = target.split(":", 1)[1]
            if location and not path.startswith(location):
                violations.append(f"removal path outside legacy install location: {target}")
        # registry-key: and shortcut: targets are app-owned by construction
    return tuple(violations)


def decide_legacy_bridge(signal: LegacyInstallSignal) -> BridgeDecision:
    """Decide whether the legacy v1.0.0 upgrade bridge must run.

    Fail-closed: every corroboration signal must match; any mismatch (or an
    absent install) yields a no-activation decision with NO removal targets,
    so the caller cannot accidentally delete anything it did not fully identify.
    """

    detected = bool(signal.display_name or signal.display_version or signal.uninstall_string)
    if not detected:
        return BridgeDecision(False, REASON_ABSENT, (), None, False)

    checks = (
        signal.display_name == LEGACY_DISPLAY_NAME,
        signal.display_version == LEGACY_DISPLAY_VERSION,
        bool(signal.install_location),
        signal.main_binary_present,
        signal.main_binary_product_name == LEGACY_BINARY_PRODUCT_NAME,
        _version_prefix_matches(signal.main_binary_file_version, LEGACY_DISPLAY_VERSION),
    )
    if not all(checks):
        return BridgeDecision(False, REASON_NOT_GENUINE, (), signal.install_location, True)

    targets = removal_targets_for(signal.install_location or "")
    decision = BridgeDecision(
        activated=True,
        reason=(
            "genuine shipped AMCA v1.0.0 installation corroborated "
            "(DisplayName=AMCA, DisplayVersion=1.0.0, binary identity AMCA/1.0.0) "
            "- activate legacy upgrade bridge"
        ),
        removal_targets=targets,
        legacy_install_location=signal.install_location,
        detected=True,
    )
    if user_data_preservation_violations(decision):
        # Defensive: a genuine signal can never produce a violating target
        # set today; if a future edit makes it possible, fail closed here.
        return BridgeDecision(
            False,
            "user-data preservation contract violation detected - bridge refused",
            (),
            signal.install_location,
            True,
        )
    return decision
