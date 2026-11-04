"""Compact, deterministic context construction for model calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from amca.domain import ProjectState, Severity


@dataclass(frozen=True, slots=True)
class ContextPacket:
    state_version: int
    cognitive_revision: int
    stable_context: str
    canonical_state: str
    recent_delta: str
    current_question: str

    def as_prompt(self) -> str:
        return "\n\n".join(
            (
                "# Stable project context\n" + self.stable_context,
                "# Canonical shared state\n" + self.canonical_state,
                "# Recent semantic events\n" + self.recent_delta,
                "# Active focus\n" + self.current_question,
            )
        )


class ContextBuilder:
    """Select structured conclusions and evidence instead of replaying a chat."""

    def __init__(self, *, max_items_per_section: int = 12, recent_event_limit: int = 8):
        if max_items_per_section < 1 or recent_event_limit < 0:
            raise ValueError("context limits must be positive")
        self.max_items_per_section = max_items_per_section
        self.recent_event_limit = recent_event_limit

    def build(self, state: ProjectState) -> ContextPacket:
        stable = {
            "project_id": state.project_id,
            "goal": state.goal,
            "success_criteria": [
                {
                    "id": criterion.id,
                    "description": criterion.description,
                    "satisfied": criterion.satisfied,
                }
                for criterion in (state.goal_contract.criteria if state.goal_contract else [])
            ],
            "user_constraints": state.user_constraints[-self.max_items_per_section :],
            "privacy_mode": state.privacy_mode.value,
            "compute_policy": state.compute_policy.value,
        }
        canonical = {
            "version": state.version,
            "cognitive_revision": state.cognitive_revision,
            "active_subproblem": state.active_subproblem,
            "accepted_facts": state.accepted_facts[-self.max_items_per_section :],
            "assumptions": state.assumptions[-self.max_items_per_section :],
            "hypotheses": state.hypotheses[-self.max_items_per_section :],
            "evidence": state.evidence[-self.max_items_per_section :],
            "experimental_results": state.experimental_results[
                -self.max_items_per_section :
            ],
            "open_questions": state.open_questions[-self.max_items_per_section :],
            "critical_disagreements": [
                disagreement.to_dict()
                for disagreement in state.unresolved_disagreements
                if disagreement.severity in {Severity.HIGH, Severity.CRITICAL}
            ],
            "progress": state.progress,
        }
        recent = [
            {
                "type": event.event_type.value,
                "source": event.source,
                "payload": event.payload,
                "severity": event.severity.value,
                "state_version": event.state_version,
            }
            for event in state.recent_events[-self.recent_event_limit :]
        ]
        return ContextPacket(
            state_version=state.version,
            cognitive_revision=state.cognitive_revision,
            stable_context=self._dump(stable),
            canonical_state=self._dump(canonical),
            recent_delta=self._dump(recent),
            current_question=state.current_focus or state.goal,
        )

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
