"""Nano Agents integration fixture tests.

Verifies the /api/nano-agents payload contract via deterministic
fixtures. Proves the backend can deliver bounded operational metadata
without raw prompts, CoT, or credentials.
"""

import unittest

from test_nano_agents_integration import (
    build_frontend_payload,
    assert_privacy_compliance,
)


class NanoFrontendPayloadTests(unittest.TestCase):
    """Verify the payload contract from /api/nano-agents."""

    def setUp(self):
        self.payload = build_frontend_payload()

    def test_payload_has_assignments(self):
        self.assertIn("assignments", self.payload)
        self.assertEqual(len(self.payload["assignments"]), 2)

    def test_assignment_has_nano_id(self):
        a = self.payload["assignments"][0]
        self.assertIn("nano_id", a)
        self.assertEqual(a["nano_id"], "nano:test-1")

    def test_assignment_has_root_parent_relationship(self):
        a1 = self.payload["assignments"][0]
        a2 = self.payload["assignments"][1]
        # a1's parent is the root task
        self.assertEqual(a1["parent_task_id"], "task:root")
        # a2's parent is a1
        self.assertEqual(a2["parent_task_id"], a1["task_id"])

    def test_assignment_has_bounded_label(self):
        a = self.payload["assignments"][0]
        self.assertIn("purpose", a)
        self.assertIsInstance(a["purpose"], str)
        self.assertGreater(len(a["purpose"]), 0)

    def test_statuses_present(self):
        self.assertIn("statuses", self.payload)
        self.assertIn("nano:test-1", self.payload["statuses"])
        self.assertIn("nano:test-2", self.payload["statuses"])

    def test_results_have_disposition(self):
        r = self.payload["results"]["nano:test-1"]
        self.assertIn("disposition", r)
        self.assertEqual(r["disposition"], "complete")

    def test_cancellation_recorded(self):
        self.assertIn("cancelled", self.payload)
        self.assertIn("nano:test-2", self.payload["cancelled"])

    def test_envelope_has_governance_bounds(self):
        e = self.payload["envelope"]
        self.assertIn("max_api_cost_usd", e)
        self.assertEqual(e["max_api_cost_usd"], 5.0)
        self.assertIn("max_nano_tasks", e)
        self.assertEqual(e["max_nano_tasks"], 128)

    def test_messages_have_bounded_metadata(self):
        m = self.payload["messages"][0]
        self.assertIn("sender_id", m)
        self.assertIn("recipient_id", m)
        self.assertIn("kind", m)
        # No raw prompt content
        self.assertNotIn("prompt", m)
        self.assertNotIn("raw_response", m)


class NanoPrivacyComplianceTests(unittest.TestCase):
    """Verify the payload is privacy-compliant (no raw model content)."""

    def setUp(self):
        self.payload = build_frontend_payload()

    def test_no_raw_prompts(self):
        is_compliant, violations = assert_privacy_compliance(self.payload)
        self.assertTrue(is_compliant, f"Privacy violations: {violations}")

    def test_no_credentials(self):
        import json
        serialized = json.dumps(self.payload).lower()
        for forbidden in ["api_key", "session_token", "authorization", "secret", "credential"]:
            self.assertNotIn(forbidden, serialized)

    def test_no_coT_or_reasoning(self):
        import json
        serialized = json.dumps(self.payload).lower()
        for forbidden in ["chain_of_thought", "raw_response", "raw_output", "thinking"]:
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
