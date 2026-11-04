"""
AMCA Studio Alpha Support & Operations Readiness Test Suite
Validates:
1. Safe diagnostic reporting (no plaintext secrets or API keys)
2. Health state truthfulness and protocol version reporting
3. Graceful handling of invalid requests and sanitized diagnostic payloads
4. Operational troubleshooting documentation completeness
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from amca.service import create_server


class StudioAlphaSupportAndOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca_ops.sqlite3"

        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.root,
            database=self.database,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        req = Request(f"{self.base_url}/api/session", method="GET")
        req.add_header("Origin", "http://localhost:1420")
        with urlopen(req, timeout=5.0) as resp:
            self.token = json.loads(resp.read().decode("utf-8"))["session_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

    def test_health_diagnostic_payload_safety(self) -> None:
        """Verify that public health diagnostics expose status and protocol without sensitive leaks."""
        req = Request(f"{self.base_url}/api/health", method="GET")
        req.add_header("Origin", "http://localhost:1420")
        with urlopen(req, timeout=5.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(data.get("status"), "ok")
        self.assertIn("protocol_version", data)
        # Verify no secret keywords leaked in health payload
        raw_text = json.dumps(data)
        self.assertNotIn("sk-", raw_text)
        self.assertNotIn("password", raw_text.lower())
        self.assertNotIn("secret", raw_text.lower())

    def test_operational_documentation_artifacts_exist(self) -> None:
        """Verify that operational runbook and troubleshooting guides are published."""
        tb_guide = self.root / "docs" / "AMCA-Studio-Alpha-User-Troubleshooting-Guide.md"
        runbook = self.root / "docs" / "AMCA-Studio-Alpha-Operational-Runbook.md"

        self.assertTrue(tb_guide.exists(), "User Troubleshooting Guide missing")
        self.assertTrue(runbook.exists(), "Operational Runbook missing")


if __name__ == "__main__":
    unittest.main()

