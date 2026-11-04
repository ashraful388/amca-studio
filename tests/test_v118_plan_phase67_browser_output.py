"""AMCA Studio v1.1.8 Final Build Plan - Phases 6 and 7 evidence.

Phase 6 requires Chrome **and Firefox** support, and clear errors for
unsupported formats.

Phase 7 requires generated files to appear in chat, correct filenames, and
"no silent failure" on download.
"""

from __future__ import annotations

import unittest

from amca.artifacts import (
    AUTHORABLE_TYPES,
    parse_artifact_blocks,
    strip_artifact_blocks,
)
from amca.local_tools import GOVERNED_BROWSERS


class BrowserRegistryTests(unittest.TestCase):
    """The plan names Chrome and Firefox explicitly."""

    def test_chrome_is_governed(self) -> None:
        self.assertIn("chrome", GOVERNED_BROWSERS)
        self.assertTrue(GOVERNED_BROWSERS["chrome"]["candidates"])

    def test_firefox_is_governed(self) -> None:
        self.assertIn("firefox", GOVERNED_BROWSERS)
        self.assertTrue(GOVERNED_BROWSERS["firefox"]["candidates"])

    def test_edge_remains_governed(self) -> None:
        self.assertIn("edge", GOVERNED_BROWSERS)

    def test_every_browser_is_chosen_by_name_not_path(self) -> None:
        """The approval scope pins a resolved executable, so the registry must
        resolve from known install paths rather than accepting a user path."""
        for key, entry in GOVERNED_BROWSERS.items():
            self.assertIsInstance(key, str)
            self.assertTrue(entry.get("display_name"), key)
            for candidate in entry["candidates"]:
                self.assertTrue(str(candidate).lower().endswith(".exe"), candidate)

    def test_firefox_does_not_claim_a_dom_dump(self) -> None:
        """Firefox cannot write a DOM dump to stdout; claiming it would pass an
        argument the browser rejects."""
        self.assertFalse(GOVERNED_BROWSERS["firefox"].get("supports_dom_dump", True))
        self.assertTrue(GOVERNED_BROWSERS["chrome"].get("supports_dom_dump", True))
        self.assertTrue(GOVERNED_BROWSERS["edge"].get("supports_dom_dump", True))


class ArtifactFenceTests(unittest.TestCase):
    """The fence is an instruction to AMCA, not prose the user should read."""

    TEXT = (
        "Here is the report you asked for.\n\n"
        "```artifact docx title=Quarterly Report\n"
        "# Quarterly Report\nRevenue rose.\n"
        "```\n\n"
        "Let me know if you want changes."
    )

    def test_a_docx_fence_is_extracted(self) -> None:
        blocks = parse_artifact_blocks(self.TEXT)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["title"], "Quarterly Report")

    def test_the_fence_is_removed_from_the_published_text(self) -> None:
        stripped = strip_artifact_blocks(self.TEXT)
        self.assertNotIn("```artifact", stripped)
        self.assertNotIn("Revenue rose.", stripped)
        self.assertIn("Here is the report you asked for.", stripped)
        self.assertIn("Let me know if you want changes.", stripped)

    def test_an_ordinary_code_block_is_left_alone(self) -> None:
        plain = "Answer.\n\n```python\nprint(1)\n```\n\nDone."
        self.assertEqual(strip_artifact_blocks(plain), plain)

    def test_text_without_a_fence_is_unchanged(self) -> None:
        plain = "Just an answer with `inline code`."
        self.assertEqual(strip_artifact_blocks(plain), plain)

    def test_a_bib_fence_is_removed_too(self) -> None:
        text = "Sources:\n\n```bib\n@book{a, title={T}}\n```\n\nDone."
        self.assertNotIn("@book", strip_artifact_blocks(text))

    def test_docx_is_authorable(self) -> None:
        """The plan lists DOCX among the supported output formats."""
        self.assertIn("docx", AUTHORABLE_TYPES)

    def test_pdf_is_not_authorable(self) -> None:
        """There is no PDF pipeline, so accepting it would promise a file that
        cannot be produced.  The honest answer is a clear refusal."""
        self.assertNotIn("pdf", AUTHORABLE_TYPES)


if __name__ == "__main__":
    unittest.main()
