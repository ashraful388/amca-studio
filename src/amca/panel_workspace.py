"""ZCode-style panel workspaces: live file explorer + operator terminal.

Round 24 (2026-10-31, owner order): the right-panel Files/Browser/Terminal/Sites
facilities mirror the ZCode screenshots — a live file tree of the project, a
real PowerShell session, and a site preview surface.

Two operator-facing surfaces live here:

- ``FileExplorer``: live disk listing of ONE project's workspace root
  (``<data>/workspaces/<project>/`` as owned by ``WorkspaceStore``). Read-only,
  path-contained, size-capped. Any file type: text returns decoded text
  (bounded), binary returns base64 + media type so the panel can render or
  download it. Internal workspace bookkeeping (``.history/`` trees and
  ``.*.versions.json`` sidecars) stays hidden.
- ``TerminalManager``: real PowerShell sessions owned by the human operator.
  This is deliberately DISTINCT from the agent's governed command path
  (``propose_argv_command`` -> Tool Approval -> ``execute_approved_argv``),
  which is unchanged: an agent still cannot run anything without an explicit
  human approval. The terminal is the operator's own hands, like the terminal
  in any IDE. Sessions are in-memory only, bounded in count/buffer/idle, and
  killed on delete; every route sits behind the same loopback + session-token
  gate as the rest of the service.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from amca.workspace import WorkspaceStore

MAX_TREE_ENTRIES = 500
MAX_TEXT_PREVIEW_BYTES = 512 * 1024
MAX_READ_BYTES = 8 * 1024 * 1024
EXCLUDED_DIR_NAMES = {".history"}
SIDECAR_SUFFIX = ".versions.json"

MAX_SESSIONS_TOTAL = 8
MAX_SESSIONS_PER_PROJECT = 4
MAX_BUFFER_CHARS = 256 * 1024
MAX_PENDING_CHARS = 1024 * 1024
MAX_INPUT_CHARS = 8 * 1024
SESSION_IDLE_SECONDS = 2 * 60 * 60
POLL_SLEEP_SECONDS = 0.05

# Appended after every operator command so the manager can track the working
# directory even though a piped (non-tty) PowerShell prints no prompt. Each
# session carries a random nonce so output lines that merely START with
# "AMCA_CWD=" (echoed files, hostile build logs) cannot forge the marker.
_CWD_PREFIX = "AMCA_CWD_"
NEWLINE = chr(10)


class PanelWorkspaceError(ValueError):
    """Fail-closed error for explorer and terminal surfaces."""


class FileExplorer:
    """Read-only live listing of one project folder.

    The base root is resolved by the service: the project's real
    ``root_path`` folder when it exists, else the AMCA workspace. An
    explicit ``base_root`` (operator-typed folder) always wins. The
    explorer is read-only and path-contained UNDER whichever base root is
    active; the agent has no access to this surface either way.
    """

    def __init__(self, workspace_store: WorkspaceStore) -> None:
        self._workspace = workspace_store

    def _base_root(self, project_id: str, base_root: Path | None) -> Path:
        if base_root is not None:
            # Resolve so the containment prefix check cannot be defeated by
            # 8.3 aliases or differing spellings of the same folder.
            return Path(base_root).resolve()
        return self._workspace.project_root(project_id)

    def _contained_target(
        self, project_id: str, raw_path: str, base_root: Path | None = None
    ) -> Path:
        root = self._base_root(project_id, base_root)
        if not isinstance(raw_path, str) or not raw_path.strip():
            return root
        candidate = Path(raw_path.strip().replace("\\", "/"))
        if candidate.is_absolute() or candidate.drive or candidate.root:
            raise PanelWorkspaceError("explorer paths must be relative to the project")
        parts = [part for part in candidate.parts if part not in {".", ""}]
        if not parts or any(part == ".." for part in parts):
            raise PanelWorkspaceError("explorer path traversal is not allowed")
        if any(":" in part for part in parts):
            # Alternate data streams (file.txt:stream) are refused: they would
            # bypass the hidden-internal-records filter via a colon suffix.
            raise PanelWorkspaceError("explorer paths must not contain colons")
        target = (root / Path(*parts)).resolve()
        if not str(target).startswith(str(root)):
            raise PanelWorkspaceError("explorer path traversal is not allowed")
        return target

    @staticmethod
    def _is_internal(relative: Path) -> bool:
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
            return True
        return relative.name.endswith(SIDECAR_SUFFIX) or (
            relative.name.startswith(".") and relative.name.endswith(".versions.json")
        )

    def list_dir(
        self, project_id: str, raw_path: str, base_root: Path | None = None
    ) -> dict[str, Any]:
        target = self._contained_target(project_id, raw_path, base_root)
        root = self._base_root(project_id, base_root)
        if not target.exists():
            raise PanelWorkspaceError("explorer path not found")
        if not target.is_dir():
            raise PanelWorkspaceError("explorer path is not a directory")
        directories: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        try:
            children = sorted(target.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise PanelWorkspaceError(f"explorer listing failed: {exc}") from exc
        truncated = False
        for child in children:
            relative = child.relative_to(root)
            if self._is_internal(relative):
                continue
            if len(directories) + len(files) >= MAX_TREE_ENTRIES:
                truncated = True
                break
            try:
                stat = child.stat()
            except OSError:
                continue
            if child.is_dir():
                directories.append(
                    {
                        "name": child.name,
                        "type": "dir",
                        "bytes": 0,
                        "updated_at": stat.st_mtime,
                    }
                )
            elif child.is_file():
                files.append(
                    {
                        "name": child.name,
                        "type": "file",
                        "bytes": stat.st_size,
                        "updated_at": stat.st_mtime,
                    }
                )
        rel_posix = target.relative_to(root).as_posix()
        return {
            "root": str(root),
            "path": "" if rel_posix == "." else rel_posix,
            "entries": directories + files,
            "truncated": truncated,
        }

    def read_file(
        self, project_id: str, raw_path: str, base_root: Path | None = None
    ) -> dict[str, Any]:
        target = self._contained_target(project_id, raw_path, base_root)
        if not target.exists() or not target.is_file():
            raise PanelWorkspaceError("explorer file not found")
        import stat as stat_module

        if not stat_module.S_ISREG(target.stat().st_mode):
            # Named pipes/devices report st_size == 0 and would block forever.
            raise PanelWorkspaceError("explorer reads require a regular file")
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            raise PanelWorkspaceError(
                "explorer file exceeds the read limit (8 MB); open it from disk"
            )
        # Bounded read: a file growing between stat and read must not be
        # slurped unbounded into memory.
        with target.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES + 1)
        if len(data) > MAX_READ_BYTES:
            raise PanelWorkspaceError(
                "explorer file exceeds the read limit (8 MB); open it from disk"
            )
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        truncated = size > len(data)
        try:
            content = data.decode("utf-8")
            if "\x00" in content:
                raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL byte")
            encoding = "text"
            payload: str = content[:MAX_TEXT_PREVIEW_BYTES]
            truncated = truncated or size > MAX_TEXT_PREVIEW_BYTES
        except UnicodeDecodeError:
            encoding = "base64"
            payload = base64.b64encode(data).decode("ascii")
        return {
            "path": target.relative_to(self._base_root(project_id, base_root)).as_posix(),
            "media_type": media_type,
            "encoding": encoding,
            "content": payload,
            "bytes": size,
            "truncated": truncated,
        }


class _TerminalSession:
    __slots__ = (
        "session_id",
        "project_id",
        "root",
        "cwd_nonce",
        "proc",
        "cwd",
        "chunks",
        "offset",
        "pending",
        "alive",
        "generation",
        "deleted",
        "last_activity",
        "lock",
    )

    def __init__(self, session_id: str, project_id: str, cwd: str, root: str) -> None:
        self.session_id = session_id
        self.project_id = project_id
        self.root = root
        self.cwd_nonce = uuid.uuid4().hex[:12]
        self.proc: subprocess.Popen[Any] | None = None
        self.cwd = cwd
        self.chunks: list[str] = []
        self.offset = 0
        self.pending = ""
        self.alive = True
        self.generation = 0
        self.deleted = False
        self.last_activity = time.monotonic()
        self.lock = threading.Lock()


def _contained_under_root(candidate: str, root: str) -> bool:
    """True when an absolute path stays inside the workspace root."""
    try:
        resolved = Path(candidate).resolve()
        root_resolved = Path(root).resolve()
    except OSError:
        return False
    cand_fold = str(resolved).casefold()
    root_fold = str(root_resolved).casefold()
    return cand_fold == root_fold or cand_fold.startswith(root_fold + os.sep)


class TerminalManager:
    """Real PowerShell sessions for the operator's Terminal panel tab."""

    def __init__(self) -> None:
        self._sessions: dict[str, _TerminalSession] = {}
        self._lock = threading.Lock()
        self._reaper_stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    # -- creation -----------------------------------------------------------

    def create(self, project_id: str, cwd_root: Path) -> dict[str, Any]:
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS_TOTAL:
                raise PanelWorkspaceError("terminal session limit reached; close a tab")
            per_project = sum(
                1 for session in self._sessions.values() if session.project_id == project_id
            )
            if per_project >= MAX_SESSIONS_PER_PROJECT:
                raise PanelWorkspaceError("terminal session limit reached for this project")
            session_id = uuid.uuid4().hex
            session = _TerminalSession(session_id, project_id, str(cwd_root), str(cwd_root))
            self._sessions[session_id] = session
        try:
            self._spawn(session)
        except Exception:
            # A failed spawn must not burn a session slot (gate F4/S6:
            # repeated failures would brick terminal creation).
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return {
            "session_id": session_id,
            "project_id": project_id,
            "cwd": session.cwd,
            "alive": True,
        }

    def _spawn(self, session: _TerminalSession) -> None:
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed executable, operator-owned
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=session.cwd,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise PanelWorkspaceError(f"terminal spawn failed: {exc}") from exc
        session.proc = proc
        session.alive = True
        session.generation += 1
        reader = threading.Thread(target=self._read_loop, args=(session, session.generation), daemon=True)
        reader.start()
        # UTF-8 console + start location; sent through the same pipe the
        # operator's commands use, before anything the operator types. The
        # respawn cwd is confined to the project root: session.cwd is parsed
        # from output (nonce-gated) and must never move a respawn outside
        # the workspace even if a forged line got through.
        respawn_cwd = session.cwd
        root_fold = str(session.root).casefold()
        cwd_fold = str(respawn_cwd).casefold()
        if cwd_fold != root_fold and not cwd_fold.startswith(root_fold + os.sep):
            respawn_cwd = session.root
        if not Path(respawn_cwd).is_dir():
            respawn_cwd = session.root
        self._write_stdin(
            session,
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
            f"Set-Location -LiteralPath '{respawn_cwd.replace(chr(39), chr(39) * 2)}'\n"
            f'Write-Output ("{_CWD_PREFIX}{session.cwd_nonce}=" + (Get-Location).Path)\n',
        )

    # -- I/O ----------------------------------------------------------------

    @staticmethod
    def _write_stdin(session: _TerminalSession, data: str) -> None:
        proc = session.proc
        stdin = proc.stdin if proc is not None else None
        if stdin is None or proc.poll() is not None:
            raise PanelWorkspaceError("terminal session has ended")
        try:
            stdin.write(data.encode("utf-8"))
            stdin.flush()
        except OSError as exc:
            raise PanelWorkspaceError(f"terminal write failed: {exc}") from exc

    def send_command(self, session_id: str, line: str) -> dict[str, Any]:
        session = self._require(session_id)
        if not isinstance(line, str) or not line.strip():
            raise PanelWorkspaceError("terminal command is required")
        if len(line) > MAX_INPUT_CHARS:
            raise PanelWorkspaceError("terminal command exceeds the input limit")
        with session.lock:
            session.last_activity = time.monotonic()
            # Nonce-gated marker: only OUR appended line updates the cwd.
            marker = f'Write-Output ("{_CWD_PREFIX}{session.cwd_nonce}=" + (Get-Location).Path)'
            self._write_stdin(session, line + "\n" + marker + "\n")
        return {"sent": True}

    def send_break(self, session_id: str) -> dict[str, Any]:
        # CTRL_BREAK to a piped PowerShell permanently stops its stdin loop
        # (probed 2026-10-31: engine survives, REPL never reads again), so a
        # break restarts the session in place: kill the process TREE (which
        # is what actually stops a stuck command), respawn PowerShell at the
        # same working directory, and keep the scrollback + session id.
        # Membership is re-checked under _lock so a concurrent delete can
        # never leave a respawned orphan (gate F5).
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is None or session.deleted:
                raise PanelWorkspaceError("unknown terminal session")
            with session.lock:
                session.last_activity = time.monotonic()
                proc = session.proc
                if proc is not None and proc.poll() is None:
                    self._kill_tree(session)
                self._spawn(session)
        return {"sent": True, "restarted": True}

    def output(self, session_id: str, after: int) -> dict[str, Any]:
        session = self._require(session_id)
        with session.lock:
            session.last_activity = time.monotonic()
            alive = session.proc is not None and session.proc.poll() is None
            if after < 0 or after > session.offset:
                after = session.offset
            text = "".join(session.chunks)
            start = max(0, after - (session.offset - len(text)))
            tail = text[start:]
            cwd = session.cwd
            base = session.offset - len(text)
        return {
            "text": tail,
            "next_offset": base + len(text),
            "cwd": cwd,
            "alive": alive,
        }

    def delete(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(str(session_id), None)
            if session is None:
                raise PanelWorkspaceError("unknown terminal session")
            session.deleted = True
        with session.lock:
            self._kill_tree(session)
        return {"deleted": session_id}

    # -- internals ----------------------------------------------------------

    def _require(self, session_id: str) -> _TerminalSession:
        with self._lock:
            session = self._sessions.get(str(session_id))
        if session is None:
            raise PanelWorkspaceError("unknown terminal session")
        return session

    def _kill_tree(self, session: _TerminalSession) -> None:
        proc = session.proc
        if proc is None or proc.poll() is not None:
            return
        # Re-verify immediately before spawning taskkill: a recycled PID must
        # not make us force-kill an unrelated tree (gate S7; window is small).
        if proc.poll() is not None:
            return
        try:
            subprocess.run(  # noqa: S603 - fixed argv, tree-kill of our own child
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass

    def _read_loop(self, session: _TerminalSession, generation: int) -> None:
        import codecs

        proc = session.proc
        stdout = proc.stdout if proc is not None else None
        if stdout is None:
            return
        # os.read (not BufferedReader.read(n)): a piped BufferedReader.read
        # blocks until the FULL n bytes or EOF, which would hold small
        # outputs hostage. os.read returns whatever is available.
        fd = stdout.fileno()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            text = decoder.decode(chunk)
            with session.lock:
                if session.generation != generation:
                    return  # a break restarted this session; stale reader
                session.pending += text
                while NEWLINE in session.pending:
                    line, session.pending = session.pending.split(NEWLINE, 1)
                    self._ingest_line(session, line)
                if len(session.pending) > MAX_PENDING_CHARS:
                    # Newline-less flood: force-ingest so backend memory
                    # stays bounded (gate F12/S5).
                    self._ingest_line(session, session.pending)
                    session.pending = ""
            if proc.poll() is not None:
                break
        with session.lock:
            if session.generation != generation:
                return  # stale reader after a break restart
            if session.pending:
                self._ingest_line(session, session.pending)
                session.pending = ""
            session.alive = False

    @staticmethod
    def _ingest_line(session: _TerminalSession, line: str) -> None:
        stripped = line.rstrip("\r")
        # Nonce-gated cwd marker (gate S1/F13): only a line carrying THIS
        # session's nonce updates the cwd, and the candidate must stay
        # inside the project workspace root. Forged-looking lines render
        # as ordinary output instead of being silently swallowed.
        marker_prefix = _CWD_PREFIX + session.cwd_nonce + "="
        if stripped.startswith(marker_prefix):
            candidate = stripped[len(marker_prefix):].strip()
            if candidate and _contained_under_root(candidate, session.root):
                session.cwd = candidate
            return
        # Offset stays an ABSOLUTE counter (gate F3): evict oldest chunks
        # while over the cap but never reset the offset - a reset makes
        # next_offset go backwards and clients silently lose output.
        session.chunks.append(stripped + NEWLINE)
        session.offset += len(stripped) + 1
        while len(session.chunks) > 1:
            committed = sum(len(chunk) for chunk in session.chunks)
            if committed <= MAX_BUFFER_CHARS:
                break
            session.chunks.pop(0)

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(60):
            now = time.monotonic()
            stale: list[_TerminalSession] = []
            with self._lock:
                for session in list(self._sessions.values()):
                    if now - session.last_activity > SESSION_IDLE_SECONDS:
                        stale.append(self._sessions.pop(session.session_id, None) or session)
            for session in stale:
                self._kill_tree(session)

    def shutdown(self) -> None:
        self._reaper_stop.set()
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._kill_tree(session)


__all__ = [
    "FileExplorer",
    "PanelWorkspaceError",
    "TerminalManager",
]
