"""Unit tests for AMCA Studio v1.0.0 Model Hub Ecosystem Expansion.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-ECOSYSTEM-EXPANSION-FOUNDATION-01
"""

from amca.model_registry_ecosystem import (
    ModelDiscoveryScanner,
    ModelEcosystemManager,
    ModelRegistryEntry,
    ProviderConnectionDescriptor,
)


def test_model_registry_enhancement_and_user_notes():
    manager = ModelEcosystemManager()
    entry = manager.register_model(
        model_id="local-deepseek-r1-7b",
        model_name="DeepSeek-R1-Distill-Qwen-7B",
        provider="ollama",
        locality="local",
        version="q4_K_M",
        capability_tags=["reasoning", "code", "chat"],
        availability_state="ready",
        user_notes="Primary local reasoning specialist for analytical subtasks",
        verified_capabilities=["code", "chat"],
    )

    assert entry.model_id == "local-deepseek-r1-7b"
    assert entry.provider == "ollama"
    assert entry.locality == "local"
    assert entry.availability_state == "ready"
    assert entry.verified_capabilities == ["code", "chat"]
    assert entry.unknown_capabilities == ["reasoning"]
    assert "Primary local reasoning specialist" in entry.user_notes


def test_provider_connection_credential_fingerprint_never_exposes_secrets():
    manager = ModelEcosystemManager()
    desc = manager.register_provider(
        provider_id="openai-azure",
        provider_type="openai_compatible",
        endpoint_url="https://api.openai.com/v1",
        raw_credential="sk-secret1234567890abcdef1234567890",
        status="connected",
    )

    assert desc.has_credential is True
    assert desc.credential_fingerprint is not None
    assert "sk-" in desc.credential_fingerprint
    # Strict secret protection: raw credential must NOT be stored or exposed
    assert "sk-secret1234567890abcdef1234567890" != desc.credential_fingerprint
    assert not hasattr(desc, "raw_credential")
    assert not hasattr(desc, "api_key")


def test_local_model_discovery_scanner_imports_without_automatic_download():
    manager = ModelEcosystemManager()
    discovered = ModelDiscoveryScanner.scan_local_models()
    assert len(discovered) >= 2

    imported = manager.import_discovered_models(discovered)
    assert len(imported) == len(discovered)

    for m in imported:
        assert m.locality == "local"
        assert m.availability_state == "ready"
        # Discovered models are marked unverified until tested by operator
        assert m.verified_capabilities == []
        assert "daemon_discovery_scan" in m.evidence_source


def test_model_capability_transparency_separates_verified_vs_unknown():
    manager = ModelEcosystemManager()
    m = manager.register_model(
        model_id="qwen-coder",
        model_name="Qwen2.5-Coder-7B",
        provider="vllm",
        locality="local",
        version="v1.0",
        capability_tags=["code_generation", "refactoring", "tool_use"],
        verified_capabilities=["code_generation"],
        evidence_source="unit_test_eval_2026_08",
    )

    assert m.verified_capabilities == ["code_generation"]
    assert set(m.unknown_capabilities) == {"refactoring", "tool_use"}
    assert m.evidence_source == "unit_test_eval_2026_08"


def test_local_first_model_listing_and_provider_counts():
    manager = ModelEcosystemManager()
    manager.register_provider("ollama-local", "ollama", "http://127.0.0.1:11434")
    manager.register_model("m1", "model1", "ollama", "local", "1.0", ["chat"])
    manager.register_model("m2", "model2", "ollama", "local", "1.0", ["code"])

    models = manager.list_models()
    providers = manager.list_providers()

    assert len(models) == 2
    assert len(providers) == 1
    assert all(m.locality == "local" for m in models)

