"""AMCA Studio v1.0.0 Model Hub Ecosystem Expansion Foundation.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-ECOSYSTEM-EXPANSION-FOUNDATION-01

Responsibilities:
1. Enhanced Local Model Registry:
   - Tracks model name, provider, locality (local/cloud), version, capability tags, availability state, user notes.
   - Recorded facts only; zero inferred claims or subjective ratings.
2. Provider Connection Management:
   - Supports OpenAI-compatible, Anthropic-compatible, local Ollama, and local llama.cpp/vLLM endpoints.
   - Credential presence state only; fingerprint-based identification; zero secret exposure.
3. Local Model Discovery Scanner:
   - Scans local instances/directories for available model tags; imports metadata.
   - Strictly enforces no automatic downloads and no automatic model routing.
4. Model Capability Transparency:
   - Separates verified capabilities from unknown capabilities with explicit evidence references.


NOT THE CANONICAL MODEL REGISTRY.  The canonical registry is
``model_registry.ModelRegistryStore``, and it is the only one the
production service instantiates.  This module is a historical,
test-only implementation retained for its originating milestone; it
must not be wired into production.  It carries hardcoded seed models,
so treating it as a source of model facts would reintroduce exactly
the name-guessing the canonical registry removed.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ModelRegistryEntry:
    model_id: str
    model_name: str
    provider: str  # "ollama" | "llamacpp" | "vllm" | "openai_compatible" | "anthropic_compatible"
    locality: str  # "local" | "cloud"
    version: str
    capability_tags: List[str]
    availability_state: str  # "ready" | "offline" | "unreachable"
    user_notes: str
    verified_capabilities: List[str]
    unknown_capabilities: List[str]
    evidence_source: str


@dataclass(frozen=True)
class ProviderConnectionDescriptor:
    provider_id: str
    provider_type: str
    endpoint_url: str
    has_credential: bool
    credential_fingerprint: Optional[str]  # e.g. "sk-...a1b2", never raw key
    status: str  # "connected" | "disconnected" | "unreachable"


class ModelDiscoveryScanner:
    """Discovers locally installed models from local daemons or directories without automatic downloading."""

    @staticmethod
    def scan_local_models(mock_daemon_response: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        # In actual operation, this inspects local Ollama or llama.cpp directories
        raw_items = mock_daemon_response or [
            {
                "name": "llama3.1:8b-instruct-q4_K_M",
                "provider": "ollama",
                "locality": "local",
                "size_gb": 4.9,
                "modified_at": "2026-08-15T12:00:00Z",
                "reported_capabilities": ["chat", "code", "json_format"],
            },
            {
                "name": "qwen2.5-coder:7b-instruct",
                "provider": "ollama",
                "locality": "local",
                "size_gb": 4.7,
                "modified_at": "2026-08-20T10:00:00Z",
                "reported_capabilities": ["code", "reasoning"],
            },
        ]
        return raw_items


class ModelEcosystemManager:
    """Manages registered models, provider connections, and capability transparency."""

    def __init__(self) -> None:
        self._models: Dict[str, ModelRegistryEntry] = {}
        self._providers: Dict[str, ProviderConnectionDescriptor] = {}

    def register_provider(
        self,
        provider_id: str,
        provider_type: str,
        endpoint_url: str,
        raw_credential: Optional[str] = None,
        status: str = "connected",
    ) -> ProviderConnectionDescriptor:
        has_cred = bool(raw_credential and len(raw_credential.strip()) > 0)
        fingerprint = None
        if has_cred and raw_credential:
            h = hashlib.sha256(raw_credential.encode("utf-8")).hexdigest()[:4]
            prefix = raw_credential[:3] if len(raw_credential) >= 3 else "key"
            fingerprint = f"{prefix}...{h}"

        desc = ProviderConnectionDescriptor(
            provider_id=provider_id,
            provider_type=provider_type,
            endpoint_url=endpoint_url,
            has_credential=has_cred,
            credential_fingerprint=fingerprint,
            status=status,
        )
        self._providers[provider_id] = desc
        return desc

    def register_model(
        self,
        model_id: str,
        model_name: str,
        provider: str,
        locality: str,
        version: str,
        capability_tags: List[str],
        availability_state: str = "ready",
        user_notes: str = "",
        verified_capabilities: Optional[List[str]] = None,
        evidence_source: str = "local_manifest",
    ) -> ModelRegistryEntry:
        verified = verified_capabilities or []
        unknown = [cap for cap in capability_tags if cap not in verified]

        entry = ModelRegistryEntry(
            model_id=model_id,
            model_name=model_name,
            provider=provider,
            locality=locality,
            version=version,
            capability_tags=capability_tags,
            availability_state=availability_state,
            user_notes=user_notes,
            verified_capabilities=verified,
            unknown_capabilities=unknown,
            evidence_source=evidence_source,
        )
        self._models[model_id] = entry
        return entry

    def get_model(self, model_id: str) -> Optional[ModelRegistryEntry]:
        return self._models.get(model_id)

    def list_models(self) -> List[ModelRegistryEntry]:
        return list(self._models.values())

    def list_providers(self) -> List[ProviderConnectionDescriptor]:
        return list(self._providers.values())

    def import_discovered_models(self, discovered_list: List[Dict[str, Any]]) -> List[ModelRegistryEntry]:
        imported: List[ModelRegistryEntry] = []
        for item in discovered_list:
            mid = f"{item['provider']}-{item['name']}".replace(":", "-").replace("/", "-")
            caps = item.get("reported_capabilities", [])
            # Imported from daemon tags: capabilities are recorded, verification requires explicit human test
            entry = self.register_model(
                model_id=mid,
                model_name=item["name"],
                provider=item["provider"],
                locality=item.get("locality", "local"),
                version="latest",
                capability_tags=caps,
                availability_state="ready",
                user_notes="Discovered via local daemon scan",
                verified_capabilities=[],  # Explicitly unverified until tested
                evidence_source=f"daemon_discovery_scan:{item['provider']}",
            )
            imported.append(entry)
        return imported

