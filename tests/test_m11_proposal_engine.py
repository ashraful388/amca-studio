"""M1.1 Adaptive Proposal Engine tests.

Focused coverage required by the issued M1.1 work order: candidate
construction, deterministic ranking, hard-constraint rejection (before any
scoring), cost/risk/permission limits, malformed and duplicate candidate
handling, non-finite score fail-closed, structured no-candidate result,
evaluator failure fallback signal, evidence field whitelist, deterministic
repeatability, and the adaptive feature gate staying OFF.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    AdaptiveProposal,
    AdaptiveProposalStatus,
    CandidateRejected,
    CandidateRiskClass,
    ProposalCandidate,
    ProposalConstraints,
    RejectedCandidate,
    Sensitivity,
    content_digest,
    propose,
)

CREATED = "2026-08-27T00:00:00+00:00"
DIMENSIONS = ("cost", "risk", "unit_count")


def meta(record_id="rec-m11", correlation="corr-m11"):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id=correlation,
        producer="m1.1-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def candidate(
    candidate_id="cand-a",
    estimated_cost=1.0,
    work_unit_count=2,
    risk_class=CandidateRiskClass.LOW,
    required_permissions=(),
):
    return ProposalCandidate(
        meta=meta("c-" + candidate_id),
        candidate_id=candidate_id,
        label=f"strategy {candidate_id}",
        collaboration_mode="solo",
        synchronization_mode="sequential",
        estimated_cost=estimated_cost,
        work_unit_count=work_unit_count,
        required_permissions=tuple(required_permissions),
        risk_class=risk_class,
    )


def constraints(**overrides):
    fields = dict(
        meta=meta("cons-1"),
        max_total_cost=5.0,
        max_work_units=4,
        allowed_permissions=("local_search",),
        max_allowed_risk=CandidateRiskClass.MEDIUM,
        scoring_dimensions=DIMENSIONS,
    )
    fields.update(overrides)
    return ProposalConstraints(**fields)


def flat_scores(cost=1.0, risk=0.0, unit_count=0.0):
    return {"cost": cost, "risk": risk, "unit_count": unit_count}


class CandidateConstructionTests(unittest.TestCase):
    def test_valid_candidate_constructs_and_round_trips(self):
        base = candidate()
        restored = ProposalCandidate.from_dict(base.to_dict())
        self.assertEqual(restored, base)
        self.assertEqual(restored.risk_class, CandidateRiskClass.LOW)

    def test_malformed_candidates_fail_closed(self):
        with self.assertRaises(Exception):
            ProposalCandidate(
                meta=meta("c-bad"),
                candidate_id="",
                label="x",
                collaboration_mode="solo",
                synchronization_mode="sequential",
                estimated_cost=1.0,
                work_unit_count=1,
                required_permissions=(),
                risk_class=CandidateRiskClass.LOW,
            )
        with self.assertRaises(Exception):
            candidate(estimated_cost=-1.0)
        with self.assertRaises(Exception):
            candidate(work_unit_count=0)
        with self.assertRaises(Exception):
            ProposalCandidate.from_dict(
                dict(candidate().to_dict(), estimated_cost=float("inf"))
            )
        with self.assertRaises(Exception):
            ProposalCandidate.from_dict(
                dict(candidate().to_dict(), risk_class="catastrophic")
            )


class HardConstraintTests(unittest.TestCase):
    def test_cost_limit_enforced_before_scoring(self):
        calls = []

        def evaluator(c):
            calls.append(c.candidate_id)
            return flat_scores()

        over = candidate("over-budget", estimated_cost=9.0)
        within = candidate("within-budget", estimated_cost=1.0)
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(over, within),
            constraints=constraints(),
            evaluator=evaluator,
        )
        self.assertEqual(outcome.status, AdaptiveProposalStatus.PROPOSED)
        self.assertEqual(outcome.selected.candidate_id, "within-budget")
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "over-budget"
        )
        self.assertEqual(rejection.reason_code, "cost_limit")
        # hard constraints run before scoring: the refused candidate is
        # never evaluated
        self.assertEqual(calls, ["within-budget"])

    def test_work_unit_bound_rejected(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("too-many-units", work_unit_count=9),),
            constraints=constraints(),
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(outcome.status, AdaptiveProposalStatus.BLOCKED)
        self.assertEqual(outcome.rejected[0].reason_code, "work_unit_bound")

    def test_risk_rejection(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("risky", risk_class=CandidateRiskClass.HIGH),),
            constraints=constraints(max_allowed_risk=CandidateRiskClass.LOW),
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(outcome.rejected[0].reason_code, "risk_exceeded")

    def test_permission_rejection(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("needs-network", required_permissions=("network",)),
            ),
            constraints=constraints(allowed_permissions=("local_search",)),
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(outcome.rejected[0].reason_code, "permission_not_granted")

    def test_duplicate_candidate_identifiers_rejected(self):
        with self.assertRaises(Exception) as ctx:
            propose(
                meta=meta(),
                request_ref="req-1",
                candidates=(candidate("dup"), candidate("dup")),
                constraints=constraints(),
                evaluator=lambda c: flat_scores(),
            )
        self.assertIn("duplicate candidate identifiers", str(ctx.exception))


class ScoringTests(unittest.TestCase):
    def test_deterministic_ranking_with_id_tie_break(self):
        left = candidate("cand-b", estimated_cost=2.0)
        right = candidate("cand-a", estimated_cost=2.0)

        def run():
            return propose(
                meta=meta(),
                request_ref="req-1",
                candidates=(left, right),
                constraints=constraints(),
                evaluator=lambda c: flat_scores(cost=c.estimated_cost),
            )

        first = run()
        second = run()
        self.assertEqual(first, second)
        self.assertEqual(
            [c.candidate_id for c in first.ranked], ["cand-a", "cand-b"]
        )
        self.assertEqual(first.selected.candidate_id, "cand-a")
        self.assertEqual(first.content_digest(), second.content_digest())

    def test_unknown_scoring_dimension_rejects_candidate(self):
        def evaluator(c):
            if c.candidate_id == "odd":
                return {"cost": 1.0, "surprise": 2.0}
            return flat_scores()

        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("odd"), candidate("plain")),
            constraints=constraints(),
            evaluator=evaluator,
        )
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "odd"
        )
        self.assertEqual(rejection.reason_code, "unknown_scoring_dimension")
        self.assertEqual(outcome.selected.candidate_id, "plain")

    def test_non_finite_score_fails_closed(self):
        def evaluator(c):
            if c.candidate_id == "nan":
                return {"cost": float("nan"), "risk": 0.0, "unit_count": 0.0}
            return flat_scores()

        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("nan"), candidate("finite")),
            constraints=constraints(),
            evaluator=evaluator,
        )
        rejection = next(
            r for r in outcome.rejected if r.candidate_id == "nan"
        )
        self.assertEqual(rejection.reason_code, "non_finite_score")
        self.assertEqual(outcome.selected.candidate_id, "finite")

    def test_all_candidates_score_rejected_yields_blocked(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("bad"),),
            constraints=constraints(),
            evaluator=lambda c: {"cost": float("inf"), "risk": 0.0,
                                 "unit_count": 0.0},
        )
        self.assertEqual(outcome.status, AdaptiveProposalStatus.BLOCKED)
        self.assertIsNone(outcome.selected)
        self.assertEqual(outcome.rejected[0].reason_code, "non_finite_score")


class FallbackSignalTests(unittest.TestCase):
    def test_evaluator_exception_yields_deterministic_fallback_signal(self):
        def evaluator(c):
            raise RuntimeError("scoring backend down")

        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate("cand-a"), candidate("cand-b")),
            constraints=constraints(),
            evaluator=evaluator,
        )
        self.assertEqual(
            outcome.status, AdaptiveProposalStatus.FALLBACK_SIGNAL
        )
        self.assertIsNone(outcome.selected)
        self.assertIn("scoring backend down", outcome.fallback_reason)
        # hard-constraint rejections found before the failure are preserved
        self.assertEqual(outcome.evidence["admissible_count"], 2)

    def test_fallback_signal_round_trips(self):
        def evaluator(c):
            raise ValueError("boom")

        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            constraints=constraints(),
            evaluator=evaluator,
        )
        restored = AdaptiveProposal.from_dict(outcome.to_dict())
        self.assertEqual(restored, outcome)


class StructuredOutcomeTests(unittest.TestCase):
    def test_no_eligible_candidate_yields_structured_blocked_result(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(
                candidate("expensive", estimated_cost=50.0),
                candidate("risky", risk_class=CandidateRiskClass.HIGH),
            ),
            constraints=constraints(),
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(outcome.status, AdaptiveProposalStatus.BLOCKED)
        self.assertIsNone(outcome.selected)
        self.assertEqual(outcome.ranked, ())
        self.assertEqual(len(outcome.rejected), 2)
        reason_codes = {r.reason_code for r in outcome.rejected}
        self.assertEqual(
            reason_codes, {"cost_limit", "risk_exceeded"}
        )

    def test_proposed_outcome_round_trips_and_binds_constraints(self):
        basis = constraints()
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            constraints=basis,
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(outcome.status, AdaptiveProposalStatus.PROPOSED)
        self.assertEqual(outcome.constraints_digest, content_digest(basis))
        restored = AdaptiveProposal.from_dict(outcome.to_dict())
        self.assertEqual(restored, outcome)

    def test_tampered_constraints_digest_rejected_on_read(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            constraints=constraints(),
            evaluator=lambda c: flat_scores(),
        )
        data = outcome.to_dict()
        data["constraints_digest"] = "0" * 64
        with self.assertRaises(Exception) as ctx:
            AdaptiveProposal.from_dict(data)
        self.assertIn("proposal", str(ctx.exception).lower())

    def test_evidence_whitelist_enforced(self):
        from amca.intelligence import ALLOWED_PROPOSAL_EVIDENCE_KEYS
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            constraints=constraints(),
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(
            set(outcome.evidence) - {"selected_estimated_cost"},
            {
                "candidate_count",
                "admissible_count",
                "rejected_count",
                "scoring_dimensions",
                "selected_candidate_id",
                "selected_total_score",
            },
        )
        self.assertTrue(set(outcome.evidence) <= ALLOWED_PROPOSAL_EVIDENCE_KEYS)
        forbidden = {"reasoning", "prompt", "scores", "candidates"}
        self.assertEqual(set(outcome.evidence) & forbidden, set())

    def test_no_candidate_input_yields_blocked(self):
        outcome = propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(),
            constraints=constraints(),
            evaluator=lambda c: flat_scores(),
        )
        self.assertEqual(outcome.status, AdaptiveProposalStatus.BLOCKED)


class FeatureGateTests(unittest.TestCase):
    def test_adaptive_gate_still_off_after_engine_use(self):
        propose(
            meta=meta(),
            request_ref="req-1",
            candidates=(candidate(),),
            constraints=constraints(),
            evaluator=lambda c: flat_scores(),
        )
        self.assertFalse(DEFAULT_GATES.adaptive_planner)
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
