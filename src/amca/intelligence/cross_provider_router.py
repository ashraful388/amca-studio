"""M1 cross-provider router integration (bridge from Model Hub to M4 Router).

Additive module introduced by the M1 Cross-Provider Router Integration
milestone (opencode/amca-v0.6.0-m1-cross-provider-router-integration-01).

Implements the provider-neutral adapter that connects AMCA's existing
configured Model Hub/provider models to the M4 Model Router foundation,
enabling real provider-neutral selections across configured local and API
models.

Critical reuse rules honored:
- Reuses EXISTING adapters/configuration for providers already implemented
  (OpenAI, Anthropic, Gemini, Kimi, DeepSeek, Mistral, xAI, Tabitoken,
  generic OpenAI-compatible, Ollama)
- Reuses EXISTING M4 Router (router_foundation.route) and its
  RoutingCandidate/RoutingPolicy/RoutingOutcome contracts
- Reuses EXISTING Model Hub store for provider configurations
- Does NOT create another model registry
- Does NOT add new provider adapters
- Does NOT expose API credentials inside Router candidates/evidence

Authority rules:
- The Router only ranks and rejects; it cannot invent providers or models
- Candidates must be locally supplied from Model Hub configuration
- Credential resolution stays inside existing provider/configuration infrastructure
- Evidence is whitelisted and privacy-safe; no credential material
- Every failure is a structured rejection with a machine-readable reason
"""

import dataclasses
import math
from typing import Any, Callable, ClassVar, Mapping, Optional, Sequence, Tuple

from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    contract_post_init,
)
from .gates import FeatureGateRegistry
from .router_foundation import (
    ALLOWED_ROUTING_EVIDENCE_KEYS,
    RejectedCandidate,
    RoutingCandidate,
    RoutingOutcome,
    RoutingOutcomeStatus,
    RoutingPolicy,
    _eligibility_rejection,
    _rank_key,
    route,
    route_with_fallback,
)
from ..model_hub import (
    AccessSource,
    ModelHubStore,
    ModelCandidate,
)
from ..models.base import ModelAdapter
from ..models.ollama import OllamaAdapter
from ..models.openai_compatible import OpenAICompatibleAdapter
from ..models.openai import OpenAIAdapter
from ..models.anthropic import AnthropicAdapter
from ..models.gemini import GeminiAdapter
from ..models.kimi import KimiAdapter
from ..models.tabitoken import TabitokenAdapter
from ..models.base import ModelCapabilities
from ..models.tool_protocol import effective_tool_support


#: Map of provider kinds to their AccessSource enum
_PROVIDER_ACCESS_SOURCE: Mapping[str, AccessSource] = {
    "ollama": AccessSource.LOCAL,
    "openai": AccessSource.BYOK,
    "anthropic": AccessSource.BYOK,
    "google": AccessSource.BYOK,
    "moonshot": AccessSource.BYOK,
    "deepseek": AccessSource.BYOK,
    "mistral": AccessSource.BYOK,
    "xai": AccessSource.BYOK,
    "tabitoken": AccessSource.BYOK,
    "openai_compatible": AccessSource.BYOK,
}

#: Provider kind to canonical adapter class (for credential safety verification)
_PROVIDER_ADAPTER_CLASSES: Mapping[str, type] = {
    "ollama": OllamaAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "google": GeminiAdapter,
    "moonshot": KimiAdapter,
    "deepseek": OpenAICompatibleAdapter,
    "mistral": OpenAICompatibleAdapter,
    "xai": OpenAICompatibleAdapter,
    "tabitoken": TabitokenAdapter,
    "openai_compatible": OpenAICompatibleAdapter,
}

#: Provider kind to human-readable name (for evidence/reporting)
_PROVIDER_NAMES: Mapping[str, str] = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google / Gemini",
    "moonshot": "Moonshot / Kimi",
    "deepseek": "DeepSeek",
    "mistral": "Mistral",
    "xai": "xAI",
    "tabitoken": "Tabitoken",
    "openai_compatible": "OpenAI-compatible",
}


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _capabilities_from_adapter(adapter: ModelAdapter) -> Tuple[str, ...]:
    """Project an existing ModelCapabilities record into capability names."""
    caps = adapter.capabilities()
    names = []
    for field in dataclasses.fields(caps):
        value = getattr(caps, field.name)
        if isinstance(value, bool) and value:
            names.append(field.name)
    return tuple(names)


def _estimate_cost_per_1m(pricing: Mapping[str, Any], model: str) -> Optional[float]:
    """Extract estimated cost per 1M tokens from pricing record."""
    input_cost = pricing.get("input_per_1m")
    output_cost = pricing.get("output_per_1m")
    if input_cost is not None and output_cost is not None:
        try:
            return float(input_cost + output_cost) / 2.0
        except (TypeError, ValueError):
            pass
    return None


def _normalize_model_id(model_id: str) -> str:
    """Normalize model ID to a canonical form for candidate_id."""
    return model_id.lower().replace(".", "-").replace("_", "-")


def _build_candidate_id(provider_kind: str, model_id: str, provider_id: str) -> str:
    """Build a stable, unique candidate identifier."""
    norm_model = _normalize_model_id(model_id)
    norm_provider = provider_kind.lower().replace(".", "-")
    return f"router-{norm_provider}-{norm_model}-{provider_id[:8]}"


def _model_hub_provider_to_routing_candidates(
    meta_factory: Callable[[str], ContractMetadata],
    provider: Mapping[str, Any],
    pricing_records: Sequence[Mapping[str, Any]],
    credential_store,
) -> Sequence[RoutingCandidate]:
    """Convert a Model Hub provider record to RoutingCandidates.

    Each configured model_id in the provider becomes one RoutingCandidate.
    Only models with valid credentials (where applicable) are marked available.
    """
    provider_kind = str(provider["provider_kind"]).casefold()
    provider_id = str(provider["id"])
    model_ids = list(provider.get("model_ids", []))
    endpoint = str(provider.get("endpoint", ""))
    status = str(provider.get("status", ""))

    if not model_ids:
        return []

    # Check credential availability for cloud providers
    access_source = _PROVIDER_ACCESS_SOURCE.get(provider_kind, AccessSource.BYOK)
    has_credential = True
    if access_source != AccessSource.LOCAL:
        cred_ref = provider.get("credential_ref") or f"model-hub.{provider_id}"
        try:
            credential_store.get(cred_ref)
        except Exception:
            has_credential = False

    # Build pricing lookup
    pricing_map = {}
    for p in pricing_records:
        if p["provider"] == provider_kind:
            pricing_map[p["logical_model_id"]] = p

    candidates = []
    for model_id in model_ids:
        # Build candidate_id
        candidate_id = _build_candidate_id(provider_kind, model_id, provider_id)

        # Determine capabilities from adapter class (no secrets, no network)
        adapter_class = _PROVIDER_ADAPTER_CLASSES.get(provider_kind)
        capabilities = ()
        if adapter_class is not None:
            try:
                is_local = (access_source == AccessSource.LOCAL)
                adapter_tools = getattr(adapter_class, "supports_tools", True)
                tools_supported = effective_tool_support(
                    adapter_supports_tools=adapter_tools,
                    model_name=model_id,
                    route_supports_tools=True,
                )
                if provider_kind == "ollama":
                    adapter = OllamaAdapter(model=model_id, base_url=endpoint or "http://127.0.0.1:11434")
                    caps = adapter.capabilities()
                elif provider_kind in ("openai", "openai_compatible", "deepseek", "mistral", "xai"):
                    adapter = OpenAICompatibleAdapter(model=model_id, base_url=endpoint or "http://127.0.0.1:8080", api_key="dummy")
                    caps = adapter.capabilities()
                elif provider_kind == "anthropic":
                    adapter = AnthropicAdapter(model=model_id, api_key="dummy")
                    caps = adapter.capabilities()
                elif provider_kind == "google":
                    adapter = GeminiAdapter(model=model_id, api_key="dummy")
                    caps = adapter.capabilities()
                else:
                    caps = ModelCapabilities(is_local=is_local)

                fields = {f.name: getattr(caps, f.name) for f in dataclasses.fields(caps)}
                fields["supports_tools"] = tools_supported
                fields["is_local"] = is_local
                capabilities = tuple(k for k, v in fields.items() if isinstance(v, bool) and v)
            except Exception:
                capabilities = ()

        # Estimated cost from pricing records
        estimated_cost = None
        pricing = pricing_map.get(model_id)
        if pricing:
            estimated_cost = _estimate_cost_per_1m(pricing, model_id)

        # Latency estimate (not available without live call)
        latency_ms = None

        # Available if credential exists (for cloud) and status is ready
        available = has_credential and status in {"connected_metadata_only", "catalog_ready"}

        candidate = RoutingCandidate(
            meta=meta_factory(f"rc-{candidate_id}"),
            candidate_id=candidate_id,
            provider=provider_kind,
            model=model_id,
            local_execution=(access_source == AccessSource.LOCAL),
            capabilities=capabilities,
            estimated_cost=estimated_cost,
            latency_ms=latency_ms,
            available=available,
        )
        candidates.append(candidate)

    return candidates


def build_routing_candidates_from_model_hub(
    meta_factory: Callable[[str], ContractMetadata],
    model_hub_store: ModelHubStore,
    credential_store,
) -> Tuple[RoutingCandidate, ...]:
    """Build RoutingCandidates from all configured Model Hub providers.

    This is the main entry point for converting Model Hub state into
    Router-ready candidates. It queries the Model Hub store for all
    providers, their model catalogs, and pricing, then converts each
    configured model into a RoutingCandidate.

    Returns a tuple of RoutingCandidate ready for use with
    router_foundation.route or route_with_fallback.
    """
    providers = model_hub_store.list_providers()
    pricing_records = model_hub_store.list_pricing()

    all_candidates = []
    for provider in providers:
        candidates = _model_hub_provider_to_routing_candidates(
            meta_factory=meta_factory,
            provider=provider,
            pricing_records=pricing_records,
            credential_store=credential_store,
        )
        all_candidates.extend(candidates)

    return tuple(all_candidates)


def route_cross_provider(
    *,
    meta_factory: Callable[[str], ContractMetadata],
    request_ref: str,
    model_hub_store: ModelHubStore,
    credential_store,
    policy: RoutingPolicy,
    gates: FeatureGateRegistry,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> RoutingOutcome:
    """One bounded routing decision over Model Hub configured models.

    This is the main cross-provider routing function. It builds
    RoutingCandidates from the Model Hub store, then delegates to the
    M4 Router foundation for the actual routing decision.

    Returns a RoutingOutcome with the selected candidate and full evidence.
    """
    candidates = build_routing_candidates_from_model_hub(
        meta_factory=meta_factory,
        model_hub_store=model_hub_store,
        credential_store=credential_store,
    )
    return route(
        meta=meta_factory(request_ref),
        request_ref=request_ref,
        candidates=candidates,
        policy=policy,
        gates=gates,
        cancel_check=cancel_check,
    )


def route_cross_provider_with_fallback(
    *,
    meta_factory: Callable[[str], ContractMetadata],
    request_ref: str,
    model_hub_store: ModelHubStore,
    credential_store,
    policies: Sequence[RoutingPolicy],
    gates: FeatureGateRegistry,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[RoutingOutcome, int]:
    """Walk a bounded fallback chain of policies over Model Hub models.

    The first policy that routes wins; every rejected candidate accumulates
    across the chain; exhausting the chain yields one BLOCKED outcome.
    """
    candidates = build_routing_candidates_from_model_hub(
        meta_factory=meta_factory,
        model_hub_store=model_hub_store,
        credential_store=credential_store,
    )
    return route_with_fallback(
        meta=meta_factory(request_ref),
        request_ref=request_ref,
        candidates=candidates,
        policies=policies,
        gates=gates,
        cancel_check=cancel_check,
    )


def resolve_adapter_for_candidate(
    candidate: RoutingCandidate,
    model_hub_store: ModelHubStore,
    credential_store,
) -> ModelAdapter:
    """Resolve a Router-selected candidate back to its configured ModelAdapter.

    This is the adapter resolution seam: after the Router selects a candidate,
    this function reconstructs the appropriate ModelAdapter using the
    stored provider configuration and credentials. Credentials never leave
    the credential store.

    Raises:
        ValueError: If the candidate cannot be resolved (deleted/stale config,
                   missing credential, unknown provider).
    """
    provider_kind = candidate.provider
    model = candidate.model

    # Find the provider in Model Hub
    providers = model_hub_store.list_providers()
    provider = None
    for p in providers:
        if p["provider_kind"] == provider_kind and model in p.get("model_ids", []):
            provider = p
            break

    if provider is None:
        raise ValueError(
            f"no configured provider found for candidate {candidate.candidate_id}"
        )

    provider_id = provider["id"]
    endpoint = provider.get("endpoint", "")
    access_source = _PROVIDER_ACCESS_SOURCE.get(provider_kind, AccessSource.BYOK)

    # Get credential
    if access_source != AccessSource.LOCAL:
        cred_ref = provider.get("credential_ref") or f"model-hub.{provider_id}"
        try:
            credential = credential_store.get(cred_ref)
        except Exception as e:
            raise ValueError(
                f"credential not found for provider {provider_id}: {e}"
            )
    else:
        credential = ""

    # Instantiate the appropriate adapter
    adapter_class = _PROVIDER_ADAPTER_CLASSES.get(provider_kind)
    if adapter_class is None:
        raise ValueError(f"no adapter class for provider kind {provider_kind}")

    # Common adapter kwargs
    is_local = (access_source == AccessSource.LOCAL)
    adapter_tools = getattr(adapter_class, "supports_tools", True)
    tools_supported = effective_tool_support(
        adapter_supports_tools=adapter_tools,
        model_name=model,
        route_supports_tools=True,
    )
    adapter_kwargs = {
        "adapter_id": f"router-{candidate.candidate_id}",
        "provider": provider_kind,
        "model": model,
        "capabilities": ModelCapabilities(
            is_local=is_local,
            supports_tools=tools_supported,
        ),
    }

    if access_source == AccessSource.LOCAL:
        # Local backends (Ollama, local openai-compatible)
        if provider_kind == "ollama":
            adapter_kwargs["endpoint"] = endpoint or "http://127.0.0.1:11434"
        elif provider_kind == "openai_compatible":
            # LocalOpenAICompatibleAdapter requires base_url and allow_remote=False
            adapter_kwargs["base_url"] = endpoint or "http://127.0.0.1:8080"
            adapter_kwargs["allow_remote"] = False
        adapter = adapter_class(**adapter_kwargs)
    else:
        # BYOK cloud providers
        if provider_kind == "openai_compatible":
            # OpenAICompatibleAdapter for remote endpoints
            adapter_kwargs["base_url"] = endpoint
            adapter_kwargs["api_key"] = credential
            adapter_kwargs["allow_remote"] = True
        elif provider_kind in ("deepseek", "mistral", "xai", "moonshot"):
            # These use OpenAICompatibleAdapter with specific base URLs
            adapter_kwargs["base_url"] = endpoint
            adapter_kwargs["api_key"] = credential
            adapter_kwargs["allow_remote"] = True
        else:
            # Standard adapters (OpenAI, Anthropic, Gemini, Kimi, Tabitoken)
            adapter_kwargs["endpoint"] = endpoint
            adapter_kwargs["secret"] = credential
        adapter = adapter_class(**adapter_kwargs)

    return adapter


__all__ = [
    "build_routing_candidates_from_model_hub",
    "route_cross_provider",
    "route_cross_provider_with_fallback",
    "resolve_adapter_for_candidate",
    "_PROVIDER_ACCESS_SOURCE",
    "_PROVIDER_ADAPTER_CLASSES",
    "_PROVIDER_NAMES",
]