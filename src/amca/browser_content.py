"""Read and extract content from an already-approved browser page.

The governed browser captures the DOM of the *exact* URL that was approved.
This module turns that captured document into something a person or a model can
actually use — a title, readable text, headings and links.

Two deliberate properties:

* **No new access.** Everything here works on a DOM that was already captured
  from an already-approved URL.  Nothing fetches anything.  Navigation to a
  different page still requires a new approval; widening an existing approval
  is not something this module can do, and must not become possible.
* **No HTML is trusted.** Parsing uses the standard library's tolerant parser
  and discards script, style and template content rather than rendering it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

#: Elements whose text is not page content.  ``head`` is deliberately NOT in
#: this set: it carries ``<title>``, which is page content worth reporting.
_SKIP_ELEMENTS = frozenset({"script", "style", "template", "noscript", "svg"})

#: Elements that imply a line break in the readable text.
_BLOCK_ELEMENTS = frozenset({
    "p", "div", "section", "article", "header", "footer", "main", "aside", "nav",
    "li", "tr", "br", "hr", "blockquote", "pre", "figure", "figcaption", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "dl", "dt", "dd",
})

#: Bounds so a pathological page cannot exhaust memory or a model's context.
MAX_TEXT_CHARS = 40_000
MAX_LINKS = 200
MAX_HEADINGS = 100


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self._text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []

    # -- text collection -------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_ELEMENTS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = tag
            self._heading_parts = []
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._anchor_href = href
            self._anchor_parts = []
        # Only `br` and `hr` break on open; other block elements break when they
        # close, which keeps one newline per block instead of two.
        if tag in {"br", "hr"}:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if tag == self._heading_tag:
            text = _collapse(" ".join(self._heading_parts))
            if text and len(self.headings) < MAX_HEADINGS:
                self.headings.append({"level": tag, "text": text[:500]})
            self._heading_tag = None
            self._heading_parts = []
        if tag == "a" and self._anchor_href is not None:
            text = _collapse(" ".join(self._anchor_parts))
            if self._anchor_href and len(self.links) < MAX_LINKS:
                self.links.append({"href": self._anchor_href[:2000], "text": text[:300]})
            self._anchor_href = None
            self._anchor_parts = []
        if tag in _BLOCK_ELEMENTS:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            # The title is reported separately; it is not part of the body text.
            self.title += data
            return
        if self._heading_tag:
            self._heading_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)
        self._text_parts.append(data)

    def readable_text(self) -> str:
        raw = "".join(self._text_parts)
        lines = [_collapse(line) for line in raw.split("\n")]
        kept: list[str] = []
        for line in lines:
            if line:
                kept.append(line)
            elif kept and kept[-1] != "":
                kept.append("")
        text = "\n".join(kept).strip()
        if len(text) > MAX_TEXT_CHARS:
            return text[:MAX_TEXT_CHARS]
        return text


def _collapse(value: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def extract_page(dom: str) -> dict[str, Any]:
    """Title, readable text, headings and links from a captured DOM.

    Never raises on malformed markup: a browser's captured DOM is usually
    well-formed, but a truncated capture is not, and a parser error must not
    turn a readable page into a failure.
    """

    parser = _Extractor()
    try:
        parser.feed(str(dom or ""))
        parser.close()
    except Exception:  # noqa: BLE001 - a tolerant parse, never a hard failure
        pass

    text = parser.readable_text()
    return {
        "title": _collapse(parser.title)[:500],
        "text": text,
        "text_chars": len(text),
        "text_truncated": len(text) >= MAX_TEXT_CHARS,
        "headings": parser.headings,
        "links": parser.links,
        "link_count": len(parser.links),
    }


__all__ = ["MAX_LINKS", "MAX_TEXT_CHARS", "extract_page"]
