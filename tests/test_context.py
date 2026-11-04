from __future__ import annotations

import unittest

from amca.context import ContextBuilder
from amca.domain import GoalContract, GoalCriterion, ProjectState


class ContextBuilderTests(unittest.TestCase):
    def test_context_is_structured_and_bounded(self) -> None:
        state = ProjectState(
            project_id="p1",
            goal="Prove the claim",
            goal_contract=GoalContract(
                goal="Prove the claim",
                criteria=[GoalCriterion(description="Proof verified")],
            ),
            current_focus="Check the boundary case",
            accepted_facts=[f"fact-{number}" for number in range(20)],
        )
        packet = ContextBuilder(max_items_per_section=3).build(state)

        self.assertEqual(packet.state_version, 0)
        self.assertIn("fact-19", packet.canonical_state)
        self.assertNotIn("fact-0\"", packet.canonical_state)
        self.assertIn("Check the boundary case", packet.as_prompt())


if __name__ == "__main__":
    unittest.main()
