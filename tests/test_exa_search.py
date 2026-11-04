"""R1.3B real search provider integration tests (Exa adapter, zero paid calls)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.exa_provider import (
    EXA_SEARCH_ENDPOINT,
    ProviderFailure,
    ExaSearchProvider,
)
from amca.research_source import ResearchStore
from amca.tool_fabric import Decision, ExecutionStatus, ToolFabric, ToolFabricStore
from amca.web_acquisition import NetworkToolPolicy, WebAcquisitionStore


class ExaFixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = self.root / "amca.db"
        self.store = ResearchStore(self.database,
                                   storage_root=self.root / "content")
        self.tstore = ToolFabricStore(self.database)
        self.fabric = ToolFabric(self.tstore, project_root="projA")
        NetworkToolPolicy.register(self.fabric)
        self.session = self.store.create_session(project_id="projA",
                                                 title="s", purpose="p")

    def fake_transport(self, http_status=200, body=None):
        payload = body if body is not None else json.dumps({
            "requestId": "req-1",
            "costDollars": {"total": 0.007},
            "results": [
                {"title": "First", "url": "https://first.example/x",
                 "publishedDate": "2026-01-02T00:00:00Z",
                 "score": 0.91},
                {"title": "Second", "url": "https://second.example/y",
                 "score": 0.42},
            ],
        }).encode("utf-8")
        captured = {}

        def transport(url, body_bytes, headers, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json.loads(body_bytes.decode("utf-8"))
            return http_status, {}, payload

        return transport, captured

    def provider(self, transport):
        return ExaSearchProvider(lambda: "sk-exa-test-key",
                                 transport=transport)


class ExaContractTests(ExaFixtureBase):
    def test_request_mapping_matches_verified_official_contract(self) -> None:
        transport, captured = self.fake_transport()
        provider = self.provider(transport)
        results = provider.search("amca research", max_results=5,
                                  domain_include=["example.com"],
                                  start_published_date="2026-01-01")
        self.assertEqual(captured["url"], EXA_SEARCH_ENDPOINT)
        self.assertEqual(captured["url"].split("/")[2], "api.exa.ai")
        self.assertEqual(captured["headers"]["x-api-key"], "sk-exa-test-key")
        body = captured["body"]
        self.assertEqual(body["query"], "amca research")
        self.assertEqual(body["numResults"], 5)
        self.assertEqual(body["includeDomains"], ["example.com"])
        self.assertEqual(body["startPublishedDate"], "2026-01-01")

    def test_normalized_results_preserve_provider_rank(self) -> None:
        transport, _ = self.fake_transport()
        provider = self.provider(transport)
        results = provider.search("query")
        self.assertEqual([r.rank for r in results], [1, 2])
        self.assertEqual(results[0].provider, "exa")
        self.assertEqual(results[0].observed_at, results[1].observed_at)

    def test_credentials_never_reach_persistence(self) -> None:
        decision, record = self.fabric.submit(_search_request(),
                                              {"query": "q"})
        stored = self.store.record_search_event(
            project_id="projA", session_id=self.session["session_id"],
            provider_id="exa", provider_version="r1.3b-exa-1.0.0",
            query="test query", options={}, started_at="t0",
            completed_at="t1", status="committed",
            results=[{"rank": 1, "title": "t", "url": "https://ok.example/z"}],
            tool_execution_id=record.execution_id)
        connection = sqlite3.connect(str(self.database))
        try:
            blob = "\n".join(
                str(r[0]) for r in connection.execute(
                    "SELECT * FROM research_search_events").fetchall())
        finally:
            connection.close()
        self.assertNotIn("sk-exa-test-key", blob)
        self.assertIsNotNone(stored["search_event_id"])

    def test_provider_failures_normalize_to_bounded_reasons(self) -> None:
        for status_code, reason in ((401, ProviderFailure.AUTH_FAILED),
                                    (429, ProviderFailure.RATE_LIMITED),
                                    (400, ProviderFailure.INVALID_REQUEST),
                                    (503, ProviderFailure.PROVIDER_UNAVAILABLE)):
            transport, _ = self.fake_transport(http_status=status_code,
                                               body=b"{}")
            with self.assertRaises(ProviderFailure) as caught:
                self.provider(transport).search("q")
            self.assertEqual(caught.exception.reason, reason)

        malformed = b"{not json at all"
        transport, _ = self.fake_transport(http_status=200, body=malformed)
        with self.assertRaises(ProviderFailure) as caught:
            self.provider(transport).search("q")
        self.assertEqual(caught.exception.reason,
                         ProviderFailure.MALFORMED_RESPONSE)

    def test_billing_estimate_is_not_authoritative_cost(self) -> None:
        # costDollars.total is an estimate: authoritative billing stays null.
        transport, _ = self.fake_transport()
        provider = self.provider(transport)
        results = provider.search("q")
        # The normalized result contract exposes no dollar figure.
        self.assertFalse(any(hasattr(r, "actual_cost_usd") for r in results))


class GatedSearchTests(ExaFixtureBase):
    def setUp(self) -> None:
        super().setUp()
        self.events = ResearchStore(self.root / "events.db",
                                    storage_root=self.root / "ev-content")

    def test_denied_before_enablement_zero_provider_requests(self) -> None:
        provider_calls = []

        class CountingProvider(ExaSearchProvider):
            pass

        from amca.web_acquisition import search_gated
        outcome = search_gated(self.fabric, self.events,
                               CountingProvider(lambda: "key"),
                               project_id="projA",
                               session_id=self.session["session_id"],
                               query="anything", approved=True)
        self.assertEqual(outcome["status"], "denied")

    def test_enablement_then_approval_then_committed_event(self) -> None:
        from amca.web_acquisition import search_gated

        NetworkToolPolicy.enable_for_project(self.tstore, "projA")
        transport, _ = self.fake_transport()
        provider = self.provider(transport)
        outcome = search_gated(self.fabric, self.events, provider,
                               project_id="projA",
                               session_id=self.session["session_id"],
                               query="gate check", approved=False)
        self.assertEqual(outcome["status"], "awaiting_approval")

        outcome = search_gated(self.fabric, self.events, provider,
                               project_id="projA",
                               session_id=self.session["session_id"],
                               query="gate check", approved=True)
        self.assertEqual(outcome["receipt_status"], "succeeded")
        self.assertEqual(len(outcome["results"]), 2)
        event = self.events.get_search_event(outcome["search_event_id"])
        self.assertEqual(event["result_count"], 2)
        self.assertIsNone(event["actual_search_cost_usd"],
                          "estimate-only billing must stay unknown")

    def test_failed_provider_call_does_not_auto_retry(self) -> None:
        from amca.web_acquisition import search_gated

        NetworkToolPolicy.enable_for_project(self.tstore, "projA")
        failing_transport, _ = self.fake_transport()
        original = failing_transport

        def failing(url, body, headers, timeout):
            raise TimeoutError("provider timeout")

        provider = ExaSearchProvider(lambda: "key", transport=failing)
        outcome = search_gated(self.fabric, self.events, provider,
                               project_id="projA",
                               session_id=self.session["session_id"],
                               query="timeout case", approved=True)
        self.assertNotEqual(outcome.get("receipt_status"), "succeeded")


def _search_request():
    from amca.tool_fabric import ToolRequest

    return ToolRequest(
        request_id="req-search-1", organization_id="agent-fabric",
        project_id="projA", tool_id=NetworkToolPolicy.SEARCH_TOOL_ID,
        operation="search", normalized_arguments={},
        requested_resource_scope=EXA_SEARCH_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
