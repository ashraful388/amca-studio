"""M4 Model Router Foundation tests.

Focused coverage required by the issued M4 work order: gate refusal in the
normal activation path, eligibility-before-ranking, provider/capability/
budget/cloud policies, deterministic ranking with tie-break, malformed
inputs fail closed, no invented providers/models, bounded fallback chain,
exhausted candidates, cancellation, evidence whitelist, repeatability, and
all feature gates remaining OFF. No real or paid model calls exist.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    RoutingCandidate,
    RoutingOutcome,
    RoutingOutcomeStatus,
    RoutingPolicy,
    Sensitivity,
    route,
    route_with_fallback,
    require_router_gate,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m4",
        producer="m4-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def meta(record_id="rec-m4"):
    return meta_factory(record_id)


def candidate(candidate_id="cand-a", provider="local/ollama", model="llama3:8b",
             local=True, capabilities=("chat", "tools"), estimated_cost=0.0,
             latency_ms=100.0, available=True):
    return RoutingCandidate(
        meta=meta("c-" + candidate_id),
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        local_execution=local,
        capabilities=tuple(capabilities),
        estimated_cost=estimated_cost,
        latency_ms=latency_ms,
        available=available,
    )


def policy(**overrides):
    fields = dict(
        meta=meta("policy-1"),
        allowed_providers=("local/ollama", "api/byok"),
        required_capabilities=("chat",),
        local_only=False,
        cloud_disabled=False,
        max_budget=1.0,
        strict_unknown_cost=False,
        maximum=3,
    )
    fields.update(overrides)
    return RoutingPolicy(**fields)


def router_gates():
    return FeatureGateRegistry(model_router=True)


class GateTests(unittest.TestCase):
    def test_router_gate_off_in_normal_activation_path(self):
        with self.assertRaises(ContractError) as ctx:
            require_router_gate(DEFAULT_GATES)
        self.assertIn("model_router gate", str(ctx.exception))
        with self.assertRaises(ContractError):
            route(
                meta=meta(),
                request_ref="req-1",
                candidates=(candidate(),),
                policy=policy(),
                gates=DEFAULT_GATES,
            )


class EligibilityTests(unittest.TestCase):
    def test_valid_authorized_candidate_selected(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            policy=policy(),
            gates=router_gates(),
        )
        self.assertEqual(outcome.status, RoutingOutcomeStatus.ROUTED)
        self.assertEqual(outcome.selected.candidate_id, "cand-a")
        self.assertEqual(outcome.ranked[0].candidate_id, "cand-a")

    def test_unauthorized_provider_rejected(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("allowed", provider="local/ollama"),
                candidate("stranger", provider="random-cloud/api"),
            ),
            policy=policy(),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "allowed")
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "stranger"
        )
        self.assertEqual(rejection.reason_code, "unauthorized_provider")

    def test_missing_required_capability_rejected(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("no-tools", capabilities=("chat",)),
                candidate("full", capabilities=("chat", "tools")),
            ),
            policy=policy(required_capabilities=("chat", "tools")),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "full")
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "no-tools"
        )
        self.assertEqual(rejection.reason_code, "missing_required_capability")

    def test_local_only_policy_excludes_cloud(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("cloudy", provider="api/byok", local=False),
                candidate("local", provider="local/ollama", local=True),
            ),
            policy=policy(local_only=True),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "local")
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "cloudy"
        )
        self.assertEqual(rejection.reason_code, "cloud_policy_excluded")

    def test_cloud_disabled_policy_enforced(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("cloudy", provider="api/byok", local=False),
                candidate("local", provider="local/ollama", local=True),
            ),
            policy=policy(cloud_disabled=True),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "local")

    def test_budget_limit_enforced(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("pricey", estimated_cost=9.0),
                candidate("cheap", estimated_cost=0.5),
            ),
            policy=policy(max_budget=1.0),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "cheap")
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "pricey"
        )
        self.assertEqual(rejection.reason_code, "budget_limit")

    def test_unknown_cost_under_strict_budget_fails_safely(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("mystery", estimated_cost=None),
                candidate("known", estimated_cost=0.2),
            ),
            policy=policy(strict_unknown_cost=True),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "known")
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "mystery"
        )
        self.assertEqual(rejection.reason_code, "unknown_cost_strict_budget")

    def test_unavailable_candidate_skipped(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("down", latency_ms=1.0, available=False),
                candidate("up", latency_ms=50.0),
            ),
            policy=policy(),
            gates=router_gates(),
        )
        self.assertEqual(outcome.selected.candidate_id, "up")
        self.assertEqual(outcome.evidence["skipped_count"], 1)
        # skipped candidates are not rejections
        self.assertEqual(outcome.rejected, ())


class RankingTests(unittest.TestCase):
    def test_deterministic_ranking_with_id_tie_break(self):
        left = candidate("cand-b", estimated_cost=1.0, latency_ms=10.0)
        right = candidate("cand-a", estimated_cost=1.0, latency_ms=10.0)

        def run():
            return route(
                meta=meta(),
                request_ref="req-1",
                candidates=(left, right),
                policy=policy(),
                gates=router_gates(),
            )

        first = run()
        second = run()
        self.assertEqual(first, second)
        self.assertEqual(
            [c.candidate_id for c in first.ranked], ["cand-a", "cand-b"]
        )
        self.assertEqual(first.content_digest(), second.content_digest())

    def test_duplicate_candidate_id_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            route(
                meta=meta(),
                request_ref="req-1",
                candidates=(candidate("dup"), candidate("dup")),
                policy=policy(),
                gates=router_gates(),
            )
        self.assertIn("duplicate candidate identifiers", str(ctx.exception))

    def test_malformed_non_finite_cost_rejected(self):
        with self.assertRaises(Exception):
            candidate("bad", estimated_cost=float("nan"))
        with self.assertRaises(Exception):
            candidate("bad", latency_ms=float("inf"))
        with self.assertRaises(Exception):
            RoutingCandidate.from_dict(
                dict(candidate("bad").to_dict(), estimated_cost=-3.0)
            )


class FallbackTests(unittest.TestCase):
    def test_bounded_fallback_chain_routes_on_second_policy(self):
        strict = policy(meta=meta("p-strict"), max_budget=0.0,
                        strict_unknown_cost=True)
        relaxed = policy(meta=meta("p-relaxed"), max_budget=5.0)
        outcome, index = route_with_fallback(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("cand-a", estimated_cost=0.5),),
            policies=(strict, relaxed),
            gates=router_gates(),
        )
        self.assertEqual(outcome.status, RoutingOutcomeStatus.ROUTED)
        self.assertEqual(index, 1)
        self.assertEqual(outcome.evidence["policy_index"], 1)

    def test_exhausted_candidates_yield_blocked(self):
        outcome, index = route_with_fallback(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("pricey", estimated_cost=99.0),),
            policies=(policy(meta=meta("p1")), policy(meta=meta("p2"))),
            gates=router_gates(),
        )
        self.assertEqual(outcome.status, RoutingOutcomeStatus.BLOCKED)
        self.assertIsNone(outcome.selected)
        self.assertEqual(index, 2)
        self.assertGreaterEqual(len(outcome.rejected), 2)

    def test_cancellation_halts_fallback(self):
        outcome_container = {}

        def cancel_after_first():
            outcome_container["count"] = outcome_container.get("count", 0) + 1
            return outcome_container["count"] > 1

        with self.assertRaises(ContractError) as ctx:
            route_with_fallback(
                meta=meta(),
                request_ref="req-1",
                candidates=(candidate("pricey", estimated_cost=99.0),),
                policies=(policy(meta=meta("p1")), policy(meta=meta("p2"))),
                gates=router_gates(),
                cancel_check=cancel_after_first,
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.CANCELLED)

    def test_empty_fallback_chain_rejected(self):
        with self.assertRaises(ContractError):
            route_with_fallback(
                meta=meta(),
                request_ref="req-1",
                candidates=(candidate(),),
                policies=(),
                gates=router_gates(),
            )


class EvidenceTests(unittest.TestCase):
    def test_evidence_whitelist(self):
        from amca.intelligence import ALLOWED_ROUTING_EVIDENCE_KEYS
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            policy=policy(),
            gates=router_gates(),
        )
        self.assertTrue(set(outcome.evidence) <= ALLOWED_ROUTING_EVIDENCE_KEYS)
        forbidden = {"api_key", "prompt", "reasoning", "scores"}
        self.assertEqual(set(outcome.evidence) & forbidden, set())

    def test_outcome_round_trip(self):
        outcome = route(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            policy=policy(),
            gates=router_gates(),
        )
        restored = RoutingOutcome.from_dict(outcome.to_dict())
        self.assertEqual(restored, outcome)


class FeatureGateTests(unittest.TestCase):
    def test_all_gates_remain_off(self):
        route(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            policy=policy(),
            gates=router_gates(),
        )
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
