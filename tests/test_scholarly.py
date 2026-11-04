"""Offline scholarly transport, route, provenance and native bibliography tests."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amca import scholarly
from amca.artifacts import ArtifactStore, parse_artifact_blocks
from amca.artifact_render import ArtifactExporter

ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2609.00001</id><title> Current\n literature </title>
<author><name>Ada Example</name></author><summary> An abstract. </summary>
<published>2026-09-17T00:00:00Z</published><arxiv:doi>10.1234/example</arxiv:doi></entry></feed>'''
CROSSREF = json.dumps({"message": {"items": [{"title": ["Second paper"],
    "author": [{"given": "Grace", "family": "Example"}],
    "published": {"date-parts": [[2026, 9]]}, "DOI": "10.1234/second",
    "URL": "https://doi.org/10.1234/second"}]}}).encode()


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


@pytest.fixture
def transport(monkeypatch):
    # No sockets or DNS: even route tests dispatch the handler in memory.
    monkeypatch.setattr(scholarly, "resolve_public_addresses", lambda host: ["93.184.216.34"])
    def route(request, **kw):
        if "arxiv.org" in request.full_url:
            return Response(ATOM)
        if "crossref" in request.full_url:
            return Response(CROSSREF)
        return Response(OPENALEX)

    opener = Mock()
    opener.open.side_effect = route
    monkeypatch.setattr(scholarly, "_proxy_free_opener", lambda *a, **kw: opener)
    return opener


def test_arxiv_parse(transport):
    result = scholarly.arxiv_search("current literature")[0]
    assert result["title"] == "Current literature"
    assert result["authors"] == ["Ada Example"]
    assert result["year"] == 2026
    assert result["doi"] == "10.1234/example"
    assert result["provider"] == "arxiv"
    assert result["snippet"] == "An abstract."
    assert "sortBy=submittedDate" in transport.open.call_args.args[0].full_url


def test_crossref_parse(transport):
    result = scholarly.crossref_search("current literature")[0]
    assert result["title"] == "Second paper"
    assert result["authors"] == ["Grace Example"]
    assert result["year"] == 2026
    assert result["doi"] == "10.1234/second"
    assert result["provider"] == "crossref"
    assert "query.bibliographic=" in transport.open.call_args.args[0].full_url


def test_crossref_bibtex_content_negotiation(transport):
    transport.open.side_effect = lambda *a, **kw: Response(b"@article{example, title={Paper}}")
    assert scholarly.crossref_bibtex("10.1234/example").startswith("@article")
    request = transport.open.call_args.args[0]
    assert request.get_header("Accept") == "application/x-bibtex"
    assert request.full_url == "https://doi.org/10.1234/example"


def test_dedupe_doi_and_url():
    records = [{"doi": "10.1234/A", "url": "http://example.org/paper"},
               {"url": "https://doi.org/10.1234/a"},
               {"url": "https://example.org/paper/#section"},
               {"url": "https://example.org/another"}]
    assert scholarly.dedupe_results(records) == [records[0], records[3]]


def test_bib_fence_round_trip_and_export(tmp_path):
    text = "@misc{example, title={Current literature}, year={2026}}"
    blocks = parse_artifact_blocks("Summary\n```bib\n" + text + "\n```\n")
    assert blocks == [{"artifact_type": "bib", "title": "Bibliography", "content": text}]
    store = ArtifactStore(tmp_path / "artifacts.db", tmp_path / "payloads")
    artifact = store.author(project_id="p", created_by="model:test", chat_id="chat", **blocks[0])
    aid = artifact["artifact_id"]
    assert store.read_source(aid)["content"] == text
    assert store.read_source(aid)["provenance"]["chat_id"] == "chat"
    assert (tmp_path / "payloads" / aid / "v1.bib").read_text() == text
    exported = ArtifactExporter(store, tmp_path / "exports").export(
        project_id="p", artifact_id=aid, target_format="bib")
    assert exported["content_type"] == "text/x-bibtex"
    assert Path(exported["path"]).read_text() == text
    assert parse_artifact_blocks("```artifact bib title=References\n" + text + "\n```")[0]["title"] == "References"


def test_crossref_failure_nonfatal(transport):
    def open_response(request, **kwargs):
        if "crossref" in request.full_url:
            raise TimeoutError()
        if "openalex" in request.full_url:
            return Response(OPENALEX)
        return Response(ATOM)
    transport.open.side_effect = open_response
    result = scholarly.search_scholarly("literature")
    assert len(result["results"]) == 2
    assert result["providers"] == ["arxiv", "openalex"]
    assert result["errors"] == [{"provider": "crossref", "error": "TimeoutError"}]


@pytest.mark.parametrize("query,limit", [("", 10), ("x" * 513, 10), ("x", 0), ("x", True)])
def test_invalid_search_makes_no_calls(transport, query, limit):
    with pytest.raises(ValueError):
        scholarly.search_scholarly(query, max_results=limit)
    transport.open.assert_not_called()


def test_response_bound_and_redirect_validation(transport, monkeypatch):
    monkeypatch.setattr(scholarly, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(ValueError, match="byte bound"):
        scholarly.arxiv_search("test")
    transport.open.side_effect = lambda *a, **kw: Response(b"", 302, {"Location": "https://localhost/private"})
    with pytest.raises(scholarly.UrlRejected):
        scholarly.arxiv_search("test")
    assert transport.open.call_count == 2


def test_authenticated_route_offline(tmp_path, transport):
    from amca.service import AMCARequestHandler, ServiceContext
    from amca.web_acquisition import WebAcquisitionStore

    store = ArtifactStore(tmp_path / "artifacts.db", tmp_path / "payloads")
    context = SimpleNamespace(database=tmp_path / "research.db", artifact_store=store)
    context.project = lambda project: {} if project == "p" else None
    context.artifact_author = lambda project, value: store.author(project_id=project, **value)
    context.research_search_web = lambda project, value: ServiceContext.research_search_web(context, project, value)
    handler = object.__new__(AMCARequestHandler)
    handler.server = SimpleNamespace(amca_context=context)
    handler.context = context
    handler.path = "/api/projects/p/research/search/web"
    handler._cors_allowed = lambda: True
    handler._loopback_and_host_ok = lambda: True
    handler._authorized = lambda: False
    handler._read_object = lambda: {"query": "literature", "max_results": 10}
    handler._error = Mock()
    handler._send_json = Mock()
    transport.open.assert_not_called()
    handler.do_POST()
    assert handler._error.call_args.args[0] == 401
    transport.open.assert_not_called()
    handler._authorized = lambda: True
    handler.do_POST()
    assert handler._send_json.call_args.args[0] == 200
    result = handler._send_json.call_args.args[1]
    assert len(result["results"]) == 3
    assert result["providers"] == ["arxiv", "crossref", "openalex"]
    assert result["errors"] == []
    assert transport.open.call_count == 3
    assert "metadata" in result["evidence_scope"]
    assert store.get(result["metadata_artifact_id"])["artifact_type"] == "json"
    assert json.loads(store.read_source(result["metadata_artifact_id"])["content"])["results"] == result["results"]
    assert "@misc" in store.read_source(result["bib_artifact_id"])["content"]
    with WebAcquisitionStore(context.database)._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_search_queries WHERE project_id='p'").fetchone()[0] == 3


# -- Chat research query extraction (v1.1.8) ---------------------------------

REAL_REQUEST = (
    "Please write a comprehensive literature review of the latest AI agent papers, "
    "covering what was built, methodology, results, capabilities, algorithms, "
    "contributions, limitations, within-paper comparisons, cross-paper comparisons, "
    "research gaps, and future directions. Include citations and a separate BibTeX file."
)


def test_research_query_reduces_instruction_to_the_topical_core():
    """The provider matches titles/abstracts, so the deliverable vocabulary
    ("comprehensive literature review ... papers") must not reach the query."""
    query = scholarly.research_query_from_goal(REAL_REQUEST)
    assert query.lower() == "ai agent"
    assert "bibtex" not in query.lower()
    assert "future directions" not in query.lower()
    assert "methodology" not in query.lower()
    assert len(query) <= 180


def test_research_query_handles_plain_imperatives_and_empty_input():
    assert scholarly.research_query_from_goal(
        "Find recent papers on retrieval augmented generation for code"
    ).lower() == "retrieval augmented generation for code"
    assert scholarly.research_query_from_goal(
        "Summarize recent studies about tool use in LLM agents"
    ).lower() == "tool use in llm agents"
    assert scholarly.research_query_from_goal("") == ""
    assert scholarly.research_query_from_goal("   ") == ""
    # No topic survives the deliverable vocabulary: keep the original text.
    assert scholarly.research_query_from_goal("Write a literature review") == (
        "literature review"
    )


@pytest.mark.parametrize(
    "goal, expected",
    [
        (REAL_REQUEST, 8),
        ("Give me the top 5 papers on agent evaluation.", 5),
        ("Summarize 12 recent studies about tool use.", 12),
        ("Find at least 3 papers on planning.", 3),
        ("Find 99 papers on planning.", 8),  # out of range -> default
    ],
)
def test_requested_result_count(goal, expected):
    assert scholarly.requested_result_count(goal) == expected


# -- Transient throttling retry and OpenAlex provider (v1.1.8) ---------------

OPENALEX = json.dumps({"results": [{
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.5555/agentic",
    "title": "Agentic AI systems: a survey",
    "publication_year": 2026,
    "authorships": [{"author": {"display_name": "Ada Example"}}],
    "abstract_inverted_index": {"Agentic": [0], "systems": [1], "survey": [2]},
}]}).encode()


def test_transient_status_is_retried_then_reported(monkeypatch):
    """arXiv's export gateway throttles with 406; the identical request is
    retried a bounded number of times, then reported honestly."""
    calls = {"n": 0}

    class Opener:
        def open(self, request, **kwargs):
            calls["n"] += 1
            return Response(b"", status=406)

    monkeypatch.setattr(scholarly, "resolve_public_addresses", lambda host: ["93.184.216.34"])
    sleeps = []
    with pytest.raises(ValueError, match="HTTP status 406"):
        scholarly._request_text(
            "https://export.arxiv.org/api/query?search_query=all:x",
            "application/atom+xml", opener=Opener(), sleep=sleeps.append,
        )
    assert calls["n"] == 3
    assert sleeps == [3.0, 6.0]


def test_transient_status_recovers_on_retry(monkeypatch):
    class Opener:
        def __init__(self):
            self.n = 0

        def open(self, request, **kwargs):
            self.n += 1
            return Response(b"ok" if self.n > 1 else b"", status=200 if self.n > 1 else 503)

    opener = Opener()
    text = scholarly._request_text(
        "https://api.crossref.org/works?query.bibliographic=x",
        "application/json", opener=opener, sleep=lambda _: None,
    )
    assert text == "ok"
    assert opener.n == 2


def test_non_transient_status_is_not_retried(monkeypatch):
    calls = {"n": 0}

    class Opener:
        def open(self, request, **kwargs):
            calls["n"] += 1
            return Response(b"", status=404)

    with pytest.raises(ValueError, match="HTTP status 404"):
        scholarly._request_text(
            "https://api.crossref.org/works?query.bibliographic=x",
            "application/json", opener=Opener(), sleep=lambda _: None,
        )
    assert calls["n"] == 1


def test_openalex_parse(monkeypatch):
    monkeypatch.setattr(scholarly, "resolve_public_addresses", lambda host: ["93.184.216.34"])
    opener = Mock()
    opener.open.side_effect = lambda request, **kw: Response(OPENALEX)
    monkeypatch.setattr(scholarly, "_proxy_free_opener", lambda *a, **kw: opener)
    results = scholarly.openalex_search("agentic ai", max_results=5)
    assert len(results) == 1
    item = results[0]
    assert item["provider"] == "openalex"
    assert item["doi"] == "10.5555/agentic"
    assert item["url"] == "https://doi.org/10.5555/agentic"
    assert item["year"] == 2026
    assert item["authors"] == ["Ada Example"]
    assert item["snippet"] == "Agentic systems survey"


def test_search_uses_all_three_providers(transport):
    payload = scholarly.search_scholarly("literature", max_results=10)
    assert payload["providers"] == ["arxiv", "crossref", "openalex"]
    assert payload["errors"] == []
    assert {item["provider"] for item in payload["results"]} == {"arxiv", "crossref", "openalex"}
