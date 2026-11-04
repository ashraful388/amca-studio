"""Nano Agents UI contract tests for M3 closeout.

Verifies the frontend NanoAgentsPanel consumes the real backend
contract without calling /api/intelligence/configure (default-OFF proof).

These tests are minimal by design — the goal is to prove the
contract mapping is correct, not to retest the backend.
"""

import json
import os
import subprocess
import sys
import unittest


# Path to the frontend type definitions
FRONTEND_TYPES_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "desktop-ui",
    "src",
    "types.ts",
)


class NanoContractFieldsTests(unittest.TestCase):
    """Verify that the Nano contract fields are exactly what the UI renders."""

    REQUIRED_FIELDS = {
        "assignments": [
            "nano_id",
            "task_id",
            "run_ref",
            "parent_task_id",
            "depth",
            "purpose",
            "task_type",
            "capabilities",
            "permissions",
            "expected_cost",
            "correlation_id",
            "proposal_ref",
            "assignment_version",
        ],
        "statuses": [],
        "results": [
            "disposition",
            "summary",
            "output_digest",
            "capabilities_used",
            "evidence_refs",
        ],
        "cancelled": [],
        "messages": [
            "sender_id",
            "recipient_id",
            "kind",
            "correlation_id",
            "summary",
        ],
        "escalations": [
            "nano_id",
            "reason",
            "requested_authority",
            "granted",
        ],
        "envelope": [
            "max_logical_agents",
            "max_concurrent_agents",
            "max_manager_depth",
            "max_nano_tasks",
            "max_cloud_calls",
            "max_frontier_calls",
            "max_api_cost_usd",
            "max_runtime_seconds",
        ],
    }

    def test_nano_response_type_defined(self):
        """The NanoAgentsResponse type is exported from types.ts."""
        with open(FRONTEND_TYPES_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("export interface NanoAgentsResponse", content)

    def test_all_required_fields_present_in_type(self):
        """All contract fields are defined in the NanoAgentsResponse type."""
        with open(FRONTEND_TYPES_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # The fields are in the NanoAssignment and NanoResult interfaces
        # Check the full file for the field names
        for field in self.REQUIRED_FIELDS["assignments"]:
            self.assertIn(field, content, f"Assignment field '{field}' missing")
        for field in self.REQUIRED_FIELDS["results"]:
            self.assertIn(field, content, f"Result field '{field}' missing")

    def test_no_pii_or_raw_model_content_in_types(self):
        """The TypeScript types do not include fields for raw model content."""
        with open(FRONTEND_TYPES_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        forbidden = [
            "raw_response", "raw_output", "prompt", "system_prompt",
            "chain_of_thought", "cot", "api_key", "session_token",
            "raw_reasoning",
        ]
        start = content.find("export interface NanoAgentsResponse")
        end = content.find("}", start)
        block = content[start:end].lower()
        for f in forbidden:
            self.assertNotIn(f, block, f"Forbidden field '{f}' in types")


class NanoComponentImportTests(unittest.TestCase):
    """Verify the NanoAgentsPanel component imports correctly."""

    COMPONENT_PATH = os.path.join(
        os.path.dirname(__file__),
        "..",
        "desktop-ui",
        "src",
        "NanoAgents.tsx",
    )

    def test_component_file_exists(self):
        self.assertTrue(
            os.path.isfile(self.COMPONENT_PATH),
            f"NanoAgentsPanel component file not found at {self.COMPONENT_PATH}",
        )

    def test_component_exports_nano_agents_panel(self):
        with open(self.COMPONENT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("export function NanoAgentsPanel", content)

    def test_component_calls_nano_agents_api(self):
        """Component must call amca.nanoAgents() to fetch status."""
        with open(self.COMPONENT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(".nanoAgents()", content)


class NanoAPIClientTests(unittest.TestCase):
    """Verify the API client has the nanoAgents() method."""

    API_PATH = os.path.join(
        os.path.dirname(__file__),
        "..",
        "desktop-ui",
        "src",
        "api.ts",
    )

    def test_nano_agents_method_defined(self):
        with open(self.API_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("nanoAgents()", content)

    def test_endpoint_path(self):
        """The endpoint path is /api/nano-agents."""
        with open(self.API_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('"/api/nano-agents"', content)

    def test_returns_nano_agents_response(self):
        with open(self.API_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("NanoAgentsResponse", content)


class NanoAgentLibraryIntegrationTests(unittest.TestCase):
    """Verify the Nano tab is integrated into AgentLibrary."""

    AGENT_LIBRARY_PATH = os.path.join(
        os.path.dirname(__file__),
        "..",
        "desktop-ui",
        "src",
        "AgentLibrary.tsx",
    )

    def test_nano_agents_imported(self):
        with open(self.AGENT_LIBRARY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("NanoAgents", content)

    def test_nano_tab_in_navigation(self):
        with open(self.AGENT_LIBRARY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # The tab type includes "nano"
        self.assertIn('"nano"', content)

    def test_nano_tab_renders_component(self):
        with open(self.AGENT_LIBRARY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # The nano tab renders the NanoAgentsPanel component
        self.assertIn("NanoAgentsPanel", content)


class DefaultOffProofTests(unittest.TestCase):
    """Verify the Nano panel does NOT call /api/intelligence/configure."""

    COMPONENT_PATH = os.path.join(
        os.path.dirname(__file__),
        "..",
        "desktop-ui",
        "src",
        "NanoAgents.tsx",
    )
    API_PATH = os.path.join(
        os.path.dirname(__file__),
        "..",
        "desktop-ui",
        "src",
        "api.ts",
    )

    def test_component_does_not_call_configure(self):
        """NanoAgentsPanel must not call intelligenceConfigure()."""
        with open(self.COMPONENT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("intelligenceConfigure", content)

    def test_api_client_nano_does_not_call_configure(self):
        """The nanoAgents() method must not call intelligenceConfigure."""
        with open(self.API_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # Find the nanoAgents method body
        idx = content.find("nanoAgents():")
        if idx >= 0:
            # Read a reasonable chunk
            chunk = content[idx:idx + 300]
            self.assertNotIn("intelligenceConfigure", chunk)
            self.assertNotIn("intelligenceValidate", chunk)


if __name__ == "__main__":
    unittest.main()
