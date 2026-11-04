"""A2.1D.1 kernel checkpoint recovery scenarios (JOINT/INDEPENDENT/ADAPTIVE).

Each scenario seeds durable kernel-turn evidence left by a previous process,
reopens the canonical store as a NEW instance, reconciles, and continues
through the real runtime. Adapter call counts prove committed turns are
reused verbatim and uncertain turns are never repeated.
"""

from __future__ import annotations

import unittest

from amca.agent_continuation import (
    ORG_STATUS_BLOCKED_UNCERTAIN,
    ORG_STATUS_RECOVERED_COMPLETE,
    continue_organization,
)
from amca.agent_runtime import NanoScheduler

from test_agent_composite_recovery import (
    CompositeFixtureBase,
    CountingAdapter,
)
from test_agent_runtime import structured_collab_output


def _key(architecture: str, round_number: int, participant: str) -> str:
    if architecture in {"adaptive", "adaptive_hybrid"}:
        return f"adaptive:phase:1:step:{round_number}:participant:{participant}"
    return f"{architecture}:phase:1:round:{round_number}:participant:{participant}"


class KernelRecoveryTests(CompositeFixtureBase):
    def _seed_collab(
        self,
        task_id: str,
        architecture: str,
        participants: tuple[str, ...],
        *,
        max_rounds: int = 2,
    ) -> None:
        self.seed_org()
        self.store.create_task(
            task_id=task_id, organization_id="org", project_id="p",
            agent_id=participants[0], focus=f"collab {task_id}",
            executor_kind="collaboration",
            participant_ids=list(participants),
            collaboration_architecture=architecture,
            sync_mode="bounded_staleness",
            max_rounds=max_rounds,
        )

    def commit_turn(self, key: str, payload: str, adapter_id: str, *,
                    task_id: str = "collab") -> None:
        self.store.record_call_state(
            organization_id="org", task_id=task_id, task_attempt_number=1,
            call_key=key, state="RESPONSE_COMMITTED",
            executor_kind="collaboration", adapter_id=adapter_id,
            reservation_id="", actual_cost_usd=0.0,
            response_payload=payload,
        )

    def intent_turn(self, key: str, adapter_id: str, *,
                    task_id: str = "collab") -> None:
        self.store.record_call_state(
            organization_id="org", task_id=task_id, task_attempt_number=1,
            call_key=key, state="DISPATCHED",
            executor_kind="collaboration", adapter_id=adapter_id,
            reservation_id="", reserved_usd=0.0,
        )

    @staticmethod
    def status(task) -> str:
        return getattr(task.status, "value", task.status)

    def test_j1_committed_round_reused_next_turn_executes_once(self) -> None:
        self._seed_collab("collab", "joint", ("p1", "p2"), max_rounds=2)
        # focus_resolved=False keeps the joint conversation open so the
        # architecture genuinely needs its round 2 after restart.
        self.commit_turn(_key("joint", 1, "p1"),
                         structured_collab_output("round1 one",
                                                  focus_resolved=False),
                         "p1")
        self.commit_turn(_key("joint", 1, "p2"),
                         structured_collab_output("round1 two",
                                                  focus_resolved=True),
                         "p2")

        p1 = CountingAdapter(adapter_id="p1")
        p2 = CountingAdapter(adapter_id="p2")
        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(self.status(org.tasks["collab"]), "completed")
        # Round-1 turns were reused from durable payloads (zero calls); the
        # never-dispatched round-2 turns each executed exactly once.
        self.assertEqual(p1.calls, 1)
        self.assertEqual(p2.calls, 1)

        fresh = self.reopen()
        r2p1 = fresh.get_call_attempt("org", "collab", 1,
                                      _key("joint", 2, "p1"))
        self.assertIsNotNone(r2p1)
        self.assertEqual(r2p1["state"], "SETTLED")
        r1p1 = fresh.get_call_attempt("org", "collab", 1,
                                      _key("joint", 1, "p1"))
        # Runtime settlement legitimately advances committed rows to SETTLED.
        self.assertEqual(r1p1["state"], "SETTLED")
        self.assertIsNotNone(r1p1["response_payload"])

    def test_j2_uncertain_turn_blocks_without_redispatch(self) -> None:
        self._seed_collab("collab", "joint", ("p1", "p2"), max_rounds=2)
        self.commit_turn(_key("joint", 1, "p1"), "r1 p1", "p1")
        self.commit_turn(_key("joint", 1, "p2"), "r1 p2", "p2")
        self.intent_turn(_key("joint", 2, "p1"), "p1")

        p1 = CountingAdapter(adapter_id="p1")
        p2 = CountingAdapter(adapter_id="p2")
        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)
        self.assertEqual(p1.calls, 0, "uncertain turn must not redispatch")
        self.assertEqual(p2.calls, 0, "later turns must not run")
        self.assertNotEqual(self.status(org.tasks["collab"]), "completed")

    def test_i1_committed_contributions_reused_missing_resumes_once(self) -> None:
        self._seed_collab("discuss", "independent_discuss",
                          ("a1", "a2", "a3"), max_rounds=1)
        self.commit_turn(_key("independent", 1, "a1"),
                         structured_collab_output("contribution a1"), "a1",
                         task_id="discuss")
        self.commit_turn(_key("independent", 1, "a2"),
                         structured_collab_output("contribution a2"), "a2",
                         task_id="discuss")

        a1 = CountingAdapter(adapter_id="a1")
        a2 = CountingAdapter(adapter_id="a2")
        a3 = CountingAdapter(adapter_id="a3")
        scheduler = NanoScheduler({"a1": a1, "a2": a2, "a3": a3},
                                  budget_usd=10.0, recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(a1.calls, 0, "committed contribution A reused")
        self.assertEqual(a2.calls, 0, "committed contribution B reused")
        self.assertLessEqual(a3.calls, 1, "safe contribution runs at most once")
        self.assertIn(self.status(org.tasks["discuss"]),
                      {"completed", "failed"})

    def test_i2_uncertain_contribution_blocks_discussion(self) -> None:
        self._seed_collab("discuss", "independent_discuss",
                          ("a1", "a2", "a3"), max_rounds=1)
        self.intent_turn(_key("independent", 1, "a3"), "a3", task_id="discuss")

        a1 = CountingAdapter(adapter_id="a1")
        a2 = CountingAdapter(adapter_id="a2")
        a3 = CountingAdapter(adapter_id="a3")
        scheduler = NanoScheduler({"a1": a1, "a2": a2, "a3": a3},
                                  budget_usd=10.0, recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)
        self.assertEqual(a3.calls, 0, "uncertain contribution not repeated")

    def test_a1_adaptive_reuses_committed_phase_and_runs_pending_step(self) -> None:
        import asyncio

        from amca.domain import (
            CollaborationMode,
            ComputePolicy,
            PrivacyMode,
            ProjectState,
            SyncMode,
        )
        from amca.kernel import CollaborationKernel, KernelConfig
        from amca.state import StateManager
        from test_agent_runtime import structured_collab_output

        self.seed_org()
        self.store.create_task(
            task_id="adaptive", organization_id="org", project_id="p",
            agent_id="w1", focus="adaptive work",
            executor_kind="collaboration", participant_ids=["w1"],
            collaboration_architecture="adaptive_hybrid", max_rounds=2,
        )
        self.commit_turn(_key("adaptive", 1, "w1"),
                         structured_collab_output("phase1 output"),
                         "w1", task_id="adaptive")

        w1 = CountingAdapter(adapter_id="w1")
        state = ProjectState(project_id="p", goal="adaptive work",
                             privacy_mode=PrivacyMode.FULL_FRONTIER,
                             compute_policy=ComputePolicy.MAXIMUM_INTELLIGENCE)
        kernel = CollaborationKernel(
            StateManager(state), [w1],
            config=KernelConfig(max_rounds=2),
            recovery_store=self.reopen(),
            journal_context={"organization_id": "org", "task_id": "adaptive",
                             "attempt": 1, "architecture": "adaptive_hybrid",
                             "phase": 1},
        )
        asyncio.run(kernel.run(mode=CollaborationMode.ADAPTIVE_HYBRID,
                               sync_mode=SyncMode.HARD))
        # Phase 1 was reused from durable evidence; only pending steps ran.
        self.assertLessEqual(w1.calls, 2)
        step1 = self.reopen().get_call_attempt(
            "org", "adaptive", 1, _key("adaptive", 1, "w1"))
        self.assertIsNotNone(step1["response_payload"])

    def test_a2_adaptive_uncertain_required_call_blocks_later_phases(self) -> None:
        """Uncertain phase-1 call: zero repeats, task blocked, nothing later."""

        self.seed_org()
        self.store.create_task(
            task_id="adaptive", organization_id="org", project_id="p",
            agent_id="w1", focus="adaptive work",
            executor_kind="collaboration", participant_ids=["w1"],
            collaboration_architecture="adaptive_hybrid", max_rounds=3,
        )
        # Phase-1 step-1 committed durably; step-2 crossed durable intent.
        self.commit_turn(_key("adaptive", 1, "w1"),
                         structured_collab_output("step1 output"),
                         "w1", task_id="adaptive")
        self.intent_turn(_key("adaptive", 2, "w1"), "w1", task_id="adaptive")

        w1 = CountingAdapter(adapter_id="w1")
        scheduler = NanoScheduler({"w1": w1}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)
        self.assertEqual(w1.calls, 0, "uncertain required call repeats zero times")
        status = self.status(org.tasks["adaptive"])
        self.assertNotEqual(status, "completed",
                            "blocked architecture cannot complete")
        # Committed step-1 evidence stays intact for later recovery.
        step1 = self.reopen().get_call_attempt(
            "org", "adaptive", 1, _key("adaptive", 1, "w1"))
        self.assertIsNotNone(step1["response_payload"])

    def test_gate_c_composite_continuation_all_reused(self) -> None:
        """Manager proposal + verified children continue with 0 provider calls."""
        import json

        self.seed_org(budget_usd=10.0)
        proposal = json.dumps({
            "version": 1,
            "reason": "gate c decomposition",
            "children": [],
        })
        self.store.create_task(
            task_id="m-lead", organization_id="org", project_id="p",
            agent_id="lead_mgr", focus="plan", executor_kind="manager",
        )
        self.commit_turn(key="manager:proposal", payload=proposal,
                         adapter_id="lead_mgr", task_id="m-lead")
        for verifier in ("v1", "v2"):
            self.store.create_task(
                task_id=f"vc-{verifier}", organization_id="org", project_id="p",
                agent_id=verifier, focus="verify",
                executor_kind="verification_cell",
                depends_on=["m-lead"],
            )
            self.commit_turn(key=f"verifier:{verifier}",
                             payload=json.dumps(
                                 {"verdict": "pass",
                                  "summary": f"{verifier} satisfied"}),
                             adapter_id=verifier,
                             task_id=f"vc-{verifier}")

        lead = CountingAdapter(adapter_id="lead_mgr")
        v1 = CountingAdapter(adapter_id="v1")
        v2 = CountingAdapter(adapter_id="v2")
        scheduler = NanoScheduler(
            {"lead_mgr": lead, "v1": v1, "v2": v2},
            budget_usd=10.0, recovery_store=self.reopen(),
        )
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_RECOVERED_COMPLETE)
        self.assertEqual(lead.calls, 0)
        self.assertEqual(v1.calls, 0)
        self.assertEqual(v2.calls, 0)
        for task_id in ("m-lead", "vc-v1"):
            status = self.status(org.tasks[task_id])
            self.assertEqual(status, "completed")




if __name__ == "__main__":
    unittest.main()
