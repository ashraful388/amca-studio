"""v1.1.8 Work Order 09 — run-scoped runtime health (Task D).

Sections 1.8 and 6.4.  The property that matters most is a SEPARATION:

    Capability  = what a model can do      (Model Registry, with provenance)
    Health      = what is happening now    (this run, and nothing else)

A timeout is evidence about health.  It must never change ``supports_tools``.
Health may rank a candidate or exclude it from THIS run; it can never rewrite a
capability fact, and it never outlives the run that observed it.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from amca.models import (
    ModelRateLimitError,
    ModelTimeoutError,
    ModelProviderUnavailableError,
)
from amca.runtime_health import (
    MAX_OUTCOMES_PER_MODEL,
    RunScopedHealth,
    RuntimeHealth,
    classify_outcome,
)


class HealthClassificationTests(unittest.TestCase):

    def test_a_timeout_reads_as_timeout_prone(self) -> None:
        self.assertIs(classify_outcome(ModelTimeoutError("timed out")),
                      RuntimeHealth.TIMEOUT_PRONE)

    def test_a_rate_limit_reads_as_rate_limited(self) -> None:
        self.assertIs(classify_outcome(ModelRateLimitError("429")),
                      RuntimeHealth.RATE_LIMITED)

    def test_a_provider_outage_reads_as_temporarily_unavailable(self) -> None:
        self.assertIs(classify_outcome(ModelProviderUnavailableError("down")),
                      RuntimeHealth.TEMPORARILY_UNAVAILABLE)

    def test_a_permission_denial_is_not_a_health_signal(self) -> None:
        # A control refusal says nothing about whether the model is working.
        # Recording it as unhealthy would let a policy decision masquerade as a
        # provider fault.
        for failure in (PermissionError("denied"), ValueError("invalid task")):
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(classify_outcome(failure), RuntimeHealth.UNKNOWN)

    def test_a_cancellation_is_not_a_health_signal(self) -> None:
        from amca.models import ModelCancelledError

        self.assertIs(classify_outcome(ModelCancelledError("cancelled")),
                      RuntimeHealth.UNKNOWN)


class RunScopedHealthTests(unittest.TestCase):

    def test_an_unobserved_model_is_unknown_not_healthy(self) -> None:
        # Absence of evidence is not evidence of health; claiming otherwise
        # would let an untested model outrank a proven one.
        self.assertIs(RunScopedHealth().health_of("never-seen"), RuntimeHealth.UNKNOWN)

    def test_a_success_after_a_failure_restores_health(self) -> None:
        health = RunScopedHealth()
        health.record_failure("m1", ModelTimeoutError("timed out"))
        self.assertIs(health.health_of("m1"), RuntimeHealth.TIMEOUT_PRONE)
        health.record_success("m1")
        self.assertIs(health.health_of("m1"), RuntimeHealth.HEALTHY)

    def test_healthy_outranks_unknown_which_outranks_rate_limited(self) -> None:
        health = RunScopedHealth()
        health.record_success("good")
        health.record_failure("limited", ModelRateLimitError("429"))
        self.assertLess(health.ranking_key("good"), health.ranking_key("unseen"))
        self.assertLess(health.ranking_key("unseen"), health.ranking_key("limited"))

    def test_ordering_prefers_the_healthier_candidate(self) -> None:
        health = RunScopedHealth()
        health.record_success("good")
        health.record_failure("limited", ModelRateLimitError("429"))
        ordered = health.order_candidates(
            [{"model_ref": "limited"}, {"model_ref": "good"}, {"model_ref": "unseen"}]
        )
        self.assertEqual([item["model_ref"] for item in ordered], ["good", "unseen", "limited"])

    def test_only_momentary_faults_exclude_a_model(self) -> None:
        health = RunScopedHealth()
        health.record_failure("limited", ModelRateLimitError("429"))
        health.record_failure("outage", ModelProviderUnavailableError("down"))
        health.record_failure("slow", ModelTimeoutError("timed out"))
        health.record_success("fine")
        self.assertTrue(health.temporarily_excluded("limited")[0])
        self.assertTrue(health.temporarily_excluded("outage")[0])
        # A timeout-prone model is ranked lower, not excluded: it may still be
        # the only capable candidate.
        self.assertFalse(health.temporarily_excluded("slow")[0])
        self.assertFalse(health.temporarily_excluded("fine")[0])

    def test_history_is_bounded_per_model(self) -> None:
        health = RunScopedHealth()
        for _ in range(MAX_OUTCOMES_PER_MODEL * 3):
            health.record_failure("m1", ModelTimeoutError("timed out"))
        self.assertLessEqual(health.snapshot()["m1"]["observations"], MAX_OUTCOMES_PER_MODEL)

    def test_an_empty_model_ref_is_ignored_rather_than_recorded(self) -> None:
        health = RunScopedHealth()
        health.record_success("")
        health.record_failure("", ModelTimeoutError("timed out"))
        self.assertEqual(health.snapshot(), {})

    def test_the_snapshot_is_serialisable(self) -> None:
        import json

        health = RunScopedHealth()
        health.record_success("m1")
        json.dumps(health.snapshot())


class CapabilitySeparationTests(unittest.TestCase):
    """The property the manager singled out in section 1.8."""

    def test_the_health_module_cannot_reach_the_model_registry(self) -> None:
        # Health must not be able to mutate a capability fact even by mistake.
        import amca.runtime_health as module

        source = inspect.getsource(module)
        for forbidden in ("model_registry", "ModelRegistryStore", "upsert", "capabilities="):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, source)

    def test_recording_a_timeout_does_not_change_a_registry_capability(self) -> None:
        from amca.model_registry import ModelRegistryStore, build_record

        tmp = Path(tempfile.mkdtemp())
        store = ModelRegistryStore(tmp / "r.sqlite")
        store.ensure_schema()
        store.upsert(build_record(
            model_ref="ollama:qwen3:1.7b", provider_kind="ollama", model_id="qwen3:1.7b",
            provider_id="ollama", capabilities={"tools": True}, capability_source="declared",
        ))
        before = store.get("ollama:qwen3:1.7b")
        self.assertTrue(before.capabilities.tools)

        health = RunScopedHealth()
        health.record_failure("ollama:qwen3:1.7b", ModelTimeoutError("timed out"))
        health.record_failure("ollama:qwen3:1.7b", ModelTimeoutError("timed out"))

        after = store.get("ollama:qwen3:1.7b")
        self.assertTrue(
            after.capabilities.tools,
            "a runtime timeout must never turn supports_tools off",
        )

    def test_health_is_not_persisted_anywhere(self) -> None:
        # It is run-scoped by construction: a fresh tracker knows nothing.
        first = RunScopedHealth()
        first.record_failure("m1", ModelTimeoutError("timed out"))
        self.assertIs(RunScopedHealth().health_of("m1"), RuntimeHealth.UNKNOWN)


class FallbackUsesHealthTests(unittest.TestCase):
    """Health influences selection without changing eligibility rules."""

    CANDIDATES = [
        {"model_ref": "limited", "supports_tools": True, "context_window": 128000,
         "is_local": False, "visible": True, "available": True, "expected_cost_usd": 0.0},
        {"model_ref": "good", "supports_tools": True, "context_window": 128000,
         "is_local": False, "visible": True, "available": True, "expected_cost_usd": 0.0},
    ]

    def test_a_rate_limited_candidate_is_skipped_for_this_run(self) -> None:
        from amca.governed_recovery import select_fallback

        health = RunScopedHealth()
        health.record_failure("limited", ModelRateLimitError("429"))
        chosen, _reason, _considered = select_fallback(
            self.CANDIDATES, failed_model="x", requirements={"runtime_health": health},
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["model_ref"], "good")

    def test_without_health_the_first_eligible_candidate_is_chosen(self) -> None:
        # The health signal changes the ORDER, not the eligibility rules.
        from amca.governed_recovery import select_fallback

        chosen, _reason, _considered = select_fallback(self.CANDIDATES, failed_model="x")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["model_ref"], "limited")

    def test_health_cannot_admit_a_model_that_fails_a_hard_requirement(self) -> None:
        from amca.governed_recovery import select_fallback

        health = RunScopedHealth()
        health.record_success("limited")
        chosen, _reason, _considered = select_fallback(
            self.CANDIDATES, failed_model="x",
            requirements={"supports_tools": True, "required_context": 999_999,
                          "runtime_health": health},
        )
        self.assertIsNone(chosen, "good health must not override a hard requirement")


if __name__ == "__main__":
    unittest.main()
