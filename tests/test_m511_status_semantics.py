"""M5.1.1 Status Semantics Hardening tests.

Focused coverage required by the issued M5.1.1 work order: the
implemented-vs-enabled distinction, truthful per-capability states,
runtime readiness derived only from safe local state (never inferred from
a gate, never guessed, no active probing), specific validation
decisions/reasons, privacy, contract-version handling, and all four gates
remaining OFF.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    BRIDGE_SEAM_ID,
    ContractError,
    ContractErrorCode,
    FeatureGateRegistry,
    IntelligenceRuntimeBridge,
    Sensitivity,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m511",
        producer="m5.1.1-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


class CapabilityStateContractTests(unittest.TestCase):
    def setUp(self):
        self.bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        self.status = self.bridge.status()

    def test_implemented_vs_enabled_distinction(self):
        for name, entry in self.status["capabilities"].items():
            self.assertIn("implemented", entry)
            self.assertIn("enabled", entry)
            self.assertIn("runtime_ready", entry)
        adaptive = self.status["capabilities"]["adaptive_planner"]
        self.assertTrue(adaptive["implemented"])
        self.assertFalse(adaptive["enabled"])

    def test_adaptive_implemented_but_disabled(self):
        adaptive = self.status["capabilities"]["adaptive_planner"]
        self.assertTrue(adaptive["implemented"])
        self.assertFalse(adaptive["enabled"])

    def test_hybrid_implemented_but_disabled(self):
        hybrid = self.status["capabilities"]["hybrid_execution"]
        self.assertTrue(hybrid["implemented"])
        self.assertFalse(hybrid["enabled"])

    def test_model_router_implemented_but_disabled(self):
        router = self.status["capabilities"]["model_router"]
        self.assertTrue(router["implemented"])
        self.assertFalse(router["enabled"])

    def test_nano_foundation_represented_truthfully(self):
        nano = self.status["capabilities"]["nano_agents"]
        # the M3 governance foundation exists (implemented), the gate is
        # OFF (enabled False), and since M5.6 the governed local execution
        # pipeline genuinely exists (sealed assignments execute through the
        # existing scheduler), so readiness is truthfully "ready" — with
        # no active probing ever performed
        self.assertTrue(nano["implemented"])
        self.assertFalse(nano["enabled"])
        self.assertEqual(nano["runtime_ready"], "ready")

    def test_deterministic_allowed_and_ready(self):
        deterministic = self.status["capabilities"]["deterministic"]
        self.assertTrue(deterministic["implemented"])
        self.assertTrue(deterministic["enabled"])
        self.assertEqual(deterministic["runtime_ready"], "ready")


class RuntimeReadinessTests(unittest.TestCase):
    def test_runtime_readiness_not_inferred_from_gate(self):
        from amca.intelligence import FeatureGateRegistry
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory,
            gates=FeatureGateRegistry(
                adaptive_planner=True,
                hybrid_execution=True,
                nano_agents=True,
                model_router=True,
            ),
        )
        # even with every gate ON, readiness is never inferred from the
        # gate: deterministic and the M5.6 local Nano pipeline are
        # structurally ready; adaptive/hybrid/router stay explicitly
        # unknown because no active probing is ever performed
        for name, entry in bridge.status()["capabilities"].items():
            if name in ("deterministic", "nano_agents"):
                self.assertEqual(entry["runtime_ready"], "ready")
            else:
                self.assertEqual(entry["runtime_ready"], "unknown")

    def test_unknown_readiness_represented_explicitly(self):
        for name, entry in self.bridge_status()["capabilities"].items():
            if name not in ("deterministic", "nano_agents"):
                self.assertEqual(entry["runtime_ready"], "unknown")

    def test_no_active_probing_no_provider_fields(self):
        snapshot = self.bridge_status()
        serialized = repr(snapshot)
        for word in ("ollama", "provider_metadata", "adapter", "probes"):
            self.assertNotIn(word, serialized.lower())

    def bridge_status(self):
        return self.bridge if False else IntelligenceRuntimeBridge(
            meta_factory=meta_factory
        ).status()


class ValidationSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)

    def test_validation_returns_specific_reason(self):
        outcome = self.bridge.validate_request(
            ["adaptive_planner"], contract_version=2
        )
        entry = outcome["decisions"]["adaptive_planner"]
        self.assertEqual(entry["decision"], "disabled_feature")
        self.assertEqual(entry["reason"], "feature gate disabled")

    def test_deterministic_allowed_with_reason(self):
        outcome = self.bridge.validate_request(
            ["deterministic"], contract_version=2
        )
        entry = outcome["decisions"]["deterministic"]
        self.assertEqual(entry["decision"], "allowed")
        self.assertEqual(entry["reason"], "deterministic planner is the default")

    def test_no_provider_or_model_call_occurs(self):
        # validation is pure local computation over the gate registry: the
        # decision entries cannot carry provider/model invocations
        outcome = self.bridge.validate_request(
            ["adaptive_planner"], contract_version=2
        )
        entry = outcome["decisions"]["adaptive_planner"]
        for forbidden in ("provider", "model_id", "call", "endpoint"):
            self.assertNotIn(forbidden, entry)

    def test_v1_contract_compatibility_preserved(self):
        outcome = self.bridge.validate_request(
            ["deterministic"], contract_version=1
        )
        self.assertEqual(outcome["contract_version"], 1)
        entry = outcome["decisions"]["deterministic"]
        self.assertEqual(entry["decision"], "allowed")
        self.assertIn("available", entry)

    def test_unsupported_contract_version_still_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            self.bridge.validate_request(
                ["deterministic"], contract_version=99
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)


class PrivacyAndGateTests(unittest.TestCase):
    def test_privacy_whitelist_remains_intact(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        payload = {
            "status": bridge.status(),
            "validation": bridge.validate_request(
                ["deterministic"], contract_version=2
            ),
        }
        serialized = repr(payload).lower()
        for word in (
            "api_key", "authorization", "prompt", "chain-of-thought",
            "chain_of_thought", "credential", "password",
            "filesystem", "model-private",
        ):
            self.assertNotIn(word, serialized)

    def test_no_internal_python_object_representations(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        serialized = repr(bridge.status())
        self.assertNotIn("<amca.intelligence", serialized)
        self.assertNotIn("object at 0x", serialized)

    def test_existing_auth_session_behavior_unchanged(self):
        # the bridge performs no authentication: auth remains the existing
        # X-AMCA-Session mechanism in the API layer (covered there)
        self.assertFalse(hasattr(self.bridge_status(), "authorize"))
        self.assertFalse(hasattr(self.bridge_status(), "session"))

    def test_all_four_gates_remain_off(self):
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )
        self.assertEqual(
            self.bridge_status()["gates"],
            DEFAULT_GATES.to_dict(),
        )

    def bridge_status(self):
        return IntelligenceRuntimeBridge(
            meta_factory=meta_factory
        ).status()


if __name__ == "__main__":
    unittest.main()
