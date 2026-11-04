"""Durable user-facing AMCA Studio Projects.

Studio Projects are containers for many conversations and shared resources.
They are deliberately separate from canonical execution ProjectState records,
which represent individual governed task runs.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat as stat_module
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

#: Ids in these prefixes name a chat, never a project.  A Studio Project may
#: not take an id that starts with one, so the two identity namespaces stay
#: disjoint instead of aliasing one another on a shared id.
CHAT_ID_PREFIXES: tuple[str, ...] = ("conv-", "desktop-")
_CHAT_ID_PREFIX = CHAT_ID_PREFIXES[0]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in result):
        raise ValueError(f"{name} must be {'1-' if required else '0-'}{maximum} characters")
    return result


#: Characters that cannot appear in a Windows directory name.
_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: Reserved DOS device names; Windows will not create a directory under one.
_RESERVED_DEVICE_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


def _is_link_or_reparse(path: Path) -> bool:
    """True when ``path`` is a symlink, junction or any other reparse point.

    A project folder is a place the user will keep their own work, so a root
    that silently redirects somewhere else is refused rather than accepted.
    """
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat_module.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def default_project_folder_base() -> Path:
    """Where new project folders are created when the user does not choose.

    Production uses the user's Documents folder. ``AMCA_DEFAULT_PROJECT_BASE``
    overrides it so the test suite never writes into the real Documents.
    """
    override = str(os.environ.get("AMCA_DEFAULT_PROJECT_BASE") or "").strip()
    if override:
        return Path(os.path.expandvars(override))
    return Path.home() / "Documents"


def default_project_folder(name: str) -> str:
    """The default folder for a new project: ``Documents/<Project Name>``."""
    cleaned = _INVALID_FOLDER_CHARS.sub("_", str(name or "").strip()).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].strip(" .")
    if not cleaned:
        cleaned = "AMCA Project"
    # A Project named "CON" (or COM1, LPT9, ...) yields a reserved DOS device
    # name, which Windows refuses to create as a directory. Prefixing keeps the
    # default usable instead of handing the user a folder that cannot exist.
    if cleaned.split(".")[0].upper() in _RESERVED_DEVICE_NAMES:
        cleaned = f"_{cleaned}"[:81]
    return str(default_project_folder_base() / cleaned)


def _validate_project_folder(raw: Any) -> Path:
    """Validate a user-supplied project folder and return its canonical form.

    Rejects device paths, relative paths and any root that traverses a link or
    reparse point.  The directory itself need not exist yet: AMCA creates it.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("a project folder is required")
    if "\x00" in text:
        raise ValueError("project folder contains an invalid character")
    if text.upper().startswith("\\\\") or "GLOBALROOT" in text.upper():
        raise ValueError("project folder must be a local path")
    # Reject UNC and device namespaces in BOTH slash spellings.  Checking only
    # for a leading double backslash let `//server/share` through, because
    # Path() turns it into a real UNC path, and `//?/C:/...` into a
    # `\\?\` device path.  Neither is a local folder on this PC.
    if _is_unc_or_device_path(text):
        raise ValueError("project folder must be a local path")
    candidate = Path(os.path.expandvars(text))
    if not candidate.is_absolute():
        raise ValueError("project folder must be an absolute path")
    if candidate.drive.startswith("\\\\") or candidate.drive.startswith("//"):
        raise ValueError("project folder must be a local path")
    if candidate.drive.endswith(":") and candidate.drive[:-1].upper() in _RESERVED_DEVICE_NAMES:
        raise ValueError("project folder must be a local path")
    # Walk the LEXICAL path component by component.  Checking only the deepest
    # existing ancestor misses a junction higher up whose leaf does not exist
    # yet, and checking only the resolved result hides the traversal.
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            raise ValueError("project folder could not be inspected") from error
        if _is_link_or_reparse(current):
            raise ValueError("project folder must not be a link, junction or reparse point")
    # Resolve the deepest existing ancestor so a not-yet-created leaf is fine.
    missing: list[str] = []
    probe = candidate
    while not probe.exists():
        missing.append(probe.name)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    resolved = probe.resolve()
    for name in reversed(missing):
        resolved = resolved / name
    if _is_link_or_reparse(resolved) or _is_link_or_reparse(probe):
        raise ValueError("project folder must not be a link, junction or reparse point")
    return resolved


#: Public alias: the permission layer needs the same "is this the same
#: folder?" test the project store uses, so a recorded folder is compared by
#: its resolved, case-folded form rather than by raw text.
def _same_folder(left: str, right: str) -> bool:
    """True when two spellings name the same folder.

    Compares the resolved, case-folded form so `C:/P`, `c:\\p` and a path
    containing `..` are all recognised as the folder already on record.  An
    8.3 short name resolves to its long form, so this stays sound on Windows
    rather than reporting two spellings of one folder as different.
    """
    def canonical(value: str) -> str:
        try:
            return os.path.normcase(str(Path(value).resolve()))
        except OSError:
            return os.path.normcase(os.path.abspath(value))

    return canonical(left) == canonical(right)


#: The permission layer needs the same "is this the same folder?" test, so a
#: recorded project folder is compared by its resolved form rather than by
#: raw text.
same_folder = _same_folder


def _is_unc_or_device_path(text: str) -> bool:
    """True for UNC shares and ``\\\\?\\`` / ``\\\\.\\`` device namespaces.

    Both slash spellings are accepted, because ``Path`` accepts both and a
    path that is a share to one parser is still a share to the other.
    """
    normalized = text.replace("/", "\\").upper()
    if "GLOBALROOT" in normalized:
        return True
    return normalized.startswith("\\\\?\\") or normalized.startswith("\\\\.\\") or normalized.startswith("\\\\")


def prepare_project_folder(raw: Any) -> tuple[Path, list[Path]]:
    """Create the project folder if needed.

    Returns ``(canonical_path, created)`` where ``created`` lists EVERY
    directory this call made, outermost first.  The caller must roll back that
    exact list on failure.

    Each missing component is created one at a time with ``exist_ok=False``, so
    a ``FileExistsError`` proves the directory was already there.  Creating
    parents in one ``mkdir(parents=True)`` call could not tell us which ones we
    made, and an ``exists()`` check followed by a creation leaves a window in
    which another process creates the directory and we wrongly claim it.
    """
    root = _validate_project_folder(raw)
    if root.exists():
        if not root.is_dir():
            raise ValueError("project folder exists and is not a directory")
        return root, []
    created: list[Path] = []
    try:
        for directory in _missing_components(root):
            try:
                directory.mkdir(exist_ok=False)
            except FileExistsError:
                # Someone else made it; not ours to delete.
                if _is_link_or_reparse(directory):
                    raise ValueError(
                        "project folder must not be a link, junction or reparse point"
                    ) from None
                continue
            created.append(directory)
        for directory in created:
            if _is_link_or_reparse(directory):
                raise ValueError("project folder must not be a link, junction or reparse point")
    except Exception:
        discard_created_folders(created)
        raise
    return root, created


def _missing_components(root: Path) -> list[Path]:
    """The chain of directories from the deepest existing ancestor to ``root``."""
    missing: list[Path] = []
    probe = root
    while not probe.exists():
        missing.append(probe)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return list(reversed(missing))


def discard_created_folders(created: list[Path]) -> None:
    """Remove, in reverse order, exactly the directories AMCA created.

    Only empty directories are removed, and only ones this call made.  A
    pre-existing directory is never touched, and a directory the user has since
    filled is left in place.
    """
    for directory in reversed(created):
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass


def folder_is_writable(root: Path) -> bool:
    """True when the folder can actually be written to, not merely present.

    The probe uses a unique name and removes it in a ``finally``.  A fixed
    filename would overwrite and then delete a user file that happened to
    share it, which is exactly the data AMCA must not touch.
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    handle = None
    probe: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".amca-write-probe-", dir=str(root))
        probe = Path(name)
        handle = os.fdopen(descriptor, "wb")
        handle.write(b"ok")
        handle.flush()
        return True
    except OSError:
        return False
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                pass


class StudioProjectStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS studio_projects (
                    project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    description TEXT NOT NULL, instructions TEXT NOT NULL,
                    template TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    root_path TEXT
                )"""
            )
            # Additive, idempotent migration for databases created before the
            # mandatory project folder existed. Existing rows keep NULL, which
            # the UI renders as "Folder not set"; AMCA never creates a
            # directory on the user's disk without being asked.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(studio_projects)")}
            if "root_path" not in columns:
                connection.execute("ALTER TABLE studio_projects ADD COLUMN root_path TEXT")

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
            "project_id": row["project_id"], "name": row["name"],
            "description": row["description"], "instructions": row["instructions"],
            "template": row["template"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "archived": bool(row["archived"]),
            "root_path": row["root_path"],
            "chat_count": int(row["chat_count"]),
        }

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.*, COUNT(c.conversation_id) AS chat_count
                FROM studio_projects p LEFT JOIN desktop_conversations c
                  ON c.workspace_project_id = p.project_id
                WHERE ? = 1 OR p.archived = 0 GROUP BY p.project_id
                ORDER BY p.updated_at DESC""", (int(include_archived),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT p.*, COUNT(c.conversation_id) AS chat_count
                FROM studio_projects p LEFT JOIN desktop_conversations c
                  ON c.workspace_project_id = p.project_id
                WHERE p.project_id = ? GROUP BY p.project_id""", (project_id,),
            ).fetchone()
        return self._record(row) if row else None

    def save(self, value: Mapping[str, Any]) -> dict[str, Any]:
        project_id = _text(value.get("project_id") or f"studio-{uuid4().hex[:12]}", "project_id", 100, required=True)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
            raise ValueError("project_id may contain only letters, numbers, hyphens, and underscores")
        if project_id.startswith(CHAT_ID_PREFIXES):
            # A standalone chat is addressed as `conv-<conversation_id>`.  If a
            # project were allowed to take that exact id, one identity would
            # resolve to the other's workspace and grants.
            raise ValueError(
                "project_id may not start with a chat prefix "
                "('conv-' or 'desktop-')"
            )
        existing = self.get(project_id)

        # The folder is mandatory for a new project. Existing projects keep
        # whatever they already have and are never given a directory behind
        # the user's back.
        raw_root = value.get("root_path")
        requested = str(raw_root).strip() if raw_root is not None else ""
        if not requested:
            if existing is not None:
                root_path = existing.get("root_path")
            else:
                root_path = default_project_folder(
                    str(value.get("name") or "")
                )
        else:
            root_path = requested
            if existing is not None and existing.get("root_path"):
                # The folder is chosen ONCE, at creation. Accepting a new one
                # here would let a caller rewrite the boundary first and then
                # present it to the permission check as "authoritative" - the
                # check would compare the new value against itself and agree.
                if not _same_folder(requested, str(existing["root_path"])):
                    raise ValueError(
                        "the project folder cannot be changed after the project "
                        "is created; create a new project to use another folder"
                    )

        if root_path is None:
            # A project created before folders existed still has no folder.
            # Editing its name or instructions must keep working; only setting
            # a folder is refused, by prepare_project_folder below.
            return self._write(value, project_id, existing, None)

        root, created = prepare_project_folder(root_path)
        if not folder_is_writable(root):
            discard_created_folders(created)
            raise ValueError("project folder is not writable")
        try:
            return self._write(value, project_id, existing, root)
        except Exception:
            # Roll back exactly the directories this call created, deepest
            # first.  A pre-existing project folder belongs to the user and is
            # never removed.
            discard_created_folders(created)
            raise

    def _write(
        self,
        value: Mapping[str, Any],
        project_id: str,
        existing: Mapping[str, Any] | None,
        root: Path | None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO studio_projects
                (project_id,name,description,instructions,template,created_at,updated_at,archived,root_path)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    _text(value.get("name", existing["name"] if existing else ""), "name", 120, required=True),
                    _text(value.get("description", existing["description"] if existing else ""), "description", 2000),
                    _text(value.get("instructions", existing["instructions"] if existing else ""), "instructions", 16000),
                    _text(value.get("template", existing["template"] if existing else "general"), "template", 64, required=True),
                    existing["created_at"] if existing else now, now,
                    int(bool(value.get("archived", existing["archived"] if existing else False))),
                    str(root) if root is not None else None,
                ),
            )
        return self.get(project_id) or {}

    def delete(self, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_conversations SET workspace_project_id = NULL WHERE workspace_project_id = ?", (project_id,)
            )
            cursor = connection.execute("DELETE FROM studio_projects WHERE project_id = ?", (project_id,))
            if cursor.rowcount == 0:
                raise ValueError("Studio Project not found")

