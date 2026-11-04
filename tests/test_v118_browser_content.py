"""v1.1.8 Work Order 02 Priority 8 — governed browser reading and extraction.

The governed browser captures the DOM of the exact approved URL.  These tests
cover turning that capture into usable content, and — more importantly — pin
the security property that reading cannot widen the approval.
"""

from __future__ import annotations

import unittest

from amca.browser_content import MAX_LINKS, MAX_TEXT_CHARS, extract_page

PAGE = """<html><head><title>Quarterly Report</title>
<style>body{color:red}</style></head>
<body>
<h1>Overview</h1>
<p>Revenue rose in the third quarter.</p>
<script>alert('should never appear')</script>
<h2>Details</h2>
<ul><li>Alpha</li><li>Beta</li></ul>
<p>See <a href="https://example.test/docs">the documentation</a> and
<a href="/local/page">a local page</a>.</p>
<noscript>enable javascript</noscript>
</body></html>"""


class ExtractionTests(unittest.TestCase):

    def test_title_headings_text_and_links_are_extracted(self) -> None:
        page = extract_page(PAGE)
        self.assertEqual(page["title"], "Quarterly Report")
        self.assertIn("Revenue rose in the third quarter.", page["text"])
        self.assertEqual(
            [item["text"] for item in page["headings"]], ["Overview", "Details"]
        )
        self.assertEqual(
            [item["href"] for item in page["links"]],
            ["https://example.test/docs", "/local/page"],
        )

    def test_script_and_style_content_is_never_in_the_text(self) -> None:
        page = extract_page(PAGE)
        self.assertNotIn("should never appear", page["text"])
        self.assertNotIn("color:red", page["text"])
        self.assertNotIn("enable javascript", page["text"])

    def test_the_title_is_not_duplicated_into_the_body(self) -> None:
        page = extract_page(PAGE)
        self.assertNotIn("Quarterly Report", page["text"])

    def test_link_text_is_captured(self) -> None:
        page = extract_page(PAGE)
        texts = [item["text"] for item in page["links"]]
        self.assertIn("the documentation", texts)
        self.assertIn("a local page", texts)

    def test_an_empty_document_yields_an_empty_page_not_an_error(self) -> None:
        page = extract_page("")
        self.assertEqual(page["text"], "")
        self.assertEqual(page["text_chars"], 0)
        self.assertEqual(page["headings"], [])
        self.assertEqual(page["links"], [])

    def test_malformed_markup_is_tolerated(self) -> None:
        page = extract_page("<p>unclosed <b>bold <div>block")
        self.assertIn("bold", page["text"])
        self.assertIn("block", page["text"])

    def test_an_unclosed_script_does_not_leak_into_the_text(self) -> None:
        page = extract_page("<p>visible</p><script>var x = 1;")
        self.assertIn("visible", page["text"])
        self.assertNotIn("var x", page["text"])

    def test_text_is_bounded(self) -> None:
        page = extract_page("<p>" + ("word " * 40000) + "</p>")
        self.assertLessEqual(page["text_chars"], MAX_TEXT_CHARS)
        self.assertTrue(page["text_truncated"])

    def test_links_are_bounded(self) -> None:
        dom = "<p>" + "".join(f'<a href="/{i}">link {i}</a>' for i in range(MAX_LINKS + 50)) + "</p>"
        page = extract_page(dom)
        self.assertLessEqual(page["link_count"], MAX_LINKS)

    def test_entities_are_decoded(self) -> None:
        page = extract_page("<p>AT&amp;T said &quot;hello&quot; &lt;ok&gt;</p>")
        self.assertIn('AT&T said "hello" <ok>', page["text"])

    def test_html_in_text_is_not_interpreted(self) -> None:
        # Whatever the page contained is reported as text; nothing is executed
        # and nothing is re-parsed into markup.
        page = extract_page("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
        self.assertIn("<script>alert(1)</script>", page["text"])

    def test_whitespace_is_collapsed_without_losing_paragraphs(self) -> None:
        page = extract_page("<p>one   two</p>\n\n\n<p>three</p>")
        self.assertIn("one two", page["text"])
        self.assertIn("three", page["text"])
        self.assertNotIn("   ", page["text"])


class BrowserOperationSurfaceTests(unittest.TestCase):
    """read and extract must exist and must not widen the approval."""

    def test_the_governed_browser_accepts_read_and_extract(self) -> None:
        import inspect

        from amca import local_tools

        source = inspect.getsource(local_tools.LocalToolExecutor.execute_browser)
        self.assertIn('"read"', source)
        self.assertIn('"extract"', source)

    def test_reading_does_not_fetch_anything(self) -> None:
        # Both operations work on the captured DOM only: the module has no
        # network import at all.
        import amca.browser_content as module

        source = inspect_source(module)
        for forbidden in ("urllib", "requests", "socket", "http.client", "subprocess"):
            with self.subTest(module=forbidden):
                self.assertNotIn(forbidden, source)


def inspect_source(module) -> str:
    import inspect

    return inspect.getsource(module)


if __name__ == "__main__":
    unittest.main()
