"""v1.1.8 Task F — the canonical Model Router.

The manager's decision was full consolidation, not a facade, because a facade
would leave three contradictory decision engines underneath.  These tests pin
the properties that make the router canonical, and the two rules the manager
was explicit about:

* name-based quality and reliability must NOT influence routing;
* cost policy must be explicit, never one hidden default here and another there.
"""

from __future__ import annotations

import inspect
import unittest

from amca.model_router import (
    DEFAULT_COST_POLICY,
    MetricUnit,
    PerformanceEvidence,
    latency_ms_of,
    load_failed,
    throughput_of,
    CostPolicy,
    ModelCandidateFacts,
    ModelSelectionRequest,
    TieBreak,
    select_model,
)
from amca.models import ModelRateLimitError
from amca.runtime_health import RunScopedHealth

LOCAL = ModelCandidateFacts(
    model_ref="ollama:qwen2.5-coder:7b", provider="ollama",
    capabilities=frozenset({"coding"}), supports_tools=True, supports_json=True,
    context_window=32000, is_local=True, expected_cost_usd=0.0,
    measured_latency_ms=120.0,
)
CLOUD = ModelCandidateFacts(
    model_ref="openai:gpt-4o", provider="openai",
    capabilities=frozenset({"coding"}), supports_tools=True, supports_json=True,
    context_window=128000, is_local=False, expected_cost_usd=None,
)


def _request(**overrides):
    fields = dict(
        task_type="coding", required_capabilities=frozenset({"coding"}),
        required_tools=True, cost_policy=CostPolicy.BALANCED,
    )
    fields.update(overrides)
    return ModelSelectionRequest(**fields)


class HardFiltersRunFirstTests(unittest.TestCase):
    """An incapable healthy model must not beat a capable degraded one."""

    def test_a_model_missing_a_required_capability_is_excluded(self) -> None:
        incapable = ModelCandidateFacts(model_ref="tiny", supports_tools=False, is_local=True)
        decision = select_model([incapable, CLOUD], _request())
        self.assertEqual(decision.selected_model, CLOUD.model_ref)
        self.assertEqual(
            [item.model_ref for item in decision.excluded_models], ["tiny"]
        )
        self.assertIn("missing required capability", decision.excluded_models[0].reason)

    def test_a_healthy_but_incapable_model_does_not_beat_a_capable_one(self) -> None:
        health = RunScopedHealth()
        incapable = ModelCandidateFacts(model_ref="tiny", supports_tools=False, is_local=True)
        health.record_success("tiny")          # healthy
        health.record_failure(CLOUD.model_ref, ModelRateLimitError("429"))  # unwell
        decision = select_model(
            [incapable, CLOUD], _request(runtime_health=health)
        )
        # The cloud model is rate-limited, so it is out for this run; but the
        # point is that the healthy incapable model never won on health alone.
        self.assertNotEqual(decision.selected_model, "tiny")

    def test_a_hidden_model_is_excluded_even_if_it_is_the_best_ranked(self) -> None:
        hidden = ModelCandidateFacts(
            model_ref="hidden", capabilities=frozenset({"coding"}), supports_tools=True,
            is_local=True, expected_cost_usd=0.0, visible=False,
        )
        decision = select_model([hidden, CLOUD], _request())
        self.assertEqual(decision.selected_model, CLOUD.model_ref)
        self.assertIn("hidden", decision.excluded_models[0].reason)

    def test_an_unestablished_context_window_is_admitted(self) -> None:
        # 0 means nobody recorded a window; refusing would disable routing for
        # every model the registry has not seen.
        unknown_window = ModelCandidateFacts(
            model_ref="unmeasured", capabilities=frozenset({"coding"}), supports_tools=True,
            context_window=0,
        )
        decision = select_model([unknown_window], _request(required_context=100_000))
        self.assertEqual(decision.selected_model, "unmeasured")

    def test_a_recorded_too_small_window_is_refused(self) -> None:
        small = ModelCandidateFacts(
            model_ref="small", capabilities=frozenset({"coding"}), supports_tools=True,
            context_window=4096,
        )
        decision = select_model([small], _request(required_context=100_000))
        self.assertEqual(decision.selected_model, "")
        self.assertIn("below the required", decision.excluded_models[0].reason)

    def test_locality_constraints_are_hard(self) -> None:
        local_only = select_model([LOCAL, CLOUD], _request(requires_local=True))
        self.assertEqual(local_only.selected_model, LOCAL.model_ref)
        remote_only = select_model([LOCAL, CLOUD], _request(requires_remote=True))
        self.assertEqual(remote_only.selected_model, CLOUD.model_ref)


class NoNameHeuristicTests(unittest.TestCase):
    """Rule 1: names are not measurements."""

    def test_the_router_has_no_quality_or_reliability_input_at_all(self) -> None:
        # The heuristics scored "opus"/"pro"/"o3" at 0.96 and "mini"/"flash"/
        # "haiku" at 0.79.  A candidate must have no field for them.
        fields = set(ModelCandidateFacts.__dataclass_fields__)
        for forbidden in ("quality_score", "reliability_score", "quality", "reliability"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, fields)

    def test_the_module_contains_no_name_substring_scoring(self) -> None:
        # Check executable CODE, not prose: the docstring legitimately names the
        # removed heuristics in order to say they were removed.
        import ast

        import amca.model_router as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body.pop(0)          # drop the docstring
        code = ast.unparse(tree)
        # Substrings that would only appear if a name were being scored.  Kept
        # specific so an unrelated identifier containing "mini" (such as
        # minimum_participants) is not a false positive.
        for forbidden in ('"opus"', '"flash"', '"haiku"', '"mini"', '"pro"',
                          "log10", "quality_score", "reliability_score",
                          "startswith(\"gpt", "in model_ref"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)

    def test_two_models_differing_only_in_name_rank_identically(self) -> None:
        a = ModelCandidateFacts(model_ref="provider:opus-ultra", capabilities=frozenset({"x"}))
        b = ModelCandidateFacts(model_ref="provider:mini-lite", capabilities=frozenset({"x"}))
        decision = select_model([a, b], _request(required_capabilities=frozenset({"x"}),
                                                 required_tools=False))
        self.assertEqual(
            decision.tie_break_reason,
            TieBreak.INSUFFICIENT_MEASURED_DIFFERENTIATION.value,
            "names must not create measured differentiation",
        )

    def test_the_tie_break_never_claims_quality(self) -> None:
        a = ModelCandidateFacts(model_ref="a", capabilities=frozenset({"x"}))
        b = ModelCandidateFacts(model_ref="b", capabilities=frozenset({"x"}))
        decision = select_model([a, b], _request(required_capabilities=frozenset({"x"}),
                                                 required_tools=False))
        text = decision.explain()
        self.assertIn("not a quality judgement", text)
        for forbidden in ("higher quality", "better quality", "best model"):
            with self.subTest(phrase=forbidden):
                self.assertNotIn(forbidden, text)


class ExplicitCostPolicyTests(unittest.TestCase):
    """Rule 2: no hidden boolean default, and unknown cost is not free."""

    def test_there_is_exactly_one_documented_global_default(self) -> None:
        self.assertIs(DEFAULT_COST_POLICY, CostPolicy.BALANCED)

    def test_unknown_cost_is_not_treated_as_free(self) -> None:
        # CLOUD has expected_cost_usd None.  Under an economy policy an unpriced
        # model must not beat a priced one by pretending to cost nothing.
        decision = select_model([LOCAL, CLOUD], _request(cost_policy=CostPolicy.ECONOMY))
        self.assertEqual(decision.selected_model, LOCAL.model_ref)
        self.assertIn("cost_unknown_for_some_candidates", decision.unknown_factors)

    def test_cost_insensitive_ignores_cost_entirely(self) -> None:
        decision = select_model(
            [LOCAL, CLOUD], _request(cost_policy=CostPolicy.COST_INSENSITIVE)
        )
        self.assertNotIn("known_cost", decision.soft_factors_used)

    def test_a_cost_limit_is_a_hard_filter(self) -> None:
        priced = ModelCandidateFacts(
            model_ref="pricey", capabilities=frozenset({"coding"}), supports_tools=True,
            expected_cost_usd=5.0,
        )
        decision = select_model([priced, LOCAL], _request(max_cost_usd=0.01))
        self.assertEqual(decision.selected_model, LOCAL.model_ref)
        self.assertIn("cost exceeds", decision.excluded_models[0].reason)

    def test_a_hard_limit_excludes_an_unknown_cost(self) -> None:
        """UPDATED for the manager's section 2.2.

        This previously asserted that an unknown cost was not refused by a
        limit.  The manager drew the distinction explicitly: a HARD maximum
        cannot be checked against an unknown, so the candidate is ineligible,
        while a SOFT economy preference keeps unknown as unknown and merely
        ranks it last.  A limit is a promise about what may be spent, and an
        unpriced model cannot be promised against.
        """
        decision = select_model([CLOUD], _request(max_cost_usd=0.01))
        self.assertEqual(decision.selected_model, "")
        self.assertIn("cost is unknown", decision.excluded_models[0].reason)


class DecisionAuditabilityTests(unittest.TestCase):

    def test_the_decision_records_why_every_exclusion_happened(self) -> None:
        hidden = ModelCandidateFacts(model_ref="h", visible=False)
        incapable = ModelCandidateFacts(model_ref="i")
        decision = select_model([hidden, incapable, LOCAL], _request())
        reasons = {item.model_ref: item.reason for item in decision.excluded_models}
        self.assertIn("h", reasons)
        self.assertIn("i", reasons)

    def test_the_decision_records_unknown_factors_rather_than_hiding_them(self) -> None:
        decision = select_model([LOCAL, CLOUD], _request())
        self.assertTrue(decision.unknown_factors)
        self.assertEqual(decision.cost_evidence, "known")

    def test_the_decision_reports_which_soft_factors_were_used(self) -> None:
        decision = select_model([LOCAL, CLOUD], _request())
        self.assertIn("measured_latency", decision.soft_factors_used)
        self.assertEqual(decision.performance_evidence, "measured")

    def test_the_decision_serialises(self) -> None:
        import json

        json.dumps(select_model([LOCAL, CLOUD], _request()).to_dict())

    def test_no_eligible_candidate_is_reported_not_guessed(self) -> None:
        decision = select_model([], _request())
        self.assertEqual(decision.selected_model, "")
        self.assertEqual(decision.eligible_models, ())

    def test_the_request_summary_records_the_policy_that_was_applied(self) -> None:
        decision = select_model([LOCAL], _request(cost_policy=CostPolicy.ECONOMY))
        self.assertEqual(decision.cost_policy, "economy")
        self.assertEqual(decision.request_summary["cost_policy"], "economy")


class RuntimeHealthIntegrationTests(unittest.TestCase):

    def test_a_rate_limited_candidate_is_skipped_for_this_run(self) -> None:
        health = RunScopedHealth()
        health.record_failure(LOCAL.model_ref, ModelRateLimitError("429"))
        decision = select_model(
            [LOCAL, CLOUD], _request(cost_policy=CostPolicy.COST_INSENSITIVE,
                                     runtime_health=health)
        )
        self.assertEqual(decision.selected_model, CLOUD.model_ref)
        self.assertIn("runtime health", decision.excluded_models[0].reason)

    def test_health_cannot_admit_a_model_that_fails_a_hard_filter(self) -> None:
        health = RunScopedHealth()
        health.record_success("incapable")
        incapable = ModelCandidateFacts(model_ref="incapable")
        decision = select_model([incapable], _request(runtime_health=health))
        self.assertEqual(decision.selected_model, "")

    def test_health_does_not_change_capability(self) -> None:
        # The separation section 1.8 requires, asserted at the router boundary.
        health = RunScopedHealth()
        health.record_failure(LOCAL.model_ref, ModelRateLimitError("429"))
        decision = select_model([LOCAL], _request(runtime_health=health))
        self.assertTrue(LOCAL.supports_tools, "health must not alter a capability fact")
        self.assertEqual(decision.selected_model, "", "but it may exclude for this run")


class PerformanceEvidenceTests(unittest.TestCase):
    """Task F completion: measured performance is real routing evidence.

    The manager's rules: evidence is generic (no local-runtime special case),
    carries its scope, and an unmeasured metric stays UNKNOWN rather than
    becoming 0 or a synthetic score.
    """

    def _evidence(self, model, metric, value, unit=MetricUnit.TOKENS_PER_SECOND):
        return PerformanceEvidence(model_id=model, metric=metric, value=value, unit=unit,
                                   environment_scope="test-machine")

    def test_throughput_is_read_from_generic_evidence(self) -> None:
        self.assertEqual(throughput_of([self._evidence("m", "generation_throughput", 18.4)]), 18.4)

    def test_an_unmeasured_metric_is_none_not_zero(self) -> None:
        # Section 5: a model without evidence must not be labelled slow.
        self.assertIsNone(throughput_of([]))
        self.assertIsNone(throughput_of([self._evidence("m", "generation_throughput", None)]))

    def test_latency_in_seconds_is_converted_within_its_own_metric(self) -> None:
        ev = [self._evidence("m", "request_latency", 1.8, MetricUnit.SECONDS)]
        self.assertEqual(latency_ms_of(ev), 1800.0)

    def test_throughput_is_not_converted_into_latency(self) -> None:
        # Section 3: unlike metrics must not be merged into one score.
        ev = [self._evidence("m", "generation_throughput", 18.4)]
        self.assertIsNone(latency_ms_of(ev))

    def test_an_established_load_failure_is_detected(self) -> None:
        ev = [self._evidence("m", "model_load_success", False, MetricUnit.BOOLEAN)]
        self.assertTrue(load_failed(ev))

    def test_evidence_carries_its_environment_scope(self) -> None:
        payload = self._evidence("m", "generation_throughput", 18.4).to_dict()
        self.assertEqual(payload["environment_scope"], "test-machine")
        self.assertEqual(payload["source"], "measured")


class HardwareFitTests(unittest.TestCase):
    """Section 2: inability to run is a hard fact; being slower is not."""

    def test_an_established_inability_to_run_is_a_hard_exclusion(self) -> None:
        cannot_run = ModelCandidateFacts(
            model_ref="cannot-run", capabilities=frozenset({"c"}), hardware_fit=False,
        )
        capable = ModelCandidateFacts(model_ref="capable", capabilities=frozenset({"c"}))
        decision = select_model([cannot_run, capable], _request(required_capabilities=frozenset({"c"}), required_tools=False))
        self.assertEqual(decision.selected_model, "capable")
        self.assertIn("cannot run", decision.excluded_models[0].reason)

    def test_measured_load_failure_is_a_hard_exclusion(self) -> None:
        failing = ModelCandidateFacts(
            model_ref="failing", capabilities=frozenset({"c"}),
            performance=(PerformanceEvidence(
                model_id="failing", metric="model_load_success", value=False,
                unit=MetricUnit.BOOLEAN),),
        )
        decision = select_model([failing], _request(required_capabilities=frozenset({"c"}), required_tools=False))
        self.assertEqual(decision.selected_model, "")

    def test_an_unestablished_fit_is_not_an_exclusion(self) -> None:
        unknown = ModelCandidateFacts(model_ref="unknown-fit", capabilities=frozenset({"c"}))
        decision = select_model([unknown], _request(required_capabilities=frozenset({"c"}), required_tools=False))
        self.assertEqual(decision.selected_model, "unknown-fit")
        self.assertIn("hardware_fit_unestablished", decision.unknown_factors)

    def test_being_slower_is_not_an_exclusion(self) -> None:
        slow = ModelCandidateFacts(
            model_ref="slow", capabilities=frozenset({"c"}),
            performance=(PerformanceEvidence(model_id="slow", metric="generation_throughput",
                                             value=5.0, unit=MetricUnit.TOKENS_PER_SECOND),),
        )
        decision = select_model([slow], _request(required_capabilities=frozenset({"c"}), required_tools=False))
        self.assertEqual(decision.selected_model, "slow")


class StagedRankingTests(unittest.TestCase):
    """Section 7: staged lexicographic reasoning, not one opaque score."""

    def _candidate(self, ref, throughput=None, **kwargs):
        fields = dict(model_ref=ref, capabilities=frozenset({"c"}), is_local=True)
        fields.update(kwargs)
        if throughput is not None:
            fields["performance"] = (PerformanceEvidence(
                model_id=ref, metric="generation_throughput", value=throughput,
                unit=MetricUnit.TOKENS_PER_SECOND),)
        return ModelCandidateFacts(**fields)

    def test_higher_measured_throughput_ranks_first(self) -> None:
        decision = select_model(
            [self._candidate("slow", 5.0), self._candidate("fast", 18.4)],
            _request(required_capabilities=frozenset({"c"}), required_tools=False),
        )
        self.assertEqual(decision.selected_model, "fast")
        self.assertIn("measured_throughput", decision.soft_factors_used)

    def test_an_unmeasured_model_sorts_last_not_slow(self) -> None:
        decision = select_model(
            [self._candidate("aaa-unmeasured"), self._candidate("slow", 5.0)],
            _request(required_capabilities=frozenset({"c"}), required_tools=False),
        )
        # Alphabetically "aaa-unmeasured" would win a naive tie-break; measured
        # evidence puts the measured model first.
        self.assertEqual(decision.selected_model, "slow")
        self.assertIn("throughput_unknown_for_some_candidates", decision.unknown_factors)

    def test_policy_outranks_measurement(self) -> None:
        # Stage 2 before stage 3: an explicit local preference is honoured even
        # when the remote model measures faster.
        local = self._candidate("local", 5.0, is_local=True)
        remote = self._candidate("remote", 50.0, is_local=False)
        decision = select_model(
            [remote, local],
            _request(required_capabilities=frozenset({"c"}), required_tools=False,
                     prefer_local=True),
        )
        self.assertEqual(decision.selected_model, "local")

    def test_runtime_health_outranks_measured_performance(self) -> None:
        # Stage 1 before stage 3: a fast model that is failing right now loses.
        from amca.models import ModelRateLimitError

        health = RunScopedHealth()
        health.record_failure("fast", ModelRateLimitError("429"))
        decision = select_model(
            [self._candidate("fast", 50.0), self._candidate("slow", 5.0)],
            _request(required_capabilities=frozenset({"c"}), required_tools=False,
                     runtime_health=health),
        )
        self.assertEqual(decision.selected_model, "slow")

    def test_the_decision_says_which_factors_mattered(self) -> None:
        decision = select_model(
            [self._candidate("slow", 5.0), self._candidate("fast", 18.4)],
            _request(required_capabilities=frozenset({"c"}), required_tools=False),
        )
        self.assertTrue(decision.soft_factors_used)
        self.assertIn("measured_throughput", decision.explain())


if __name__ == "__main__":
    unittest.main()
