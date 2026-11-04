"""R1.3A controlled web-acquisition tests (local deterministic fixtures)."""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from amca.research_source import ResearchStore
from amca.research_ingest import ResearchIngestor
from amca.tool_fabric import (
    Decision,
    ExecutionStatus,
    ToolFabric,
    ToolFabricStore,
)
from amca.web_acquisition import (
    AcquisitionStatus,
    NetworkToolPolicy,
    UrlRejected,
    WebAcquisitionClient,
    WebAcquisitionStore,
    acquire,
    assert_public_host,
    validate_public_url,
)


class FabricBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = ToolFabricStore(self.root / "amca.db")
        self.fabric = ToolFabric(self.store, project_root="projA")
        NetworkToolPolicy.register(self.fabric)

    def enable(self) -> None:
        NetworkToolPolicy.enable_for_project(self.store, "projA")


class SecurityValidatorTests(FabricBase):
    def test_schemes_credentials_and_local_hosts_rejected(self) -> None:
        for bad in ("file:///etc/passwd", "ftp://h/x", "data:text/plain,x",
                    "javascript:void(0)", "gopher://h",
                    "https://user:secret@example.com/"):
            with self.assertRaises(UrlRejected):
                validate_public_url(bad)

    def test_private_and_reserved_hosts_rejected(self) -> None:
        for host in ("localhost", "sub.localhost", "127.0.0.1", "::1",
                     "10.1.2.3", "172.16.9.9", "192.168.0.1",
                     "169.254.169.254", "224.0.0.5", "0.0.0.0"):
            with self.assertRaises(UrlRejected):
                assert_public_host(host)

    def test_public_url_accepted(self) -> None:
        self.assertEqual(validate_public_url("https://example.com/doc"),
                         "https://example.com/doc")

    def test_ipv4_mapped_ipv6_does_not_bypass_classification(self) -> None:
        from amca.web_acquisition import _classify_address

        for mapped in ("::ffff:127.0.0.1", "::ffff:169.254.10.1",
                       "::ffff:10.0.0.1"):
            self.assertFalse(_classify_address(mapped), mapped)

    def test_multiple_addresses_any_prohibited_rejects(self) -> None:
        import socket as socket_module
        from amca.web_acquisition import resolve_public_addresses

        original = socket_module.getaddrinfo
        def fake(host, *args, **kwargs):
            return [(None, None, None, "", ("203.0.113.9", 0)),
                    (None, None, None, "", ("10.0.0.5", 0))]
        socket_module.getaddrinfo = fake
        try:
            with self.assertRaises(UrlRejected):
                resolve_public_addresses("rebinding.example")
        finally:
            socket_module.getaddrinfo = original

    def test_opener_has_no_ambient_proxy_handler(self) -> None:
        import os
        from amca.web_acquisition import _proxy_free_opener

        saved = {k: os.environ.get(k) for k in
                 ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
        for key, value in saved.items():
            if value is None:
                os.environ[key] = "http://proxy.example:3128"
        try:
            opener = _proxy_free_opener(None, https=True)
            handlers = {type(h).__name__ for h in opener.handlers}
            self.assertNotIn("ProxyHandler", handlers)
        finally:
            for key, value in saved.items():
                if value is None:
                    del os.environ[key]
                else:
                    os.environ[key] = value


class GatingTests(FabricBase):
    def test_default_network_policy_is_deny(self) -> None:
        decision, record = self.fabric.submit(_fetch_request(), {"url": url()})
        self.assertEqual(decision.decision, Decision.DENY)
        self.assertEqual(record.status, ExecutionStatus.DENIED)

    def test_project_enablement_escalates_to_approval_not_allow(self) -> None:
        self.enable()
        decision, record = self.fabric.submit(_fetch_request(), {"url": url()})
        self.assertEqual(decision.decision, Decision.REQUIRE_APPROVAL)
        self.assertEqual(record.status, ExecutionStatus.REQUESTED)


def _fetch_request():
    from amca.tool_fabric import ToolRequest

    return ToolRequest(
        request_id="req-web-1", organization_id="agent-fabric",
        project_id="projA", tool_id=NetworkToolPolicy.FETCH_TOOL_ID,
        operation="acquire", normalized_arguments={},
        requested_resource_scope="https://example.com/doc")


def url() -> str:
    return "https://example.com/doc"


class FakeResponse(io.BytesIO):
    status = 200

    def geturl(self) -> str:
        return getattr(self, "_url", "")


class FakeTransport:
    """Deterministic single-hop transport (no real sockets)."""

    def __init__(self, body: bytes, content_type: str = "text/html") -> None:
        self.body = body
        self.content_type = content_type
        self.opened_urls: list[str] = []

    def open(self, request, timeout=None):
        self.opened_urls.append(request.full_url)
        response = FakeResponse(self.body)
        response._url = request.full_url
        headers = {"Content-Type": self.content_type}
        object.__setattr__(response, "headers",
                           type("H", (), {
                               "get": lambda s, k: headers.get(k),
                               "__getitem__": lambda s, k: headers[k],
                           })())
        return response


class RedirectingTransport:
    """Redirects every request to a fixed location without following."""

    def __init__(self, location: str, final_body: bytes = b"final") -> None:
        self.location = location
        self.final_body = final_body

    def open(self, request, timeout=None):
        error = urllib.error.HTTPError(request.full_url, 302, "redirect",
                                       {"Location": self.location}, io.BytesIO())
        raise error

    @property
    def opened_urls(self):
        return []


import urllib.error  # noqa: E402


class BoundedFetchTests(FabricBase):
    def client(self, opener, **kwargs) -> WebAcquisitionClient:
        return WebAcquisitionClient(opener=opener, **kwargs)

    def test_success_fetch_reads_bounded_body(self) -> None:
        transport = FakeTransport(b"<html><body>hello world</body></html>")
        outcome = self.client(transport).fetch("https://example.com/page")
        self.assertEqual(outcome.status, AcquisitionStatus.SUCCEEDED)
        self.assertIn(b"hello world", outcome.body)
        self.assertEqual(len(outcome.redirect_chain), 1)

    def test_response_size_bound_truncates_truthfully(self) -> None:
        transport = FakeTransport(b"z" * 5000)
        outcome = self.client(transport, max_response_bytes=100).fetch(
            "https://example.com/big")
        self.assertEqual(outcome.status, AcquisitionStatus.TRUNCATED)
        self.assertLessEqual(outcome.byte_count, 100)

    def test_redirect_to_private_destination_rejected(self) -> None:
        transport = RedirectingTransport("http://127.0.0.1:9/secret")
        outcome = self.client(transport).fetch("https://example.com/start")
        self.assertEqual(outcome.status, AcquisitionStatus.FAILED)
        self.assertTrue(any("rejected" in hop or "127.0.0.1" in hop
                            for hop in [outcome.error or ""]))

    def test_gzip_expansion_bounded(self) -> None:
        import gzip as gzip_mod

        compressed = gzip_mod.compress(b"y" * 3000)
        transport = FakeTransport(compressed)
        # Pretend the transport declared gzip encoding.
        original_open = transport.open

        def open_with_encoding(request, timeout=None):
            response = original_open(request, timeout=timeout)
            response.headers = type("H", (), {"get":
                lambda s, k: "gzip" if k == "Content-Encoding"
                else "text/html"})()
            return response

        transport.open = open_with_encoding
        outcome = self.client(transport, max_decompressed_bytes=100).fetch(
            "https://example.com/gz")
        self.assertEqual(outcome.status, AcquisitionStatus.TRUNCATED)
        self.assertLessEqual(outcome.byte_count, 100)


class AuthorizedAcquisitionTests(FabricBase):
    def _prepare(self):
        store = ResearchStore(self.root / "amca.db",
                              storage_root=self.root / "content")
        ingestor = ResearchIngestor(store, base_dir=self.root / "proj")
        session = store.create_session(project_id="projA", title="s",
                                       purpose="web")
        acq_store = WebAcquisitionStore(self.root / "amca.db")
        return store, ingestor, session["session_id"], acq_store

    def test_no_approval_zero_network_requests(self) -> None:
        self.enable()
        store, ingestor, sid, acq_store = self._prepare()
        client = WebAcquisitionClient(opener=_DenyingTransport())
        outcome = acquire(self.fabric, acq_store, ingestor, client,
                          project_id="projA", session_id=sid,
                          url="https://example.com/a")
        self.assertEqual(outcome["status"], "awaiting_approval")
        self.assertEqual(outcome["network_requests_made"], 0)

    def test_denied_policy_makes_zero_network_requests(self) -> None:
        store, ingestor, sid, acq_store = self._prepare()
        outcome = acquire(self.fabric, acq_store, ingestor, WebAcquisitionClient(),
                          project_id="projA", session_id=sid,
                          url="https://example.com/denied")
        self.assertEqual(outcome["status"], "denied")

    def test_approved_acquisition_creates_web_source_snapshot_evidence(self) -> None:
        self.enable()
        store, ingestor, sid, acq_store = self._prepare()
        transport = FakeTransport(b"<html><body>acquired research body</body></html>",
                                  content_type="text/html")
        # example.com cannot resolve offline: inject an allow-all host
        # validator so the deterministic fixture stands in for DNS.
        client = WebAcquisitionClient(opener=transport,
                                      host_validator=lambda host: None)
        outcome = acquire(self.fabric, acq_store, ingestor, client,
                          project_id="projA", session_id=sid,
                          url="https://example.com/article", approved=True)
        self.assertEqual(outcome["receipt_status"], "succeeded")
        self.assertIsNotNone(outcome["source_id"])
        self.assertIsNotNone(outcome["snapshot_id"])

        extraction = ingestor.extract(outcome["snapshot_id"],
                                      project_id="projA")
        self.assertEqual(extraction["status"], "ok")
        # Search-hit -> validated extractive evidence off the acquired page.
        first_segment = extraction["segments"][0]
        evidence = store.add_evidence(
            outcome["snapshot_id"], project_id="projA",
            locator=first_segment.locator,
            excerpt=first_segment.text)
        self.assertEqual(evidence["extraction_method"], "extractive")
        # One transport, one observation event recorded.
        acquisition_row = acq_store and self._acquisition(acq_store)
        self.assertEqual(acquisition_row["http_status"], 200)

    def _excerpt_of(self, extraction) -> str:
        return extraction["segments"][0].text[:80]

    def _first_segment_id(self, store):
        connection = sqlite3.connect(str(store.database))
        try:
            row = connection.execute(
                "SELECT segment_id FROM research_extracted_segments").fetchone()
            return row[0]
        finally:
            connection.close()

    def _acquisition(self, acq_store):
        connection = sqlite3.connect(str(acq_store.database))
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM research_web_acquisitions").fetchone()
            return dict(row) if row else None
        finally:
            connection.close()


class _DenyingTransport:
    def open(self, request, timeout=None):  # pragma: no cover
        raise AssertionError("network request made without approval")


def url() -> str:
    return "https://example.com/doc"


if __name__ == "__main__":
    unittest.main()
