"""Bounded append-only background lifecycle audit trail (B1.3D).

One JSON Lines file beside the desktop database records low-frequency
lifecycle events from both the Python backend and the Tauri shell (scheduler
start/stop, backend shutdown phases, autostart registration changes). Entries
are single-line JSON objects with bounded safe fields only — never secrets,
tokens, environment dumps, or file contents.

Durability model: best-effort cross-process appends; the file is compacted to
the newest MAX_ENTRIES records whenever it grows past COMPACT_AFTER, so it can
never grow unbounded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MAX_ENTRIES = 400
COMPACT_AFTER = 600


@dataclass(frozen=True)
class AuditEvent:
    event: str
    at: str
    success: bool
    detail: str | None = None

    def to_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": self.event,
            "at": self.at,
            "success": self.success,
        }
        if self.detail is not None:
            payload["detail"] = self.detail[:200]
        return payload


class BackgroundAudit:
    """Small truthful lifecycle audit writer/reader over one JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(self, event: str, *, success: bool = True, detail: str | None = None) -> bool:
        """Append one bounded event; returns False when writing failed."""

        entry = AuditEvent(
            event=str(event),
            at=datetime.now(UTC).isoformat(),
            success=bool(success),
            detail=str(detail) if detail is not None else None,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.to_line() + "\n")
            self._compact_if_needed()
            return True
        except OSError:
            # Auditing must never break the lifecycle action it observes.
            return False

    def _compact_if_needed(self) -> None:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return
        if len(lines) <= COMPACT_AFTER:
            return
        kept = lines[-MAX_ENTRIES:]
        temporary = self.path.with_suffix(".jsonl.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.writelines(kept)
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first decoded events, tolerating torn trailing writes."""

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(events) >= max(1, limit):
                break
        return events
