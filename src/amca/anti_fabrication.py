"""Fail-closed Anti-Fabrication Gate for AMCA Studio.

Enforces physical defect remediation requirements (Work Order 24A):
A final response must not assert that AMCA:
- read a file,
- wrote a file,
- renamed/moved/deleted a file,
- executed a command,
- created an artifact,
unless canonical run state contains the corresponding successful executed tool/artifact event.

When an operation is asserted without verified execution:
- The unsupported claim is intercepted and replaced with:
  "That operation was not executed."
- For file reads, returned content must originate from the tool result;
  model-generated invented content must not be presented as file contents.
- Truthful context replaces generic false refusals ("As an AI, I cannot access local files").
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

_GENERIC_REFUSAL_PATTERN = re.compile(
    r"(?i)\b(?:as\s+an\s+ai(?:(?:\s+language)?\s+model)?\s*,?\s*i\s+(?:cannot|can't|do\s+not\s+have|don't\s+have)\s+access\s*(?:to\s+)?(?:local\s+files?|your\s+file\s+system|local\s+system|files?\s+on\s+your\s+computer)?|i\s+(?:cannot|can't)\s+(?:directly\s+)?access\s+(?:local\s+files?|your\s+file\s+system|files?\s+on\s+your\s+computer))\b.*"
)

_WRITE_CLAIM_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"(?:i|we|amca)?\s*(?:have|'ve)?\s*(?:successfully\s+)?(?:created|written|wrote|saved|generated|modified|appended|updated)(?:\s+and\s+(?:created|written|wrote|saved|generated|modified|appended|updated))?\s+(?:to\s+)?(?:(?:a|an|the|this|that|new|all)\s+)?(?:[a-zA-Z0-9_.-]+\s+)*(?:file|script|code|implementation|[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8})\b|"
    r"(?:(?:a|an|the|this|that|new)\s+)?(?:[a-zA-Z0-9_.-]+\s+)?file\s+(?:was|has\s+been|is)\s+(?:successfully\s+)?(?:created|written|saved|generated|modified|appended|updated)\b|"
    r"file\s+created\b"
    r")"
)

_MOVE_DELETE_CLAIM_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"(?:i|we|amca)?\s*(?:have|'ve)?\s*(?:successfully\s+)?(?:renamed|moved|deleted|removed|trashed)\s+(?:(?:a|an|the|this|that|old|obsolete)\s+)?(?:[a-zA-Z0-9_.-]+\s+)*(?:file|script|[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8})\b|"
    r"(?:(?:a|an|the|this|that|old|obsolete)\s+)?(?:[a-zA-Z0-9_.-]+\s+)?file\s+(?:was|has\s+been|is)\s+(?:successfully\s+)?(?:renamed|moved|deleted|removed|trashed)\b"
    r")"
)

_COMMAND_CLAIM_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"(?:i|we|amca)?\s*(?:have|'ve)?\s*(?:successfully\s+)?(?:executed|run)\s+(?:the\s+)?command\b|"
    r"(?:the\s+)?command\s+(?:was|has\s+been|is)\s+(?:successfully\s+)?(?:executed|run)\b"
    r")"
)

_ARTIFACT_CLAIM_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"(?:i|we|amca)?\s*(?:have|'ve)?\s*(?:successfully\s+)?(?:created|generated|authored)\s+(?:(?:a|an|the|this|that)\s+)?(?:docx|pptx|xlsx|artifact)\b|"
    r"(?:(?:a|an|the|this|that)\s+)?(?:docx|pptx|xlsx|artifact)\s+(?:was|has\s+been|is)\s+(?:successfully\s+)?(?:created|generated|authored)\b"
    r")"
)

_FILE_READ_CLAIM_PATTERN = re.compile(
    r"(?i)(?:\b(?:here\s+is\s+the\s+contents?\s+of\s+['\"`]?(?P<fname1>[a-zA-Z0-9_.-]+)['\"`]?|"    r"contents?\s+of\s+['\"`]?(?P<fname2>[a-zA-Z0-9_.-]+)['\"`]?\s+(?:are|is|contains?)|"    r"reading\s+['\"`]?(?P<fname3>[a-zA-Z0-9_.-]+)['\"`]?|"    r"i\s+(?:have\s+)?read\s+['\"`]?(?P<fname4>[a-zA-Z0-9_.-]+)['\"`]?)\b)"
)

_OPERATION_NOT_EXECUTED = "That operation was not executed."
_OPERATION_UNVERIFIED = "That operation was not executed or could not be verified."

#: Outcome vocabulary accepted as proof that an operation actually completed,
#: used by BOTH evidence loops so the adapter-level gate and the publication
#: gate can never drift into different definitions of success.  Deliberately
#: narrow: "allowed" is an authorisation/audit word (the local executor records
#: it when a grant resolves an action), not a statement that the mutation ran,
#: so it never credits a completion claim.  "succeeded" is included because it
#: is the executor's own audit wording.
_OP_SUCCESS_STATES = frozenset({"success", "succeeded", "completed"})
_OP_SUCCESS_OUTCOMES = _OP_SUCCESS_STATES

#: Semantic success-assertion detection (26C DEFECT-13).
#: A success assertion is ANY sentence that couples an operation concept
#: (write/create/save/read/move/delete/command/artifact/operation/...) or a
#: file-like token with a success predicate ("succeeded", "success",
#: "worked", "completed", "done", "in place", ...).  Model wording alone is
#: never sufficient evidence: when the run contains no matching successful
#: event, the assertion is downgraded to truthful language.  This replaces
#: enumerating individual phrasings - the contract is claim-class vs evidence,
#: not keyword spotting.
_SUCCESS_PREDICATE_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"succeed(?:s|ed|ing)?|success(?:ful|fully)?|"
    r"(?:was|is|has\s+been|were|are)\s+successful|"
    r"worked(?:\s+fine)?|complet(?:e|ed|ion)(?:\s+successfully)?|"
    r"done|finished(?:\s+successfully)?|went\s+through|in\s+place|"
    r"(?:was|is|has\s+been)\s+(?:written|created|saved|generated|modified|updated|appended|executed|performed)|"
    r"(?:I|we|AMCA)\s+(?:successfully\s+)?(?:wrote|created|saved|generated|modified|updated|appended|executed|performed)|"
    r"no\s+(?:errors?|issues?|problems?)\s*(?:occurred|encountered|reported)?"
    r")\b"
)

_NEGATED_OPERATION_PATTERN = re.compile(
    r"(?i)\b(?:not|never|cannot|unable\s+to|\w+n['’]t)\s+"
    r"(?:(?:be|been|have|has|was|were|is|are|being|successfully|yet)\s+)*"
    r"(?:succeed(?:s|ed|ing)?|successful(?:ly)?|complet(?:e|ed|ion)|"
    r"done|finished|worked|written|wrote|write|created|create|saved|save|"
    r"generated|generate|modified|modify|updated|update|appended|append|"
    r"executed|execute|performed|perform|authored|author|exported|export|"
    r"renamed|rename|moved|move|deleted|delete|removed|remove|trashed|trash|"
    r"read|run|ran|verified|verify)\b(?:\s+successfully\b)?"
)

_OPERATION_NOUN_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"writes?|writ(?:ten|ing)|creates?|creation|saved?|saving|"
    r"reads?|written|moves?|moved|cop(?:y|ied)|delet(?:e|ed|ion)|renam(?:e|ed)|"
    r"commands?|execut(?:e|ed|ion)|operations?|actions?|tasks?|"
    r"artifacts?|docx|pptx|xlsx|"
    r"file|script|code|directory|folder|grant"
    r")\b"
)

#: Read-content assertion.  A sentence that states a named file's contents
#: ("feed-check.txt contains FEED-CHECK-OK", "the log shows errors") is a
#: read claim even with no success verb — it presents file contents the run
#: never returned.  Without this, hallucinated file contents publish
#: verbatim because no success predicate fires.  Bug-hunt R5 catch: content
#: assertions must bind to same-file successful read evidence.
_READ_CONTENT_CLAIM_PATTERN = re.compile(
    r"(?i)(?:\b[\w.-]+\.(?:txt|md|json|csv|log|py|js|ts|html|css|xml|ya?ml|ini|cfg|docx?|xlsx?|pptx?|pdf|zip)\b"
    r"\s+(?:contains?|shows?|reads?|says?|lists?|holds?|has|includes?)\b)"
)

_FILE_TOKEN_PATTERN = re.compile(
    r"(?i)(?:\b[\w.-]+\.(?:txt|md|json|csv|log|py|js|ts|html|css|xml|ya?ml|ini|cfg|docx?|xlsx?|pptx?|pdf|zip)\b)"
)

#: Strong operation-success language.  In a governed turn, these phrases assert
#: that an operation went through even when no operation noun is present, so a
#: generic success class applies ("went through without errors", "completed
#: successfully").  Weak standalone words ("done", "great") stay unclassified to
#: avoid rewriting ordinary conversation.
_STRONG_SUCCESS_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"succeed(?:s|ed|ing)?|success(?:ful|fully)?|"
    r"went\s+through|without\s+(?:any\s+)?errors?|no\s+(?:errors?|issues?|problems?)\s*(?:occurred|encountered|reported)?|"
    r"(?:was|is|has\s+been|were|are)\s+(?:successfully\s+)?completed|"
    r"completed\s+successfully|executed\s+successfully|"
    r"operation\s+(?:was|is)\s+a\s+success"
    r")\b"
)


def _is_structured_payload(text: str) -> bool:
    """True when the reply is a structured payload that must not be rewritten.

    JSON, an array, or a fenced block is machine-shaped output.  Rewriting a
    line inside it corrupts the syntax, so the reconciler replaces the whole
    payload with the truthful sentence instead of editing it line by line.
    """
    stripped = str(text or "").lstrip()
    return stripped.startswith(("{", "[", "```"))


def _affirmative_claim_text(segment: str) -> str:
    """Mask only a negated verb phrase, never the rest of its sentence.

    The original text remains the response; this view is used only for claim
    detection so an independent affirmative claim still requires evidence.
    """
    return _NEGATED_OPERATION_PATTERN.sub(lambda match: " " * len(match.group()), segment)


def _split_sentences(text: str) -> list[str]:
    """Split into sentence-ish segments while preserving delimiters."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part for part in parts if part.strip()]


#: Clause boundaries inside one sentence.  A compound sentence names different
#: objects for different verbs ("I read notes.txt and created the summary
#: file"), so each clause must be judged against the evidence for ITS OWN
#: object.  Judging the whole sentence at once required the write evidence to
#: cover the read's target and downgraded a fully verified report to "That
#: operation was not executed." (false negative, not a security property).
_CLAUSE_BOUNDARY_PATTERN = re.compile(r"(?i)\s*(?:,|;|\band\b|\bthen\b)\s*")


def _claim_clauses(segment: str) -> list[str]:
    """Split a claim sentence into clauses; the sentence itself when unsplittable."""
    clauses = [part.strip() for part in _CLAUSE_BOUNDARY_PATTERN.split(segment)]
    return [clause for clause in clauses if clause] or [segment]


def _asserted_targets(segment: str) -> set[str]:
    """Canonical filenames explicitly referenced by a claim segment."""
    targets: set[str] = set()
    for match in _FILE_TOKEN_PATTERN.finditer(segment):
        targets.add(_canonical_name(match.group(0)))
    for match in re.finditer(
        r"(?i)(?:file|named|called)\s+['\"`]*([a-zA-Z0-9_.\-]{1,64})['\"`]*",
        segment,
    ):
        name = match.group(1)
        if name and "." in name:
            targets.add(_canonical_name(name))
    return targets

def _canonical_name(path_str: str) -> str:
    cleaned = path_str.strip().replace("\\", "/").rstrip("/")
    if "/" in cleaned:
        cleaned = cleaned.rsplit("/", 1)[-1]
    return cleaned.casefold()


def _safe_denial_reason(value: Any, limit: int = 200) -> str:
    """Render a denial reason as one inert line of plain text.

    The reason can originate from a tool error string, and a provider can put
    arbitrary text into a tool result.  It is therefore stripped of newlines
    and non-printable characters and length-bounded before it is shown next to
    the refusal, so a model cannot plant formatting or multi-line text of its
    own in the trusted reply channel.
    """

    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(text.split())[:limit].strip()


def _denial_text(denied_tools: Sequence[Mapping[str, Any]], effective_profile: str | None) -> str:
    """Truthful denial language reflecting the last governance decision."""
    if denied_tools:
        last_denial = denied_tools[-1]
        denial_reason = _safe_denial_reason(
            last_denial.get("content")
            or last_denial.get("detail")
            or last_denial.get("error")
            or ""
        )
        lowered = denial_reason.lower()
        if "approval required" in lowered or "approval_required" in lowered:
            return (
                f"{_OPERATION_NOT_EXECUTED} Human approval is required before this file mutation executes."
            )
        if "restricted" in lowered or effective_profile == "restricted":
            return f"{_OPERATION_NOT_EXECUTED} Operation blocked by Restricted profile."
        if denial_reason:
            return f"{_OPERATION_NOT_EXECUTED} ({denial_reason})"
    return _OPERATION_NOT_EXECUTED

def reconcile_anti_fabrication(
    text: str,
    *,
    executed_tools: Sequence[Mapping[str, Any]] = (),
    authored_artifacts: Sequence[Mapping[str, Any]] | int = 0,
    effective_profile: str | None = None,
    workspace_root: str | None = None,
    executed_ops: Sequence[str | Mapping[str, Any]] | None = None,
) -> tuple[str, bool]:
    """Reconcile model claims against canonical executed tool and artifact events."""
    if not text or not text.strip():
        return text, False

    altered = False
    result_text = text

    successful_writes: set[str] = set()
    successful_reads: dict[str, str] = {}
    successful_moves: set[str] = set()
    successful_deletions: set[str] = set()
    successful_commands: set[str] = set()
    denied_tools: list[Mapping[str, Any]] = []

    if executed_ops:
        for op in executed_ops:
            if isinstance(op, str):
                # Two ledger spellings exist: canonical "write:path" and
                # the display "filesystem.write · path · success" form
                # (the Tools chips / _summary_field text).  Both carry
                # action + target, so normalize the display spelling to
                # the canonical parse — otherwise display-only evidence
                # is silently unevidenced and verified claims downgrade
                # (bug-hunt R5 catch).  Security R5 catch: the display
                # spelling's trailing state is AUTHORITATIVE like the
                # Mapping branch's — a "denied" display string must never
                # credit evidence.  Only stateless legacy strings
                # ("write:plan.txt") default to credited.
                display_state: str | None = None
                normalized = op
                if ":" not in op and " · " in op:
                    segments = [seg.strip() for seg in op.split(" · ")]
                    head = segments[0].lower()
                    if "." in head:
                        head = head.rsplit(".", 1)[-1]
                    action_part = head
                    if len(segments) >= 3 and segments[-1].lower() in (
                        _OP_SUCCESS_STATES | _OP_SUCCESS_OUTCOMES
                    ):
                        display_state = "success"
                        target_part = segments[-2]
                    elif len(segments) >= 2 and segments[-1]:
                        last = segments[-1].lower()
                        veto_words = (
                            "denied", "den", "refus", "block", "forbid",
                            "reject", "fail", "error", "approv", "grant",
                            "restrict", "cancel", "timeout", "expir",
                        )
                        if any(word in last for word in veto_words):
                            # A vetoing display state authorizes nothing —
                            # same rule as a vetoing Mapping state.
                            denied_tools.append(
                                {"denied_display_op": op[:200]}
                            )
                            continue
                        target_part = segments[-1]
                    else:
                        target_part = ""
                    normalized = f"{action_part}:{target_part}"
                    _ = display_state
                parts = normalized.split(":", 1)
                op_action = parts[0].lower()
                op_target = parts[1] if len(parts) > 1 else ""
                op_norm = _canonical_name(op_target) if op_target else ""
            elif isinstance(op, Mapping):
                op_action = str(op.get("action") or op.get("name") or "").lower()
                op_target = str(op.get("path") or op.get("target") or "")
                op_norm = _canonical_name(op_target) if op_target else ""
                # v1.1.9: a mapping that carries an outcome is authoritative
                # about it.  Crediting "write -> plan.txt" while its own state
                # reads approval_required would let a run report a gated
                # overwrite as a completed one.  Entries with no state at all
                # (the legacy "write:plan.txt" provenance strings) are
                # unchanged: absence of an outcome is not evidence of failure.
                op_state = str(op.get("state") or op.get("status") or "").lower()
                op_outcome = str(op.get("outcome") or "").lower()
                if op_state:
                    # A state is a decision about the operation itself, so it
                    # decides alone: a vetoing state is never overturned by an
                    # outcome field, which describes authorisation/audit
                    # bookkeeping ("allowed") rather than completion.
                    op_is_success = op_state in _OP_SUCCESS_STATES
                elif op_outcome:
                    op_is_success = op_outcome in _OP_SUCCESS_OUTCOMES
                else:
                    op_is_success = True
                if not op_is_success:
                    denied_tools.append(op)
                    continue
            else:
                continue

            if op_action in ("write", "create", "append", "mkdir"):
                if op_norm:
                    successful_writes.add(op_norm)
                successful_writes.add("__any__")
            elif op_action == "read":
                # Content provenance: a read claim about a named file must
                # trace to a successful read of THAT file.  "__any__" is only
                # class-level evidence for target-less read references.
                if op_norm:
                    successful_reads[op_norm] = ""
                else:
                    successful_reads["__any__"] = ""
            elif op_action in ("move", "copy"):
                if op_norm:
                    successful_moves.add(op_norm)
                successful_moves.add("__any__")
            elif op_action == "trash":
                if op_norm:
                    successful_deletions.add(op_norm)
                successful_deletions.add("__any__")
            elif op_action in ("run", "inspect", "command"):
                successful_commands.add(op_target or "__any__")

    for tool in executed_tools:
        action = str(tool.get("action") or tool.get("name") or "").lower()
        if action.startswith("filesystem."):
            action = action.removeprefix("filesystem.")
        elif action.startswith("command."):
            action = action.removeprefix("command.")

        state = str(tool.get("state") or tool.get("status") or "").lower()
        outcome = str(tool.get("outcome") or "").lower()
        # Same rule as the executed_ops branch: a state is a decision about
        # the operation itself and decides alone, so a vetoing state is never
        # overturned by an outcome field.  "allowed" is authorisation
        # bookkeeping and never proves a mutation ran.  Sharing the constants
        # keeps the adapter-level gate and the publication gate from drifting
        # into different success vocabularies.  With neither field recorded
        # there is no evidence of success, as before.
        is_success = (
            state in _OP_SUCCESS_STATES
            if state
            else outcome in _OP_SUCCESS_OUTCOMES
        )
        is_denied = state in ("denied", "approval_required", "execution_error") or outcome in ("denied", "blocked")

        target_path = str(tool.get("path") or tool.get("target") or tool.get("source") or "")
        norm_target = _canonical_name(target_path) if target_path else ""

        if is_success:
            if action in ("write", "create", "append", "mkdir"):
                if norm_target:
                    successful_writes.add(norm_target)
                successful_writes.add("__any__")
            elif action == "read":
                # Content provenance: a read claim about a named file must
                # trace to a successful read of THAT file.  "__any__" is only
                # class-level evidence for target-less read references.
                content = str(tool.get("content") or tool.get("output") or "")
                if norm_target:
                    successful_reads[norm_target] = content
                else:
                    successful_reads["__any__"] = content
            elif action in ("move", "copy"):
                if norm_target:
                    successful_moves.add(norm_target)
                successful_moves.add("__any__")
            elif action == "trash":
                if norm_target:
                    successful_deletions.add(norm_target)
                successful_deletions.add("__any__")
            elif action in ("run", "inspect"):
                successful_commands.add(str(tool.get("argv") or "__any__"))
        elif is_denied:
            denied_tools.append(tool)

    artifact_count = authored_artifacts if isinstance(authored_artifacts, int) else len(authored_artifacts)

    # 1. Check for generic refusal falsehood ("As an AI, I cannot access local files")
    if _GENERIC_REFUSAL_PATTERN.search(result_text):
        profile_label = (effective_profile or "restricted").replace("_", " ").title()
        if not workspace_root:
            truthful_reason = (
                "AMCA Studio includes a permissioned Local PC tool architecture, "
                "but no workspace root is configured for this Project. "
                "Configure a workspace root in Local PC & Permissions to enable local file tools."
            )
        elif effective_profile == "restricted":
            truthful_reason = (
                "AMCA Studio operates under the Restricted Local PC profile for this Project, "
                "which allows only governed read-only inspection inside the configured workspace root."
            )
        else:
            truthful_reason = (
                f"AMCA Studio operates under the {profile_label} Local PC profile. "
                "Access requires a valid workspace root and appropriate security grants."
            )
        result_text = _GENERIC_REFUSAL_PATTERN.sub(truthful_reason, result_text)
        altered = True

    def _evidence_for(claim_class: str, targets: set[str]) -> bool:
        """Evidence-bound claim check (26C DEFECT-13 contract).

        A named-target claim is authorized only by a successful event whose
        canonical target matches one of the claimed names.  A claim without a
        specific target is authorized only by at least one successful event of
        the same operation class.  A successful event for another path never
        authorizes an unrelated named success claim.
        """
        if claim_class == "write":
            evidence = successful_writes
        elif claim_class == "read":
            evidence = set(successful_reads)
        elif claim_class == "move":
            evidence = successful_moves
        elif claim_class == "delete":
            evidence = successful_deletions
        elif claim_class == "command":
            evidence = successful_commands
        elif claim_class == "artifact":
            # 28A: artifact claims bind to authored artifact records, and to
            # verified filesystem creates when the claim names a target that a
            # successful create actually produced (real file on disk).
            if artifact_count > 0:
                return True
            if targets:
                return any(target in successful_writes for target in targets)
            return "__any__" in successful_writes
        else:  # operation-generic: any successful event is required
            return bool(
                successful_writes
                or successful_reads
                or successful_moves
                or successful_deletions
                or successful_commands
                or artifact_count > 0
            )
        if targets:
            return any(target in evidence for target in targets)
        return "__any__" in evidence

    def _claim_class(segment: str) -> str | None:
        """Classify a segment's operation SUCCESS claim, or None when none.

        Only segments that assert success (success predicate) are classified;
        denial or failure statements are never rewritten by the semantic pass.
        """
        if re.search(r"(?i)\b(?:artifact|docx|pptx|xlsx)\b", segment) and re.search(
            r"(?i)\b(?:created|generated|authored|written|saved|exported)\b", segment
        ):
            # 28A: a bare past-participle over an artifact type ("Artifact
            # created.", "DOCX generated.") asserts artifact success without
            # an auxiliary verb — always classified as an artifact claim.
            return "artifact"
        if not _SUCCESS_PREDICATE_PATTERN.search(segment):
            return None
        if re.search(r"(?i)\b(?:artifact|docx|pptx|xlsx)\b", segment):
            return "artifact"
        if re.search(r"(?i)\b(?:command|execut(?:e|ed|ion)|script|ran)\b", segment):
            return "command"
        if re.search(r"(?i)\b(?:renam|mov|delet|trash|remov)\w*\b", segment):
            return "move"
        if re.search(r"(?i)\b(?:write|wrote|written|creat|sav|generat|updat|append|modif)\w*\b", segment):
            return "write"
        # Bug-hunt R5 catch: a sentence stating a named file's contents
        # ("feed-check.txt contains FEED-CHECK-OK") is a read claim even
        # with no success verb — it presents file contents the run never
        # returned.  Binds to same-file successful read evidence.
        if _READ_CONTENT_CLAIM_PATTERN.search(segment):
            return "read"
        if _OPERATION_NOUN_PATTERN.search(segment) or _FILE_TOKEN_PATTERN.search(segment):
            # A success predicate over a generic operation/file concept with no
            # specific verb class: require same-class-generic evidence.
            return "generic"
        if _STRONG_SUCCESS_PATTERN.search(segment):
            # Strong operation-success language without an operation noun
            # ("went through without errors"): evidence-bound generic claim.
            return "generic"
        return None

    def _every_claim_clause(text: str, pattern: re.Pattern[str], predicate) -> bool:
        """True when every clause carrying this claim is backed by its own evidence.

        Targets are read from the matching clause only, so a verified write is
        never condemned by a read target that appears elsewhere in the same
        sentence.  When the pattern only matches across clause boundaries the
        whole text is judged, which is the strict historical behavior.
        """
        matched = False
        for clause in _claim_clauses(text):
            if not pattern.search(clause):
                continue
            matched = True
            if not predicate(clause):
                return False
        if not matched:
            return bool(predicate(text))
        return True

    def _line_unverified(line: str) -> str | None:
        """Return truthful replacement when a line asserts an unverifiable operation.

        Each clause is classified and judged separately; a clause that asserts
        success without matching evidence condemns the line, and a verified
        clause never borrows another clause's failure (or vice versa).
        """
        stripped = line.strip()
        if not stripped:
            return None
        clauses = _claim_clauses(stripped)
        classified_any = False
        for clause in clauses:
            segment_class = _claim_class(clause)
            if segment_class is None:
                continue
            classified_any = True
            if not _evidence_for(segment_class, _asserted_targets(clause)):
                return _OPERATION_UNVERIFIED
        if classified_any:
            return None
        segment_class = _claim_class(stripped)
        if segment_class is None:
            return None
        if _evidence_for(segment_class, _asserted_targets(stripped)):
            return None
        return _OPERATION_UNVERIFIED

    # 2. Legacy explicit claim patterns (defense in depth) + semantic pass.
    #    Every success-asserting line must be backed by matching canonical
    #    evidence; unsupported claims are downgraded to truthful language.
    lines = result_text.splitlines()
    reconciled_lines: list[str] = []
    replaced_any = False
    first_replacement: str | None = None
    for line in lines:
        claim_text = _affirmative_claim_text(line)
        replacement: str | None = None
        if _WRITE_CLAIM_PATTERN.search(claim_text) and not _every_claim_clause(
            claim_text,
            _WRITE_CLAIM_PATTERN,
            lambda clause: _evidence_for("write", _asserted_targets(clause)),
        ):
            replacement = _denial_text(denied_tools, effective_profile)
        if replacement is None and _MOVE_DELETE_CLAIM_PATTERN.search(
            claim_text
        ) and not _every_claim_clause(
            claim_text,
            _MOVE_DELETE_CLAIM_PATTERN,
            lambda clause: _evidence_for("move", _asserted_targets(clause))
            or _evidence_for("delete", _asserted_targets(clause)),
        ):
            replacement = _OPERATION_NOT_EXECUTED
        if replacement is None and _COMMAND_CLAIM_PATTERN.search(
            claim_text
        ) and not _every_claim_clause(
            claim_text,
            _COMMAND_CLAIM_PATTERN,
            lambda clause: _evidence_for("command", set()),
        ):
            replacement = _OPERATION_NOT_EXECUTED
        if replacement is None and _ARTIFACT_CLAIM_PATTERN.search(
            claim_text
        ) and not _every_claim_clause(
            claim_text,
            _ARTIFACT_CLAIM_PATTERN,
            lambda clause: _evidence_for("artifact", set()),
        ):
            replacement = _OPERATION_NOT_EXECUTED
        if replacement is None:
            replacement = _line_unverified(claim_text)
        if replacement is not None:
            reconciled_lines.append(replacement)
            if first_replacement is None:
                first_replacement = replacement
            replaced_any = True
            altered = True
        else:
            reconciled_lines.append(line)
    if replaced_any:
        if _is_structured_payload(text):
            # Never hand the user a half-rewritten JSON document: editing a line
            # inside machine-shaped output corrupts the syntax and the reader
            # sees broken punctuation instead of an answer.  The
            # anti-fabrication guarantee is kept by replacing the whole payload
            # with the truthful sentence.
            return (first_replacement or _OPERATION_UNVERIFIED), True
        result_text = "\n".join(reconciled_lines).strip()
        if not result_text or result_text in (_OPERATION_NOT_EXECUTED, _OPERATION_UNVERIFIED):
            return _OPERATION_NOT_EXECUTED, True

    # 6. Check for unexecuted / fabricated file read claims — both the
    # legacy "here is the contents of X" phrasing AND bare content
    # assertions ("f.txt contains <content>"), which present file
    # contents the run never returned.  The semantic pass above already
    # handles read-content claims that carry a success predicate; this
    # catches predicate-free content assertions.  Bug-hunt R5 catch:
    # hallucinated file contents are the highest-harm claim class, so
    # the check binds to same-file successful read evidence.
    content_match = None
    read_match = _FILE_READ_CLAIM_PATTERN.search(result_text)
    if not read_match:
        content_match = _READ_CONTENT_CLAIM_PATTERN.search(result_text)
    read_match = read_match or content_match
    if read_match:
        try:
            target_file = (
                read_match.group("fname1")
                or read_match.group("fname2")
                or read_match.group("fname3")
                or read_match.group("fname4")
                or ""
            )
        except IndexError:
            # _READ_CONTENT_CLAIM_PATTERN has no named groups — the file
            # token is the match head.
            target_file = (read_match.group(0) or "").split()[0]
        norm_file = _canonical_name(target_file) if target_file else ""
        if norm_file and norm_file not in successful_reads and "__any__" not in successful_reads:
            if denied_tools:
                last_denial = denied_tools[-1]
                # Sanitised for the same reason as _denial_text: this string
                # comes from a tool error or a provider result, so it must not
                # be able to steer which explanation the user is shown.
                denial_reason = _safe_denial_reason(last_denial.get("content"))
                if "outside" in denial_reason.lower():
                    result_text = f"{_OPERATION_NOT_EXECUTED} Path '{target_file}' is outside allowed scope."
                elif "grant" in denial_reason.lower():
                    result_text = f"{_OPERATION_NOT_EXECUTED} Explicit grant required to read '{target_file}'."
                elif "restricted" in denial_reason.lower():
                    result_text = f"{_OPERATION_NOT_EXECUTED} Operation blocked by Restricted profile."
                else:
                    result_text = _OPERATION_NOT_EXECUTED
            else:
                result_text = _OPERATION_NOT_EXECUTED
            altered = True

    return result_text, altered


def reconcile_refusal(text: str) -> str:
    """Rewrite generic false AI refusals to truthful AMCA tool architecture context."""
    res, _ = reconcile_anti_fabrication(text)
    return res


def sanitize_file_read_output(content: str, was_executed: bool = False) -> str:
    """Sanitize invented file content for unexecuted or failed reads."""
    if not was_executed:
        return "The file content could not be read. That operation was not executed."
    return content
