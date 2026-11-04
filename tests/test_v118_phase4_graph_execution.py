"""v1.1.8 Phase 4 — the three architectural gaps the manager made implementation tasks.

(a) the task graph is not scheduled by an execution engine;
(b) the plan's ``tool_policy`` is not applied to a run's tool catalog;
(c) there is no agent-to-agent handoff.

These tests pin the behaviour that closes them, and — more importantly — pin the
security properties that must not be relaxed while doing so: tool authorization
is an intersection of two ceilings and fails closed, and no plan label may cause
an executor kind that needs injected collaborators to be selected.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentFabricStore
from amca.agent_graph_execution import (
    DEFAULT_EXECUTOR_KIND,
    EXECUTOR_CLASS_MAP,
    GOVERNANCE_FRAME,
    is_control_agent,
    apply_tool_policy,
    assignment_for,
    authorized_tool_names,
    executor_kind_for,
    graph_tasks,
    handoff_block,
    handoff_records,
)
from amca.agent_runtime import (
    HANDOFF_HEADER,
    MAX_HANDOFF_CHARS,
    AgentTaskStatus,
    ExecutorKind,
)

GOAL = "Write and review a Python module with unit tests"


def _store() -> AgentFabricStore:
    directory = tempfile.mkdtemp()
    return AgentFabricStore(Path(directory) / "fabric.sqlite")


def _plan(goal: str = GOAL) -> dict:
    return _store().plan(goal, project_id="p1")


class ExecutorKindMappingTests(unittest.TestCase):
    """A plan label must never select an executor that cannot run."""

    def test_every_known_class_maps_to_a_model_backed_kind(self) -> None:
        unsafe = {ExecutorKind.DETERMINISTIC_TOOL, ExecutorKind.VERIFICATION_CELL,
                  ExecutorKind.MANAGER, ExecutorKind.COLLABORATION}
        for executor_class in EXECUTOR_CLASS_MAP:
            with self.subTest(executor_class=executor_class):
                self.assertNotIn(executor_kind_for(executor_class), unsafe)

    def test_an_unknown_class_falls_back_to_a_model_call(self) -> None:
        self.assertEqual(executor_kind_for("something_new"), DEFAULT_EXECUTOR_KIND)
        self.assertEqual(executor_kind_for(""), DEFAULT_EXECUTOR_KIND)

    def test_mapping_is_not_case_or_whitespace_sensitive(self) -> None:
        self.assertEqual(executor_kind_for("  specialist  "), EXECUTOR_CLASS_MAP["specialist"])


class GraphAdapterTests(unittest.TestCase):
    """The plan graph must become schedulable tasks without losing structure."""

    def test_every_planned_node_becomes_a_task(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        self.assertEqual(len(tasks), len(plan["task_graph"]))

    def test_task_ids_and_dependencies_are_preserved(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        by_id = {task["id"]: task for task in tasks}
        for node in plan["task_graph"]:
            with self.subTest(node=node["id"]):
                self.assertIn(node["id"], by_id)
                self.assertEqual(by_id[node["id"]]["depends_on"], list(node["depends_on"]))
                self.assertEqual(by_id[node["id"]]["agent_id"], node["agent_id"])

    def test_every_task_carries_the_goal_and_its_role(self) -> None:
        tasks = graph_tasks(_plan(), GOAL)
        for task in tasks:
            with self.subTest(task=task["id"]):
                self.assertIn(GOAL, task["focus"])
                # Governance roles are framed differently on purpose; the
                # working roles carry their role statement.
                if not is_control_agent(task["agent_id"]):
                    self.assertIn("Your role:", task["focus"])

    def test_a_plan_with_no_graph_produces_no_tasks(self) -> None:
        self.assertEqual(graph_tasks({"task_graph": []}, GOAL), [])
        self.assertEqual(graph_tasks({}, GOAL), [])
        self.assertEqual(graph_tasks({"task_graph": "not-a-graph"}, GOAL), [])

    def test_malformed_nodes_are_skipped_not_crashed_on(self) -> None:
        plan = {"task_graph": [{"id": "", "agent_id": "a"}, {"agent_id": "b"}, "junk",
                               {"id": "ok", "agent_id": "c", "depends_on": []}]}
        tasks = graph_tasks(plan, GOAL)
        self.assertEqual([task["id"] for task in tasks], ["ok"])


class ToolPolicyTests(unittest.TestCase):
    """The plan's tool policy must narrow the catalog, and deny by default."""

    CATALOG = ["filesystem.read", "filesystem.write", "command.inspect", "application.control.propose"]

    def test_an_agent_with_no_assignment_gets_no_tools(self) -> None:
        plan = _plan()
        self.assertEqual(authorized_tool_names(plan, "nobody.at.all", self.CATALOG), ())

    def test_the_policy_can_only_narrow_the_catalog(self) -> None:
        plan = {"tool_policy": {"deny_by_default": True, "assignments": [
            {"agent_id": "a", "allowed_tools": ["filesystem.read", "not.in.catalog"]}]}}
        self.assertEqual(authorized_tool_names(plan, "a", self.CATALOG), ("filesystem.read",))

    def test_a_read_only_role_cannot_reach_a_write_tool(self) -> None:
        plan = _plan()
        reviewer = next(
            (item for item in plan["tool_policy"]["assignments"]
             if item["agent_id"] == "specialist.code-review"), None,
        )
        if reviewer is None:
            self.skipTest("code-review was not selected for this goal")
        granted = authorized_tool_names(plan, "specialist.code-review", self.CATALOG)
        self.assertNotIn("filesystem.write", granted)
        self.assertNotIn("command.inspect", granted)

    def test_control_agents_receive_no_governed_tools(self) -> None:
        # Their policy names abstract tools (approval_store, path_grant_store)
        # that are not governed catalog entries.  That must yield nothing, not
        # the whole catalog.
        plan = _plan()
        for agent_id in ("core.agent-governor", "core.permission-agent", "core.tool-router"):
            with self.subTest(agent_id=agent_id):
                self.assertEqual(authorized_tool_names(plan, agent_id, self.CATALOG), ())

    def test_a_malformed_allowed_tools_denies(self) -> None:
        for bad in ("filesystem.read", 5, None, {"filesystem.read": True}):
            with self.subTest(bad=bad):
                plan = {"tool_policy": {"deny_by_default": True,
                                        "assignments": [{"agent_id": "a", "allowed_tools": bad}]}}
                self.assertEqual(authorized_tool_names(plan, "a", self.CATALOG), ())

    def test_a_missing_or_broken_policy_denies_everything(self) -> None:
        for plan in ({}, {"tool_policy": None}, {"tool_policy": "nope"},
                     {"tool_policy": {"assignments": "nope"}}):
            with self.subTest(plan=plan):
                self.assertEqual(authorized_tool_names(plan, "specialist.coding", self.CATALOG), ())
                self.assertIsNone(assignment_for(plan, "specialist.coding"))

    def test_deny_by_default_is_reported_and_honoured(self) -> None:
        plan = _plan()
        self.assertTrue(plan["tool_policy"]["deny_by_default"])
        authorized = apply_tool_policy(plan, graph_tasks(plan, GOAL), self.CATALOG)
        unassigned = [names for names in authorized.values()]
        self.assertTrue(any(names == () for names in unassigned))

    def test_apply_tool_policy_keys_every_task(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        authorized = apply_tool_policy(plan, tasks, self.CATALOG)
        self.assertEqual(set(authorized), {task["id"] for task in tasks})

    def test_an_empty_catalog_grants_nothing(self) -> None:
        plan = _plan()
        for names in apply_tool_policy(plan, graph_tasks(plan, GOAL), []).values():
            self.assertEqual(names, ())


class HandoffTests(unittest.TestCase):
    """Agent A's result must reach agent B, and the transfer must be auditable."""

    def test_a_handoff_block_names_the_producing_agent(self) -> None:
        plan = _plan()
        node = {"id": "b", "depends_on": ["a"]}
        block = handoff_block(plan, node, {"a": "the upstream finding"})
        self.assertIn(HANDOFF_HEADER, block)
        self.assertIn("the upstream finding", block)

    def test_a_handoff_is_absent_when_no_dependency_produced_anything(self) -> None:
        plan = _plan()
        self.assertEqual(handoff_block(plan, {"id": "b", "depends_on": ["a"]}, {}), "")
        self.assertEqual(handoff_block(plan, {"id": "b", "depends_on": []}, {"a": "x"}), "")

    def test_an_oversized_upstream_result_is_bounded(self) -> None:
        plan = _plan()
        block = handoff_block(plan, {"id": "b", "depends_on": ["a"]}, {"a": "x" * 20000})
        self.assertLessEqual(len(block), MAX_HANDOFF_CHARS + 200)

    def test_records_cover_every_real_edge_and_report_delivery(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        records = handoff_records(plan, tasks, {})
        expected_edges = sum(len(task["depends_on"]) for task in tasks)
        self.assertEqual(len(records), expected_edges)
        self.assertTrue(all(record["delivered"] is False for record in records))

        # Deliver something a downstream task actually depends on.
        upstream_id = records[0]["from_task"]
        delivered = handoff_records(plan, tasks, {upstream_id: "done"})
        self.assertTrue(any(record["delivered"] for record in delivered))
        for record in delivered:
            self.assertIn("from_agent", record)
            self.assertIn("to_agent", record)

    def test_records_ignore_edges_to_tasks_that_do_not_exist(self) -> None:
        tasks = [{"id": "b", "agent_id": "x", "depends_on": ["ghost"]}]
        self.assertEqual(handoff_records({"task_graph": []}, tasks, {}), [])


class HandoffEnvelopeTests(unittest.TestCase):
    """Handoffs must be bounded and structured, not a growing prompt.

    The Architecture Completion Directive (section 23) is explicit: do not grow
    a giant concatenated prompt every time Agent A hands to Agent B.  The
    envelope carries identity, status, a content digest and a bounded excerpt,
    and the whole block is capped across all dependencies.
    """

    def _org(self, results: dict[str, str]):
        from amca.agent_runtime import AgentTaskRecord, AgentTaskStatus, OrganizationRun

        org = OrganizationRun(organization_id="o", project_id="p", goal="g", budget_usd=0.0, max_concurrent=1)
        for task_id, text in results.items():
            record = AgentTaskRecord(
                id=task_id, agent_id=f"agent.{task_id}", focus="f",
                executor_kind=ExecutorKind.NANO_LOCAL_MODEL, depends_on=(),
                organization_id="o", project_id="p",
            )
            record.result_summary = text
            record.status = AgentTaskStatus.COMPLETED
            org.tasks[task_id] = record
        return org

    def test_the_envelope_carries_identity_status_and_a_digest(self) -> None:
        from amca.agent_runtime import AgentTaskRecord, build_handoff_envelope

        org = self._org({"t1": "the upstream finding"})
        task = AgentTaskRecord(id="t2", agent_id="agent.t2", focus="f",
                               executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                               depends_on=("t1",), organization_id="o", project_id="p")
        envelope = build_handoff_envelope(task, org)
        self.assertIn("[t1]", envelope)
        self.assertIn("agent.t1", envelope)
        self.assertIn("status:", envelope)
        self.assertIn("result_digest:", envelope)
        self.assertIn("the upstream finding", envelope)

    def test_the_same_result_produces_the_same_digest(self) -> None:
        # A digest the downstream agent can compare is only useful if it is
        # stable for identical content.
        from amca.agent_runtime import AgentTaskRecord, build_handoff_envelope

        task = AgentTaskRecord(id="t2", agent_id="agent.t2", focus="f",
                               executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                               depends_on=("t1",), organization_id="o", project_id="p")
        first = build_handoff_envelope(task, self._org({"t1": "same"}))
        second = build_handoff_envelope(task, self._org({"t1": "same"}))
        third = build_handoff_envelope(task, self._org({"t1": "different"}))
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_a_wide_graph_cannot_inflate_the_prompt_without_limit(self) -> None:
        from amca.agent_runtime import (
            HANDOFF_TOTAL_BUDGET,
            AgentTaskRecord,
            build_handoff_envelope,
        )

        deps = tuple(f"t{i}" for i in range(12))
        org = self._org({name: "x" * 3000 for name in deps})
        task = AgentTaskRecord(id="wide", agent_id="agent.wide", focus="f",
                               executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                               depends_on=deps, organization_id="o", project_id="p")
        envelope = build_handoff_envelope(task, org)
        # Twelve 3000-character results would be 36,000 characters if the
        # prompt simply concatenated them.
        self.assertLess(len(envelope), HANDOFF_TOTAL_BUDGET + 2000)
        self.assertIn("budget exhausted", envelope)

    def test_an_empty_upstream_produces_no_section_at_all(self) -> None:
        from amca.agent_runtime import AgentTaskRecord, build_handoff_envelope

        task = AgentTaskRecord(id="t2", agent_id="agent.t2", focus="f",
                               executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                               depends_on=("t1",), organization_id="o", project_id="p")
        self.assertEqual(build_handoff_envelope(task, self._org({})), "")

    def test_a_task_with_no_dependencies_gets_no_envelope(self) -> None:
        from amca.agent_runtime import AgentTaskRecord, build_handoff_envelope

        task = AgentTaskRecord(id="t1", agent_id="agent.t1", focus="f",
                               executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
                               depends_on=(), organization_id="o", project_id="p")
        self.assertEqual(build_handoff_envelope(task, self._org({"t9": "x"})), "")

    def test_the_graph_side_builder_matches_the_runtime_side(self) -> None:
        # Two builders exist; they must produce the same shape or a handoff
        # would look different depending on which path built it.
        from amca.agent_runtime import HANDOFF_TOTAL_BUDGET

        plan = {"task_graph": [{"id": "a", "agent_id": "agent.a"}]}
        node = {"id": "b", "depends_on": ["a"]}
        block = handoff_block(plan, node, {"a": "x" * 30000})
        self.assertIn("[a]", block)
        self.assertIn("result_digest:", block)
        self.assertLess(len(block), HANDOFF_TOTAL_BUDGET + 500)


class SchedulerHandoffWiringTests(unittest.TestCase):
    """The live scheduler, not just the helper, must pass results downstream."""

    def test_the_scheduler_injects_dependency_results_into_the_prompt(self) -> None:
        import asyncio

        from amca.agent_runtime import NanoScheduler
        from amca.models.scripted import ScriptedAdapter

        seen: dict[str, str] = {}

        class _Recording(ScriptedAdapter):
            def __init__(self, name: str) -> None:
                super().__init__([f"result from {name}"], adapter_id=name, is_local=True)
                self._name = name

            async def generate(self, request):
                seen[self._name] = request.messages[0]["content"]
                return await super().generate(request)

        scheduler = NanoScheduler(
            {"agent.a": _Recording("a"), "agent.b": _Recording("b")},
            budget_usd=10.0,
            max_concurrent=1,
        )
        org = scheduler.schedule(
            [
                {"id": "first", "agent_id": "agent.a", "focus": "do the first thing",
                 "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "depends_on": []},
                {"id": "second", "agent_id": "agent.b", "focus": "do the second thing",
                 "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "depends_on": ["first"]},
            ],
            organization_id="org-1", project_id="p1", goal=GOAL,
        )
        asyncio.run(scheduler.run(org))

        # The downstream agent must have received the upstream result, which is
        # the difference between a handoff and two independent calls.
        self.assertIn("b", seen, "the downstream agent never executed")
        self.assertIn(HANDOFF_HEADER, seen["b"])
        self.assertIn("result from a", seen["b"])
        self.assertNotIn(HANDOFF_HEADER, seen["a"])


class PlanIntegrationTests(unittest.TestCase):
    """The three gaps closed together, against a real plan."""

    def test_a_real_plan_schedules_and_authorizes_end_to_end(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        self.assertTrue(tasks)
        authorized = apply_tool_policy(plan, tasks, ["filesystem.read", "filesystem.write"])
        records = handoff_records(plan, tasks, {})
        self.assertEqual(set(authorized), {task["id"] for task in tasks})
        self.assertEqual(
            len(records), sum(len(task["depends_on"]) for task in tasks)
        )

    def test_every_selected_agent_is_scheduled_with_a_usable_executor(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        self.assertEqual(
            {task["agent_id"] for task in tasks}, set(plan["selected_agents"])
        )
        self.assertTrue(all(task["executor_kind"] == ExecutorKind.NANO_LOCAL_MODEL for task in tasks))


class GovernanceTaskingTests(unittest.TestCase):
    """Governance agents assess the run; they do not perform the user's work.

    A real-model run exposed this: the governor, permission agent and tool
    router were each handed "write a Python function", so three governance
    roles produced the user's deliverable instead of governing it.
    """

    def test_a_control_agent_is_not_given_the_users_task(self) -> None:
        plan = _plan()
        tasks = graph_tasks(plan, GOAL)
        control = [task for task in tasks if is_control_agent(task["agent_id"])]
        self.assertTrue(control, "the control roles must still be tasked, not skipped")
        for task in control:
            with self.subTest(agent=task["agent_id"]):
                self.assertIn(GOVERNANCE_FRAME, task["focus"])
                self.assertNotIn("Your role:", task["focus"])

    def test_a_working_agent_still_receives_the_goal(self) -> None:
        tasks = graph_tasks(_plan(), GOAL)
        for task in tasks:
            if is_control_agent(task["agent_id"]):
                continue
            with self.subTest(agent=task["agent_id"]):
                self.assertIn(GOAL, task["focus"])
                self.assertNotIn(GOVERNANCE_FRAME, task["focus"])

    def test_each_control_agent_gets_its_own_brief(self) -> None:
        tasks = graph_tasks(_plan(), GOAL)
        briefs = [task["focus"] for task in tasks if is_control_agent(task["agent_id"])]
        self.assertEqual(len(briefs), len(set(briefs)), "control briefs must be distinct")

    def test_the_control_roles_are_recognised(self) -> None:
        for agent_id in ("core.agent-governor", "core.permission-agent", "core.tool-router"):
            with self.subTest(agent=agent_id):
                self.assertTrue(is_control_agent(agent_id))
        self.assertFalse(is_control_agent("specialist.coding"))
        self.assertFalse(is_control_agent(""))


class CodingVocabularyTests(unittest.TestCase):
    """"Write a Python function" must select the coding agent."""

    def test_a_phrased_coding_goal_selects_the_coding_specialist(self) -> None:
        for goal in (
            "Write a Python function that reverses a string and add a unit test for it.",
            "Write a script that parses a CSV file.",
            "Implement a sorting algorithm in Python.",
            "Add a new class to the module.",
        ):
            with self.subTest(goal=goal):
                self.assertIn("specialist.coding", _plan(goal)["selected_agents"])

    def test_a_non_coding_goal_does_not_select_it(self) -> None:
        plan = _plan("Summarise the attached quarterly report.")
        self.assertNotIn("specialist.coding", plan["selected_agents"])


class ServicePlanExecutionTests(unittest.TestCase):
    """The plan must execute through the real service path, not just the helpers."""

    def _context(self, tmp: Path):
        from amca.service import ServiceContext

        software_root = Path(__file__).resolve().parents[1]
        return ServiceContext(
            root=software_root,
            database=tmp / "t.db",
            benchmarks=software_root / "benchmarks" / "tasks.json",
        )

    def test_the_service_schedules_the_graph_and_reports_handoffs(self) -> None:
        from amca.models.scripted import ScriptedAdapter

        tmp = Path(tempfile.mkdtemp())
        context = self._context(tmp)
        adapter = ScriptedAdapter(
            ["a short finding"], adapter_id="p", is_local=True, repeat_last=True
        )
        out = context.agent_plan_execute(
            {
                "project_id": "p1",
                "goal": GOAL,
                "participant": "unused-with-test-adapter",
                "_test_adapter": adapter,
            }
        )

        # 7 now that the coding vocabulary fix selects specialist.coding for
        # this goal; the governance roles are included but framed as governance.
        self.assertEqual(len(out["tasks"]), 7)
        self.assertTrue(all(task["status"] == "completed" for task in out["tasks"]))
        self.assertTrue(all(task["executor_kind"] == ExecutorKind.NANO_LOCAL_MODEL
                            for task in out["tasks"]))
        # Every dependency edge actually carried a result downstream.
        self.assertEqual(
            len(out["handoffs"]),
            sum(len(task["depends_on"]) for task in out["tasks"]),
        )
        self.assertTrue(all(handoff["delivered"] for handoff in out["handoffs"]))
        self.assertTrue(out["tool_policy"]["deny_by_default"])

    def test_no_grant_means_no_tools_even_though_the_run_succeeded(self) -> None:
        # Fail-closed: a project with no active grant yields an empty catalog,
        # so every agent is authorized for nothing while still being scheduled.
        from amca.models.scripted import ScriptedAdapter

        tmp = Path(tempfile.mkdtemp())
        context = self._context(tmp)
        adapter = ScriptedAdapter(["x"], adapter_id="p", is_local=True, repeat_last=True)
        out = context.agent_plan_execute(
            {"project_id": "p1", "goal": GOAL, "participant": "unused",
             "_test_adapter": adapter}
        )
        self.assertEqual(out["tool_policy"]["catalog"], [])
        for task in out["tasks"]:
            with self.subTest(task=task["id"]):
                self.assertEqual(task["authorized_tools"], [])

    def test_an_adapter_cannot_be_injected_from_a_json_body(self) -> None:
        # The seam is object-level only: a JSON body can never carry an adapter.
        tmp = Path(tempfile.mkdtemp())
        context = self._context(tmp)
        with self.assertRaises(ValueError):
            context.agent_plan_execute(
                {"project_id": "p1", "goal": GOAL, "participant": "unused",
                 "_test_adapter": {"generate": "x"}}
            )

    def test_missing_inputs_are_rejected_with_a_reason(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        context = self._context(tmp)
        for payload, expected in (
            ({"project_id": "p1", "participant": "x"}, "goal is required"),
            ({"project_id": "p1", "goal": GOAL}, "participant is required"),
            ({"project_id": "p1", "goal": "x" * 4001, "participant": "x"}, "4,000 characters"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as caught:
                    context.agent_plan_execute(payload)
                self.assertIn(expected, str(caught.exception))


class ParallelExecutionTests(unittest.TestCase):
    """Independent tasks must actually overlap, or "Parallel" is just a label."""

    def _run(self, max_concurrent: int):
        import asyncio
        import time as _time

        from amca.agent_runtime import NanoScheduler
        from amca.models.scripted import ScriptedAdapter

        spans: list[tuple[float, float]] = []
        lock = __import__("threading").Lock()

        class _Timed(ScriptedAdapter):
            def __init__(self, name: str) -> None:
                super().__init__(["done"], adapter_id=name, is_local=True)
                self._name = name

            async def generate(self, request):
                started = _time.monotonic()
                await asyncio.sleep(0.05)
                try:
                    return await super().generate(request)
                finally:
                    with lock:
                        spans.append((started, _time.monotonic()))

        names = [f"agent.{index}" for index in range(4)]
        scheduler = NanoScheduler(
            {name: _Timed(name) for name in names}, budget_usd=10.0,
            max_concurrent=max_concurrent,
        )
        graph = [
            {"id": f"t{index}", "agent_id": name, "focus": "independent work",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "depends_on": []}
            for index, name in enumerate(names)
        ]
        org = scheduler.schedule(graph, organization_id="org-p", project_id="p", goal=GOAL)
        asyncio.run(scheduler.run(org))
        return spans, org

    @staticmethod
    def _overlaps(spans) -> int:
        count = 0
        for index, (start_a, end_a) in enumerate(spans):
            for start_b, end_b in spans[index + 1:]:
                if start_a < end_b and start_b < end_a:
                    count += 1
        return count

    def test_bounded_concurrency_really_overlaps_independent_tasks(self) -> None:
        spans, org = self._run(max_concurrent=4)
        self.assertEqual(len(spans), 4)
        self.assertTrue(
            all(task.status == AgentTaskStatus.COMPLETED for task in org.tasks.values()),
            "every independent task must still complete",
        )
        self.assertGreater(
            self._overlaps(spans), 0,
            "with max_concurrent=4 the independent tasks must overlap in time",
        )

    def test_concurrency_of_one_is_genuinely_sequential(self) -> None:
        spans, org = self._run(max_concurrent=1)
        self.assertEqual(len(spans), 4)
        self.assertEqual(
            self._overlaps(spans), 0,
            "max_concurrent=1 must not overlap, so the test above proves something",
        )

    def test_results_stay_keyed_by_task_under_concurrency(self) -> None:
        # Deterministic aggregation: concurrency must not change which result
        # belongs to which task.
        _spans, org = self._run(max_concurrent=4)
        self.assertEqual(set(org.tasks), {"t0", "t1", "t2", "t3"})
        for task in org.tasks.values():
            self.assertEqual(task.result_summary, "done")


if __name__ == "__main__":
    unittest.main()
