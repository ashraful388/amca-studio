"""M0 feature-gate registry for v0.5.0 intelligence behaviors.

Additive module introduced by the M0 Contract Foundation milestone. All gates
default to FALSE and the registry is passive: nothing in the pre-existing
application reads it, there is no activation path, and enabling a gate in
memory changes no existing behavior (contracts spec section 8: disabling the
gates routes work to the accepted deterministic path).
"""

import dataclasses
from typing import Any, Mapping

from .errors import ContractError, ContractErrorCode


@dataclasses.dataclass(frozen=True)
class FeatureGateRegistry:
    """Immutable default-OFF registry of v0.5.0 intelligence gates."""

    adaptive_planner: bool = False
    hybrid_execution: bool = False
    nano_agents: bool = False
    model_router: bool = False

    def is_enabled(self, gate: str) -> bool:
        """Return the passive state of one gate; unknown names fail closed."""

        value = getattr(self, gate, None)
        if not isinstance(value, bool):
            raise ContractError(
                ContractErrorCode.INVALID, f"unknown feature gate {gate!r}"
            )
        return value

    def with_overrides(self, **overrides: bool) -> "FeatureGateRegistry":
        """Return a new registry with the named gates replaced.

        Unknown gate names and non-boolean values are rejected so a typo can
        never silently enable or create behavior.
        """

        known = {field.name for field in dataclasses.fields(self)}
        for name, value in overrides.items():
            if name not in known:
                raise ContractError(
                    ContractErrorCode.INVALID, f"unknown feature gate {name!r}"
                )
            if not isinstance(value, bool):
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"feature gate {name!r} must be a boolean",
                )
        return dataclasses.replace(self, **overrides)

    def to_dict(self) -> dict:
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}


#: The shipped default: every v0.5.0 intelligence behavior disabled.
DEFAULT_GATES = FeatureGateRegistry()

_GATE_FIELDS = tuple(field.name for field in dataclasses.fields(FeatureGateRegistry))


def gates_from_dict(data: Mapping[str, Any]) -> FeatureGateRegistry:
    """Rebuild a registry from its serialized form, failing closed.

    Unknown gate names and non-boolean values are rejected rather than
    ignored, so an out-of-date or tampered record can never widen behavior.
    """

    if not isinstance(data, Mapping):
        raise ContractError(
            ContractErrorCode.INVALID, "feature gate record must be an object"
        )
    kwargs = {}
    for name, value in data.items():
        if name not in _GATE_FIELDS:
            raise ContractError(
                ContractErrorCode.INVALID, f"unknown feature gate {name!r}"
            )
        if not isinstance(value, bool):
            raise ContractError(
                ContractErrorCode.INVALID, f"feature gate {name!r} must be a boolean"
            )
        kwargs[name] = value
    return FeatureGateRegistry(**kwargs)
