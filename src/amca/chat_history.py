"""Durable desktop conversation history stored alongside canonical AMCA state."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Sequence
from uuid import uuid4


MAX_CONVERSATIONS = 200
MAX_MESSAGE_CHARS = 64 * 1024
MAX_TITLE_CHARS = 120
MAX_PARTICIPANTS = 8

_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|gsk|xox[baprs]|gh[pousr])[-_][A-Za-z0-9_-]{16,}\b", re.I),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.I),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_text(value: str, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} must not exceed {limit:,} characters")
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise ValueError(f"{field} appears to contain credential material and was not saved")
    return value


def validate_message_text(value: str) -> str:
    """Validate user-visible chat text before any AMCA persistence occurs."""

    value = _safe_text(value, field="message", limit=MAX_MESSAGE_CHARS)
    if not value:
        raise ValueError("message is required")
    return value


def _participants(value: Sequence[str]) -> tuple[str, ...]:
    participants = tuple(dict.fromkeys(str(item).strip() for item in value))
    if any(not item for item in participants):
        raise ValueError("participant IDs must not be empty")
    if len(participants) > MAX_PARTICIPANTS:
        raise ValueError(f"a conversation supports at most {MAX_PARTICIPANTS} participants")
    return participants


CONVERSATION_MODES = ("solo", "joint", "independent_discuss", "parallel", "adaptive_hybrid")

_MODE_SQL_LIST = ", ".join(f"'{mode}'" for mode in CONVERSATION_MODES)


def _mode(value: str) -> str:
    if value not in CONVERSATION_MODES:
        raise ValueError(
            "conversation mode must be one of: " + ", ".join(CONVERSATION_MODES)
        )
    return value


def _preview(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:117] + "..." if len(normalized) > 120 else normalized


class ConversationStore:
    """Small transactional store for user-visible desktop conversations.

    Conversation records do not replace canonical ProjectState.  They point to
    the latest project/run while messages retain their own project/run links.
    Each operation opens a short-lived SQLite connection so desktop service
    shutdown never leaves a database handle behind.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if self.path != Path(":memory:"):
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS desktop_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('solo', 'joint', 'independent_discuss', 'parallel', 'adaptive_hybrid')),
                    participant_ids_json TEXT NOT NULL,
                    workspace_project_id TEXT,
                    project_id TEXT,
                    active_run_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                );

                CREATE TABLE IF NOT EXISTS desktop_conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    meta TEXT,
                    run_id TEXT,
                    project_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (conversation_id, ordinal),
                    FOREIGN KEY (conversation_id)
                        REFERENCES desktop_conversations(conversation_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_desktop_conversations_updated
                    ON desktop_conversations(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_desktop_messages_conversation
                    ON desktop_conversation_messages(conversation_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_desktop_messages_run
                    ON desktop_conversation_messages(run_id);
                """
            )
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(desktop_conversations)")}
            if "workspace_project_id" not in columns:
                connection.execute("ALTER TABLE desktop_conversations ADD COLUMN workspace_project_id TEXT")
            # I2D.1 ordering: the mode-widening rebuild SELECTs
            # workspace_project_id, so the column must exist before the
            # rebuild runs (pre-C1 databases have neither the new CHECK
            # nor the column).
            self._migrate_conversation_modes(connection)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_desktop_conversations_workspace ON desktop_conversations(workspace_project_id, updated_at DESC)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _migrate_conversation_modes(connection: sqlite3.Connection) -> None:
        """Widen a pre-existing binary mode CHECK constraint in place.

        Older databases constrain desktop_conversations.mode to
        ('solo', 'joint').  The rebuild copies every row verbatim inside one
        transaction; modern SQLite repoints the messages table's foreign key
        when the parent table is renamed, so no message history is touched.
        """

        existing = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'desktop_conversations'"
        ).fetchone()
        if existing is None or "independent_discuss" in str(existing["sql"]):
            return
        # The standard SQLite rebuild recipe requires foreign keys disabled
        # around the copy; the pragma only takes effect outside a transaction.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Individual statements (not executescript, which would commit the
            # open transaction before running).
            connection.execute(
                f"""
                CREATE TABLE desktop_conversations_new (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ({_MODE_SQL_LIST})),
                    participant_ids_json TEXT NOT NULL,
                    workspace_project_id TEXT,
                    project_id TEXT,
                    active_run_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO desktop_conversations_new
                    SELECT
                        conversation_id, title, mode, participant_ids_json,
                        workspace_project_id, project_id, active_run_id,
                        status, created_at, updated_at, last_message_at
                    FROM desktop_conversations
                """
            )
            connection.execute("DROP TABLE desktop_conversations")
            connection.execute(
                "ALTER TABLE desktop_conversations_new RENAME TO desktop_conversations"
            )
            # DROP removes the old table's indexes; restore the standard
            # conversation indexes (IF NOT EXISTS: harmless when __init__
            # recreates them afterwards, and required when the rebuild runs
            # after the schema setup, as ordered by the I2D.1 repair).
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_desktop_conversations_updated "
                "ON desktop_conversations(updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_desktop_conversations_workspace "
                "ON desktop_conversations(workspace_project_id, updated_at DESC)"
            )
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        participants = json.loads(row["participant_ids_json"])
        return {
            "conversation_id": row["conversation_id"],
            "title": row["title"],
            "mode": row["mode"],
            "participant_ids": participants,
            "workspace_project_id": row["workspace_project_id"],
            "project_id": row["project_id"],
            "active_run_id": row["active_run_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_message_at": row["last_message_at"],
            "message_count": int(row["message_count"]),
            "last_message_preview": _preview(row["last_message"] or ""),
        }

    def create(
        self,
        *,
        title: str = "New chat",
        mode: str = "solo",
        participant_ids: Sequence[str] = (),
        conversation_id: str | None = None,
        workspace_project_id: str | None = None,
    ) -> dict[str, Any]:
        title = _safe_text(title, field="title", limit=MAX_TITLE_CHARS) or "New chat"
        mode = _mode(mode)
        participants = _participants(participant_ids)
        if mode == "solo" and len(participants) > 1:
            raise ValueError("a Solo conversation may save at most one participant")
        identifier = conversation_id or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            raise ValueError("invalid conversation ID")
        created_at = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO desktop_conversations (
                    conversation_id, title, mode, participant_ids_json,
                    workspace_project_id, project_id, active_run_id, status, created_at, updated_at,
                    last_message_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 'idle', ?, ?, NULL)
                """,
                (
                    identifier,
                    title,
                    mode,
                    json.dumps(participants, separators=(",", ":")),
                    workspace_project_id,
                    created_at,
                    created_at,
                ),
            )
        document = self.get(identifier)
        assert document is not None
        return document["conversation"]

    def set_workspace_project(self, conversation_id: str, workspace_project_id: str | None) -> dict[str, Any]:
        if workspace_project_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", workspace_project_id):
            raise ValueError("invalid Studio Project ID")
        with self._transaction() as connection:
            changed = connection.execute(
                "UPDATE desktop_conversations SET workspace_project_id = ?, updated_at = ? WHERE conversation_id = ?",
                (workspace_project_id, _now(), conversation_id),
            ).rowcount
            if not changed:
                raise ValueError("conversation not found")
        document = self.get(conversation_id)
        assert document is not None
        return document["conversation"]

    def list(self, *, search: str = "") -> list[dict[str, Any]]:
        search = _safe_text(search, field="search", limit=200)
        pattern = f"%{search}%"
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*,
                    COUNT(m.message_id) AS message_count,
                    COALESCE((
                        SELECT latest.content
                        FROM desktop_conversation_messages AS latest
                        WHERE latest.conversation_id = c.conversation_id
                        ORDER BY latest.ordinal DESC LIMIT 1
                    ), '') AS last_message
                FROM desktop_conversations AS c
                LEFT JOIN desktop_conversation_messages AS m
                    ON m.conversation_id = c.conversation_id
                WHERE ? = '' OR c.title LIKE ? OR EXISTS (
                    SELECT 1 FROM desktop_conversation_messages AS searched
                    WHERE searched.conversation_id = c.conversation_id
                      AND searched.content LIKE ?
                )
                GROUP BY c.conversation_id
                ORDER BY COALESCE(c.last_message_at, c.updated_at) DESC
                LIMIT ?
                """,
                (search, pattern, pattern, MAX_CONVERSATIONS),
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.*,
                    COUNT(m.message_id) AS message_count,
                    COALESCE((
                        SELECT latest.content
                        FROM desktop_conversation_messages AS latest
                        WHERE latest.conversation_id = c.conversation_id
                        ORDER BY latest.ordinal DESC LIMIT 1
                    ), '') AS last_message
                FROM desktop_conversations AS c
                LEFT JOIN desktop_conversation_messages AS m
                    ON m.conversation_id = c.conversation_id
                WHERE c.conversation_id = ?
                GROUP BY c.conversation_id
                """,
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            messages = connection.execute(
                """
                SELECT message_id, conversation_id, ordinal, role, content,
                       meta, run_id, project_id, created_at
                FROM desktop_conversation_messages
                WHERE conversation_id = ? ORDER BY ordinal
                """,
                (conversation_id,),
            ).fetchall()
        return {
            "conversation": self._summary(row),
            "messages": [dict(message) for message in messages],
        }

    def find_by_project(self, project_id: str) -> dict[str, Any] | None:
        """Return the newest conversation attached to a project, if any."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT conversation_id FROM desktop_conversations
                WHERE project_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return self.get(str(row["conversation_id"])) if row is not None else None

    def update_preferences(
        self,
        conversation_id: str,
        *,
        mode: str,
        participant_ids: Sequence[str],
    ) -> dict[str, Any]:
        mode = _mode(mode)
        participants = _participants(participant_ids)
        if mode == "solo" and len(participants) > 1:
            raise ValueError("a Solo conversation may save at most one participant")
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE desktop_conversations
                SET mode = ?, participant_ids_json = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    mode,
                    json.dumps(participants, separators=(",", ":")),
                    _now(),
                    conversation_id,
                ),
            ).rowcount
            if not changed:
                raise ValueError("conversation not found")
        document = self.get(conversation_id)
        assert document is not None
        return document["conversation"]

    def rename(self, conversation_id: str, title: str) -> dict[str, Any]:
        title = _safe_text(title, field="title", limit=MAX_TITLE_CHARS)
        if not title:
            raise ValueError("title is required")
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE desktop_conversations SET title = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (title, _now(), conversation_id),
            ).rowcount
            if not changed:
                raise ValueError("conversation not found")
        document = self.get(conversation_id)
        assert document is not None
        return document["conversation"]

    def add_message(
        self,
        conversation_id: str,
        *,
        message_id: str,
        role: str,
        content: str,
        meta: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("message role must be user, assistant, or system")
        content = validate_message_text(content)
        meta = _safe_text(meta or "", field="message metadata", limit=2_000) or None
        timestamp = created_at or _now()
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT message_id, conversation_id, ordinal, role, content,
                       meta, run_id, project_id, created_at
                FROM desktop_conversation_messages WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
            expected = (conversation_id, role, content, meta, run_id, project_id)
            if existing is not None:
                actual = tuple(
                    existing[key]
                    for key in (
                        "conversation_id",
                        "role",
                        "content",
                        "meta",
                        "run_id",
                        "project_id",
                    )
                )
                if actual != expected:
                    raise ValueError("message ID collision")
                return dict(existing)
            conversation = connection.execute(
                "SELECT title FROM desktop_conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ValueError("conversation not found")
            ordinal = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), 0) + 1
                    FROM desktop_conversation_messages WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO desktop_conversation_messages (
                    message_id, conversation_id, ordinal, role, content, meta,
                    run_id, project_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    conversation_id,
                    ordinal,
                    role,
                    content,
                    meta,
                    run_id,
                    project_id,
                    timestamp,
                ),
            )
            title = conversation["title"]
            if role == "user" and title == "New chat":
                normalized = " ".join(content.split())
                title = normalized[:57] + "..." if len(normalized) > 60 else normalized
            connection.execute(
                """
                UPDATE desktop_conversations
                SET title = ?, updated_at = ?, last_message_at = ?
                WHERE conversation_id = ?
                """,
                (title, timestamp, timestamp, conversation_id),
            )
        return {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "ordinal": ordinal,
            "role": role,
            "content": content,
            "meta": meta,
            "run_id": run_id,
            "project_id": project_id,
            "created_at": timestamp,
        }

    def attach_project(
        self, conversation_id: str, *, project_id: str, status: str
    ) -> None:
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE desktop_conversations
                SET project_id = ?, status = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (project_id, status, _now(), conversation_id),
            ).rowcount
            if not changed:
                raise ValueError("conversation not found")

    def attach_run(
        self, conversation_id: str, *, run_id: str, project_id: str
    ) -> None:
        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE desktop_conversations
                SET project_id = ?, active_run_id = ?, status = 'running', updated_at = ?
                WHERE conversation_id = ?
                """,
                (project_id, run_id, _now(), conversation_id),
            ).rowcount
            if not changed:
                raise ValueError("conversation not found")

    def finish_run(
        self,
        conversation_id: str,
        *,
        run_id: str,
        role: str,
        content: str,
        meta: str,
        status: str,
        project_id: str,
        created_at: str | None = None,
    ) -> None:
        self.add_message(
            conversation_id,
            message_id=f"run-{run_id}-result",
            role=role,
            content=content,
            meta=meta,
            run_id=run_id,
            project_id=project_id,
            created_at=created_at,
        )
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE desktop_conversations
                SET active_run_id = CASE WHEN active_run_id = ? THEN NULL ELSE active_run_id END,
                    project_id = ?, status = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (run_id, project_id, status, _now(), conversation_id),
            )

    def clear_run(
        self,
        conversation_id: str,
        *,
        run_id: str,
        status: str,
        project_id: str,
    ) -> None:
        """Clear a terminal run even when its public response is not persistable."""

        with self._transaction() as connection:
            changed = connection.execute(
                """
                UPDATE desktop_conversations
                SET active_run_id = CASE WHEN active_run_id = ? THEN NULL ELSE active_run_id END,
                    project_id = ?, status = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (run_id, project_id, status, _now(), conversation_id),
            ).rowcount
            if not changed:
                raise ValueError("conversation not found")

    def delete(self, conversation_id: str) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT active_run_id FROM desktop_conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("conversation not found")
            if row["active_run_id"]:
                raise ValueError("stop the active run before deleting this conversation")
            connection.execute(
                "DELETE FROM desktop_conversations WHERE conversation_id = ?",
                (conversation_id,),
            )


__all__ = [
    "ConversationStore",
    "MAX_CONVERSATIONS",
    "MAX_PARTICIPANTS",
    "validate_message_text",
]
