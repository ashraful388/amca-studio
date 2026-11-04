"""A1 Agent Fabric runtime fixtures - the three stop-gate demonstrations."""

from __future__ import annotations

import asyncio
import json
from typing import Any
import unittest

from amca.agent_fabric import AgentFabricSettings, AgentGovernor, ResourceEnvelope
from amca.agent_runtime import (
    AgentTaskRecord,
    AgentTaskStatus,
    DeterministicManagerAdapter,
    DuplicateResultReuse,
    ExecutorKind,
    NanoScheduler,
    OrganizationRun,
    parse_manager_proposal_payload,
)
from amca.domain import CollaborationMode, SyncMode
from amca.models.base import ModelResponse, TokenUsage
from amca.models.scripted import ScriptedAdapter
from amca.modes import DecompositionPlan, WorkUnit
from amca.security import SensitiveDataError, assert_no_sensitive_keys, is_sensitive_key


class CallHistoryTests(unittest.TestCase):
    def test_cloud_and_local_executor_labels_use_adapter_capabilities(self):
        from amca.agent_runtime import live_organization_from_call_records
        for local, expected in ((True, ExecutorKind.NANO_LOCAL_MODEL),
                                (False, ExecutorKind.NANO_CLOUD_MODEL)):
            with self.subTest(local=local):
                adapter = ScriptedAdapter(["ok"], adapter_id="test", is_local=local)
                doc = live_organization_from_call_records(
                    [{"adapter_id": "test", "status": "complete", "cost_usd": 0.01}],
                    organization_id="history", project_id="p", goal="g",
                    adapters_by_id={"test": adapter},
                )
                self.assertEqual(doc["tasks"][0]["executor_kind"], expected)

    def test_reconstructed_timing_never_claims_execution_time(self):
        from amca.agent_runtime import live_organization_from_call_records
        doc = live_organization_from_call_records(
            [{"adapter_id": "test", "status": "failed", "cost_usd": 0}],
            organization_id="history", project_id="p", goal="g",
        )
        task = doc["tasks"][0]
        self.assertEqual(task["source"], "reconstructed_from_calls")
        self.assertIsNone(task["started_at"])
        self.assertIsNone(task["completed_at"])
        self.assertTrue(task["created_at"])

    def test_captured_call_timing_is_preserved(self):
        from amca.agent_runtime import live_organization_from_call_records
        started, completed = "2026-01-01T00:00:00Z", "2026-01-01T00:00:02Z"
        doc = live_organization_from_call_records(
            [{"adapter_id": "test", "status": "complete", "cost_usd": 0,
              "started_at": started, "completed_at": completed}],
            organization_id="history", project_id="p", goal="g",
        )
        self.assertEqual(doc["tasks"][0]["started_at"], started)
        self.assertEqual(doc["tasks"][0]["completed_at"], completed)


class FakeResponse:
    def __init__(self, text: str = "nano contribution", cost_usd: float = 0.01):
        self.text = text
        self.usage = type("Usage", (), {"cost_usd": cost_usd})()


class FakeAdapter:
    def __init__(
        self,
        *,
        delay: float = 0.0,
        fail_first: int = 0,
        cost_usd: float = 0.01,
        is_conservative_bound: bool = True,
        is_local: bool = True,
    ) -> None:
        self.delay = delay
        self.fail_first = fail_first
        self.cost_usd = cost_usd
        self.is_conservative_bound = is_conservative_bound
        self.is_local = is_local
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.calls <= self.fail_first:
            exc = RuntimeError("simulated transient failure")
            setattr(exc, "cost_usd", self.cost_usd)
            raise exc
        if self.delay:
            await asyncio.sleep(self.delay)
        return FakeResponse(f"done:{request.messages[0]['content'][:40]}", cost_usd=self.cost_usd)


class LifecycleRuleTests(unittest.TestCase):
    def test_invalid_transition_is_rejected(self) -> None:
        task = AgentTaskRecord(
            id="t", organization_id="o", project_id="p",
            agent_id="a", focus="f",
        )
        with self.assertRaises(ValueError):
            task.transition(AgentTaskStatus.COMPLETED)

    def test_completed_merges_then_retires_only(self) -> None:
        task = AgentTaskRecord(
            id="t", organization_id="o", project_id="p",
            agent_id="a", focus="f", status=AgentTaskStatus.COMPLETED,
        )
        task.transition(AgentTaskStatus.MERGED)
        task.transition(AgentTaskStatus.RETIRED)
        self.assertEqual(task.status, AgentTaskStatus.RETIRED)


class ReuseRegistryTests(unittest.TestCase):
    def test_register_requires_non_empty_summary(self) -> None:
        registry = DuplicateResultReuse()
        with self.assertRaises(ValueError):
            registry.register("digest", "   ")

    def test_lookup_miss_returns_none(self) -> None:
        self.assertIsNone(DuplicateResultReuse().lookup("nope"))


class FixtureADeterministicOnlyTests(unittest.TestCase):
    """Stop gate A: deterministic-only task with zero model agents."""

    def test_three_node_graph_runs_without_any_model_call(self) -> None:
        scheduler = NanoScheduler({}, budget_usd=5.0)
        graph = [
            {"id": "plan", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
             "focus": "draft plan"},
            {"id": "compute", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
             "focus": "evaluate expression", "depends_on": ["plan"]},
            {"id": "format", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
             "focus": "format output", "depends_on": ["compute"]},
        ]
        org = scheduler.schedule(graph, organization_id="org-a", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertTrue(all(
            task.status == AgentTaskStatus.COMPLETED for task in org.tasks.values()
        ))
        self.assertEqual(org.total_cost_usd(), 0.0)
        self.assertEqual(sum(task.local_calls + task.api_calls for task in org.tasks.values()), 0)
        self.assertEqual(org.active_count(), 0)

        asyncio.run(scheduler.retire(org))
        self.assertTrue(org.retired)
        self.assertTrue(all(
            task.status == AgentTaskStatus.RETIRED for task in org.tasks.values()
        ))


class FixtureBParallelNanoTests(unittest.TestCase):
    """Stop gate B: parallel Nano/Micro task with real lifecycle transitions."""

    def test_four_leaves_and_integration_execute_with_ordering(self) -> None:
        adapters = {
            "worker-1": FakeAdapter(delay=0.02),
            "worker-2": FakeAdapter(delay=0.02),
        }
        scheduler = NanoScheduler(adapters, budget_usd=10.0, max_concurrent=4)
        graph = [
            {"id": f"leaf-{index}", "agent_id": "worker-1" if index % 2 else "worker-2",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
             "focus": f"leaf focus {index}", "depends_on": []}
            for index in range(4)
        ]
        graph.append({
            "id": "integration", "agent_id": "worker-1",
            "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
            "focus": "integrate leaf results",
            "depends_on": ["leaf-0", "leaf-1", "leaf-2", "leaf-3"],
        })
        org = scheduler.schedule(graph, organization_id="org-b", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        asyncio.run(scheduler.retire(org))

        self.assertTrue(all(
            task.status == AgentTaskStatus.RETIRED for task in org.tasks.values()
        ))
        integration = org.tasks["integration"]
        for leaf_id in ("leaf-0", "leaf-1", "leaf-2", "leaf-3"):
            self.assertIsNotNone(org.tasks[leaf_id].completed_at)
            self.assertGreaterEqual(
                integration.started_at, org.tasks[leaf_id].completed_at,
                "integration must not start before every leaf completed",
            )
        self.assertGreaterEqual(sum(task.local_calls for task in org.tasks.values()), 5)


class FixtureCBudgetManagedTests(unittest.TestCase):
    """Stop gate C: managed run within a hard budget."""

    def test_third_nano_task_is_cancelled_when_budget_exhausted(self) -> None:
        adapters = {
            "w1": FakeAdapter(),
            "w2": FakeAdapter(),
        }
        # Two paid nano calls at 0.01 each exhaust a 0.02 budget.
        scheduler = NanoScheduler(adapters, budget_usd=0.02, max_concurrent=2)
        graph = [
            {"id": "prep", "agent_id": "w1",
             "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "focus": "prepare"},
            {"id": "paid-1", "agent_id": "w1",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "paid one",
             "depends_on": ["prep"]},
            {"id": "paid-2", "agent_id": "w2",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "paid two",
             "depends_on": ["prep"]},
            {"id": "paid-3", "agent_id": "w2",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "paid three",
             "depends_on": ["prep"]},
        ]
        org = scheduler.schedule(graph, organization_id="org-c", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["paid-3"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget", str(org.tasks["paid-3"].error))
        self.assertLessEqual(org.total_cost_usd(), 0.02 + 1e-9)
        asyncio.run(scheduler.retire(org))
        self.assertTrue(org.retired)
        merged_or_retired = {
            task.status
            for task in org.tasks.values()
            if task.status in {AgentTaskStatus.MERGED, AgentTaskStatus.RETIRED}
        }
        self.assertTrue(merged_or_retired)


class SchedulerGuardTests(unittest.TestCase):
    def test_unknown_dependency_is_rejected_at_schedule_time(self) -> None:
        scheduler = NanoScheduler({}, budget_usd=1.0)
        with self.assertRaises(ValueError):
            scheduler.schedule(
                [{"id": "x", "depends_on": ["ghost"], "focus": "f"}],
                organization_id="o", project_id="p", goal="g",
            )

    def test_transient_failure_retries_then_completes(self) -> None:
        adapter = FakeAdapter(fail_first=1)
        scheduler = NanoScheduler({"w": adapter}, budget_usd=5.0, max_attempts=2)
        org = scheduler.schedule(
            [{"id": "n", "agent_id": "w",
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "retry me"}],
            organization_id="o", project_id="p", goal="g",
        )
        org = asyncio.run(scheduler.run(org))
        self.assertEqual(org.tasks["n"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["n"].attempts, 2)
        self.assertIsNone(org.tasks["n"].error)

    def test_duplicate_focus_reuses_verified_result_without_calls(self) -> None:
        reuse = DuplicateResultReuse()
        digest_source = AgentTaskRecord(
            id="seed", organization_id="o", project_id="p",
            agent_id="w", executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
            focus="identical focus",
        )
        adapters = {"w": FakeAdapter()}
        reuse.register(digest_source.execution_identity("v2", adapters=adapters),
                       "cached verified summary", version="v2")
        scheduler = NanoScheduler(adapters, budget_usd=5.0, reuse=reuse)
        org = scheduler.schedule(
            [{"id": "dup", "agent_id": "w",
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
              "focus": "identical focus"}],
            organization_id="o", project_id="p", goal="g",
        )
        self.assertEqual(org.tasks["dup"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["dup"].reused_from,
                         digest_source.execution_identity("v2", adapters={"w": FakeAdapter()}))
        self.assertEqual(org.tasks["dup"].local_calls, 0)
        org = asyncio.run(scheduler.run(org))
        self.assertEqual(org.total_cost_usd(), 0.0)


class FakeVerifierAdapter:
    def __init__(
        self,
        verdict: str = "pass",
        summary: str = "verified claim",
        refs: list[str] | None = None,
        cost_usd: float = 0.01,
        delay: float = 0.0,
        raw_text: str | None = None,
        is_local: bool = True,
        is_conservative_bound: bool = True,
    ) -> None:
        self.verdict = verdict
        self.summary = summary
        self.refs = refs or []
        self.cost_usd = cost_usd
        self.delay = delay
        self.raw_text = raw_text
        self.is_local = is_local
        self.is_conservative_bound = is_conservative_bound
        self.received_requests: list[Any] = []

    @property
    def calls(self) -> int:
        return len(self.received_requests)

    async def generate(self, request):
        self.received_requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raw_text is not None:
            return FakeResponse(self.raw_text, cost_usd=self.cost_usd)
        payload = json.dumps({
            "verdict": self.verdict,
            "summary": self.summary,
            "evidence_refs": self.refs,
        })
        return FakeResponse(payload, cost_usd=self.cost_usd)


class VerificationCellUnitTests(unittest.TestCase):
    """Audited Verification Cell runtime unit tests."""

    def test_two_independent_pass_verifiers_yields_verified(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass", summary="v1 check passed", refs=["ref-A"]),
            "v2": FakeVerifierAdapter(verdict="pass", summary="v2 check passed", refs=["ref-B"]),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-1",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "verify claim X against source Y",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-1"]

        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertIsNotNone(task.verification_result)
        v_res = task.verification_result
        self.assertEqual(v_res["outcome"], "verified")
        self.assertFalse(v_res["dissent_present"])
        self.assertEqual(v_res["total_verifiers"], 2)
        self.assertEqual(v_res["verdicts"]["v1"]["verdict"], "pass")
        self.assertEqual(v_res["verdicts"]["v2"]["verdict"], "pass")

    def test_pass_and_fail_verifiers_yields_disputed(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass", summary="all good"),
            "v2": FakeVerifierAdapter(verdict="fail", summary="contradiction found in doc"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-dispute",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "verify disputed hypothesis",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-dispute"]

        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(task.verification_result["outcome"], "disputed")
        self.assertTrue(task.verification_result["dissent_present"])

    def test_no_majority_vote_shortcut_two_pass_one_fail_is_disputed(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="pass"),
            "v3": FakeVerifierAdapter(verdict="fail", summary="subtle flaw detected"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-non-majoritarian",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2", "v3"],
                "focus": "audit security boundary",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-non-majoritarian"]

        # 2 PASS + 1 FAIL MUST be DISPUTED, never majority-vote VERIFIED!
        self.assertEqual(task.verification_result["outcome"], "disputed")
        self.assertTrue(task.verification_result["dissent_present"])
        self.assertEqual(task.verification_result["total_verifiers"], 3)

    def test_pass_and_uncertain_yields_inconclusive(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="uncertain", summary="insufficient context"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-inc",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "evaluate ambiguous evidence",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-inc"]

        self.assertEqual(task.verification_result["outcome"], "inconclusive")
        self.assertFalse(task.verification_result["dissent_present"])

    def test_malformed_verifier_verdict_yields_inconclusive_never_verified(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(raw_text="Random unparseable garbage without verdict"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-malformed",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "verify with noisy output",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-malformed"]

        self.assertEqual(task.verification_result["outcome"], "inconclusive")

    def test_raw_text_pass_cannot_produce_verified(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(raw_text="PASS"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-raw-pass",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "raw text pass check",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-raw-pass"]

        # Raw prose "PASS" without structured JSON is uncertain -> outcome inconclusive
        self.assertEqual(task.verification_result["outcome"], "inconclusive")
        self.assertEqual(task.verification_result["verdicts"]["v2"]["verdict"], "uncertain")

    def test_missing_summary_in_json_cannot_produce_verified(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(raw_text='{"verdict": "pass"}'),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-no-summary",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "json without summary",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-no-summary"]

        self.assertEqual(task.verification_result["outcome"], "inconclusive")
        self.assertEqual(task.verification_result["verdicts"]["v2"]["verdict"], "uncertain")

    def test_evidence_ref_bounds_are_enforced(self) -> None:
        long_summary = "A" * 600
        long_refs = [f"ref-{'x' * 150}-{i}" for i in range(40)]
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="pass", summary=long_summary, refs=long_refs),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-bounds",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "bounds check",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-bounds"]

        self.assertEqual(task.verification_result["outcome"], "verified")
        v2_res = task.verification_result["verdicts"]["v2"]
        self.assertLessEqual(len(v2_res["summary"]), 400)
        self.assertLessEqual(len(v2_res["evidence_refs"]), 20)
        self.assertTrue(all(len(ref) <= 100 for ref in v2_res["evidence_refs"]))

    def test_single_verifier_fails_closed(self) -> None:
        adapters = {"v1": FakeVerifierAdapter(verdict="pass")}
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-solo",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1"],
                "focus": "single verifier attempt",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-solo"]

        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("requires at least 2 explicit verifier_ids", str(task.error))

    def test_missing_verifier_ids_fails_closed(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="pass"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-no-ids",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "focus": "missing verifier ids attempt",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-no-ids"]

        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("requires at least 2 explicit verifier_ids", str(task.error))

    def test_unavailable_requested_verifier_fails_closed_without_replacement(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="pass"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-ghost",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "ghost-adapter"],
                "focus": "ghost verifier test",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-ghost"]

        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("requested unknown or unavailable verifier adapter(s)", str(task.error))
        self.assertIn("ghost-adapter", str(task.error))

    def test_comma_separated_agent_id_fails_closed_when_verifier_ids_missing(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="pass"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-csv",
                "agent_id": "v1,v2",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "focus": "comma separated agent_id attempt",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-csv"]

        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("requires at least 2 explicit verifier_ids", str(task.error))

    def test_all_verifiers_receive_same_focus_independently_and_concurrently(self) -> None:
        v1 = FakeVerifierAdapter(verdict="pass", delay=0.02)
        v2 = FakeVerifierAdapter(verdict="pass", delay=0.02)
        adapters = {"v1": v1, "v2": v2}
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-prompt",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "exact isolated prompt test",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(len(v1.received_requests), 1)
        self.assertEqual(len(v2.received_requests), 1)
        p1 = v1.received_requests[0].messages[0]["content"]
        p2 = v2.received_requests[0].messages[0]["content"]
        self.assertEqual(p1, "exact isolated prompt test")
        self.assertEqual(p2, "exact isolated prompt test")
        self.assertNotIn("v1", p2)
        self.assertNotIn("v2", p1)

    def test_actual_aggregate_cost_equals_sum_of_response_usage(self) -> None:
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass", cost_usd=0.025),
            "v2": FakeVerifierAdapter(verdict="pass", cost_usd=0.035),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-cost",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "cost check",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-cost"]

        self.assertAlmostEqual(task.cost_usd, 0.060, places=5)
        self.assertEqual(task.local_calls, 2)
        self.assertEqual(task.api_calls, 0)
        self.assertAlmostEqual(org.total_cost_usd(), 0.060, places=5)

    def test_mixed_local_and_api_verifiers_records_calls_truthfully(self) -> None:
        adapters = {
            "local-v": FakeVerifierAdapter(verdict="pass", is_local=True, cost_usd=0.0),
            "cloud-v": FakeVerifierAdapter(verdict="pass", is_local=False, cost_usd=0.04),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0)
        graph = [
            {
                "id": "cell-mixed",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["local-v", "cloud-v"],
                "focus": "mixed compute test",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        task = org.tasks["cell-mixed"]

        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(task.local_calls, 1)
        self.assertEqual(task.api_calls, 1)
        self.assertAlmostEqual(task.cost_usd, 0.04, places=5)

    def test_verification_cell_bypasses_and_does_not_populate_reuse_cache(self) -> None:
        reuse = DuplicateResultReuse()
        reuse.register(
            AgentTaskRecord(id="seed", organization_id="o", project_id="p", agent_id="a", focus="verify focus").focus_digest,
            "cached previous verification",
        )
        adapters = {
            "v1": FakeVerifierAdapter(verdict="pass"),
            "v2": FakeVerifierAdapter(verdict="pass"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.0, reuse=reuse)
        graph = [
            {
                "id": "cell-fresh",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "verify focus",
            }
        ]
        org = scheduler.schedule(graph, organization_id="org-vc", project_id="p", goal="g")
        self.assertEqual(org.tasks["cell-fresh"].status, AgentTaskStatus.QUEUED)
        org = asyncio.run(scheduler.run(org))
        self.assertEqual(org.tasks["cell-fresh"].status, AgentTaskStatus.COMPLETED)

        clean_reuse = DuplicateResultReuse()
        scheduler2 = NanoScheduler(adapters, budget_usd=1.0, reuse=clean_reuse)
        org2 = scheduler2.schedule(graph, organization_id="org-vc2", project_id="p", goal="g")
        asyncio.run(scheduler2.run(org2))
        asyncio.run(scheduler2.retire(org2))
        self.assertIsNone(clean_reuse.lookup(org2.tasks["cell-fresh"].focus_digest))


class VerificationCellIntegrationFixtureTests(unittest.TestCase):
    """Full scheduler DAG integration fixtures with Verification Cell gating."""

    def test_integration_fixture_with_verified_outcome_allows_downstream_task(self) -> None:
        adapters = {
            "worker": FakeAdapter(delay=0.01),
            "v1": FakeVerifierAdapter(verdict="pass", summary="sound math verified"),
            "v2": FakeVerifierAdapter(verdict="pass", summary="code logic verified"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=5.0)
        graph = [
            {
                "id": "prep",
                "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                "focus": "gather mathematical proof and implementation",
            },
            {
                "id": "verify-cell",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "verify mathematical proof correctness",
                "depends_on": ["prep"],
            },
            {
                "id": "finalize-publish",
                "agent_id": "worker",
                "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                "focus": "publish verified proof to theorem bank",
                "depends_on": ["verify-cell"],
                "requires_verified": True,
            },
        ]
        org = scheduler.schedule(graph, organization_id="org-integ", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["prep"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["verify-cell"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["verify-cell"].verification_result["outcome"], "verified")
        self.assertEqual(org.tasks["finalize-publish"].status, AgentTaskStatus.COMPLETED)

        asyncio.run(scheduler.retire(org))
        self.assertTrue(org.retired)

    def test_integration_fixture_with_disputed_outcome_blocks_downstream_verified_gate(self) -> None:
        adapters = {
            "worker": FakeAdapter(delay=0.01),
            "v1": FakeVerifierAdapter(verdict="pass", summary="v1 approved"),
            "v2": FakeVerifierAdapter(verdict="fail", summary="v2 found fatal counterexample"),
        }
        scheduler = NanoScheduler(adapters, budget_usd=5.0)
        graph = [
            {
                "id": "prep",
                "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                "focus": "gather candidate proof",
            },
            {
                "id": "verify-cell",
                "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ["v1", "v2"],
                "focus": "verify candidate proof",
                "depends_on": ["prep"],
            },
            {
                "id": "finalize-publish",
                "agent_id": "worker",
                "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                "focus": "publish proof to official theorem bank",
                "depends_on": ["verify-cell"],
                "requires_verified": True,
            },
        ]
        org = scheduler.schedule(graph, organization_id="org-integ-dispute", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["prep"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["verify-cell"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["verify-cell"].verification_result["outcome"], "disputed")
        # Downstream task MUST be CANCELLED due to verification gate failure!
        self.assertEqual(org.tasks["finalize-publish"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("upstream verification gate failed", str(org.tasks["finalize-publish"].error))


class HardBudgetReservationUnitTests(unittest.TestCase):
    """Audited hard-budget reservation invariant and concurrency tests."""

    def test_two_concurrent_point_six_tasks_with_one_dollar_budget_only_one_dispatches(self) -> None:
        adapters = {
            "w1": FakeAdapter(cost_usd=0.60),
            "w2": FakeAdapter(cost_usd=0.60),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.00, max_concurrent=2)
        graph = [
            {"id": "t1", "agent_id": "w1", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "f1"},
            {"id": "t2", "agent_id": "w2", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "f2"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-1", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        completed = [t for t in org.tasks.values() if t.status == AgentTaskStatus.COMPLETED]
        cancelled = [t for t in org.tasks.values() if t.status == AgentTaskStatus.CANCELLED]
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(cancelled), 1)
        self.assertIn("budget exhausted", str(cancelled[0].error))
        self.assertAlmostEqual(org.total_cost_usd(), 0.60, places=5)
        self.assertLessEqual(org.total_cost_usd(), 1.00)

    def test_two_point_four_tasks_both_reserve_and_execute(self) -> None:
        adapters = {
            "w1": FakeAdapter(cost_usd=0.40),
            "w2": FakeAdapter(cost_usd=0.40),
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.00, max_concurrent=2)
        graph = [
            {"id": "t1", "agent_id": "w1", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "f1"},
            {"id": "t2", "agent_id": "w2", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "f2"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-2", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t1"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["t2"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.total_cost_usd(), 0.80, places=5)

    def test_active_reservation_reduces_available_budget_immediately(self) -> None:
        started_event = asyncio.Event()
        release_event = asyncio.Event()

        class BarrierAdapter:
            max_cost_usd: float = 0.60
            async def generate(self, request):
                started_event.set()
                await release_event.wait()
                return FakeResponse("done", cost_usd=0.60)

        scheduler = NanoScheduler({"barrier": BarrierAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-bar", "agent_id": "barrier", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "wait"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-bar", project_id="p", goal="g")

        async def run_and_inspect():
            run_task = asyncio.create_task(scheduler.run(org))
            await asyncio.wait_for(started_event.wait(), timeout=5.0)
            # Assert active reservation state while running
            self.assertAlmostEqual(org.reserved_cost_usd(), 0.60, places=5)
            self.assertAlmostEqual(org.available_budget_usd(), 0.40, places=5)
            self.assertAlmostEqual(org.total_cost_usd(), 0.0, places=5)
            release_event.set()
            await run_task

        asyncio.run(run_and_inspect())
        self.assertAlmostEqual(org.reserved_cost_usd(), 0.0, places=5)
        self.assertAlmostEqual(org.total_cost_usd(), 0.60, places=5)
        self.assertAlmostEqual(org.available_budget_usd(), 0.40, places=5)

    def test_actual_cost_below_reservation_releases_unused_amount(self) -> None:
        class VariableCostAdapter:
            max_cost_usd: float = 0.50
            async def generate(self, request):
                # Returns lower actual cost than conservative bound
                return FakeResponse("done", cost_usd=0.30)

        scheduler = NanoScheduler({"var": VariableCostAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-var", "agent_id": "var", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "variable"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-var", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-var"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.tasks["t-var"].cost_usd, 0.30, places=5)
        self.assertAlmostEqual(org.total_cost_usd(), 0.30, places=5)
        self.assertAlmostEqual(org.reserved_cost_usd(), 0.0, places=5)
        # Unused 0.20 is released back to available
        self.assertAlmostEqual(org.available_budget_usd(), 0.70, places=5)

    def test_failed_task_preserves_actual_incurred_cost(self) -> None:
        class FailingCostlyAdapter:
            max_cost_usd: float = 0.25
            async def generate(self, request):
                raise RuntimeError("call failed after billable request")

        scheduler = NanoScheduler({"fail_cost": FailingCostlyAdapter()}, budget_usd=1.00, max_attempts=1)
        graph = [
            {"id": "t-fail", "agent_id": "fail_cost", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "fail"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-fail", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-fail"].status, AgentTaskStatus.FAILED)
        self.assertAlmostEqual(org.reserved_cost_usd(), 0.0, places=5)

    def test_paid_retry_requires_new_reservation(self) -> None:
        adapter = FakeAdapter(fail_first=1, cost_usd=0.20)
        scheduler = NanoScheduler({"retrier": adapter}, budget_usd=1.00, max_attempts=2)
        graph = [
            {"id": "t-retry", "agent_id": "retrier", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "retry"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-retry", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-retry"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["t-retry"].attempts, 2)
        # Two reservations created and settled
        self.assertEqual(len(org.reservations), 2)
        self.assertAlmostEqual(org.reserved_cost_usd(), 0.0, places=5)

    def test_insufficient_retry_budget_prevents_second_call(self) -> None:
        class BillableError(RuntimeError):
            def __init__(self, msg: str, cost_usd: float = 0.20):
                super().__init__(msg)
                self.cost_usd = cost_usd

        class FailingBillableAdapter:
            max_cost_usd: float = 0.20
            def __init__(self) -> None:
                self.calls = 0
            async def generate(self, request):
                self.calls += 1
                if self.calls == 1:
                    raise BillableError("provider failed after tokens generated", cost_usd=0.20)
                return FakeResponse("success", cost_usd=0.20)

        adapter = FailingBillableAdapter()
        # Budget of 0.30 allows attempt 1 (0.20), but remaining 0.10 is insufficient for attempt 2 (0.20)
        scheduler = NanoScheduler({"retrier": adapter}, budget_usd=0.30, max_attempts=2)
        graph = [
            {"id": "t-tight", "agent_id": "retrier", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "tight"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-tight", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-tight"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget exhausted", str(org.tasks["t-tight"].error))
        self.assertEqual(adapter.calls, 1)
        self.assertAlmostEqual(org.total_cost_usd(), 0.20, places=5)

    def test_zero_cost_deterministic_and_local_tasks_require_no_monetary_reservation(self) -> None:
        class ZeroCostLocalAdapter:
            is_local: bool = True
            async def generate(self, request):
                return FakeResponse("local free", cost_usd=0.0)

        scheduler = NanoScheduler({"free_local": ZeroCostLocalAdapter()}, budget_usd=0.0)
        graph = [
            {"id": "t-det", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "focus": "det"},
            {"id": "t-loc", "agent_id": "free_local", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "loc"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-free", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-det"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["t-loc"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.total_cost_usd(), 0.0, places=5)

    def test_unknown_paid_cost_bound_fails_closed_in_hard_mode(self) -> None:
        class UnboundedPaidAdapter:
            is_local: bool = False
            # No cost_usd, max_cost_usd, or estimate_cost defined!
            async def generate(self, request):
                return FakeResponse("cloud answer", cost_usd=0.10)

        scheduler = NanoScheduler({"unbounded": UnboundedPaidAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-unbounded", "agent_id": "unbounded", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "cloud"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-unb", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-unbounded"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("cannot establish conservative cost bound", str(org.tasks["t-unbounded"].error))

    def test_verification_cell_reserves_aggregate_participant_bound_before_dispatch(self) -> None:
        v1 = FakeVerifierAdapter(verdict="pass", cost_usd=0.30)
        v2 = FakeVerifierAdapter(verdict="pass", cost_usd=0.30)
        scheduler = NanoScheduler({"v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [
            {"id": "cell", "executor_kind": ExecutorKind.VERIFICATION_CELL, "verifier_ids": ["v1", "v2"], "focus": "audit"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-cell", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["cell"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.total_cost_usd(), 0.60, places=5)

    def test_insufficient_full_cell_reservation_launches_zero_verifiers(self) -> None:
        v1 = FakeVerifierAdapter(verdict="pass", cost_usd=0.30)
        v2 = FakeVerifierAdapter(verdict="pass", cost_usd=0.30)
        # Budget of 0.50 cannot cover cell bound of 0.60
        scheduler = NanoScheduler({"v1": v1, "v2": v2}, budget_usd=0.50)
        graph = [
            {"id": "cell-denied", "executor_kind": ExecutorKind.VERIFICATION_CELL, "verifier_ids": ["v1", "v2"], "focus": "audit"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-cell-denied", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["cell-denied"].status, AgentTaskStatus.CANCELLED)
        self.assertEqual(len(v1.received_requests), 0)
        self.assertEqual(len(v2.received_requests), 0)

    def test_mixed_local_and_api_verification_cell_reserves_only_paid_bound(self) -> None:
        v_local = FakeVerifierAdapter(verdict="pass", is_local=True, cost_usd=0.0)
        v_cloud = FakeVerifierAdapter(verdict="pass", is_local=False, cost_usd=0.25)
        scheduler = NanoScheduler({"v_loc": v_local, "v_cld": v_cloud}, budget_usd=0.30)
        graph = [
            {"id": "cell-mix", "executor_kind": ExecutorKind.VERIFICATION_CELL, "verifier_ids": ["v_loc", "v_cld"], "focus": "audit"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-mix", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["cell-mix"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.total_cost_usd(), 0.25, places=5)
        self.assertEqual(len(v_local.received_requests), 1)
        self.assertEqual(len(v_cloud.received_requests), 1)

    def test_duplicate_reused_task_does_not_consume_reservation(self) -> None:
        reuse = DuplicateResultReuse()
        adapters = {"w": FakeAdapter(cost_usd=0.50)}
        seed = AgentTaskRecord(id="seed", organization_id="o", project_id="p",
                               agent_id="w",
                               executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                               focus="dup")
        reuse.register(seed.execution_identity("v2", adapters=adapters),
                       "cached answer", version="v2")
        scheduler = NanoScheduler(adapters, budget_usd=1.00, reuse=reuse)
        graph = [
            {"id": "t-dup", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "dup"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-dup", project_id="p", goal="g")
        self.assertEqual(org.tasks["t-dup"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(len(org.reservations), 0)
        org = asyncio.run(scheduler.run(org))
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_dependencies_do_not_proceed_after_budget_denied_parent(self) -> None:
        adapters = {
            "w1": FakeAdapter(cost_usd=0.80),
            "w2": FakeAdapter(cost_usd=0.10),
        }
        scheduler = NanoScheduler(adapters, budget_usd=0.50)
        graph = [
            {"id": "parent", "agent_id": "w1", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "parent"},
            {"id": "child", "agent_id": "w2", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "child", "depends_on": ["parent"]},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-dep", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["parent"].status, AgentTaskStatus.CANCELLED)
        self.assertEqual(org.tasks["child"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("dependency", str(org.tasks["child"].error))

    def test_special_invariant_incurred_plus_reserved_never_exceeds_budget_throughout_execution(self) -> None:
        # 10 tasks with diverse costs and delays under concurrency 4
        adapters = {
            f"w{i}": FakeAdapter(delay=0.01, cost_usd=0.10 * (i + 1))
            for i in range(10)
        }
        scheduler = NanoScheduler(adapters, budget_usd=1.00, max_concurrent=4)
        graph = [
            {"id": f"t{i}", "agent_id": f"w{i}", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": f"focus-{i}"}
            for i in range(10)
        ]
        org = scheduler.schedule(graph, organization_id="org-res-inv", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        # Assert final invariant
        self.assertLessEqual(org.total_cost_usd(), 1.00 + 1e-9)
        self.assertAlmostEqual(org.reserved_cost_usd(), 0.0, places=5)
        # All completed tasks must not have exceeded 1.00
        completed_cost = sum(t.cost_usd for t in org.tasks.values() if t.status == AgentTaskStatus.COMPLETED)
        self.assertLessEqual(completed_cost, 1.00 + 1e-9)


    def test_estimate_cost_callable_only_is_rejected_in_hard_mode(self) -> None:
        class EstimateOnlyCallableAdapter:
            is_local: bool = False
            def estimate_cost(self) -> float:
                return 0.25
            async def generate(self, request):
                return FakeResponse("result", cost_usd=0.25)

        scheduler = NanoScheduler({"est_call": EstimateOnlyCallableAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-est-call", "agent_id": "est_call", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "est"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-est-c", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-est-call"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("provides only an estimate", str(org.tasks["t-est-call"].error))

    def test_estimated_cost_usd_attribute_only_is_rejected_in_hard_mode(self) -> None:
        class EstimatedCostAttrAdapter:
            is_local: bool = False
            estimated_cost_usd: float = 0.25
            async def generate(self, request):
                return FakeResponse("result", cost_usd=0.25)

        scheduler = NanoScheduler({"est_attr": EstimatedCostAttrAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-est-attr", "agent_id": "est_attr", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "est"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-est-a", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-est-attr"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("provides only an estimate", str(org.tasks["t-est-attr"].error))

    def test_generic_cost_usd_without_conservative_contract_is_rejected(self) -> None:
        class GenericCostAdapter:
            is_local: bool = False
            cost_usd: float = 0.25
            # is_conservative_bound is NOT True
            async def generate(self, request):
                return FakeResponse("result", cost_usd=0.25)

        scheduler = NanoScheduler({"generic_cost": GenericCostAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-gen-cost", "agent_id": "generic_cost", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "gen"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-gen", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-gen-cost"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("provides only an estimate", str(org.tasks["t-gen-cost"].error))

    def test_task_max_cost_usd_without_enforceable_executor_cap_fails_closed(self) -> None:
        class UnboundedPaidAdapter:
            is_local: bool = False
            enforces_max_cost: bool = False
            async def generate(self, request):
                return FakeResponse("cloud", cost_usd=0.50)

        scheduler = NanoScheduler({"unbounded": UnboundedPaidAdapter()}, budget_usd=1.00)
        graph = [
            {
                "id": "t-task-max",
                "agent_id": "unbounded",
                "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                "focus": "cloud with unenforced task cap",
                "max_cost_usd": 0.20,
            },
        ]
        org = scheduler.schedule(graph, organization_id="org-res-uncapped", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t-task-max"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("policy metadata without an enforceable executor cap", str(org.tasks["t-task-max"].error))

    def test_verification_cell_with_one_estimate_only_verifier_rejects_entire_cell_with_zero_calls(self) -> None:
        v1 = FakeVerifierAdapter(verdict="pass", cost_usd=0.20, is_conservative_bound=True)
        class EstimateOnlyVerifier:
            is_local: bool = False
            estimated_cost_usd: float = 0.20
            calls: int = 0
            async def generate(self, request):
                self.calls += 1
                return FakeResponse('{"verdict":"pass","summary":"ok"}', cost_usd=0.20)

        v2 = EstimateOnlyVerifier()
        scheduler = NanoScheduler({"v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [
            {"id": "cell-est", "executor_kind": ExecutorKind.VERIFICATION_CELL, "verifier_ids": ["v1", "v2"], "focus": "audit"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-cell-est", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        # Entire cell is rejected before dispatch
        self.assertEqual(org.tasks["cell-est"].status, AgentTaskStatus.CANCELLED)
        self.assertEqual(len(v1.received_requests), 0)
        self.assertEqual(v2.calls, 0)

    def test_actual_exceeding_declared_bound_records_integrity_violation(self) -> None:
        class RogueOverchargingAdapter:
            is_local: bool = False
            max_cost_usd: float = 0.20  # declared bound
            async def generate(self, request):
                # charges more than declared bound
                return FakeResponse("done", cost_usd=0.35)

        scheduler = NanoScheduler({"rogue": RogueOverchargingAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "t-rogue", "agent_id": "rogue", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "rogue"},
        ]
        org = scheduler.schedule(graph, organization_id="org-res-rogue", project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["t-rogue"]
        self.assertAlmostEqual(task.cost_usd, 0.35, places=5)
        self.assertAlmostEqual(org.total_cost_usd(), 0.35, places=5)
        res = list(org.reservations.values())[0]
        self.assertTrue(res.integrity_violation)
        self.assertIn("budget bound integrity violation", str(task.error))


class ManagerProposalUnitTests(unittest.TestCase):
    """Manager child-task proposal generation, validation, and provenance tests."""

    def test_squad_manager_executes_and_produces_structured_proposals(self) -> None:
        adapter = DeterministicManagerAdapter()
        # With max_logical_agents=1, Governor evaluates proposal but denies dynamic insertion beyond cap,
        # allowing us to test proposal record structure in isolation
        scheduler = NanoScheduler({"squad_mgr": adapter}, budget_usd=1.00, envelope=ResourceEnvelope(max_logical_agents=1, max_concurrent_agents=1))
        graph = [
            {
                "id": "squad-1",
                "agent_id": "squad_mgr",
                "executor_kind": ExecutorKind.MANAGER,
                "focus": "decompose database migration plan",
            },
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-squad", project_id="p", goal="migrate db")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["squad-1"]
        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertIsNotNone(task.result_summary)
        self.assertEqual(len(task.proposed_children), 2)
        # Verify children are PROPOSED records only
        child1, child2 = task.proposed_children
        self.assertEqual(child1["status"], AgentTaskStatus.PROPOSED)
        self.assertEqual(child1["proposed_by_manager_id"], "squad-1")
        self.assertEqual(child1["manager_depth"], 1)
        self.assertEqual(child1["executor_kind"], ExecutorKind.DETERMINISTIC_TOOL)

        self.assertEqual(child2["status"], AgentTaskStatus.PROPOSED)
        self.assertEqual(child2["proposed_by_manager_id"], "squad-1")
        self.assertEqual(child2["manager_depth"], 1)
        self.assertEqual(child2["executor_kind"], ExecutorKind.NANO_LOCAL_MODEL)
        self.assertEqual(child2["depends_on"], ["c1"])

        # Invariant: ZERO children added to active execution graph when denied
        self.assertEqual(len(org.tasks), 1)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_lead_manager_executes_through_same_canonical_runtime(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"lead_mgr": adapter}, budget_usd=1.00, envelope=ResourceEnvelope(max_logical_agents=1, max_concurrent_agents=1))
        graph = [
            {
                "id": "lead-1",
                "agent_id": "lead_mgr",
                "executor_kind": ExecutorKind.MANAGER,
                "focus": "decompose top-level architecture",
            },
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-lead", project_id="p", goal="build arch")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["lead-1"]
        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(len(task.proposed_children), 2)
        self.assertEqual(task.proposed_children[0]["status"], AgentTaskStatus.PROPOSED)

    def test_manager_proposal_does_not_execute_children_and_status_remains_proposed(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"mgr": adapter}, budget_usd=1.00, envelope=ResourceEnvelope(max_logical_agents=1, max_concurrent_agents=1))
        graph = [
            {"id": "mgr-task", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-exec", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-task"]
        for child in task.proposed_children:
            self.assertEqual(child["status"], AgentTaskStatus.PROPOSED)
            self.assertNotIn(child["id"], org.tasks)

    def test_malformed_manager_output_fails_closed_with_zero_children(self) -> None:
        class MalformedManagerAdapter:
            is_local = True
            is_conservative_bound = True
            async def generate(self, request):
                return FakeResponse("{broken json...")

        scheduler = NanoScheduler({"malformed_mgr": MalformedManagerAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "mgr-malformed", "agent_id": "malformed_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-malformed", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-malformed"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("manager proposal validation failed", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_prose_non_json_output_produces_zero_children(self) -> None:
        class ProseManagerAdapter:
            is_local = True
            is_conservative_bound = True
            async def generate(self, request):
                return FakeResponse("Sure, I think we should create a child task for data collection and one for modeling.")

        scheduler = NanoScheduler({"prose_mgr": ProseManagerAdapter()}, budget_usd=1.00)
        graph = [
            {"id": "mgr-prose", "agent_id": "prose_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-prose", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-prose"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("non-structured or prose output cannot be parsed", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_unknown_executor_kind_rejected(self) -> None:
        payload = {
            "version": 1,
            "reason": "bad executor",
            "children": [
                {
                    "focus": "unsafe payload execution",
                    "executor_kind": "arbitrary_os_script",
                    "depends_on": [],
                }
            ],
        }
        adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"bad_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "mgr-bad-ex", "agent_id": "bad_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-bad-ex", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-bad-ex"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("unknown or unpermitted executor_kind", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_child_count_limit_enforced(self) -> None:
        payload = {
            "version": 1,
            "reason": "too many children",
            "children": [
                {"id": f"c{i}", "focus": f"child task {i}", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL}
                for i in range(12)  # Limit is 8
            ],
        }
        adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"overflow_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "mgr-overflow", "agent_id": "overflow_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-overflow", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-overflow"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("exceeds manager limit", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_manager_depth_limit_enforced(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"depth_mgr": adapter}, budget_usd=1.00)
        graph = [
            {
                "id": "mgr-deep",
                "agent_id": "depth_mgr",
                "executor_kind": ExecutorKind.MANAGER,
                "focus": "plan at depth 2",
            },
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-deep", project_id="p", goal="plan")
        org.tasks["mgr-deep"].manager_depth = 2  # At max depth limit
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-deep"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("exceeds max_manager_depth limit", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_self_dependency_rejected(self) -> None:
        payload = {
            "version": 1,
            "reason": "self loop",
            "children": [
                {
                    "id": "c1",
                    "focus": "self loop task",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": ["c1"],
                }
            ],
        }
        adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"loop_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "mgr-loop", "agent_id": "loop_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-loop", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-loop"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("self-dependency", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_unknown_dependency_rejected(self) -> None:
        payload = {
            "version": 1,
            "reason": "unknown dep",
            "children": [
                {
                    "id": "c1",
                    "focus": "dep task",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": ["ghost_task_xyz"],
                }
            ],
        }
        adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"unk_dep_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "mgr-unk-dep", "agent_id": "unk_dep_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-unk-dep", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-unk-dep"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("unknown or future dependency", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_duplicate_child_proposal_handled_deterministically(self) -> None:
        payload = {
            "version": 1,
            "reason": "duplicate tasks",
            "children": [
                {
                    "id": "c1",
                    "focus": "identical focus",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": [],
                },
                {
                    "id": "c2",
                    "focus": "identical focus",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": [],
                },
            ],
        }
        adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"dup_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "mgr-dup", "agent_id": "dup_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-dup", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-dup"]
        self.assertEqual(task.status, AgentTaskStatus.FAILED)
        self.assertIn("duplicate proposal", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)

    def test_proposal_provenance_records_manager_identity_and_depth(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"prov_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "prov-manager", "agent_id": "prov_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-prov", project_id="p", goal="plan")
        org.tasks["prov-manager"].manager_depth = 0
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["prov-manager"]
        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        for child in task.proposed_children:
            self.assertEqual(child["proposed_by_manager_id"], "prov-manager")
            self.assertEqual(child["manager_depth"], 1)
            self.assertEqual(child["proposal_version"], 1)
            self.assertIsNotNone(child["proposal_reason"])

    def test_proposed_verification_cell_requires_explicit_verifier_ids(self) -> None:
        # 1. Invalid verification cell (no verifiers)
        bad_payload = {
            "version": 1,
            "reason": "bad vcell",
            "children": [
                {
                    "id": "c_vcell_bad",
                    "focus": "verify something",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": [],
                }
            ],
        }
        scheduler = NanoScheduler({"bad_vcell_mgr": DeterministicManagerAdapter(proposal_payload=bad_payload)}, budget_usd=1.00)
        graph = [{"id": "mgr-bv", "agent_id": "bad_vcell_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-bv", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))
        self.assertEqual(org.tasks["mgr-bv"].status, AgentTaskStatus.FAILED)
        self.assertIn("requires at least 2 explicit verifier_ids", str(org.tasks["mgr-bv"].error))

        # 2. Valid verification cell (explicit verifiers)
        good_payload = {
            "version": 1,
            "reason": "good vcell",
            "children": [
                {
                    "id": "c_vcell_good",
                    "focus": "verify something",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                }
            ],
        }
        scheduler2 = NanoScheduler({"good_vcell_mgr": DeterministicManagerAdapter(proposal_payload=good_payload)}, budget_usd=1.00)
        graph2 = [{"id": "mgr-gv", "agent_id": "good_vcell_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org2 = scheduler2.schedule(graph2, organization_id="org-gv", project_id="p", goal="plan")
        org2 = asyncio.run(scheduler2.run(org2))
        self.assertEqual(org2.tasks["mgr-gv"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org2.tasks["mgr-gv"].proposed_children[0]["verifier_ids"], ["v1", "v2"])

    def test_proposal_creation_consumes_no_child_execution_cost(self) -> None:
        adapter = DeterministicManagerAdapter(cost_usd=0.05)
        scheduler = NanoScheduler({"paid_mgr": adapter}, budget_usd=1.00)
        graph = [
            {"id": "mgr-paid", "agent_id": "paid_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-paid-prop", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-paid"]
        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(task.cost_usd, 0.05, places=5)
        self.assertAlmostEqual(org.total_cost_usd(), 0.05, places=5)
        self.assertEqual(len(task.proposed_children), 2)

    def test_paid_manager_uses_hard_budget_admission(self) -> None:
        adapter = FakeAdapter(cost_usd=0.50, is_conservative_bound=True)
        # Budget of 0.30 cannot cover Manager bound of 0.50
        scheduler = NanoScheduler({"paid_mgr": adapter}, budget_usd=0.30)
        graph = [
            {"id": "mgr-denied", "agent_id": "paid_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-budget-denied", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-denied"]
        self.assertEqual(task.status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget exhausted", str(task.error))
        self.assertEqual(len(task.proposed_children), 0)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_deterministic_manager_fixture_makes_zero_provider_calls(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"det_mgr": adapter}, budget_usd=0.0)
        graph = [
            {"id": "mgr-det", "agent_id": "det_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-mgr-det-calls", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task = org.tasks["mgr-det"]
        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(task.api_calls, 0)
        self.assertEqual(task.local_calls, 1)
        self.assertEqual(org.total_cost_usd(), 0.0)


class GovernorAuthorizationDynamicSpawningUnitTests(unittest.TestCase):
    """Governor authorization, ResourceEnvelope limits, dynamic task insertion, and execution tests."""

    def test_valid_manager_proposal_evaluated_by_governor(self) -> None:
        adapter = DeterministicManagerAdapter()
        worker = FakeAdapter(is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": adapter, "analyzer_worker": worker}, budget_usd=1.00)
        graph = [
            {"id": "mgr-task", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-gov-eval", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        mgr_task = org.tasks["mgr-task"]
        self.assertEqual(mgr_task.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(len(mgr_task.proposed_children), 2)
        for child in mgr_task.proposed_children:
            self.assertIn("authorization_record", child)
            auth_rec = child["authorization_record"]
            self.assertTrue(auth_rec["authorized"])
            self.assertEqual(auth_rec["governor_rule"], "envelope_and_safety_passed")

    def test_valid_child_transitions_proposed_to_authorized(self) -> None:
        adapter = DeterministicManagerAdapter()
        worker = FakeAdapter(is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": adapter, "analyzer_worker": worker}, budget_usd=1.00)
        graph = [
            {"id": "mgr-task", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]
        org = scheduler.schedule(graph, organization_id="org-gov-trans", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        # Children were authorized, inserted, and completed
        self.assertIn("c1", org.tasks)
        self.assertIn("c2", org.tasks)
        self.assertEqual(org.tasks["c1"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["c2"].status, AgentTaskStatus.COMPLETED)

    def test_manager_cannot_authorize_its_own_child_directly(self) -> None:
        # A task in PROPOSED cannot jump directly to QUEUED, STARTING, or RUNNING without AUTHORIZED
        child = AgentTaskRecord(
            id="c-direct",
            organization_id="org",
            project_id="p",
            agent_id="worker",
            focus="direct execution",
            status=AgentTaskStatus.PROPOSED,
        )
        with self.assertRaises(ValueError):
            child.transition(AgentTaskStatus.QUEUED)
        with self.assertRaises(ValueError):
            child.transition(AgentTaskStatus.RUNNING)
        with self.assertRaises(ValueError):
            child.transition(AgentTaskStatus.COMPLETED)

    def test_authorized_deterministic_child_actually_executes(self) -> None:
        payload = {
            "version": 1,
            "reason": "run tool",
            "children": [
                {
                    "id": "det-child",
                    "focus": "generate report format",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "agent_id": "tool",
                }
            ],
        }
        adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"mgr": adapter}, budget_usd=1.00)
        graph = [{"id": "mgr-det-exec", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-det-exec", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("det-child", org.tasks)
        child = org.tasks["det-child"]
        self.assertEqual(child.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(child.result_summary, "deterministic:generate report format")
        self.assertEqual(child.cost_usd, 0.0)

    def test_authorized_nano_child_actually_executes(self) -> None:
        payload = {
            "version": 1,
            "reason": "run nano",
            "children": [
                {
                    "id": "nano-child",
                    "focus": "analyze dataset sample",
                    "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                    "agent_id": "nano_worker",
                }
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        worker_adapter = FakeAdapter(cost_usd=0.01, is_local=True, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "nano_worker": worker_adapter}, budget_usd=1.00)
        graph = [{"id": "mgr-nano-exec", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-nano-exec", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("nano-child", org.tasks)
        child = org.tasks["nano-child"]
        self.assertEqual(child.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(child.local_calls, 1)
        self.assertEqual(child.cost_usd, 0.01)

    def test_authorized_verification_cell_executes_through_canonical_cell_runtime(self) -> None:
        payload = {
            "version": 1,
            "reason": "verify output",
            "children": [
                {
                    "id": "vcell-child",
                    "focus": "verify correctness",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                }
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        v1 = FakeVerifierAdapter(verdict="pass", summary="v1 approved", cost_usd=0.02, is_conservative_bound=True)
        v2 = FakeVerifierAdapter(verdict="pass", summary="v2 approved", cost_usd=0.03, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [{"id": "mgr-vcell", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-vcell-dyn", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("vcell-child", org.tasks)
        child = org.tasks["vcell-child"]
        self.assertEqual(child.status, AgentTaskStatus.COMPLETED)
        self.assertIsNotNone(child.verification_result)
        self.assertEqual(child.verification_result["outcome"], "verified")
        self.assertAlmostEqual(child.cost_usd, 0.05, places=5)
        self.assertAlmostEqual(org.total_cost_usd(), 0.05, places=5)

    def test_denied_child_produces_zero_execution_calls(self) -> None:
        payload = {
            "version": 1,
            "reason": "bad child",
            "children": [
                {
                    "id": "bad-child",
                    "focus": "prohibited child",
                    "executor_kind": "unsupported_rogue_executor",
                }
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"mgr": mgr_adapter}, budget_usd=1.00)
        graph = [{"id": "mgr-bad", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-denied-zero", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["mgr-bad"].status, AgentTaskStatus.FAILED)
        self.assertNotIn("bad-child", org.tasks)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_logical_agent_cap_denies_excess_child(self) -> None:
        payload = {
            "version": 1,
            "reason": "propose two children",
            "children": [
                {"id": "c1", "focus": "child 1", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL},
                {"id": "c2", "focus": "child 2", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        # Cap is 2: 1 Manager task + 1 child authorized. Second child must be denied!
        scheduler = NanoScheduler({"mgr": mgr_adapter}, budget_usd=1.00, envelope=ResourceEnvelope(max_logical_agents=2, max_concurrent_agents=2))
        graph = [{"id": "mgr-cap", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-cap-denial", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("c1", org.tasks)
        self.assertNotIn("c2", org.tasks)
        self.assertEqual(len(org.tasks), 2)
        # c2 was denied
        c2_rec = org.tasks["mgr-cap"].proposed_children[1]
        self.assertFalse(c2_rec["authorization_record"]["authorized"])
        self.assertIn("exceeds envelope limit", c2_rec["authorization_record"]["reason"])

    def test_two_simultaneous_manager_proposals_cannot_race_past_logical_agent_cap(self) -> None:
        async def _run_race():
            ready_barrier = asyncio.Event()
            release_barrier = asyncio.Event()
            entered_count = 0

            class BarrierManagerAdapter:
                is_local = True
                is_conservative_bound = True
                def __init__(self, child_id: str):
                    self.child_id = child_id
                async def generate(self, request):
                    nonlocal entered_count
                    entered_count += 1
                    if entered_count >= 2:
                        ready_barrier.set()
                    await asyncio.wait_for(release_barrier.wait(), timeout=5.0)
                    return FakeResponse(json.dumps({
                        "version": 1,
                        "reason": "barrier proposal",
                        "children": [{"id": self.child_id, "focus": f"child {self.child_id}", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL}]
                    }))

            mgr1 = BarrierManagerAdapter("c1")
            mgr2 = BarrierManagerAdapter("c2")
            scheduler = NanoScheduler(
                {"mgr1": mgr1, "mgr2": mgr2},
                budget_usd=1.00,
                envelope=ResourceEnvelope(max_logical_agents=3, max_concurrent_agents=3),
            )
            graph = [
                {"id": "m1", "agent_id": "mgr1", "executor_kind": ExecutorKind.MANAGER, "focus": "m1"},
                {"id": "m2", "agent_id": "mgr2", "executor_kind": ExecutorKind.MANAGER, "focus": "m2"},
            ]
            org = scheduler.schedule(graph, organization_id="org-race-cap", project_id="p", goal="plan")
            run_task = asyncio.create_task(scheduler.run(org))
            await asyncio.wait_for(ready_barrier.wait(), timeout=5.0)
            release_barrier.set()
            return await run_task

        org = asyncio.run(_run_race())

        # Total tasks must be strictly <= 3
        self.assertEqual(len(org.tasks), 3)
        # Exactly one child made it into org.tasks
        has_c1 = "c1" in org.tasks
        has_c2 = "c2" in org.tasks
        self.assertTrue((has_c1 and not has_c2) or (has_c2 and not has_c1))

    def test_manager_depth_limit_denies_nested_manager(self) -> None:
        parent = AgentTaskRecord(id="m-root", organization_id="org", project_id="p", agent_id="mgr", focus="root", status=AgentTaskStatus.COMPLETED, manager_depth=1)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-root"] = parent

        # Child manager task at depth 2 with envelope max_manager_depth = 1 -> DENIED
        child = AgentTaskRecord(
            id="c-nested-mgr",
            organization_id="org",
            project_id="p",
            agent_id="mgr",
            focus="nested manager",
            executor_kind=ExecutorKind.MANAGER,
            manager_depth=2,
        )
        auth = AgentGovernor.authorize_child_proposal(
            child,
            parent_task=parent,
            org=org,
            envelope=ResourceEnvelope(max_manager_depth=1),
            settings=AgentFabricSettings(recursive_spawning=True),
        )
        self.assertFalse(auth["authorized"])
        self.assertIn("exceeds envelope limit", auth["reason"])

    def test_recursive_spawning_false_denies_manager_child(self) -> None:
        payload = {
            "version": 1,
            "reason": "nested manager",
            "children": [
                {
                    "id": "c-mgr-child",
                    "focus": "nested manager task",
                    "executor_kind": ExecutorKind.MANAGER,
                    "agent_id": "mgr",
                }
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        settings = AgentFabricSettings(recursive_spawning=False)
        scheduler = NanoScheduler({"mgr": mgr_adapter}, budget_usd=1.00, settings=settings)
        graph = [{"id": "m-root", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "root"}]
        org = scheduler.schedule(graph, organization_id="org-rec-denial", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertNotIn("c-mgr-child", org.tasks)
        c_rec = org.tasks["m-root"].proposed_children[0]
        self.assertFalse(c_rec["authorization_record"]["authorized"])
        self.assertIn("recursive manager spawning is disabled", c_rec["authorization_record"]["reason"])

    def test_valid_nested_manager_allowed_when_recursive_spawning_enabled_and_depth_permits(self) -> None:
        payload_root = {
            "version": 1,
            "reason": "spawn squad manager",
            "children": [
                {
                    "id": "squad-mgr",
                    "focus": "squad level decomposition",
                    "executor_kind": ExecutorKind.MANAGER,
                    "agent_id": "squad_adapter",
                }
            ],
        }
        payload_squad = {
            "version": 1,
            "reason": "spawn leaf tool",
            "children": [
                {
                    "id": "leaf-tool",
                    "focus": "execute leaf tool",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "agent_id": "tool",
                }
            ],
        }
        root_adapter = DeterministicManagerAdapter(proposal_payload=payload_root)
        squad_adapter = DeterministicManagerAdapter(proposal_payload=payload_squad)
        settings = AgentFabricSettings(recursive_spawning=True, envelope=ResourceEnvelope(max_manager_depth=3))
        scheduler = NanoScheduler(
            {"root_mgr": root_adapter, "squad_adapter": squad_adapter},
            budget_usd=1.00,
            settings=settings,
            envelope=ResourceEnvelope(max_manager_depth=3),
        )
        graph = [{"id": "m-root", "agent_id": "root_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "root"}]
        org = scheduler.schedule(graph, organization_id="org-nested-success", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("squad-mgr", org.tasks)
        self.assertIn("leaf-tool", org.tasks)
        self.assertEqual(org.tasks["squad-mgr"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["leaf-tool"].status, AgentTaskStatus.COMPLETED)

    def test_unknown_dependency_denied(self) -> None:
        child = AgentTaskRecord(
            id="c-unk-dep",
            organization_id="org",
            project_id="p",
            agent_id="tool",
            focus="task",
            depends_on=("nonexistent_task_xyz",),
        )
        parent = AgentTaskRecord(id="p-task", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["p-task"] = parent

        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertIn("unknown or not authorized", auth["reason"])

    def test_cycle_producing_dynamic_insertion_denied(self) -> None:
        child = AgentTaskRecord(
            id="c-self-cycle",
            organization_id="org",
            project_id="p",
            agent_id="tool",
            focus="task",
            depends_on=("c-self-cycle",),
        )
        parent = AgentTaskRecord(id="p-task", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["p-task"] = parent

        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertIn("introduces self-cycle", auth["reason"])

    def test_sibling_dependency_preserved(self) -> None:
        execution_order = []
        class OrderTrackerTool:
            is_local = True
            is_conservative_bound = True
            def __init__(self, name: str):
                self.name = name
            async def generate(self, request):
                execution_order.append(self.name)
                return FakeResponse(f"{self.name} done")

        payload = {
            "version": 1,
            "reason": "ordered pipeline",
            "children": [
                {"id": "step1", "focus": "step 1", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "w1", "depends_on": []},
                {"id": "step2", "focus": "step 2", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "w2", "depends_on": ["step1"]},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler(
            {"mgr": mgr_adapter, "w1": OrderTrackerTool("w1"), "w2": OrderTrackerTool("w2")},
            budget_usd=1.00,
        )
        graph = [{"id": "m-order", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "order"}]
        org = scheduler.schedule(graph, organization_id="org-sib-order", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(execution_order, ["w1", "w2"])
        self.assertEqual(org.tasks["step1"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["step2"].status, AgentTaskStatus.COMPLETED)

    def test_denied_sibling_causes_dependent_sibling_to_be_denied(self) -> None:
        payload = {
            "version": 1,
            "reason": "chained sibling denial",
            "children": [
                {"id": "bad_sib", "focus": "bad", "executor_kind": "unsupported_kind", "depends_on": []},
                {"id": "dep_sib", "focus": "dep", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "depends_on": ["bad_sib"]},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"mgr": mgr_adapter}, budget_usd=1.00)
        graph = [{"id": "m-chain", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "chain"}]
        org = scheduler.schedule(graph, organization_id="org-chain-denial", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        # Manager failed validation because child 0 had unsupported executor
        self.assertEqual(org.tasks["m-chain"].status, AgentTaskStatus.FAILED)
        self.assertNotIn("dep_sib", org.tasks)

    def test_independent_sibling_can_still_execute_when_another_sibling_is_denied(self) -> None:
        # Construct proposal where c1 is denied by Governor (due to depth limit or custom envelope) but c2 is independent
        parent = AgentTaskRecord(id="m-parent", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-parent"] = parent

        # c1 is a manager child with depth 2 under max_manager_depth = 1 -> DENIED
        c1 = AgentTaskRecord(id="c1", organization_id="org", project_id="p", agent_id="mgr2", focus="c1", executor_kind=ExecutorKind.MANAGER, manager_depth=2)
        # c2 is an independent deterministic child -> AUTHORIZED
        c2 = AgentTaskRecord(id="c2", organization_id="org", project_id="p", agent_id="tool", focus="c2", executor_kind=ExecutorKind.DETERMINISTIC_TOOL)

        envelope = ResourceEnvelope(max_manager_depth=1)
        auth1 = AgentGovernor.authorize_child_proposal(c1, parent_task=parent, org=org, envelope=envelope)
        self.assertFalse(auth1["authorized"])

        auth2 = AgentGovernor.authorize_child_proposal(c2, parent_task=parent, org=org, envelope=envelope, denied_siblings={"c1"})
        self.assertTrue(auth2["authorized"])

    def test_organization_cancellation_blocks_subsequent_authorization(self) -> None:
        parent = AgentTaskRecord(id="m-parent", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.CANCELLED, error="organization cancelled")
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-parent"] = parent

        child = AgentTaskRecord(id="c1", organization_id="org", project_id="p", agent_id="tool", focus="c1", executor_kind=ExecutorKind.DETERMINISTIC_TOOL)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertIn("cancelled", auth["reason"])

    def test_dynamic_paid_nano_uses_hard_budget_reservation(self) -> None:
        payload = {
            "version": 1,
            "reason": "paid child",
            "children": [
                {"id": "c-paid", "focus": "compute embedding", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "paid_worker"}
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        paid_worker = FakeAdapter(cost_usd=0.04, is_local=False, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "paid_worker": paid_worker}, budget_usd=1.00)
        graph = [{"id": "m-paid", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "paid"}]
        org = scheduler.schedule(graph, organization_id="org-paid-res", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("c-paid", org.tasks)
        self.assertEqual(org.tasks["c-paid"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.tasks["c-paid"].cost_usd, 0.04, places=5)
        # Reservation was tracked and settled
        res_list = list(org.reservations.values())
        self.assertTrue(any(r.task_id == "c-paid" and r.status == "settled" for r in res_list))

    def test_insufficient_hard_budget_prevents_dynamic_dispatch(self) -> None:
        payload = {
            "version": 1,
            "reason": "expensive child",
            "children": [
                {"id": "c-exp", "focus": "expensive work", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "exp_worker"}
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        exp_worker = FakeAdapter(cost_usd=0.50, is_local=False, is_conservative_bound=True)
        # Budget is only 0.20, cannot cover exp_worker of 0.50
        scheduler = NanoScheduler({"mgr": mgr_adapter, "exp_worker": exp_worker}, budget_usd=0.20)
        graph = [{"id": "m-exp", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "exp"}]
        org = scheduler.schedule(graph, organization_id="org-budget-block", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("c-exp", org.tasks)
        # Child was authorized by Governor but denied budget reservation during NanoScheduler execution
        self.assertEqual(org.tasks["c-exp"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget exhausted", str(org.tasks["c-exp"].error))
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_dynamic_verification_cell_reserves_whole_paid_participant_bound(self) -> None:
        payload = {
            "version": 1,
            "reason": "paid vcell",
            "children": [
                {
                    "id": "c-vcell",
                    "focus": "verify paid",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                }
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        v1 = FakeVerifierAdapter(verdict="pass", summary="v1 ok", cost_usd=0.08, is_conservative_bound=True)
        v2 = FakeVerifierAdapter(verdict="pass", summary="v2 ok", cost_usd=0.07, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [{"id": "m-vcell", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "vcell"}]
        org = scheduler.schedule(graph, organization_id="org-vcell-whole-res", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("c-vcell", org.tasks)
        self.assertEqual(org.tasks["c-vcell"].status, AgentTaskStatus.COMPLETED)
        self.assertAlmostEqual(org.tasks["c-vcell"].cost_usd, 0.15, places=5)
        # Cell reservation settled total of 0.15
        vcell_res = [r for r in org.reservations.values() if r.task_id == "c-vcell"][0]
        self.assertAlmostEqual(vcell_res.settled_actual_usd, 0.15, places=5)

    def test_proposal_and_authorization_itself_costs_zero(self) -> None:
        payload = {
            "version": 1,
            "reason": "zero cost check",
            "children": [
                {"id": "c-det", "focus": "zero cost child", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL}
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload, cost_usd=0.0)
        scheduler = NanoScheduler({"mgr": mgr_adapter}, budget_usd=0.0)
        graph = [{"id": "m-zero", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "zero"}]
        org = scheduler.schedule(graph, organization_id="org-zero-cost", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["m-zero"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["c-det"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_duplicate_dynamic_work_follows_sanctioned_reuse_path(self) -> None:
        reuse = DuplicateResultReuse()
        payload = {
            "version": 1,
            "reason": "reuse check",
            "children": [
                {"id": "c-cached", "focus": "identical query", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "worker"}
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        worker_called = 0
        class WorkerTracker:
            is_local = True
            is_conservative_bound = True
            async def generate(self, request):
                nonlocal worker_called
                worker_called += 1
                return FakeResponse("fresh response")

        mgr_adapters = {"mgr": mgr_adapter, "worker": WorkerTracker()}
        # Seed cache with the v2 execution identity of the dynamic task
        child_rec = AgentTaskRecord(id="c-cached", organization_id="org", project_id="p", agent_id="worker",
                                    executor_kind=ExecutorKind.NANO_LOCAL_MODEL, focus="identical query")
        reuse.register(child_rec.execution_identity("v2", adapters=mgr_adapters),
                       "cached answer from earlier run", version="v2")

        scheduler = NanoScheduler(mgr_adapters, budget_usd=1.00, reuse=reuse)
        graph = [{"id": "m-reuse", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "reuse"}]
        org = scheduler.schedule(graph, organization_id="org-dyn-reuse", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-cached"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["c-cached"].result_summary, "cached answer from earlier run")
        self.assertEqual(org.tasks["c-cached"].reused_from,
                         child_rec.execution_identity("v2", adapters=mgr_adapters))
        # Worker was not called due to reuse!
        self.assertEqual(worker_called, 0)

    def test_verification_cell_dynamic_child_never_uses_reuse_cache(self) -> None:
        reuse = DuplicateResultReuse()
        # Seed reuse cache
        vcell_rec = AgentTaskRecord(id="c-vcell", organization_id="org", project_id="p", agent_id="vcell", focus="verify something")
        reuse.register(vcell_rec.focus_digest, "fake cached verification")

        payload = {
            "version": 1,
            "reason": "vcell reuse bypass",
            "children": [
                {
                    "id": "c-vcell",
                    "focus": "verify something",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                }
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        v1 = FakeVerifierAdapter(verdict="pass", summary="v1 pass", cost_usd=0.01, is_conservative_bound=True)
        v2 = FakeVerifierAdapter(verdict="pass", summary="v2 pass", cost_usd=0.01, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "v1": v1, "v2": v2}, budget_usd=1.00, reuse=reuse)
        graph = [{"id": "m-vcell", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "vcell"}]
        org = scheduler.schedule(graph, organization_id="org-vcell-no-reuse", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        # Both verifiers were called; cache was bypassed
        self.assertEqual(v1.calls, 1)
        self.assertEqual(v2.calls, 1)
        self.assertIsNone(org.tasks["c-vcell"].reused_from)

    def test_authorization_provenance_records_manager_and_governor_reason(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"mgr": adapter, "analyzer_worker": FakeAdapter(is_conservative_bound=True)}, budget_usd=1.00)
        graph = [{"id": "m-prov", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "prov"}]
        org = scheduler.schedule(graph, organization_id="org-prov-check", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        for child_dict in org.tasks["m-prov"].proposed_children:
            auth_rec = child_dict["authorization_record"]
            self.assertIsNotNone(auth_rec)
            self.assertEqual(auth_rec["manager_id"], "m-prov")
            self.assertIn("child_id", auth_rec)
            self.assertIn("governor_rule", auth_rec)
            self.assertIn("reason", auth_rec)
            self.assertIn("authorized_at", auth_rec)
            self.assertIn("envelope_snapshot", auth_rec)

    def test_dynamically_inserted_task_appears_exactly_once_in_org_tasks(self) -> None:
        adapter = DeterministicManagerAdapter()
        scheduler = NanoScheduler({"mgr": adapter, "analyzer_worker": FakeAdapter(is_conservative_bound=True)}, budget_usd=1.00)
        graph = [{"id": "m-once", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "once"}]
        org = scheduler.schedule(graph, organization_id="org-once-check", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        task_ids = list(org.tasks.keys())
        self.assertEqual(len(task_ids), len(set(task_ids)))
        self.assertIn("c1", org.tasks)
        self.assertIn("c2", org.tasks)

    def test_retry_count_does_not_create_extra_logical_task_records(self) -> None:
        attempts = 0
        class FlakyAdapter:
            is_local = True
            is_conservative_bound = True
            async def generate(self, request):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("flaky failure on attempt 1")
                return FakeResponse("success on attempt 2")

        payload = {
            "version": 1,
            "reason": "retry task",
            "children": [
                {"id": "c-flaky", "focus": "flaky task", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "flaky"}
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "flaky": FlakyAdapter()}, budget_usd=1.00, max_attempts=2)
        graph = [{"id": "m-flaky", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "flaky"}]
        org = scheduler.schedule(graph, organization_id="org-retry-single-record", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-flaky"].attempts, 2)
        self.assertEqual(org.tasks["c-flaky"].status, AgentTaskStatus.COMPLETED)
        # Exactly 2 logical tasks in total: m-flaky and c-flaky
        self.assertEqual(len(org.tasks), 2)


class RecursiveSpawningHardeningUnitTests(unittest.TestCase):
    """Recursive hierarchy safety, depth boundaries, cancellation, retry, and cycle hardening tests."""

    def test_two_level_manager_hierarchy_executes_successfully(self) -> None:
        payload_root = {
            "version": 1,
            "reason": "spawn squad manager",
            "children": [
                {
                    "id": "squad-mgr",
                    "focus": "squad level decomposition",
                    "executor_kind": ExecutorKind.MANAGER,
                    "agent_id": "squad_adapter",
                }
            ],
        }
        payload_squad = {
            "version": 1,
            "reason": "spawn leaf tool",
            "children": [
                {
                    "id": "leaf-tool",
                    "focus": "execute leaf tool",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "agent_id": "tool",
                }
            ],
        }
        root_adapter = DeterministicManagerAdapter(proposal_payload=payload_root)
        squad_adapter = DeterministicManagerAdapter(proposal_payload=payload_squad)
        settings = AgentFabricSettings(recursive_spawning=True, envelope=ResourceEnvelope(max_manager_depth=3))
        scheduler = NanoScheduler(
            {"root_mgr": root_adapter, "squad_adapter": squad_adapter},
            budget_usd=1.00,
            settings=settings,
            envelope=ResourceEnvelope(max_manager_depth=3),
        )
        graph = [{"id": "m-root", "agent_id": "root_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "root"}]
        org = scheduler.schedule(graph, organization_id="org-two-level", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("squad-mgr", org.tasks)
        self.assertIn("leaf-tool", org.tasks)
        self.assertEqual(org.tasks["m-root"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["squad-mgr"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["leaf-tool"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["squad-mgr"].manager_depth, 1)

    def test_depth_boundary_is_exact(self) -> None:
        parent = AgentTaskRecord(id="m-depth1", organization_id="org", project_id="p", agent_id="mgr", focus="d1", status=AgentTaskStatus.COMPLETED, manager_depth=1)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-depth1"] = parent

        # Depth 1 parent proposing Manager child -> child canonical depth is 2. Under max_manager_depth = 2, this is ALLOWED.
        child = AgentTaskRecord(id="m-depth2", organization_id="org", project_id="p", agent_id="mgr", focus="d2", executor_kind=ExecutorKind.MANAGER)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org, envelope=ResourceEnvelope(max_manager_depth=2), settings=AgentFabricSettings(recursive_spawning=True))
        self.assertTrue(auth["authorized"])
        self.assertEqual(child.manager_depth, 2)

    def test_one_level_over_depth_manager_denied(self) -> None:
        parent = AgentTaskRecord(id="m-depth1", organization_id="org", project_id="p", agent_id="mgr", focus="d1", status=AgentTaskStatus.COMPLETED, manager_depth=1)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-depth1"] = parent

        # Depth 1 parent proposing Manager child -> canonical depth is 2. Under max_manager_depth = 1, this is DENIED.
        child = AgentTaskRecord(id="m-depth2", organization_id="org", project_id="p", agent_id="mgr", focus="d2", executor_kind=ExecutorKind.MANAGER)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org, envelope=ResourceEnvelope(max_manager_depth=1), settings=AgentFabricSettings(recursive_spawning=True))
        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["governor_rule"], "max_manager_depth")
        self.assertIn("exceeds envelope limit", auth["reason"])

    def test_forged_manager_depth_in_child_proposal_is_overridden_by_runtime_ancestry(self) -> None:
        parent = AgentTaskRecord(id="m-depth1", organization_id="org", project_id="p", agent_id="mgr", focus="d1", status=AgentTaskStatus.COMPLETED, manager_depth=1)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-depth1"] = parent

        # Child tries to forge depth 0
        child = AgentTaskRecord(id="m-forged", organization_id="org", project_id="p", agent_id="mgr", focus="forged", executor_kind=ExecutorKind.MANAGER, manager_depth=0)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org, envelope=ResourceEnvelope(max_manager_depth=1), settings=AgentFabricSettings(recursive_spawning=True))
        # Evaluated at depth 2 (parent + 1) and correctly denied
        self.assertFalse(auth["authorized"])
        self.assertEqual(child.manager_depth, 2)

    def test_recursive_spawning_false_blocks_nested_manager(self) -> None:
        parent = AgentTaskRecord(id="m-root", organization_id="org", project_id="p", agent_id="mgr", focus="root", status=AgentTaskStatus.COMPLETED, manager_depth=0)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-root"] = parent

        child = AgentTaskRecord(id="m-child", organization_id="org", project_id="p", agent_id="mgr", focus="child", executor_kind=ExecutorKind.MANAGER)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org, settings=AgentFabricSettings(recursive_spawning=False))
        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["governor_rule"], "recursive_spawning")

    def test_nested_nano_child_still_allowed_when_policy_permits(self) -> None:
        parent = AgentTaskRecord(id="m-root", organization_id="org", project_id="p", agent_id="mgr", focus="root", status=AgentTaskStatus.COMPLETED, manager_depth=0)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-root"] = parent

        child = AgentTaskRecord(id="nano-child", organization_id="org", project_id="p", agent_id="nano", focus="child", executor_kind=ExecutorKind.NANO_LOCAL_MODEL)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org, settings=AgentFabricSettings(recursive_spawning=False))
        self.assertTrue(auth["authorized"])

    def test_nested_verification_cell_executes_normally(self) -> None:
        payload = {
            "version": 1,
            "reason": "verify nested",
            "children": [
                {"id": "vcell-nested", "focus": "verify correctness", "executor_kind": ExecutorKind.VERIFICATION_CELL, "verifier_ids": ["v1", "v2"]}
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        v1 = FakeVerifierAdapter(verdict="pass", summary="v1 ok", is_conservative_bound=True)
        v2 = FakeVerifierAdapter(verdict="pass", summary="v2 ok", is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [{"id": "m-vcell-nest", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"}]
        org = scheduler.schedule(graph, organization_id="org-vcell-nest", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertIn("vcell-nested", org.tasks)
        self.assertEqual(org.tasks["vcell-nested"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["vcell-nested"].verification_result["outcome"], "verified")

    def test_logical_agent_cap_enforced_across_hierarchy(self) -> None:
        payload = {
            "version": 1,
            "reason": "spawn 3 children",
            "children": [
                {"id": "c1", "focus": "child 1", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL},
                {"id": "c2", "focus": "child 2", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL},
                {"id": "c3", "focus": "child 3", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        # Cap is 3: root manager (1) + child 1 (2) + child 2 (3). Child 3 exceeds cap and must be denied!
        scheduler = NanoScheduler({"mgr": mgr_adapter}, budget_usd=1.00, envelope=ResourceEnvelope(max_logical_agents=3, max_concurrent_agents=3))
        graph = [{"id": "m-cap", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "cap"}]
        org = scheduler.schedule(graph, organization_id="org-cap-hier", project_id="p", goal="cap")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(len(org.tasks), 3)
        self.assertIn("c1", org.tasks)
        self.assertIn("c2", org.tasks)
        self.assertNotIn("c3", org.tasks)

    def test_dynamic_children_obey_same_concurrency_semaphore(self) -> None:
        concurrency_tracker = []
        current_concurrent = 0
        max_seen = 0

        class ConcurrencyTool:
            is_local = True
            is_conservative_bound = True
            async def generate(self, request):
                nonlocal current_concurrent, max_seen
                current_concurrent += 1
                max_seen = max(max_seen, current_concurrent)
                await asyncio.sleep(0.05)
                current_concurrent -= 1
                return FakeResponse("done")

        payload = {
            "version": 1,
            "reason": "4 parallel children",
            "children": [
                {"id": "c1", "focus": "c1", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "w"},
                {"id": "c2", "focus": "c2", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "w"},
                {"id": "c3", "focus": "c3", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "w"},
                {"id": "c4", "focus": "c4", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "w"},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler(
            {"mgr": mgr_adapter, "w": ConcurrencyTool()},
            budget_usd=1.00,
            envelope=ResourceEnvelope(max_logical_agents=8, max_concurrent_agents=2),
        )
        graph = [{"id": "m-conc", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "conc"}]
        org = scheduler.schedule(graph, organization_id="org-conc-check", project_id="p", goal="conc")
        org.max_concurrent = 2
        org = asyncio.run(scheduler.run(org))

        self.assertLessEqual(max_seen, 2)
        self.assertEqual(len(org.tasks), 5)

    def test_cancellation_before_manager_completion_prevents_children(self) -> None:
        parent = AgentTaskRecord(id="m-canc", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.CANCELLED, error="organization cancelled")
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-canc"] = parent

        child = AgentTaskRecord(id="c-canc", organization_id="org", project_id="p", agent_id="tool", focus="c", executor_kind=ExecutorKind.DETERMINISTIC_TOOL)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertIn("cancelled", auth["reason"])

    def test_cancellation_after_manager_completion_prevents_children(self) -> None:
        parent = AgentTaskRecord(id="m-comp", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-comp"] = parent
        # Another task in org was cancelled due to org cancellation
        org.tasks["other"] = AgentTaskRecord(id="other", organization_id="org", project_id="p", agent_id="t", focus="f", status=AgentTaskStatus.CANCELLED, error="organization cancelled by user")

        child = AgentTaskRecord(id="c1", organization_id="org", project_id="p", agent_id="tool", focus="c1", executor_kind=ExecutorKind.DETERMINISTIC_TOOL)
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["governor_rule"], "organization_not_cancelled")

    def test_cancellation_while_child_queued_prevents_execution(self) -> None:
        child = AgentTaskRecord(id="c-queued", organization_id="org", project_id="p", agent_id="tool", focus="c", status=AgentTaskStatus.QUEUED)
        child.error = "organization cancelled"
        child.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(child.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(child.cost_usd, 0.0)

    def test_child_failure_blocks_dependent_descendants(self) -> None:
        payload = {
            "version": 1,
            "reason": "dependent on failing child",
            "children": [
                {"id": "c-failing", "focus": "fail work", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "bad_worker"},
                {"id": "c-dependent", "focus": "dep work", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "depends_on": ["c-failing"]},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        bad_worker = FakeAdapter(fail_first=5, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "bad_worker": bad_worker}, budget_usd=1.00, max_attempts=1)
        graph = [{"id": "m-fail-prop", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "fail"}]
        org = scheduler.schedule(graph, organization_id="org-fail-prop", project_id="p", goal="fail")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-failing"].status, AgentTaskStatus.FAILED)
        self.assertEqual(org.tasks["c-dependent"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("upstream dependency did not complete", org.tasks["c-dependent"].error)

    def test_independent_sibling_survives_unrelated_child_failure(self) -> None:
        payload = {
            "version": 1,
            "reason": "independent sibling",
            "children": [
                {"id": "c-fail", "focus": "fail work", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "bad_worker"},
                {"id": "c-indep", "focus": "indep work", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "depends_on": []},
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        bad_worker = FakeAdapter(fail_first=5, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr_adapter, "bad_worker": bad_worker}, budget_usd=1.00, max_attempts=1)
        graph = [{"id": "m-indep", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "indep"}]
        org = scheduler.schedule(graph, organization_id="org-indep-survive", project_id="p", goal="indep")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-fail"].status, AgentTaskStatus.FAILED)
        self.assertEqual(org.tasks["c-indep"].status, AgentTaskStatus.COMPLETED)

    def test_manager_retry_does_not_duplicate_proposals_or_tasks(self) -> None:
        attempts = 0
        payload = {
            "version": 1,
            "reason": "single proposal",
            "children": [{"id": "c-single", "focus": "single child", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL}],
        }
        class FlakyManagerAdapter:
            is_local = True
            is_conservative_bound = True
            async def generate(self, request):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("flaky manager attempt 1")
                return FakeResponse(json.dumps(payload))

        scheduler = NanoScheduler({"mgr": FlakyManagerAdapter()}, budget_usd=1.00, max_attempts=2)
        graph = [{"id": "m-flaky-mgr", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "mgr"}]
        org = scheduler.schedule(graph, organization_id="org-flaky-mgr-retry", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["m-flaky-mgr"].attempts, 2)
        self.assertEqual(org.tasks["m-flaky-mgr"].status, AgentTaskStatus.COMPLETED)
        self.assertIn("c-single", org.tasks)
        self.assertEqual(len(org.tasks), 2)

    def test_child_retry_remains_one_logical_agent_task_record(self) -> None:
        flaky = FakeAdapter(fail_first=1, is_conservative_bound=True)
        payload = {
            "version": 1,
            "reason": "retry child",
            "children": [{"id": "c-retry-one", "focus": "retry", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "flaky"}],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"mgr": mgr, "flaky": flaky}, budget_usd=1.00, max_attempts=2)
        graph = [{"id": "m-child-retry", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "retry"}]
        org = scheduler.schedule(graph, organization_id="org-child-retry-single", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-retry-one"].attempts, 2)
        self.assertEqual(org.tasks["c-retry-one"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(len(org.tasks), 2)

    def test_paid_retry_gets_new_reservation(self) -> None:
        flaky = FakeAdapter(cost_usd=0.03, fail_first=1, is_conservative_bound=True, is_local=False)
        payload = {
            "version": 1,
            "reason": "paid retry",
            "children": [{"id": "c-paid-retry", "focus": "paid", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "flaky"}],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        scheduler = NanoScheduler({"mgr": mgr, "flaky": flaky}, budget_usd=1.00, max_attempts=2)
        graph = [{"id": "m-paid-ret", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "paid"}]
        org = scheduler.schedule(graph, organization_id="org-paid-retry-res", project_id="p", goal="paid")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-paid-retry"].attempts, 2)
        self.assertAlmostEqual(org.tasks["c-paid-retry"].cost_usd, 0.06, places=5)
        self.assertAlmostEqual(org.total_cost_usd(), 0.06, places=5)

    def test_insufficient_retry_budget_prevents_dispatch(self) -> None:
        flaky = FakeAdapter(cost_usd=0.05, fail_first=1, is_conservative_bound=True, is_local=False)
        payload = {
            "version": 1,
            "reason": "budget starved retry",
            "children": [{"id": "c-starved", "focus": "starved", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "flaky"}],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        # Budget of 0.05 covers attempt 1 (0.05 spend), but leaves 0.00 for attempt 2
        scheduler = NanoScheduler({"mgr": mgr, "flaky": flaky}, budget_usd=0.05, max_attempts=2)
        graph = [{"id": "m-starve", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "starve"}]
        org = scheduler.schedule(graph, organization_id="org-retry-starve", project_id="p", goal="starve")
        org = asyncio.run(scheduler.run(org))

        # Attempt 1 ran, attempt 2 was budget-cancelled
        self.assertEqual(org.tasks["c-starved"].status, AgentTaskStatus.CANCELLED)
        self.assertAlmostEqual(org.total_cost_usd(), 0.05, places=5)

    def test_three_node_indirect_dynamic_cycle_rejected(self) -> None:
        parent = AgentTaskRecord(id="m-parent", organization_id="org", project_id="p", agent_id="mgr", focus="p", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-parent"] = parent

        # Existing task t1 depends on t2, t2 depends on t3
        org.tasks["t1"] = AgentTaskRecord(id="t1", organization_id="org", project_id="p", agent_id="w", focus="t1", depends_on=("t2",))
        org.tasks["t2"] = AgentTaskRecord(id="t2", organization_id="org", project_id="p", agent_id="w", focus="t2", depends_on=("t3",))
        org.tasks["t3"] = AgentTaskRecord(id="t3", organization_id="org", project_id="p", agent_id="w", focus="t3", depends_on=())

        # Proposed child t3_cycle (pretending to be t3 or completing t3 -> t1)
        child = AgentTaskRecord(id="t3_cycle", organization_id="org", project_id="p", agent_id="w", focus="t3", depends_on=("t1",))
        # Now if t2 depends on t3_cycle and t3_cycle depends on t1, it forms t1 -> t2 -> t3_cycle -> t1
        org.tasks["t2"].depends_on = ("t3_cycle",)

        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["governor_rule"], "no_cycle")
        self.assertIn("indirect cycle", auth["reason"])

    def test_existing_graph_plus_new_child_cycle_rejected(self) -> None:
        parent = AgentTaskRecord(id="m-parent", organization_id="org", project_id="p", agent_id="mgr", focus="p", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-parent"] = parent
        org.tasks["t1"] = AgentTaskRecord(id="t1", organization_id="org", project_id="p", agent_id="w", focus="t1", depends_on=("c_cross",))

        child = AgentTaskRecord(id="c_cross", organization_id="org", project_id="p", agent_id="w", focus="c", depends_on=("t1",))
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["governor_rule"], "no_cycle")

    def test_duplicate_nested_proposal_does_not_cause_runaway_insertion(self) -> None:
        parent = AgentTaskRecord(id="m-parent", organization_id="org", project_id="p", agent_id="mgr", focus="p", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-parent"] = parent
        org.tasks["existing_child"] = AgentTaskRecord(id="existing_child", organization_id="org", project_id="p", agent_id="w", focus="f")

        child = AgentTaskRecord(id="existing_child", organization_id="org", project_id="p", agent_id="w", focus="f")
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["governor_rule"], "unique_task_id")

    def test_verification_cell_descendant_cannot_bypass_verifier_contract(self) -> None:
        parent = AgentTaskRecord(id="m-parent", organization_id="org", project_id="p", agent_id="mgr", focus="p", status=AgentTaskStatus.COMPLETED)
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-parent"] = parent

        # 1. Less than 2 verifiers
        child1 = AgentTaskRecord(id="v1", organization_id="org", project_id="p", agent_id="v", focus="v", executor_kind=ExecutorKind.VERIFICATION_CELL, verifier_ids=("ver1",))
        auth1 = AgentGovernor.authorize_child_proposal(child1, parent_task=parent, org=org)
        self.assertFalse(auth1["authorized"])
        self.assertEqual(auth1["governor_rule"], "verification_cell_min_verifiers")

        # 2. Missing adapter
        child2 = AgentTaskRecord(id="v2", organization_id="org", project_id="p", agent_id="v", focus="v", executor_kind=ExecutorKind.VERIFICATION_CELL, verifier_ids=("v1", "v2_missing"))
        auth2 = AgentGovernor.authorize_child_proposal(child2, parent_task=parent, org=org, available_adapters={"v1": FakeAdapter()})
        self.assertFalse(auth2["authorized"])
        self.assertEqual(auth2["governor_rule"], "verification_cell_adapter_available")

    def test_nested_paid_task_cannot_bypass_hard_budget_admission(self) -> None:
        payload = {
            "version": 1,
            "reason": "nested paid task",
            "children": [{"id": "c-paid-nested", "focus": "paid", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "paid_w"}],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        paid_w = FakeAdapter(cost_usd=0.80, is_local=False, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr, "paid_w": paid_w}, budget_usd=0.20)
        graph = [{"id": "m-root-paid", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "root"}]
        org = scheduler.schedule(graph, organization_id="org-nested-budget", project_id="p", goal="plan")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["c-paid-nested"].status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget exhausted", org.tasks["c-paid-nested"].error)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_organization_terminates_deterministically_under_maximum_legal_recursion(self) -> None:
        # Construct a 2-level manager tree with leaf tools
        payload_l1 = {
            "version": 1,
            "reason": "l1 proposal",
            "children": [{"id": "l2_mgr", "focus": "l2", "executor_kind": ExecutorKind.MANAGER, "agent_id": "mgr_l2"}],
        }
        payload_l2 = {
            "version": 1,
            "reason": "l2 proposal",
            "children": [{"id": "leaf", "focus": "leaf", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL}],
        }
        scheduler = NanoScheduler(
            {"mgr_l1": DeterministicManagerAdapter(proposal_payload=payload_l1), "mgr_l2": DeterministicManagerAdapter(proposal_payload=payload_l2)},
            budget_usd=1.00,
            settings=AgentFabricSettings(recursive_spawning=True, envelope=ResourceEnvelope(max_manager_depth=3)),
            envelope=ResourceEnvelope(max_manager_depth=3),
        )
        graph = [{"id": "root", "agent_id": "mgr_l1", "executor_kind": ExecutorKind.MANAGER, "focus": "root"}]
        org = scheduler.schedule(graph, organization_id="org-term-check", project_id="p", goal="term")
        org = asyncio.run(scheduler.run(org))

        # Check all terminal statuses
        self.assertTrue(all(t.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED, AgentTaskStatus.RETIRED} for t in org.tasks.values()))
        self.assertEqual(len(org.tasks), 3)

    def test_task_and_call_counts_remain_truthful(self) -> None:
        payload = {
            "version": 1,
            "reason": "truthful counts",
            "children": [
                {"id": "t_local", "focus": "local", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "loc"},
                {"id": "t_api", "focus": "api", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "agent_id": "api"},
            ],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        loc_w = FakeAdapter(is_local=True, is_conservative_bound=True)
        api_w = FakeAdapter(is_local=False, is_conservative_bound=True)
        scheduler = NanoScheduler({"mgr": mgr, "loc": loc_w, "api": api_w}, budget_usd=1.00)
        graph = [{"id": "m-truth", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "truth"}]
        org = scheduler.schedule(graph, organization_id="org-truth-check", project_id="p", goal="truth")
        org = asyncio.run(scheduler.run(org))

        self.assertEqual(org.tasks["t_local"].local_calls, 1)
        self.assertEqual(org.tasks["t_local"].api_calls, 0)
        self.assertEqual(org.tasks["t_api"].local_calls, 0)
        self.assertEqual(org.tasks["t_api"].api_calls, 1)

    def test_security_regression_credential_sanitization(self) -> None:
        # 1. Ensure sensitive keys are strictly rejected
        sensitive_cases = (
            "Authorization",
            "authorization",
            "authorization_header",
            "Cookie",
            "cookie",
            "session_cookie",
            "X-API-Key",
            "api_key",
            "APIKey",
            "apikey",
            "access_token",
            "refresh_token",
            "sessionToken",
            "auth_token",
            "password",
            "passwd",
            "passphrase",
            "secret",
            "clientSecret",
            "client_secret",
            "private_key",
            "signing_key",
            "decryption_key",
            "database_url",
            "connection_string",
            "webhook_secret",
        )
        for key in sensitive_cases:
            with self.subTest(key=key):
                self.assertTrue(is_sensitive_key(key), f"Expected {key!r} to be detected as sensitive")
                with self.assertRaises(SensitiveDataError):
                    assert_no_sensitive_keys({key: "secret_value"})

        # 2. Ensure non-sensitive governance provenance compounds are allowed
        safe_governance_cases = (
            "authorization_record",
            "authorization_records",
            "authorization_rule",
            "authorization_rules",
            "authorization_reason",
            "authorization_decision",
            "authorization_status",
            "input_tokens",
            "output_tokens",
            "public_key",
        )
        for key in safe_governance_cases:
            with self.subTest(key=key):
                self.assertFalse(is_sensitive_key(key), f"Expected {key!r} to NOT be detected as sensitive")
                # Assert passes without raising
                assert_no_sensitive_keys({key: {"rule": "safety_check", "status": "authorized"}})


def structured_collab_output(
    summary: str,
    *,
    event_type: str = "alternative",
    focus_resolved: bool = True,
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "events": [
                {
                    "type": event_type,
                    "content": summary,
                    "severity": "info",
                    "confidence": 0.85,
                    "evidence_refs": [],
                }
            ],
            "criterion_evidence": [],
            "focus_resolved": focus_resolved,
        }
    )


def make_collab_response(
    summary: str,
    *,
    cost_usd: float = 0.0,
    event_type: str = "alternative",
    focus_resolved: bool = True,
) -> ModelResponse:
    return ModelResponse(
        text=structured_collab_output(summary, event_type=event_type, focus_resolved=focus_resolved),
        model="scripted",
        provider="scripted",
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost_usd=cost_usd),
    )


class GateCResult:
    """Structured acceptance result for Gate C integration verification."""

    def __init__(self, org: OrganizationRun):
        self.organization_id = org.organization_id
        self.status = "completed" if all(t.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED, AgentTaskStatus.RETIRED} for t in org.tasks.values()) else "incomplete"
        self.manager_completed = any(t.executor_kind == ExecutorKind.MANAGER and t.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED, AgentTaskStatus.RETIRED} for t in org.tasks.values())
        self.collaboration_completed = any(t.executor_kind == ExecutorKind.COLLABORATION and t.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED, AgentTaskStatus.RETIRED} for t in org.tasks.values())
        
        vcell = next((t for t in org.tasks.values() if t.executor_kind == ExecutorKind.VERIFICATION_CELL), None)
        self.verification_outcome = vcell.verification_result.get("outcome") if vcell and vcell.verification_result else None
        
        integ = next((t for t in org.tasks.values() if "integration" in t.id or "blueprint" in t.id or "final" in t.id), None)
        self.integration_completed = integ.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.MERGED, AgentTaskStatus.RETIRED} if integ else False
        
        self.budget_usd = org.budget_usd
        self.incurred_usd = org.total_cost_usd()
        self.reserved_usd = org.reserved_cost_usd()
        self.logical_task_count = len(org.tasks)
        self.model_call_count = sum(t.local_calls + t.api_calls for t in org.tasks.values())
        self.provenance_complete = all(t.to_dict() is not None for t in org.tasks.values())


class AuthoritativeComplexGateCUnitTests(unittest.TestCase):
    """Authoritative Gate C Stop Gate Integration Tests."""

    def test_gate_c_authoritative_positive_flow(self) -> None:
        """Complete Gate C flow: Manager -> Dynamic Collab + Verification Cell + Integration -> Verified -> Retired."""
        payload = {
            "version": 1,
            "reason": "Decompose complex system architecture into collaborative synthesis, multi-verifier validation, and final integration",
            "children": [
                {
                    "id": "c-collab",
                    "focus": "Synthesize enterprise multi-tier architecture",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["arch_lead", "sec_spec"],
                    "collaboration_architecture": "joint",
                    "sync_mode": "bounded_staleness",
                },
                {
                    "id": "c-vcell",
                    "focus": "Verify security and performance compliance of architecture",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["ver_comp", "ver_perf"],
                    "depends_on": ["c-collab"],
                },
                {
                    "id": "c-integration",
                    "focus": "Assemble and publish verified deployable system blueprint",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": ["c-collab", "c-vcell"],
                },
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        arch_lead = ScriptedAdapter(
            [structured_collab_output("Designed scalable microservices architecture with gRPC and mTLS")],
            adapter_id="arch_lead",
            is_local=True,
        )
        sec_spec = ScriptedAdapter(
            [structured_collab_output("Validated zero-trust networking, IAM policies, and secret rotation", focus_resolved=True)],
            adapter_id="sec_spec",
            is_local=True,
        )
        ver_comp = FakeVerifierAdapter(verdict="pass", summary="All security and compliance rules satisfied", is_conservative_bound=True)
        ver_perf = FakeVerifierAdapter(verdict="pass", summary="Throughput and latency targets satisfied", is_conservative_bound=True)

        adapters = {
            "lead_mgr": mgr_adapter,
            "arch_lead": arch_lead,
            "sec_spec": sec_spec,
            "ver_comp": ver_comp,
            "ver_perf": ver_perf,
        }

        scheduler = NanoScheduler(adapters, budget_usd=2.50, envelope=ResourceEnvelope(max_logical_agents=8, max_concurrent_agents=4))
        graph = [{"id": "m-lead", "agent_id": "lead_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "Lead architectural planning"}]
        org = scheduler.schedule(graph, organization_id="org-gate-c-pos", project_id="p-gate-c", goal="Architect Enterprise Platform")
        
        # Execute live scheduler
        org = asyncio.run(scheduler.run(org))
        org = asyncio.run(scheduler.retire(org))

        # Assertions
        gate_res = GateCResult(org)
        self.assertEqual(gate_res.status, "completed")
        self.assertTrue(gate_res.manager_completed)
        self.assertTrue(gate_res.collaboration_completed)
        self.assertEqual(gate_res.verification_outcome, "verified")
        self.assertTrue(gate_res.integration_completed)
        self.assertTrue(org.retired)
        self.assertEqual(gate_res.logical_task_count, 4)
        self.assertTrue(gate_res.provenance_complete)

        # Provenance verification
        collab_task = org.tasks["c-collab"]
        self.assertIsNotNone(collab_task.collaboration_result)
        self.assertEqual(collab_task.authorization_record["authorized"], True)
        self.assertIn("microservices", collab_task.result_summary)

        vcell_task = org.tasks["c-vcell"]
        self.assertEqual(vcell_task.verification_result["outcome"], "verified")
        self.assertFalse(vcell_task.verification_result["dissent_present"])

        integ_task = org.tasks["c-integration"]
        self.assertEqual(integ_task.status, AgentTaskStatus.RETIRED)

    def test_gate_c_authoritative_dissent_negative_flow(self) -> None:
        """Dissent negative Gate C flow: Verifier dissent produces DISPUTED, blocking integration."""
        payload = {
            "version": 1,
            "reason": "Decompose and verify",
            "children": [
                {
                    "id": "c-collab-dissent",
                    "focus": "Synthesize design",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["arch_lead", "sec_spec"],
                    "collaboration_architecture": "joint",
                },
                {
                    "id": "c-vcell-dissent",
                    "focus": "Verify design",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["ver_pass", "ver_fail"],
                    "depends_on": ["c-collab-dissent"],
                },
                {
                    "id": "c-integration-dissent",
                    "focus": "Assemble blueprint",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": ["c-collab-dissent", "c-vcell-dissent"],
                },
            ],
        }
        mgr_adapter = DeterministicManagerAdapter(proposal_payload=payload)
        arch_lead = ScriptedAdapter([structured_collab_output("Architecture outline")], adapter_id="arch_lead", is_local=True)
        sec_spec = ScriptedAdapter([structured_collab_output("Security review", focus_resolved=True)], adapter_id="sec_spec", is_local=True)
        ver_pass = FakeVerifierAdapter(verdict="pass", summary="Compliant", is_conservative_bound=True)
        ver_fail = FakeVerifierAdapter(verdict="fail", summary="Critical security flaw detected in auth layer", is_conservative_bound=True)

        adapters = {
            "mgr": mgr_adapter,
            "arch_lead": arch_lead,
            "sec_spec": sec_spec,
            "ver_pass": ver_pass,
            "ver_fail": ver_fail,
        }

        scheduler = NanoScheduler(adapters, budget_usd=2.00)
        graph = [{"id": "m-dissent", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "Plan"}]
        org = scheduler.schedule(graph, organization_id="org-gate-c-dissent", project_id="p-gate-c", goal="Plan")
        org = asyncio.run(scheduler.run(org))

        # Verification outcome is DISPUTED
        vcell = org.tasks["c-vcell-dissent"]
        self.assertEqual(vcell.verification_result["outcome"], "disputed")
        self.assertTrue(vcell.verification_result["dissent_present"])

        # Downstream integration task was CANCELLED and never executed
        integ = org.tasks["c-integration-dissent"]
        self.assertEqual(integ.status, AgentTaskStatus.CANCELLED)
        self.assertIn("upstream verification gate failed", integ.error)

    def test_gate_c_authoritative_budget_negative_flow(self) -> None:
        """Budget negative Gate C flow: Insufficient conservative budget blocks collaboration execution."""
        payload = {
            "version": 1,
            "reason": "Paid collaboration decomposition",
            "children": [
                {
                    "id": "c-paid-collab",
                    "focus": "Paid collaborative analysis",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["p1", "p2"],
                },
                {
                    "id": "c-vcell",
                    "focus": "Verify",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                    "depends_on": ["c-paid-collab"],
                },
                {
                    "id": "c-integ",
                    "focus": "Integrate",
                    "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                    "depends_on": ["c-vcell"],
                },
            ],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        # Each paid participant requires $0.05 bound (total $0.10)
        p1 = FakeAdapter(cost_usd=0.05, is_local=False, is_conservative_bound=True)
        p2 = FakeAdapter(cost_usd=0.05, is_local=False, is_conservative_bound=True)
        v1 = FakeVerifierAdapter(is_conservative_bound=True)
        v2 = FakeVerifierAdapter(is_conservative_bound=True)

        adapters = {"mgr": mgr, "p1": p1, "p2": p2, "v1": v1, "v2": v2}
        # Org budget of $0.06 is insufficient for $0.10 collaboration reservation
        scheduler = NanoScheduler(adapters, budget_usd=0.06)
        graph = [{"id": "m-budg", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "Plan"}]
        org = scheduler.schedule(graph, organization_id="org-gate-c-budget", project_id="p-gate-c", goal="Plan")
        org = asyncio.run(scheduler.run(org))

        # Collaboration task cancelled due to budget exhaustion
        collab_task = org.tasks["c-paid-collab"]
        self.assertEqual(collab_task.status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget exhausted", collab_task.error)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_gate_c_authoritative_cancellation_flow(self) -> None:
        """Cancellation Gate C flow: Mid-run cancellation blocks subsequent dynamic authorization and dispatch."""
        payload = {
            "version": 1,
            "reason": "Spawn work",
            "children": [
                {
                    "id": "c-collab-canc",
                    "focus": "Collab",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["a1", "a2"],
                }
            ],
        }
        parent = AgentTaskRecord(id="m-canc", organization_id="org", project_id="p", agent_id="mgr", focus="plan", status=AgentTaskStatus.CANCELLED, error="organization cancelled")
        org = OrganizationRun(organization_id="org", project_id="p", goal="g", budget_usd=1.0, max_concurrent=2)
        org.tasks["m-canc"] = parent

        child = AgentTaskRecord(id="c-collab-canc", organization_id="org", project_id="p", agent_id="w", focus="collab", executor_kind=ExecutorKind.COLLABORATION, participant_ids=("a1", "a2"))
        auth = AgentGovernor.authorize_child_proposal(child, parent_task=parent, org=org)
        self.assertFalse(auth["authorized"])
        self.assertIn("cancelled", auth["reason"])

    def test_gate_c_truthful_logical_agent_accounting(self) -> None:
        """Gate C logical agent counting: internal model calls do not inflate logical task records."""
        payload = {
            "version": 1,
            "reason": "Gate C tasks",
            "children": [
                {
                    "id": "c-collab-cnt",
                    "focus": "Collab synthesis",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["c1", "c2"],
                },
                {
                    "id": "c-vcell-cnt",
                    "focus": "Verify",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                    "depends_on": ["c-collab-cnt"],
                },
            ],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        c1 = ScriptedAdapter([structured_collab_output("part 1")], adapter_id="c1", is_local=True)
        c2 = ScriptedAdapter([structured_collab_output("part 2", focus_resolved=True)], adapter_id="c2", is_local=True)
        v1 = FakeVerifierAdapter(verdict="pass", summary="v1 pass", is_conservative_bound=True)
        v2 = FakeVerifierAdapter(verdict="pass", summary="v2 pass", is_conservative_bound=True)

        scheduler = NanoScheduler({"mgr": mgr, "c1": c1, "c2": c2, "v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [{"id": "m-count", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "Plan"}]
        org = scheduler.schedule(graph, organization_id="org-gate-c-count", project_id="p-gate-c", goal="Plan")
        org = asyncio.run(scheduler.run(org))

        # Exactly 3 logical tasks: Manager, Collaboration, Verification Cell
        self.assertEqual(len(org.tasks), 3)
        self.assertEqual(org.active_count(), 0)
        self.assertEqual(org.completed_count(), 3)
        # Total model calls was at least 4 across collaboration and verifiers
        total_calls = sum(t.local_calls + t.api_calls for t in org.tasks.values())
        self.assertGreaterEqual(total_calls, 4)

    def test_gate_c_security_sanitization_in_provenance(self) -> None:
        """Gate C provenance does not expose credentials or sensitive headers."""
        payload = {
            "version": 1,
            "reason": "Security test flow",
            "children": [
                {
                    "id": "c-collab-sec",
                    "focus": "Design secure system",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["s1", "s2"],
                },
                {
                    "id": "c-vcell-sec",
                    "focus": "Verify security",
                    "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ["v1", "v2"],
                    "depends_on": ["c-collab-sec"],
                },
            ],
        }
        mgr = DeterministicManagerAdapter(proposal_payload=payload)
        s1 = ScriptedAdapter([structured_collab_output("Security design s1")], adapter_id="s1", is_local=True)
        s2 = ScriptedAdapter([structured_collab_output("Security design s2", focus_resolved=True)], adapter_id="s2", is_local=True)
        v1 = FakeVerifierAdapter(verdict="pass", summary="Security pass", is_conservative_bound=True)
        v2 = FakeVerifierAdapter(verdict="pass", summary="Security pass 2", is_conservative_bound=True)

        scheduler = NanoScheduler({"mgr": mgr, "s1": s1, "s2": s2, "v1": v1, "v2": v2}, budget_usd=1.00)
        graph = [{"id": "m-sec", "agent_id": "mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "Plan"}]
        org = scheduler.schedule(graph, organization_id="org-gate-c-sec", project_id="p-gate-c", goal="Plan")
        org = asyncio.run(scheduler.run(org))

        # Assert no sensitive keys anywhere in persisted org representation
        assert_no_sensitive_keys(org.to_dict())


class CollaborationWholeExecutionBudgetUnitTests(unittest.TestCase):
    """Deterministic tests for Work Order AG-A1.3.1 Collaboration whole-execution budget bounds."""

    def test_joint_collaboration_multiple_rounds_reservation_covers_all_calls(self) -> None:
        """JOINT mode with >1 bounded rounds reserves sum(participant bounds) * max_rounds."""
        # 2 paid participants at $0.02 each, max_rounds=3 -> bound must be (0.02 + 0.02) * 3 = 0.12
        p1 = ScriptedAdapter(
            [
                make_collab_response("p1 round 1", cost_usd=0.02, focus_resolved=False),
                make_collab_response("p1 round 2", cost_usd=0.02, focus_resolved=False),
                make_collab_response("p1 round 3", cost_usd=0.02, focus_resolved=False),
            ],
            adapter_id="p1",
            is_local=False,
        )
        p1.cost_usd = 0.02
        p1.is_conservative_bound = True

        p2 = ScriptedAdapter(
            [
                make_collab_response("p2 round 1", cost_usd=0.02, focus_resolved=False),
                make_collab_response("p2 round 2", cost_usd=0.02, focus_resolved=False),
                make_collab_response("p2 round 3", cost_usd=0.02, focus_resolved=True),
            ],
            adapter_id="p2",
            is_local=False,
        )
        p2.cost_usd = 0.02
        p2.is_conservative_bound = True

        task = AgentTaskRecord(
            id="t-joint-multi",
            organization_id="org-joint-multi",
            project_id="p-1",
            agent_id="p1",
            focus="joint multi-round design",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("p1", "p2"),
            collaboration_architecture="joint",
            max_rounds=3,
        )

        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=1.00)
        bound = scheduler._calculate_task_cost_bound(task)
        self.assertAlmostEqual(bound, 0.12, places=5)

        org = scheduler.schedule([task.to_dict()], organization_id="org-joint-multi", project_id="p-1", goal="Goal")
        org = asyncio.run(scheduler.run(org))

        collab_task = org.tasks["t-joint-multi"]
        self.assertEqual(collab_task.status, AgentTaskStatus.COMPLETED)
        # Reservation settled
        res = list(org.reservations.values())[0]
        self.assertAlmostEqual(res.reserved_usd, 0.12, places=5)
        self.assertEqual(res.status, "settled")
        self.assertFalse(res.integrity_violation)
        # Truthful API call accounting: 2 participants * 3 rounds = 6 calls
        self.assertEqual(collab_task.api_calls, 6)
        self.assertEqual(collab_task.local_calls, 0)
        self.assertAlmostEqual(collab_task.cost_usd, 0.12, places=5)
        # 1 logical task identity preserved
        self.assertEqual(len(org.tasks), 1)

    def test_joint_collaboration_insufficient_whole_execution_budget_yields_zero_calls(self) -> None:
        """JOINT mode with insufficient whole-execution budget cancels before dispatch with 0 calls."""
        # 2 paid participants at $0.02 each, max_rounds=3 -> bound 0.12
        p1 = ScriptedAdapter([make_collab_response("p1", cost_usd=0.02)], adapter_id="p1", is_local=False)
        p1.cost_usd = 0.02
        p1.is_conservative_bound = True
        p2 = ScriptedAdapter([make_collab_response("p2", cost_usd=0.02)], adapter_id="p2", is_local=False)
        p2.cost_usd = 0.02
        p2.is_conservative_bound = True

        task = AgentTaskRecord(
            id="t-joint-insufficient",
            organization_id="org-joint-insufficient",
            project_id="p-1",
            agent_id="p1",
            focus="joint multi-round design",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("p1", "p2"),
            collaboration_architecture="joint",
            max_rounds=3,
        )

        # Budget of 0.08 is enough for 2 rounds (0.08) but NOT whole-execution (0.12)
        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=0.08)
        org = scheduler.schedule([task.to_dict()], organization_id="org-joint-insufficient", project_id="p-1", goal="Goal")
        org = asyncio.run(scheduler.run(org))

        collab_task = org.tasks["t-joint-insufficient"]
        self.assertEqual(collab_task.status, AgentTaskStatus.CANCELLED)
        self.assertIn("budget exhausted", collab_task.error)
        # Zero model calls made!
        self.assertEqual(collab_task.api_calls, 0)
        self.assertEqual(collab_task.cost_usd, 0.0)
        self.assertEqual(org.total_cost_usd(), 0.0)

    def test_independent_discuss_maximum_call_reservation(self) -> None:
        """INDEPENDENT_DISCUSS reserves (1 + max_rounds) * participant bounds for each participant."""
        # 2 participants at $0.03 each, max_rounds=2
        # Calls per participant = 1 (independent reveal) + 2 (joint discussion) = 3 calls
        # Bound = (0.03 * 3) + (0.03 * 3) = 0.18
        p1 = ScriptedAdapter(
            [
                make_collab_response("p1 indep", cost_usd=0.03),
                make_collab_response("p1 discuss 1", cost_usd=0.03),
                make_collab_response("p1 discuss 2", cost_usd=0.03, focus_resolved=True),
            ],
            adapter_id="p1",
            is_local=False,
        )
        p1.cost_usd = 0.03
        p1.is_conservative_bound = True

        p2 = ScriptedAdapter(
            [
                make_collab_response("p2 indep", cost_usd=0.03),
                make_collab_response("p2 discuss 1", cost_usd=0.03),
                make_collab_response("p2 discuss 2", cost_usd=0.03, focus_resolved=True),
            ],
            adapter_id="p2",
            is_local=False,
        )
        p2.cost_usd = 0.03
        p2.is_conservative_bound = True

        task = AgentTaskRecord(
            id="t-indep-discuss",
            organization_id="org-indep",
            project_id="p-1",
            agent_id="p1",
            focus="independent synthesis",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("p1", "p2"),
            collaboration_architecture="independent_discuss",
            max_rounds=2,
        )

        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=1.00)
        bound = scheduler._calculate_task_cost_bound(task)
        self.assertAlmostEqual(bound, 0.18, places=5)

        org = scheduler.schedule([task.to_dict()], organization_id="org-indep", project_id="p-1", goal="Goal")
        org = asyncio.run(scheduler.run(org))

        collab_task = org.tasks["t-indep-discuss"]
        self.assertEqual(collab_task.status, AgentTaskStatus.COMPLETED)
        res = list(org.reservations.values())[0]
        self.assertAlmostEqual(res.reserved_usd, 0.18, places=5)
        self.assertLessEqual(collab_task.cost_usd, 0.18)
        self.assertEqual(len(org.tasks), 1)

    def test_parallel_decomposition_bound_with_plan_includes_integration_owner(self) -> None:
        """PARALLEL derives conservative bound summing all work units and integration owner."""
        w1 = FakeAdapter(cost_usd=0.02, is_local=False, is_conservative_bound=True)
        w2 = FakeAdapter(cost_usd=0.03, is_local=False, is_conservative_bound=True)
        w3 = FakeAdapter(cost_usd=0.01, is_local=False, is_conservative_bound=True)
        integ = FakeAdapter(cost_usd=0.04, is_local=False, is_conservative_bound=True)

        plan = DecompositionPlan(
            work_units=(
                WorkUnit(
                    id="u1",
                    focus="Unit 1",
                    inputs={},
                    output_contract={"schema": {}},
                    dependencies=(),
                    assignee_ids=("w1",),
                    integration_owner="integ",
                ),
                WorkUnit(
                    id="u2",
                    focus="Unit 2",
                    inputs={},
                    output_contract={"schema": {}},
                    dependencies=(),
                    assignee_ids=("w2",),
                    integration_owner="integ",
                ),
                WorkUnit(
                    id="u3",
                    focus="Unit 3",
                    inputs={},
                    output_contract={"schema": {}},
                    dependencies=(),
                    assignee_ids=("w3",),
                    integration_owner="integ",
                ),
            ),
        )

        task_with_plan = AgentTaskRecord(
            id="t-parallel-plan",
            organization_id="org-parallel",
            project_id="p-1",
            agent_id="w1",
            focus="parallel decomposition",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("w1", "w2", "w3", "integ"),
            collaboration_architecture="parallel",
            decomposition_plan=plan,
        )

        scheduler = NanoScheduler({"w1": w1, "w2": w2, "w3": w3, "integ": integ}, budget_usd=1.00)
        bound = scheduler._calculate_task_cost_bound(task_with_plan)
        # Sum of assigned work unit bounds (0.02 + 0.03 + 0.01 = 0.06) + integration owner (0.04) = 0.10
        self.assertAlmostEqual(bound, 0.10, places=5)

    def test_parallel_missing_plan_fails_closed_zero_calls(self) -> None:
        """PARALLEL without decomposition plan fails closed before dispatch with zero calls."""
        w1 = FakeAdapter(cost_usd=0.02, is_local=False, is_conservative_bound=True)
        w2 = FakeAdapter(cost_usd=0.03, is_local=False, is_conservative_bound=True)

        task_no_plan = AgentTaskRecord(
            id="t-parallel-no-plan",
            organization_id="org-parallel",
            project_id="p-1",
            agent_id="w1",
            focus="parallel decomposition no plan",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("w1", "w2"),
            collaboration_architecture="parallel",
            decomposition_plan=None,
        )

        scheduler = NanoScheduler({"w1": w1, "w2": w2}, budget_usd=1.00)
        with self.assertRaises(ValueError) as ctx:
            scheduler._calculate_task_cost_bound(task_no_plan)
        self.assertIn("missing decomposition_plan", str(ctx.exception))

        # Test execution in organization run cancels with 0 calls
        org = scheduler.schedule([task_no_plan.to_dict()], organization_id="org-parallel", project_id="p-1", goal="Goal")
        org = asyncio.run(scheduler.run(org))
        t = org.tasks["t-parallel-no-plan"]
        self.assertEqual(t.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(t.api_calls, 0)
        self.assertEqual(t.cost_usd, 0.0)

    def test_adaptive_hybrid_in_hard_budget_fails_closed_with_zero_calls(self) -> None:
        """ADAPTIVE_HYBRID in hard-budget mode strictly fails closed before dispatch with zero calls."""
        p1 = FakeAdapter(cost_usd=0.01, is_local=False, is_conservative_bound=True)
        p2 = FakeAdapter(cost_usd=0.01, is_local=False, is_conservative_bound=True)

        task = AgentTaskRecord(
            id="t-adaptive",
            organization_id="org-adaptive",
            project_id="p-1",
            agent_id="p1",
            focus="adaptive hybrid work",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("p1", "p2"),
            collaboration_architecture="adaptive_hybrid",
            max_adaptive_phases=3,
        )

        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=1.00)
        with self.assertRaises(ValueError) as ctx:
            scheduler._calculate_task_cost_bound(task)
        self.assertIn("ADAPTIVE_HYBRID collaboration task", str(ctx.exception))
        self.assertIn("fails closed in hard-budget mode", str(ctx.exception))

        # Organization run confirms zero calls and CANCELLED status
        org = scheduler.schedule([task.to_dict()], organization_id="org-adaptive", project_id="p-1", goal="Goal")
        org = asyncio.run(scheduler.run(org))
        t = org.tasks["t-adaptive"]
        self.assertEqual(t.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(t.api_calls, 0)
        self.assertEqual(t.cost_usd, 0.0)
        self.assertIn("cost bound error", t.error)

    def test_unsupported_collaboration_architecture_fails_closed(self) -> None:
        """Unsupported collaboration architectures fail closed in hard-budget mode."""
        p1 = FakeAdapter(cost_usd=0.01, is_local=False, is_conservative_bound=True)
        p2 = FakeAdapter(cost_usd=0.01, is_local=False, is_conservative_bound=True)

        task = AgentTaskRecord(
            id="t-unsupported",
            organization_id="org-unsupported",
            project_id="p-1",
            agent_id="p1",
            focus="experimental topology",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("p1", "p2"),
            collaboration_architecture="quantum_neural_swarm",
        )

        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=1.00)
        with self.assertRaises(ValueError) as ctx:
            scheduler._calculate_task_cost_bound(task)
        self.assertIn("unsupported collaboration architecture", str(ctx.exception))

    def test_manager_proposal_parsing_preserves_collaboration_budget_bounds(self) -> None:
        """Manager proposal parser correctly extracts max_rounds, max_adaptive_phases, and decomposition_plan."""
        payload = {
            "version": 1,
            "reason": "Test collab proposal",
            "children": [
                {
                    "id": "c-joint-rounds",
                    "focus": "Joint 3 rounds",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["p1", "p2"],
                    "collaboration_architecture": "joint",
                    "max_rounds": 3,
                },
                {
                    "id": "c-adaptive-phases",
                    "focus": "Adaptive 5 phases",
                    "executor_kind": ExecutorKind.COLLABORATION,
                    "participant_ids": ["p1", "p2"],
                    "collaboration_architecture": "adaptive_hybrid",
                    "max_adaptive_phases": 5,
                },
            ],
        }

        parsed = parse_manager_proposal_payload(json.dumps(payload), manager_id="m1", organization_id="org", project_id="p")
        self.assertTrue(parsed["valid"])
        children = parsed["children"]
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0].max_rounds, 3)
        self.assertEqual(children[1].max_adaptive_phases, 5)

    def test_scripted_adapter_bound_integrity_violation_in_collaboration(self) -> None:
        """If an adapter exceeds its declared conservative bound during collaboration, integrity_violation is flagged."""
        # Declared bound $0.02, but actual execution returns cost $0.05
        p1 = ScriptedAdapter(
            [make_collab_response("p1 exceeding bound", cost_usd=0.05, focus_resolved=True)],
            adapter_id="p1",
            is_local=False,
        )
        p1.cost_usd = 0.02
        p1.is_conservative_bound = True

        p2 = ScriptedAdapter(
            [make_collab_response("p2 normal", cost_usd=0.02, focus_resolved=True)],
            adapter_id="p2",
            is_local=False,
        )
        p2.cost_usd = 0.02
        p2.is_conservative_bound = True

        task = AgentTaskRecord(
            id="t-violation",
            organization_id="org-violation",
            project_id="p-1",
            agent_id="p1",
            focus="collaboration with integrity violation",
            executor_kind=ExecutorKind.COLLABORATION,
            participant_ids=("p1", "p2"),
            collaboration_architecture="joint",
            max_rounds=1,
        )

        scheduler = NanoScheduler({"p1": p1, "p2": p2}, budget_usd=1.00)
        # Reserved bound = 0.02 + 0.02 = 0.04
        self.assertAlmostEqual(scheduler._calculate_task_cost_bound(task), 0.04, places=5)

        org = scheduler.schedule([task.to_dict()], organization_id="org-violation", project_id="p-1", goal="Goal")
        org = asyncio.run(scheduler.run(org))

        collab_task = org.tasks["t-violation"]
        self.assertEqual(collab_task.status, AgentTaskStatus.COMPLETED)
        # Actual cost was 0.05 + 0.02 = 0.07 (which exceeded reserved 0.04)
        self.assertAlmostEqual(collab_task.cost_usd, 0.07, places=5)
        # Reservation flagged integrity violation and retained truthful actual cost
        res = list(org.reservations.values())[0]
        self.assertTrue(res.integrity_violation)
        self.assertAlmostEqual(res.settled_actual_usd, 0.07, places=5)


if __name__ == "__main__":
    unittest.main()




