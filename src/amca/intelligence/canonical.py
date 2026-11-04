"""M0 canonical serialization, digest generation, and version rules.

Additive module introduced by the M0 Contract Foundation milestone. Implements
the contract-wide rules from docs/AMCA-v0.5.0-Intelligence-Contracts.md 1.1:
deterministic JSON-compatible records, content digests over
execution-significant inputs, and rejection of non-finite numbers and
unsupported contract versions.
"""

import dataclasses
import datetime as _dt
import enum
import hashlib
import json
import math
from typing import Any, Mapping

from .errors import ContractError, ContractErrorCode

#: Major contract versions this foundation understands. Records written by an
#: unsupported major version are rejected on read instead of executed under
#: guessed semantics (contracts spec 7.3).
SUPPORTED_CONTRACT_VERSIONS = frozenset({1})


def canonical_json(value: Any) -> str:
    """Serialize ``value`` to a deterministic JSON string.

    Object keys are sorted, separators are compact, and non-finite floats are
    rejected so the output is stable across processes and platforms.
    """

    def _check(node: Any) -> Any:
        if isinstance(node, enum.Enum):
            return _check(node.value)
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            return {
                field.name: _check(getattr(node, field.name))
                for field in dataclasses.fields(node)
            }
        if isinstance(node, float) and not math.isfinite(node):
            raise ContractError(
                ContractErrorCode.INVALID,
                "non-finite numbers are not valid contract data",
            )
        if isinstance(node, Mapping):
            return {str(key): _check(item) for key, item in node.items()}
        if isinstance(node, (list, tuple)):
            return [_check(item) for item in node]
        return node

    try:
        return json.dumps(
            _check(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except ValueError as error:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"value is not deterministically serializable: {error}",
        ) from error


def content_digest(value: Any) -> str:
    """SHA-256 hex digest of the canonical JSON form of ``value``."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_supported_version(data: Mapping[str, Any]) -> int:
    """Validate ``contract_version`` in a serialized record and return it."""

    version = data.get("contract_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ContractError(
            ContractErrorCode.INVALID,
            "contract_version must be a positive integer",
        )
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"unsupported contract_version {version!r}; supported major "
            f"versions: {sorted(SUPPORTED_CONTRACT_VERSIONS)}",
        )
    return version


def parse_utc_timestamp(text: str, *, field: str = "created_at") -> _dt.datetime:
    """Parse an ISO-8601 timestamp that must be explicitly UTC."""

    if not isinstance(text, str) or not text:
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must be a non-empty ISO-8601 string"
        )
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = _dt.datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} is not a valid ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must carry an explicit UTC offset"
        )
    if parsed.utcoffset().total_seconds() != 0:
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must be expressed in UTC"
        )
    return parsed
