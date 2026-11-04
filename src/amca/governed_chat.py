"""Governed Chat Tool Adapter for AMCA Studio (Work Order 24A).

Bridges local-model Chat execution with AMCA's canonical governance chain:
model
-> tool request
-> Tool Router
-> Agent Governor
-> Permission Agent
-> exact-scope decision / approval
-> actual execution
-> tool result
-> model continuation.

Ensures local models receive governed tool capabilities under the active
Local PC profile (Restricted, Standard, Extended, Full Computer), reconciles
claims through the Anti-Fabrication Gate, and stamps executed tool provenance.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
from dataclasses import replace
from typing import Any, AsyncIterator, Mapping, Sequence

from .anti_fabrication import reconcile_anti_fabrication
from .artifacts import strip_artifact_blocks
from .artifacts import parse_artifact_blocks
from .local_tools import FileCapability, PermissionProfile, PROFILE_CAPABILITIES
from .models.base import (
    ModelAdapter,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    TokenUsage,
)
from .models.tool_protocol import (
    ToolCall,
    ToolResult,
    ToolResultState,
)
from .models.tool_capability_evidence import tool_catalog_drop
from .semantic import SEMANTIC_RESPONSE_INSTRUCTIONS


AMCA_TOOL_AWARENESS_SYSTEM = (
    "# AMCA Studio Environment & Permissioned Tools\n"
    "You are running inside AMCA Studio with permissioned, governed Local PC tools.\n"
    "You have access to governed filesystem and inspection tools subject to the active project permission profile and user security grants.\n"
    "Do NOT claim 'As an AI, I cannot access local files' or similar generic refusals when a file operation is requested.\n"
    "When a local file operation or command is needed, call the appropriate governed tool.\n"
    "If a file or tool operation is requested but unavailable or fails, explain the exact truthful reason (for example:\n"
    "- workspace root not configured\n"
    "- path outside allowed scope\n"
    "- grant required\n"
    "- operation blocked by Restricted profile\n"
    "- approval denied\n"
    "- tool unavailable).\n"
    "FAIL-CLOSED INTEGRITY RULE: NEVER claim that you read, wrote, moved, renamed, or deleted a file, or executed a command, or authored an artifact, unless the corresponding tool or artifact pipeline actually executed successfully in this run. If an operation was not executed, state that truthfully.\n"
    "For standard conversational prompts, instructions, questions, or text tasks that do not request file or command actions, reply directly to the user's prompt without tool calls and without mentioning workspace or filesystem status.\n"
    "CREATING FILES AS ARTIFACTS: when the user asks you to write, create, save, or draft a file, document, plan, report, or code deliverable, emit the FULL content in one artifact block:\n"
    "```artifact md title=<File name>\n"
    "<complete file content>\n"
    "```\n"
    "(use the right type: md, docx, csv, code, or text; one block per file). AMCA stores the block as a real artifact the user can open and download from the Artifacts panel - that IS how a file is delivered in this environment, and it does not require a filesystem tool. Do not merely describe a file or tell the user to save the text themselves when an artifact block delivers it.\n"
)


# Tool schemas for governed catalog
_GOVERNED_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "filesystem.read": {
        "type": "function",
        "name": "filesystem.read",
        "description": "Read content from a file within granted scope.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative path to read"}
            },
            "required": ["path"],
        },
    },
    "filesystem.list": {
        "type": "function",
        "name": "filesystem.list",
        "description": "List files and directories within granted scope.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative directory path"}
            },
        },
    },
    "filesystem.stat": {
        "type": "function",
        "name": "filesystem.stat",
        "description": "Get metadata (size, timestamps, kind) for a file or directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative path"}
            },
            "required": ["path"],
        },
    },
    "filesystem.search": {
        "type": "function",
        "name": "filesystem.search",
        "description": "Search file names or file text within granted scope.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to search"},
                "query": {"type": "string", "description": "Search text"}
            },
        },
    },
    "filesystem.write": {
        "type": "function",
        "name": "filesystem.write",
        "description": "Write or create a file with given text content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative file path to create/write"},
                "content": {"type": "string", "description": "File text content"},
                "overwrite": {"type": "boolean", "description": "Whether to overwrite if file exists"}
            },
            "required": ["path", "content"],
        },
    },
    "filesystem.append": {
        "type": "function",
        "name": "filesystem.append",
        "description": "Append text content to an existing file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative file path to append to"},
                "content": {"type": "string", "description": "Text content to append"}
            },
            "required": ["path", "content"],
        },
    },
    "filesystem.copy": {
        "type": "function",
        "name": "filesystem.copy",
        "description": "Copy a file within granted scope.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source file path"},
                "destination": {"type": "string", "description": "Destination file path"}
            },
            "required": ["path", "destination"],
        },
    },
    "filesystem.move": {
        "type": "function",
        "name": "filesystem.move",
        "description": "Move or rename a file within granted scope.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source file path"},
                "destination": {"type": "string", "description": "Destination file path"}
            },
            "required": ["path", "destination"],
        },
    },
    "filesystem.trash": {
        "type": "function",
        "name": "filesystem.trash",
        "description": "Move a file to recoverable trash.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative file path to trash"}
            },
            "required": ["path"],
        },
    },
    "command.inspect": {
        "type": "function",
        "name": "command.inspect",
        "description": "Propose a read-only inspection command (e.g. git status, python --version).",
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact command arguments"
                },
                "path": {"type": "string", "description": "Working directory"}
            },
            "required": ["argv"],
        },
    },
    "command.run": {
        "type": "function",
        "name": "command.run",
        "description": "Propose an approved command under Full Computer profile.",
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact command arguments"
                },
                "path": {"type": "string", "description": "Working directory"}
            },
            "required": ["argv"],
        },
    },
}


def model_facing_json(value: Any, limit: int = 6000) -> str:
    """Serialise a tool result for the model, as VALID JSON.

    The result used to be cut at a raw character offset, which left a
    half-written JSON object in the model's tool channel - a result the
    model could not read at all.  A result that is too long is now
    REDUCED instead, and the reduction says so, so the model is told what
    happened rather than being handed a broken document.

    The reduction is PROPORTIONAL: every long string is trimmed and every
    long list is shortened until the whole document fits.  Dropping the
    content altogether - which is what a single hard cut does - leaves the
    model with nothing at all, which is worse than a partial answer.
    """
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        text = json.dumps({"result": str(value)}, ensure_ascii=False)
    if len(text) <= limit:
        return text
    reduced: dict[str, Any] = (
        dict(value) if isinstance(value, dict) else {"result": value}
    )
    reduced.pop("_truncated", None)
    # 1. Shorten the lists to what fits, keeping the first entries.
    for key, item in list(reduced.items()):
        if isinstance(item, (list, tuple)) and len(item) > 1:
            kept: list[Any] = []
            for entry in list(item):
                candidate = dict(reduced)
                candidate[key] = kept + [entry]
                if len(json.dumps(candidate, ensure_ascii=False, default=str)) > limit - 400:
                    break
                kept.append(entry)
            reduced[key] = kept
    # 2. Then trim the strings, proportionally, until the document fits.
    for _ in range(12):
        try:
            text = json.dumps(reduced, ensure_ascii=False, default=str)
        except (TypeError, ValueError, RecursionError):
            text = ""
        if text and len(text) <= limit - 300:
            break
        strings = [
            key for key, item in reduced.items()
            if isinstance(item, str) and len(item) > 40
        ]
        if not strings:
            break
        longest = max(strings, key=lambda key: len(str(reduced[key])))
        current = str(reduced[longest])
        # Take a third off the longest string each pass, so every long
        # string is trimmed and none is left whole while another is cut.
        keep = max(40, len(current) // 3)
        reduced[longest] = current[:keep] + "... [cut]"
    reduced["_truncated"] = (
        "the result was too long for the model's tool channel, so it was "
        "trimmed; narrow the request to see the rest"
    )
    try:
        text = json.dumps(reduced, ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        text = ""
    if text and len(text) <= limit:
        return text
    return json.dumps(
        {
            "_truncated": (
                "the result was too long for the model's tool channel and was "
                "not shown; narrow the request"
            )
        },
        ensure_ascii=False,
    )


def get_governed_tools_for_profile(profile: PermissionProfile) -> tuple[dict[str, Any], ...]:
    """Return tool schemas allowed under the specified PermissionProfile."""
    allowed_caps = PROFILE_CAPABILITIES[profile]
    tools: list[dict[str, Any]] = []

    if FileCapability.READ in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.read"])
    if FileCapability.LIST in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.list"])
    if FileCapability.STAT in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.stat"])
    if FileCapability.SEARCH in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.search"])

    if FileCapability.WRITE in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.write"])
    if FileCapability.APPEND in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.append"])
    if FileCapability.COPY in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.copy"])
    if FileCapability.TRASH in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.trash"])

    if FileCapability.MOVE in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["filesystem.move"])
    if FileCapability.PROPOSE_COMMAND in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["command.inspect"])

    if FileCapability.PROPOSE_APPLICATION in allowed_caps:
        tools.append(_GOVERNED_TOOL_SCHEMAS["command.run"])

    return tuple(tools)


def _is_structured_text(text: str) -> bool:
    """True when the reply is a structured payload that must not be annotated."""
    stripped = str(text or "").strip()
    return stripped.startswith(("{", "[", "```"))


#: A tool name is MODEL-CONTROLLED.  It is rendered into an authoritative
#: message and into the run record, so it is accepted only in the shape AMCA
#: itself issues, and it is length-bounded.  A provider must not be able to
#: forge extra rows or instructions through this field.
_SAFE_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,63}")


def approval_id_of(value: Any) -> str:
    """Return the approval id from any governed approval result shape.

    ``LocalToolExecutor.propose_filesystem_operation`` and
    ``LocalToolExecutor.request_inspection_command`` return
    ``{"approval": {"id": ...}}`` while other surfaces return a flat
    ``approval_id``/``id``.  A governed tool result must always carry the exact
    id the human has to act on, so both shapes are accepted here and an
    unreadable result yields an empty string instead of a fabricated id.
    """
    if not isinstance(value, Mapping):
        return ""
    for key in ("approval_id", "id"):
        candidate = value.get(key)
        if candidate:
            return str(candidate)
    nested = value.get("approval")
    if isinstance(nested, Mapping):
        for key in ("id", "approval_id"):
            candidate = nested.get(key)
            if candidate:
                return str(candidate)
    return ""


#: Governed tool rounds per model call (initial call + each tool-result
#: continuation).  Single-sourced here because the desktop run's cloud-call
#: ceiling must cover the same bound (see service.py KernelConfig).
DEFAULT_MAX_TOOL_ITERATIONS = 5

#: Bounds for the model-visible governed-operation evidence note.
_EVIDENCE_NOTE_MAX_ROWS = 12
_EVIDENCE_NOTE_MAX_CHARS = 4000
_EVIDENCE_FIELD_MAX_CHARS = 120


def _evidence_field(value: Any) -> str:
    """Render one ledger field for the model-visible evidence note.

    The tool name and target path are model-controlled, so a newline in either
    would otherwise let a model forge extra rows -- or whole instructions --
    inside a system message that reads as authoritative.  Fields are therefore
    escaped, stripped of non-printable characters and length-bounded.
    """

    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n")
    text = "".join(character if character.isprintable() else "?" for character in text)
    return text.strip()[:_EVIDENCE_FIELD_MAX_CHARS]


class GovernedChatAdapter(ModelAdapter):
    """Wraps an underlying ModelAdapter to provide AMCA governed tool execution."""

    def __init__(
        self,
        inner: ModelAdapter,
        *,
        project_id: str,
        service: Any,
        conversation_id: str | None = None,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        super().__init__(
            model=inner.model,
            provider=inner.provider,
            adapter_id=inner.adapter_id,
            capabilities=getattr(inner, "capabilities", None),
        )
        self.request_timeout_seconds = getattr(inner, "request_timeout_seconds", 60.0)
        self.inner = inner
        self.project_id = project_id
        self.service = service
        self.conversation_id = conversation_id
        self.max_tool_iterations = max_tool_iterations
        self.executed_tools: list[dict[str, Any]] = []
        #: Canonical names offered to the model for the current run; None
        #: before the first run so a direct dispatch call keeps its old path.
        self._offered_tool_names: frozenset[str] | None = None
        self.effective_profile: PermissionProfile = PermissionProfile.RESTRICTED
        self.workspace_root: str | None = None
        # 26C DEFECT-13: one governed adapter is constructed per chat run, so
        # clearing the per-conversation evidence ledger here scopes recorded
        # tool evidence to exactly this run and never leaks across turns.
        # v1.1.9 invariant: every governed adapter for a conversation is built
        # before the run is dispatched (service.start_chat_run constructs them
        # all, then RunRegistry starts).  Constructing one mid-run would wipe a
        # peer's already-published evidence, so keep construction ahead of
        # dispatch if that is ever refactored.
        if conversation_id is not None:
            try:
                self.service.begin_governed_ops(str(conversation_id))
            except Exception:
                pass
        self._refresh_policy()

    def _refresh_policy(self) -> None:
        policy = self.service.local_permission_store.profile(self.project_id)
        self.effective_profile = PermissionProfile(str(policy.get("profile", "restricted")))
        self.workspace_root = policy.get("workspace_root")

    def capabilities(self) -> ModelCapabilities:
        caps = self.inner.capabilities()
        return replace(caps, supports_tools=True)

    def provider_metadata(self) -> Mapping[str, Any]:
        """Forward the delegate's metadata unchanged.

        The call-admission gate reads ``privacy_boundary`` from this mapping to
        prove a cloud call sits behind an exact-scope approved boundary; a
        wrapper that dropped it made every governed cloud call fail preflight.
        """

        return self.inner.provider_metadata()

    def _dispatch_one_tool(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call through AMCA's governance chain."""
        from .service import _AGENT_TOOL_ACTIONS

        action = _AGENT_TOOL_ACTIONS.get(call.name)
        args = dict(call.arguments or {})
        path = str(args.get("path", ""))[:512]
        allowed_caps = PROFILE_CAPABILITIES[self.effective_profile]

        # A model may only call a tool this run actually offered.  The global
        # action map is not sufficient authority on its own: a tool the run
        # never transmitted is refused before any permission or grant check.
        offered = self._offered_tool_names
        if offered is not None and call.name not in offered:
            res = ToolResult(
                call_id=call.call_id,
                name=call.name,
                state=ToolResultState.UNKNOWN_TOOL,
                content="tool was not offered to this run",
                arguments=args,
            )
            self.executed_tools.append({"name": call.name, "action": "unknown", "state": "unknown", "path": path})
            return res

        if action is None:
            res = ToolResult(
                call_id=call.call_id,
                name=call.name,
                state=ToolResultState.UNKNOWN_TOOL,
                content="tool is not part of the governed AMCA catalog",
                arguments=args,
            )
            self.executed_tools.append({"name": call.name, "action": "unknown", "state": "unknown", "path": path})
            return res

        # Check profile capability boundary
        cap_enum = {
            "read": FileCapability.READ,
            "list": FileCapability.LIST,
            "stat": FileCapability.STAT,
            "search": FileCapability.SEARCH,
            "write": FileCapability.WRITE,
            "append": FileCapability.APPEND,
            "copy": FileCapability.COPY,
            "move": FileCapability.MOVE,
            "trash": FileCapability.TRASH,
            "inspect": FileCapability.PROPOSE_COMMAND,
            "run": FileCapability.PROPOSE_APPLICATION,
        }.get(action)

        if cap_enum is not None and cap_enum not in allowed_caps:
            msg = f"operation blocked by {self.effective_profile.value.replace('_', ' ').title()} profile"
            res = ToolResult(
                call_id=call.call_id,
                name=call.name,
                state=ToolResultState.DENIED,
                content=msg,
                arguments=args,
                is_error=True,
            )
            self.executed_tools.append({
                "name": call.name, "action": action, "state": "denied", "path": path, "content": msg
            })
            return res

        # A project folder is required for every level except Full Computer.
        # Restricted, Standard AND Extended are all bounded by the project
        # folder - only Full Computer is PC-wide, by product decision - so
        # without one there is nothing for the executor to contain the target
        # against. Extended adds capabilities, not reach.
        if (
            not self.workspace_root
            and action in ("read", "write", "append", "copy", "move", "trash")
            and self.effective_profile
            in (
                PermissionProfile.RESTRICTED,
                PermissionProfile.STANDARD,
                PermissionProfile.EXTENDED,
            )
        ):
            if self.effective_profile is PermissionProfile.RESTRICTED:
                msg = (
                    "this chat has no project folder and Restricted access is limited "
                    "to one. Open the chat in a Project, or raise Local PC Access."
                )
            elif self.effective_profile is PermissionProfile.EXTENDED:
                msg = (
                    "this chat has no project folder and Extended access is limited to "
                    "one. Open the chat in a Project, or choose Full Access."
                )
            else:
                msg = (
                    "this chat has no project folder and Standard access is limited to "
                    "one. Open the chat in a Project, or raise Local PC Access."
                )
            res = ToolResult(
                call_id=call.id,
                name=call.name,
                state=ToolResultState.DENIED,
                content=msg,
                arguments=args,
                is_error=True,
            )
            self.executed_tools.append({
                "name": call.name, "action": action, "state": "denied", "path": path, "content": msg
            })
            return res

        # Direct read actions
        if action in ("read", "list", "stat", "search"):
            # The path decides the grant.  Asking for "a grant that can read"
            # picked whichever grant came first, which with the whole-PC drive
            # set meant a read of a C:\ path could be attempted against the
            # H:\ grant and refused for escaping a root it never left.
            grant_id = self.service._agent_grant_for(self.project_id, action, path)
            if not grant_id:
                # Resolving the grant from the path means an out-of-scope
                # request now lands here rather than being attempted against an
                # unrelated grant.  Say so: the model is told the path is
                # outside its grants, not that a grant id was malformed.
                msg = (
                    f"the requested path is outside every active path grant "
                    f"for this chat: {path}"
                )
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.DENIED,
                    content=msg,
                    arguments=args,
                    is_error=True,
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "denied",
                    "path": path, "content": msg,
                })
                return res
            try:
                out = self.service.execute_local_tool({
                    "project_id": self.project_id,
                    "grant_id": grant_id,
                    "action": action,
                    "path": path,
                    "query": str(args.get("query", ""))[:256] or None,
                })
                text = model_facing_json(out)
                content_val = out.get("content") if isinstance(out, dict) and "content" in out else text
                if isinstance(content_val, str) and len(content_val) > 6000:
                    # The read tool hands over the file's TEXT, which can be
                    # megabytes.  It goes to the model as plain text, so it
                    # is trimmed to the same budget as every other result.
                    content_val = content_val[:6000] + "... [cut: the result was too long]"
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.SUCCESS,
                    content=str(content_val),
                    arguments=args,
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "success", "path": path, "content": str(content_val)
                })
                return res
            except Exception as error:
                err_msg = str(error)
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.DENIED if "outside" in err_msg.lower() or "denie" in err_msg.lower() else ToolResultState.EXECUTION_ERROR,
                    content=err_msg,
                    arguments=args,
                    is_error=True,
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "denied", "path": path, "content": err_msg
                })
                return res

        # Mutations requiring grant resolution (write, append, copy, move, trash)
        if action in ("write", "append", "copy", "move", "trash"):
            perm_action = (
                "local_file_move" if action == "move"
                else "local_file_trash" if action == "trash"
                else "local_file_modify"
            )
            resolution = self.service.local_tool_executor.resolve_mutation_target(
                project_id=self.project_id, action=action, path=path,
            )
            if not resolution.get("matched"):
                msg = str(resolution.get("reason") or "no active path grant covers this target")
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.DENIED,
                    content=msg,
                    arguments=args,
                    is_error=True,
                    details={"grant_resolution": {
                        "matched": False,
                        "requested_path": str(resolution.get("requested_path") or path),
                    }},
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "denied", "path": path, "content": msg
                })
                return res

            grant_id = str(resolution["grant_id"])
            effective_capability = str(resolution["capability"])
            relative_path = str(resolution["relative_path"])

            # v1.1.9: writing bytes the file already holds cannot destroy
            # anything, so it is answered as a verified no-op instead of
            # raising an exact-scope approval for a change that would not
            # occur.  Only the byte-identical case is short-circuited.
            if action == "write" and resolution.get("target_exists"):
                no_op_note = self._identical_content_no_op(grant_id, relative_path, args)
                if no_op_note is not None:
                    res = ToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        state=ToolResultState.SUCCESS,
                        content=no_op_note,
                        arguments=args,
                        details={
                            "grant_id": grant_id,
                            "capability": "write",
                            "path": relative_path,
                            "unchanged": True,
                        },
                    )
                    # Recorded as its own action: the anti-fabrication gate
                    # credits a successful ``write`` as proof that a write ran,
                    # and no write ran here.  ``write_noop`` is a verified
                    # observation, not an execution.
                    self.executed_tools.append({
                        "name": call.name, "action": "write_noop", "state": "unchanged",
                        "path": relative_path, "content": no_op_note,
                    })
                    return res

            # 26C DEFECT-14: a directory grant authorizing CREATE lets a NEW
            # child file inside the granted directory be created under the
            # grant itself (the grant is the explicit human authorization).
            # Existing-file modification still requires the approval flow.
            if (
                effective_capability == "create"
                and action in ("write", "create")
                and not resolution.get("target_exists")
            ):
                try:
                    out = self.service.execute_local_tool({
                        "project_id": self.project_id,
                        "grant_id": grant_id,
                        "action": "create",
                        "path": relative_path,
                        "content": (str(args["content"]) if args.get("content") is not None else None),
                        "content_base64": (str(args["content_base64"]) if args.get("content_base64") is not None else None),
                    })
                    created_path = str(out.get("path", relative_path))
                    size_bytes = out.get("size_bytes")
                    # 28A NEW-02: the canonical absolute path comes from the
                    # executed tool record (grant root + relative), never from
                    # model string concatenation, so confirmations cannot show
                    # a malformed or guessed location.
                    absolute_path = str(
                        (pathlib.PurePath(str(out.get("root", ""))) / created_path)
                    )
                    content_val = (
                        f"created {created_path} ({size_bytes} bytes) under grant {grant_id} "
                        f"with capability create. Canonical location: {absolute_path}"
                    )
                    res = ToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        state=ToolResultState.SUCCESS,
                        content=content_val,
                        arguments=args,
                        details={
                            "grant_id": grant_id,
                            "capability": "create",
                            "path": created_path,
                            "absolute_path": absolute_path,
                        },
                    )
                    self.executed_tools.append({
                        "name": call.name, "action": "create", "state": "success", "path": created_path,
                        "content": content_val, "absolute_path": absolute_path,
                    })
                    return res
                except Exception as error:
                    err_msg = str(error)
                    res = ToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        state=ToolResultState.DENIED,
                        content=err_msg,
                        arguments=args,
                        is_error=True,
                        details={"grant_id": grant_id, "capability": "create"},
                    )
                    self.executed_tools.append({
                        "name": call.name, "action": "create", "state": "denied", "path": relative_path,
                        "content": err_msg,
                    })
                    return res

            try:
                approval = self.service.propose_local_filesystem_approval({
                    "project_id": self.project_id,
                    "grant_id": grant_id,
                    "capability": effective_capability,
                    "approval_action": perm_action,
                    "source": relative_path,
                    "destination": str(args.get("destination", ""))[:512] or None,
                    "overwrite": bool(args.get("overwrite", False)),
                    "reason": f"chat governed tool execution ({action})",
                })
                approval_id = approval_id_of(approval)
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.APPROVAL_REQUIRED,
                    content=f"Human approval required: {approval_id} for {action} on {relative_path}. Review and approve in Approvals.",
                    arguments=args,
                    details={"approval_id": approval_id, "grant_id": grant_id, "capability": effective_capability},
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "approval_required", "path": relative_path, "approval_id": approval_id
                })
                return res
            except Exception as error:
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.DENIED,
                    content=str(error),
                    arguments=args,
                    is_error=True,
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "denied", "path": relative_path, "content": str(error)
                })
                return res

        # Commands (inspect, run)
        if action in ("inspect", "run"):
            grant_id = self.service._agent_grant_for(self.project_id, "propose_command") or ""
            argv = [str(x)[:256] for x in (args.get("argv") or [])][:16]
            try:
                approval = self.service.request_local_inspection_command({
                    "project_id": self.project_id,
                    "grant_id": grant_id,
                    "argv": argv,
                    "cwd": path or ".",
                    "timeout_seconds": 15,
                    "reason": f"chat governed command execution ({argv[:2]})",
                })
                approval_id = approval_id_of(approval)
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.APPROVAL_REQUIRED,
                    content=f"Human approval required: {approval_id} for command {' '.join(argv)}. Review in Approvals.",
                    arguments=args,
                    details={"approval_id": approval_id},
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "approval_required", "argv": argv, "approval_id": approval_id
                })
                return res
            except Exception as error:
                res = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    state=ToolResultState.DENIED,
                    content=str(error),
                    arguments=args,
                    is_error=True,
                )
                self.executed_tools.append({
                    "name": call.name, "action": action, "state": "denied", "argv": argv, "content": str(error)
                })
                return res

        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            state=ToolResultState.UNKNOWN_TOOL,
            content="unhandled action",
            arguments=args,
        )

    @staticmethod
    def _rejected_tool_result(
        call_id: str,
        name: str,
        reported_state: Any,
        raw: Any,
        is_map: bool,
    ) -> ToolResult:
        """Convert a provider-reported failure entry into a truthful refusal.

        The entry is never dispatched: it carries no validated arguments.  A
        state this adapter cannot read fails closed to MALFORMED_REQUEST, and a
        provider-claimed success is never credited as an AMCA execution.
        """
        content = str((raw.get("content") if is_map else getattr(raw, "content", "")) or "")
        try:
            state = (
                reported_state
                if isinstance(reported_state, ToolResultState)
                else ToolResultState(str(reported_state))
            )
        except ValueError:
            state = ToolResultState.MALFORMED_REQUEST
        if state is ToolResultState.SUCCESS:
            state = ToolResultState.MALFORMED_REQUEST
        return ToolResult(
            call_id=call_id or "amca-rejected-call",
            name=name or "tool",
            state=state,
            content=content or "tool call was rejected before execution",
            is_error=True,
        )

    def _author_claimed_artifacts(self, text: str) -> int:
        """Author any parsed artifact blocks into ArtifactStore."""
        count = 0
        blocks = parse_artifact_blocks(text)
        for block in blocks:
            try:
                self.service.artifact_store.author(
                    project_id=self.project_id,
                    artifact_type=block["artifact_type"],
                    title=block["title"],
                    content=block["content"],
                    created_by=f"model:{self.adapter_id}",
                    chat_id=self.conversation_id,
                )
                count += 1
            except Exception:
                pass
        return count

    def _tool_catalog_withheld_reason(self) -> str:
        """Observed reason why the wrapped adapter withheld the tool catalog.

        Read from the capability-evidence ledger, which an adapter writes only
        when it actually withheld the catalog (unverified or verified
        unsupported capability).  Nothing is predicted here: an adapter without
        a capability gate never records a drop, so no false claim is made about
        tools that were in fact offered.
        """
        inner = self.inner
        provider = str(getattr(inner, "provider", "") or "")
        model = str(getattr(inner, "model", "") or "")
        drop = tool_catalog_drop(provider, model)
        return drop.reason if drop is not None else ""

    #: Header for the per-run governed-operation evidence note injected into
    #: every participant prompt (see ``_governed_evidence_note``).
    _EVIDENCE_NOTE_MARKER = "AMCA governed operation evidence for this run"
    _EVIDENCE_NOTE_HEADER = (
        "AMCA governed operation evidence for this run. Every operation listed "
        "below has already been dispatched and its outcome is authoritative. "
        "Do not repeat an operation listed here -- verify with a read or stat "
        "instead -- and do not claim any operation that is not listed here."
    )

    def _run_governed_evidence(self) -> list[dict[str, Any]]:
        """Canonical governed-operation evidence for the whole run.

        v1.1.9: the anti-fabrication reconciliation used to see only
        ``self.executed_tools`` -- the operations THIS adapter dispatched.  A
        participant that reported a peer's successful write was therefore
        judged against an evidence list that never contained it, and its
        truthful report was replaced with "That operation was not executed."
        while the file it described sat on disk.  The run ledger is the same
        canonical evidence the publication gate already merges, and it is
        cleared at run admission, so this credits exactly the operations that
        ran in this run -- no more.
        """

        evidence: list[dict[str, Any]] = [
            dict(entry) for entry in self.executed_tools if isinstance(entry, Mapping)
        ]
        if self.conversation_id:
            try:
                from .service import governed_ops_for

                for entry in governed_ops_for(str(self.conversation_id)):
                    if isinstance(entry, Mapping):
                        evidence.append(dict(entry))
            except Exception:
                pass
        return evidence

    def _governed_evidence_note(self) -> str:
        """Summarise the governed operations already dispatched in this run.

        v1.1.9: a governed tool result was visible only inside the
        ``generate()`` call that produced it.  Every later round rebuilt a
        fresh one-message request from ``ContextBuilder``, which carries no
        tool results, so a second participant re-issued the same write, the
        retry resolved to WRITE on the now-existing file, and the run stalled
        on an exact-scope approval -- then reported failure while the file it
        had already created sat on disk.  The per-conversation ledger already
        held this evidence for the publication gate; surfacing the same ledger
        to the participants is what makes a run idempotent and truthful.

        This text is sent to cloud models, so it renders metadata only: tool
        name, target path, outcome.  Command arguments are never rendered --
        they can carry credentials -- and every field is sanitised because the
        path and tool name are model-controlled and must not be able to forge
        extra rows or instructions inside an authoritative system message.
        """

        ledger: list[Mapping[str, Any]] = list(self.executed_tools)
        peer_evidence_missing = False
        if self.conversation_id:
            try:
                from .service import governed_ops_for

                ledger.extend(governed_ops_for(str(self.conversation_id)))
            except Exception:
                # Never claim peer evidence was considered when it was not.
                peer_evidence_missing = True
        rows: list[str] = []
        seen: set[str] = set()
        for op in ledger:
            if not isinstance(op, Mapping):
                continue
            name = _evidence_field(op.get("name") or op.get("action"))
            if not name:
                continue
            if op.get("path"):
                target = _evidence_field(op.get("path"))
            elif op.get("argv") is not None:
                try:
                    count = len(op.get("argv") or ())
                except TypeError:
                    count = 0
                target = f"{count} command argument(s) redacted"
            else:
                target = ""
            state = _evidence_field(op.get("state"))
            line = " - " + " -> ".join(part for part in (name, target, state) if part)
            if line in seen:
                continue
            seen.add(line)
            rows.append(line)
            if len(rows) >= _EVIDENCE_NOTE_MAX_ROWS:
                break
        if not rows and not peer_evidence_missing:
            return ""
        note = self._EVIDENCE_NOTE_HEADER + "\n" + "\n".join(rows)
        if peer_evidence_missing:
            note += (
                "\n(peer operation evidence was unavailable for this prompt; "
                "only this participant's own operations are listed)"
            )
        return note[:_EVIDENCE_NOTE_MAX_CHARS]

    def _with_evidence_note(
        self, messages: Sequence[Any]
    ) -> list[Any]:
        """Return ``messages`` with this run's evidence note as the first message."""

        messages = list(messages)
        evidence_note = self._governed_evidence_note()
        messages = [
            m for m in messages
            if not (
                isinstance(m, Mapping)
                and self._EVIDENCE_NOTE_MARKER in str(m.get("content", ""))
            )
        ]
        if evidence_note:
            messages.insert(0, {"role": "system", "content": evidence_note})
        return messages

    def _identical_content_no_op(
        self, grant_id: str, relative_path: str, args: Mapping[str, Any]
    ) -> str | None:
        """Confirm a write whose target already holds exactly the proposed bytes.

        A governed write to an existing file normally needs an exact-scope
        approval because it can destroy the file's current content.  When the
        bytes on disk are already byte-identical to the bytes the model is
        proposing, the write cannot destroy anything -- it is a no-op.  Without
        this, one participant creating a file and a peer re-sending the same
        bytes stalls the whole run on an approval card for a change that would
        not happen.

        Every guard below fails CLOSED to the approval path:

        * only a real ``str`` payload is compared, so a malformed argument can
          never be "equal" to the empty file it would not have written;
        * the read grant is resolved path-aware and the read result must
          resolve to the SAME canonical file as the write target, so a read
          grant rooted elsewhere can never stand in for the target;
        * the file is re-stat'ed after the read, and the confirmation is phrased
          as an observation ("when it was checked") because a concurrent
          writer can always change the file after any check.
        """

        content = args.get("content")
        if not isinstance(content, str) or args.get("content_base64") is not None:
            return None
        proposed = content.encode("utf-8")
        before = self._observe_target(relative_path)
        if before is None:
            return None
        try:
            read_grant = self.service._agent_grant_for(
                self.project_id, "read", relative_path
            )
        except Exception:
            return None
        if not read_grant:
            return None
        try:
            current = self.service.execute_local_tool({
                "project_id": self.project_id,
                "grant_id": read_grant,
                "action": "read",
                "path": relative_path,
            })
        except Exception:
            return None
        if not isinstance(current, dict) or "content" not in current:
            return None
        existing = current.get("content")
        if not isinstance(existing, str) or existing.encode("utf-8") != proposed:
            return None
        # The comparison is only meaningful if it was made against the file the
        # write would actually replace.
        if not self._same_canonical_file(grant_id, relative_path, current):
            return None
        # Re-check the target after the read: a file replaced in between must
        # not be reported as already containing the content.  Size AND mtime
        # must be unchanged across the read, so a same-size swap is caught too.
        after = self._observe_target(relative_path)
        if after is None or after != before:
            return None
        return (
            f"{relative_path} already contained exactly this content "
            f"({len(proposed)} bytes) when it was checked; the file was not modified."
        )

    def _observe_target(self, relative_path: str) -> tuple[int, str] | None:
        """Return ``(size_bytes, modified_at)`` for the target, or None."""

        try:
            stat_grant = self.service._agent_grant_for(
                self.project_id, "stat", relative_path
            )
            observed = self.service.execute_local_tool({
                "project_id": self.project_id,
                "grant_id": stat_grant or "",
                "action": "stat",
                "path": relative_path,
            })
        except Exception:
            return None
        if not isinstance(observed, dict) or not observed.get("exists"):
            return None
        metadata = observed.get("metadata") or {}
        try:
            return int(metadata["size_bytes"]), str(metadata.get("modified_at") or "")
        except (KeyError, TypeError, ValueError):
            return None

    def _same_canonical_file(
        self, grant_id: str, relative_path: str, read_result: Mapping[str, Any]
    ) -> bool:
        """Whether a governed read resolved to the write target's canonical file."""

        try:
            write_grant = self.service.local_permission_store.grant(str(grant_id))
            if write_grant is None:
                return False
            write_target = pathlib.Path(write_grant.canonical_root) / relative_path
            read_target = pathlib.Path(str(read_result.get("root") or "")) / str(
                read_result.get("path") or ""
            )
            return os.path.normcase(str(write_target.resolve())) == os.path.normcase(
                str(read_target.resolve())
            )
        except Exception:
            return False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._refresh_policy()
        tools = get_governed_tools_for_profile(self.effective_profile)
        self._offered_tool_names = frozenset(
            str(
                (tool.get("name") if isinstance(tool, Mapping) else getattr(tool, "name", ""))
                or ""
            )
            for tool in tools
        )
        effective_req = replace(request, tools=tools)

        # Inject tool awareness system prompt if not present
        messages = list(effective_req.messages)
        has_awareness = any(
            isinstance(m, Mapping) and "AMCA Studio Environment" in str(m.get("content", ""))
            or isinstance(m, str) and "AMCA Studio Environment" in m
            for m in messages
        )
        if not has_awareness:
            messages.insert(0, {
                "role": "system",
                "content": AMCA_TOOL_AWARENESS_SYSTEM + f"\nActive Local PC Profile: {self.effective_profile.value.upper()}\nWorkspace root: {self.workspace_root or 'Not configured'}\n",
            })
        effective_req = replace(effective_req, messages=tuple(messages))

        # v1.1.9: give every participant of every round this run's governed
        # operation evidence.  Re-applied before every provider call, because a
        # peer's round -- or this participant's own tool loop -- may have
        # dispatched operations since the previous call.
        messages = self._with_evidence_note(messages)
        effective_req = replace(effective_req, messages=tuple(messages))

        resp = await self.inner.generate(effective_req)
        # Evaluated after the first provider call so a local runtime capability
        # probe has already had the chance to record verified evidence.
        withheld_reason = (
            self._tool_catalog_withheld_reason() if tools else ""
        )
        iterations = 0
        # The last assistant text the provider actually produced.  A provider
        # may answer with tool calls and no prose at all (the installed
        # failure of 2026-10-21 recorded the SHA-256 of the empty string in
        # the transmission ledger), and a later turn may be empty again.
        # Keeping the last non-empty turn means the run still has the model's
        # own words to publish instead of failing the whole chat.
        last_non_empty_text = ""
        # Tool calls the provider asked for but the iteration budget did not
        # allow to run.  They are reported, never silently dropped.
        undispatched_tool_names: list[str] = []

        while iterations < self.max_tool_iterations:
            raw_calls = tuple(getattr(resp, "tool_calls", ()) or ())
            if not raw_calls:
                break
            iterations += 1
            turn_text = str(getattr(resp, "text", "") or "")
            if turn_text.strip():
                last_non_empty_text = turn_text

            tool_calls: list[ToolCall] = []
            rejected: list[ToolResult] = []
            assistant_calls: list[dict[str, Any]] = []
            for c in raw_calls:
                is_map = isinstance(c, Mapping)
                call_id = str(
                    (c.get("call_id") if is_map else getattr(c, "call_id", "")) or ""
                )
                name = str((c.get("name") if is_map else getattr(c, "name", "")) or "")
                reported_state = c.get("state") if is_map else getattr(c, "state", None)
                if reported_state is not None:
                    # A provider-reported failure entry is not a model tool call:
                    # the Ollama parser returns a ToolResult (state +
                    # content, no arguments) when the model emitted malformed
                    # arguments.  Dispatching it as a call with empty arguments
                    # would execute a tool the model never validly requested
                    # (MALFORMED_ARGUMENTS_EXECUTABLE=NO), so it is refused
                    # before execution and only reported back to the model.
                    rejected.append(
                        self._rejected_tool_result(call_id, name, reported_state, c, is_map)
                    )
                    continue
                arguments = c.get("arguments") if is_map else getattr(c, "arguments", None)
                tool_calls.append(ToolCall(
                    call_id=call_id,
                    name=name,
                    arguments=dict(arguments or {}),
                ))
                assistant_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": dict(arguments or {})},
                })

            # Dispatch each valid tool call; provider-reported failures are
            # answered truthfully without any execution.
            tool_results: list[ToolResult] = list(rejected)
            for call in tool_calls:
                tool_results.append(self._dispatch_one_tool(call))
            for res in rejected:
                # Evidence ledger: a refused call is recorded as a refusal, so
                # the anti-fabrication gate can never credit it as an execution.
                self.executed_tools.append({
                    "name": res.name,
                    "action": "rejected",
                    "state": res.state.value,
                    "content": res.content[:500],
                })

            # Append assistant message with tool_calls and tool results
            messages.append({
                "role": "assistant",
                "content": getattr(resp, "text", "") or "",
                "tool_calls": assistant_calls,
            })
            for res in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": res.call_id,
                    # Local providers (Ollama) match a tool result to its call by
                    # the tool name; an unnamed tool message loses that binding.
                    "name": res.name,
                    "tool_name": res.name,
                    "content": res.content,
                })

            resp = await self.inner.generate(
                replace(effective_req, messages=tuple(self._with_evidence_note(messages)))
            )

        # The loop exits at its iteration ceiling, so the LAST provider
        # response's tool calls are never dispatched.  That response is the
        # one the kernel sees, and a provider that answers with tool calls
        # and no prose sends an EMPTY body (the installed failure of
        # 2026-10-21 recorded the SHA-256 of the empty string in the
        # transmission ledger).  Record what was left undone so the run can
        # say so instead of publishing nothing.
        #
        # The tool name is MODEL-CONTROLLED, so it is sanitised and bounded
        # before it can reach an authoritative message or the run record -
        # the same rule the evidence ledger applies to the fields it renders.
        for pending in tuple(getattr(resp, "tool_calls", ()) or ()):
            pending_name = (
                pending.get("name") if isinstance(pending, Mapping)
                else getattr(pending, "name", "")
            )
            safe_name = _SAFE_TOOL_NAME.match(str(pending_name or ""))
            if safe_name:
                undispatched_tool_names.append(safe_name.group(0)[:64])

        # Process artifact creation and anti-fabrication reconciliation
        final_text = getattr(resp, "text", "") or ""
        provider_body_empty = not final_text.strip()
        if provider_body_empty:
            # The provider's final turn carried no text.  Fall back to the
            # last turn that did, so the run publishes what the model
            # actually said rather than an empty body that the semantic
            # extractor would reject.
            final_text = last_non_empty_text
        if not final_text.strip() and iterations:
            # Every turn of this tool exchange was tool-only and carried no
            # prose.  Say so truthfully instead of publishing nothing: a
            # silent empty answer is indistinguishable from a broken
            # product.  The wording must match what actually happened.
            if iterations >= self.max_tool_iterations:
                # The ceiling was reached: the model wanted more and was cut
                # off.
                reason = (
                    "the model used its whole governed tool budget "
                    f"({iterations} iteration(s))"
                )
            else:
                # The model stopped on its own.  It asked for nothing more,
                # so the note must not claim that it did.
                reason = (
                    "the model stopped after "
                    f"{iterations} governed tool iteration(s)"
                )
            if undispatched_tool_names:
                # Scoped to the PENDING request: the same tool may have run
                # successfully in an earlier turn of this exchange, so the
                # claim must be about the request that did not run.
                reason += (
                    "; its last request for "
                    + ", ".join(sorted(set(undispatched_tool_names)))
                    + " did not run"
                )
            reason += " and it produced no answer text"
            # AMCA is speaking here, not the model, so the note says so.  It
            # must NOT start with "[" or "{": the prose->JSON normalizer below
            # treats such text as machine-shaped and would skip it, handing
            # the extractor invalid JSON and reproducing the very failure this
            # note exists to prevent.
            final_text = f"AMCA note: {reason}."
        # The semantic extractor accepts JSON fences.  A fenced committee
        # payload must keep that contract while its claims are reconciled:
        # the reconciler replaces a machine-shaped payload wholesale, which
        # would leave the kernel holding a sentence it cannot parse.  Decode
        # the fence first, so the payload is reconciled field by field.
        fenced = re.fullmatch(
            r"\s*```(?:json)?\s*(.*?)\s*```\s*", final_text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced:
            try:
                decoded = json.loads(fenced.group(1))
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict) and isinstance(decoded.get("summary"), str):
                final_text = json.dumps(decoded, ensure_ascii=False)
        # Gate-2 fix (manager decision 1a): capture the titles before the
        # fence strip so an artifact-only reply can keep a minimal, truthful
        # transcript entry instead of losing the assistant message entirely
        # (the stripped text is empty and empty content fails message
        # validation downstream).
        authored_titles = [
            str(block["title"]) for block in parse_artifact_blocks(final_text)
        ]
        authored_count = self._author_claimed_artifacts(final_text)
        # v1.1.8 Phase 7: the fence is an instruction to AMCA, not prose.  It was
        # rendered verbatim in the transcript, so the reader saw
        # "```artifact docx title=..." and the file body as a code block.
        final_text = strip_artifact_blocks(final_text)

        # 26C DEFECT-13: publish this run's canonical tool evidence so the
        # final public-response reconciliation (service._public_response)
        # judges claims against the same evidence this adapter verified.
        if conversation_key := self.conversation_id:
            try:
                self.service.note_governed_executed_ops(
                    str(conversation_key), self.executed_tools
                )
            except Exception:
                pass

        # v1.1.9: judge this participant's claims against the WHOLE run's
        # governed evidence, not only the operations this adapter dispatched.
        # A participant that truthfully reported a peer's write was previously
        # stripped to "That operation was not executed." because the peer's
        # success was absent from its own list.
        run_evidence = self._run_governed_evidence()
        reconciled_text, _ = reconcile_anti_fabrication(
            final_text,
            executed_tools=run_evidence,
            authored_artifacts=authored_count,
            effective_profile=self.effective_profile.value,
            workspace_root=self.workspace_root,
        )
        # v1.1.8: a structured reply must stay valid JSON. Injecting the
        # reconciliation sentence into the payload corrupted it, so the kernel
        # could no longer read the answer's summary and the raw JSON (with a
        # correction sentence spliced into it) became the user-visible answer.
        # Reconcile the summary field in place instead.
        if _is_structured_text(final_text) and reconciled_text != final_text:
            try:
                payload = json.loads(final_text)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, Mapping) and isinstance(payload.get("summary"), str):
                corrected, _ = reconcile_anti_fabrication(
                    payload["summary"],
                    executed_tools=run_evidence,
                    authored_artifacts=authored_count,
                    effective_profile=self.effective_profile.value,
                    workspace_root=self.workspace_root,
                )
                payload["summary"] = corrected
                reconciled_text = json.dumps(payload, ensure_ascii=False)

        metadata = dict(getattr(resp, "provider_metadata", {}) or {})
        if provider_body_empty:
            # The provider's final body was empty.  The run publishes the
            # truthful note, but the kernel records this round as a PROVIDER
            # failure so the run's stop_reason names the real cause.
            metadata["provider_body_empty"] = True
        if undispatched_tool_names:
            # The provider asked for more governed operations than the
            # iteration budget allowed, and those operations did NOT run.
            # Record that, so the run can never be read as having completed
            # work it skipped.
            metadata["undispatched_tool_calls"] = list(undispatched_tool_names)
            metadata["tool_iterations_used"] = iterations
        if withheld_reason:
            # Make the otherwise silent failure visible instead of letting the
            # user believe the model simply chose not to use a tool.
            metadata["tool_catalog_withheld"] = withheld_reason
            if not _is_structured_text(reconciled_text):
                # Only plain-language replies are annotated: appending a note to
                # a structured committee payload would corrupt the JSON contract.
                reconciled_text = (
                    reconciled_text.rstrip()
                    + "\n\n[AMCA: no governed tools were offered in this run - "
                    + withheld_reason
                    + "]"
                )

        # Gate-2 fix (manager decision 1a): a reply that is ONLY artifact
        # fences strips to empty text. Keep a minimal non-empty assistant
        # transcript entry naming what was authored, so the artifact never
        # silently vanishes from the conversation.
        if authored_titles and not reconciled_text.strip():
            reconciled_text = "Created artifact: " + ", ".join(authored_titles) + "."

        # A governed participant that finishes a tool exchange in prose
        # would otherwise be rejected by the committee JSON interchange and
        # the whole run would fail with "Response is not valid JSON".
        # Preserve the already-reconciled report without inventing events,
        # criterion evidence, consensus, or a completed focus.
        if (
            SEMANTIC_RESPONSE_INSTRUCTIONS in (request.system or "")
            and reconciled_text.strip()
            and not _is_structured_text(reconciled_text)
        ):
            reconciled_text = json.dumps({
                "summary": reconciled_text,
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": False,
            }, ensure_ascii=False)
            metadata["semantic_prose_normalized"] = True

        return replace(resp, text=reconciled_text, provider_metadata=metadata)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        # Generate with full governed tool loop, then stream the reconciled result
        resp = await self.generate(request)
        yield StreamChunk(delta=resp.text, done=True, response=resp)
