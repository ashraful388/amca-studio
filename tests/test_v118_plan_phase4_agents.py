"""AMCA Studio v1.1.8 Final Build Plan - Phase 4 evidence (agent system repair).

Phase 4 objective: "Make AMCA agents actually functional."

Required agent types: Research, Coding, Review, Testing, Bug Finding,
Validation, Security, Documentation.

The plan also names these defects to fix: "inactive agents, skipped roles,
silent failures, incorrect planner behavior".

These tests assert the behaviour that was actually fixed:

* every one of the eight required types is reachable by the planner;
* a planned role is never silently given no task - the "skipped role" defect;
* custom agents the user created are tasked, not only listed;
* control roles are tasked;
* the goal vocabulary selects the *specific* specialist rather than only the
  generalist (a security goal picks the security agent, not just coding).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentFabricStore


class RequiredAgentTypeTests(unittest.TestCase):
    """The eight types the plan names must be selectable and tasked."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = AgentFabricStore(Path(self.temporary.name) / "amca.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self, goal: str):
        plan = self.store.plan(goal)
        tasked = {task["agent_id"] for task in plan["task_graph"]}
        return plan, tasked

    def test_research_agent(self) -> None:
        plan, tasked = self._plan("research the literature and cite sources")
        self.assertIn("specialist.deep-research", plan["selected_agents"])
        self.assertIn("specialist.deep-research", tasked)

    def test_coding_agent(self) -> None:
        plan, tasked = self._plan("implement the software feature in the repository")
        self.assertIn("specialist.coding", plan["selected_agents"])
        self.assertIn("specialist.coding", tasked)

    def test_review_agent(self) -> None:
        """Was catalog-only: no rule could select it."""
        plan, tasked = self._plan("review this code and the pull request")
        self.assertIn("specialist.code-review", plan["selected_agents"])
        self.assertIn("specialist.code-review", tasked)

    def test_testing_agent(self) -> None:
        """Was catalog-only: 'test' mapped to the coding specialist."""
        plan, tasked = self._plan("add unit test coverage for the module")
        self.assertIn("specialist.unit-testing", plan["selected_agents"])
        self.assertIn("specialist.unit-testing", tasked)

    def test_bug_finding_agent(self) -> None:
        """Was catalog-only: 'bug' mapped to the coding specialist."""
        plan, tasked = self._plan("reproduce the bug and find the defect")
        self.assertIn("specialist.bug-reproduction", plan["selected_agents"])
        self.assertIn("specialist.bug-reproduction", tasked)

    def test_validation_agent(self) -> None:
        plan, tasked = self._plan("validate the acceptance criteria and verify the result")
        self.assertIn("core.verification-agent", plan["selected_agents"])
        self.assertIn("core.verification-agent", tasked)

    def test_security_agent(self) -> None:
        """Was catalog-only: no security rule existed at all."""
        plan, tasked = self._plan("find security vulnerabilities and check for a cve")
        self.assertIn("specialist.security-analysis", plan["selected_agents"])
        self.assertIn("specialist.security-analysis", tasked)

    def test_documentation_agent(self) -> None:
        plan, tasked = self._plan("write the readme and user guide documentation")
        self.assertIn("specialist.technical-writing", plan["selected_agents"])
        self.assertIn("specialist.technical-writing", tasked)


class NoSkippedRolesTests(unittest.TestCase):
    """A planned role must never be silently given no task."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = AgentFabricStore(Path(self.temporary.name) / "amca.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_every_selected_agent_has_a_task(self) -> None:
        goals = [
            "research the literature and cite sources",
            "review this code for security vulnerabilities",
            "write unit tests and fix the bug in the repository",
            "document the api in the readme",
            "write code for adding two numbers in here.",
            "summarize this note",
        ]
        for goal in goals:
            plan = self.store.plan(goal)
            tasked = {task["agent_id"] for task in plan["task_graph"]}
            missing = [
                agent for agent in plan["selected_agents"] if agent not in tasked
            ]
            self.assertEqual(missing, [], f"{goal!r} left roles untasked: {missing}")

    def test_control_roles_are_tasked(self) -> None:
        """agent-governor / tool-router / permission-agent used to get no task."""
        plan = self.store.plan("edit the file in the repository")
        tasked = {task["agent_id"] for task in plan["task_graph"]}
        for control in ("core.agent-governor", "core.tool-router", "core.permission-agent"):
            if control in plan["selected_agents"]:
                self.assertIn(control, tasked, f"{control} was selected but untasked")

    def test_a_custom_agent_is_tasked_not_only_listed(self) -> None:
        """Custom ids are `custom.<hex>`, which the old filter excluded."""
        created = self.store.save_custom_agent(
            {
                "name": "Invoice Auditor",
                "description": "Audits invoice documents for mismatches",
                "category": "Finance",
                "instructions": "Compare each invoice line against the purchase order.",
            }
        )
        agent_id = str(created.get("id") or created.get("agent_id"))
        plan = self.store.plan("audit the invoice documents for mismatches")
        self.assertIn(agent_id, plan["selected_agents"])
        tasked = {task["agent_id"] for task in plan["task_graph"]}
        self.assertIn(agent_id, tasked, "a custom agent must receive a task")

    def test_the_minimal_organization_tasks_more_than_the_first_role(self) -> None:
        """The single-worker branch used to task only selected[0]."""
        plan = self.store.plan("summarize this")
        tasked = {task["agent_id"] for task in plan["task_graph"]}
        self.assertEqual(len(tasked), len(plan["selected_agents"]))
        self.assertGreaterEqual(len(tasked), 1)


class SpecificSpecialistTests(unittest.TestCase):
    """A goal that names a domain must select that domain's specialist."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = AgentFabricStore(Path(self.temporary.name) / "amca.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_security_goal_picks_the_security_specialist(self) -> None:
        plan = self.store.plan("check for vulnerabilities in the login flow")
        self.assertIn("specialist.security-analysis", plan["selected_agents"])

    def test_a_testing_goal_picks_the_testing_specialist(self) -> None:
        plan = self.store.plan("raise unit test coverage")
        self.assertIn("specialist.unit-testing", plan["selected_agents"])

    def test_a_review_goal_picks_the_review_specialist(self) -> None:
        plan = self.store.plan("review the pull request")
        self.assertIn("specialist.code-review", plan["selected_agents"])


if __name__ == "__main__":
    unittest.main()
