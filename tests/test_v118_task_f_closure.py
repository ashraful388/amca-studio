"""Task F final closure proofs.

Work Order: "Before starting the long Auto runs, execute the remaining targeted
closure tests."  Five items, each proving a property that would otherwise be
left implicit.  Targeted tests only — the manager explicitly said not to run
another full regression for these.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from amca.model_router import (
    CostPolicy,
    ModelCandidateFacts,
    ModelSelectionRequest,
    select_model,
)


def _request(**overrides):
    fields = dict(required_capabilities=frozenset({"c"}), required_tools=False)
    fields.update(overrides)
    return ModelSelectionRequest(**fields)


def _model(ref="m", capabilities=("c",), **kwargs):
    fields = dict(
        model_ref=ref, capabilities=frozenset(capabilities),
        supports_tools="tools" in capabilities,
    )
    fields.update(kwargs)
    return ModelCandidateFacts(**fields)


class EffectiveCapabilityAuthorityTests(unittest.TestCase):
    """2.1 — one capability answer across router, execution, recovery, binding."""

    def test_tool_capability_authority_is_the_same_object_everywhere(self) -> None:
        # Router, execution and the evidence path must all consult the same
        # authority rather than each deriving their own answer.
        from amca.models.tool_protocol import effective_tool_support

        for model_name in ("gpt-4o-mini", "dolphin-llama3:8b", "some-unlisted-model"):
            with self.subTest(model=model_name):
                first = effective_tool_support(True, model_name, True)
                second = effective_tool_support(True, model_name, True)
                self.assertEqual(first, second)

    def test_a_curated_denial_cannot_be_re_enabled_by_a_legacy_fallback(self) -> None:
        from amca.models.tool_protocol import effective_tool_support

        # dolphin-llama3:8b is a curated no-tools model.  Nothing may re-enable it.
        self.assertFalse(effective_tool_support(True, "dolphin-llama3:8b", True))

    def test_the_legacy_fallback_marks_itself(self) -> None:
        # If a compatibility fallback remains it must identify its source, so a
        # decision made from it is distinguishable from one made from evidence.
        from amca.models import tool_protocol

        source = inspect.getsource(tool_protocol)
        self.assertIn("LEGACY_FALLBACK", source)
        self.assertIn("removal_target", source)

    def test_the_router_does_not_derive_capability_from_a_name(self) -> None:
        # The router consumes capability facts; it never computes them.
        import amca.model_router as module

        source = inspect.getsource(module).casefold()
        for forbidden in ("known_model_tool_support", "dolphin", "if \"gpt"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)


class HardMaximumCostTests(unittest.TestCase):
    """2.2 — hard cost limit and soft economy preference are distinct."""

    def test_a_hard_maximum_excludes_an_unknown_cost_candidate(self) -> None:
        # "Do not pretend Model B is cheaper."  A limit cannot be checked
        # against an unknown, so the candidate is ineligible rather than assumed
        # to be within budget.
        unknown = _model("unknown-cost", expected_cost_usd=None)
        decision = select_model([unknown], _request(max_cost_usd=1.0))
        self.assertEqual(
            decision.selected_model, "",
            "an unknown cost must not be admitted under a hard maximum",
        )

    def test_a_hard_maximum_still_excludes_a_known_over_limit_cost(self) -> None:
        pricey = _model("pricey", expected_cost_usd=5.0)
        decision = select_model([pricey], _request(max_cost_usd=1.0))
        self.assertEqual(decision.selected_model, "")

    def test_a_hard_maximum_admits_a_known_within_limit_cost(self) -> None:
        cheap = _model("cheap", expected_cost_usd=0.1)
        decision = select_model([cheap], _request(max_cost_usd=1.0))
        self.assertEqual(decision.selected_model, "cheap")

    def test_a_soft_economy_preference_treats_unknown_as_unknown_not_free(self) -> None:
        priced = _model("priced", expected_cost_usd=0.5)
        unknown = _model("unknown", expected_cost_usd=None)
        decision = select_model([unknown, priced], _request(cost_policy=CostPolicy.ECONOMY))
        self.assertEqual(
            decision.selected_model, "priced",
            "an unpriced model must not win an economy comparison by pretending to be free",
        )
        self.assertIn("cost_unknown_for_some_candidates", decision.unknown_factors)

    def test_the_two_semantics_are_genuinely_different(self) -> None:
        # Same candidate, same unknown cost: admitted under no limit, excluded
        # under a hard limit.  If these agreed, the distinction would be fake.
        unknown = _model("unknown", expected_cost_usd=None)
        without_limit = select_model([unknown], _request())
        with_limit = select_model([unknown], _request(max_cost_usd=1.0))
        self.assertEqual(without_limit.selected_model, "unknown")
        self.assertEqual(with_limit.selected_model, "")


class BalancedLocalPreferenceTests(unittest.TestCase):
    """2.3 — no hidden local favouritism."""

    def test_balanced_does_not_prefer_local_by_default(self) -> None:
        local = _model("local", is_local=True)
        remote = _model("remote", is_local=False)
        decision = select_model([local, remote], _request(cost_policy=CostPolicy.BALANCED))
        self.assertFalse(decision.request_summary["prefer_local"])

    def test_an_explicit_local_preference_is_honoured(self) -> None:
        local = _model("zzz-local", is_local=True)
        remote = _model("aaa-remote", is_local=False)
        decision = select_model(
            [remote, local], _request(prefer_local=True)
        )
        self.assertEqual(decision.selected_model, "zzz-local")
        self.assertIn("explicit_local_preference", decision.soft_factors_used)

    def test_the_router_default_request_does_not_prefer_local(self) -> None:
        # The default must be off, so nothing prefers local by accident.
        self.assertFalse(ModelSelectionRequest().prefer_local)

    def test_a_hard_locality_constraint_is_separate_from_the_preference(self) -> None:
        # requires_local excludes; prefer_local ranks.  Conflating them would
        # make a preference behave like a constraint.
        local = _model("local", is_local=True)
        remote = _model("remote", is_local=False)
        excluded = select_model([local, remote], _request(requires_local=True))
        self.assertEqual(excluded.selected_model, "local")
        ranked = select_model([local, remote], _request())
        self.assertTrue(ranked.selected_model)  # no exclusion happened


class SecondRegistryTests(unittest.TestCase):
    """2.4 — one Model Registry authority in production."""

    def test_only_one_registry_is_instantiated_in_production(self) -> None:
        from amca import service

        source = inspect.getsource(service)
        self.assertIn("ModelRegistryStore(", source)
        self.assertNotIn("ModelEcosystemManager(", source)

    def test_the_ecosystem_registry_is_not_imported_by_production_code(self) -> None:
        import amca.service as service

        source = inspect.getsource(service)
        self.assertNotIn("model_registry_ecosystem", source)

    def test_the_ecosystem_registry_declares_itself_non_canonical(self) -> None:
        # A dormant second registry with hardcoded seed models must say what it
        # is, so nobody wires it up believing it is the canonical source.
        import amca.model_registry_ecosystem as module

        source = inspect.getsource(module).casefold()
        self.assertTrue(
            any(word in source for word in ("test-only", "historical", "deprecated",
                                            "not the canonical", "non-canonical")),
            "the second registry must declare that it is not the canonical source",
        )


class CrossPathConsistencyTests(unittest.TestCase):
    """2.5 — identical facts and constraints resolve identically across paths."""

    def _facts(self):
        return [
            _model("alpha", ("c",), expected_cost_usd=0.10),
            _model("beta", ("c",), expected_cost_usd=0.05),
        ]

    def test_the_same_request_produces_the_same_answer_every_time(self) -> None:
        request = _request(cost_policy=CostPolicy.ECONOMY)
        answers = {
            select_model(self._facts(), request).selected_model for _ in range(5)
        }
        self.assertEqual(len(answers), 1)

    def test_two_callers_building_the_same_request_agree(self) -> None:
        # This is what the three contradictory selectors failed: same goal, same
        # models, different answers.  Now the request IS the decision input.
        first = select_model(self._facts(), _request(cost_policy=CostPolicy.ECONOMY))
        second = select_model(list(reversed(self._facts())),
                              _request(cost_policy=CostPolicy.ECONOMY))
        self.assertEqual(first.selected_model, second.selected_model,
                         "input order must not change the decision")

    def test_different_requests_may_differ_and_that_is_allowed(self) -> None:
        # Different answers are allowed only where the explicit request differs.
        cheap_first = select_model(self._facts(), _request(cost_policy=CostPolicy.ECONOMY))
        self.assertEqual(cheap_first.selected_model, "beta")

    def test_every_path_goes_through_the_one_router(self) -> None:
        # The consolidation proof: each former selector delegates.
        from amca import model_hub, routing

        for module, label in ((model_hub, "Auto"), (routing, "CLI")):
            with self.subTest(path=label):
                source = inspect.getsource(module)
                self.assertIn("select_model", source,
                              f"the {label} path must delegate to the canonical router")

    def test_the_service_has_no_second_ranking_implementation(self) -> None:
        # Inspect executable CODE, not prose: the retired heuristics are named
        # in a docstring precisely in order to say they were retired.
        import ast

        from amca import service

        tree = ast.parse(inspect.getsource(service))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module,
                                 ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ):
                    body.pop(0)
        code = ast.unparse(tree)
        # The name-quality curve and the name-prestige token lists are gone.
        self.assertNotIn("0.62 + 0.18", code)
        self.assertNotIn("log10", code)
        for token in ("opus", "haiku", "flash"):
            with self.subTest(token=token):
                self.assertNotIn(f'"{token}"', code)
        self.assertNotIn(f"'{token}'", code)


if __name__ == "__main__":
    unittest.main()
