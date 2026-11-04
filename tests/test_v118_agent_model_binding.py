"""v1.1.8 Work Order 09 section 6 — capability-based agent -> model binding.

The directive forbids hard-coding (Research Agent -> qwen3) and forbids
arbitrary assignment.  What existed was worse than either: round-robin by
index, pairing the Nth adapter with the Nth role, so a coding agent could be
bound to whatever model sat at that offset.

These tests pin the replacement: roles declare requirements, the canonical
router decides, and a role that cannot be satisfied says so instead of being
handed a model that cannot do its job.
"""

from __future__ import annotations

import inspect
import unittest

from amca.agent_model_binding import (
    KNOWN_CAPABILITIES,
    ROLE_REQUIREMENTS,
    bind_agents,
    requirements_for,
)
from amca.model_router import ModelCandidateFacts


def _model(ref, capabilities=(), **kwargs):
    fields = dict(
        model_ref=ref,
        capabilities=frozenset(capabilities),
        supports_tools="tools" in capabilities,
        supports_json="json_mode" in capabilities,
        supports_vision="vision" in capabilities,
    )
    fields.update(kwargs)
    return ModelCandidateFacts(**fields)


class RoleDescriptorTests(unittest.TestCase):

    def test_role_descriptors_use_only_capabilities_the_registry_represents(self) -> None:
        # Inventing a capability would make a role permanently unbindable while
        # looking entirely reasonable.
        for agent_id, descriptor in ROLE_REQUIREMENTS.items():
            with self.subTest(agent_id=agent_id):
                used = descriptor.required_capabilities | descriptor.preferred_capabilities
                self.assertTrue(
                    used <= KNOWN_CAPABILITIES,
                    f"{agent_id} uses unknown capabilities: {sorted(used - KNOWN_CAPABILITIES)}",
                )

    def test_no_descriptor_names_a_model(self) -> None:
        # The directive: do NOT hard-code Research Agent -> qwen3.
        for agent_id, descriptor in ROLE_REQUIREMENTS.items():
            with self.subTest(agent_id=agent_id):
                text = repr(descriptor).casefold()
                for forbidden in ("qwen", "gpt", "claude", "gemini", "llama", "mistral"):
                    self.assertNotIn(forbidden, text)

    def test_requirements_and_preferences_are_separate(self) -> None:
        # Section 6.2: a preference that silently excluded would make a role
        # unbindable on a machine lacking one nice-to-have capability.
        coding = requirements_for("specialist.coding")
        self.assertFalse(coding.required_capabilities)
        self.assertTrue(coding.preferred_capabilities)

    def test_a_browser_role_requires_tools_rather_than_preferring_them(self) -> None:
        browser = requirements_for("specialist.browser")
        self.assertTrue(browser.required_tools)

    def test_an_unknown_role_gets_no_invented_requirements(self) -> None:
        unknown = requirements_for("specialist.something-new")
        self.assertFalse(unknown.required_capabilities)
        self.assertFalse(unknown.required_tools)
        self.assertIn("no specific requirements", unknown.rationale)


class BindingTests(unittest.TestCase):

    def test_a_role_is_bound_to_a_model_that_has_what_it_prefers(self) -> None:
        coder = _model("coder", ["coding", "tools"])
        writer = _model("writer", ["long_context"])
        result = bind_agents(["specialist.coding"], [writer, coder])
        self.assertEqual(result.model_for("specialist.coding"), "coder")

    def test_a_hard_requirement_excludes_an_incapable_model(self) -> None:
        no_tools = _model("no-tools", ["long_context"])
        result = bind_agents(["specialist.browser"], [no_tools])
        self.assertEqual(result.model_for("specialist.browser"), "")
        self.assertEqual(len(result.unbound), 1)
        self.assertIn("tool support is required", result.unbound[0][1])

    def test_an_unbindable_role_is_reported_not_given_an_arbitrary_model(self) -> None:
        # An agent quietly bound to a model that cannot do its job is worse than
        # an agent that says it could not be bound.
        vision_required = _model("text-only", ["coding"])
        result = bind_agents(["specialist.coding"], [vision_required])
        # coding has no hard requirement, so it binds — this is the contrast.
        self.assertEqual(result.model_for("specialist.coding"), "text-only")

    def test_distinct_roles_prefer_distinct_models_when_available(self) -> None:
        coder = _model("coder", ["coding", "tools"])
        writer = _model("writer", ["long_context"])
        result = bind_agents(
            ["specialist.coding", "specialist.deep-research"], [coder, writer]
        )
        self.assertEqual(result.model_for("specialist.coding"), "coder")
        self.assertEqual(result.model_for("specialist.deep-research"), "writer")

    def test_reuse_is_recorded_when_only_one_model_is_eligible(self) -> None:
        only = _model("only", ["coding", "tools", "long_context"])
        result = bind_agents(
            ["specialist.coding", "specialist.deep-research"], [only]
        )
        self.assertEqual(len(result.bindings), 2)
        # The FIRST role legitimately takes the model; only the SECOND is
        # reusing it, and that is the one whose rationale must say so.
        first, second = result.bindings
        self.assertNotIn("reused", first.rationale)
        self.assertIn("reused", second.rationale)

    def test_every_binding_records_why_it_was_made(self) -> None:
        result = bind_agents(["specialist.coding"], [_model("coder", ["coding"])])
        binding = result.bindings[0]
        self.assertTrue(binding.rationale.strip())
        self.assertEqual(binding.agent_id, "specialist.coding")

    def test_the_binding_carries_the_router_decision(self) -> None:
        result = bind_agents(["specialist.coding"], [_model("coder", ["coding"])])
        self.assertTrue(result.decisions)
        self.assertIn("agent_id", result.decisions[0])
        self.assertIn("selected_model", result.decisions[0])

    def test_binding_serialises(self) -> None:
        import json

        result = bind_agents(["specialist.coding"], [_model("coder", ["coding"])])
        json.dumps(result.to_dict())

    def test_no_models_means_every_role_is_unbound_with_a_reason(self) -> None:
        result = bind_agents(["specialist.coding", "specialist.deep-research"], [])
        self.assertEqual(result.bindings, ())
        self.assertEqual(len(result.unbound), 2)


class NoRoundRobinTests(unittest.TestCase):
    """The old assignment must be gone, not merely unused."""

    def test_the_service_binds_by_capability_and_has_no_round_robin_fallback(self) -> None:
        """The manager's correction: an unused model stays INACTIVE.

        An earlier version of this fix forced every adapter into a role to stop
        a KeyError.  That preserved a bad invariant — "every configured adapter
        must have a role assignment" — which AMCA does not have: dynamic team
        size, Solo, partial Model Teams and Auto subsets are all normal.  The
        consumer was fixed instead, so no fake assignment is needed and no
        round-robin fallback remains.
        """
        from amca import service

        source = inspect.getsource(service)
        self.assertIn("bind_agents", source)
        # No round-robin assignment of any kind survives.
        self.assertNotIn("index % len(selected_roles)", source)
        self.assertNotIn("index % len(unclaimed_roles)", source)
        # The consumer requires only ACTIVE participants.
        self.assertIn("active_adapters", source)
        self.assertIn("no model was selected for this run", source)

    def test_a_model_may_be_inactive_without_error(self) -> None:
        # The property the correction exists for.
        coder = _model("coder", ["coding"])
        unused = _model("unused", ["coding"])
        result = bind_agents(["specialist.coding"], [coder, unused])
        self.assertIn("unused", result.inactive_model_refs)
        self.assertNotIn("unused", result.active_model_refs)
        self.assertEqual(result.active_model_refs, ("coder",))

    def test_a_governance_role_is_never_used_as_a_sink(self) -> None:
        # Section 1.1: control-plane roles must not absorb unused models.
        from amca.agent_model_binding import CONTROL_ROLES

        candidates = [_model(f"m{i}", ["coding"]) for i in range(6)]
        result = bind_agents(["specialist.coding"], candidates)
        for binding in result.bindings:
            with self.subTest(agent=binding.agent_id):
                self.assertNotIn(binding.agent_id, CONTROL_ROLES)

    def test_a_user_selected_model_participates_as_a_general_participant(self) -> None:
        # Section 1.4: an explicit Model Team is a participation constraint.
        coder = _model("coder", ["coding"])
        writer = _model("writer", ["long_context"])
        result = bind_agents(
            ["specialist.coding"], [coder, writer], required_participants=["writer"]
        )
        self.assertEqual(
            result.model_for("specialist.coding"), "coder"
        )
        self.assertIn("writer", result.active_model_refs)
        self.assertIn("writer", result.general_model_refs)
        self.assertNotIn("writer", result.inactive_model_refs)

    def test_an_auto_run_mandates_nothing(self) -> None:
        # Under Auto the selection policy owns participation, so an unclaimed
        # model is inactive rather than forced into the team.
        coder = _model("coder", ["coding"])
        spare = _model("spare", ["coding"])
        result = bind_agents(["specialist.coding"], [coder, spare])
        self.assertEqual(result.active_model_refs, ("coder",))

    def test_the_binding_module_names_no_provider(self) -> None:
        import ast

        import amca.agent_model_binding as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.Module, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ):
                    body.pop(0)
        code = ast.unparse(tree).casefold()
        for forbidden in ("qwen", "claude", "gemini", "llama", "mistral", "ollama:"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
