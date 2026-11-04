"""AMCA MCP servers registry — LOCAL stdio transports only (facilities panel).

Design constraint (security): v1 supports exactly one transport, `stdio`
(local process spawn). Remote transports (HTTP/SSE) are an unreviewed
network surface and are refused at save time with an honest error — the UI
states this instead of offering them. "Connect" runs a bounded
`initialize` + `tools/list` JSON-RPC probe over stdio with a hard timeout;
discovered tools are LISTED only and never executed by this module —
execution stays behind the existing Tool Fabric approval flow.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from . import PROVIDER_REQUEST_TIMEOUT_SECONDS
from .studio_memory import _clean_text, _now

MCP_PROBE_TIMEOUT_SECONDS = 20.0
MCP_PROTOCOL_VERSION = "2024-11-05"
# A flooding binary must not bloat backend RAM: give up past 4 MiB of probe
# output without a tools/list answer (a real tools list is kilobytes).
MCP_PROBE_MAX_BYTES = 4 * 1024 * 1024


class McpServerStore:
    """Manages local MCP server definitions in SQLite."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS mcp_servers (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    transport TEXT NOT NULL DEFAULT 'stdio',
                    command TEXT NOT NULL,
                    args_json TEXT NOT NULL DEFAULT '[]',
                    env_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_tools_json TEXT,
                    last_probe_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
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
            "server_id": row["server_id"],
            "name": row["name"],
            "transport": row["transport"],
            "command": row["command"],
            "args": json.loads(row["args_json"] or "[]"),
            "env": json.loads(row["env_json"] or "{}"),
            "enabled": bool(row["enabled"]),
            "last_tools": json.loads(row["last_tools_json"]) if row["last_tools_json"] else None,
            "last_probe_at": row["last_probe_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_server(
        self,
        *,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: str = "stdio",
        enabled: bool = True,
        server_id: str | None = None,
    ) -> dict[str, Any]:
        """Saves or updates a local MCP server definition."""
        name = _clean_text(name, "name", 128, required=True)
        transport = _clean_text(transport, "transport", 16) or "stdio"
        if transport != "stdio":
            raise ValueError("Only local 'stdio' MCP servers are supported in this release (remote transports are an unreviewed network surface)")
        command = _clean_text(command, "command", 512, required=True)
        args = [str(a) for a in (args or [])][:32]
        env = {str(k): str(v) for k, v in (env or {}).items()}

        now = _now()
        target_id = server_id or f"mcp-{uuid4().hex[:12]}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO mcp_servers (
                    server_id, name, transport, command, args_json, env_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    name=excluded.name,
                    transport=excluded.transport,
                    command=excluded.command,
                    args_json=excluded.args_json,
                    env_json=excluded.env_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at""",
                (target_id, name, transport, command, json.dumps(args), json.dumps(env), int(enabled), now, now),
            )
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?", (target_id,)
            ).fetchone()
            return self._record(row)

    def list_servers(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM mcp_servers ORDER BY name ASC"
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)
            ).fetchone()
            return self._record(row) if row else None

    def delete_server(self, server_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM mcp_servers WHERE server_id = ?", (server_id,)
            )
            return cursor.rowcount > 0

    def set_enabled(self, server_id: str, *, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE mcp_servers SET enabled = ?, updated_at = ? WHERE server_id = ?",
                (int(bool(enabled)), _now(), server_id),
            )
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)
            ).fetchone()
            return self._record(row) if row else None

    def probe_server(self, server_id: str) -> dict[str, Any]:
        """Connect: spawn the local server, run initialize + tools/list, record.

        Bounded: one attempt, MCP_PROBE_TIMEOUT_SECONDS, no retries. The
        server process is always terminated afterwards. Returns the stored
        record with fresh last_tools / last_error.
        """
        record = self.get_server(server_id)
        if record is None:
            raise KeyError(f"Unknown MCP server: {server_id}")
        if not record["enabled"]:
            raise ValueError("MCP server is disabled")

        tools: list[dict[str, Any]] | None = None
        error: str | None = None
        try:
            tools = _probe_stdio_server(
                record["command"], record["args"], record["env"]
            )
        except Exception as exc:  # probe must never raise: record the failure
            error = f"{type(exc).__name__}: {exc}"[:500]

        with self._connect() as connection:
            connection.execute(
                """UPDATE mcp_servers
                   SET last_tools_json = ?, last_probe_at = ?, last_error = ?, updated_at = ?
                   WHERE server_id = ?""",
                (
                    json.dumps(tools) if tools is not None else None,
                    _now(),
                    error,
                    _now(),
                    server_id,
                ),
            )
        return self.get_server(server_id) or record


def _rpc_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body


def _probe_stdio_server(command: str, args: list[str], env: dict[str, str]) -> list[dict[str, Any]]:
    """One-shot MCP initialize + tools/list over stdio. Raises on any failure.

    Cross-platform bounded read: a background thread pumps stdout into a
    queue in small bounded chunks (select() on pipes is POSIX-only and
    BufferedReader.read(n) blocks to n/EOF, either of which hangs the
    request thread against a live server). Total accumulation is capped at
    MCP_PROBE_MAX_BYTES so a flooding binary cannot bloat backend RAM.
    """
    import os
    import queue
    import threading
    import time

    child_env = dict(os.environ)
    child_env.update(env)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_env,
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"Could not launch MCP server: {exc}") from exc
    assert proc.stdin is not None and proc.stdout is not None
    try:
        proc.stdin.write(
            _rpc_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "AMCA-Studio", "version": "1.1.21"},
                    },
                }
            )
        )
        proc.stdin.write(_rpc_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        proc.stdin.write(
            _rpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        )
        proc.stdin.flush()

        chunks: queue.Queue[bytes | None] = queue.Queue()
        stdout = proc.stdout

        def _pump() -> None:
            try:
                while True:
                    data = stdout.read1(65536) if hasattr(stdout, "read1") else stdout.read(65536)
                    if not data:
                        break
                    chunks.put(data)
            except Exception:
                pass
            finally:
                chunks.put(None)

        pump = threading.Thread(target=_pump, daemon=True)
        pump.start()

        end = time.monotonic() + MCP_PROBE_TIMEOUT_SECONDS
        buffer = b""
        total_bytes = 0
        response: dict[str, Any] | None = None
        # Read until we have the tools/list response (id 2) or time out.
        while time.monotonic() < end:
            try:
                item = chunks.get(timeout=max(0.0, end - time.monotonic()))
            except queue.Empty:
                break
            if item is None:
                break
            total_bytes += len(item)
            if total_bytes > MCP_PROBE_MAX_BYTES:
                raise TimeoutError("MCP server exceeded the probe output cap without answering tools/list")
            buffer += item
            # Parse framed messages; look for id == 2.
            while b"Content-Length:" in buffer:
                header_end = buffer.find(b"\r\n\r\n")
                if header_end < 0:
                    break
                try:
                    length = int(buffer[:header_end].split(b"Content-Length:")[1].split(b"\r\n")[0].strip())
                except ValueError:
                    break
                start = header_end + 4
                if len(buffer) < start + length:
                    break
                body = json.loads(buffer[start:start + length].decode("utf-8"))
                buffer = buffer[start + length:]
                if isinstance(body, dict) and body.get("id") == 2:
                    response = body
                    break
            if response is not None:
                break

        if response is None:
            raise TimeoutError("MCP server did not answer tools/list within the probe timeout")
        if response.get("error"):
            raise RuntimeError(f"MCP tools/list error: {response['error']}")
        result = response.get("result") or {}
        listed = result.get("tools") or []
        return [
            {"name": str(t.get("name", "")), "description": str(t.get("description", ""))}
            for t in listed
            if isinstance(t, dict) and t.get("name")
        ]
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
