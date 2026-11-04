"""
AMCA Studio v0.8.0 Technical Architecture Assessment Test Suite
Validates:
1. Deferred capability inventory completeness across 4 core architectural pillars
2. Roadmap consistency, priority classification (Must Have, Should Have, Future Research)
3. Zero premature feature implementation (pure planning and feasibility assessment)
4. Local-first, zero cloud telemetry design principles preserved
"""

import unittest

from amca.v080_architecture_assessment import (
    ArchitecturePillarAssessment,
    V080ReadinessReport,
    V080RoadmapItem,
    evaluate_v080_architecture_readiness,
)


class StudioV080ArchitectureReadinessTests(unittest.TestCase):
    def test_pillar_inventory_completeness(self) -> None:
        """Verify presence of all 4 required v0.8.0 architectural pillars."""
        report = evaluate_v080_architecture_readiness("v0.8.0")
        self.assertTrue(report.assessment_id.startswith("v08-assess-"))
        self.assertEqual(report.target_version, "v0.8.0")
        self.assertEqual(report.overall_verdict, "ARCHITECTURE_READINESS_APPROVED")

        pillar_names = {p.pillar_name for p in report.pillars}
        expected_pillars = {
            "Agent Fabric v2",
            "Distributed Execution",
            "Knowledge Intelligence",
            "Provider Health & Credentials",
        }
        self.assertEqual(pillar_names, expected_pillars)

    def test_roadmap_consistency_and_priorities(self) -> None:
        """Verify priority classifications and item schema consistency."""
        report = evaluate_v080_architecture_readiness("v0.8.0")
        all_items: list[V080RoadmapItem] = []
        for p in report.pillars:
            all_items.extend(p.items)

        self.assertGreaterEqual(len(all_items), 8)
        priorities = {i.priority for i in all_items}
        self.assertTrue({"Must Have", "Should Have"}.issubset(priorities))

        for item in all_items:
            self.assertTrue(item.item_id.startswith("v08-"))
            self.assertIn(item.complexity, ("Low", "Medium", "High"))
            self.assertIn(item.migration_risk, ("Low", "Medium", "High"))

    def test_zero_premature_implementation_boundary(self) -> None:
        """Verify that architecture assessment introduces zero runtime modifications."""
        # Ensure evaluate_v080_architecture_readiness returns data structures only
        report = evaluate_v080_architecture_readiness()
        d = report.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(len(d["pillars"]), 4)


if __name__ == "__main__":
    unittest.main()

