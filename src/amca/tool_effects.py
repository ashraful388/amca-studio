"""Authoritative tool-effect metadata for recovery decisions.

Architecture correction, Work Order 09 section 2.  Recovery must NOT decide
replay safety by recognising tool names, prefixes or hard-coded lists spread
through recovery logic.  The Tool Fabric owns that fact; recovery asks.

Each governed tool declares:

* ``effect_class`` — what kind of change, if any, executing it makes;
* ``replay_safe`` — whether running it again is harmless;
* ``requires_approval`` — whether it is gated on an approval.

The rule that matters most is the default.  A tool with no metadata is
``UNKNOWN`` and ``replay_safe = False``: safety is never inferred from a name.
An unrecognised tool refuses the replay rather than being assumed harmless.
"""

from __future__ import annotations

import enum
from typing import Any, Mapping


class EffectClass(str, enum.Enum):
    """What executing a tool does to the world."""

    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    UNKNOWN = "unknown"


#: The Tool Fabric's authoritative table.  Every governed tool appears here.
#: A tool absent from this table is UNKNOWN and fails closed.
TOOL_EFFECTS: dict[str, dict[str, Any]] = {
    # Reading and inspecting change nothing, so replaying them is harmless.
    "filesystem.read": {"effect_class": EffectClass.READ_ONLY, "replay_safe": True,
                        "requires_approval": False},
    "filesystem.stat": {"effect_class": EffectClass.READ_ONLY, "replay_safe": True,
                        "requires_approval": False},
    "filesystem.list": {"effect_class": EffectClass.READ_ONLY, "replay_safe": True,
                        "requires_approval": False},
    "filesystem.search": {"effect_class": EffectClass.READ_ONLY, "replay_safe": True,
                          "requires_approval": False},
    "command.inspect": {"effect_class": EffectClass.READ_ONLY, "replay_safe": True,
                        "requires_approval": False},
    # Writing is NOT replay-safe by default.  It is tempting to call it
    # idempotent because the same content lands the same result — but the
    # replaying model is a DIFFERENT model, so the second write is different
    # content, not a no-op.  Only the read-only class is proven safe.
    "filesystem.write": {"effect_class": EffectClass.NON_IDEMPOTENT_WRITE,
                         "replay_safe": False, "requires_approval": True},
    "filesystem.mkdir": {"effect_class": EffectClass.IDEMPOTENT_WRITE, "replay_safe": False,
                         "requires_approval": True},
    "filesystem.create": {"effect_class": EffectClass.NON_IDEMPOTENT_WRITE,
                          "replay_safe": False, "requires_approval": True},
    # Appending, moving and trashing are not: replaying duplicates or loses data.
    "filesystem.append": {"effect_class": EffectClass.NON_IDEMPOTENT_WRITE,
                          "replay_safe": False, "requires_approval": True},
    "filesystem.move": {"effect_class": EffectClass.NON_IDEMPOTENT_WRITE,
                        "replay_safe": False, "requires_approval": True},
    "filesystem.copy": {"effect_class": EffectClass.NON_IDEMPOTENT_WRITE,
                        "replay_safe": False, "requires_approval": True},
    "filesystem.trash": {"effect_class": EffectClass.NON_IDEMPOTENT_WRITE,
                         "replay_safe": False, "requires_approval": True},
    # Running a command has effects AMCA cannot see or undo.
    "command.run": {"effect_class": EffectClass.EXTERNAL_SIDE_EFFECT, "replay_safe": False,
                    "requires_approval": True},
}

#: Only the read-only class is proven replay-safe.  Every write, command or
#: unrecognised tool refuses a replay, because the effect cannot be undone and
#: the replaying model would not reproduce the same content.

#: The metadata returned for anything the Tool Fabric does not know.
UNKNOWN_EFFECT: dict[str, Any] = {
    "effect_class": EffectClass.UNKNOWN,
    "replay_safe": False,
    "requires_approval": True,
}


def effect_for(tool_name: str) -> dict[str, Any]:
    """Authoritative effect metadata for a tool.

    Fails closed: an unknown tool is UNKNOWN and not replay-safe.  A name that
    merely looks read-only is not treated as read-only — that inference is the
    defect this module exists to remove.
    """

    entry = TOOL_EFFECTS.get(str(tool_name or "").strip())
    if entry is None:
        return dict(UNKNOWN_EFFECT)
    return dict(entry)


def replay_safe(tool_name: str) -> bool:
    """Whether re-running this tool is harmless.  Unknown tools are not."""

    return bool(effect_for(tool_name).get("replay_safe", False))


def task_replay_safe(tool_names: Any) -> tuple[bool, tuple[str, ...]]:
    """Whether a task that ran these tools can safely be replayed.

    Returns ``(safe, blocking_tools)``.  A task that ran ANY tool that is not
    proven replay-safe is not replayable, because the effect would happen twice.
    """

    blocking = tuple(
        sorted({str(name) for name in (tool_names or ()) if not replay_safe(str(name))})
    )
    return (not blocking), blocking


def describe_effects() -> list[dict[str, Any]]:
    """The whole table, for audit and for the UI to show what a tool will do."""

    return [
        {"tool": name, **entry}
        for name, entry in sorted(TOOL_EFFECTS.items())
    ]


__all__ = [
    "EffectClass",
    "TOOL_EFFECTS",
    "UNKNOWN_EFFECT",
    "describe_effects",
    "effect_for",
    "replay_safe",
    "task_replay_safe",
]
