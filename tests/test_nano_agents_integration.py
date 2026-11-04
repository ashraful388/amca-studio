"""Nano Agents integration fixtures for M3 closeout.

Deterministic fixtures that establish the Nano Agents runtime contract
that the frontend consumes via /api/nano-agents. These fixtures prove the
backend can deliver bounded operational metadata (Nano ID, root/parent,
assignment label, lifecycle status, timestamps, cancellation/failure
reason, result/disposition) without raw prompts, CoT, or credentials.
"""

import dataclasses
import json
import time
from typing import Any, Mapping, Sequence


@dataclasses.dataclass(frozen=True)
class NanoAssignmentFixture:
    """Bounded operational metadata for one Nano Agent assignment."""

    nano_id: str
    task_id: str
    run_ref: str
    parent_task_id: str
    depth: int
    purpose: str
    task_type: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    expected_cost: float
    correlation_id: str
    proposal_ref: str
    assignment_version: int


@dataclasses.dataclass(frozen=True)
class NanoResultFixture:
    """Bounded result metadata for one Nano Agent completion."""

    nano_id: str
    disposition: str
    summary: str
    output_digest: str
    capabilities_used: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class NanoMessageFixture:
    """Bounded operational message between Nano Agents."""

    sender_id: str
    recipient_id: str
    kind: str
    correlation_id: str
    summary: str


@dataclasses.dataclass(frozen=True)
class NanoEscalationFixture:
    """Bounded escalation record."""

    nano_id: str
    reason: str
    requested_authority: str
    granted: bool


#: Deterministic fixture data for integration testing
SAMPLE_ASSIGNMENTS: tuple[NanoAssignmentFixture, ...] = (
    NanoAssignmentFixture(
        nano_id="nano:test-1",
        task_id="task:test-1",
        run_ref="demo-run",
        parent_task_id="task:root",
        depth=1,
        purpose="Read a local document and summarize it",
        task_type="document_summarization",
        capabilities=("text", "reading"),
        permissions=("read_local_file",),
        expected_cost=0.0,
        correlation_id="corr-test-1",
        proposal_ref="prop-test-1",
        assignment_version=1,
    ),
    NanoAssignmentFixture(
        nano_id="nano:test-2",
        task_id="task:test-2",
        run_ref="demo-run",
        parent_task_id="task:test-1",
        depth=2,
        purpose="Extract metadata from the summary",
        task_type="metadata_extraction",
        capabilities=("text", "extraction"),
        permissions=(),
        expected_cost=0.0,
        correlation_id="corr-test-2",
        proposal_ref="prop-test-2",
        assignment_version=1,
    ),
)


SAMPLE_RESULTS: dict[str, NanoResultFixture] = {
    "nano:test-1": NanoResultFixture(
        nano_id="nano:test-1",
        disposition="complete",
        summary="Summary created",
        output_digest="abc123def456",
        capabilities_used=("text", "reading"),
        evidence_refs=("task:test-1",),
    ),
}


SAMPLE_MESSAGES: tuple[NanoMessageFixture, ...] = (
    NanoMessageFixture(
        sender_id="nano:test-1",
        recipient_id="nano:test-2",
        kind="evidence",
        correlation_id="corr-test-1",
        summary="Summary ready",
    ),
)


SAMPLE_ESCALATIONS: tuple[NanoEscalationFixture, ...] = (
    NanoEscalationFixture(
        nano_id="nano:test-2",
        reason="Needs network access for metadata enrichment",
        requested_authority="network",
        granted=False,
    ),
)


def build_frontend_payload() -> dict[str, Any]:
    """Build the payload that /api/nano-agents returns to the frontend.

    This is the contract the frontend NanoAgentsPanel consumes.
    """
    return {
        "assignments": [
            {
                "nano_id": a.nano_id,
                "task_id": a.task_id,
                "run_ref": a.run_ref,
                "parent_task_id": a.parent_task_id,
                "depth": a.depth,
                "purpose": a.purpose,
                "task_type": a.task_type,
                "capabilities": list(a.capabilities),
                "permissions": list(a.permissions),
                "expected_cost": a.expected_cost,
                "correlation_id": a.correlation_id,
                "proposal_ref": a.proposal_ref,
                "assignment_version": a.assignment_version,
            }
            for a in SAMPLE_ASSIGNMENTS
        ],
        "statuses": {
            "nano:test-1": "completed",
            "nano:test-2": "cancelled",
        },
        "results": {
            r.nano_id: {
                "disposition": r.disposition,
                "summary": r.summary,
                "output_digest": r.output_digest,
                "capabilities_used": list(r.capabilities_used),
                "evidence_refs": list(r.evidence_refs),
            }
            for r in SAMPLE_RESULTS.values()
        },
        "cancelled": ["nano:test-2"],
        "messages": [
            {
                "sender_id": m.sender_id,
                "recipient_id": m.recipient_id,
                "kind": m.kind,
                "correlation_id": m.correlation_id,
                "summary": m.summary,
            }
            for m in SAMPLE_MESSAGES
        ],
        "escalations": [
            {
                "nano_id": e.nano_id,
                "reason": e.reason,
                "requested_authority": e.requested_authority,
                "granted": e.granted,
            }
            for e in SAMPLE_ESCALATIONS
        ],
        "envelope": {
            "max_logical_agents": 64,
            "max_concurrent_agents": 8,
            "max_manager_depth": 2,
            "max_nano_tasks": 128,
            "max_cloud_calls": 16,
            "max_frontier_calls": 4,
            "max_api_cost_usd": 5.0,
            "max_runtime_seconds": 3600,
        },
        "run_cancelled": False,
        "gate_enabled": False,
    }


def assert_privacy_compliance(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Verify the payload contains only bounded operational metadata.

    Returns (is_compliant, list_of_violations). No raw prompts, CoT,
    credentials, or session tokens should be present.
    """
    violations = []

    forbidden_keys = {
        "prompt", "system_prompt", "raw_response", "raw_output",
        "reasoning", "chain_of_thought", "cot", "thinking",
        "api_key", "token", "session_token", "authorization",
        "model_request", "model_response", "request_body", "response_body",
        "secret", "credential",
    }

    def check_value(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k.lower() in forbidden_keys:
                    violations.append(f"Forbidden key at {path}.{k}")
                check_value(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                check_value(item, f"{path}[{i}]")

    check_value(payload, "payload")
    return (len(violations) == 0, violations)
