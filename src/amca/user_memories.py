"""AMCA account-level user memories (facilities panel).

Mirrors studio_memory.py but scoped to the operator account instead of a
project: this is the "Memory" tab (everything the assistant should retain
across chats) and the "User memories" tab (operator-authored entries only,
memory_type = 'operator'). Same guards: project isolation becomes
single-account by design, credential refusal, inspectable + purgeable.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .studio_memory import _clean_text, _now


class UserMemoryStore:
    """Manages durable, account-scoped memory items in SQLite."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS user_memories (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_ref TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    classification TEXT NOT NULL DEFAULT 'factual',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_user_memories_type
                   ON user_memories(memory_type, pinned)"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "memory_id": row["memory_id"],
            "memory_type": row["memory_type"],
            "title": row["title"],
            "content": row["content"],
            "source_ref": row["source_ref"],
            "pinned": bool(row["pinned"]),
            "confidence": float(row["confidence"]),
            "classification": str(row["classification"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "estimated_tokens": max(1, len(row["content"]) // 4),
        }

    def save_memory(
        self,
        *,
        title: str,
        content: str,
        memory_type: str = "general",
        source_ref: str | None = None,
        pinned: bool = False,
        confidence: float = 1.0,
        classification: str = "factual",
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        """Saves or updates an account-level memory item."""
        title = _clean_text(title, "title", 256, required=True)
        content = _clean_text(content, "content", 32000, required=True)
        memory_type = _clean_text(memory_type, "memory_type", 64, required=True)
        source_ref = _clean_text(source_ref, "source_ref", 256) if source_ref else None
        classification = _clean_text(classification, "classification", 64) or "factual"
        confidence = round(min(1.0, max(0.0, float(confidence))), 2)

        lowered = content.casefold()
        forbidden_tokens = ("sk-proj-", "ghp_", "xoxb-", "xapp-", "aws_secret", "-----begin private key-----")
        if any(token in lowered for token in forbidden_tokens):
            raise ValueError("User memory must not contain raw authentication tokens, private keys, or credentials")

        now = _now()
        target_id = memory_id or f"umem-{uuid4().hex[:12]}"

        with self._connect() as connection:
            connection.execute(
                """INSERT INTO user_memories (
                    memory_id, memory_type, title, content, source_ref, pinned, confidence, classification, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    memory_type=excluded.memory_type,
                    source_ref=excluded.source_ref,
                    pinned=excluded.pinned,
                    confidence=excluded.confidence,
                    classification=excluded.classification,
                    updated_at=excluded.updated_at""",
                (target_id, memory_type, title, content, source_ref, int(pinned), confidence, classification, now, now),
            )
            row = connection.execute(
                "SELECT * FROM user_memories WHERE memory_id = ?", (target_id,)
            ).fetchone()
            return self._record(row)

    def list_memories(
        self,
        *,
        memory_type: str | None = None,
        pinned_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lists account memories, newest pinned first."""
        with self._connect() as connection:
            if memory_type and pinned_only:
                rows = connection.execute(
                    """SELECT * FROM user_memories
                       WHERE memory_type = ? AND pinned = 1
                       ORDER BY updated_at DESC LIMIT ?""",
                    (memory_type, int(limit)),
                ).fetchall()
            elif memory_type:
                rows = connection.execute(
                    """SELECT * FROM user_memories
                       WHERE memory_type = ?
                       ORDER BY pinned DESC, updated_at DESC LIMIT ?""",
                    (memory_type, int(limit)),
                ).fetchall()
            elif pinned_only:
                rows = connection.execute(
                    """SELECT * FROM user_memories WHERE pinned = 1
                       ORDER BY updated_at DESC LIMIT ?""",
                    (int(limit),),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM user_memories
                       ORDER BY pinned DESC, updated_at DESC LIMIT ?""",
                    (int(limit),),
                ).fetchall()
        return [self._record(row) for row in rows]

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            return self._record(row) if row else None

    def delete_memory(self, memory_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM user_memories WHERE memory_id = ?", (memory_id,)
            )
            return cursor.rowcount > 0

    def clear_memories(self) -> int:
        """Purges all account memories (user privacy control)."""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM user_memories")
            return cursor.rowcount

    def toggle_pin(self, memory_id: str, *, pinned: bool | None = None) -> dict[str, Any] | None:
        current = self.get_memory(memory_id)
        if not current:
            return None
        new_pinned = not current["pinned"] if pinned is None else bool(pinned)
        with self._connect() as connection:
            connection.execute(
                "UPDATE user_memories SET pinned = ?, updated_at = ? WHERE memory_id = ?",
                (int(new_pinned), _now(), memory_id),
            )
        return self.get_memory(memory_id)

    def retrieve_context(
        self,
        *,
        query: str = "",
        token_budget: int = 2000,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Budget-constrained account-memory envelope (keyword-ranked, no vectors)."""
        memories = self.list_memories(limit=200)
        if types:
            allowed = set(types)
            memories = [m for m in memories if m["memory_type"] in allowed]

        if query:
            q_terms = set(re.findall(r"\w+", query.casefold()))

            def score(m: dict[str, Any]) -> tuple[int, int, float, str]:
                t_score = sum(1 for w in q_terms if w in m["title"].casefold() or w in m["content"].casefold())
                return (1 if m["pinned"] else 0, t_score, float(m.get("confidence", 1.0)), m["updated_at"])

            memories.sort(key=score, reverse=True)
        else:
            memories.sort(key=lambda m: (1 if m["pinned"] else 0, float(m.get("confidence", 1.0)), m["updated_at"]), reverse=True)

        selected: list[dict[str, Any]] = []
        total_tokens = 0
        for m in memories:
            cost = m["estimated_tokens"]
            if total_tokens + cost <= token_budget:
                selected.append(m)
                total_tokens += cost

        return {
            "memories": selected,
            "total_available": len(memories),
            "selected_count": len(selected),
            "estimated_tokens": total_tokens,
            "token_budget": token_budget,
            "pinned_count": sum(1 for m in selected if m["pinned"]),
        }
