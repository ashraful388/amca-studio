"""Model Hub persistence and deterministic Agent Fabric model intelligence.

This module deliberately keeps secrets and money movement outside agent state.
Provider credentials remain in :mod:`amca.credentials`; the records here contain
only opaque credential references and public model-access metadata.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from .hardware import GIB, LocalComputeProfile


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    return dict(value) if isinstance(value, Mapping) else {}


def _https_endpoint(value: str, *, required: bool = True) -> str:
    endpoint = str(value).strip().rstrip("/")
    if not endpoint and not required:
        return ""
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("endpoint must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("endpoint cannot contain credentials, query, or fragment")
    return endpoint


def _text(value: Any, *, name: str, maximum: int = 160, required: bool = True) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > maximum or any(ord(c) < 32 for c in result):
        raise ValueError(f"{name} must be {'1-' if required else '0-'}{maximum} printable characters")
    return result


def _model_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("model_ids must be an array")
    result: list[str] = []
    for raw in value:
        model = _text(raw, name="model_id", maximum=200)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}", model):
            raise ValueError("model ID contains unsupported characters")
        if model not in result:
            result.append(model)
    if len(result) > 2_000:
        raise ValueError("one provider catalog may contain at most 2,000 models")
    return result


class AccessSource(str, Enum):
    AMCA_API = "amca_api"
    BYOK = "byok"
    LOCAL = "local"


class PricingStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    USER_OVERRIDE = "user_override"
    SUPERSEDED = "superseded"
    UNAVAILABLE = "unavailable"


class CostStatus(str, Enum):
    ACTUAL_PROVIDER_REPORTED = "actual_provider_reported"
    ACTUAL_AMCA_LEDGER = "actual_amca_ledger"
    CALCULATED_FROM_MEASURED_USAGE = "calculated_from_measured_usage"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SelectionPolicy(str, Enum):
    AUTO_INTELLIGENCE = "auto_intelligence"
    MANUAL_MODEL_SET = "manual_model_set"
    MANUAL_MODEL_TEAM = "manual_model_team"


KNOWN_ENDPOINTS: dict[str, tuple[str, str]] = {
    "openai": ("OpenAI", "https://api.openai.com/v1"),
    "anthropic": ("Anthropic", "https://api.anthropic.com"),
    "google": ("Google / Gemini", "https://generativelanguage.googleapis.com/v1beta"),
    "moonshot": ("Moonshot / Kimi", "https://api.moonshot.ai/v1"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1"),
    "xai": ("xAI", "https://api.x.ai/v1"),
    # Round 29 (Meta 401 fix): Meta published an OpenAI-compatible endpoint
    # so the "Meta" providers now default to the real URL instead of the
    # hand-filled custom one the owner was told to type. This is only the
    # DEFAULT base URL; the operator can still override the endpoint.
    "meta": ("Meta", "https://api.meta.ai/v1"),
}


@dataclass(frozen=True, slots=True)
class NanoAgentRole:
    role_id: str
    objective: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    normally_active: bool = False


MAIA_NANO_ROLES: tuple[NanoAgentRole, ...] = (
    NanoAgentRole("endpoint_check", "Verify one official provider endpoint.", ("provider", "cached_record"), ("endpoint", "source", "confidence")),
    NanoAgentRole("provider_docs", "Resolve bounded ambiguity from official provider docs.", ("provider", "question"), ("answer", "official_source")),
    NanoAgentRole("model_catalog", "Enrich one provider's sanitized model catalog.", ("provider", "model_ids"), ("capabilities", "lifecycle", "sources")),
    NanoAgentRole("price_check", "Find current official pricing for a bounded model batch.", ("provider", "model_ids", "route"), ("raw_rates", "source", "verified_at")),
    NanoAgentRole("pricing_normalize", "Normalize comparable billing units without discarding raw units.", ("raw_rates",), ("token_rates", "other_rates")),
    NanoAgentRole("pricing_change", "Compare pricing versions and report material changes.", ("old", "new"), ("changed", "delta")),
    NanoAgentRole("route_metadata", "Verify public route and region metadata.", ("provider", "model"), ("routes", "regions", "source")),
    NanoAgentRole("provenance_verify", "Check that metadata is supported by an official source.", ("record",), ("status", "confidence")),
)


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    model_ref: str
    source: AccessSource
    provider: str
    expected_cost_usd: float | None
    quality_score: float
    reliability_score: float = 1.0
    capabilities: frozenset[str] = field(default_factory=frozenset)
    context_length: int = 0
    available: bool = True
    privacy_eligible: bool = True


@dataclass(frozen=True, slots=True)
class ModelSelectionDecision:
    policy: SelectionPolicy
    model_refs: tuple[str, ...]
    expected_cost_usd: float | None
    rationale: str
    confidence: str


class CostQualityGate:
    """Deterministic quality/capability admission before cost comparison."""

    def accepts(
        self,
        candidate: ModelCandidate,
        *,
        minimum_quality: float,
        minimum_reliability: float = 0.8,
        required_capabilities: Iterable[str] = (),
        required_context: int = 0,
    ) -> bool:
        required = frozenset(required_capabilities)
        # AMCA-MR-01: context_length is now the *recorded* window from the
        # canonical registry, and 0 means nobody has established it.  An
        # unestablished window must not disqualify a model - excluding every
        # model the registry has not seen would disable routing entirely - but
        # a window that IS known and too small is now correctly refused, which
        # the previous hardcoded 32k/128k values could never do.
        context_ok = (
            candidate.context_length == 0
            or candidate.context_length >= required_context
        )
        return (
            candidate.available
            and candidate.privacy_eligible
            and candidate.quality_score >= minimum_quality
            and candidate.reliability_score >= minimum_reliability
            and required.issubset(candidate.capabilities)
            and context_ok
        )


class AutoIntelligenceRouter:
    """Lowest-known-cost routing constrained by the Cost-Quality Gate."""

    def __init__(self, gate: CostQualityGate | None = None) -> None:
        self.gate = gate or CostQualityGate()

    def resolve(
        self,
        candidates: Sequence[ModelCandidate],
        *,
        policy: SelectionPolicy = SelectionPolicy.AUTO_INTELLIGENCE,
        manual_refs: Sequence[str] = (),
        minimum_quality: float = 0.7,
        required_capabilities: Iterable[str] = (),
        required_context: int = 0,
        required_participants: int = 1,
        optimize_cost: bool = True,
    ) -> ModelSelectionDecision:
        if policy is not SelectionPolicy.AUTO_INTELLIGENCE:
            requested = tuple(dict.fromkeys(str(item) for item in manual_refs if str(item)))
            known = {item.model_ref: item for item in candidates}
            if not requested or any(ref not in known for ref in requested):
                raise ValueError("manual selection must contain known logical model references")
            unavailable = [ref for ref in requested if not known[ref].available]
            if unavailable:
                raise ValueError(f"manual model unavailable: {', '.join(unavailable)}")
            known_costs = [known[ref].expected_cost_usd for ref in requested]
            total = sum(cost for cost in known_costs if cost is not None) if all(cost is not None for cost in known_costs) else None
            return ModelSelectionDecision(policy, requested, total, "Manual model membership preserved.", "high")

        # COMPATIBILITY SHIM (Task F, manager decision 2026-09-19).
        #
        # This method used to rank candidates itself: by -quality_score when
        # optimize_cost was False (the production call), by cost when True (its
        # own default), and by locality in a third implementation.  Those three
        # answers contradicted each other for the same goal and the same models.
        #
        # The ranking logic is GONE.  This translates its inputs into the
        # canonical request and delegates to the one Model Router, which applies
        # hard filters first, then evidence-based ranking, then a neutral
        # tie-break.  The signature is unchanged so existing callers keep
        # working while the migration completes.
        from .model_router import (
            CostPolicy,
            ModelCandidateFacts,
            ModelSelectionRequest,
            select_model,
        )

        if required_participants < 1:
            raise ValueError("required_participants must be positive")

        # The old gate enforced a name-heuristic quality floor.  It is not
        # carried forward as a ranking input, but a caller that still supplies
        # one is honoured as a hard eligibility filter so behaviour does not
        # silently widen.
        viable = [
            item for item in candidates
            if self.gate.accepts(
                item,
                minimum_quality=minimum_quality,
                required_capabilities=required_capabilities,
                required_context=required_context,
            )
        ]
        if not viable:
            raise ValueError("no available model satisfies the required quality and capability gate")

        facts = [
            ModelCandidateFacts(
                model_ref=item.model_ref,
                provider=str(item.provider),
                capabilities=frozenset(str(name) for name in item.capabilities),
                supports_tools="tools" in {str(name) for name in item.capabilities},
                context_window=int(item.context_length or 0),
                is_local=item.source is AccessSource.LOCAL,
                visible=True,
                available=bool(item.available),
                expected_cost_usd=item.expected_cost_usd,
            )
            for item in viable
        ]
        request = ModelSelectionRequest(
            task_type="auto_intelligence",
            required_capabilities=frozenset(str(name) for name in required_capabilities),
            required_context=int(required_context or 0),
            minimum_participants=required_participants,
            maximum_participants=required_participants,
            # The explicit policy the manager required.  The caller's intent is
            # carried through rather than a hidden boolean default.
            cost_policy=CostPolicy.BALANCED if optimize_cost else CostPolicy.COST_INSENSITIVE,
            # The old ranking preferred a local model on equal cost.  That was
            # an implicit rule inside the selector; it is now stated here, by
            # the caller, where it can be seen and changed.
            prefer_local=True,
        )
        decision = select_model(facts, request)
        if not decision.selected_model:
            raise ValueError(
                "no eligible model satisfied the canonical router's hard constraints"
            )

        selected = [item for item in viable if item.model_ref in set(decision.selected_models)]
        selected.sort(key=lambda item: decision.selected_models.index(item.model_ref))
        if len(selected) < required_participants:
            raise ValueError(
                "not enough eligible models satisfy the task's independent-participant requirement"
            )

        priced = [item for item in selected if item.expected_cost_usd is not None]
        rationale = decision.explain()
        return ModelSelectionDecision(
            SelectionPolicy.AUTO_INTELLIGENCE,
            tuple(item.model_ref for item in selected),
            (
                sum(float(item.expected_cost_usd) for item in selected)
                if all(item.expected_cost_usd is not None for item in selected)
                else None
            ),
            rationale,
            "high" if priced else "limited",
        )


@dataclass(frozen=True, slots=True)
class LocalCompatibility:
    status: str
    explanation: str
    required_ram_bytes: int
    required_vram_bytes: int
    installed_ram_bytes: int | None
    installed_vram_bytes: int
    expected_offload: str
    storage_bytes: int
    performance_class: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalModelCompatibilityEvaluator:
    """Conservative deterministic RAM/VRAM/disk feasibility policy."""

    def evaluate(
        self,
        profile: LocalComputeProfile,
        *,
        model_size_bytes: int,
        context_tokens: int = 8192,
        runtime_available: bool = True,
    ) -> LocalCompatibility:
        size = max(1, int(model_size_bytes))
        context_overhead = max(512 * 1024**2, int(context_tokens) * 96 * 1024)
        required_ram = int(size * 1.2 + context_overhead)
        required_vram = int(size * 1.05 + context_overhead * 0.5)
        installed_vram = max((gpu.total_vram_bytes or 0 for gpu in profile.gpus), default=0)
        ram_ok = profile.total_ram_bytes is not None and profile.total_ram_bytes >= required_ram
        disk_ok = profile.free_disk_bytes >= int(size * 1.1)
        gpu_ok = installed_vram >= required_vram
        cpu_offload_ok = ram_ok and profile.logical_cores >= 4
        supported = runtime_available and disk_ok and (gpu_ok or cpu_offload_ok)
        if not runtime_available:
            reason = "Required local runtime is not available."
        elif not disk_ok:
            reason = "Free disk space is below the conservative installation requirement."
        elif gpu_ok:
            reason = "GPU VRAM, RAM, and storage satisfy the conservative configuration."
        elif cpu_offload_ok:
            reason = "System RAM supports CPU or mixed CPU/GPU offload; expect reduced speed."
        else:
            reason = "RAM/VRAM is below the conservative feasibility threshold."
        return LocalCompatibility(
            "green" if supported else "red",
            reason,
            required_ram,
            required_vram,
            profile.total_ram_bytes,
            installed_vram,
            "full_gpu" if gpu_ok else "cpu_or_partial" if cpu_offload_ok else "not_feasible",
            int(size * 1.1),
            "fast" if gpu_ok else "usable_cpu" if cpu_offload_ok else "unsupported",
        )


class ModelAccessIntelligenceAgent:
    """Event-driven MAIA coordinator; dormant unless explicitly invoked."""

    role_id = "maia"
    normally_active = False

    def __init__(self, store: "ModelHubStore", *, freshness_hours: int = 24 * 7) -> None:
        self.store = store
        self.freshness = timedelta(hours=freshness_hours)

    def endpoint_for(self, provider: str) -> dict[str, Any]:
        """Level 0/1 endpoint lookup with no network or secret access."""
        record = self.store.current_endpoint(provider)
        if record is None:
            return {"provider": provider, "status": "unverified", "endpoint": "", "level": 3}
        verified = datetime.fromisoformat(str(record["verified_at"]))
        stale = datetime.now(UTC) - verified > self.freshness
        return {**record, "status": "stale" if stale else record["status"], "level": 0}

    def nano_roles(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in MAIA_NANO_ROLES]


class ModelHubStore:
    """Versioned, secret-free Model Hub metadata in the desktop SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_hub_endpoints (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, endpoint TEXT NOT NULL,
                    api_style TEXT NOT NULL, auth_style TEXT NOT NULL,
                    official_source TEXT NOT NULL, verified_at TEXT NOT NULL,
                    confidence REAL NOT NULL, status TEXT NOT NULL, supersedes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_model_hub_endpoint_provider
                    ON model_hub_endpoints(provider, verified_at DESC);
                CREATE TABLE IF NOT EXISTS model_hub_providers (
                    id TEXT PRIMARY KEY, provider_kind TEXT NOT NULL, name TEXT NOT NULL,
                    endpoint TEXT NOT NULL, credential_ref TEXT NOT NULL,
                    status TEXT NOT NULL, pricing_mode TEXT NOT NULL,
                    model_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_hub_pricing (
                    id TEXT PRIMARY KEY, access_source TEXT NOT NULL, provider TEXT NOT NULL,
                    logical_model_id TEXT NOT NULL, currency TEXT NOT NULL,
                    input_per_1m REAL, cached_input_per_1m REAL, output_per_1m REAL,
                    other_rates_json TEXT NOT NULL, user_custom INTEGER NOT NULL,
                    source TEXT NOT NULL, verified_at TEXT NOT NULL, status TEXT NOT NULL,
                    supersedes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_model_hub_pricing_model
                    ON model_hub_pricing(access_source, provider, logical_model_id, verified_at DESC);
                CREATE TABLE IF NOT EXISTS model_hub_teams (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    model_refs_json TEXT NOT NULL, quality_policy TEXT NOT NULL,
                    default_budget REAL, fallback_permissions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_hub_task_costs (
                    task_id TEXT PRIMARY KEY, project_id TEXT, completed_at TEXT NOT NULL,
                    total_external_api_cost REAL, amca_api_cost REAL, byok_cost REAL,
                    local_external_api_cost REAL NOT NULL, local_compute_json TEXT NOT NULL,
                    breakdown_json TEXT NOT NULL, pricing_record_refs_json TEXT NOT NULL,
                    cost_status TEXT NOT NULL
                );
                -- Per-model visibility.  Discovery alone must not publish every
                -- model a provider returns into the chat model selectors.
                CREATE TABLE IF NOT EXISTS model_hub_model_visibility (
                    provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider_id, model_id)
                );
                -- Operator-recorded per-model metadata (context window, max
                -- output tokens).  These are what the operator typed, never a
                -- measurement: the UI presents them as recorded, and nothing
                -- downstream treats them as verified facts.
                CREATE TABLE IF NOT EXISTS model_hub_model_meta (
                    provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    context_window INTEGER, max_output_tokens INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider_id, model_id)
                );
                """
            )
            # Additive, idempotent migration for databases created before the
            # provider include/exclude toggle existed. Existing providers keep
            # the DEFAULT 1: everything configured before this column is
            # exactly the set the operator chose to configure.
            provider_columns = {
                row[1] for row in db.execute("PRAGMA table_info(model_hub_providers)")
            }
            if "enabled" not in provider_columns:
                try:
                    db.execute(
                        "ALTER TABLE model_hub_providers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                    )
                except sqlite3.OperationalError as error:
                    # executescript above implicitly committed the surrounding
                    # transaction, so the check and the ALTER run outside it:
                    # a second process that passed the same check in that
                    # window would lose the race here. The column existing is
                    # the outcome both racers want; anything else re-raises.
                    if "duplicate column name" not in str(error).casefold():
                        raise
            count = db.execute("SELECT COUNT(*) FROM model_hub_endpoints").fetchone()[0]
            if not count:
                timestamp = _now()
                for key, (_, endpoint) in KNOWN_ENDPOINTS.items():
                    db.execute(
                        "INSERT INTO model_hub_endpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                        (f"endpoint.{uuid4().hex}", key, endpoint, "provider_native", "api_key", "bundled official endpoint registry seed", timestamp, 0.9, "current"),
                    )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def current_endpoint(self, provider: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_hub_endpoints WHERE provider=? ORDER BY verified_at DESC LIMIT 1",
                (str(provider).casefold(),),
            ).fetchone()
        return dict(row) if row else None

    def list_providers(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM model_hub_providers ORDER BY name COLLATE NOCASE").fetchall()
            visibility = db.execute(
                "SELECT provider_id, model_id, enabled FROM model_hub_model_visibility"
            ).fetchall()
            meta_rows = db.execute(
                "SELECT provider_id, model_id, context_window, max_output_tokens FROM model_hub_model_meta"
            ).fetchall()
        recorded: set[str] = set()
        enabled: dict[str, set[str]] = {}
        for entry in visibility:
            provider_key = str(entry["provider_id"])
            recorded.add(provider_key)
            if int(entry["enabled"]):
                enabled.setdefault(provider_key, set()).add(str(entry["model_id"]))
        model_meta: dict[str, dict[str, dict[str, int | None]]] = {}
        for entry in meta_rows:
            model_meta.setdefault(str(entry["provider_id"]), {})[str(entry["model_id"])] = {
                "context_window": entry["context_window"],
                "max_output_tokens": entry["max_output_tokens"],
            }
        result = []
        for row in rows:
            item = dict(row)
            item["model_ids"] = json.loads(item.pop("model_ids_json"))
            # The include/exclude toggle is a stored operator decision, not a
            # health fact: a provider is excluded only because the operator
            # excluded it.
            item["enabled"] = bool(item.pop("enabled"))
            # A provider whose catalog predates the visibility table has no
            # rows at all and keeps its whole catalog visible.  Every catalog
            # written since records one row per model, so from then on only
            # explicitly enabled IDs are published.  Adoption is therefore
            # lazy - it happens on the next write, never by opening the store,
            # which must stay side-effect free.
            if str(row["id"]) in recorded:
                visible = enabled.get(str(row["id"]), set())
                item["enabled_model_ids"] = [
                    model for model in item["model_ids"] if model in visible
                ]
            else:
                item["enabled_model_ids"] = list(item["model_ids"])
            # Meta rows are keyed by (provider, model), so one for a model that
            # has left the catalog is kept in the database - a discovery that
            # transiently omits a model must never destroy operator-typed
            # records - but only models in the current catalog are surfaced.
            catalog_meta = model_meta.get(str(row["id"]), {})
            item["model_meta"] = {
                model: catalog_meta[model]
                for model in item["model_ids"]
                if model in catalog_meta
            }
            # credential_ref is intentionally reduced to an opaque configured flag at the API boundary.
            item.pop("credential_ref", None)
            result.append(item)
        return result

    def set_model_visibility(
        self, provider_id: str, model_id: str, enabled: bool
    ) -> dict[str, Any]:
        """Show or hide one discovered model in the model selectors."""
        provider = self.provider(provider_id)
        if provider is None:
            raise ValueError("provider connection not found")
        model = str(model_id).strip()
        if model not in provider["model_ids"]:
            raise ValueError("model is not part of this provider's discovered catalog")
        timestamp = _now()
        with self._transaction() as db:
            # A provider whose catalog predates the visibility table is fully
            # visible with no rows.  The first explicit toggle has to
            # materialize that state before applying the change, otherwise
            # every other model in the catalog would vanish with it.
            existing = {
                str(row["model_id"])
                for row in db.execute(
                    "SELECT model_id FROM model_hub_model_visibility WHERE provider_id=?",
                    (str(provider_id),),
                ).fetchall()
            }
            if not existing:
                for catalog_model in provider["model_ids"]:
                    db.execute(
                        """INSERT OR IGNORE INTO model_hub_model_visibility
                           (provider_id, model_id, enabled, updated_at)
                           VALUES (?, ?, 1, ?)""",
                        (str(provider_id), str(catalog_model), timestamp),
                    )
            db.execute(
                """INSERT INTO model_hub_model_visibility
                   (provider_id, model_id, enabled, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(provider_id, model_id) DO UPDATE SET
                   enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (str(provider_id), model, 1 if enabled else 0, timestamp),
            )
        result = self.provider(provider_id)
        assert result is not None
        return result

    def set_model_meta(
        self, provider_id: str, model_id: str, meta: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Record operator-typed per-model metadata (context window, max output).

        These values are what the operator typed about their own provider —
        AMCA has not measured them.  They are stored so the UI can show them
        and so the operator does not have to remember them; nothing in the
        routing or verification path reads them as an established fact.
        """
        provider = self.provider(provider_id)
        if provider is None:
            raise ValueError("provider connection not found")
        model = str(model_id).strip()
        if model not in provider["model_ids"]:
            raise ValueError("model is not part of this provider's discovered catalog")
        clean: dict[str, int | None] = {}
        for key in ("context_window", "max_output_tokens"):
            if key not in meta:
                continue
            value = meta[key]
            if value is None or value == "":
                clean[key] = None
                continue
            # bool is an int subclass; a boolean token count is never valid.
            # Values beyond SQLite's INTEGER range would otherwise raise
            # OverflowError at bind time, which escapes the 400 mapping as an
            # unhandled server error.
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > 2**63 - 1
            ):
                raise ValueError(f"{key} must be a non-negative integer")
            clean[key] = int(value)
        if not clean:
            raise ValueError("no model metadata fields to record")
        timestamp = _now()
        with self._transaction() as db:
            existing = db.execute(
                "SELECT context_window, max_output_tokens FROM model_hub_model_meta"
                " WHERE provider_id=? AND model_id=?",
                (str(provider_id), model),
            ).fetchone()
            merged = {
                "context_window": existing["context_window"] if existing else None,
                "max_output_tokens": existing["max_output_tokens"] if existing else None,
                **clean,
            }
            db.execute(
                """INSERT INTO model_hub_model_meta
                   (provider_id, model_id, context_window, max_output_tokens, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id, model_id) DO UPDATE SET
                   context_window=excluded.context_window,
                   max_output_tokens=excluded.max_output_tokens,
                   updated_at=excluded.updated_at""",
                (
                    str(provider_id),
                    model,
                    merged["context_window"],
                    merged["max_output_tokens"],
                    timestamp,
                ),
            )
        result = self.provider(provider_id)
        assert result is not None
        return result

    def enabled_model_ids(self, provider_id: str) -> list[str]:
        provider = self.provider(provider_id)
        if provider is None:
            return []
        return [str(model) for model in (provider.get("enabled_model_ids") or ())]

    def provider(self, provider_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.list_providers() if item["id"] == str(provider_id)),
            None,
        )

    def create_provider(self, values: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        provider_kind = _text(values.get("provider_kind"), name="provider_kind", maximum=64).casefold()
        name = _text(values.get("name") or KNOWN_ENDPOINTS.get(provider_kind, (provider_kind, ""))[0], name="name")
        supplied_endpoint = str(values.get("endpoint") or "").strip()
        registry = self.current_endpoint(provider_kind)
        endpoint = _https_endpoint(supplied_endpoint or (registry["endpoint"] if registry else ""), required=False)
        status = "connected_metadata_only" if endpoint else "endpoint_unverified"
        provider_id = f"provider.{uuid4().hex[:24]}"
        credential_ref = f"model-hub.{provider_id}"
        timestamp = _now()
        catalog = _model_ids(values.get("model_ids"))
        if catalog:
            status = "catalog_ready"
        with self._transaction() as db:
            # Named columns: the table grew an `enabled` column with DEFAULT 1,
            # so a positional INSERT would no longer line up.
            db.execute(
                """INSERT INTO model_hub_providers
                   (id, provider_kind, name, endpoint, credential_ref, status,
                    pricing_mode, model_ids_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (provider_id, provider_kind, name, endpoint, credential_ref, status, str(values.get("pricing_mode") or "auto"), _json(catalog), timestamp, timestamp),
            )
        provider = next(item for item in self.list_providers() if item["id"] == provider_id)
        return provider, credential_ref

    def update_provider(self, provider_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        current = self.provider(provider_id)
        if current is None:
            raise ValueError("provider connection not found")
        provider_kind = str(values.get("provider_kind") or current["provider_kind"]).casefold()
        name = _text(values.get("name") or current["name"], name="name")
        endpoint = _https_endpoint(
            str(values.get("endpoint") if "endpoint" in values else current["endpoint"]),
            required=False,
        )
        pricing_mode = str(values.get("pricing_mode") or current["pricing_mode"])
        if pricing_mode not in {"auto", "manual"}:
            raise ValueError("pricing_mode must be auto or manual")
        catalog = _model_ids(values["model_ids"]) if "model_ids" in values else list(current["model_ids"])
        # Include/exclude is an explicit operator decision, so it only changes
        # when the update actually carries it; a metadata edit never flips it.
        enabled = current["enabled"]
        if "enabled" in values:
            if not isinstance(values["enabled"], bool):
                raise ValueError("enabled must be a boolean")
            enabled = values["enabled"]
        status = "catalog_ready" if catalog else "connected_metadata_only" if endpoint else "endpoint_unverified"
        with self._transaction() as db:
            db.execute(
                """UPDATE model_hub_providers
                   SET provider_kind=?, name=?, endpoint=?, status=?, pricing_mode=?,
                       model_ids_json=?, enabled=?, updated_at=? WHERE id=?""",
                (provider_kind, name, endpoint, status, pricing_mode, _json(catalog), 1 if enabled else 0, _now(), provider_id),
            )
            # Catalog adoption made explicit on write: a provider whose catalog
            # predates the visibility table is fully visible with no rows, and
            # every model that has no row yet gets one with enabled=1 here, so
            # a later single-model toggle affects exactly that model instead of
            # silently flipping the whole pre-table catalog's visibility.
            with_rows = {
                str(row["model_id"])
                for row in db.execute(
                    "SELECT model_id FROM model_hub_model_visibility WHERE provider_id=?",
                    (provider_id,),
                ).fetchall()
            }
            for model_id in catalog:
                if model_id in with_rows:
                    continue
                db.execute(
                    "INSERT INTO model_hub_model_visibility"
                    " (provider_id, model_id, enabled, updated_at) VALUES (?, ?, 1, ?)",
                    (str(provider_id), str(model_id), _now()),
                )
        result = self.provider(provider_id)
        assert result is not None
        return result

    def replace_provider_catalog(self, provider_id: str, model_ids: Sequence[str]) -> dict[str, Any]:
        catalog = _model_ids(model_ids)
        if not catalog:
            raise ValueError("provider returned no usable model IDs")
        timestamp = _now()
        with self._transaction() as db:
            previous_row = db.execute(
                "SELECT model_ids_json FROM model_hub_providers WHERE id=?", (provider_id,)
            ).fetchone()
            if previous_row is None:
                raise ValueError("provider connection not found")
            previous = set(_model_ids(json.loads(previous_row["model_ids_json"])))
            recorded = {
                str(row["model_id"])
                for row in db.execute(
                    "SELECT model_id FROM model_hub_model_visibility WHERE provider_id=?",
                    (provider_id,),
                ).fetchall()
            }
            db.execute(
                """UPDATE model_hub_providers SET model_ids_json=?, status='catalog_ready',
                   updated_at=? WHERE id=?""",
                (_json(catalog), timestamp, provider_id),
            )
            # A newly discovered model starts hidden: testing a connection
            # validates credentials and discovery without publishing the whole
            # catalog into chat.  A model that was already visible - either the
            # user enabled it or it predates the visibility table - stays
            # visible, so re-testing a connection never unpublishes anything.
            for model_id in catalog:
                if model_id in recorded:
                    continue
                was_visible = 1 if model_id in previous else 0
                db.execute(
                    """INSERT INTO model_hub_model_visibility
                       (provider_id, model_id, enabled, updated_at) VALUES (?, ?, ?, ?)""",
                    (str(provider_id), str(model_id), was_visible, timestamp),
                )
        result = self.provider(provider_id)
        assert result is not None
        return result

    def delete_provider(self, provider_id: str) -> str:
        with self._transaction() as db:
            row = db.execute("SELECT credential_ref FROM model_hub_providers WHERE id=?", (provider_id,)).fetchone()
            if row is None:
                raise ValueError("provider connection not found")
            db.execute("DELETE FROM model_hub_providers WHERE id=?", (provider_id,))
            # Visibility rows belong to the provider; leaving them behind would
            # keep them forever and block the one-time catalog adoption.
            db.execute(
                "DELETE FROM model_hub_model_visibility WHERE provider_id=?",
                (str(provider_id),),
            )
            db.execute(
                "DELETE FROM model_hub_model_meta WHERE provider_id=?",
                (str(provider_id),),
            )
        return str(row["credential_ref"])

    def list_teams(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM model_hub_teams ORDER BY name COLLATE NOCASE").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["model_refs"] = json.loads(item.pop("model_refs_json"))
            item["fallback_permissions"] = _object(item.pop("fallback_permissions_json"))
            result.append(item)
        return result

    def save_team(self, values: Mapping[str, Any], *, team_id: str | None = None) -> dict[str, Any]:
        refs = values.get("model_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item.strip() for item in refs):
            raise ValueError("model_refs must be a non-empty string array")
        refs = list(dict.fromkeys(item.strip() for item in refs))
        if len(refs) > 16:
            raise ValueError("a Model Team may contain at most 16 models")
        name = _text(values.get("name"), name="name")
        description = _text(values.get("description", ""), name="description", maximum=500, required=False)
        budget = values.get("default_budget")
        if budget in (None, ""):
            budget = None
        else:
            budget = float(budget)
            if not math.isfinite(budget) or budget < 0:
                raise ValueError("default_budget must be finite and nonnegative")
        timestamp = _now()
        team_id = team_id or f"team.{uuid4().hex[:24]}"
        fallback = values.get("fallback_permissions") if isinstance(values.get("fallback_permissions"), Mapping) else {}
        with self._transaction() as db:
            existing = db.execute("SELECT created_at FROM model_hub_teams WHERE id=?", (team_id,)).fetchone()
            created_at = str(existing["created_at"]) if existing else timestamp
            db.execute(
                """INSERT OR REPLACE INTO model_hub_teams
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (team_id, name, description, _json(refs), str(values.get("quality_policy") or "quality_protected"), budget, _json(fallback), created_at, timestamp),
            )
        return next(item for item in self.list_teams() if item["id"] == team_id)

    def delete_team(self, team_id: str) -> None:
        with self._transaction() as db:
            deleted = db.execute("DELETE FROM model_hub_teams WHERE id=?", (team_id,)).rowcount
            if not deleted:
                raise ValueError("Model Team not found")

    def list_pricing(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM model_hub_pricing ORDER BY provider, logical_model_id, verified_at DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["other_rates"] = _object(item.pop("other_rates_json"))
            item["user_custom"] = bool(item["user_custom"])
            result.append(item)
        return result

    def record_task_cost(self, values: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _text(values.get("task_id"), name="task_id", maximum=160)
        status = CostStatus(str(values.get("cost_status", CostStatus.UNAVAILABLE.value)))
        costs: dict[str, float | None] = {}
        for key in ("total_external_api_cost", "amca_api_cost", "byok_cost"):
            raw = values.get(key)
            if raw is None:
                costs[key] = None
            else:
                number = float(raw)
                if not math.isfinite(number) or number < 0:
                    raise ValueError(f"{key} must be finite and nonnegative")
                costs[key] = number
        local_cost = float(values.get("local_external_api_cost", 0))
        with self._transaction() as db:
            db.execute(
                """INSERT OR REPLACE INTO model_hub_task_costs
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, values.get("project_id"), _now(), costs["total_external_api_cost"], costs["amca_api_cost"], costs["byok_cost"], local_cost, _json(values.get("local_compute", {})), _json(values.get("breakdown", [])), _json(values.get("pricing_record_refs", [])), status.value),
            )
        return self.task_cost(task_id) or {}

    def task_cost(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM model_hub_task_costs WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["local_compute"] = _object(item.pop("local_compute_json"))
        item["breakdown"] = json.loads(item.pop("breakdown_json"))
        item["pricing_record_refs"] = json.loads(item.pop("pricing_record_refs_json"))
        return item


__all__ = [
    "AccessSource", "AutoIntelligenceRouter", "CostQualityGate", "CostStatus",
    "LocalCompatibility", "LocalModelCompatibilityEvaluator", "MAIA_NANO_ROLES",
    "ModelAccessIntelligenceAgent", "ModelCandidate", "ModelHubStore",
    "ModelSelectionDecision", "NanoAgentRole", "PricingStatus", "SelectionPolicy",
]
