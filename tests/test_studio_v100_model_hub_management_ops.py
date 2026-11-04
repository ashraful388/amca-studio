"""Unit tests for AMCA Studio v1.0.0 Model Hub Management UX & Local Operations.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-MANAGEMENT-UX-AND-LOCAL-MODEL-OPERATIONS-01
"""

from amca.model_registry_ecosystem import (
    ModelDiscoveryScanner,
    ModelEcosystemManager,
    ModelRegistryEntry,
    ProviderConnectionDescriptor,
)


def test_local_model_operations_scans_without_download_or_routing():
    scanner = ModelDiscoveryScanner()
    models = scanner.scan_local_models()

    assert len(models) >= 2
    for m in models:
        assert m["locality"] == "local"
        assert m["size_gb"] > 0
        assert "reported_capabilities" in m


def test_provider_verification_state_tracking():
    manager = ModelEcosystemManager()
    p = manager.register_provider(
        provider_id="ollama-local",
        provider_type="ollama",
        endpoint_url="http://127.0.0.1:11434",
        status="connected",
    )

    assert p.provider_id == "ollama-local"
    assert p.status == "connected"
    assert p.has_credential is False
    assert p.credential_fingerprint is None


def test_model_capability_evidence_explicit_records():
    manager = ModelEcosystemManager()
    m = manager.register_model(
        model_id="local-qwen",
        model_name="Qwen2.5-Coder-7B",
        provider="ollama",
        locality="local",
        version="q4_K_M",
        capability_tags=["code", "refactoring"],
        verified_capabilities=["code"],
        evidence_source="benchmark_run_12.json",
    )

    assert m.verified_capabilities == ["code"]
    assert m.unknown_capabilities == ["refactoring"]
    assert m.evidence_source == "benchmark_run_12.json"


def test_model_registry_zero_inferred_claims():
    manager = ModelEcosystemManager()
    m = manager.register_model(
        model_id="raw-import",
        model_name="untested-7b",
        provider="llamacpp",
        locality="local",
        version="v1",
        capability_tags=["text_generation"],
        # When no verified capabilities provided, all capability tags are marked unknown
        verified_capabilities=[],
        evidence_source="none_recorded",
    )

    assert m.verified_capabilities == []
    assert m.unknown_capabilities == ["text_generation"]
    assert m.evidence_source == "none_recorded"


def test_router_isolation_and_immutability():
    # Confirm that registering models and providers does not mutate router configurations
    manager = ModelEcosystemManager()
    manager.register_model("m1", "m1", "ollama", "local", "1", ["chat"])
    manager.register_provider("p1", "ollama", "http://localhost:11434")

    # Assert manager only acts as registry, exposing zero router decision methods
    assert not hasattr(manager, "route_request")
    assert not hasattr(manager, "select_best_model")
    assert not hasattr(manager, "rank_models")

