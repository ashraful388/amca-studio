"""Key-free scholarly metadata search. No requests occur until explicitly called.

Results are bibliographic metadata/abstracts, NOT evidence of full-text review.
The transport reuses the acquisition client's pinned, proxy-free urllib opener.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any, Mapping

from .web_acquisition import (
    MAX_QUERY_LENGTH, MAX_REDIRECTS, MAX_RESPONSE_BYTES,
    DEFAULT_READ_TIMEOUT, UrlRejected, _proxy_free_opener,
    resolve_public_addresses, validate_public_url,
)


# Transient provider throttling: arXiv's export gateway returns 406/429/503
# for a short window before accepting the identical request again, so the
# status alone must not be treated as a permanent client error. The retry is
# bounded and never applied to any other failure.
_TRANSIENT_STATUS = frozenset({406, 429, 503})
_TRANSIENT_ATTEMPTS = 3
# arXiv's API manual asks for at least three seconds between requests, so the
# retry spacing matches the documented polite interval.
_TRANSIENT_DELAYS = (3.0, 6.0)


def _request_text(url: str, accept: str, *, opener=None, sleep=None) -> str:
    """Bounded UTF-8 GET; every redirect is public-address validated/pinned.

    An injected single-hop opener is for offline tests only. Compressed replies
    are rejected rather than expanded without a bound. Never follows returned
    article URLs automatically. Transient provider throttling is retried a
    bounded number of times; every other failure is reported immediately.
    """
    if sleep is None:
        import time as _time

        sleep = _time.sleep
    for attempt in range(_TRANSIENT_ATTEMPTS):
        try:
            return _request_once(url, accept, opener=opener)
        except _TransientStatus as error:
            if attempt == _TRANSIENT_ATTEMPTS - 1:
                raise ValueError(f"scholarly HTTP status {error.status}") from None
            sleep(_TRANSIENT_DELAYS[min(attempt, len(_TRANSIENT_DELAYS) - 1)])
    raise ValueError("scholarly transient retry bound exceeded")


class _TransientStatus(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _request_once(url: str, accept: str, *, opener=None) -> str:
    for hop in range(MAX_REDIRECTS + 1):
        validate_public_url(url)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https":
            raise UrlRejected("scholarly transport requires HTTPS")
        addresses = resolve_public_addresses(parsed.hostname)
        transport = opener or _proxy_free_opener(addresses[0], https=True)
        request = urllib.request.Request(url, headers={
            "Accept": accept, "Accept-Encoding": "identity",
            "User-Agent": "AMCA-Scholarly/1.0",
        })
        with transport.open(request, timeout=DEFAULT_READ_TIMEOUT) as response:
            status = response.status
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location or hop == MAX_REDIRECTS:
                    raise ValueError("scholarly redirect bound exceeded or missing location")
                url = urllib.parse.urljoin(url, location)
                continue
            if status in _TRANSIENT_STATUS:
                raise _TransientStatus(status)
            if status != 200:
                raise ValueError(f"scholarly HTTP status {status}")
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ValueError("compressed scholarly response not accepted")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("scholarly response exceeds byte bound")
            return raw.decode("utf-8-sig")
    raise ValueError("scholarly redirect bound exceeded")


_LEADING_INSTRUCTION = re.compile(
    r"^\s*(?:please\s+|kindly\s+|now\s+|then\s+|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+|"
    r"help\s+me\s+|let'?s\s+|go\s+ahead\s+and\s+|"
    r"(?:write|prepare|produce|create|draft|do|give\s+me|find|search\s+for|look\s+up|"
    r"get|collect|gather|summarize|summarise|review|compile|list)\s+"
    r"(?:me\s+)?(?:a|an|the|some|my)?\s*)",
    re.I,
)

_CLAUSE_BOUNDARY = re.compile(
    r"(?:\s*[,;:]?\s*)(?:"
    r"cover(?:ing)?\b|includ(?:e|ing)\b|discuss(?:ing)?\b|compar(?:e|ing|isons?)\b|"
    r"analyz(?:e|ing)\b|analys(?:e|ing)\b|summariz(?:e|ing)\b|summaris(?:e|ing)\b|"
    r"explain(?:ing)?\b|describ(?:e|ing)\b|with\s+citations?\b|with\s+references?\b|"
    r"and\s+a\s+separate\b|and\s+the\s+bibtex\b|bibtex\b|that\s+covers?\b|"
    r"make\s+sure\b|ensure\b|do\s+not\b|don'?t\b|for\s+each\b|"
    r"research\s+gaps?\b|future\s+directions?\b|limitations?\b|"
    r"methodology\b|methodologies\b|contributions?\b"
    r")",
    re.I,
)

_COUNT_PATTERNS = (
    re.compile(
        r"\b(\d{1,2})\s+(?:[a-z]+\s+){0,2}"
        r"(?:papers?|studies|articles?|works|publications?|sources?|results?)\b",
        re.I,
    ),
    re.compile(r"\b(?:top|latest|first|last|at\s+least|about|around|approximately)\s+(\d{1,2})\b", re.I),
)


# Generic research vocabulary that describes the deliverable rather than the
# topic.  Providers match query terms against titles/abstracts, so keeping
# these ("comprehensive literature review of the latest ... papers") pulls in
# unrelated reviews that merely share the words "comprehensive" and "review".
_GENERIC_RESEARCH_TOKENS = frozenset({
    "literature", "review", "reviews", "survey", "surveys", "overview",
    "papers", "paper", "studies", "study", "articles", "article",
    "publications", "publication", "sources", "source", "results", "result",
    "latest", "recent", "recently", "newest", "new", "comprehensive",
    "complete", "detailed", "brief", "scholarly", "academic", "peer-reviewed",
    "peer", "reviewed", "state-of-the-art", "state", "art", "bibliography",
    "bibtex", "citations", "citation", "references", "reference", "findings",
})

_EDGE_STOPWORDS = re.compile(
    r"^(?:of|on|about|for|in|into|and|the|a|an|to|with|from|by|at|is|are)\s+"
    r"|\s+(?:of|on|about|for|in|into|and|the|a|an|to|with|from|by|at)$",
    re.I,
)


def _topical_core(text: str) -> str:
    """Drop deliverable vocabulary, keeping the topic the user asked about."""

    tokens = [
        token for token in text.split()
        if token.casefold().strip(",;:.-") not in _GENERIC_RESEARCH_TOKENS
    ]
    core = _text(" ".join(tokens))
    # "top 5 papers on X" carries the count, not the topic.
    core = re.sub(r"^(?:top|first|last|latest|best)\s+\d{1,2}\s+", "", core, flags=re.I)
    core = re.sub(r"^\d{1,2}\s+", "", core)
    while core:
        stripped = _EDGE_STOPWORDS.sub("", core).strip(" ,;:.-")
        if stripped == core:
            break
        core = stripped
    return core


def research_query_from_goal(goal: str, *, limit: int = 180) -> str:
    """Extract the topical search phrase from a request.

    A full instruction ("write a literature review covering methods, results,
    comparisons, gaps and future directions with citations and BibTeX") is a
    poor bibliographic query: providers match it against titles and abstracts
    and return unrelated records.  This reduces the request to its topical
    noun phrase.  It is deliberately lexical and deterministic - no model
    call - and falls back to the trimmed original when cleaning empties it.
    """
    text = _text(goal)
    if not text:
        return ""
    text = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", text)
    # Stacked scaffolding ("Please write a ...") needs more than one pass.
    while True:
        stripped = _LEADING_INSTRUCTION.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    # The topical ask is in the first sentence; later sentences usually add
    # deliverable requirements ("Retrieve 5 papers and cite them").
    first_sentence = re.split(r"(?<=[.?!])\s+", text, maxsplit=1)[0]
    if first_sentence.strip():
        text = first_sentence
    boundary = _CLAUSE_BOUNDARY.search(text)
    if boundary and boundary.start() > 0:
        text = text[: boundary.start()]
    text = text.strip(" \t\r\n,;:.-")
    text = re.sub(r"^(?:and|a|an|the|of|on|about|into|for)\s+", "", text, flags=re.I)
    text = _text(text)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip(" ,;:.-")
    if not text:
        return _text(goal)[:limit].strip()
    # The deliverable words are only dropped when a topic survives without them.
    return _topical_core(text) or text


def requested_result_count(goal: str, *, default: int = 8, maximum: int = 25) -> int:
    """Honor an explicit count in the request; otherwise use the default."""

    for pattern in _COUNT_PATTERNS:
        match = pattern.search(goal or "")
        if match:
            value = int(match.group(1))
            if 1 <= value <= maximum:
                return value
    return default


def validate_search(query: str, max_results: int) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must contain 1-{MAX_QUERY_LENGTH} characters")
    if type(max_results) is not int or not 1 <= max_results <= 50:
        raise ValueError("max_results must be an integer between 1 and 50")
    return query.strip()


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _result(provider: str, query: str, rank: int, **metadata) -> dict[str, Any]:
    return {"provider": provider, "source": provider, "query": query,
            "rank": rank, "observed_at": datetime.now(UTC).isoformat(),
            **metadata}


def arxiv_search(query: str, *, max_results: int = 10, opener=None) -> list[dict[str, Any]]:
    query = validate_search(query, max_results)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": "all:" + query, "start": 0, "max_results": max_results,
        "sortBy": "submittedDate", "sortOrder": "descending",
    })
    text = _request_text(url, "application/atom+xml", opener=opener)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations not accepted")
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
    results = []
    for entry in root.findall("a:entry", ns)[:max_results]:
        published = _text(entry.findtext("a:published", namespaces=ns))
        url = _text(entry.findtext("a:id", namespaces=ns))
        if not url:
            continue
        results.append(_result("arxiv", query, len(results) + 1,
            title=_text(entry.findtext("a:title", namespaces=ns)), url=url,
            authors=[_text(a.findtext("a:name", namespaces=ns)) for a in entry.findall("a:author", ns)],
            snippet=_text(entry.findtext("a:summary", namespaces=ns)), published=published,
            year=int(published[:4]) if published[:4].isdigit() else None,
            doi=_text(entry.findtext("ar:doi", namespaces=ns))))
    return results


def crossref_search(query: str, *, max_results: int = 10, opener=None) -> list[dict[str, Any]]:
    query = validate_search(query, max_results)
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query.bibliographic": query, "rows": max_results,
    })
    items = json.loads(_request_text(url, "application/json", opener=opener))["message"]["items"]
    results = []
    for item in items[:max_results]:
        doi = _text(item.get("DOI"))
        url = item.get("URL") or ("https://doi.org/" + doi if doi else "")
        if not url:
            continue
        dates = item.get("published") or item.get("published-print") or item.get("published-online") or item.get("issued") or {}
        parts = dates.get("date-parts") or [[]]
        title = item.get("title") or []
        results.append(_result("crossref", query, len(results) + 1,
            title=_text(title[0] if isinstance(title, list) and title else title), url=url,
            authors=[_text(" ".join(filter(None, [a.get("given"), a.get("family")])) or a.get("name", "")) for a in item.get("author", [])],
            year=parts[0][0] if parts and parts[0] else None, doi=doi,
            snippet=_text(re.sub(r"<[^>]+>", " ", item.get("abstract") or ""))))
    return results


def _openalex_abstract(inverted: Any) -> str:
    """Rebuild the plain abstract from OpenAlex's inverted index (bounded)."""

    if not isinstance(inverted, Mapping):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                positioned.append((position, str(word)))
    positioned.sort()
    return " ".join(word for _, word in positioned[:400])


def openalex_search(query: str, *, max_results: int = 10, opener=None) -> list[dict[str, Any]]:
    query = validate_search(query, max_results)
    # title_and_abstract.search is a topic filter; a bare `search` matches any
    # term across the whole record and returns unrelated newest works.
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode({
        "filter": f"title_and_abstract.search:{query}",
        "per-page": max_results, "sort": "publication_date:desc",
    })
    items = json.loads(_request_text(url, "application/json", opener=opener)).get("results") or []
    current_year = datetime.now(UTC).year
    results = []
    for item in items[:max_results]:
        if not isinstance(item, Mapping):
            continue
        doi = _doi(_text(item.get("doi")))
        landing = _text(item.get("id"))
        url = ("https://doi.org/" + doi) if doi else landing
        if not url:
            continue
        authors = [
            _text((entry.get("author") or {}).get("display_name"))
            for entry in item.get("authorships") or []
            if isinstance(entry, Mapping)
        ]
        year = item.get("publication_year")
        # OpenAlex carries a few mis-dated records; a work cannot post-date now.
        if isinstance(year, int) and year > current_year:
            continue
        results.append(_result("openalex", query, len(results) + 1,
            title=_text(item.get("title")), url=url,
            authors=[name for name in authors if name],
            year=year if isinstance(year, int) else None, doi=doi,
            snippet=_openalex_abstract(item.get("abstract_inverted_index"))))
    return results


def _doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).casefold()


def crossref_bibtex(doi: str, *, opener=None) -> str:
    doi = _doi(doi)
    if not re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi) or len(doi) > 512:
        raise ValueError("invalid DOI")
    text = _request_text("https://doi.org/" + urllib.parse.quote(doi, safe="/"),
                         "application/x-bibtex", opener=opener)
    if not re.match(r"\s*@\w+\s*[{(]", text):
        raise ValueError("DOI response is not BibTeX")
    return text


def dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept, seen = [], set()
    for result in results:
        url = str(result.get("url") or "").strip()
        parsed = urllib.parse.urlsplit(url)
        normalized = urllib.parse.urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))
        doi = _doi(str(result.get("doi") or result.get("DOI") or ""))
        if parsed.hostname in ("doi.org", "dx.doi.org"):
            doi = doi or _doi(url)
        keys = {"url:" + normalized} if url else set()
        if doi:
            keys.add("doi:" + doi)
        # Providers register the same work under different landing URLs.
        title_key = re.sub(r"[^a-z0-9]+", "", _text(result.get("title")).casefold())[:80]
        if len(title_key) >= 12:
            keys.add("title:" + title_key)
        duplicate = bool(keys & seen)
        seen.update(keys)
        if not duplicate:
            kept.append(result)
    return kept


def search_scholarly(query: str, *, max_results: int = 10, store=None,
                     project_id: str = "", opener=None) -> dict[str, Any]:
    """Explicit-call helper for the desktop route and future Chat integration.

    Caller owns authorization. Metadata only; no full-text acquisition/review.
    Provider failures are reported independently, with no automatic retries.
    """
    query = validate_search(query, max_results)
    results, providers, errors = [], [], []
    per_provider: list[list[dict[str, Any]]] = []
    for name, search in (
        ("arxiv", arxiv_search),
        ("crossref", crossref_search),
        ("openalex", openalex_search),
    ):
        try:
            found = search(query, max_results=max_results, opener=opener)
        except Exception as error:
            # Do not expose transport URLs or remote response bodies.
            errors.append({"provider": name, "error": type(error).__name__})
            continue
        if store is not None:
            store.save_search_query(project_id=project_id, provider=name, query=query, results=found)
        providers.append(name)
        per_provider.append(found)
    # Round-robin the providers so one responsive provider cannot consume the
    # whole result bound and hide the others' coverage.
    for rank in range(max((len(found) for found in per_provider), default=0)):
        for found in per_provider:
            if rank < len(found):
                results.append(found[rank])
    return {"results": dedupe_results(results)[:max_results],
            "providers": providers, "errors": errors,
            "evidence_scope": "bibliographic_metadata_and_abstracts_only"}


def metadata_bibtex(results: list[dict[str, Any]]) -> str:
    """Serialize only observed fields; never invent journal/pages or authors."""
    def escape(value):
        replacements = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
                        "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_", "$": r"\$"}
        return "".join(replacements.get(c, c) for c in _text(value))
    entries = []
    for index, result in enumerate(results, 1):
        fields = {"title": result.get("title"), "author": " and ".join(result.get("authors") or []),
                  "year": result.get("year"), "doi": result.get("doi"), "url": result.get("url")}
        lines = [f"  {key} = {{{escape(value)}}}" for key, value in fields.items() if value]
        entries.append(f"@misc{{amca{index},\n" + ",\n".join(lines) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")
