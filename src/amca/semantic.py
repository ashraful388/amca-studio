"""Extraction of compact, reportable semantic events from model output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from amca.domain import CognitiveEvent, EventType, Severity


SEMANTIC_RESPONSE_INSTRUCTIONS = """Return only a JSON object with this shape:
{
  "summary": "concise reportable contribution, not hidden chain-of-thought",
  "events": [
    {
      "type": "hypothesis|objection|question|answer|alternative|correction|evidence|experiment_request|tool_request|consensus|blocker|critical_conflict",
      "content": "one precise claim, objection, question, or evidence statement",
      "severity": "info|low|medium|high|critical",
      "confidence": 0.0,
      "evidence_refs": [],
      "tool": "math (only for tool_request)",
      "operation": "evaluate (the only model-authorized operation in this slice)",
      "expression": "restricted mathematical expression",
      "symbol": "optional variable name",
      "criterion_id": "required when the tool result evaluates a goal criterion"
    }
  ],
  "criterion_evidence": [
    {"criterion_id": "id supplied in context", "evidence": "observable support"}
  ],
  "focus_resolved": false
}
Do not expose private chain-of-thought. State conclusions, assumptions, concise
rationale, objections, and evidence that teammates can inspect. Use ONLY the
exact event-type strings listed above; never invent new type names, and return
an empty "events" array whenever unsure. An objection must identify the
disputed claim and a check that could resolve it. Do not mark the focus
resolved while any critical objection lacks discriminating evidence.
"""


_ALLOWED_MODEL_EVENTS = {
    EventType.HYPOTHESIS,
    EventType.OBJECTION,
    EventType.QUESTION,
    EventType.ANSWER,
    EventType.ALTERNATIVE,
    EventType.CORRECTION,
    EventType.EVIDENCE,
    EventType.EXPERIMENT_REQUEST,
    EventType.TOOL_REQUEST,
    EventType.CONSENSUS,
    EventType.BLOCKER,
    EventType.CRITICAL_CONFLICT,
    EventType.ARCHITECTURE_PROPOSAL,
}


def is_model_authorable_event_type(event_type: EventType) -> bool:
    """Return whether the semantic extractor can accept this type from a model."""

    return event_type in _ALLOWED_MODEL_EVENTS


@dataclass(frozen=True, slots=True)
class SemanticContribution:
    source: str
    summary: str
    events: tuple[CognitiveEvent, ...]
    criterion_evidence: tuple[Mapping[str, str], ...]
    focus_resolved: bool
    raw_text: str
    structured: bool = True
    dropped_event_count: int = 0


class SemanticExtractionError(ValueError):
    pass


def _json_text(text: str) -> str:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return fenced.group(1) if fenced else stripped


class SemanticEventExtractor:
    """Validate the model-authored interchange format without inferring claims."""

    def __init__(self, *, maximum_events: int = 24, maximum_content_chars: int = 12_000):
        self.maximum_events = maximum_events
        self.maximum_content_chars = maximum_content_chars

    def extract(self, text: str, *, source: str, state_version: int) -> SemanticContribution:
        payload = _json_text(text)
        if not payload.strip():
            # An empty body is a DIFFERENT condition from a malformed one.
            # json.loads("") raises JSONDecodeError("Expecting value"), which
            # used to be reported as "Response is not valid JSON" and so
            # blamed the model's JSON formatting for a provider that returned
            # nothing at all (the installed failure of 2026-10-21, whose
            # transmission ledger recorded the SHA-256 of the empty string).
            raise SemanticExtractionError(
                "The provider returned an empty response"
            )
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SemanticExtractionError(f"Response is not valid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise SemanticExtractionError("Response must be a JSON object")
        summary = value.get("summary", "")
        if not isinstance(summary, str) or len(summary) > self.maximum_content_chars:
            raise SemanticExtractionError("summary must be a bounded string")
        raw_events = value.get("events", [])
        if not isinstance(raw_events, list) or len(raw_events) > self.maximum_events:
            raise SemanticExtractionError("events must be a bounded array")

        events: list[CognitiveEvent] = []
        dropped_event_count = 0
        for index, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, dict):
                raise SemanticExtractionError(f"event {index} must be an object")
            try:
                event_type = EventType(str(raw_event.get("type", "")))
            except ValueError:
                # Weak local models intermittently invent type names.  The
                # malformed event is dropped and counted so the caller can
                # record a visible audit trail; it is never integrated and
                # never destroys an otherwise-valid verified summary.
                dropped_event_count += 1
                continue
            if not is_model_authorable_event_type(event_type):
                dropped_event_count += 1
                continue
            content = raw_event.get("content")
            if not isinstance(content, str) or not content.strip():
                # CLOSURE-05B Part C: same policy as unknown type names — a
                # weak local model intermittently emits a well-typed event
                # with empty content.  The event carries no claim to lose,
                # so it is dropped and counted instead of condemning the
                # whole otherwise-valid contribution.
                dropped_event_count += 1
                continue
            if len(content) > self.maximum_content_chars:
                raise SemanticExtractionError(f"event {index} content is too long")
            try:
                severity = Severity(str(raw_event.get("severity", Severity.INFO.value)))
            except ValueError as exc:
                raise SemanticExtractionError(f"event {index} has an unknown severity") from exc
            confidence = raw_event.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                    raise SemanticExtractionError(f"event {index} confidence must be numeric")
                if not 0 <= float(confidence) <= 1:
                    raise SemanticExtractionError(f"event {index} confidence must be between 0 and 1")
            evidence_refs = raw_event.get("evidence_refs", [])
            if not isinstance(evidence_refs, list) or not all(isinstance(item, str) for item in evidence_refs):
                raise SemanticExtractionError(f"event {index} evidence_refs must be strings")
            payload: dict[str, Any] = {
                "content": content.strip(),
                "confidence": float(confidence) if confidence is not None else None,
                "evidence_refs": evidence_refs,
            }
            for optional in (
                "id",
                "criterion_id",
                "disagreement_id",
                "disputed_claim",
                "proposed_check",
                "target_model",
                "accepted_fact",
                "resolution",
                "tool",
                "operation",
                "expression",
                "symbol",
                "variables",
            ):
                if optional in raw_event:
                    payload[optional] = raw_event[optional]
            events.append(
                CognitiveEvent(
                    event_type=event_type,
                    source=source,
                    payload=payload,
                    state_version=state_version,
                    severity=severity,
                )
            )

        raw_criterion_evidence = value.get("criterion_evidence", [])
        if not isinstance(raw_criterion_evidence, list):
            raise SemanticExtractionError("criterion_evidence must be an array")
        criterion_evidence: list[Mapping[str, str]] = []
        for item in raw_criterion_evidence:
            if not isinstance(item, dict):
                raise SemanticExtractionError("criterion_evidence items must be objects")
            criterion_id = item.get("criterion_id")
            evidence = item.get("evidence")
            if not isinstance(criterion_id, str) or not isinstance(evidence, str):
                raise SemanticExtractionError("criterion evidence requires string fields")
            criterion_evidence.append({"criterion_id": criterion_id, "evidence": evidence})

        focus_resolved = value.get("focus_resolved", False)
        if not isinstance(focus_resolved, bool):
            raise SemanticExtractionError("focus_resolved must be boolean")
        return SemanticContribution(
            source=source,
            summary=summary.strip(),
            events=tuple(events),
            criterion_evidence=tuple(criterion_evidence),
            focus_resolved=focus_resolved,
            raw_text=text,
            dropped_event_count=dropped_event_count,
        )

    def extraction_error_event(
        self, error: Exception, *, source: str, state_version: int
    ) -> CognitiveEvent:
        return CognitiveEvent(
            event_type=EventType.MODEL_ERROR,
            source=source,
            payload={"stage": "semantic_extraction", "error": str(error)},
            state_version=state_version,
            severity=Severity.ERROR,
        )
