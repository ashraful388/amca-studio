"""v1.2.7: a previous focus's disagreements must not wedge every later message.

Owner case (installed AMCA, AMCA Project): ~18 critical objections recorded
while the workspace root still read "Not configured" survived every later
message, forced Joint + hard synchronization over the user's own mode choice
(``safety.canonical_conflict_to_joint``) and blocked synchronization for
requests that had nothing to do with them.
"""

from __future__ import annotations

import unittest

from amca.collaboration import (
    CollaborationSignals,
    DesktopCollaborationRouter,
    TaskShape,
)
from amca.domain import Disagreement, ProjectState, Severity
from amca.service import (
    SUPERSEDED_DISAGREEMENTS_ARTIFACT,
    _supersede_prior_focus_disagreements,
)


OLD_GOAL = "Open the AMCA dev source and check the project settings option."
NEW_GOAL = "Fix the attachment option from the '+' menu and allow pasting files."


def _wedged_state() -> ProjectState:
    state = ProjectState(project_id="studio-wedged", goal=OLD_GOAL)
    state.active_disagreements = [
        Disagreement(
            topic="workspace",
            description="Workspace root is reported as 'Not configured'.",
            severity=Severity.CRITICAL,
        ),
        Disagreement(
            topic="source",
            description="AMCA dev source location unknown.",
            severity=Severity.HIGH,
        ),
        Disagreement(
            topic="resolved-without-evidence",
            description="Resolved critical objection lacking evidence.",
            severity=Severity.CRITICAL,
            resolved=True,
            resolution="Resolved",
        ),
    ]
    return state


class StaleFocusDisagreementTests(unittest.TestCase):
    def test_wedged_state_forces_joint_hard_before_the_fix(self) -> None:
        state = _wedged_state()
        decision = DesktopCollaborationRouter().decide(
            TaskShape.from_goal(NEW_GOAL),
            CollaborationSignals.from_state(state),
            eligible_model_count=4,
        )
        self.assertEqual(decision.policy_rule, "safety.canonical_conflict_to_joint")

    def test_new_focus_retires_previous_disagreements_into_audit(self) -> None:
        state = _wedged_state()
        ids = [item.id for item in state.active_disagreements]
        count = _supersede_prior_focus_disagreements(state, NEW_GOAL, at="T")
        self.assertEqual(count, 3)
        self.assertEqual(state.active_disagreements, [])
        self.assertFalse(state.has_critical_objection)
        archive = state.artifacts[SUPERSEDED_DISAGREEMENTS_ARTIFACT]
        self.assertEqual([item["id"] for item in archive], ids)
        self.assertTrue(all(item["superseded_focus"] == OLD_GOAL for item in archive))
        self.assertTrue(all(item["superseded_at"] == "T" for item in archive))
        # The archived record survives a persistence round trip.
        restored = ProjectState.from_dict(state.to_dict())
        self.assertEqual(len(restored.artifacts[SUPERSEDED_DISAGREEMENTS_ARTIFACT]), 3)
        self.assertEqual(restored.active_disagreements, [])

    def test_new_focus_is_routed_on_its_own_merits(self) -> None:
        state = _wedged_state()
        _supersede_prior_focus_disagreements(state, NEW_GOAL)
        decision = DesktopCollaborationRouter().decide(
            TaskShape.from_goal(NEW_GOAL),
            CollaborationSignals.from_state(state),
            eligible_model_count=4,
        )
        self.assertNotEqual(decision.policy_rule, "safety.canonical_conflict_to_joint")

    def test_same_focus_keeps_its_live_objections(self) -> None:
        state = _wedged_state()
        count = _supersede_prior_focus_disagreements(state, OLD_GOAL)
        self.assertEqual(count, 0)
        self.assertEqual(len(state.active_disagreements), 3)
        self.assertTrue(state.has_critical_objection)
        self.assertNotIn(SUPERSEDED_DISAGREEMENTS_ARTIFACT, state.artifacts)

    def test_archive_is_bounded(self) -> None:
        state = ProjectState(project_id="studio-bounded", goal=OLD_GOAL)
        for turn in range(12):
            state.active_disagreements = [
                Disagreement(topic=f"t{turn}-{index}", severity=Severity.CRITICAL)
                for index in range(20)
            ]
            _supersede_prior_focus_disagreements(state, f"{NEW_GOAL} #{turn}")
            state.goal = f"{NEW_GOAL} #{turn}"
        self.assertEqual(len(state.artifacts[SUPERSEDED_DISAGREEMENTS_ARTIFACT]), 200)


if __name__ == "__main__":
    unittest.main()
