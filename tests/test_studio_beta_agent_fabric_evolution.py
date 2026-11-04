"""
AMCA Studio Beta B1 Agent Fabric Evolution Test Suite
Validates:
1. Agent capability registration and project isolation
2. Capability querying and filtering
3. Lexical task suitability scoring
4. Full backward compatibility with existing AgentFabricStore methods
"""

import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentFabricStore, AgentCapabilityRecord


class StudioBetaAgentFabricEvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "agent_fabric_beta.sqlite3"
        self.store = AgentFabricStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_agent_capability_registration_and_isolation(self) -> None:
        """Verify registering agents in project A does not leak to project B."""
        ag_a = self.store.register_capability_agent(
            project_id="proj-alpha",
            name="Security Reviewer",
            role="security auditor",
            specializations=["auth", "cryptography", "owasp"],
            capabilities=["code_review", "policy_check"],
            description="Audits source code for auth flaws",
        )
        self.assertTrue(ag_a["agent_id"].startswith("agent."))
        self.assertEqual(ag_a["project_id"], "proj-alpha")

        # Verify isolated list
        agents_a = self.store.list_capability_agents("proj-alpha")
        self.assertEqual(len(agents_a), 1)
        self.assertEqual(agents_a[0]["name"], "Security Reviewer")

        agents_b = self.store.list_capability_agents("proj-beta")
        self.assertEqual(len(agents_b), 0)

    def test_capability_filtering_and_discovery(self) -> None:
        """Verify discovery of agents by specific declared capability."""
        self.store.register_capability_agent(
            project_id="proj-gamma",
            name="Data Analyst",
            role="data scientist",
            specializations=["pandas", "statistics"],
            capabilities=["tabular_analysis", "charting"],
        )
        self.store.register_capability_agent(
            project_id="proj-gamma",
            name="Frontend Dev",
            role="ui engineer",
            specializations=["react", "typescript"],
            capabilities=["component_design", "charting"],
        )

        charting_agents = self.store.find_agents_by_capability("proj-gamma", "charting")
        self.assertEqual(len(charting_agents), 2)

        data_agents = self.store.find_agents_by_capability("proj-gamma", "tabular_analysis")
        self.assertEqual(len(data_agents), 1)
        self.assertEqual(data_agents[0]["name"], "Data Analyst")

    def test_suitability_scoring_heuristic(self) -> None:
        """Verify lexical suitability scoring based on role and specializations."""
        agent = {
            "name": "Python Backend Engineer",
            "role": "backend python developer",
            "specializations": ["fastapi", "sqlite", "pytest"],
            "capabilities": ["unit_testing", "api_design"],
        }
        score_high = self.store.score_agent_suitability(
            agent,
            "Design fastapi REST API endpoint and add pytest suite",
        )
        score_low = self.store.score_agent_suitability(
            agent,
            "Create marketing flyer illustration for social media",
        )
        self.assertGreater(score_high, score_low)
        self.assertGreater(score_high, 3.0)
        self.assertEqual(score_low, 0.0)

    def test_backward_compatibility_with_existing_fabric_catalog(self) -> None:
        """Verify existing catalog and planning methods execute unmodified."""
        catalog = self.store.catalog()
        self.assertIn("agents", catalog)
        self.assertIn("settings", catalog)
        self.assertGreater(catalog["counts"]["core"], 0)


if __name__ == "__main__":
    unittest.main()

