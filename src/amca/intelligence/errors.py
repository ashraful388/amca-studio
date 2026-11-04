"""M0 shared error taxonomy and secret-material rejection rules.

Additive module introduced by the M0 Contract Foundation milestone
(zcode/amca-v0.5.0-m0-contract-foundation-01). It implements the shared
top-level error taxonomy from docs/AMCA-v0.5.0-Intelligence-Contracts.md
section 7.2 and the fail-closed credential rules from sections 1.1, 5.3
and 8. Nothing in the pre-existing application imports this package.
"""

import enum
from typing import Any, Mapping, Optional

# Exact credential-like field names that must never appear in a contract
# record. Matching is exact (case-insensitive) so legitimate fields such as
# ``total_tokens`` or ``authorization_ref`` are not false positives.
_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "api_token",
        "access_token",
        "refresh_token",
        "auth_header",
        "authorization",
        "bearer_token",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "secret",
        "secret_key",
        "session_key",
    }
)


class ContractErrorCode(enum.Enum):
    """Shared top-level boundary error taxonomy (contracts spec 7.2)."""

    INVALID = "invalid"
    BLOCKED = "blocked"
    APPROVAL_REQUIRED = "approval_required"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTEGRITY_VIOLATION = "integrity_violation"


class ContractError(Exception):
    """A fail-closed contract violation with a stable taxonomy code.

    ``details`` must already be safe for display: it must never carry
    credential material, raw provider payloads, or unsanitized input.
    """

    def __init__(
        self,
        code: ContractErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details) if details else {}

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


def reject_secret_material(value: Any, *, where: str = "contract record") -> None:
    """Raise :class:`ContractError` if credential-like keys appear in ``value``.

    Walks mappings and sequences recursively and fails closed on any exact
    credential-like field name, so secrets cannot enter durable contract
    records even when a caller supplies them unintentionally.
    """

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}" if path else key_text
                if key_text.lower() in _SECRET_FIELD_NAMES:
                    raise ContractError(
                        ContractErrorCode.INVALID,
                        f"credential-like field {child_path!r} is not allowed "
                        f"in a {where}",
                    )
                _walk(item, child_path)
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                _walk(item, f"{path}[{index}]")

    _walk(value, "")
