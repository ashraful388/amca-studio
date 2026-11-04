"""AMCA-MR-01: the canonical Model Registry.

One place that records what a model *is*, so no component has to infer it. Every
fact is either known - with the source that established it - or explicitly
unknown. Nothing is ever guessed from a model's name.

Design rules this module enforces:

* **No writes on open.** Constructing :class:`ModelRegistryStore` never creates a
  table, never migrates and never seeds. The table appears only when something is
  deliberately written. Opening a database must stay side-effect free.
* **Unknown is a value.** A capability that has not been established is ``None``,
  not ``False`` and not a plausible default. ``context_window`` is ``None``
  rather than a fabricated number.
* **Pricing is never invented.** A price is present only when its source is
  recorded; otherwise the status is ``unknown`` with null rates.
* **The registry records facts, not availability.** Visibility stays owned by
  ``model_hub_model_visibility``; this module never enables a model.

The vocabulary reuses the existing spec contract
``amca.intelligence.contracts.CapabilitySource`` (declared / discovered /
measured) rather than inventing a parallel one.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .intelligence.contracts import CapabilitySource

#: Capability provenance reuses the existing spec vocabulary - declared,
#: discovered or measured - rather than inventing a parallel one.
CAPABILITY_SOURCES = frozenset(item.value for item in CapabilitySource)

__all__ = [
    "ApiStyle",
    "CapabilityProfile",
    "CostProfile",
    "ExecutionProfile",
    "ModelIdentity",
    "ModelLocality",
    "ModelRecord",
    "ModelRegistryError",
    "ModelRegistryStore",
    "PerformanceProfile",
    "PricingStatus",
    "RecordSource",
    "SpeedClass",
    "ToolCapabilityResolution",
    "KNOWN_PROVIDER_KINDS",
    "ROUTING_CAPABILITY_TAGS",
    "ADAPTER_DECLARED_CAPABILITIES",
    "adapter_declared_capabilities",
    "build_record",
    "unknown_record",
]

TABLE = "model_registry"

#: Providers the registry is specified to support. ``openai_compatible`` covers
#: "other OpenAI-compatible APIs", and an unlisted kind is still accepted as a
#: plain token so a new provider never requires a code change to be recorded.
KNOWN_PROVIDER_KINDS = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "openrouter",
        "zai",
        "openai_compatible",
        "ollama",
        "llama_cpp",
    }
)

#: Provider kinds that run on this machine, and kinds that are known to be
#: remote.  Anything else keeps an unknown locality instead of being asserted.
LOCAL_PROVIDER_KINDS = frozenset({"ollama", "llama_cpp"})
CLOUD_PROVIDER_KINDS = frozenset(
    {"openai", "anthropic", "google", "openrouter", "zai", "kimi"}
)

#: What each provider's adapter declares about every model it serves, mirrored
#: from the adapter defaults so an existing install has real facts rather than
#: nothing.  Sources (kept in sync by hand - the adapters build these inside
#: ``__init__``, so they cannot be read off the class):
#:   models/openai.py, models/anthropic.py, models/kimi.py, models/gemini.py,
#:   models/ollama.py - each builds ``ModelCapabilities`` with these defaults.
#: A missing key means the adapter declares nothing, so the fact stays unknown.
ADAPTER_DECLARED_CAPABILITIES: dict[str, dict[str, bool]] = {
    "openai": {"tools": True, "vision": True, "streaming": True, "json_mode": True},
    "anthropic": {"tools": True, "vision": True, "streaming": True, "json_mode": True},
    "google": {
        "tools": True,
        "vision": True,
        "audio": True,
        "streaming": True,
        "json_mode": True,
    },
    "ollama": {"tools": True, "vision": True, "streaming": True, "json_mode": True},
    "kimi": {"tools": True, "vision": False, "streaming": True, "json_mode": True},
}


def adapter_declared_capabilities(provider_kind: str) -> dict[str, bool]:
    """The capability facts the provider's adapter declares, or nothing.

    An unlisted provider kind declares nothing rather than inheriting another
    provider's facts.
    """
    return dict(ADAPTER_DECLARED_CAPABILITIES.get(str(provider_kind or "").lower(), {}))

MAX_TEXT = 200
_MAX_CONTEXT = 100_000_000


class ModelRegistryError(ValueError):
    """A record was rejected before it could be stored."""


class ModelLocality(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    #: A provider kind that is neither known-local nor known-cloud.  Recorded
    #: rather than asserted: an OpenAI-compatible server on the loopback is not
    #: automatically "cloud".
    UNKNOWN = "unknown"


class SpeedClass(str, Enum):
    """Categories, not fake precision (work order requirement)."""

    VERY_FAST = "very_fast"
    FAST = "fast"
    STANDARD = "standard"
    SLOW = "slow"
    UNKNOWN = "unknown"


class PricingStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ApiStyle(str, Enum):
    PROVIDER_NATIVE = "provider_native"
    OPENAI_COMPATIBLE = "openai_compatible"
    UNKNOWN = "unknown"


class RecordSource(str, Enum):
    """How a registry row came to exist. Recorded, never inferred."""

    ADAPTER_DECLARATION = "adapter_declaration"
    #: The record was derived from the reference *format* alone; no adapter,
    #: provider or measurement has stated anything about the model.
    REFERENCE_PARSE = "reference_parse"
    LOCAL_DISCOVERY = "local_discovery"
    PROVIDER_DISCOVERY = "provider_discovery"
    MEASURED = "measured"
    MODEL_HUB_MIGRATION = "model_hub_migration"
    USER_DECLARATION = "user_declaration"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _text(value: Any, *, field: str, maximum: int = MAX_TEXT, required: bool = True) -> str:
    raw = "" if value is None else str(value).strip()
    if not raw:
        if required:
            raise ModelRegistryError(f"{field} is required")
        return ""
    if len(raw) > maximum:
        raise ModelRegistryError(f"{field} exceeds {maximum} characters")
    if any(ord(ch) < 32 for ch in raw):
        raise ModelRegistryError(f"{field} contains control characters")
    return raw


def _optional_bool(value: Any, *, field: str) -> bool | None:
    """A capability is True, False, or unknown. Anything else is malformed."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ModelRegistryError(f"{field} must be a boolean or null (unknown)")


def _optional_positive_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelRegistryError(f"{field} must be an integer or null (unknown)")
    if value < 1 or value > _MAX_CONTEXT:
        raise ModelRegistryError(f"{field} must be between 1 and {_MAX_CONTEXT}")
    return value


def _optional_rate(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelRegistryError(f"{field} must be a number or null (unknown)")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ModelRegistryError(f"{field} must be a finite non-negative number")
    return number


def _provider_token(value: Any, *, field: str) -> str:
    token = _text(value, field=field, maximum=64)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", token):
        raise ModelRegistryError(f"{field} is not a valid provider identifier")
    return token


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    provider_kind: str
    provider_id: str | None
    model_id: str
    display_name: str
    locality: ModelLocality

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_kind": self.provider_kind,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "locality": self.locality.value,
        }


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    tools: bool | None = None
    vision: bool | None = None
    streaming: bool | None = None
    json_mode: bool | None = None
    audio: bool | None = None
    system_messages: bool | None = None
    coding: bool | None = None
    capability_source: str = "declared"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "vision": self.vision,
            "streaming": self.streaming,
            "json_mode": self.json_mode,
            "audio": self.audio,
            "system_messages": self.system_messages,
            "coding": self.coding,
            "capability_source": self.capability_source,
        }


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    context_window: int | None = None
    max_output_tokens: int | None = None
    api_style: ApiStyle = ApiStyle.PROVIDER_NATIVE
    endpoint_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "api_style": self.api_style.value,
            "endpoint_class": self.endpoint_class,
        }


@dataclass(frozen=True, slots=True)
class CostProfile:
    pricing_status: PricingStatus = PricingStatus.UNKNOWN
    input_per_1m_usd: float | None = None
    output_per_1m_usd: float | None = None
    currency: str | None = None
    pricing_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pricing_status": self.pricing_status.value,
            "input_per_1m_usd": self.input_per_1m_usd,
            "output_per_1m_usd": self.output_per_1m_usd,
            "currency": self.currency,
            "pricing_source": self.pricing_source,
        }


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    speed_class: SpeedClass = SpeedClass.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {"speed_class": self.speed_class.value}


@dataclass(frozen=True, slots=True)
class ModelRecord:
    model_ref: str
    identity: ModelIdentity
    capabilities: CapabilityProfile
    execution: ExecutionProfile
    cost: CostProfile
    performance: PerformanceProfile
    updated_at: str
    record_source: str
    verified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_ref": self.model_ref,
            "identity": self.identity.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "execution": self.execution.to_dict(),
            "cost": self.cost.to_dict(),
            "performance": self.performance.to_dict(),
            "updated_at": self.updated_at,
            "record_source": self.record_source,
            "verified_at": self.verified_at,
        }

    # -- routing-facing accessors ------------------------------------------

    def capability_tags(self) -> frozenset[str]:
        """Routing tags this model actually declares. Unknown adds nothing."""
        tags: set[str] = set()
        if self.capabilities.vision is True:
            tags.add("vision")
        if self.capabilities.audio is True:
            tags.add("audio")
        if self.capabilities.coding is True:
            tags.add("coding")
        if self.capabilities.tools is True:
            tags.add("tools")
        if self.capabilities.json_mode is True:
            tags.add("json")
        return frozenset(tags)


def build_record(
    *,
    model_ref: Any,
    provider_kind: Any,
    model_id: Any,
    display_name: Any = None,
    provider_id: Any = None,
    locality: Any = None,
    capabilities: Mapping[str, Any] | None = None,
    capability_source: Any = None,
    context_window: Any = None,
    max_output_tokens: Any = None,
    api_style: Any = None,
    endpoint_class: Any = None,
    pricing_status: Any = None,
    input_per_1m_usd: Any = None,
    output_per_1m_usd: Any = None,
    currency: Any = None,
    pricing_source: Any = None,
    speed_class: Any = None,
    record_source: Any = None,
    verified_at: Any = None,
) -> ModelRecord:
    """Validate raw input into a record, or raise :class:`ModelRegistryError`.

    Every field is validated here rather than at the SQLite layer, so malformed
    capability data is rejected before it can be stored.
    """
    if isinstance(model_ref, (dict, list, tuple, set)):
        raise ModelRegistryError("model_ref must be a string")
    ref = _text(model_ref, field="model_ref")
    # The key must name the *model* the record describes: routing reads facts by
    # key alone, so a record keyed to one model while asserting another model's
    # facts would misattribute them.  The provider prefix is not policed against
    # ``provider_id`` because callers legitimately use a shortened form there.
    _head, separator, tail = ref.partition(":")
    if separator and tail and tail != str(model_id):
        raise ModelRegistryError(
            "model_ref and model_id disagree: the key names a different model"
        )
    kind = _provider_token(provider_kind, field="provider_kind")
    identifier = _text(model_id, field="model_id")
    label = _text(display_name, field="display_name", required=False) or identifier
    provider = (
        _provider_token(provider_id, field="provider_id") if provider_id else None
    )

    if locality is None:
        if kind in LOCAL_PROVIDER_KINDS:
            locality_value = ModelLocality.LOCAL
        elif kind in CLOUD_PROVIDER_KINDS:
            locality_value = ModelLocality.CLOUD
        else:
            # An unlisted kind may well be a local OpenAI-compatible server.
            locality_value = ModelLocality.UNKNOWN
    else:
        try:
            locality_value = ModelLocality(str(locality))
        except ValueError as exc:
            raise ModelRegistryError(
                f"locality must be one of {[item.value for item in ModelLocality]}"
            ) from exc

    if capabilities is not None and not isinstance(capabilities, Mapping):
        raise ModelRegistryError("capabilities must be an object or null")
    raw_caps = dict(capabilities or {})
    if capability_source is not None and not str(capability_source).strip():
        raise ModelRegistryError("capability_source must not be empty")
    source = str(capability_source or CapabilitySource.DECLARED.value)
    if source not in CAPABILITY_SOURCES:
        raise ModelRegistryError(
            f"unknown capability_source {source!r}; expected one of "
            f"{sorted(CAPABILITY_SOURCES)}"
        )
    caps = CapabilityProfile(
        tools=_optional_bool(raw_caps.get("tools"), field="capabilities.tools"),
        vision=_optional_bool(raw_caps.get("vision"), field="capabilities.vision"),
        streaming=_optional_bool(raw_caps.get("streaming"), field="capabilities.streaming"),
        json_mode=_optional_bool(raw_caps.get("json_mode"), field="capabilities.json_mode"),
        audio=_optional_bool(raw_caps.get("audio"), field="capabilities.audio"),
        system_messages=_optional_bool(
            raw_caps.get("system_messages"), field="capabilities.system_messages"
        ),
        coding=_optional_bool(raw_caps.get("coding"), field="capabilities.coding"),
        capability_source=source,
    )

    if kind in {"openai", "openrouter", "zai", "openai_compatible"}:
        style = ApiStyle.OPENAI_COMPATIBLE
    elif kind in {"anthropic", "google", "kimi", "ollama", "llama_cpp"}:
        style = ApiStyle.PROVIDER_NATIVE
    else:
        style = ApiStyle.UNKNOWN
    if api_style is not None:
        try:
            style = ApiStyle(str(api_style))
        except ValueError as exc:
            raise ModelRegistryError(
                f"api_style must be one of {[item.value for item in ApiStyle]}"
            ) from exc

    execution = ExecutionProfile(
        context_window=_optional_positive_int(
            context_window, field="context_window"
        ),
        max_output_tokens=_optional_positive_int(
            max_output_tokens, field="max_output_tokens"
        ),
        api_style=style,
        endpoint_class=(
            _text(endpoint_class, field="endpoint_class", required=False) or None
        ),
    )

    if pricing_status is None:
        status = (
            PricingStatus.NOT_APPLICABLE
            if locality_value is ModelLocality.LOCAL
            else PricingStatus.UNKNOWN
        )
    else:
        try:
            status = PricingStatus(str(pricing_status))
        except ValueError as exc:
            raise ModelRegistryError(
                f"pricing_status must be one of "
                f"{[item.value for item in PricingStatus]}"
            ) from exc
    input_rate = _optional_rate(input_per_1m_usd, field="input_per_1m_usd")
    output_rate = _optional_rate(output_per_1m_usd, field="output_per_1m_usd")
    if status is not PricingStatus.KNOWN and (input_rate is not None or output_rate is not None):
        # Pricing is never invented: rates without a recorded source are refused
        # rather than quietly stored.
        raise ModelRegistryError(
            "pricing rates require pricing_status='known' with a pricing_source"
        )
    if status is PricingStatus.KNOWN and not pricing_source:
        raise ModelRegistryError("pricing_status='known' requires a pricing_source")
    cost = CostProfile(
        pricing_status=status,
        input_per_1m_usd=input_rate,
        output_per_1m_usd=output_rate,
        currency=(_text(currency, field="currency", required=False) or None),
        pricing_source=(_text(pricing_source, field="pricing_source", required=False) or None),
    )

    if speed_class is None:
        speed = SpeedClass.UNKNOWN
    else:
        try:
            speed = SpeedClass(str(speed_class))
        except ValueError as exc:
            raise ModelRegistryError(
                f"speed_class must be one of {[item.value for item in SpeedClass]}"
            ) from exc

    if record_source is not None and not str(record_source).strip():
        raise ModelRegistryError("record_source must not be empty")
    origin = str(record_source or RecordSource.ADAPTER_DECLARATION.value)
    if origin not in {item.value for item in RecordSource}:
        raise ModelRegistryError(f"unknown record_source {origin!r}")

    return ModelRecord(
        model_ref=ref,
        identity=ModelIdentity(
            provider_kind=kind,
            provider_id=provider,
            model_id=identifier,
            display_name=label,
            locality=locality_value,
        ),
        capabilities=caps,
        execution=execution,
        cost=cost,
        performance=PerformanceProfile(speed_class=speed),
        updated_at=_now(),
        record_source=origin,
        verified_at=(_text(verified_at, field="verified_at", required=False) or None),
    )


def unknown_record(
    model_ref: str,
    *,
    provider_kind: str,
    model_id: str,
    provider_id: str | None = None,
    display_name: str | None = None,
) -> ModelRecord:
    """A record for a model nobody has established facts about.

    Used instead of a guess: the identity is known from the reference, every
    capability is unknown, and the context window is unknown rather than a
    plausible default.
    """
    return build_record(
        model_ref=model_ref,
        provider_kind=provider_kind,
        model_id=model_id,
        display_name=display_name,
        provider_id=provider_id,
        capabilities={},
        capability_source=CapabilitySource.DECLARED.value,
        record_source=RecordSource.REFERENCE_PARSE.value,
    )


@dataclass(frozen=True, slots=True)
class ToolCapabilityResolution:
    """What the registry knows about one model's tool capability.

    ``source`` records where the answer came from so a caller can tell an
    authoritative registry fact from a deprecated fallback:

    ``registry``             one record states the fact (``value`` may be None,
                             which means the fact is unknown -> callers block)
    ``registry_conflict``    two records disagree about the same model id; the
                             safe answer is to block and surface the conflict
    """

    value: bool | None
    source: str
    conflicting_refs: tuple[str, ...] = ()

    @property
    def authoritative(self) -> bool:
        return self.source in {"registry", "registry_conflict"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "conflicting_refs": list(self.conflicting_refs),
        }


class ModelRegistryStore:
    """Durable canonical model facts in the desktop SQLite file.

    Construction is deliberately inert: no table is created, no migration runs
    and nothing is seeded. A read against a database that has never held a
    registry returns "not registered" rather than writing anything.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- connection helpers -------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection that is always closed.

        ``sqlite3.Connection`` used directly as a context manager only commits
        or rolls back - it does not close - so every call site here goes
        through this helper and the file handle is released.
        """
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def _table_exists(self, connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
            ).fetchone()
            is not None
        )

    def ensure_schema(self) -> bool:
        """Create the registry table if it is missing.  Explicit, never implicit.

        The store stays inert on construction, so the service performs this as a
        deliberate startup step.  That is what makes an *absent* table mean
        "the registry is gone" rather than "the registry was never written" -
        without it, a dropped table would make every lookup look like an
        unknown model and hand the decision to the deprecated fallback.
        """
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_table(connection)
                connection.execute("COMMIT")
            except BaseException:
                raise
        return True

    def _ensure_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                model_ref TEXT PRIMARY KEY,
                provider_kind TEXT NOT NULL,
                provider_id TEXT,
                model_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                locality TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                execution_json TEXT NOT NULL,
                cost_json TEXT NOT NULL,
                performance_json TEXT NOT NULL,
                record_source TEXT NOT NULL,
                verified_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_provider ON {TABLE}(provider_kind)"
        )
        # The tool path resolves by normalized model id on every model call.
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_model_id_norm "
            f"ON {TABLE}(lower(model_id))"
        )
        # A table left by a different schema must fail loudly here rather than
        # surfacing as a raw IndexError from a later read.
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({TABLE})").fetchall()
        }
        missing = {
            "model_ref", "provider_kind", "provider_id", "model_id", "display_name",
            "locality", "capabilities_json", "execution_json", "cost_json",
            "performance_json", "record_source", "verified_at", "updated_at",
        } - columns
        if missing:
            raise ModelRegistryError(
                f"{TABLE} has an incompatible schema; missing {sorted(missing)}"
            )

    # -- reads --------------------------------------------------------------

    def get(self, model_ref: str) -> ModelRecord | None:
        """The stored record, or None when this model is not registered."""
        ref = str(model_ref or "").strip()
        if not ref or not self.database.exists():
            # Connecting to a missing file would create it, which is exactly
            # the unwanted write on open the work order forbids.
            return None
        with self._lock, self._connect() as connection:
            if not self._table_exists(connection):
                return None
            row = connection.execute(
                f"SELECT * FROM {TABLE} WHERE model_ref=?", (ref,)
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def resolve(self, model_ref: str) -> tuple[ModelRecord, bool]:
        """``(record, registered)``; an unknown model yields an unknown record.

        Never raises for a missing or odd reference: the caller asked what is
        known, and "nothing" is a valid answer.
        """
        stored = self.get(model_ref)
        if stored is not None:
            return stored, True
        return self._unregistered(model_ref), False

    def list_records(self) -> list[ModelRecord]:
        if not self.database.exists():
            return []
        with self._lock, self._connect() as connection:
            if not self._table_exists(connection):
                return []
            rows = connection.execute(
                f"SELECT * FROM {TABLE} ORDER BY provider_kind, model_id COLLATE NOCASE"
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def capabilities_for(self, model_ref: str) -> frozenset[str]:
        """Routing capability tags. An unregistered model declares nothing."""
        record = self.get(model_ref)
        return record.capability_tags() if record is not None else frozenset()

    def record_or_none(self, model_ref: str) -> ModelRecord | None:
        """Alias of :meth:`get`, kept so a caller can fetch once and read both
        capabilities and the context window without a second connection."""
        return self.get(model_ref)

    @staticmethod
    def normalize_model_id(model_id: str) -> str:
        """The identity a model id denotes, independent of how it is written.

        The ``models/`` prefix is not part of a model's identity and provider
        model ids are case-insensitive in practice, so the lookup must use the
        same rule the rest of the tool path uses.  Matching the raw string let a
        recorded ``tools=false`` be bypassed simply by writing
        ``models/Gemini-2.0-Flash`` instead of ``gemini-2.0-flash``.
        """
        key = str(model_id or "").strip().lower()
        # Strip the prefix repeatedly: a doubly-prefixed id must not be able to
        # slip past a recorded denial.
        while key.startswith("models/"):
            key = key[len("models/"):]
        return key

    def resolve_tool_capability(self, model_id: str) -> ToolCapabilityResolution | None:
        """Tool capability for a model id, across every record that describes it.

        Returns ``None`` when no record mentions this model at all, which is the
        only case where a caller may fall back to a legacy source.  Two records
        that disagree about the same model id are reported as a conflict rather
        than one of them silently winning.

        The lookup is a single indexed query on the normalized id, not a scan of
        every record: this runs once per model call and once per tool iteration.
        """
        wanted = self.normalize_model_id(model_id)
        if not wanted or not self.database.exists():
            return None
        try:
            with self._lock, self._connect() as connection:
                if not self._table_exists(connection):
                    # The database exists but its registry does not: the table
                    # was dropped or the file was restored without one.  That is
                    # "cannot answer", not "no record", so it blocks rather than
                    # handing the decision to the deprecated fallback.
                    return ToolCapabilityResolution(
                        value=None, source="registry_unavailable"
                    )
                rows = connection.execute(
                    f"""SELECT * FROM {TABLE}
                        WHERE lower(model_id) IN (?, ?)""",
                    (wanted, f"models/{wanted}"),
                ).fetchall()
        except sqlite3.Error:
            # "Cannot read" is not "no record".  Reporting it as absent would
            # let the deprecated table grant a capability the registry denies,
            # so it is surfaced as an explicit unavailable state that blocks.
            return ToolCapabilityResolution(
                value=None, source="registry_unavailable"
            )
        matches = [self._record_from_row(row) for row in rows]
        if not matches:
            return None
        values = [record.capabilities.tools for record in matches]
        stated = {value for value in values if value is not None}
        # An unknown record beside a stated one is a conflict, not a licence to
        # use the stated one: one provider's blanket grant must not enable a
        # different provider's model whose own record is unknown.
        if len(stated) > 1 or (stated and any(value is None for value in values)):
            return ToolCapabilityResolution(
                value=None,
                source="registry_conflict",
                conflicting_refs=tuple(sorted(record.model_ref for record in matches)),
            )
        return ToolCapabilityResolution(
            value=next(iter(stated)) if stated else None,
            source="registry",
        )

    def context_window_for(self, model_ref: str) -> int | None:
        """The recorded context window, or None - never a default."""
        record = self.get(model_ref)
        return record.execution.context_window if record is not None else None

    # -- writes -------------------------------------------------------------

    def upsert(self, record: ModelRecord) -> ModelRecord:
        """Create or replace one record."""
        if not isinstance(record, ModelRecord):
            raise ModelRegistryError("a ModelRecord is required")
        stored = self._revalidate(record)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_table(connection)
                connection.execute(
                    f"""
                    INSERT INTO {TABLE} (
                        model_ref, provider_kind, provider_id, model_id, display_name,
                        locality, capabilities_json, execution_json, cost_json,
                        performance_json, record_source, verified_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model_ref) DO UPDATE SET
                        provider_kind=excluded.provider_kind,
                        provider_id=excluded.provider_id,
                        model_id=excluded.model_id,
                        display_name=excluded.display_name,
                        locality=excluded.locality,
                        capabilities_json=excluded.capabilities_json,
                        execution_json=excluded.execution_json,
                        cost_json=excluded.cost_json,
                        performance_json=excluded.performance_json,
                        record_source=excluded.record_source,
                        verified_at=excluded.verified_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        stored.model_ref,
                        stored.identity.provider_kind,
                        stored.identity.provider_id,
                        stored.identity.model_id,
                        stored.identity.display_name,
                        stored.identity.locality.value,
                        json.dumps(stored.capabilities.to_dict(), ensure_ascii=False),
                        json.dumps(stored.execution.to_dict(), ensure_ascii=False),
                        json.dumps(stored.cost.to_dict(), ensure_ascii=False),
                        json.dumps(stored.performance.to_dict(), ensure_ascii=False),
                        stored.record_source,
                        stored.verified_at,
                        stored.updated_at,
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                # Closing the connection rolls back; an explicit ROLLBACK here
                # can raise "no transaction is active" and mask the real error.
                raise
        return stored

    def delete(self, model_ref: str) -> bool:
        ref = str(model_ref or "").strip()
        if not ref or not self.database.exists():
            return False
        with self._lock, self._connect() as connection:
            if not self._table_exists(connection):
                return False
            cursor = connection.execute(f"DELETE FROM {TABLE} WHERE model_ref=?", (ref,))
        return bool(cursor.rowcount)

    def adopt(self, records: Iterable[ModelRecord]) -> dict[str, Any]:
        """Adopt records for models that are not already registered.

        Idempotent and non-destructive: an existing row - including a
        user-declared or measured one - is never overwritten. This is how an
        upgrade brings existing providers into the registry without losing
        anything the user has already established.

        The whole batch runs in one transaction and inserts with
        ``ON CONFLICT DO NOTHING``, so the check and the write cannot be
        separated by a concurrent declaration and a partial batch cannot be
        left behind by a failure part-way through.
        """
        prepared: list[ModelRecord] = []
        for record in records:
            if not isinstance(record, ModelRecord):
                raise ModelRegistryError("adopt expects ModelRecord items")
            prepared.append(self._revalidate(record))
        adopted: list[str] = []
        kept: list[str] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_table(connection)
                for record in prepared:
                    cursor = connection.execute(
                        f"""
                        INSERT INTO {TABLE} (
                            model_ref, provider_kind, provider_id, model_id,
                            display_name, locality, capabilities_json,
                            execution_json, cost_json, performance_json,
                            record_source, verified_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(model_ref) DO NOTHING
                        """,
                        self._insert_values(record),
                    )
                    (adopted if cursor.rowcount else kept).append(record.model_ref)
                connection.execute("COMMIT")
            except BaseException:
                raise
        return {"adopted": adopted, "kept": kept}

    @staticmethod
    def _insert_values(record: ModelRecord) -> tuple[Any, ...]:
        return (
            record.model_ref,
            record.identity.provider_kind,
            record.identity.provider_id,
            record.identity.model_id,
            record.identity.display_name,
            record.identity.locality.value,
            json.dumps(record.capabilities.to_dict(), ensure_ascii=False),
            json.dumps(record.execution.to_dict(), ensure_ascii=False),
            json.dumps(record.cost.to_dict(), ensure_ascii=False),
            json.dumps(record.performance.to_dict(), ensure_ascii=False),
            record.record_source,
            record.verified_at,
            record.updated_at,
        )

    @staticmethod
    def _revalidate(record: ModelRecord) -> ModelRecord:
        """Round-trip a record through validation so stored rows are always sane."""
        return build_record(
            model_ref=record.model_ref,
            provider_kind=record.identity.provider_kind,
            provider_id=record.identity.provider_id,
            model_id=record.identity.model_id,
            display_name=record.identity.display_name,
            locality=record.identity.locality.value,
            capabilities={
                "tools": record.capabilities.tools,
                "vision": record.capabilities.vision,
                "streaming": record.capabilities.streaming,
                "json_mode": record.capabilities.json_mode,
                "audio": record.capabilities.audio,
                "system_messages": record.capabilities.system_messages,
                "coding": record.capabilities.coding,
            },
            capability_source=record.capabilities.capability_source,
            context_window=record.execution.context_window,
            max_output_tokens=record.execution.max_output_tokens,
            api_style=record.execution.api_style.value,
            endpoint_class=record.execution.endpoint_class,
            pricing_status=record.cost.pricing_status.value,
            input_per_1m_usd=record.cost.input_per_1m_usd,
            output_per_1m_usd=record.cost.output_per_1m_usd,
            currency=record.cost.currency,
            pricing_source=record.cost.pricing_source,
            speed_class=record.performance.speed_class.value,
            record_source=record.record_source,
            verified_at=record.verified_at,
        )

    # -- internals ----------------------------------------------------------

    def _unregistered(self, model_ref: str) -> ModelRecord:
        """Build the honest unknown record for a reference we have never seen.

        The provider kind and model id are parsed from the reference *format*
        (a structural fact, not a name guess); every capability stays unknown.
        """
        ref = str(model_ref or "").strip()[:MAX_TEXT]
        if not ref:
            ref = "unknown"
        provider_kind = "openai_compatible"
        provider_id: str | None = None
        model_id = ref
        if ref.startswith("ollama:"):
            provider_kind = "ollama"
            model_id = ref[len("ollama:"):]
        else:
            head, separator, tail = ref.partition(":")
            if separator and tail:
                provider_id = head
                model_id = tail
                # A reference whose prefix names a provider the registry knows
                # is that provider; reporting every ref as openai_compatible
                # would be a false claim about the model.
                if head.casefold() in KNOWN_PROVIDER_KINDS:
                    provider_kind = head.casefold()
                    provider_id = None
        return unknown_record(
            ref,
            provider_kind=provider_kind,
            model_id=model_id or ref,
            provider_id=provider_id,
        )

    @staticmethod
    def _blob(row: sqlite3.Row, column: str) -> dict[str, Any]:
        """Decode one JSON column, degrading to "unknown" on a bad row.

        A single corrupt row must not break ``list_records`` or abort the
        routing pass that reads these accessors, so an unreadable blob becomes
        an empty mapping rather than an exception.
        """
        try:
            value = json.loads(row[column])
        except (ValueError, TypeError, IndexError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _known_int(value: Any) -> int | None:
        """Only a real positive integer is a recorded window."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return value

    @classmethod
    def _record_from_row(cls, row: sqlite3.Row) -> ModelRecord:
        capabilities = cls._blob(row, "capabilities_json")
        execution = cls._blob(row, "execution_json")
        cost = cls._blob(row, "cost_json")
        performance = cls._blob(row, "performance_json")
        return ModelRecord(
            model_ref=str(row["model_ref"]),
            identity=ModelIdentity(
                provider_kind=str(row["provider_kind"]),
                provider_id=row["provider_id"],
                model_id=str(row["model_id"]),
                display_name=str(row["display_name"]),
                locality=ModelLocality(str(row["locality"])),
            ),
            capabilities=CapabilityProfile(
                tools=capabilities.get("tools"),
                vision=capabilities.get("vision"),
                streaming=capabilities.get("streaming"),
                json_mode=capabilities.get("json_mode"),
                audio=capabilities.get("audio"),
                system_messages=capabilities.get("system_messages"),
                coding=capabilities.get("coding"),
                capability_source=str(capabilities.get("capability_source", "declared")),
            ),
            execution=ExecutionProfile(
                context_window=cls._known_int(execution.get("context_window")),
                max_output_tokens=cls._known_int(execution.get("max_output_tokens")),
                api_style=ApiStyle(str(execution.get("api_style", "provider_native"))),
                endpoint_class=execution.get("endpoint_class"),
            ),
            cost=CostProfile(
                pricing_status=PricingStatus(str(cost.get("pricing_status", "unknown"))),
                input_per_1m_usd=cost.get("input_per_1m_usd"),
                output_per_1m_usd=cost.get("output_per_1m_usd"),
                currency=cost.get("currency"),
                pricing_source=cost.get("pricing_source"),
            ),
            performance=PerformanceProfile(
                speed_class=SpeedClass(str(performance.get("speed_class", "unknown")))
            ),
            updated_at=str(row["updated_at"]),
            record_source=str(row["record_source"]),
            verified_at=row["verified_at"],
        )
