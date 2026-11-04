"""M0 v0.4.0 deterministic compatibility helpers.

Additive module introduced by the M0 Contract Foundation milestone. Implements
the compatibility requirements from docs/AMCA-v0.5.0-Intelligence-Contracts.md
section 2.4 and the M0 roadmap section: v0.4.0 decisions project into versioned
envelopes without changing their meaning, absent v0.5.0 fields resolve to
least-authority defaults, and old records replay without adaptive policy or
learned metadata.
"""

import dataclasses
import json
from typing import Any, ClassVar, Mapping

from .canonical import canonical_json, content_digest
from .contracts import Contract, ContractMetadata, contract_post_init
from .errors import ContractError, ContractErrorCode

#: Conservative defaults for absent v0.5.0 fields (spec 2.4: "absent v0.5
#: fields resolve to deterministic, least-authority defaults"). Every value
#: is the most restrictive choice: no adaptive, hybrid, nano, or routing
#: behavior, one round, no retries, no cloud or frontier calls, no children.
LEAST_AUTHORITY_DEFAULTS = {
    "adaptive_enabled": False,
    "hybrid_enabled": False,
    "nano_agents_enabled": False,
    "model_routing_evidence": False,
    "max_rounds": 1,
    "max_retries": 0,
    "cloud_calls": 0,
    "frontier_calls": 0,
    "child_agents": 0,
}


def least_authority_defaults() -> dict:
    """A fresh copy of the conservative default field set."""

    return dict(LEAST_AUTHORITY_DEFAULTS)


@dataclasses.dataclass(frozen=True)
class DeterministicDecisionProjection(Contract):
    """A v0.4.0 deterministic decision carried in a versioned envelope.

    The ``decision`` payload is preserved verbatim: the projection may add
    correlation, version, digest, and least-authority default metadata, but it
    may not silently change collaboration mode, model eligibility, cost
    admission, permission scope, or work-unit topology (spec 2.4).
    """

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.deterministic_decision_projection"

    meta: ContractMetadata
    source_record_kind: str
    decision: Mapping[str, Any]
    decision_digest: str
    defaults_applied: Mapping[str, Any]

    def __post_init__(self):
        # Normalize decision payloads to JSON-native data (tuples become
        # lists) so round-trips through from_dict compare equal and digests
        # remain stable across processes.
        object.__setattr__(self, "decision", _json_native(self.decision))
        object.__setattr__(
            self, "defaults_applied", _json_native(self.defaults_applied)
        )
        contract_post_init(self)

    def _validate(self):
        if self.decision_digest != content_digest(self.decision):
            raise ContractError(
                ContractErrorCode.INTEGRITY_VIOLATION,
                "projection decision_digest does not match the decision payload",
            )
        if self.defaults_applied != LEAST_AUTHORITY_DEFAULTS:
            raise ContractError(
                ContractErrorCode.INTEGRITY_VIOLATION,
                "projection defaults must equal the least-authority default set",
            )

    def decision_fields(self) -> dict:
        """The preserved v0.4.0 decision fields, verbatim."""

        return dict(self.decision)


def _json_native(value):
    normalized = json.loads(canonical_json(value))
    if not isinstance(normalized, dict):
        raise ContractError(
            ContractErrorCode.INVALID,
            "projection payloads must be objects",
        )
    return normalized


def project_v04_decision(
    decision: Mapping[str, Any],
    *,
    meta: ContractMetadata,
    source_record_kind: str,
) -> DeterministicDecisionProjection:
    """Project one v0.4.0 deterministic decision into a versioned envelope."""

    if not isinstance(decision, Mapping) or not decision:
        raise ContractError(
            ContractErrorCode.INVALID,
            "a v0.4.0 decision projection requires a non-empty decision object",
        )
    if not isinstance(source_record_kind, str) or not source_record_kind:
        raise ContractError(
            ContractErrorCode.INVALID,
            "source_record_kind must be a non-empty string",
        )
    return DeterministicDecisionProjection(
        meta=meta,
        source_record_kind=source_record_kind,
        decision=dict(decision),
        decision_digest=content_digest(decision),
        defaults_applied=dict(LEAST_AUTHORITY_DEFAULTS),
    )
