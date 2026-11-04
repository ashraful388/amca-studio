"""M1 planner evaluation evidence hooks (passive metadata only).

Additive module introduced by the M1 Adaptive Planner Foundation milestone
(zcode/amca-v0.5.0-m1-adaptive-planner-foundation-01). Records bounded,
attributable metadata about planner selection and fallback decisions:
planner type selected, fallback reason, evaluation status, and a whitelisted
subset of execution-strategy metadata. Hidden reasoning, chain-of-thought,
prompts, and model-private outputs are never captured, and no pre-existing
application code imports this package.
"""

import dataclasses
import enum
from typing import Any, ClassVar, Mapping, Optional

from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    PlannerResult,
    contract_post_init,
)


class PlannerEvaluationStatus(enum.Enum):
    """Bounded evaluation disposition of one planner decision."""

    NOT_EVALUATED = "not_evaluated"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    FELL_BACK = "fell_back"


#: The only execution-strategy fields evidence hooks may record. Everything
#: else about a proposed strategy (work-unit content, assignments, payloads)
#: stays out of evaluation evidence.
ALLOWED_STRATEGY_METADATA_KEYS = frozenset(
    {
        "planner_kind",
        "planner_version",
        "collaboration_mode",
        "synchronization_mode",
        "work_unit_count",
        "max_rounds",
        "max_total_cost",
        "stop_condition",
    }
)


def execution_strategy_metadata(result: Optional[PlannerResult]) -> Mapping[str, Any]:
    """Extract the whitelisted strategy metadata from a planner result.

    Non-proposed results and missing fields yield empty values; no strategy
    payload, assignment content, or free-form rationale is captured.
    """

    strategy = getattr(result, "strategy", None) if result is not None else None
    if strategy is None:
        return {}
    metadata: dict[str, Any] = {}
    for key in (
        "planner_kind",
        "planner_version",
        "collaboration_mode",
        "synchronization_mode",
        "stop_condition",
    ):
        value = getattr(strategy, key, None)
        if isinstance(value, str) and value:
            metadata[key] = value
    unit_count = len(getattr(strategy, "work_units", ()) or ())
    if unit_count:
        metadata["work_unit_count"] = unit_count
    for key in ("max_rounds", "max_total_cost"):
        value = getattr(strategy, key, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metadata[key] = value
    unexpected = set(metadata) - ALLOWED_STRATEGY_METADATA_KEYS
    if unexpected:
        raise ContractError(
            ContractErrorCode.INTEGRITY_VIOLATION,
            f"evaluation evidence metadata keys outside the whitelist: "
            f"{sorted(unexpected)}",
        )
    return metadata


@dataclasses.dataclass(frozen=True)
class PlannerEvaluationEvidence(Contract):
    """Passive evaluation metadata for one planner decision (M1 hook)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_evaluation_evidence"

    meta: ContractMetadata
    request_ref: str
    planner_type_selected: str
    evaluation_status: PlannerEvaluationStatus
    fallback: bool
    fallback_reason: Optional[str]
    strategy_metadata: Mapping[str, Any]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        unexpected = set(self.strategy_metadata) - ALLOWED_STRATEGY_METADATA_KEYS
        if unexpected:
            raise ContractError(
                ContractErrorCode.INTEGRITY_VIOLATION,
                f"evaluation evidence metadata keys outside the whitelist: "
                f"{sorted(unexpected)}",
            )
        if self.fallback and not self.fallback_reason:
            raise ContractError(
                ContractErrorCode.INVALID,
                "a fallback evidence record must carry a fallback reason",
            )
