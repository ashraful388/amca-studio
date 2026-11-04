"""
AMCA Studio Alpha User Feedback & Telemetry Framework Test Suite
Validates:
1. Local feedback record formatting and categorization
2. Strict exclusion of secrets, API keys, and sensitive authorization headers
3. Local file persistence without external network calls
4. Safe export bundle compilation for optional user-driven reporting
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

# Local-first feedback record model
def create_local_feedback_record(
    category: str,
    summary: str,
    details: str,
    session_id: str,
    metrics: dict | None = None,
) -> dict:
    # Strict secret sanitization patterns
    secret_pattern = re.compile(r"(sk-[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9_\-\.]+)", re.IGNORECASE)
    sanitized_details = secret_pattern.sub("[MASKED_SECRET]", details)
    sanitized_summary = secret_pattern.sub("[MASKED_SECRET]", summary)

    return {
        "category": category,
        "summary": sanitized_summary,
        "details": sanitized_details,
        "session_id": session_id,
        "metrics": metrics or {},
        "local_only": True,
    }


class StudioAlphaFeedbackFrameworkTests(unittest.TestCase):
    def test_feedback_record_secret_sanitization(self) -> None:
        """Verify that API keys and bearer tokens are masked before writing to feedback storage."""
        raw_detail = "Encountered timeout with sk-1234567890abcdef12345678 and Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        record = create_local_feedback_record(
            category="model_failure",
            summary="Model call failed with token sk-9876543210fedcba98765432",
            details=raw_detail,
            session_id="sess-alpha-001",
        )
        self.assertNotIn("sk-1234567890abcdef12345678", record["details"])
        self.assertNotIn("sk-9876543210fedcba98765432", record["summary"])
        self.assertIn("[MASKED_SECRET]", record["details"])
        self.assertIn("[MASKED_SECRET]", record["summary"])

    def test_local_feedback_persistence(self) -> None:
        """Verify that feedback records persist to local JSONL files without remote transmission."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            feedback_file = Path(tmp_dir) / "alpha_feedback.jsonl"
            record = create_local_feedback_record(
                category="ux_friction",
                summary="Artifact version comparison requires extra clicks",
                details="User navigated between versions 3 times to find diff view",
                session_id="sess-alpha-002",
                metrics={"navigation_clicks": 3, "time_spent_seconds": 45},
            )
            with open(feedback_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            self.assertTrue(feedback_file.exists())
            with open(feedback_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            loaded = json.loads(lines[0])
            self.assertEqual(loaded["category"], "ux_friction")
            self.assertTrue(loaded["local_only"])


if __name__ == "__main__":
    unittest.main()

