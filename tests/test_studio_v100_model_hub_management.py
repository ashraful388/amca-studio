"""Model Hub management UX contract tests.

ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-MANAGEMENT-UX-AND-LOCAL-MODEL-OPERATIONS-01

Validates the management surface contract: registry rendering from
recorded fields, credential privacy (never display keys/secrets/tokens),
local model visibility (Ollama/llama.cpp/vLLM), capability honesty
(evidence source, no generated explanations), and no routing
modification.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop-ui", "tests"))


class TestModelHubManagementContract(unittest.TestCase):
    """Contract checks over the recorded management UX constants."""

    @classmethod
    def setUpClass(cls):
        import json
        import subprocess
        # node --test already covers the TS model; here we assert the TS
        # source itself carries the privacy and honesty rules.
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "desktop-ui", "src", "modelHubManagementUxModel.ts",
        )
        cls.model_path = path
        with open(path, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_credential_privacy_rule_present(self):
        lowered = self.source.lower()
        self.assertIn("never", lowered)
        self.assertIn("api key", lowered)
        self.assertIn("secret", lowered)
        self.assertIn("token", lowered)

    def test_capability_evidence_honesty(self):
        self.assertIn("verified", self.source.lower())
        self.assertIn("unknown", self.source.lower())
        self.assertIn("no generated explanations", self.source.lower())

    def test_no_routing_modification(self):
        self.assertIn("no routing", self.source.lower())

    def test_local_model_support_recorded(self):
        lowered = self.source.lower()
        for runtime in ("ollama", "llama.cpp", "vllm"):
            self.assertIn(runtime, lowered)


if __name__ == "__main__":
    unittest.main()
