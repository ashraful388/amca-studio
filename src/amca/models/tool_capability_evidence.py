"""Verified tool-calling capability evidence (32F follow-up).

Tool capability truth in AMCA is the intersection

    adapter_supports_tools ∩ model_capability ∩ route_supports_tools

(``amca.models.tool_protocol.effective_tool_support``).  The model dimension
used to be decided ONLY by the static ``KNOWN_MODEL_TOOL_SUPPORT`` name table,
which fails closed for every model it does not list — so a configured BYOK
model (or a local Ollama model outside the two curated names) silently lost the
tool catalog and could never call a tool.

This module keeps the fail-closed default and adds the missing evidence path:
a per ``provider`` + ``model`` record of what the provider itself demonstrated.
Only real observations are recorded — never a guess:

``runtime_api_show``
    A local runtime declared the capability for that exact model
    (Ollama ``POST /api/show`` ``capabilities`` list).
``probe_tool_call``
    A bounded probe request was answered with a real tool call.
``provider_tool_call``
    A provider response carried tool calls for a request that offered tools.
``provider_rejection``
    A provider rejected a request *because* it carried tools (invalid-request
    status whose body names the tools field).

Records live in-process only (no persistence, no telemetry, no network): they
are cleared when the desktop service restarts, so a stale verdict can never
outlive the process that observed it.  ``tool_capability_drop_reason`` makes
the otherwise silent "no tools were offered" outcome explicit for the UI and
for the governed chat response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import threading
from typing import Any, Mapping

#: Sources that constitute verified evidence, most authoritative first.
VERIFIED_EVIDENCE_SOURCES: tuple[str, ...] = (
    "runtime_api_show",
    "probe_tool_call",
    "provider_tool_call",
    "provider_rejection",
)

#: Sources that only make the *absence* of tools visible, never a capability.
OBSERVED_ONLY_SOURCES: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_model_key(model: str) -> str:
    """Normalize a model identifier for evidence lookup.

    The ``models/`` prefix some providers use is not part of the model
    identity, and provider model ids are case-insensitive in practice.
    """
    key = str(model or "").strip().lower()
    if key.startswith("models/"):
        key = key[len("models/") :]
    return key


@dataclass(frozen=True, slots=True)
class ToolCapabilityRecord:
    """One verified observation about a provider + model tool capability."""

    provider: str
    model: str
    supported: bool
    source: str
    detail: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "supported": self.supported,
            "source": self.source,
            "detail": self.detail,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class ToolCatalogDrop:
    """A recorded fact that a tool catalog was withheld from a provider."""

    provider: str
    model: str
    reason: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "observed_at": self.observed_at,
        }


class ToolCapabilityEvidenceStore:
    """Bounded, thread-safe, in-process evidence ledger."""

    def __init__(self, *, maximum_records: int = 256, maximum_drops: int = 256) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], ToolCapabilityRecord] = {}
        self._drops: dict[tuple[str, str], ToolCatalogDrop] = {}
        self._maximum_records = maximum_records
        self._maximum_drops = maximum_drops

    # -- verified capability -------------------------------------------

    def record(
        self,
        provider: str,
        model: str,
        *,
        supported: bool,
        source: str,
        detail: str = "",
    ) -> ToolCapabilityRecord:
        """Record one observation; a new observation replaces the old verdict."""
        provider_key = str(provider or "").strip().lower()
        model_key = normalize_model_key(model)
        record = ToolCapabilityRecord(
            provider=provider_key,
            model=model_key,
            supported=bool(supported),
            source=str(source),
            detail=str(detail)[:500],
            observed_at=_now(),
        )
        with self._lock:
            if (
                (provider_key, model_key) not in self._records
                and len(self._records) >= self._maximum_records
            ):
                self._records.pop(next(iter(self._records)), None)
            self._records[(provider_key, model_key)] = record
            if record.supported:
                # A positive verdict makes any earlier "withheld" note stale.
                self._drops.pop((provider_key, model_key), None)
        return record

    def record_for(self, provider: str, model: str) -> ToolCapabilityRecord | None:
        with self._lock:
            return self._records.get(
                (str(provider or "").strip().lower(), normalize_model_key(model))
            )

    def verdict(self, provider: str, model: str) -> bool | None:
        """Return the verified verdict, or ``None`` when nothing is verified."""
        record = self.record_for(provider, model)
        return None if record is None else record.supported

    # -- visibility of withheld tool catalogs --------------------------

    def record_drop(self, provider: str, model: str, reason: str) -> ToolCatalogDrop:
        provider_key = str(provider or "").strip().lower()
        model_key = normalize_model_key(model)
        drop = ToolCatalogDrop(
            provider=provider_key,
            model=model_key,
            reason=str(reason)[:500],
            observed_at=_now(),
        )
        with self._lock:
            if (
                (provider_key, model_key) not in self._drops
                and len(self._drops) >= self._maximum_drops
            ):
                self._drops.pop(next(iter(self._drops)), None)
            self._drops[(provider_key, model_key)] = drop
        return drop

    def drop_for(self, provider: str, model: str) -> ToolCatalogDrop | None:
        with self._lock:
            return self._drops.get(
                (str(provider or "").strip().lower(), normalize_model_key(model))
            )

    def clear_drop(self, provider: str, model: str) -> None:
        with self._lock:
            self._drops.pop(
                (str(provider or "").strip().lower(), normalize_model_key(model)), None
            )

    # -- inspection / tests --------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "records": [item.to_dict() for item in self._records.values()],
                "drops": [item.to_dict() for item in self._drops.values()],
            }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._drops.clear()


#: Process-wide ledger used by every adapter and by the governed chat wrapper.
DEFAULT_TOOL_CAPABILITY_EVIDENCE = ToolCapabilityEvidenceStore()


def record_tool_capability(
    provider: str,
    model: str,
    *,
    supported: bool,
    source: str,
    detail: str = "",
) -> ToolCapabilityRecord:
    return DEFAULT_TOOL_CAPABILITY_EVIDENCE.record(
        provider, model, supported=supported, source=source, detail=detail
    )


def tool_capability_verdict(provider: str, model: str) -> bool | None:
    """Verified verdict for this provider + model, or ``None`` when unverified."""
    return DEFAULT_TOOL_CAPABILITY_EVIDENCE.verdict(provider, model)


def tool_capability_record(provider: str, model: str) -> ToolCapabilityRecord | None:
    return DEFAULT_TOOL_CAPABILITY_EVIDENCE.record_for(provider, model)


def record_tool_catalog_drop(provider: str, model: str, reason: str) -> ToolCatalogDrop:
    return DEFAULT_TOOL_CAPABILITY_EVIDENCE.record_drop(provider, model, reason)


def tool_catalog_drop(provider: str, model: str) -> ToolCatalogDrop | None:
    return DEFAULT_TOOL_CAPABILITY_EVIDENCE.drop_for(provider, model)


def clear_tool_catalog_drop(provider: str, model: str) -> None:
    DEFAULT_TOOL_CAPABILITY_EVIDENCE.clear_drop(provider, model)


def tool_capability_drop_reason(provider: str, model: str) -> str:
    """Truthful reason why no tool catalog can be offered to this model.

    Returns an empty string when the model is either verified as tool-capable
    or listed by the static table (i.e. tools are offered).  Otherwise the
    caller must state this reason instead of implying that a tool ran.
    """
    from .tool_protocol import known_model_tool_support, registry_tool_resolution

    # AMCA-MR-02: the registry is the authority, so a registry block must
    # produce a reason.  Deciding this from the legacy table alone made a
    # registry denial drop the catalog silently, which the adapters' own
    # contract forbids.
    resolution = registry_tool_resolution(model)
    if resolution is not None:
        if resolution.get("source") == "registry_conflict":
            return (
                f"the model registry holds conflicting tool-capability records "
                f"for {model}, so no governed tools were offered to it"
            )
        if resolution.get("value") is None:
            return (
                f"the model registry has no verified tool capability for "
                f"{model}, so no governed tools were offered to it"
            )
        if resolution.get("value") is False:
            return (
                f"the model registry records {model} as not supporting tool "
                "calling, so no governed tools were offered to it"
            )
        if known_model_tool_support(model) is False:
            return (
                f"{model} is known not to support tool calling, so no governed "
                "tools were offered to it"
            )
        return ""

    static = known_model_tool_support(model)
    if static is True:
        return ""
    if static is False:
        return (
            f"{provider}:{model} is known not to support tool calling, so no "
            "governed tools were offered to it"
        )
    record = tool_capability_record(provider, model)
    if record is None:
        return (
            f"tool support for {provider}:{model} is unverified, so no governed "
            "tools were offered to it (AMCA fails closed until the provider "
            "demonstrates tool calling)"
        )
    if record.supported:
        return ""
    return (
        f"the provider reported that {provider}:{model} does not support tool "
        f"calling ({record.source}), so no governed tools were offered to it"
    )


def record_tool_calls_observed(provider: str, model: str, *, count: int) -> None:
    """Record a provider response that actually carried tool calls."""
    if count <= 0:
        return
    record_tool_capability(
        provider,
        model,
        supported=True,
        source="provider_tool_call",
        detail=f"provider response carried {int(count)} tool call(s)",
    )


def record_tool_rejection(provider: str, model: str, *, detail: str) -> None:
    """Record a provider rejection caused by the tools field in the request."""
    record_tool_capability(
        provider,
        model,
        supported=False,
        source="provider_rejection",
        detail=detail,
    )


def rejection_mentions_tools(body: Any) -> bool:
    """True when a provider error body points at the tools field.

    Deliberately narrow: a generic 400/422 is never interpreted as a tool
    capability verdict, only an error that names the tools/function-calling
    field is.  Anything else stays unverified and therefore fail-closed.
    """
    text = str(body or "").lower()
    if not text:
        return False
    for marker in (
        "tools",
        "tool_choice",
        "tool calling",
        "tool_calling",
        "function calling",
        "function_calling",
        "function calling is not supported",
        "tool use",
    ):
        if marker in text:
            return True
    return False


__all__ = [
    "VERIFIED_EVIDENCE_SOURCES",
    "OBSERVED_ONLY_SOURCES",
    "ToolCapabilityRecord",
    "ToolCatalogDrop",
    "ToolCapabilityEvidenceStore",
    "DEFAULT_TOOL_CAPABILITY_EVIDENCE",
    "normalize_model_key",
    "record_tool_capability",
    "tool_capability_verdict",
    "tool_capability_record",
    "record_tool_catalog_drop",
    "tool_catalog_drop",
    "clear_tool_catalog_drop",
    "tool_capability_drop_reason",
    "record_tool_calls_observed",
    "record_tool_rejection",
    "rejection_mentions_tools",
]
