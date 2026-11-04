"""Permissioned, bounded local-computer tool foundations for AMCA Studio.

This module intentionally implements filesystem work and *proposals* for
command/application control.  It never turns a broad profile into silent
authority: every filesystem operation needs an active path grant, destructive
or modifying operations need an exact approval, and command/application
execution is restricted to reviewed allowlist policies under an approved,
digest-bound record.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .permissions import ApprovalStatus, PermissionAction, PermissionStore
from .security import assert_no_sensitive_keys


MAX_READ_BYTES = 4 * 1024 * 1024
MAX_WRITE_BYTES = 4 * 1024 * 1024

#: 28A NEW-01: document extensions whose payload must be a genuine OOXML
#: package.  Extension-only success is forbidden: a plain-text file named
#: ``.docx``/``.pptx``/``.xlsx`` is rejected at execution time.
_OOXML_DOCUMENT_EXTENSIONS = (".docx", ".pptx", ".xlsx")
_OOXML_CONTENT_TYPES = "[Content_Types].xml"


def _validate_ooxml_payload(target_name: str, data: bytes) -> None:
    if not target_name.endswith(_OOXML_DOCUMENT_EXTENSIONS):
        return
    if len(data) < 4 or data[:4] != b"PK\x03\x04":
        raise ValueError(
            f"{target_name} has a document extension but its content is not a "
            "valid OOXML package (missing ZIP signature). Write real OOXML "
            "content, or save the text with a .txt/.md extension instead."
        )
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            names = set(bundle.namelist())
    except zipfile.BadZipFile as error:
        raise ValueError(
            f"{target_name} has a document extension but its content is not a "
            f"valid OOXML package ({error})."
        ) from error
    if _OOXML_CONTENT_TYPES not in names:
        raise ValueError(
            f"{target_name} is a ZIP archive but not an OOXML package "
            f"(missing {_OOXML_CONTENT_TYPES})."
        )
MAX_LIST_ENTRIES = 5_000
MAX_SEARCH_RESULTS = 2_000
MAX_SEARCH_DEPTH = 12
MAX_SEARCH_SECONDS = 5.0
#: The most file text one search may read, in bytes.  A content search
#: reads files, so it needs a total budget and not only a result count.
MAX_SEARCH_CONTENT_BYTES = 64 * 1024 * 1024
MAX_EVENT_DETAIL_CHARS = 8_000
#: The actors that write a folder-binding row.  Such a row carries the
#: global level that was already in force; it is NOT a legacy per-project
#: level, so the legacy adoption must not read it as one.
_BOUNDING_ROW_ACTORS = frozenset({"project_creation", "project_backfill"})
MAX_TREE_ENTRIES = 5_000
MAX_TREE_BYTES = 256 * 1024 * 1024
MAX_COMMAND_SECONDS = 30.0
MAX_COMMAND_OUTPUT_BYTES = 128 * 1024


class PermissionProfile(str, Enum):
    RESTRICTED = "restricted"
    STANDARD = "standard"
    EXTENDED = "extended"
    FULL_COMPUTER = "full_computer"


class FileCapability(str, Enum):
    LIST = "list"
    STAT = "stat"
    SEARCH = "search"
    READ = "read"
    MKDIR = "mkdir"
    CREATE = "create"
    WRITE = "write"
    APPEND = "append"
    COPY = "copy"
    MOVE = "move"
    TRASH = "trash"
    RESTORE = "restore"
    PROPOSE_COMMAND = "propose_command"
    PROPOSE_APPLICATION = "propose_application"


#: Each level is a strict superset of the one below it so the preset is
#: predictable: Restricted reads, Standard edits, Extended adds the
#: destructive file workflow and read-only command inspection, and Full
#: Computer is the only level that may propose command/application execution.
PROFILE_CAPABILITIES: dict[PermissionProfile, frozenset[FileCapability]] = {
    PermissionProfile.RESTRICTED: frozenset(
        {FileCapability.LIST, FileCapability.STAT, FileCapability.SEARCH, FileCapability.READ}
    ),
    PermissionProfile.STANDARD: frozenset(
        {
            FileCapability.LIST,
            FileCapability.STAT,
            FileCapability.SEARCH,
            FileCapability.READ,
            FileCapability.MKDIR,
            FileCapability.CREATE,
            FileCapability.WRITE,
            FileCapability.APPEND,
            FileCapability.COPY,
        }
    ),
    PermissionProfile.EXTENDED: frozenset(
        {
            FileCapability.LIST,
            FileCapability.STAT,
            FileCapability.SEARCH,
            FileCapability.READ,
            FileCapability.MKDIR,
            FileCapability.CREATE,
            FileCapability.WRITE,
            FileCapability.APPEND,
            FileCapability.COPY,
            FileCapability.MOVE,
            FileCapability.TRASH,
            FileCapability.RESTORE,
            FileCapability.PROPOSE_COMMAND,
        }
    ),
    PermissionProfile.FULL_COMPUTER: frozenset(FileCapability),
}


def _safe_relative_git_path(raw: str, repo_root: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PermissionError("git pathspec must stay inside the granted repository")
    resolved = (repo_root / candidate).resolve()
    # A string-prefix test accepts a sibling whose name extends the repo name
    # ("repo" vs "repo-evil"); _contains compares real path components.
    if not _contains(repo_root.resolve(), resolved):
        raise PermissionError("git pathspec escapes the granted repository")
    return candidate


#: Actions whose target must already exist.  ``read``/``list``/``stat``/
#: ``search`` are deliberately absent: they are existence-shaped, and
#: ``ServiceContext._agent_grant_for`` routes read grants through the same
#: resolver, so a read grant must still resolve for a not-yet-created path.
_EXISTING_TARGET_ACTIONS: frozenset[str] = frozenset(
    {"append", "copy", "move", "trash"}
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _clean_text(value: object, *, name: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{name} is required and must be at most {maximum} characters")
    return text


def _approved_ping_policy(args: Sequence[str]) -> bool:
    """Bounded loopback-only ping: the approved-tier long-running probe.

    Only Windows ping.exe shapes are accepted: [-n count] 127.0.0.1 with
    1 <= count <= 60, so a run is at most ~1 minute and never leaves the
    loopback interface.
    """
    rest = list(args[1:])
    if rest in (["127.0.0.1"], ["localhost"]):
        return True
    count = -1
    if len(rest) == 3 and rest[0] == "-n" and rest[2] == "127.0.0.1":
        try:
            count = int(rest[1])
        except ValueError:
            return False
    elif len(rest) == 3 and rest[0] == "127.0.0.1" and rest[1] == "-n":
        try:
            count = int(rest[2])
        except ValueError:
            return False
    else:
        return False
    return 1 <= count <= 60


#: Applications the governed application executor may ever touch.  Each entry
#: binds a canonical display name to the set of absolute executable paths that
#: may be launched, so a grant to "Notepad" can never be re-targeted at a
#: different binary by editing the request payload.
GOVERNED_APPLICATIONS: dict[str, dict[str, Any]] = {
    "notepad": {
        "display_name": "Notepad",
        "executable": str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "notepad.exe"),
        "window_title_substrings": ("Notepad",),
    },
}
MAX_APPLICATION_TASK_SECONDS = 300.0
_APPLICATION_OPS = frozenset({"launch", "inspect", "terminate"})


def _resolve_governed_application(name: str) -> tuple[str, dict[str, Any]]:
    """Resolve a user/application request to a governed application entry.

    Accepts either the canonical key ("notepad") or the display name
    ("Notepad"); anything else is refused.
    """
    key = name.strip().casefold()
    if key in GOVERNED_APPLICATIONS:
        entry = GOVERNED_APPLICATIONS[key]
        return key, entry
    for candidate_key, entry in GOVERNED_APPLICATIONS.items():
        if entry["display_name"].casefold() == key:
            return candidate_key, entry
    raise PermissionError(
        f"application {name!r} is outside the governed application registry"
    )


def _application_resolution(application: str) -> dict[str, Any]:
    """Resolve the executable path for a governed application request."""
    _key, entry = _resolve_governed_application(application)
    executable = Path(entry["executable"])
    if not executable.is_file():
        raise ValueError(f"governed application executable is missing: {executable}")
    return {"key": _key, "executable": str(executable), "display_name": entry["display_name"]}


#: Governed browser runtimes for loopback-only navigation control.  Candidate
#: executables are checked at request time; the first existing standard
#: install location is pinned into the approval scope so the approved browser
#: can never be re-targeted at a different binary.
GOVERNED_BROWSERS: dict[str, dict[str, Any]] = {
    "edge": {
        "display_name": "Microsoft Edge",
        "candidates": (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ),
    },
    "chrome": {
        "display_name": "Google Chrome",
        "candidates": (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
        ),
    },
    # v1.1.8 Phase 6: the plan requires Chrome *and* Firefox support.  Firefox
    # is governed the same way - a pinned executable from a known install path,
    # chosen by registry key, with the resolved binary written into the approval
    # scope so an approval can never be re-targeted at another program.
    "firefox": {
        "display_name": "Mozilla Firefox",
        "candidates": (
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ),
        # Firefox cannot write a DOM dump to stdout; it is supported for
        # launching a reviewed page, and the DOM-capture path is skipped.
        "supports_dom_dump": False,
    },
}
MAX_BROWSER_DOM_BYTES = 4 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _resolve_governed_browser(name: str) -> tuple[str, dict[str, Any]]:
    key = name.strip().casefold()
    if key not in GOVERNED_BROWSERS:
        raise PermissionError(
            f"browser {name!r} is outside the governed browser registry"
        )
    for candidate in GOVERNED_BROWSERS[key]["candidates"]:
        if Path(candidate).is_file():
            return key, {
                "key": key,
                "executable": candidate,
                "display_name": GOVERNED_BROWSERS[key]["display_name"],
            }
    raise ValueError(f"governed browser executable not found: {key}")


def _normalize_loopback_browser_url(raw: str) -> str:
    """Governed browser navigation policy: loopback http(s) URLs only.

    The public web is deliberately out of scope for the browser runtime —
    web_acquisition owns public fetching with its own SSRF protections — so
    browser control is bounded to 127.0.0.1/localhost test surfaces.
    """
    text = str(raw or "").strip()
    if not text or len(text) > 2048:
        raise PermissionError("browser url missing or exceeds length bound")
    parsed = urllib.parse.urlsplit(text)
    scheme = parsed.scheme.strip().lower()
    if scheme not in ("http", "https"):
        raise PermissionError("browser url scheme must be http or https on loopback")
    if "@" in (parsed.netloc or "") or parsed.username or parsed.password:
        raise PermissionError("embedded credentials in browser url rejected")
    host = (parsed.hostname or "").casefold()
    if host not in _LOOPBACK_HOSTS:
        raise PermissionError(
            "browser navigation is loopback-only (127.0.0.1/localhost); "
            f"host {host!r} is refused"
        )
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise PermissionError("browser url port out of range")
    return text



def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_filesystem_root(path: Path) -> bool:
    return path == Path(path.anchor)


def _is_network_path(path: Path) -> bool:
    value = str(path)
    return value.startswith("\\\\") or value.startswith("//")


def _contains(root: Path, candidate: Path) -> bool:
    root_value = os.path.normcase(str(root))
    candidate_value = os.path.normcase(str(candidate))
    try:
        return os.path.commonpath((root_value, candidate_value)) == root_value
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _assert_no_link_components(path: Path, *, root: Path) -> None:
    """Fail closed on symlink, junction, mount-point, or other reparse traversal."""

    lexical_root = Path(os.path.abspath(str(root)))
    lexical_target = Path(os.path.abspath(str(path)))
    if not _contains(lexical_root, lexical_target):
        raise PermissionError("path is not lexically contained by the granted root")
    try:
        parts = lexical_target.relative_to(lexical_root).parts
    except ValueError as error:
        raise PermissionError("path is not contained by the granted root") from error
    current = lexical_root
    if _is_link_or_reparse(current):
        raise PermissionError("grant root cannot be a symlink or reparse point")
    for part in parts:
        current /= part
        try:
            current.lstat()
        except FileNotFoundError:
            break
        if _is_link_or_reparse(current):
            raise PermissionError(f"symlink/reparse-point traversal is denied: {current}")


def _resolve_workspace_root(raw: str | Path) -> Path:
    """Resolve a recorded workspace folder, refusing link traversal first.

    The recorded Project folder is the boundary every bounded profile is held
    to.  Resolving it with ``Path.resolve`` alone follows a junction that
    replaced the folder after it was recorded, which would silently move the
    boundary to wherever the link points.  The lexical check runs first, so the
    boundary can only ever be the folder that was actually recorded.
    """
    lexical = Path(os.path.abspath(str(Path(raw).expanduser())))
    if str(lexical).startswith("\\\\"):
        # A share or a device path is somebody else's PC, or the OS itself.
        # The lexical walk below would have to touch the network to check it.
        raise PermissionError("network and device workspace paths are not supported")
    if len(lexical.anchor.rstrip("\\/")) != 2 or lexical.anchor[1:2] != ":":
        raise PermissionError("workspace_root must be a local drive path")
    _assert_no_link_components(lexical, root=Path(lexical.anchor))
    root = lexical.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace_root must be an existing directory")
    return root


def _same_filesystem(left: Path, right: Path) -> bool:
    try:
        left_existing = left if left.exists() else left.parent
        right_existing = right if right.exists() else right.parent
        return left_existing.stat().st_dev == right_existing.stat().st_dev
    except OSError:
        return os.name == "nt" and left.drive.casefold() == right.drive.casefold()


#: Grants carrying this reason are the machine-wide Full Access grants. They
#: are created and revoked automatically with the profile, never by hand, and
#: are identified by this constant rather than by a model-chosen string.
_PROVISIONED_REASON = "Automatic Full Computer access grant"

#: Action recorded in ``local_tool_events`` when Full Access grants are made
#: or withdrawn, so a machine-wide grant is never silent.
_PROVISION_ACTIONS = ("full_access_provisioned", "full_access_revoked")

#: ``GetDriveTypeW`` classification.  Only ``DRIVE_FIXED`` is ever granted: a
#: USB stick, an optical drive and a mapped network share all present as a
#: drive letter and would otherwise be swept into whole-PC access.
_DRIVE_FIXED = 3

#: Every level except Full Computer is bounded by the Project folder.  Full
#: Computer is the only PC-wide level, by product decision; Extended adds
#: capabilities (move, recoverable trash, command inspection) but no wider
#: reach, so it belongs here too.
_WORKSPACE_BOUND_PROFILES = frozenset({
    PermissionProfile.RESTRICTED,
    PermissionProfile.STANDARD,
    PermissionProfile.EXTENDED,
})


def _local_drive_roots() -> list[Path]:
    """Every fixed local drive root currently attached, e.g. ``C:\\``.

    Full Computer access is modelled as one grant per drive root rather than
    as a single "unrestricted" root.  A path is then contained in its own
    drive, so every existing containment, reparse and approval check keeps
    working unchanged.

    Only ``DRIVE_FIXED`` drives are returned.  A drive letter alone is not
    enough: removable media, optical drives and mapped network shares all
    present as ``X:\\``, and granting those would widen the access to whatever
    happens to be plugged in rather than to the local PC the user agreed to.
    """
    roots: list[Path] = []
    if os.name != "nt":
        return [Path("/")]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.GetLogicalDrives.restype = ctypes.c_uint32
    bitmask = kernel32.GetLogicalDrives()
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    for index in range(26):
        if not bitmask & (1 << index):
            continue
        letter = f"{chr(ord('A') + index)}:\\"
        root = Path(letter)
        try:
            if int(get_drive_type(letter)) != _DRIVE_FIXED:
                continue
            if not root.is_dir():
                continue
            if _is_network_path(root):
                continue
        except OSError:
            continue
        roots.append(root)
    return roots


@dataclass(frozen=True, slots=True)
class LocalGrant:
    grant_id: str
    project_id: str
    requested_path: str
    canonical_root: str
    capabilities: tuple[str, ...]
    created_at: str
    expires_at: str | None
    created_by: str
    reason: str
    revoked_at: str | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None
    #: True only for grants AMCA created and owns for Full Access.  Ownership
    #: is a stored fact, not a convention: classifying by the ``reason`` text
    #: let any caller mint a grant that impersonated a provisioned one, which
    #: suppressed the real drive grant and was later revoked with it.
    provisioned: bool = False

    @property
    def active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or datetime.now(UTC) < _parse_time(self.expires_at)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        value["active"] = self.active
        return value


@dataclass(frozen=True, slots=True)
class LocalToolEvent:
    event_id: str
    project_id: str
    grant_id: str | None
    action: str
    target: str | None
    outcome: str
    detail: Mapping[str, Any]
    created_at: str
    actor: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["detail"] = dict(self.detail)
        return value


class LocalPermissionStore:
    """Durable profiles, explicit path grants, and local-tool audit events."""

    #: Singleton key of the global PC access level row.
    GLOBAL_POLICY_ID = "global"

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database, timeout=5, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_permission_profiles (
                    project_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_path_grants (
                    grant_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    requested_path TEXT NOT NULL,
                    canonical_root TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    created_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    revoked_at TEXT,
                    revoked_by TEXT,
                    revoke_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_local_grants_project
                    ON local_path_grants(project_id, created_at);
                CREATE TABLE IF NOT EXISTS local_tool_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    grant_id TEXT,
                    action TEXT NOT NULL,
                    target TEXT,
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_tool_events_project
                    ON local_tool_events(project_id, created_at);
                CREATE TABLE IF NOT EXISTS local_trash_records (
                    trash_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    original_relative_path TEXT NOT NULL,
                    trash_path TEXT NOT NULL,
                    is_directory INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    restored_at TEXT,
                    restored_by TEXT
                );
                -- The PC access level is one global policy: every chat,
                -- project and model reads this single row.  Path grants stay
                -- per-project because folder access is inherently
                -- path-specific.
                CREATE TABLE IF NOT EXISTS local_permission_policy (
                    policy_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                """
            )
            self._ensure_provisioned_column()
        self._ensure_full_access_scope_table()
        self._adopt_legacy_global_profile()
        self._purge_chat_namespace_workspace_roots()

    def _purge_chat_namespace_workspace_roots(self) -> None:
        """Drop workspace folders recorded against a chat-prefixed identity.

        A chat is never legitimately bounded by a project folder: a chat that
        belongs to a Project is addressed by the Project's own id.  An earlier
        build could copy a colliding project row's folder onto a ``conv-`` row,
        and the UI then posted that value straight back, so the row could keep
        a boundary alive that nothing ever legitimately granted.  Clearing it
        and revoking the affected grants restores the fail-closed state: a
        standalone chat with no stated folder gets an actionable refusal from
        the bounded levels, and Full Access works through its own drive grants.

        Idempotent: once the rows are cleared there is nothing left to do.
        """
        clauses = " OR ".join(
            f"project_id LIKE ?" for _ in self._CHAT_PREFIXES
        )
        parameters = [f"{prefix}%" for prefix in self._CHAT_PREFIXES]
        contaminated = [
            str(row["project_id"])
            for row in self._connection.execute(
                f"SELECT project_id FROM local_permission_profiles "
                f"WHERE workspace_root IS NOT NULL AND ({clauses})",
                parameters,
            ).fetchall()
        ]
        if not contaminated:
            return
        with self._lock, self._connection:
            # A row is only written when a folder is stated, so removing it is
            # how "this identity has no folder" is represented.
            self._connection.execute(
                f"DELETE FROM local_permission_profiles WHERE ({clauses})",
                parameters,
            )
        for project_id in contaminated:
            self.revoke_identity(
                project_id,
                reason="chat namespace no longer carries a project workspace folder",
            )

    def _ensure_full_access_scope_table(self) -> None:
        """Create the table that freezes the Full Access drive set.

        Full Access is a promise about the machine, not about the chat that
        happened to switch it on.  A Project created an hour later, or a chat
        started tomorrow, must get the same coverage - but a drive plugged in
        after the user agreed must not be covered until they say so.  The
        snapshot is what separates those two: it records the drives that were
        attached at the moment the level was turned on (or refreshed), and
        every later provisioning uses exactly that list.
        """
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_full_access_scope (
                    drive_root TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL
                )
                """
            )

    def _full_access_scope_drives(self) -> list[Path]:
        """The drives the current Full Access episode covers, as frozen."""
        return [
            Path(str(row["drive_root"]))
            for row in self._connection.execute(
                "SELECT drive_root FROM local_full_access_scope ORDER BY drive_root"
            ).fetchall()
        ]

    def _full_access_scope_drives_text(self) -> list[str]:
        """The frozen scope as plain strings, for assertions and reports."""
        return [str(drive) for drive in self._full_access_scope_drives()]

    def _record_full_access_scope(self, drives: list[Path]) -> None:
        """Freeze the drive set for this Full Access episode.

        Replacing the contents is what makes "Refresh drives" work: a drive
        attached since the level was turned on joins the scope only here, and
        only when the user asked for the refresh.
        """
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM local_full_access_scope")
            self._connection.executemany(
                "INSERT INTO local_full_access_scope (drive_root, recorded_at) VALUES (?, ?)",
                [(str(drive), _now()) for drive in drives],
            )

    def clear_full_access_scope(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM local_full_access_scope")

    def _ensure_provisioned_column(self) -> None:
        """Add ``local_path_grants.provisioned`` to an existing database.

        Additive and idempotent, like every other migration here: a database
        created before Full Access existed simply gains a column defaulting to
        0, so no pre-existing grant is ever mistaken for an AMCA-owned one.
        """
        columns = {
            (row["name"] if isinstance(row, sqlite3.Row) else row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(local_path_grants)"
            ).fetchall()
        }
        if "provisioned" not in columns:
            with self._lock, self._connection:
                self._connection.execute(
                    "ALTER TABLE local_path_grants "
                    "ADD COLUMN provisioned INTEGER NOT NULL DEFAULT 0"
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "LocalPermissionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    #: Prefixed id forms the desktop sends, and the row each one must resolve to.
    _PREFIXED_IDS: tuple[tuple[str, str, str], ...] = (
        ("conv-", "desktop_conversations", "conversation_id"),
        ("desktop-", "desktop_conversations", "conversation_id"),
        ("studio-", "studio_projects", "project_id"),
    )

    #: Prefixes that name the chat namespace exclusively.  A ``conv-`` id can
    #: only ever be a conversation, never a canonical project that happens to
    #: spell itself the same way.
    _CHAT_PREFIXES: tuple[str, ...] = ("conv-", "desktop-")

    def _row_exists(self, table: str, column: str, value: str) -> bool:
        exists = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return False
        return self._connection.execute(
            f"SELECT 1 FROM {table} WHERE {column}=?", (value,)
        ).fetchone() is not None

    def _project_exists(self, project_id: str) -> bool:
        """True when this id names a real chat, project or conversation.

        The desktop addresses a chat as ``conv-<conversation_id>``, so a
        prefixed id is resolved to its underlying row.  Either way a real row
        is required - accepting the prefix alone made this check a no-op for
        exactly the ids the UI sends, and because the PC access level is
        global, any caller could invent an id and raise the level for every
        chat on the machine.

        The chat prefixes are an exclusive namespace.  A legacy or canonical
        project row whose id literally reads ``conv-<id>`` used to win the
        exact-match lookup, which let one project and one standalone chat
        share a single grant identity - revoking the chat then revoked the
        project too.  A chat prefix now resolves only to its conversation, and
        a project row carrying a chat prefix is never addressable as a project.
        """
        for prefix in self._CHAT_PREFIXES:
            if project_id.startswith(prefix):
                remainder = project_id[len(prefix):]
                if not remainder:
                    return False
                return self._row_exists(
                    "desktop_conversations", "conversation_id", remainder
                ) or self._row_exists(
                    "desktop_conversations", "conversation_id", project_id
                )
        for table in ("projects", "studio_projects"):
            if self._row_exists(table, "project_id", project_id):
                return True
        for prefix, table, column in self._PREFIXED_IDS:
            if prefix in self._CHAT_PREFIXES:
                continue
            if project_id.startswith(prefix):
                remainder = project_id[len(prefix):]
                if remainder and self._row_exists(table, column, remainder):
                    return True
        # A bare conversation id is not how the desktop addresses a chat, but
        # older callers pass one; it still has to name a real conversation.
        return self._row_exists(
            "desktop_conversations", "conversation_id", project_id
        )

    def _known_project_ids(self) -> set[str]:
        """Every project/chat id form the desktop actually addresses.

        A chat is addressed as ``conv-<conversation_id>``, never by its bare
        conversation id, so counting the bare form would invent a project that
        never had a level and force a Restricted adoption on every install.

        A project row whose own id starts with a chat prefix is skipped: that
        spelling belongs to the conversation namespace, and provisioning a
        whole-PC grant to it would resurrect the shared-identity collision.
        """
        known: set[str] = set()
        for table, column, prefixes in (
            ("projects", "project_id", ("",)),
            ("studio_projects", "project_id", ("", "studio-")),
            ("desktop_conversations", "conversation_id", ("conv-",)),
        ):
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                continue
            for row in self._connection.execute(
                f"SELECT {column} AS id FROM {table}"
            ).fetchall():
                value = str(row["id"])
                if table != "desktop_conversations" and value.startswith(
                    self._CHAT_PREFIXES
                ):
                    continue
                for prefix in prefixes:
                    known.add(f"{prefix}{value}")
        return known

    def global_profile(self) -> dict[str, Any]:
        """Return the single global PC access level shared by every chat."""
        row = self._connection.execute(
            "SELECT * FROM local_permission_policy WHERE policy_id=?",
            (self.GLOBAL_POLICY_ID,),
        ).fetchone()
        if row is None:
            return {
                "profile": PermissionProfile.RESTRICTED.value,
                "capabilities": [
                    item.value
                    for item in PROFILE_CAPABILITIES[PermissionProfile.RESTRICTED]
                ],
                "configured": False,
            }
        profile = PermissionProfile(row["profile"])
        return {
            "profile": profile.value,
            "capabilities": [item.value for item in PROFILE_CAPABILITIES[profile]],
            "updated_at": str(row["updated_at"]),
            "updated_by": str(row["updated_by"]),
            "configured": True,
        }

    def set_global_profile(
        self,
        *,
        profile: PermissionProfile | str,
        updated_by: str = "desktop_user",
    ) -> dict[str, Any]:
        """Set the global PC access level.  No workspace folder is involved."""
        resolved = PermissionProfile(profile)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO local_permission_policy
                   (policy_id, profile, updated_at, updated_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(policy_id) DO UPDATE SET profile=excluded.profile,
                   updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (self.GLOBAL_POLICY_ID, resolved.value, _now(), updated_by[:120]),
            )
        return self.global_profile()

    def _adopt_legacy_global_profile(self) -> None:
        """Adopt a legacy per-project level as the global policy, exactly once.

        Installs that predate the global policy stored a level per project.
        Promoting the *newest* of those would silently raise every other
        project to whatever one project happened to have - and because the
        workspace-containment rule only applies below Extended, that would
        also revive grants the lower projects had made inert.  The most
        restrictive legacy level is adopted instead, so no project can gain
        capability it did not already have; the user raises the level
        explicitly, once, knowing it is global.
        """
        with self._lock, self._connection:
            if self._connection.execute(
                "SELECT 1 FROM local_permission_policy WHERE policy_id=?",
                (self.GLOBAL_POLICY_ID,),
            ).fetchone() is not None:
                return
            rows = self._connection.execute(
                """SELECT project_id, profile, updated_at, updated_by
                   FROM local_permission_profiles"""
            ).fetchall()
            # A row this product wrote is not a LEGACY per-project level: it
            # is a folder binding, and it carries the global level that was
            # already in force.  Adopting it would mark the global policy
            # "configured" on the strength of a row the operator never
            # wrote, and would make a grant possible where the operator had
            # never chosen a level.
            rows = [
                row for row in rows
                if str(row["updated_by"] or "") not in _BOUNDING_ROW_ACTORS
            ]
            if not rows:
                return
            rank = {
                PermissionProfile.RESTRICTED: 0,
                PermissionProfile.STANDARD: 1,
                PermissionProfile.EXTENDED: 2,
                PermissionProfile.FULL_COMPUTER: 3,
            }
            adopted = min(rows, key=lambda row: rank[PermissionProfile(str(row["profile"]))])
            adopted_level = str(adopted["profile"])
            recorded = {str(row["project_id"]) for row in rows}
            unrecorded = self._known_project_ids() - recorded
            if unrecorded:
                # A project with no legacy row was effectively Restricted
                # (configured=False), so adopting anything higher would hand it
                # capability it never had.  The user raises the level once,
                # explicitly, knowing it is global.
                adopted_level = PermissionProfile.RESTRICTED.value
            self._connection.execute(
                """INSERT INTO local_permission_policy
                   (policy_id, profile, updated_at, updated_by)
                   VALUES (?, ?, ?, ?)""",
                (
                    self.GLOBAL_POLICY_ID,
                    adopted_level,
                    _now(),
                    "legacy_adoption",
                ),
            )
            self._connection.execute(
                """INSERT INTO local_tool_events
                   (event_id, project_id, grant_id, action, target, outcome,
                    detail_json, created_at, actor)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                (
                    f"event.{uuid4().hex}",
                    str(adopted["project_id"]),
                    "profile_legacy_adoption",
                    "local_permission_policy",
                    "adopted",
                    json.dumps(
                        {
                            "adopted_profile": adopted_level,
                            "most_restrictive_recorded_level": str(adopted["profile"]),
                            "source_project_id": str(adopted["project_id"]),
                            "legacy_rows": len(rows),
                            "unrecorded_projects": len(unrecorded),
                            "rule": "most_restrictive_recorded_level_within_recorded_projects",
                        }
                    ),
                    _now(),
                    "system",
                ),
            )

    def profile(self, project_id: str) -> dict[str, Any]:
        """Return the effective policy for one chat.

        The PC access *level* is global, so every chat, project and model
        reports the same level.  Only ``workspace_root`` stays per-project,
        because folder access is inherently path-specific.
        """
        policy = self.global_profile()
        level = PermissionProfile(policy["profile"])
        row = self._connection.execute(
            "SELECT * FROM local_permission_profiles WHERE project_id=?", (project_id,)
        ).fetchone()
        result: dict[str, Any] = {
            "project_id": project_id,
            "profile": level.value,
            "workspace_root": str(row["workspace_root"]) if row is not None else None,
            "capabilities": [item.value for item in PROFILE_CAPABILITIES[level]],
            "configured": bool(policy["configured"]),
        }
        if policy.get("updated_at"):
            result["updated_at"] = policy["updated_at"]
            result["updated_by"] = policy["updated_by"]
        return result

    def set_profile(
        self,
        *,
        project_id: str,
        profile: PermissionProfile | str,
        workspace_root: str | Path | None = None,
        updated_by: str = "desktop_user",
    ) -> dict[str, Any]:
        """Set the global PC access level and this project's workspace folder.

        Changing the level never requires a folder: the level is global and a
        folder is only needed to create path-specific grants.

        A chat-prefixed identity never carries a folder.  A chat that belongs
        to a Project is addressed by that Project's id, so a ``conv-`` id is a
        standalone chat, which has no folder of its own - Full Access reaches
        the PC through its drive grants and the folder-bound levels refuse it
        with an actionable message.  Storing one anyway is how an earlier build
        let a chat inherit a colliding project row's boundary.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        resolved_profile = PermissionProfile(profile)
        raw_root = "" if workspace_root is None else str(workspace_root).strip()
        if raw_root and project_id.startswith(self._CHAT_PREFIXES):
            raise ValueError(
                "a standalone chat has no Project folder; the folder-bound "
                "levels need a Project, and Full Access needs no folder"
            )
        if not raw_root or raw_root.lower() == "none":
            existing = self._connection.execute(
                "SELECT workspace_root FROM local_permission_profiles WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if existing is not None and existing["workspace_root"]:
                raw_root = str(existing["workspace_root"])
        root: Path | None = None
        if raw_root:
            root = _resolve_workspace_root(raw_root)
        # A level change is global, so the caller must still name a real chat
        # or project: an unknown id must never be able to raise the level.
        if not self._project_exists(project_id):
            raise ValueError("project does not exist")
        with self._lock, self._connection:
            previous = self.global_profile()
            self._connection.execute(
                """INSERT INTO local_permission_policy
                   (policy_id, profile, updated_at, updated_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(policy_id) DO UPDATE SET profile=excluded.profile,
                   updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (self.GLOBAL_POLICY_ID, resolved_profile.value, _now(), updated_by[:120]),
            )
            if root is not None:
                self._connection.execute(
                    """INSERT INTO local_permission_profiles
                       (project_id, profile, workspace_root, updated_at, updated_by)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(project_id) DO UPDATE SET profile=excluded.profile,
                       workspace_root=excluded.workspace_root, updated_at=excluded.updated_at,
                       updated_by=excluded.updated_by""",
                    (project_id, resolved_profile.value, str(root), _now(), updated_by[:120]),
                )
            if previous.get("profile") != resolved_profile.value:
                # The most consequential state change in this module - PC access
                # for every chat on the machine - leaves a per-change trail.
                self._connection.execute(
                    """INSERT INTO local_tool_events
                       (event_id, project_id, grant_id, action, target, outcome,
                        detail_json, created_at, actor)
                       VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"event.{uuid4().hex}",
                        project_id,
                        "permission_level_changed",
                        "local_permission_policy",
                        "changed",
                        json.dumps(
                            {
                                "from": str(previous.get("profile")),
                                "to": resolved_profile.value,
                                "requested_project_id": project_id,
                                "workspace_root_set": root is not None,
                            }
                        ),
                        _now(),
                        updated_by[:120],
                    ),
                )
        return self.profile(project_id)

    def bind_workspace_root(
        self,
        *,
        project_id: str,
        workspace_root: str | Path,
        updated_by: str = "project_creation",
    ) -> dict[str, Any]:
        """Point one project's local tools at that project's own folder.

        This binds the FOLDER only.  The PC access level is global and is
        never touched here: the operator's chosen level, and the grants that
        go with it, stay exactly as they are.  A project whose folder is
        bound but which holds no grant still refuses a write, with its own
        actionable message.

        A relative target such as "calculator.html" is resolved against
        this folder, so "in this Project's folder" means what it says
        instead of whichever drive happened to be granted first.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        if project_id.startswith(self._CHAT_PREFIXES):
            # A standalone chat is in the conversation namespace and has no
            # Project folder of its own.  Binding one would put a folder in
            # the chat namespace, which is exactly the contamination the
            # purge routine at construction exists to clean up.
            raise ValueError(
                "a standalone chat has no Project folder; the folder-bound "
                "levels need a Project, and Full Access needs no folder"
            )
        if not self._project_exists(project_id):
            raise PermissionError("the requesting project or chat no longer exists")
        root = _resolve_workspace_root(workspace_root)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT workspace_root FROM local_permission_profiles WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if existing is not None and existing["workspace_root"]:
                # The folder is chosen once, at creation.  A folder that is
                # already bound is not replaced.
                return self.profile(project_id)
            self._connection.execute(
                """INSERT INTO local_permission_profiles
                   (project_id, profile, workspace_root, updated_at, updated_by)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                   workspace_root=excluded.workspace_root,
                   updated_at=excluded.updated_at,
                   updated_by=excluded.updated_by""",
                (
                    project_id,
                    str(self.global_profile().get("profile") or "restricted"),
                    str(root),
                    _now(),
                    updated_by[:120],
                ),
            )
        return self.profile(project_id)

    def _provisioned_grants(self, project_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        """The machine-wide Full Access grants for this project, if any."""
        query = (
            "SELECT * FROM local_path_grants WHERE project_id=? AND provisioned=1"
        )
        if active_only:
            query += " AND revoked_at IS NULL"
        return [self._grant_from_row(row).to_dict() for row in self._connection.execute(
            query, (project_id,)
        ).fetchall()]

    def full_access_scope(self, project_id: str) -> dict[str, Any]:
        """Report the machine-wide access currently in force for a project.

        Read-only: it never creates or withdraws anything. The UI uses it to
        show which drives Full Access currently covers, so that a drive
        attached after the grant was made is visibly not yet covered.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        policy = self.profile(project_id)
        profile = PermissionProfile(str(policy["profile"]))
        drives = _local_drive_roots()
        granted = {
            os.path.normcase(str(row["canonical_root"]))
            for row in self._connection.execute(
                "SELECT canonical_root FROM local_path_grants "
                "WHERE project_id=? AND provisioned=1 AND revoked_at IS NULL",
                (project_id,),
            ).fetchall()
        }
        covered = [drive for drive in drives if os.path.normcase(str(drive)) in granted]
        return {
            "project_id": project_id,
            "profile": profile.value,
            "active": profile is PermissionProfile.FULL_COMPUTER and bool(covered),
            "drives_detected": [str(drive) for drive in drives],
            "drives_granted": [str(drive) for drive in covered],
            "drives_missing": [str(drive) for drive in drives if drive not in covered],
        }

    def provision_full_access(
        self, project_id: str, *, drives: list[Path] | None = None
    ) -> dict[str, Any]:
        """Grant whole-PC access for this project, one grant per fixed drive.

        Only meaningful at the Full Computer level, and it never grants more
        than that level allows: every grant still carries the full capability
        set, still requires an active grant, and every destructive operation
        still needs its exact-scope approval. The approval model is untouched.

        ``drives`` defaults to the frozen Full Access scope, never to the live
        drive list: a drive attached after the user agreed stays uncovered
        until they press "Refresh drives".  The caller that performs that
        refresh passes the freshly enumerated drives explicitly.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        policy = self.profile(project_id)
        if PermissionProfile(str(policy["profile"])) is not PermissionProfile.FULL_COMPUTER:
            raise PermissionError(
                "whole-PC access requires the Full Computer level"
            )
        if drives is None:
            drives = self._full_access_scope_drives()
        if not drives:
            # Nothing was ever frozen for this Full Access episode, which is
            # what a database written before the scope existed looks like.
            # Freeze what is attached now: the same moment, and the same
            # machine, that switching the level on would have frozen.
            drives = _local_drive_roots()
            if not drives:
                raise PermissionError("no local drive could be enumerated for whole-PC access")
            self._record_full_access_scope(drives)
        already = {
            os.path.normcase(str(row["canonical_root"]))
            for row in self._connection.execute(
                "SELECT canonical_root FROM local_path_grants "
                "WHERE project_id=? AND provisioned=1 AND revoked_at IS NULL",
                (project_id,),
            ).fetchall()
        }
        created: list[str] = []
        covered = set(already)
        for drive in drives:
            if os.path.normcase(str(drive)) in already:
                continue
            try:
                grant = self._create_whole_pc_grant(project_id=project_id, drive=drive)
            except (OSError, ValueError) as error:
                # A drive can vanish between enumeration and grant - an
                # unplugged volume, a letter that is no longer fixed.  That
                # must cost exactly that drive, never the ones after it.
                self.record_event(
                    project_id=project_id,
                    grant_id=None,
                    action="full_access_not_provisioned",
                    target=str(drive),
                    outcome="denied",
                    actor="amca_full_access",
                    detail={"reason": type(error).__name__},
                )
                continue
            created.append(grant.grant_id)
            covered.add(os.path.normcase(str(drive)))
            self.record_event(
                project_id=project_id,
                grant_id=grant.grant_id,
                action="full_access_provisioned",
                target=str(drive),
                outcome="allowed",
                actor="amca_full_access",
                detail={"reason": _PROVISIONED_REASON, "profile": "full_computer"},
            )
        if drives and not covered:
            raise PermissionError("no local drive could be enumerated for whole-PC access")
        return self.full_access_scope(project_id)

    def revoke_full_access(self, project_id: str) -> dict[str, Any]:
        """Withdraw this project's machine-wide grants, keeping any other grant."""
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        revoked: list[str] = []
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT grant_id, canonical_root FROM local_path_grants "
                "WHERE project_id=? AND provisioned=1 AND revoked_at IS NULL",
                (project_id,),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    """UPDATE local_path_grants
                       SET revoked_at=?, revoked_by=?, revoke_reason=?
                       WHERE grant_id=?""",
                    (_now(), "amca_full_access", "Full Computer access turned off", row["grant_id"]),
                )
                revoked.append(str(row["grant_id"]))
        for grant_id in revoked:
            self.record_event(
                project_id=project_id,
                grant_id=grant_id,
                action="full_access_revoked",
                target="*",
                outcome="revoked",
                actor="amca_full_access",
                detail={"reason": "Full Computer access turned off"},
            )
        return self.full_access_scope(project_id)

    def revoke_all_full_access(self) -> int:
        """Withdraw every provisioned Full Access grant, for every identity.

        The access level is one global policy, so leaving Full Access has to
        withdraw machine-wide grants machine-wide.  Revoking only the identity
        that happened to make the request left drive-wide grants sitting in
        the database for every other chat, where a later return to Full Access
        would silently reactivate the old, wider coverage.
        """
        revoked: list[tuple[str, str]] = []
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT grant_id, project_id FROM local_path_grants "
                "WHERE provisioned=1 AND revoked_at IS NULL"
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    """UPDATE local_path_grants
                       SET revoked_at=?, revoked_by=?, revoke_reason=?
                       WHERE grant_id=?""",
                    (_now(), "amca_full_access", "Full Computer access turned off", row["grant_id"]),
                )
                revoked.append((str(row["grant_id"]), str(row["project_id"])))
        for grant_id, project_id in revoked:
            self.record_event(
                project_id=project_id,
                grant_id=grant_id,
                action="full_access_revoked",
                target="*",
                outcome="revoked",
                actor="amca_full_access",
                detail={"reason": "Full Computer access turned off"},
            )
        return len(revoked)

    def revoke_identity(
        self, project_id: str, *, reason: str = "identity no longer exists"
    ) -> int:
        """Revoke every grant held by an identity that no longer exists.

        Called when a conversation or project is deleted, and when a stored
        workspace folder is withdrawn.  Without it a stale grant id keeps
        working for whoever still holds the string, because the executor only
        ever compared the grant's stored project id.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        revoked: list[str] = []
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT grant_id FROM local_path_grants "
                "WHERE project_id=? AND revoked_at IS NULL",
                (project_id,),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    """UPDATE local_path_grants
                       SET revoked_at=?, revoked_by=?, revoke_reason=?
                       WHERE grant_id=?""",
                    (_now(), "amca_identity", reason, row["grant_id"]),
                )
                revoked.append(str(row["grant_id"]))
        for grant_id in revoked:
            self.record_event(
                project_id=project_id,
                grant_id=grant_id,
                action="identity_revoked",
                target="*",
                outcome="revoked",
                actor="amca_identity",
                detail={"reason": "identity no longer exists"},
            )
        return len(revoked)

    def sync_full_access(self, project_id: str) -> dict[str, Any]:
        """Reconcile one identity when a run is admitted.

        Run admission never re-reads the live drive list.  It provisions only
        the drives frozen into the current Full Access episode, which is what
        lets a Project or chat created *after* the level was switched on get
        exactly the coverage the user agreed to - and nothing wider.  A drive
        attached since then stays uncovered until the user presses "Refresh
        drives", which is the only thing that re-freezes the scope.

        The narrowing half still always runs: if the global level has left
        Full Access, this identity's provisioned grants are withdrawn.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        if PermissionProfile(str(self.profile(project_id)["profile"])) is PermissionProfile.FULL_COMPUTER:
            try:
                self.provision_full_access(project_id)
            except (PermissionError, OSError, ValueError):
                # An identity that cannot be provisioned stays fail-closed
                # rather than blocking the run: no grant, no access.
                pass
            return self.full_access_scope(project_id)
        return self.revoke_full_access(project_id)

    def apply_profile_transition(
        self, *, project_id: str, profile: PermissionProfile | str
    ) -> dict[str, Any]:
        """Reconcile machine-wide access after the GLOBAL level changed.

        Turning Full Access on freezes the drives attached right now, and
        provisions that scope for every identity the desktop can address, so
        the global promise ("every chat") is true immediately.  Turning it off
        withdraws every provisioned grant on the machine, not just the
        caller's, and discards the frozen scope.
        """
        project_id = _clean_text(project_id, name="project_id", maximum=128)
        if PermissionProfile(profile) is PermissionProfile.FULL_COMPUTER:
            self.refresh_full_access_scope()
            return self.full_access_scope(project_id)
        self.revoke_all_full_access()
        self.clear_full_access_scope()
        return self.full_access_scope(project_id)

    def refresh_full_access_scope(self) -> dict[str, Any]:
        """Re-freeze the Full Access drive set and re-provision every identity.

        This is the one and only way a newly attached drive becomes covered,
        and it is always an explicit user action.
        """
        drives = _local_drive_roots()
        if not drives:
            raise PermissionError("no local drive could be enumerated for whole-PC access")
        self._record_full_access_scope(drives)
        for identity in sorted(self._known_project_ids()):
            try:
                self.provision_full_access(identity, drives=drives)
            except (PermissionError, OSError, ValueError):
                # An identity that cannot be provisioned (no fixed drive,
                # malformed id) must not block the refresh; it simply has no
                # whole-PC grant and stays fail-closed.
                continue
        return {"drives": [str(drive) for drive in drives]}

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> LocalGrant:
        capabilities = json.loads(row["capabilities_json"])
        if not isinstance(capabilities, list):
            raise ValueError("persisted grant capabilities are invalid")
        return LocalGrant(
            grant_id=str(row["grant_id"]),
            project_id=str(row["project_id"]),
            requested_path=str(row["requested_path"]),
            canonical_root=str(row["canonical_root"]),
            capabilities=tuple(str(item) for item in capabilities),
            created_at=str(row["created_at"]),
            expires_at=row["expires_at"],
            created_by=str(row["created_by"]),
            reason=str(row["reason"]),
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
            revoke_reason=row["revoke_reason"],
            provisioned=bool(row["provisioned"]) if "provisioned" in row.keys() else False,
        )

    def create_grant(
        self,
        *,
        project_id: str,
        root_path: str | Path,
        capabilities: Sequence[FileCapability | str],
        reason: str,
        ttl_seconds: float | None = None,
        created_by: str = "desktop_user",
        allow_system_root: bool = False,
    ) -> LocalGrant:
        """Create a grant the user asked for.

        This is the public entry point and it can NEVER mark a grant as
        AMCA-owned.  Ownership used to be a keyword here, which meant any
        caller could mint a grant that impersonated a provisioned one, suppress
        the real whole-PC grant, and then be revoked with it.  Whole-PC
        provisioning goes through :meth:`_create_whole_pc_grant` instead.
        """
        return self._build_grant(
            project_id=project_id,
            root_path=root_path,
            capabilities=capabilities,
            reason=reason,
            ttl_seconds=ttl_seconds,
            created_by=created_by,
            allow_system_root=allow_system_root,
            provisioned=False,
        )

    def _create_whole_pc_grant(
        self, *, project_id: str, drive: Path
    ) -> LocalGrant:
        """Create the one machine-wide grant AMCA owns for a fixed drive.

        Private on purpose. ``provisioned`` is set here and nowhere else, so
        the ownership bit that governs automatic revocation cannot be supplied
        from outside this class.
        """
        return self._build_grant(
            project_id=project_id,
            root_path=drive,
            capabilities=PROFILE_CAPABILITIES[PermissionProfile.FULL_COMPUTER],
            reason=_PROVISIONED_REASON,
            ttl_seconds=None,
            created_by="amca_full_access",
            allow_system_root=True,
            provisioned=True,
        )

    def _build_grant(
        self,
        *,
        project_id: str,
        root_path: str | Path,
        capabilities: Sequence[FileCapability | str],
        reason: str,
        ttl_seconds: float | None,
        created_by: str,
        allow_system_root: bool,
        provisioned: bool,
    ) -> LocalGrant:
        policy = self.profile(project_id)
        if not policy["configured"]:
            raise ValueError("configure a permission profile before creating grants")
        if not self._project_exists(project_id):
            raise PermissionError("the requesting project or chat no longer exists")
        profile = PermissionProfile(str(policy["profile"]))
        raw_path = str(root_path)
        normalized_path = raw_path.replace("/", "\\").casefold()
        if "\x00" in raw_path:
            raise ValueError("grant path contains a NUL byte")
        if normalized_path.startswith("\\\\.\\") or normalized_path.startswith("\\\\?\\globalroot"):
            raise PermissionError("device and GLOBALROOT paths are not grantable")
        # Reject every UNC form BEFORE touching the filesystem. `Path` turns
        # `//server/share` into a real UNC path, and an lstat on it reaches
        # across the network - so a path that must never be granted would still
        # cause a network round trip on its way to being refused.
        if raw_path.replace("/", "\\").startswith("\\\\"):
            raise PermissionError("network-root grants are not supported in this product")
        requested = Path(raw_path).expanduser()
        if not requested.is_absolute():
            raise ValueError("grant root must be an absolute path")
        if _is_link_or_reparse(requested):
            raise PermissionError("grant root cannot be a symlink or reparse point")
        # Check every LEXICAL component of the root before resolving it.
        # Resolving first would rewrite a junction to its target and hide the
        # very traversal the reparse rule exists to refuse.
        _assert_no_link_components(requested, root=Path(requested.anchor))
        root = requested.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("grant root must be an existing directory")
        # The project folder bounds every level except Full Computer.  Only
        # Full Computer is PC-wide by product decision, so Extended - which
        # adds move/trash/command capabilities but no wider reach - stays
        # inside the project folder just like Restricted and Standard.
        if profile in _WORKSPACE_BOUND_PROFILES:
            workspace_value = policy.get("workspace_root")
            if not workspace_value:
                raise PermissionError(
                    "project workspace folder is not configured; set one before "
                    "creating grants under this profile"
                )
            workspace = _resolve_workspace_root(str(workspace_value))
            if not _contains(workspace, root):
                raise PermissionError("this profile may grant only paths inside the project workspace")
        if _is_filesystem_root(root) and not (
            profile is PermissionProfile.FULL_COMPUTER and allow_system_root
        ):
            raise PermissionError("filesystem-root grants require Full Computer plus explicit root confirmation")
        if _is_network_path(root):
            # This product grants the local PC. A share is somebody else's PC,
            # reached over somebody else's credentials, so it is never
            # grantable here - not even at Full Computer.
            raise PermissionError("network-root grants are not supported in this product")
        requested_capabilities = tuple(
            dict.fromkeys(FileCapability(item).value for item in capabilities)
        )
        if not requested_capabilities:
            raise ValueError("at least one capability is required")
        allowed = PROFILE_CAPABILITIES[profile]
        if any(FileCapability(item) not in allowed for item in requested_capabilities):
            raise PermissionError("requested grant capability exceeds the active profile")
        expires_at = None
        if ttl_seconds is not None:
            if not math.isfinite(float(ttl_seconds)) or not 60 <= ttl_seconds <= 31_536_000:
                raise ValueError("grant ttl must be between 60 seconds and 365 days")
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=float(ttl_seconds))
            ).isoformat().replace("+00:00", "Z")
        grant = LocalGrant(
            grant_id=f"grant.{uuid4().hex}",
            project_id=project_id,
            requested_path=str(requested),
            canonical_root=str(root),
            capabilities=requested_capabilities,
            created_at=_now(),
            expires_at=expires_at,
            created_by=created_by[:120],
            reason=(
                _PROVISIONED_REASON
                if provisioned
                else _clean_text(reason, name="reason", maximum=2_000)
            ),
            provisioned=bool(provisioned),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO local_path_grants
                   (grant_id, project_id, requested_path, canonical_root,
                    capabilities_json, created_at, expires_at, created_by, reason,
                    provisioned)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    grant.grant_id,
                    grant.project_id,
                    grant.requested_path,
                    grant.canonical_root,
                    json.dumps(list(grant.capabilities), separators=(",", ":")),
                    grant.created_at,
                    grant.expires_at,
                    grant.created_by,
                    grant.reason,
                    1 if grant.provisioned else 0,
                ),
            )
        self.record_event(
            project_id=project_id,
            grant_id=grant.grant_id,
            action="grant_created",
            target=str(root),
            outcome="allowed",
            detail={"capabilities": list(grant.capabilities), "expires_at": expires_at},
            actor=created_by,
        )
        return grant

    def widen_grant_capabilities(
        self,
        grant_id: str,
        capabilities: Sequence[FileCapability | str],
    ) -> LocalGrant:
        """Bring an active grant up to the capabilities the level now allows.

        The PC access level is global, so a level change has to reach the
        grants that were made under the old one: a grant that was frozen at
        a read-only level would otherwise leave the Project read-only for
        ever, with no way for the operator to tell why.

        The new capabilities are checked against the level in force, so this
        can only ever NARROW towards the level as well as widen to it - it
        can never grant more than the level permits.  A REVOKED grant is
        not brought back: an explicit revocation stays a revocation.
        """
        grant = self.grant(grant_id)
        if grant is None:
            raise ValueError("grant not found")
        if not grant.active:
            raise PermissionError("path grant is revoked or expired")
        profile = PermissionProfile(str(self.profile(grant.project_id)["profile"]))
        allowed = PROFILE_CAPABILITIES[profile]
        requested = tuple(
            dict.fromkeys(FileCapability(item).value for item in capabilities)
        )
        if not requested:
            raise ValueError("at least one capability is required")
        if any(FileCapability(item) not in allowed for item in requested):
            raise PermissionError("requested grant capability exceeds the active profile")
        if set(requested) == set(grant.capabilities):
            return grant
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE local_path_grants SET capabilities_json=? WHERE grant_id=?",
                (
                    json.dumps(list(requested), separators=(",", ":")),
                    grant_id,
                ),
            )
        self.record_event(
            project_id=grant.project_id,
            grant_id=grant_id,
            action="grant_capabilities_widened",
            target=grant.canonical_root,
            outcome="allowed",
            detail={
                "from": list(grant.capabilities),
                "to": list(requested),
                "profile": profile.value,
            },
            actor="permission_level_changed",
        )
        widened = self.grant(grant_id)
        assert widened is not None
        return widened

    def grant(self, grant_id: str) -> LocalGrant | None:
        row = self._connection.execute(
            "SELECT * FROM local_path_grants WHERE grant_id=?", (grant_id,)
        ).fetchone()
        return self._grant_from_row(row) if row is not None else None

    def grants(self, project_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM local_path_grants WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        grants = [self._grant_from_row(row) for row in rows]
        if active_only:
            grants = [grant for grant in grants if grant.active]
        return [grant.to_dict() for grant in grants]

    def revoke_grant(
        self, grant_id: str, *, revoked_by: str = "desktop_user", reason: str = "User revoked access."
    ) -> LocalGrant:
        grant = self.grant(grant_id)
        if grant is None:
            raise ValueError("grant not found")
        if grant.revoked_at is not None:
            return grant
        timestamp = _now()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE local_path_grants SET revoked_at=?, revoked_by=?, revoke_reason=? WHERE grant_id=?",
                (timestamp, revoked_by[:120], reason[:1000], grant_id),
            )
        self.record_event(
            project_id=grant.project_id,
            grant_id=grant_id,
            action="grant_revoked",
            target=grant.canonical_root,
            outcome="revoked",
            detail={"reason": reason[:1000]},
            actor=revoked_by,
        )
        resolved = self.grant(grant_id)
        assert resolved is not None
        return resolved

    def record_event(
        self,
        *,
        project_id: str,
        action: str,
        outcome: str,
        actor: str,
        grant_id: str | None = None,
        target: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> LocalToolEvent:
        detail_value = dict(detail or {})
        assert_no_sensitive_keys(detail_value, path="local_tool_event.detail")
        encoded = _canonical_json(detail_value)
        if len(encoded) > MAX_EVENT_DETAIL_CHARS:
            raise ValueError("local tool event detail is too large")
        event = LocalToolEvent(
            event_id=f"tool-event.{uuid4().hex}",
            project_id=project_id,
            grant_id=grant_id,
            action=action[:120],
            target=target[:2000] if target else None,
            outcome=outcome[:80],
            detail=detail_value,
            created_at=_now(),
            actor=actor[:120],
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO local_tool_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.project_id,
                    event.grant_id,
                    event.action,
                    event.target,
                    event.outcome,
                    encoded,
                    event.created_at,
                    event.actor,
                ),
            )
        return event

    def create_trash_record(
        self,
        *,
        project_id: str,
        grant_id: str,
        original_relative_path: str,
        trash_path: Path,
        is_directory: bool,
    ) -> str:
        trash_id = f"trash.{uuid4().hex}"
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO local_trash_records VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    trash_id,
                    project_id,
                    grant_id,
                    original_relative_path,
                    str(trash_path),
                    int(is_directory),
                    _now(),
                ),
            )
        return trash_id

    def trash_record(self, trash_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM local_trash_records WHERE trash_id=?", (trash_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def mark_restored(self, trash_id: str, *, restored_by: str) -> None:
        with self._lock, self._connection:
            if not self._connection.execute(
                "UPDATE local_trash_records SET restored_at=?, restored_by=? "
                "WHERE trash_id=? AND restored_at IS NULL",
                (_now(), restored_by[:120], trash_id),
            ).rowcount:
                raise ValueError("trash record is missing or already restored")

    def events(self, project_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("event limit must be between 1 and 1000")
        rows = self._connection.execute(
            "SELECT * FROM local_tool_events WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [
            LocalToolEvent(
                event_id=str(row["event_id"]),
                project_id=str(row["project_id"]),
                grant_id=row["grant_id"],
                action=str(row["action"]),
                target=row["target"],
                outcome=str(row["outcome"]),
                detail=json.loads(row["detail_json"]),
                created_at=str(row["created_at"]),
                actor=str(row["actor"]),
            ).to_dict()
            for row in rows
        ]

    def snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "profile": self.profile(project_id),
            "grants": self.grants(project_id),
            "events": self.events(project_id),
            "full_access": self.full_access_scope(project_id),
            "limits": {
                "max_read_bytes": MAX_READ_BYTES,
                "max_write_bytes": MAX_WRITE_BYTES,
                "max_list_entries": MAX_LIST_ENTRIES,
                "max_search_results": MAX_SEARCH_RESULTS,
                "max_search_depth": MAX_SEARCH_DEPTH,
                "max_search_seconds": MAX_SEARCH_SECONDS,
                "max_search_content_bytes": MAX_SEARCH_CONTENT_BYTES,
                "max_tree_entries": MAX_TREE_ENTRIES,
                "max_tree_bytes": MAX_TREE_BYTES,
                "max_command_seconds": MAX_COMMAND_SECONDS,
                "max_command_output_bytes": MAX_COMMAND_OUTPUT_BYTES,
            },
            "command_execution": "inspection_only_exact_approval",
            "application_control": "proposal_only",
        }


class LocalToolExecutor:
    """Execute only explicitly granted, bounded deterministic local tools."""

    #: Frozen, auditable refusal list.  These executables stay refused EVEN
    #: when a user approved the proposal: approval authorizes the reviewed
    #: argv, never a category of destructive system mutation.
    DENIED_EXECUTABLES = frozenset({
        "format", "diskpart", "reg", "bcdedit", "shutdown", "logoff",
        "sc", "net", "netsh", "wmic", "rundll32", "msiexec", "cipher",
        "vssadmin", "wevtutil", "takeown", "icacls", "attrib",
        "powershell", "pwsh", "cmd",
    })
    _SHELL_NAMES = frozenset({"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "bash", "sh"})
    _RUN_LOG_NEWLINE = "\n...output truncated at policy cap...\n"

    #: Deterministic allowlist policies for the approved tier.  Grant-scoped
    #: cwd is NOT a filesystem boundary for an arbitrary subprocess, so v1
    #: executes only argv shapes reviewed here (read-only inspection class).
    #: Each policy returns True when the full argv is within policy.
    APPROVED_ARGV_POLICIES: dict[str, Any] = {
        "git": lambda args: tuple(args[1:]) in {
            ("--version",),
            ("status", "--short"),
            ("status", "--porcelain"),
            ("status", "--porcelain", "--branch"),
            ("branch", "--show-current"),
            ("branch", "-a"),
            ("rev-parse", "--is-inside-work-tree"),
            ("rev-parse", "--show-toplevel"),
            ("ls-files",),
            ("ls-files", "--others", "--exclude-standard"),
            ("diff", "--stat"),
            ("diff", "--name-only"),
            ("diff", "--cached", "--stat"),
            ("log", "--oneline", "--max-count=20"),
            ("log", "--oneline", "--graph", "--max-count=20"),
            ("remote", "-v"),
            ("show", "--stat"),
        },
        "whoami": lambda args: len(args) == 1,
        "python": lambda args: len(args) == 2 and args[1] == "--version",
        "node": lambda args: len(args) == 2 and args[1] == "--version",
        "npm": lambda args: tuple(args[1:]) in {("--version",), ("--v",)},
        # Long-running diagnostics need a genuinely long-lived approved command
        # to prove timeout and cancellation against real processes.  ping is
        # the minimal safe choice: read-only probe, loopback target only,
        # count bounded to 60 (~1 minute on Windows).
        "ping": lambda args: _approved_ping_policy(args),
    }

    def __init__(self, store: LocalPermissionStore, *, state_root: str | Path) -> None:
        self.store = store
        self.state_root = Path(state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.command_runs: dict[str, dict[str, Any]] = {}
        self._command_lock = threading.Lock()
        self._run_db_path = self.state_root / "command-runs.sqlite3"
        self._active_processes: dict[str, "subprocess.Popen[bytes]"] = {}
        self._timed_out_runs: set[str] = set()
        self._application_sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self._browser_sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self._init_run_store()
        self._load_persisted_runs()
        self._cancel_requested: set[str] = set()

    def _active_grant(
        self, *, project_id: str, grant_id: str, capability: FileCapability
    ) -> LocalGrant:
        grant = self.store.grant(grant_id)
        if grant is None or grant.project_id != project_id:
            raise PermissionError("path grant was not found for this project")
        if not grant.active:
            raise PermissionError("path grant is revoked or expired")
        if not self.store._project_exists(project_id):
            raise PermissionError("the requesting project or chat no longer exists")
        profile = PermissionProfile(str(self.store.profile(project_id)["profile"]))
        policy = self.store.profile(project_id)
        if capability not in PROFILE_CAPABILITIES[profile]:
            raise PermissionError("active permission profile denies this capability")
        if capability.value not in grant.capabilities:
            raise PermissionError("path grant does not include this capability")
        # Defence in depth: a machine-wide grant is only ever usable while the
        # GLOBAL level is Full Computer.  If a revocation was missed or lagged,
        # the grant is still inert here rather than quietly staying live.
        if grant.provisioned and profile is not PermissionProfile.FULL_COMPUTER:
            raise PermissionError(
                "machine-wide Full Computer access is not active for this profile"
            )
        if profile in _WORKSPACE_BOUND_PROFILES:
            workspace_value = policy.get("workspace_root")
            if not workspace_value or not _contains(
                _resolve_workspace_root(str(workspace_value)),
                Path(grant.canonical_root).resolve(strict=True),
            ):
                raise PermissionError(
                    "path grant is outside the active project workspace"
                )
        return grant

    def _workspace_root(self, project_id: str) -> Path | None:
        """The folder this Project is rooted at, when it has one."""

        try:
            row = self.store.profile(project_id) or {}
        except Exception:
            return None
        workspace = str(row.get("workspace_root") or "").strip()
        if not workspace:
            return None
        try:
            return _resolve_workspace_root(workspace)
        except (OSError, ValueError, PermissionError):
            return None

    def _workspace_is_granted(
        self,
        workspace: Path,
        candidates: Sequence[LocalGrant],
    ) -> bool:
        """True when some granted root covers the Project's folder.

        Under Full Computer the drive grants cover it; under a folder-bound
        level it only counts when the Project folder was granted explicitly.
        """

        for grant in candidates:
            root = Path(grant.canonical_root)
            if _contains(root.resolve(strict=False), workspace.resolve(strict=False)):
                return True
        return False

    def resolve_mutation_target(
        self,
        *,
        project_id: str,
        action: FileCapability | str,
        path: str,
    ) -> dict[str, Any]:
        """Resolve the governing grant for a mutation request (26C DEFECT-14).

        Required semantics:
        - A directory grant authorizing CREATE lets a NEW child file inside the
          granted directory be created (capability CREATE).
        - Existing-file modification is not blanket-authorized by CREATE; it
          requires WRITE (and an exact human approval at execution).
        - A relative target normalizes deterministically against the granted
          root; an absolute target inside the granted scope resolves to the
          same authorization decision as its equivalent relative target.

        Returns a resolution dict:
        {matched, grant_id, requested_path, relative_path, capability,
         target_exists, reason, capabilities}
        On no match, matched=False with an audit-ready reason.
        """
        action_value = FileCapability(action).value
        profile = PermissionProfile(str(self.store.profile(project_id)["profile"]))
        candidate_grants = []
        for grant_dict in self.store.grants(project_id, active_only=True):
            grant = self.store.grant(str(grant_dict["grant_id"]))
            if grant is None or not grant.active:
                continue
            candidate_grants.append(grant)

        raw_path = str(path or "").strip().strip('"').strip("`")
        # Deterministic normalization (26C DEFECT-14): small local models
        # frequently emit a malformed drive ("D\ChatGPT\..." instead of
        # "D:\ChatGPT\...").  Repairing the drive colon before the containment
        # decision is not a governance weakening: the repaired path is still
        # subject to the exact same inside-scope check.
        drive_fix = re.match(r"^([A-Za-z])[\\/](.+)$", raw_path)
        if drive_fix and not re.match(r"^[A-Za-z]:[\\/]", raw_path):
            raw_path = f"{drive_fix.group(1)}:\\{drive_fix.group(2)}"
        if not raw_path:
            return self._unresolved_mutation(
                project_id=project_id, action=action_value, path=path,
                reason="mutation target path is empty",
                candidates=candidate_grants,
            )

        parsed = Path(raw_path)
        is_absolute = bool(parsed.is_absolute() or parsed.drive or parsed.anchor)
        workspace = self._workspace_root(project_id)
        if (
            not is_absolute
            and workspace is not None
            and self._workspace_is_granted(workspace, candidate_grants)
        ):
            # A relative path is PROJECT-relative: the agent's home is the
            # folder the operator pointed this Project at, not whichever
            # drive happens to be granted first.  Under Full Computer the
            # drive grants cover the folder but the tool contract still
            # says "project-relative", so the path is anchored here and then
            # matched against the grants like any absolute target.
            raw_path = str(workspace / raw_path) if raw_path not in ("", ".") else str(workspace)
            parsed = Path(raw_path)
            is_absolute = True

        def root_contains_lexical(root: Path, target: Path) -> bool:
            root_str = str(root).rstrip("\\/").casefold()
            target_str = str(target).casefold()
            return target_str == root_str or target_str.startswith(root_str + "\\") or target_str.startswith(root_str + "/")

        matched: list[tuple[LocalGrant, str]] = []  # (grant, relative path or "")
        relative_candidates: list[tuple[LocalGrant, str]] = []
        for grant in candidate_grants:
            root = Path(grant.canonical_root)
            if is_absolute:
                normalized = Path(raw_path.replace("/", "\\"))
                try:
                    absolute = normalized.resolve(strict=False)
                except OSError:
                    continue
                if root_contains_lexical(root, absolute):
                    try:
                        relative = absolute.resolve(strict=False).relative_to(root.resolve(strict=False))
                    except ValueError:
                        relative_cf = Path(str(absolute).casefold()).relative_to(
                            Path(str(root).casefold())
                        )
                        relative = Path(*relative_cf.parts)
                    matched.append((grant, relative.as_posix()))
            else:
                relative_candidates.append((grant, raw_path.replace("\\", "/")))

        if len(relative_candidates) == 1:
            # One granted root is the anchor; this is how a relative target has
            # always been read, and it is unambiguous.
            matched.extend(relative_candidates)
        elif relative_candidates:
            # A relative path carries no anchor of its own, so with several
            # granted roots -- the whole-PC drive set -- every root matched it
            # and the first one won.  A target that already sits under exactly
            # one of them is anchored there instead, which is what the model
            # meant and what containment then enforces.
            holding = [
                item for item in relative_candidates
                if (Path(item[0].canonical_root) / item[1]).exists()
            ]
            if len(holding) == 1:
                matched.extend(holding)
            else:
                # Nothing holds it, or more than one does.  Refusing here would
                # break a contract the product depends on: a relative path to a
                # file that does not exist yet must still resolve, so a stat can
                # answer "false" and a create can proceed.  The first capable
                # grant therefore still anchors it, exactly as before, and the
                # executor's own existence check decides the outcome.
                matched.extend(relative_candidates)

        def has_cap(grant: LocalGrant, capability: str) -> bool:
            return capability in grant.capabilities and capability in {
                item.value for item in PROFILE_CAPABILITIES[profile]
            }

        # Capability preference: exact action capability first; for write
        # requests against a not-yet-existing target, a CREATE grant governs.
        def pick(preferred: str) -> tuple[LocalGrant, str] | None:
            for grant, relative in matched:
                if has_cap(grant, preferred):
                    return grant, relative
            return None

        resolution: dict[str, Any] | None = None
        if action_value in ("write", "create"):
            for grant, relative in matched:
                root = Path(grant.canonical_root)
                target_exists = (root / relative).exists()
                # A write against a not-yet-existing target is a CREATION, so
                # the CREATE capability governs it.  Preferring WRITE first made
                # a grant holding BOTH capabilities resolve to WRITE for a file
                # that did not exist yet, which pushed the request into the
                # exact-scope approval path -- and that path requires the target
                # to exist, so a governed write could never create a file at
                # all.  This restores the rule stated above; the existing-target
                # case (WRITE + approval + version backup) is unchanged.
                if not target_exists and has_cap(grant, "create"):
                    resolution = {
                        "matched": True,
                        "grant_id": grant.grant_id,
                        "requested_path": raw_path,
                        "relative_path": relative,
                        "capability": FileCapability.CREATE.value,
                        "target_exists": False,
                        "capabilities": list(grant.capabilities),
                    }
                    break
                if has_cap(grant, "write") and action_value == "write":
                    resolution = {
                        "matched": True,
                        "grant_id": grant.grant_id,
                        "requested_path": raw_path,
                        "relative_path": relative,
                        "capability": FileCapability.WRITE.value,
                        "target_exists": target_exists,
                        "capabilities": list(grant.capabilities),
                    }
                    break
        else:
            chosen = pick(action_value)
            if chosen is not None:
                grant, relative = chosen
                resolution = {
                    "matched": True,
                    "grant_id": grant.grant_id,
                    "requested_path": raw_path,
                    "relative_path": relative,
                    "capability": action_value,
                    "target_exists": (Path(grant.canonical_root) / relative).exists(),
                    "capabilities": list(grant.capabilities),
                }

        if (
            resolution is not None
            and action_value in _EXISTING_TARGET_ACTIONS
            and not resolution.get("target_exists")
        ):
            # append/copy/move/trash all act on an EXISTING target.  Refusing
            # here with a reason the model can act on replaces the opaque
            # "FileNotFoundError [WinError 2]" the approval proposal used to
            # raise for a missing target.  Read-shaped capabilities are NOT in
            # this set: _agent_grant_for routes read grants through the same
            # resolver, and a read grant must still resolve for a path that
            # does not exist yet.
            return self._unresolved_mutation(
                project_id=project_id, action=action_value, path=path,
                reason=(
                    f"{action_value} target does not exist; "
                    f"create it first, then retry"
                ),
                candidates=candidate_grants,
            )

        if resolution is None:
            reason = (
                "no active path grant covers this target with the required capability"
                if matched
                else "target path is outside every active path grant for this project"
            )
            return self._unresolved_mutation(
                project_id=project_id, action=action_value, path=path,
                reason=reason, candidates=candidate_grants,
            )

        resolution["matched"] = True
        resolution["reason"] = "matched active path grant"
        self.store.record_event(
            project_id=project_id,
            grant_id=str(resolution["grant_id"]),
            action=f"{action_value}_grant_resolved",
            target=str(resolution["requested_path"]),
            outcome="allowed",
            detail={
                "matched_grant_id": str(resolution["grant_id"]),
                "matched_capability": str(resolution["capability"]),
                "grant_capabilities": list(resolution["capabilities"]),
                "relative_path": str(resolution["relative_path"]),
                "target_exists": bool(resolution["target_exists"]),
            },
            actor="permission_agent",
        )
        return resolution

    def _unresolved_mutation(
        self,
        *,
        project_id: str,
        action: str,
        path: str,
        reason: str,
        candidates: Sequence[LocalGrant],
    ) -> dict[str, Any]:
        detail = {
            "matched_grant_id": None,
            "matched_capability": None,
            "reason": reason,
            "active_grants": [
                {"grant_id": grant.grant_id, "capabilities": list(grant.capabilities)}
                for grant in candidates
            ],
        }
        self.store.record_event(
            project_id=project_id,
            grant_id=None,
            action=f"{action}_grant_resolved",
            target=str(path),
            outcome="denied",
            detail=detail,
            actor="permission_agent",
        )
        return {
            "matched": False,
            "grant_id": None,
            "requested_path": str(path),
            "relative_path": None,
            "capability": None,
            "target_exists": None,
            "capabilities": [],
            "reason": reason,
        }

    @staticmethod
    def _target(
        grant: LocalGrant,
        relative_path: str,
        *,
        must_exist: bool,
        require_parent: bool = True,
    ) -> Path:
        """Resolve a request path against the granted root.

        26C DEFECT-14 semantics: an absolute target inside the granted scope
        resolves to the same authorization decision as its equivalent
        normalized relative target.  Absolute targets outside the granted
        scope are rejected exactly like an escaping relative target.

        ``require_parent=False`` is for existence queries (stat), where a
        missing parent directory is an ANSWER rather than a traversal failure.
        Lexical containment, normalized resolution and the symlink/reparse
        guards still run; only the strict parent existence check is skipped.

        The reparse guard runs on the LEXICAL path, before anything is
        resolved.  Resolving first rewrites a junction to its target, so the
        traversal disappears from the string that the check reads and the
        guard has nothing left to refuse.
        """
        candidate_path = Path(relative_path or ".")
        # The lexical root is the path the grant was created from, not the
        # resolved one.  If the grant root is later replaced by a junction,
        # resolving it here would silently adopt the junction target as the
        # new grant root and the original boundary would be gone.
        lexical_root = Path(os.path.abspath(grant.requested_path))
        if not lexical_root.is_absolute() or _is_network_path(lexical_root):
            lexical_root = Path(os.path.abspath(grant.canonical_root))
        _assert_no_link_components(lexical_root, root=Path(lexical_root.anchor))
        root = lexical_root.resolve(strict=True)
        # Defense in depth: repair a malformed drive ("D\path" -> "D:\path")
        # before the containment decision; checks are unchanged after repair.
        raw_text = relative_path or ""
        drive_fix = re.match(r"^([A-Za-z])[\\/](.+)$", raw_text.strip())
        if drive_fix and not re.match(r"^[A-Za-z]:[\\/]", raw_text.strip()):
            candidate_path = Path(f"{drive_fix.group(1)}:\\{drive_fix.group(2)}")
        if candidate_path.is_absolute() or candidate_path.drive or candidate_path.anchor:
            # Reject reparse traversal on the caller's own spelling first.
            _assert_no_link_components(
                candidate_path, root=Path(candidate_path.anchor)
            )
            try:
                absolute = candidate_path.resolve(strict=must_exist)
            except (FileNotFoundError, OSError) as error:
                raise PermissionError(
                    "absolute target outside the granted root could not be resolved"
                ) from error
            try:
                candidate_path = absolute.relative_to(root)
            except ValueError:
                try:
                    absolute_cf = Path(str(absolute).casefold())
                    root_cf = Path(str(root).casefold())
                    candidate_path = absolute_cf.relative_to(root_cf)
                    candidate_path = Path(*candidate_path.parts)
                except ValueError as error:
                    raise PermissionError(
                        "resolved path escapes the granted root"
                    ) from error
        relative = candidate_path
        lexical = lexical_root / relative
        _assert_no_link_components(lexical, root=lexical_root)
        candidate = lexical.resolve(strict=must_exist)
        if not _contains(root, candidate):
            raise PermissionError("resolved path escapes the granted root")
        if not must_exist and require_parent:
            parent = candidate.parent.resolve(strict=True)
            if not _contains(root, parent):
                raise PermissionError("target parent escapes the granted root")
        return candidate

    @staticmethod
    def _metadata(path: Path, root: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": path.relative_to(root).as_posix() if path != root else ".",
            "name": path.name or str(path),
            "kind": "directory" if path.is_dir() else "file" if path.is_file() else "other",
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat().replace("+00:00", "Z"),
            "read_only": not os.access(path, os.W_OK),
        }

    @staticmethod
    def _file_contains_text(path: Path, needle: str) -> tuple[bool, int]:
        """Does this file's TEXT contain the needle, and how many bytes?

        The file's text is read, never its name, so the answer is the one
        the tool's description promises.  It is bounded on every axis: a
        file too large to read is not searched, a file that is not valid
        UTF-8 text is not searched (a binary file's bytes would only
        produce a meaningless answer), and a file that cannot be read is
        skipped rather than failing the whole search.  The byte count is
        returned so the caller can keep to a total budget.

        The byte count is what was READ FROM DISK, not what was
        searched: a binary file is read and then rejected, and its bytes
        still cost the machine, so they still count against the budget.
        """
        try:
            if not path.is_file() or path.is_symlink():
                return False, 0
            size = path.stat().st_size
            if size > MAX_READ_BYTES or size == 0:
                return False, 0
            data = path.read_bytes()
        except (OSError, ValueError):
            return False, 0
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # A binary file: its bytes were read, so they count, but its
            # bytes would only produce a meaningless answer.
            return False, len(data)
        return needle in text.casefold(), len(data)

    @staticmethod
    def _scan_tree(path: Path) -> tuple[int, int]:
        started = time.monotonic()
        entries = 0
        total_bytes = 0
        stack: list[tuple[Path, int]] = [(path, 0)]
        while stack:
            current, depth = stack.pop()
            if time.monotonic() - started > MAX_SEARCH_SECONDS:
                raise TimeoutError("filesystem tree scan exceeded its time limit")
            metadata = current.lstat()
            if _is_link_or_reparse(current):
                raise PermissionError(f"tree contains a symlink or reparse point: {current}")
            entries += 1
            if entries > MAX_TREE_ENTRIES:
                raise ValueError("filesystem tree exceeds the entry limit")
            if stat.S_ISREG(metadata.st_mode):
                total_bytes += metadata.st_size
                if total_bytes > MAX_TREE_BYTES:
                    raise ValueError("filesystem tree exceeds the byte limit")
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise PermissionError(f"unsupported filesystem object: {current}")
            if depth >= MAX_SEARCH_DEPTH:
                raise ValueError("filesystem tree exceeds the depth limit")
            with os.scandir(current) as iterator:
                for child in iterator:
                    child_metadata = child.stat(follow_symlinks=False)
                    if child.is_symlink() or bool(
                        getattr(child_metadata, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    ):
                        raise PermissionError(
                            f"tree contains a symlink or reparse point: {child.path}"
                        )
                    stack.append((Path(child.path), depth + 1))
        return entries, total_bytes

    def _approval(
        self,
        approval_id: str | None,
        *,
        project_id: str,
        action: PermissionAction,
        scope: Mapping[str, Any],
    ) -> None:
        if not approval_id:
            raise PermissionError(f"{action.value} requires an exact user approval")
        with PermissionStore(self.store.database) as approvals:
            approvals.authorize(
                approval_id, project_id=project_id, action=action, scope=scope
            )

    def _version(self, *, project_id: str, grant: LocalGrant, path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        if path.stat().st_size > MAX_TREE_BYTES:
            raise ValueError("existing file exceeds the version-backup byte limit")
        version_dir = self.state_root / "versions" / project_id / grant.grant_id
        version_dir.mkdir(parents=True, exist_ok=True)
        version = version_dir / f"{uuid4().hex}-{path.name}"
        shutil.copy2(path, version)
        return str(version)

    def _trash_path(self, *, project_id: str, grant: LocalGrant, source: Path) -> Path:
        destination_dir = self.state_root / "trash" / project_id / grant.grant_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        return destination_dir / f"{uuid4().hex}-{source.name}"

    def request_scope(
        self,
        *,
        action: PermissionAction | str,
        grant_id: str,
        source: str,
        destination: str | None = None,
        overwrite: bool = False,
        trash_id: str | None = None,
    ) -> dict[str, Any]:
        value = {
            "grant_id": grant_id,
            "source": source,
            "overwrite": bool(overwrite),
        }
        if destination is not None:
            value["destination"] = destination
        if trash_id is not None:
            # A restore moves a specific trashed object back; binding the
            # record id stops one restore approval covering another record.
            value["trash_id"] = trash_id
        return value

    def execute(
        self,
        *,
        project_id: str,
        grant_id: str,
        action: FileCapability | str,
        path: str = ".",
        destination: str | None = None,
        content: str | None = None,
        content_base64: str | None = None,
        query: str | None = None,
        trash_id: str | None = None,
        overwrite: bool = False,
        approval_id: str | None = None,
        actor: str = "amca_tool_router",
    ) -> dict[str, Any]:
        capability = FileCapability(action)
        grant = self._active_grant(
            project_id=project_id, grant_id=grant_id, capability=capability
        )
        root = Path(grant.canonical_root)
        try:
            result = self._execute(
                project_id=project_id,
                grant=grant,
                capability=capability,
                path=path,
                destination=destination,
                content=content,
                content_base64=content_base64,
                query=query,
                trash_id=trash_id,
                overwrite=overwrite,
                approval_id=approval_id,
            )
        except Exception as error:
            self.store.record_event(
                project_id=project_id,
                grant_id=grant_id,
                action=capability.value,
                target=path,
                outcome="denied" if isinstance(error, PermissionError) else "failed",
                detail={"error_type": type(error).__name__, "message": str(error)[:1000]},
                actor=actor,
            )
            raise
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action=capability.value,
            target=path,
            outcome="succeeded",
            detail={key: value for key, value in result.items() if key not in {"content", "content_base64", "entries", "matches"}},
            actor=actor,
        )
        return {"action": capability.value, "grant_id": grant_id, "root": str(root), **result}

    def _execute(
        self,
        *,
        project_id: str,
        grant: LocalGrant,
        capability: FileCapability,
        path: str,
        destination: str | None,
        content: str | None,
        content_base64: str | None,
        query: str | None,
        trash_id: str | None,
        overwrite: bool,
        approval_id: str | None,
    ) -> dict[str, Any]:
        root = Path(grant.canonical_root)
        if capability is FileCapability.RESTORE:
            record = self.store.trash_record(
                _clean_text(trash_id, name="trash_id", maximum=128)
            )
            if (
                record is None
                or record["project_id"] != project_id
                or record["grant_id"] != grant.grant_id
            ):
                raise ValueError("trash record was not found for this grant")
            if record["restored_at"] is not None:
                raise ValueError("trash record is already restored")
            target = self._target(
                grant, str(record["original_relative_path"]), must_exist=False
            )
            if target.exists():
                raise FileExistsError("restore target already exists")
            source = Path(str(record["trash_path"])).resolve(strict=True)
            trash_root = (self.state_root / "trash").resolve(strict=True)
            if not _contains(trash_root, source):
                raise PermissionError("trash record points outside AMCA recoverable storage")
            _assert_no_link_components(source, root=trash_root)
            entries, total_bytes = self._scan_tree(source)
            if not _same_filesystem(source, target.parent):
                raise PermissionError("recoverable restore must remain on one filesystem")
            # Restore moves an EXISTING filesystem object back into the tree,
            # so it is a move like any other and needs the same exact-scope
            # approval.  Without it, trashing with one approval and restoring
            # with none was a complete round trip with a single ask.
            self._approval(
                approval_id,
                project_id=project_id,
                action=PermissionAction.LOCAL_FILE_MOVE,
                scope=self.request_scope(
                    action=PermissionAction.LOCAL_FILE_MOVE,
                    grant_id=grant.grant_id,
                    source=str(record["trash_path"]),
                    # The grant-relative path, not the resolved absolute one:
                    # the proposal records the relative form, and the scope
                    # hash only matches if both sides use the same string.
                    destination=str(record["original_relative_path"]),
                    overwrite=False,
                    trash_id=str(record["trash_id"]),
                ),
            )
            shutil.move(str(source), str(target))
            self.store.mark_restored(str(record["trash_id"]), restored_by="desktop_user")
            return {
                "path": str(record["original_relative_path"]),
                "trash_id": str(record["trash_id"]),
                "restored": True,
                "entries": entries,
                "bytes": total_bytes,
            }
        if capability is FileCapability.LIST:
            target = self._target(grant, path, must_exist=True)
            if not target.is_dir():
                raise ValueError("list target must be a directory")
            entries = []
            for child in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
                if len(entries) >= MAX_LIST_ENTRIES:
                    break
                if _is_link_or_reparse(child):
                    continue
                resolved = child.resolve(strict=True)
                if _contains(root, resolved):
                    entries.append(self._metadata(resolved, root))
            return {"path": path, "entries": entries, "truncated": len(entries) >= MAX_LIST_ENTRIES}
        if capability is FileCapability.STAT:
            # stat is an existence query, so a missing target -- including one
            # whose parent directory does not exist -- is an ANSWER, not a
            # failure: reporting it as a failed tool call made models read a
            # not-yet-created file as a write error and abandon the task.
            target = self._target(grant, path or ".", must_exist=False, require_parent=False)
            requested = path or "."
            if not target.exists():
                return {"path": requested, "exists": False}
            return {"path": requested, "exists": True, "metadata": self._metadata(target, root)}
        if capability is FileCapability.READ:
            target = self._target(grant, path, must_exist=True)
            if not target.is_file():
                raise ValueError("read target must be a file")
            size = target.stat().st_size
            if size > MAX_READ_BYTES:
                raise ValueError(f"file exceeds the {MAX_READ_BYTES}-byte read limit")
            data = target.read_bytes()
            try:
                return {"path": path, "size_bytes": len(data), "encoding": "utf-8", "content": data.decode("utf-8")}
            except UnicodeDecodeError:
                return {"path": path, "size_bytes": len(data), "encoding": "base64", "content_base64": base64.b64encode(data).decode("ascii")}
        if capability is FileCapability.SEARCH:
            target = self._target(grant, path, must_exist=True)
            if not target.is_dir():
                raise ValueError("search target must be a directory")
            needle = _clean_text(query, name="query", maximum=500).casefold()
            started = time.monotonic()
            matches: list[dict[str, Any]] = []
            bytes_read = 0
            # The TEXT of a file is only searched when the grant also
            # carries READ, so a search-only grant cannot be used to ask
            # whether an unknown string is in a file.
            may_read_content = FileCapability.READ.value in grant.capabilities
            base_depth = len(target.parts)
            for current, directories, filenames in os.walk(target, followlinks=False):
                current_path = Path(current).resolve(strict=True)
                depth = len(current_path.parts) - base_depth
                if depth >= MAX_SEARCH_DEPTH:
                    directories[:] = []
                directories[:] = [
                    name for name in directories
                    if not _is_link_or_reparse(current_path / name)
                    and _contains(root, (current_path / name).resolve(strict=True))
                ]
                for name in [*directories, *filenames]:
                    if _is_link_or_reparse(current_path / name):
                        continue
                    matched_in: list[str] = []
                    if needle in name.casefold():
                        matched_in.append("name")
                    if name in filenames and may_read_content:
                        # The tool promises to search file TEXT as well as
                        # file names.  It used to match names only, so a model
                        # that searched for a string it had just written got
                        # an empty result and rightly concluded the search
                        # was unreliable - which blocked the run.
                        #
                        # The TEXT is only searched when the grant also
                        # carries READ.  A search-only grant must not become
                        # a way to ask whether an unknown string is in a
                        # file: that is reading, by another name.
                        hit, size = self._file_contains_text(
                            current_path / name, needle
                        )
                        bytes_read += size
                        if hit:
                            matched_in.append("content")
                    if matched_in:
                        match = (current_path / name).resolve(strict=True)
                        if _contains(root, match):
                            found = self._metadata(match, root)
                            # ONE list, with the kind of match on each entry.
                            # The result used to carry the same entries three
                            # times over, and the audit event keeps the result
                            # OUT of its detail, so the copies pushed the
                            # event past its size limit and the whole tool
                            # call failed for a large result set.
                            found["matched_in"] = "+".join(matched_in)
                            matches.append(found)
                    if (
                        len(matches) >= MAX_SEARCH_RESULTS
                        or bytes_read >= MAX_SEARCH_CONTENT_BYTES
                        or time.monotonic() - started >= MAX_SEARCH_SECONDS
                    ):
                        return {
                            "query": query,
                            "matches": matches,
                            "truncated": True,
                        }
            return {
                "query": query,
                "matches": matches,
                "truncated": False,
            }
        if capability is FileCapability.MKDIR:
            target = self._target(grant, path, must_exist=False)
            if target.exists():
                raise FileExistsError("mkdir target already exists")
            target.mkdir()
            return {"path": path, "created": True}

        data = (
            base64.b64decode(content_base64, validate=True)
            if content_base64 is not None
            else (content or "").encode("utf-8")
        )
        if len(data) > MAX_WRITE_BYTES:
            raise ValueError(f"content exceeds the {MAX_WRITE_BYTES}-byte write limit")
        # 28A NEW-01: document-extension content validation.  A plain-text
        # payload written to a .docx/.pptx/.xlsx name must never succeed as an
        # "artifact" — the content must be a genuine OOXML package.
        target_name = Path(path).name.lower()
        if target_name.endswith((".docx", ".pptx", ".xlsx")):
            _validate_ooxml_payload(target_name, data)
        if capability in {FileCapability.CREATE, FileCapability.WRITE, FileCapability.APPEND}:
            target = self._target(grant, path, must_exist=False)
            exists = target.exists()
            if exists and target.is_dir():
                raise ValueError("write target cannot be a directory")
            if capability is FileCapability.CREATE and exists:
                raise FileExistsError("create target already exists")
            # CREATE authorizes making a new file; WRITE authorizes replacing an
            # existing one (that is how the desktop UI labels them).  Enforcing it
            # here, at the boundary every caller passes through, is what stops a
            # stale resolution from silently downgrading an approved overwrite
            # into an approval-free creation when the target disappears between
            # resolution and execution.
            if capability is FileCapability.WRITE and not exists:
                raise FileNotFoundError(
                    "write target does not exist; use create to create a new file"
                )
            if capability is FileCapability.APPEND and not exists:
                # filesystem.append is documented to the model as appending to an
                # EXISTING file, so the executor must not be the layer that
                # silently creates one.
                raise FileNotFoundError(
                    "append target does not exist; create the file first"
                )
            version = None
            if exists:
                scope = self.request_scope(
                    action=PermissionAction.LOCAL_FILE_MODIFY,
                    grant_id=grant.grant_id,
                    source=path,
                    overwrite=overwrite,
                )
                self._approval(
                    approval_id,
                    project_id=project_id,
                    action=PermissionAction.LOCAL_FILE_MODIFY,
                    scope=scope,
                )
                version = self._version(project_id=project_id, grant=grant, path=target)
            if capability is FileCapability.APPEND:
                mode = "ab"
            elif capability is FileCapability.CREATE:
                # Exclusive create: a target that appears after the existence
                # check must never be overwritten, so the kernel decides, not
                # the check-then-open race.
                mode = "xb"
            else:
                mode = "wb"
            # Narrow (it does not close) the window between _target()'s
            # validation and the open below: a component that became a reparse
            # point in that window must not be followed.  The durable fix is a
            # handle-relative open with FILE_FLAG_OPEN_REPARSE_POINT.
            _assert_no_link_components(target, root=root)
            with target.open(mode) as stream:
                stream.write(data)
            return {"path": path, "size_bytes": len(data), "created": not exists, "version_backup": version}
        if capability is FileCapability.COPY:
            source = self._target(grant, path, must_exist=True)
            if destination is None:
                raise ValueError("copy requires a source and destination")
            entries, total_bytes = self._scan_tree(source)
            target = self._target(grant, destination, must_exist=False)
            if source.is_dir() and _contains(source, target):
                raise ValueError("a directory cannot be copied into itself")
            if target.exists() and not overwrite:
                raise FileExistsError("copy destination already exists")
            version = None
            # A copy is a mutation, so it needs a validated, approved id
            # WHETHER the destination already exists or not.  Validating only
            # the overwrite case let a copy to a NEW destination run with a
            # fabricated (or still-pending) approval id.
            # The scope carries the REQUEST's own overwrite flag, exactly as
            # the proposal recorded it, so a validly approved copy still
            # matches its approval.
            scope = self.request_scope(
                action=PermissionAction.LOCAL_FILE_MODIFY,
                grant_id=grant.grant_id,
                source=path,
                destination=destination,
                overwrite=overwrite,
            )
            self._approval(approval_id, project_id=project_id, action=PermissionAction.LOCAL_FILE_MODIFY, scope=scope)
            if target.exists():
                version = self._version(project_id=project_id, grant=grant, path=target)
            if source.is_dir():
                if target.exists():
                    raise ValueError("overwriting a directory is not supported")
                try:
                    shutil.copytree(source, target, symlinks=False)
                except Exception:
                    if target.exists():
                        shutil.rmtree(target)
                    raise
            else:
                shutil.copy2(source, target)
            return {"source": path, "destination": destination, "version_backup": version, "entries": entries, "bytes": total_bytes}
        if capability is FileCapability.MOVE:
            source = self._target(grant, path, must_exist=True)
            if destination is None:
                raise ValueError("move requires a destination")
            target = self._target(grant, destination, must_exist=False)
            if source.is_dir() and _contains(source, target):
                raise ValueError("a directory cannot be moved into itself")
            if target.exists() and not overwrite:
                raise FileExistsError("move destination already exists")
            scope = self.request_scope(
                action=PermissionAction.LOCAL_FILE_MOVE,
                grant_id=grant.grant_id,
                source=path,
                destination=destination,
                overwrite=overwrite,
            )
            self._approval(approval_id, project_id=project_id, action=PermissionAction.LOCAL_FILE_MOVE, scope=scope)
            entries, total_bytes = self._scan_tree(source)
            version = self._version(project_id=project_id, grant=grant, path=target) if target.exists() else None
            if target.exists():
                if target.is_dir():
                    raise ValueError("overwriting a directory is not supported")
                target.unlink()
            shutil.move(str(source), str(target))
            return {"source": path, "destination": destination, "version_backup": version, "entries": entries, "bytes": total_bytes}
        if capability is FileCapability.TRASH:
            source = self._target(grant, path, must_exist=True)
            entries, total_bytes = self._scan_tree(source)
            scope = self.request_scope(
                action=PermissionAction.LOCAL_FILE_TRASH,
                grant_id=grant.grant_id,
                source=path,
            )
            self._approval(approval_id, project_id=project_id, action=PermissionAction.LOCAL_FILE_TRASH, scope=scope)
            target = self._trash_path(project_id=project_id, grant=grant, source=source)
            if not _same_filesystem(source, target.parent):
                raise PermissionError("recoverable trash must remain on one filesystem")
            is_directory = source.is_dir()
            shutil.move(str(source), str(target))
            record_id = self.store.create_trash_record(
                project_id=project_id,
                grant_id=grant.grant_id,
                original_relative_path=path,
                trash_path=target,
                is_directory=is_directory,
            )
            return {"source": path, "trash_id": record_id, "recoverable": True, "entries": entries, "bytes": total_bytes}
        raise PermissionError("this capability has no direct executor")

    def propose_command(
        self, *, project_id: str, grant_id: str, command: str, cwd: str = ".", reason: str
    ) -> dict[str, Any]:
        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        cwd_path = self._target(grant, cwd, must_exist=True)
        if not cwd_path.is_dir():
            raise ValueError("command cwd must be a directory")
        command = _clean_text(command, name="command", maximum=8_000)
        scope = {
            "grant_id": grant_id,
            "cwd": cwd,
            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "executor": "not_implemented",
        }
        with PermissionStore(self.store.database) as approvals:
            request = approvals.request(
                project_id=project_id,
                action=PermissionAction.RUN_COMMAND,
                scope=scope,
                reason=_clean_text(reason, name="reason", maximum=2_000),
                risk="high",
                requested_by="tool_router",
            )
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action="command_proposed",
            target=cwd,
            outcome="waiting_for_approval",
            detail={"approval_id": request.id, "command_sha256": scope["command_sha256"]},
            actor="tool_router",
        )
        return {"approval": request.to_dict(), "execution_supported": False}

    #: Git write-form policy: exact subcommand shapes allowed on the
    #: approved tier.  Every form still requires an explicit approval bound
    #: to the full argv; push/pull/fetch/reset/rebase/clean and friends stay
    #: refused at v1 regardless of approval.
    GIT_REFUSED_SUBCOMMANDS = frozenset({
        "push", "pull", "fetch", "remote", "reset", "rebase",
        "clean", "cherry-pick", "revert", "bisect", "mv", "rm",
    })
    _GIT_REF_NAME = re.compile(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,80}"
    )

    @staticmethod
    def _validate_git_write(argv: Sequence[str], repo_root: Path) -> None:
        if len(argv) < 2 or Path(argv[0]).stem.casefold() != "git":
            raise PermissionError("git policy applies to git commands only")
        sub = argv[1]
        if sub in LocalToolExecutor.GIT_REFUSED_SUBCOMMANDS:
            raise PermissionError(f"git {sub!r} is refused at v1 even with approval")
        rest = argv[2:]
        def _ref(name: str) -> str:
            if not LocalToolExecutor._GIT_REF_NAME.fullmatch(name) or ".." in name:
                raise PermissionError(f"invalid git ref name {name!r}")
            return name

        repo_root_resolved = repo_root.resolve()

        def _contained(path: str) -> Path:
            if path.startswith("-"):
                raise PermissionError("pathspec must not look like an option")
            candidate = Path(path)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (repo_root / candidate).resolve()
            )
            # _contains compares real path components; a string-prefix test
            # would accept a sibling named like an extension of the repo.
            if not _contains(repo_root_resolved, resolved):
                raise PermissionError(
                    "git path escapes the granted repository"
                )
            return candidate

        if sub == "add":
            if not 1 <= len(rest) <= 8:
                raise PermissionError("git add requires 1-8 pathspecs")
            for pathspec in rest:
                _contained(pathspec)
        elif sub == "commit":
            if len(rest) != 2 or rest[0] != "-m" or not (1 <= len(rest[1]) <= 500):
                raise PermissionError("git commit requires exactly -m with a bounded message")
        elif sub in {"checkout", "switch"}:
            if len(rest) != 2 or rest[0] not in {"-b", "-c"}:
                raise PermissionError("only checkout -b / switch -c create branches")
            _ref(rest[1])
        elif sub == "branch":
            if len(rest) != 1:
                raise PermissionError("git branch requires exactly one new ref name")
            _ref(rest[0])
        elif sub == "worktree":
            if rest[:1] == ["add"] and len(rest) == 3:
                _contained(rest[1])
                _ref(rest[2])
            elif rest == ["list"]:
                pass
            else:
                raise PermissionError("only worktree add/list are permitted")
        else:
            raise PermissionError(
                f"git write form {sub!r} is outside the reviewed v1 policy"
            )

    def propose_argv_command(
        self,
        *,
        project_id: str,
        grant_id: str,
        argv: Sequence[str],
        cwd: str = ".",
        reason: str,
    ) -> dict[str, Any]:
        """Bind an exact argv under an explicit RUN_COMMAND approval."""

        args = self._validate_proposal_argv(argv)
        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        cwd_path = self._target(grant, cwd, must_exist=True)
        if not cwd_path.is_dir():
            raise ValueError("command cwd must be a directory")
        executable = Path(args[0]).stem.casefold()
        if executable in self.DENIED_EXECUTABLES:
            raise PermissionError(
                f"executable {executable!r} is refused by AMCA policy even with approval"
            )
        policy = self.APPROVED_ARGV_POLICIES.get(executable)
        if policy is None or not policy(args):
            raise PermissionError(
                "approved-tier commands must match a reviewed allowlist policy; "
                f"no policy accepts this argv for {executable!r}"
            )
        scope = {
            "grant_id": grant_id,
            "cwd": cwd,
            "argv": args,
            "argv_sha256": hashlib.sha256(
                json.dumps(args, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "executor": "bounded_subprocess",
        }
        with PermissionStore(self.store.database) as approvals:
            request = approvals.request(
                project_id=project_id,
                action=PermissionAction.RUN_COMMAND,
                scope=scope,
                reason=_clean_text(reason, name="reason", maximum=2_000),
                risk="high",
                requested_by="tool_router",
            )
        return {"approval": request.to_dict(), "execution_supported": True}

    @staticmethod
    def _validate_proposal_argv(argv: Sequence[str]) -> list[str]:
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
            raise ValueError("argv must be an array of strings")
        args = [str(item) for item in argv]
        if not 1 <= len(args) <= 16 or any(
            not item or len(item) > 500 or "\x00" in item for item in args
        ):
            raise ValueError("argv is empty or exceeds bounded-command limits")
        for item in args:
            if Path(item).name.casefold() in LocalToolExecutor._SHELL_NAMES:
                raise PermissionError("shell interpreters are refused in bounded argv")
        return args

    def execute_approved_argv(
        self,
        *,
        project_id: str,
        grant_id: str,
        approval_id: str,
        timeout_seconds: float = 60,
        max_output_bytes: int = 256 * 1024,
    ) -> dict[str, Any]:
        """Execute the EXACT approved argv with hard bounds and an audit trail.

        Output streams into an incremental in-memory run log so the terminal
        can poll progress; subprocess is always spawned shell-free.
        """

        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        with PermissionStore(self.store.database) as approvals:
            record = approvals.get(approval_id)
        if record is None or record.project_id != project_id:
            raise PermissionError("unknown command approval")
        if record.action != PermissionAction.RUN_COMMAND:
            raise PermissionError("approval is not a RUN_COMMAND approval")
        if ApprovalStatus(record.status) != ApprovalStatus.APPROVED:
            raise PermissionError("approval is not in the approved state")
        scope = dict(record.scope or {})
        if scope.get("executor") != "bounded_subprocess":
            raise PermissionError("approval was not proposed for bounded execution")
        argv = [str(item) for item in scope.get("argv", [])]
        digest = hashlib.sha256(
            json.dumps(argv, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if digest != scope.get("argv_sha256"):
            raise PermissionError("approved argv does not match its recorded digest")
        # Consume the approval atomically.  It is single-use, so a replay of the
        # same id has to fail even while the record still reads APPROVED -
        # checking the status alone let one approval run without limit.
        self._approval(
            approval_id,
            project_id=project_id,
            action=PermissionAction.RUN_COMMAND,
            scope=scope,
        )
        executable = Path(argv[0]).stem.casefold()
        policy = self.APPROVED_ARGV_POLICIES.get(executable)
        if policy is None or not policy(argv):
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="command_executed", target=str(scope.get("cwd", ".")),
                outcome="refused",                detail={"reason": "outside approved argv policy", "executable": executable},
                actor="tool_router",
            )
            raise PermissionError(
                "approved-tier commands must match a reviewed allowlist policy"
            )
        if executable in self.DENIED_EXECUTABLES:
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="command_executed", target=str(scope.get("cwd", ".")),
                outcome="refused",
                detail={"reason": "denied executable", "executable": executable},
                actor="tool_router",
            )
            raise PermissionError(
                f"executable {executable!r} is refused by AMCA policy even with approval"
            )
        cwd_path = self._target(grant, str(scope.get("cwd", ".")), must_exist=True)

        run_id = uuid4().hex
        started_mono = time.monotonic()
        entry: dict[str, Any] = {
            "run_id": run_id,
            "project_id": project_id,
            "grant_id": grant_id,
            "approval_id": approval_id,
            "argv": argv,
            "status": "running",
            "output": "",
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "exit_code": None,
            "elapsed_seconds": None,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
        }
        with self._command_lock:
            self.command_runs[run_id] = entry
        self._persist_run(dict(entry), project_id=project_id, grant_id=grant_id)

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

        def _reader(process: "subprocess.Popen[bytes]") -> None:
            received = 0
            try:
                assert process.stdout is not None
                for line in iter(process.stdout.readline, b""):
                    received += len(line)
                    if received > max_output_bytes:
                        entry["truncated"] = True
                        entry["output"] += self._RUN_LOG_NEWLINE
                        process.kill()
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    entry["output"] += decoded
                    entry["stdout"] += decoded
                    if len(entry["output"]) > max_output_bytes:
                        entry["output"] = entry["output"][-max_output_bytes:]
                for err_line in iter(process.stderr.readline, b""):
                    entry["stderr"] += err_line.decode("utf-8", errors="replace")
                entry["exit_code"] = process.wait(timeout=10)
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                if entry["status"] == "running":
                    entry["status"] = (
                        "cancelled" if run_id in self._cancel_requested
                        else "timeout" if run_id in self._timed_out_runs
                        else "completed" if entry["exit_code"] is not None else "failed"
                    )
                entry["finished_at"] = datetime.now(UTC).isoformat()
                entry["elapsed_seconds"] = round(time.monotonic() - started_mono, 6)
                self._active_processes.pop(run_id, None)
                self._persist_run(dict(entry), project_id=project_id, grant_id=grant_id)

        def _watchdog() -> None:
            deadline = time.monotonic() + float(timeout_seconds)
            while time.monotonic() < deadline and entry["status"] == "running":
                time.sleep(0.1)
            if entry["status"] == "running":
                self._timed_out_runs.add(run_id)
                entry["truncated"] = True
                entry["status"] = "timeout"
                entry["output"] += "\n...terminated at approved timeout...\n"
                try:
                    process.kill()
                except OSError:
                    pass

        try:
            process = subprocess.Popen(  # noqa: S603 - exact approved argv, no shell
                argv,
                cwd=cwd_path,
                env=self._safe_command_environment(),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
        except OSError as error:
            entry.update({"status": "failed", "error": str(error), "finished_at": _now()})
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="command_executed", target=str(scope.get("cwd", ".")),
                outcome="failed", detail={"error": str(error), "argv": argv},
                actor="tool_router",
            )
            raise ValueError(f"failed to start approved command: {error}") from error

        self._active_processes[run_id] = process
        threading.Thread(target=_reader, args=(process,), daemon=True).start()
        threading.Thread(target=_watchdog, daemon=True).start()
        self.store.record_event(
            project_id=project_id, grant_id=grant_id,
            action="command_executed", target=str(scope.get("cwd", ".")),
            outcome="started", detail={"run_id": run_id, "argv": argv},
            actor="tool_router",
        )
        return {key: value for key, value in entry.items() if key != "output"}

    def git_worktree_add(
        self,
        *,
        project_id: str,
        grant_id: str,
        approval_id: str,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Approved 'git worktree add <path> <branch>' via the write policy."""

        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        with PermissionStore(self.store.database) as approvals:
            record = approvals.get(approval_id)
        if record is None or record.project_id != project_id:
            raise PermissionError("unknown command approval")
        if record.action != PermissionAction.RUN_COMMAND:
            raise PermissionError("approval is not a RUN_COMMAND approval")
        if ApprovalStatus(record.status) != ApprovalStatus.APPROVED:
            raise PermissionError("approval is not in the approved state")
        scope = dict(record.scope or {})
        argv = [str(item) for item in scope.get("argv", [])]
        digest = hashlib.sha256(
            json.dumps(argv, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if digest != scope.get("argv_sha256"):
            raise PermissionError("approved argv does not match its recorded digest")
        # Single-use: consume the approval before the git command runs, so a
        # replay of the same id fails even while it still reads APPROVED.
        self._approval(
            approval_id,
            project_id=project_id,
            action=PermissionAction.RUN_COMMAND,
            scope=scope,
        )
        repo_root = self._target(grant, str(scope.get("cwd", ".")), must_exist=True)
        self._validate_git_write(argv, repo_root)
        effective_argv = [
            argv[0],
            "-c", "user.email=amca@local",
            "-c", "user.name=AMCA Local",
            *argv[1:],
        ]
        result = self._run_git_sync(effective_argv, repo_root, timeout_seconds=timeout_seconds)
        listing = None
        if result["exit_code"] == 0:
            list_argv = ["git", "worktree", "list", "--porcelain"]
            listing = self._run_git_sync(list_argv, repo_root, timeout_seconds=20)
        return {
            **result,
            "worktrees": (
                self._parse_worktrees(listing["output"]) if listing else []
            ),
        }

    @staticmethod
    def _parse_worktrees(output: str) -> list[dict[str, str]]:
        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in output.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[len("worktree "):]}
            elif line.startswith("HEAD ") and current is not None:
                current["head"] = line[5:]
            elif line.startswith("branch ") and current is not None:
                current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        if current:
            worktrees.append(current)
        return worktrees

    def git_worktree_list(self, *, project_id: str, grant_id: str) -> dict[str, Any]:
        """Read-only porcelain worktree listing at the granted repo root."""

        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        repo_root = self._target(grant, ".", must_exist=True)
        result = self._run_git_sync(
            ["git", "worktree", "list", "--porcelain"], repo_root, timeout_seconds=20
        )
        return {"worktrees": self._parse_worktrees(result["output"])}

    def _run_git_sync(
        self,
        argv: Sequence[str],
        cwd_path: Path,
        *,
        timeout_seconds: float = 30,
        max_output_bytes: int = 256 * 1024,
    ) -> dict[str, Any]:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd_path,
                env=self._safe_command_environment(),
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=float(timeout_seconds),
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("git command exceeded its approved timeout") from error
        stdout = completed.stdout[:max_output_bytes]
        stderr_text = completed.stderr[:4096].decode("utf-8", errors="replace")
        return {
            "exit_code": completed.returncode,
            "output": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr_text,
            "truncated": len(completed.stdout) > len(stdout),
        }

    def git_status(self, *, project_id: str, grant_id: str) -> dict[str, Any]:
        """Read-only repository status at the granted repo root."""

        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        repo_root = self._target(grant, ".", must_exist=True)
        argv = ["git", "status", "--porcelain", "--branch"]
        result = self._run_git_sync(argv, repo_root, timeout_seconds=20)
        lines = [line for line in result["output"].splitlines() if line.strip()]
        branch = ""
        if lines and lines[0].startswith("## "):
            branch = lines[0][3:].split("...")[0].strip("[]")
            changes = lines[1:]
        else:
            changes = lines
        return {
            "branch": branch,
            "changes": changes,
            "exit_code": result["exit_code"],
            "stderr": result["stderr"],
        }

    def git_write_command(
        self,
        *,
        project_id: str,
        grant_id: str,
        approval_id: str,
        timeout_seconds: float = 60,
    ) -> dict[str, Any]:
        """Execute an approved git write argv through the bounded runner."""

        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        with PermissionStore(self.store.database) as approvals:
            record = approvals.get(approval_id)
        if record is None or record.project_id != project_id:
            raise PermissionError("unknown command approval")
        if record.action != PermissionAction.RUN_COMMAND:
            raise PermissionError("approval is not a RUN_COMMAND approval")
        if ApprovalStatus(record.status) != ApprovalStatus.APPROVED:
            raise PermissionError("approval is not in the approved state")
        scope = dict(record.scope or {})
        argv = [str(item) for item in scope.get("argv", [])]
        if len(argv) < 2 or Path(argv[0]).stem.casefold() != "git":
            raise PermissionError("approval is not a git command")
        import hashlib as _hashlib

        digest = _hashlib.sha256(
            json.dumps(argv, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if digest != scope.get("argv_sha256"):
            raise PermissionError("approved argv does not match its recorded digest")
        # Single-use: consume the approval before the git command runs, so a
        # replay of the same id fails even while it still reads APPROVED.
        self._approval(
            approval_id,
            project_id=project_id,
            action=PermissionAction.RUN_COMMAND,
            scope=scope,
        )
        repo_root = self._target(grant, str(scope.get("cwd", ".")), must_exist=True)
        self._validate_git_write(argv, repo_root)
        effective_argv = [
            argv[0],
            "-c", "user.email=amca@local",
            "-c", "user.name=AMCA Local",
            *argv[1:],
        ]
        result = self._run_git_sync(effective_argv, repo_root, timeout_seconds=timeout_seconds)
        self.store.record_event(
            project_id=project_id, grant_id=grant_id,
            action="git_write", target=str(scope.get("cwd", ".")),
            outcome="completed" if result["exit_code"] == 0 else "failed",
            detail={"argv": argv, "exit_code": result["exit_code"]},
            actor="tool_router",
        )
        return {**result, "argv": argv}

    # H2C.2I Phase A: persistent command execution (survives restart)
    def _init_run_store(self) -> None:
        import sqlite3 as _sq
        db = _sq.connect(str(self._run_db_path))
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_command_runs ("
                " run_id TEXT PRIMARY KEY, project_id TEXT, grant_id TEXT,"
                " approval_id TEXT, argv_json TEXT, status TEXT,"
                " exit_code INTEGER, stdout TEXT, stderr TEXT,"
                " elapsed_seconds REAL, truncated INTEGER,"
                " started_at TEXT, finished_at TEXT)"
            )
            db.commit()
        finally:
            db.close()
        # Run-records marked running by a previous process can never finish:
        # their subprocess died with the old backend.  Fail closed.
        try:
            db = _sq.connect(str(self._run_db_path))
            try:
                db.execute(
                    "UPDATE local_command_runs SET status = 'failed',"
                    " stderr = 'backend restarted before completion',"
                    " finished_at = ? WHERE status = 'running'",
                    (datetime.now(UTC).isoformat(),),
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    def _persist_run(self, entry: dict[str, Any], project_id: str = "", grant_id: str = "") -> None:
        import sqlite3 as _sq
        try:
            db = _sq.connect(str(self._run_db_path))
            try:
                db.execute(
                    "INSERT OR REPLACE INTO local_command_runs "
                    "(run_id, project_id, grant_id, approval_id, argv_json, status, exit_code, stdout, stderr, elapsed_seconds, truncated, started_at, finished_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry["run_id"], project_id or str(entry.get("project_id", "")),
                     grant_id or str(entry.get("grant_id", "")),
                     entry.get("approval_id", ""), json.dumps(entry.get("argv", [])),
                     entry.get("status", "running"), entry.get("exit_code"),
                     entry.get("stdout", ""), entry.get("stderr", ""),
                     entry.get("elapsed_seconds"), 1 if entry.get("truncated") else 0,
                     entry.get("started_at", ""), entry.get("finished_at", "")))
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    def _load_persisted_runs(self) -> None:
        import sqlite3 as _sq
        try:
            db = _sq.connect(str(self._run_db_path))
            try:
                db.row_factory = _sq.Row
                cur = db.execute("SELECT * FROM local_command_runs")
                for row in cur.fetchall():
                    rec = dict(row)
                    rec["argv"] = json.loads(rec.pop("argv_json", "[]"))
                    rec["truncated"] = bool(rec.get("truncated"))
                    if rec.get("status") == "running":
                        # backend restarted while this run was live: process gone
                        rec["status"] = "interrupted"
                        rec["error"] = "backend restarted while run was active"
                    self.command_runs[rec["run_id"]] = rec
            finally:
                db.close()
        except Exception:
            pass

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """User cancellation: terminate the subprocess, mark CANCELLED."""
        with self._command_lock:
            entry = self.command_runs.get(run_id)
        if entry is None or entry.get("status") != "running":
            raise ValueError("run is not in a cancellable state")
        self._cancel_requested.add(run_id)
        entry["status"] = "cancelled"
        entry["finished_at"] = datetime.now(UTC).isoformat()
        proc = self._active_processes.pop(run_id, None)
        killed = False
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=10)
                killed = True
            except (OSError, subprocess.TimeoutExpired):
                killed = False
        entry["cancelled"] = True
        entry["process_killed"] = killed
        self._persist_run(dict(entry))
        self.store.record_event(
            project_id=str(entry.get("project_id", "")),
            grant_id=str(entry.get("grant_id", "")),
            action="command_cancelled",
            target=str((entry.get("argv") or [""])[0]),
            outcome="cancelled",
            detail={"run_id": run_id, "process_killed": killed},
            actor="desktop_user",
        )
        return {"run_id": run_id, "status": "cancelled", "process_killed": killed}

    def command_run_snapshot(self, run_id: str) -> dict[str, Any]:

        with self._command_lock:
            record = self.command_runs.get(run_id)
        if record is None:
            raise ValueError("unknown command run id")
        return dict(record)

    @staticmethod
    def _validate_inspection_argv(argv: Sequence[str]) -> list[str]:
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
            raise ValueError("argv must be an array of strings")
        args = [str(item) for item in argv]
        if not 1 <= len(args) <= 16 or any(
            not item or len(item) > 500 or "\x00" in item for item in args
        ):
            raise ValueError("argv is empty or exceeds inspection-command bounds")
        if args[0] != Path(args[0]).name or any(separator in args[0] for separator in ("/", "\\")):
            raise PermissionError("inspection commands must use an allowlisted executable name")
        executable = Path(args[0]).stem.casefold()
        if executable == "whoami" and len(args) == 1:
            return args
        safe_git_forms = {
            ("--version",),
            ("status", "--short"),
            ("status", "--porcelain"),
            ("status", "--porcelain", "--branch"),
            ("branch", "--show-current"),
            ("rev-parse", "--is-inside-work-tree"),
            ("rev-parse", "--show-toplevel"),
            ("ls-files",),
            ("ls-files", "--others", "--exclude-standard"),
            ("diff", "--stat"),
            ("diff", "--name-only"),
            ("diff", "--cached", "--stat"),
            ("diff", "--cached", "--name-only"),
            ("log", "--oneline", "--max-count=20"),
            ("worktree", "list"),
        }
        if executable == "git" and tuple(args[1:]) in safe_git_forms:
            return args
        raise PermissionError(
            "command execution is inspection-only; this argv is outside the bounded allowlist"
        )

    @staticmethod
    def _safe_command_environment() -> dict[str, str]:
        allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
        environment = {
            key: value for key, value in os.environ.items() if key.upper() in allowed
        }
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "NUL" if os.name == "nt" else "/dev/null",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return environment

    @staticmethod
    def _executable_identity(executable: Path) -> dict[str, Any]:
        metadata = executable.stat()
        if not executable.is_file() or metadata.st_size > 64 * 1024 * 1024:
            raise PermissionError("inspection executable is not a bounded regular file")
        digest = hashlib.sha256()
        with executable.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        return {
            "executable": str(executable.resolve(strict=True)),
            "executable_sha256": digest.hexdigest(),
            "executable_size": metadata.st_size,
        }

    def _inspection_contract(
        self,
        *,
        project_id: str,
        grant_id: str,
        argv: Sequence[str],
        cwd: str,
        timeout_seconds: float,
        agent_id: str | None = None,
    ) -> tuple[LocalGrant, list[str], Path, dict[str, Any]]:
        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_COMMAND,
        )
        args = self._validate_inspection_argv(argv)
        cwd_path = self._target(grant, cwd, must_exist=True)
        if not cwd_path.is_dir():
            raise ValueError("command cwd must be a directory")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or not 1 <= timeout <= MAX_COMMAND_SECONDS:
            raise ValueError(f"command timeout must be between 1 and {MAX_COMMAND_SECONDS:g} seconds")
        environment = self._safe_command_environment()
        resolved = shutil.which(args[0], path=environment.get("PATH"))
        if not resolved:
            raise ValueError(f"allowlisted executable {args[0]!r} was not found")
        identity = self._executable_identity(Path(resolved))
        scope = {
            "grant_id": grant_id,
            "argv": args,
            "cwd": cwd,
            "timeout_seconds": timeout,
            "execution_scope": "inspection_only",
            "agent_id": agent_id,
            **identity,
        }
        return grant, args, cwd_path, scope

    def request_inspection_command(
        self,
        *,
        project_id: str,
        grant_id: str,
        argv: Sequence[str],
        cwd: str = ".",
        timeout_seconds: float = 15,
        reason: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        _, args, _, scope = self._inspection_contract(
            project_id=project_id,
            grant_id=grant_id,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            agent_id=agent_id,
        )
        with PermissionStore(self.store.database) as approvals:
            request = approvals.request(
                project_id=project_id,
                action=PermissionAction.RUN_COMMAND,
                scope=scope,
                reason=_clean_text(reason, name="reason", maximum=2_000),
                risk="high",
                requested_by="tool_router",
            )
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action="inspection_command_proposed",
            target=cwd,
            outcome="waiting_for_approval",
            detail={"approval_id": request.id, "argv": args},
            actor="tool_router",
        )
        return {"approval": request.to_dict(), "contract": scope, "execution_supported": True}

    def execute_inspection_command(
        self,
        *,
        project_id: str,
        grant_id: str,
        approval_id: str,
        argv: Sequence[str],
        cwd: str = ".",
        timeout_seconds: float = 15,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        _, args, cwd_path, scope = self._inspection_contract(
            project_id=project_id,
            grant_id=grant_id,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            agent_id=agent_id,
        )
        self._approval(
            approval_id,
            project_id=project_id,
            action=PermissionAction.RUN_COMMAND,
            scope=scope,
        )
        effective_args = [scope["executable"], *args[1:]]
        if Path(args[0]).stem.casefold() == "git":
            effective_args = [
                scope["executable"],
                "-c", "core.pager=cat",
                "-c", "pager.diff=false",
                "-c", "diff.external=",
                *args[1:],
            ]
        started = time.monotonic()
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                effective_args,
                cwd=cwd_path,
                env=self._safe_command_environment(),
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=float(timeout_seconds),
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as error:
            self.store.record_event(
                project_id=project_id,
                grant_id=grant_id,
                action="inspection_command",
                target=cwd,
                outcome="failed",
                detail={"error_type": "TimeoutExpired", "argv": args},
                actor="tool_router",
            )
            raise TimeoutError("inspection command exceeded its approved timeout") from error
        stdout = completed.stdout[:MAX_COMMAND_OUTPUT_BYTES]
        stderr = completed.stderr[:MAX_COMMAND_OUTPUT_BYTES]
        result = {
            "argv": args,
            "cwd": cwd,
            "exit_code": completed.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "stdout_truncated": len(completed.stdout) > len(stdout),
            "stderr_truncated": len(completed.stderr) > len(stderr),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "execution_scope": "inspection_only",
        }
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action="inspection_command",
            target=cwd,
            outcome="succeeded",
            detail={"argv": args, "exit_code": completed.returncode, "elapsed_seconds": result["elapsed_seconds"]},
            actor="tool_router",
        )
        return result

    def propose_filesystem_operation(
        self,
        *,
        project_id: str,
        grant_id: str,
        action: PermissionAction | str,
        capability: FileCapability | str,
        source: str,
        reason: str,
        destination: str | None = None,
        overwrite: bool = False,
        trash_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_action = PermissionAction(action)
        resolved_capability = FileCapability(capability)
        allowed_pairs = {
            PermissionAction.LOCAL_FILE_MODIFY: {
                FileCapability.CREATE,
                FileCapability.WRITE,
                FileCapability.APPEND,
                FileCapability.COPY,
            },
            # Restore is a move of an existing object, so it rides the move
            # approval rather than inventing an un-gated one.
            PermissionAction.LOCAL_FILE_MOVE: {
                FileCapability.MOVE,
                FileCapability.RESTORE,
            },
            PermissionAction.LOCAL_FILE_TRASH: {FileCapability.TRASH},
        }
        if resolved_action not in allowed_pairs or resolved_capability not in allowed_pairs[resolved_action]:
            raise ValueError("approval action does not match the filesystem capability")
        grant = self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=resolved_capability,
        )
        must_exist = resolved_capability is not FileCapability.CREATE
        if resolved_capability is FileCapability.RESTORE:
            record = self.store.trash_record(
                _clean_text(trash_id, name="trash_id", maximum=128)
            )
            if (
                record is None
                or record["project_id"] != project_id
                or record["grant_id"] != grant_id
            ):
                raise ValueError("trash record was not found for this grant")
            source = str(record["trash_path"])
            destination = str(record["original_relative_path"])
            self._target(grant, destination, must_exist=False)
        else:
            self._target(grant, source, must_exist=must_exist)
            if destination is not None:
                self._target(grant, destination, must_exist=False)
        scope = self.request_scope(
            action=resolved_action,
            grant_id=grant_id,
            source=source,
            destination=destination,
            overwrite=overwrite,
            trash_id=trash_id,
        )
        with PermissionStore(self.store.database) as approvals:
            request = approvals.request(
                project_id=project_id,
                action=resolved_action,
                scope=scope,
                reason=_clean_text(reason, name="reason", maximum=2_000),
                risk="high" if resolved_action is not PermissionAction.LOCAL_FILE_MODIFY else "medium",
                requested_by="permission_agent",
            )
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action=f"{resolved_action.value}_proposed",
            target=source,
            outcome="waiting_for_approval",
            detail={"approval_id": request.id, "capability": resolved_capability.value},
            actor="permission_agent",
        )
        return {"approval": request.to_dict()}

    def propose_application(
        self, *, project_id: str, grant_id: str, application: str, task: str, reason: str
    ) -> dict[str, Any]:
        self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_APPLICATION,
        )
        resolution = _application_resolution(application)
        scope = {
            "grant_id": grant_id,
            "application": resolution["display_name"],
            "application_key": resolution["key"],
            "executable": resolution["executable"],
            "window_title_substrings": list(
                GOVERNED_APPLICATIONS[resolution["key"]]["window_title_substrings"]
            ),
            "task": _clean_text(task, name="task", maximum=8_000),
            "executor": "governed_application_v1",
        }
        with PermissionStore(self.store.database) as approvals:
            request = approvals.request(
                project_id=project_id,
                action=PermissionAction.CONTROL_APPLICATION,
                scope=scope,
                reason=_clean_text(reason, name="reason", maximum=2_000),
                risk="high",
                requested_by="tool_router",
            )
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action="application_control_proposed",
            target=scope["application"],
            outcome="waiting_for_approval",
            detail={"approval_id": request.id, "executable": resolution["executable"]},
            actor="tool_router",
        )
        return {"approval": request.to_dict(), "execution_supported": True}

    # CLOSURE-05 Phase B: governed application launch / inspect / terminate.
    def _approved_application(
        self,
        *,
        project_id: str,
        approval_id: str,
        application: str,
        allow_consumed: bool = False,
    ) -> dict[str, Any]:
        """Validate a CONTROL_APPLICATION approval for this exact application.

        ``allow_consumed`` is set for inspect and terminate: those re-validate
        the same approval against the live session it started, so an already
        spent record is still the right authority there.  A launch does not
        allow it, which is what makes the approval single-use.
        """
        with PermissionStore(self.store.database) as approvals:
            record = approvals.get(approval_id)
        if record is None or record.project_id != project_id:
            raise PermissionError("unknown application approval")
        if record.action != PermissionAction.CONTROL_APPLICATION:
            raise PermissionError("approval is not a CONTROL_APPLICATION approval")
        allowed = (
            (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED)
            if allow_consumed
            else (ApprovalStatus.APPROVED,)
        )
        if ApprovalStatus(record.status) not in allowed:
            raise PermissionError("approval is not in the approved state")
        scope = dict(record.scope or {})
        if scope.get("executor") != "governed_application_v1":
            raise PermissionError("approval was not proposed for governed execution")
        requested = _application_resolution(application)
        if scope.get("application_key") != requested["key"] or scope.get(
            "executable"
        ) != requested["executable"]:
            raise PermissionError("approved application does not match the request")
        return scope

    def execute_application(
        self,
        *,
        project_id: str,
        grant_id: str,
        approval_id: str,
        application: str,
        operation: str,
        task: str = "",
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Launch, inspect, or terminate a governed application.

        The approval binds the exact executable; launch starts the real
        process (one instance per approval), inspect reports PID, window
        presence, and main-window title without stealing focus, terminate
        closes the launched instance.  Each approval is single-use: it is
        consumed on launch and cannot launch a second time.
        """
        self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_APPLICATION,
        )
        if operation not in _APPLICATION_OPS:
            raise ValueError("operation must be one of launch|inspect|terminate")
        scope = self._approved_application(
            project_id=project_id,
            approval_id=approval_id,
            application=application,
            allow_consumed=operation != "launch",
        )
        executable = scope["executable"]
        task_text = str(scope.get("task", ""))

        if operation == "launch":
            with self._command_lock:
                session = self._application_sessions.get((approval_id, executable))
            if session is not None and session["process"].poll() is None:
                raise ValueError("approval already has a running application session")
            if not task_text:
                raise ValueError("approved task text is empty; refusing blind launch")
            creation_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            # Single-use: every check has passed, so spend the approval now and
            # a replay of this id cannot start a second instance.
            self._approval(
                approval_id,
                project_id=project_id,
                action=PermissionAction.CONTROL_APPLICATION,
                scope=scope,
            )
            process = subprocess.Popen(  # noqa: S603 - exact governed executable
                [executable],
                cwd=str(self.state_root),
                env=self._safe_command_environment(),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            session = {
                "process": process,
                "pid": process.pid,
                "launched_at": _now(),
                "terminated": False,
            }
            with self._command_lock:
                self._application_sessions[(approval_id, executable)] = session
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="application_controlled", target=executable,
                outcome="launched",
                detail={"approval_id": approval_id, "pid": process.pid},
                actor="tool_router",
            )
            return {
                "operation": "launch",
                "application": scope["application"],
                "executable": executable,
                "pid": process.pid,
                "approval_id": approval_id,
                "status": "running",
            }

        session = self._application_sessions.get((approval_id, executable))
        if session is None:
            raise ValueError("no application session for this approval; launch first")
        process: subprocess.Popen[bytes] = session["process"]

        if operation == "inspect":
            alive = process.poll() is None
            window_title = ""
            windows: list[dict[str, Any]] = []
            if alive and os.name == "nt":
                window_title = self._probe_window_title(process.pid) or ""
                windows = self._probe_governed_windows(
                    scope.get("window_title_substrings", ())
                )
            result = {
                "operation": "inspect",
                "application": scope["application"],
                "pid": session["pid"],
                "alive": alive,
                "exit_code": None if alive else process.returncode,
                "window_title": window_title,
                "windows": windows,
                # Windows 11 note: packaged apps (e.g. modern Notepad) host
                # their windows in a separate single-instance process, so a
                # governed window may exist that this PID does not own.
                "window_ownership_note": (
                    "windows list is governed-application-scoped; host pid may "
                    "differ for packaged apps"
                    if windows
                    else ""
                ),
                "launched_at": session["launched_at"],
            }
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="application_controlled", target=executable,
                outcome="inspected",
                detail={"approval_id": approval_id, "pid": session["pid"], "alive": alive},
                actor="tool_router",
            )
            return result

        # terminate
        session["terminated"] = True
        if process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            except OSError as error:
                raise ValueError(f"failed to terminate application: {error}") from error
        self._application_sessions.pop((approval_id, executable), None)
        self.store.record_event(
            project_id=project_id, grant_id=grant_id,
            action="application_controlled", target=executable,
            outcome="terminated",
            detail={"approval_id": approval_id, "pid": session["pid"],
                    "exit_code": process.returncode},
            actor="tool_router",
        )
        return {
            "operation": "terminate",
            "application": scope["application"],
            "pid": session["pid"],
            "exit_code": process.returncode,
            "status": "terminated",
        }

    @staticmethod
    def _probe_governed_windows(
        title_substrings: Sequence[str], *, pid: int | None = None
    ) -> list[dict[str, Any]]:
        """List visible top-level windows whose titles match the governed
        application (and optionally belong to a specific PID)."""
        if os.name != "nt":
            return []
        try:  # pragma: no cover - exercised by the physical proof only
            import ctypes

            user32 = ctypes.windll.user32
            get_window_thread_process_id = user32.GetWindowThreadProcessId
            is_window_visible = user32.IsWindowVisible
            get_window_text_length = user32.GetWindowTextLengthW
            get_window_text = user32.GetWindowTextW
            needles = tuple(s.casefold() for s in title_substrings)
            found: list[dict[str, Any]] = []

            def _callback(handle: int, _lparam: int) -> bool:
                window_pid = ctypes.c_ulong(0)
                get_window_thread_process_id(handle, ctypes.byref(window_pid))
                if pid is not None and window_pid.value != pid:
                    return True
                if not is_window_visible(handle):
                    return True
                length = get_window_text_length(handle)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                get_window_text(handle, buffer, length + 1)
                title = buffer.value
                if needles and not any(n in title.casefold() for n in needles):
                    return True
                found.append({"pid": window_pid.value, "title": title})
                return True

            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )
            enum_windows = user32.EnumWindows
            enum_windows(callback_type(_callback), None)
            return found
        except Exception:
            return []

    @staticmethod
    def _probe_window_title(pid: int) -> str:
        """Best-effort main-window title probe for a PID (Windows)."""
        windows = LocalToolExecutor._probe_governed_windows((), pid=pid)
        return windows[0]["title"] if windows else ""

    # CLOSURE-05 Phase C: governed browser runtime (loopback-only navigation).
    def propose_browser_navigation(
        self,
        *,
        project_id: str,
        grant_id: str,
        browser: str,
        url: str,
        task: str,
        reason: str,
    ) -> dict[str, Any]:
        """Propose a governed browser navigation against a loopback URL."""
        self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_APPLICATION,
        )
        _key, resolution = _resolve_governed_browser(browser)
        normalized_url = _normalize_loopback_browser_url(url)
        scope = {
            "grant_id": grant_id,
            "browser": resolution["display_name"],
            "browser_key": resolution["key"],
            "executable": resolution["executable"],
            "url": normalized_url,
            "task": _clean_text(task, name="task", maximum=8_000),
            "executor": "governed_browser_v1",
        }
        with PermissionStore(self.store.database) as approvals:
            request = approvals.request(
                project_id=project_id,
                action=PermissionAction.CONTROL_APPLICATION,
                scope=scope,
                reason=_clean_text(reason, name="reason", maximum=2_000),
                risk="high",
                requested_by="tool_router",
            )
        self.store.record_event(
            project_id=project_id,
            grant_id=grant_id,
            action="browser_navigation_proposed",
            target=normalized_url,
            outcome="waiting_for_approval",
            detail={"approval_id": request.id, "browser": resolution["display_name"]},
            actor="tool_router",
        )
        return {"approval": request.to_dict(), "execution_supported": True}

    def _approved_browser(self, *, project_id: str, approval_id: str) -> dict[str, Any]:
        with PermissionStore(self.store.database) as approvals:
            record = approvals.get(approval_id)
        if record is None or record.project_id != project_id:
            raise PermissionError("unknown browser approval")
        if record.action != PermissionAction.CONTROL_APPLICATION:
            raise PermissionError("approval is not a CONTROL_APPLICATION approval")
        if ApprovalStatus(record.status) != ApprovalStatus.APPROVED:
            raise PermissionError("approval is not in the approved state")
        scope = dict(record.scope or {})
        if scope.get("executor") != "governed_browser_v1":
            raise PermissionError("approval was not proposed for governed browser execution")
        # Re-validate at execution time: policy must still hold.
        _normalize_loopback_browser_url(str(scope.get("url", "")))
        return scope

    def execute_browser(
        self,
        *,
        project_id: str,
        grant_id: str,
        approval_id: str,
        operation: str,
    ) -> dict[str, Any]:
        """Open, inspect, or close a governed loopback browser navigation.

        open starts the approved browser headless against the exact approved
        URL (isolated user-data-dir under the state root) and captures the
        rendered DOM; inspect reports capture progress; read returns the full
        captured document; extract returns its title, readable text, headings
        and links; close terminates the browser instance.  One live session per
        approval.

        read and extract operate on the DOM already captured from the already
        approved URL.  They fetch nothing and cannot widen the approval, so a
        different page still needs a new approval.
        """
        self._active_grant(
            project_id=project_id,
            grant_id=grant_id,
            capability=FileCapability.PROPOSE_APPLICATION,
        )
        if operation not in {"open", "inspect", "close", "read", "extract"}:
            raise ValueError("operation must be one of open|inspect|close|read|extract")
        scope = self._approved_browser(project_id=project_id, approval_id=approval_id)
        executable = scope["executable"]
        url = scope["url"]
        session_key = (approval_id, executable)

        if operation == "open":
            with self._command_lock:
                existing = self._browser_sessions.get(session_key)
            if existing is not None:
                raise ValueError(
                    "approval already has a browser session; close it first"
                )
            profile_dir = (
                self.state_root / "browser-profiles"
                / hashlib.sha256(approval_id.encode("utf-8")).hexdigest()[:16]
            )
            profile_dir.mkdir(parents=True, exist_ok=True)
            # Chromium browsers can dump the DOM to stdout; Firefox cannot, so
            # it is launched for the reviewed page and the DOM capture is
            # skipped rather than passing an argument it would reject.
            dom_capable = bool(
                GOVERNED_BROWSERS.get(str(scope.get("browser_key") or ""), {}).get(
                    "supports_dom_dump", True
                )
            )
            if dom_capable:
                argv = [
                    executable,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--disable-extensions",
                    f"--user-data-dir={profile_dir}",
                    "--virtual-time-budget=2000",
                    "--dump-dom",
                    url,
                ]
            else:
                argv = [
                    executable,
                    "--headless",
                    "--new-instance",
                    f"--profile={profile_dir}",
                    url,
                ]
            creation_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            try:
                process = subprocess.Popen(  # noqa: S603 - exact governed browser
                    argv,
                    cwd=str(self.state_root),
                    env=self._safe_command_environment(),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
            except OSError as error:
                raise ValueError(f"failed to start governed browser: {error}") from error
            session: dict[str, Any] = {
                "process": process,
                "output": "",
                "status": "running",
                "exit_code": None,
                "started_at": _now(),
            }
            with self._command_lock:
                self._browser_sessions[session_key] = session

            def _reader() -> None:
                received = 0
                try:
                    assert process.stdout is not None
                    for line in iter(process.stdout.readline, b""):
                        received += len(line)
                        if received > MAX_BROWSER_DOM_BYTES:
                            session["truncated"] = True
                            process.kill()
                            break
                        session["output"] += line.decode("utf-8", errors="replace")
                    session["exit_code"] = process.wait(timeout=15)
                except Exception as exc:
                    session["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    if session["status"] == "running":
                        session["status"] = (
                            "completed" if session.get("exit_code") == 0 else "failed"
                        )

            threading.Thread(target=_reader, daemon=True).start()
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="browser_navigation", target=url,
                outcome="opened",
                detail={"approval_id": approval_id, "pid": process.pid},
                actor="tool_router",
            )
            return {
                "operation": "open",
                "browser": scope["browser"],
                "url": url,
                "pid": process.pid,
                "status": "running",
            }

        with self._command_lock:
            session = self._browser_sessions.get(session_key)
        if session is None:
            raise ValueError("no browser session for this approval; open first")

        if operation == "inspect":
            result = {
                "operation": "inspect",
                "url": url,
                "status": session["status"],
                "exit_code": session.get("exit_code"),
                "dom_bytes": len(session["output"]),
                "dom_preview": session["output"][:512],
                "started_at": session["started_at"],
            }
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="browser_navigation", target=url,
                outcome="inspected",
                detail={"approval_id": approval_id, "status": session["status"]},
                actor="tool_router",
            )
            return result

        if operation in {"read", "extract"}:
            # Both operate on the DOM already captured from the already
            # approved URL.  Nothing is fetched and the approval cannot be
            # widened, so a different page still requires a new approval.
            from .browser_content import extract_page

            if not session["output"]:
                return {
                    "operation": operation,
                    "url": url,
                    "status": session["status"],
                    "available": False,
                    "reason": (
                        "no page content was captured for this approval; the "
                        "browser may still be loading, or this browser does not "
                        "support DOM capture"
                    ),
                }
            page = extract_page(session["output"])
            self.store.record_event(
                project_id=project_id, grant_id=grant_id,
                action="browser_navigation", target=url,
                outcome="read" if operation == "read" else "extracted",
                detail={
                    "approval_id": approval_id,
                    "dom_bytes": len(session["output"]),
                    "text_chars": page["text_chars"],
                },
                actor="tool_router",
            )
            if operation == "read":
                return {
                    "operation": "read",
                    "url": url,
                    "title": page["title"],
                    "dom": session["output"],
                    "dom_bytes": len(session["output"]),
                    "truncated": bool(session.get("truncated")),
                }
            return {
                "operation": "extract",
                "url": url,
                "available": True,
                "title": page["title"],
                "text": page["text"],
                "text_chars": page["text_chars"],
                "text_truncated": page["text_truncated"],
                "headings": page["headings"],
                "links": page["links"],
                "link_count": page["link_count"],
            }

        # close
        was_running = session["status"] == "running"
        if was_running:
            try:
                session["process"].kill()
            except OSError:
                pass
            session["status"] = "terminated"
        else:
            session["status"] = "closed"
        dom_bytes = len(session["output"])
        with self._command_lock:
            self._browser_sessions.pop(session_key, None)
        self.store.record_event(
            project_id=project_id, grant_id=grant_id,
            action="browser_navigation", target=url,
            outcome="closed",
            detail={"approval_id": approval_id, "terminated_running": was_running},
            actor="tool_router",
        )
        return {
            "operation": "close",
            "url": url,
            "terminated_running": was_running,
            "status": session["status"],
            "dom_bytes": dom_bytes,
        }


__all__ = [
    "FileCapability",
    "LocalGrant",
    "LocalPermissionStore",
    "LocalToolEvent",
    "LocalToolExecutor",
    "PermissionProfile",
    "PROFILE_CAPABILITIES",
]
