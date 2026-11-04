from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from amca.agent_fabric import (
    AgentEconomyManager, AgentFabricSettings, AgentFabricStore, AgentGovernor, AgentToolRouter,
    BUILT_IN_AGENTS, CORE_AGENT_NAMES, ResourceEnvelope,
)


class AgentFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "amca.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_contains_complete_core_and_broad_specialist_library(self) -> None:
        catalog = AgentFabricStore(self.path).catalog()
        self.assertEqual(catalog["counts"]["core"], len(CORE_AGENT_NAMES))
        self.assertEqual(len(CORE_AGENT_NAMES), 41)
        self.assertGreaterEqual(catalog["counts"]["built_in_specialists"], 150)
        self.assertEqual(catalog["counts"]["total_available"], len(BUILT_IN_AGENTS))
        self.assertEqual(catalog["default_policy"], "auto_agents")
        self.assertIsNone(catalog["active_organization"])

    def test_custom_agent_round_trip_and_delete(self) -> None:
        store = AgentFabricStore(self.path)
        created = store.save_custom_agent({
            "name": "Clinical Admin Reviewer",
            "description": "Reviews non-diagnostic healthcare administration documents.",
            "instructions": "Check completeness and flag missing administrative fields.",
            "category": "Healthcare Administration",
            "allowed_tools": ["document_reader"],
            "permissions": ["read_project_files"],
            "can_spawn": False,
        })
        self.assertEqual(created["origin"], "custom")
        self.assertEqual(created["allowed_tools"], ["document_reader"])
        self.assertIn("instructions", created)
        self.assertEqual(store.catalog()["counts"]["custom"], 1)
        store.delete_custom_agent(created["id"])
        self.assertEqual(store.custom_agents(), [])

    def test_settings_persist_and_governor_enforces_hard_limits(self) -> None:
        store = AgentFabricStore(self.path)
        updated = store.update_settings({
            "scaling": "conservative", "recursive_spawning": False,
            "envelope": {"max_logical_agents": 5, "max_concurrent_agents": 2, "max_api_cost_usd": 1.0},
        })
        self.assertEqual(AgentFabricStore(self.path).settings(), updated)
        denied = AgentGovernor.authorize(6, 2, 1, 0.5, updated)
        self.assertFalse(denied["authorized"])
        self.assertFalse(denied["checks"]["logical_agents"])

    def test_builtin_agents_have_minimal_governed_tool_capabilities(self) -> None:
        store = AgentFabricStore(self.path)
        coding = store.tool_capabilities("specialist.coding")
        research = store.tool_capabilities("specialist.deep-research")
        browser = store.tool_capabilities("specialist.browser")
        self.assertIn("filesystem.write", coding)
        self.assertIn("command.inspect", coding)
        self.assertNotIn("filesystem.write", research)
        self.assertIn("application.control.propose", browser)
        self.assertTrue(AgentToolRouter.authorize("specialist.coding", "filesystem.write", coding)["authorized"])
        self.assertFalse(AgentToolRouter.authorize("specialist.deep-research", "filesystem.write", research)["authorized"])

    def test_economy_manager_scales_down_and_up_without_model_calls(self) -> None:
        settings = AgentFabricSettings(envelope=ResourceEnvelope(max_logical_agents=100))
        tiny = AgentEconomyManager.recommend("Count these rows", settings)
        large = AgentEconomyManager.recommend(
            ("Research current sources, compare evidence, analyze a spreadsheet and database, "
             "update repository code, test it, create a report, PDF, citations, and deployment notes "
             "with browser verification and image analysis. ") * 5,
            settings,
        )
        self.assertEqual(tiny["organization_size"], "minimal")
        self.assertEqual(tiny["logical_workers"], 1)
        self.assertEqual(large["organization_size"], "large")
        self.assertGreater(large["logical_workers"], tiny["logical_workers"])

    def test_plan_is_local_authorized_and_contains_no_secret_fields(self) -> None:
        plan = AgentFabricStore(self.path).plan(
            "Research authoritative sources, update the repository code, run tests, compare evidence, and verify the completed implementation."
        )
        self.assertTrue(plan["governor"]["authorized"])
        self.assertFalse(plan["model_calls_started"])
        self.assertEqual(plan["external_api_cost_usd"], 0)
        serialized = str(plan).casefold()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("credential", serialized)
        self.assertTrue(plan["task_graph"])
        self.assertIn("core.critic-agent", plan["selected_agents"])
        self.assertEqual(plan["nano_policy"]["rule"], "deterministic tools first; batch only independent bounded tasks")
        self.assertTrue(plan["tool_policy"]["deny_by_default"])
        self.assertTrue(plan["tool_policy"]["local_access_required"])
        self.assertEqual(plan["tool_policy"]["command_execution"], "inspection_only_exact_approval")
        self.assertIn("core.agent-governor", plan["selected_agents"])
        self.assertIn("core.permission-agent", plan["selected_agents"])
        self.assertIn("core.tool-router", plan["selected_agents"])
        assignments = {item["agent_id"]: item for item in plan["tool_policy"]["assignments"]}
        self.assertIn("bounded_local_filesystem", assignments["core.tool-router"]["allowed_tools"])
        self.assertIn("never_self_approve", assignments["core.permission-agent"]["permissions"])


if __name__ == "__main__":
    unittest.main()
