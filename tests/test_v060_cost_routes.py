"""Tests for the v0.6.0 M2 cost/budget HTTP routes.

Exercises the three endpoints (GET /api/cost/state, GET /api/cost/budget,
POST /api/cost/budget) and verifies session / body validation.
"""

from __future__ import annotations

import json
import unittest
from http import HTTPStatus
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

from amca.cost_budget import (
    BudgetPolicy,
    PricingRegistry,
    ModelPrice,
    RunCostAccumulator,
)
from amca.cost_routes import (
    handle_cost_state,
    handle_cost_get_budget,
    handle_cost_set_budget,
    install_cost_routes,
    get_cost_state,
)


class _StubHandler:
    """Minimal stub of a BaseHTTPRequestHandler for unit-testing routes."""

    def __init__(self, *, authorized: bool = True, body: dict | None = None) -> None:
        self.context = MagicMock()
        # Pre-authorize or not.
        self._authorized_returns = authorized
        self._sent: list[tuple[HTTPStatus, Any]] = []
        self._errors: list[tuple[HTTPStatus, str]] = []
        # Body parsing: simulate _read_object returning a dict.
        self._body = body
        self.command = "POST" if body is not None else "GET"

    def _authorized(self) -> bool:
        return self._authorized_returns

    def _send_json(self, status: HTTPStatus, value: Any) -> None:
        self._sent.append((status, value))

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._errors.append((status, message))

    def _read_object(self) -> dict:
        if self._body is None:
            return {}
        return dict(self._body)


class TestGetState(unittest.TestCase):
    def test_state_with_no_run(self) -> None:
        handler = _StubHandler(authorized=True)
        handle_cost_state(handler)
        self.assertEqual(handler._errors, [])
        self.assertEqual(len(handler._sent), 1)
        status, payload = handler._sent[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["run_id"], None)
        self.assertEqual(payload["currency"], "USD")
        self.assertEqual(payload["total_calls"], 0)
        self.assertIn("run_budget_usd", payload)

    def test_state_with_run_and_policy(self) -> None:
        handler = _StubHandler(authorized=True)
        state = get_cost_state(handler.context)
        # Set a run accumulator and record a call.
        state.reset_run(run_id="r-42", project_id="p-1")
        state.policy = BudgetPolicy(run_budget_usd=1.0, strict_monetary=True)
        # Synthesise a recorded call via the public API path.
        from amca.cost_budget import CostCore

        core = CostCore(run_id=state.run_accumulator.run_id, project_id="p-1", policy=state.policy)
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.3,
            pricing_known=True,
        )
        state.run_accumulator = core.accumulator
        # Read state.
        handle_cost_state(handler)
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["run_id"], "r-42")
        self.assertEqual(payload["project_id"], "p-1")
        self.assertEqual(payload["total_known_cost_usd"], 0.3)
        self.assertEqual(payload["run_budget_usd"], 1.0)
        self.assertEqual(payload["strict_monetary"], True)
        self.assertAlmostEqual(payload["remaining_run_budget_usd"], 0.7, places=6)
        self.assertTrue(payload["known"])

    def test_state_does_not_leak_credentials(self) -> None:
        handler = _StubHandler(authorized=True)
        state = get_cost_state(handler.context)
        state.reset_run(run_id="r-1")
        from amca.cost_budget import CostCore

        core = CostCore(run_id="r-1", policy=state.policy)
        # Inject a fake call record that *should not* appear in the
        # state output, then check that sanitisation removed it.
        core.accumulator.calls["c1"] = type("FakeCall", (), {
            "call_id": "c1",
            "model_id": "gpt-4o",
            "provider": "openai",
            "local": False,
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.01,
            "cost_source": type("Src", (), {"value": "provider_reported"}),
            "pricing_known": True,
            "api_key": "sk-SECRET",
            "Authorization": "Bearer SECRET",
        })()
        state.run_accumulator = core.accumulator
        handle_cost_state(handler)
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        # CostReadState.to_dict() only ever emits run-level aggregate
        # fields, so the per-call record is not in the payload at all.
        # What we want to assert is that no forbidden field appears.
        for forbidden in ("api_key", "Authorization", "x-amca-session", "raw_prompt"):
            self.assertNotIn(forbidden, payload)


class TestGetBudget(unittest.TestCase):
    def test_get_budget_unauthorized(self) -> None:
        handler = _StubHandler(authorized=False)
        handle_cost_get_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(len(handler._errors), 1)
        self.assertEqual(handler._errors[0][0], HTTPStatus.UNAUTHORIZED)

    def test_get_budget_no_policy(self) -> None:
        handler = _StubHandler(authorized=True)
        handle_cost_get_budget(handler)
        self.assertEqual(len(handler._sent), 1)
        status, payload = handler._sent[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIsNone(payload["policy"])

    def test_get_budget_with_policy(self) -> None:
        handler = _StubHandler(authorized=True)
        state = get_cost_state(handler.context)
        state.policy = BudgetPolicy(
            run_budget_usd=1.0,
            project_budget_usd=5.0,
            warning_threshold_usd=0.5,
            strict_monetary=True,
        )
        handle_cost_get_budget(handler)
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(
            payload["policy"],
            {
                "run_budget_usd": 1.0,
                "project_budget_usd": 5.0,
                "warning_threshold_usd": 0.5,
                "strict_monetary": True,
            },
        )


class TestSetBudget(unittest.TestCase):
    def test_set_budget_unauthorized(self) -> None:
        handler = _StubHandler(authorized=False, body={"run_budget_usd": 1.0})
        handle_cost_set_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(handler._errors[0][0], HTTPStatus.UNAUTHORIZED)

    def test_set_budget_happy_path(self) -> None:
        handler = _StubHandler(
            authorized=True,
            body={
                "run_budget_usd": 0.5,
                "project_budget_usd": 2.0,
                "warning_threshold_usd": 0.4,
                "strict_monetary": False,
            },
        )
        handle_cost_set_budget(handler)
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["policy"]["run_budget_usd"], 0.5)
        self.assertEqual(payload["policy"]["project_budget_usd"], 2.0)
        # Confirm the policy is now active on the context.
        state = get_cost_state(handler.context)
        self.assertIsNotNone(state.policy)
        self.assertEqual(state.policy.run_budget_usd, 0.5)

    def test_set_budget_rejects_unknown_field(self) -> None:
        handler = _StubHandler(
            authorized=True,
            body={"run_budget_usd": 1.0, "surprise": "value"},
        )
        handle_cost_set_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(len(handler._errors), 1)
        self.assertEqual(handler._errors[0][0], HTTPStatus.BAD_REQUEST)
        self.assertIn("surprise", handler._errors[0][1])

    def test_set_budget_rejects_negative(self) -> None:
        handler = _StubHandler(
            authorized=True,
            body={"run_budget_usd": -0.01},
        )
        handle_cost_set_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(handler._errors[0][0], HTTPStatus.BAD_REQUEST)
        self.assertIn("run_budget_usd", handler._errors[0][1])

    def test_set_budget_rejects_nan(self) -> None:
        # NaN survives JSON encoding only when written as a literal
        # token. We send a string instead and check the validator
        # rejects the wrong type.
        handler = _StubHandler(
            authorized=True,
            body={"run_budget_usd": "NaN"},
        )
        handle_cost_set_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(handler._errors[0][0], HTTPStatus.BAD_REQUEST)

    def test_set_budget_rejects_boolean_for_number(self) -> None:
        handler = _StubHandler(
            authorized=True,
            body={"run_budget_usd": True},
        )
        handle_cost_set_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(handler._errors[0][0], HTTPStatus.BAD_REQUEST)

    def test_set_budget_strict_monetary_must_be_bool(self) -> None:
        handler = _StubHandler(
            authorized=True,
            body={"strict_monetary": "yes"},
        )
        handle_cost_set_budget(handler)
        self.assertEqual(handler._sent, [])
        self.assertEqual(handler._errors[0][0], HTTPStatus.BAD_REQUEST)

    def test_set_budget_empty_body_is_valid(self) -> None:
        # An empty object is a valid "no-op" policy.
        handler = _StubHandler(authorized=True, body={})
        handle_cost_set_budget(handler)
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["policy"]["run_budget_usd"], None)
        self.assertEqual(payload["policy"]["strict_monetary"], False)


class TestInstallCostRoutes(unittest.TestCase):
    def test_install_dispatches_state_get(self) -> None:
        handler = _StubHandler(authorized=True)
        self.assertTrue(install_cost_routes(handler, "/api/cost/state"))
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["run_id"], None)

    def test_install_dispatches_get_budget(self) -> None:
        handler = _StubHandler(authorized=True)
        self.assertTrue(install_cost_routes(handler, "/api/cost/budget"))
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIsNone(payload["policy"])

    def test_install_dispatches_set_budget(self) -> None:
        handler = _StubHandler(authorized=True, body={"run_budget_usd": 0.1})
        self.assertTrue(install_cost_routes(handler, "/api/cost/budget"))
        status, payload = handler._sent[-1]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["policy"]["run_budget_usd"], 0.1)

    def test_install_returns_false_for_unknown_path(self) -> None:
        handler = _StubHandler(authorized=True)
        self.assertFalse(install_cost_routes(handler, "/api/other"))


class TestRoutePrivacyAndGating(unittest.TestCase):
    def test_state_route_never_returns_authorization_header(self) -> None:
        handler = _StubHandler(authorized=True)
        state = get_cost_state(handler.context)
        state.policy = BudgetPolicy(run_budget_usd=1.0, strict_monetary=True)
        handle_cost_state(handler)
        _, payload = handler._sent[-1]
        for forbidden in ("Authorization", "authorization", "api_key", "x-amca-session", "raw_prompt"):
            self.assertNotIn(forbidden, payload)

    def test_set_budget_does_not_alter_feature_gates(self) -> None:
        handler = _StubHandler(
            authorized=True,
            body={"run_budget_usd": 1.0, "strict_monetary": True},
        )
        handle_cost_set_budget(handler)
        from amca.intelligence.gates import FeatureGateRegistry

        reg = FeatureGateRegistry()
        for gate in ("model_router", "adaptive_planner", "hybrid_execution", "nano_agents"):
            self.assertFalse(reg.is_enabled(gate), f"{gate} must remain OFF")


if __name__ == "__main__":
    unittest.main()
