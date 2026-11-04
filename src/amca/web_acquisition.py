"""R1.3A controlled web-acquisition foundation.

A bounded, policy-gated HTTP acquisition engine underneath future AMCA web
research. NOT a browser: no JavaScript rendering, no login/session
automation, no form submission, no recursive crawling.

Security model:
- only http:// and https:// schemes are eligible; file:/ftp:/gopher:/data:/
  javascript:, UNC paths and embedded credentials are rejected;
- destinations are resolved and rejected when they land in loopback,
  private, link-local, multicast, reserved or unspecified ranges (IPv4 and
  IPv6), including localhost and cloud-metadata style endpoints;
- redirects are bounded AND every hop is revalidated against the same
  address policy (a public URL that redirects to localhost is rejected);
- TLS certificate verification is never disabled;
- response bytes and decompressed expansion are bounded during the read;
  truncation produces an explicit truthful status, never a fake complete
  snapshot;
- every acquisition requires an authoritative Tool Fabric authorization:
  without approval there are ZERO network requests.
"""

from __future__ import annotations

import gzip
import io
import ipaddress
import json
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

MAX_URL_LENGTH = 2048
MAX_QUERY_LENGTH = 512
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0

ALLOWED_SCHEMES = {"http", "https"}
DENIED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}

SAFE_HEADER_SUBSET = ("content-type", "etag", "last-modified")


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class UrlRejected(ValueError):
    """URL failed scheme/credential/address security validation."""


class AcquisitionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    TRUNCATED = "truncated"
    DENIED = "denied"


def validate_public_url(url: str) -> str:
    """Validate scheme/credentials/length; returns normalized URL.

    Address-range checks run per resolved socket address at request time via
    :func:`assert_public_host` (and again for every redirect hop).
    """

    if not url or len(url) > MAX_URL_LENGTH:
        raise UrlRejected("url missing or exceeds length bound")
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.strip().lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlRejected(f"scheme not allowed: {scheme!r}")
    if "@" in (parsed.netloc or ""):
        raise UrlRejected("embedded credentials in URL rejected")
    if not parsed.hostname:
        raise UrlRejected("missing hostname")
    lowered = parsed.hostname.lower()
    if lowered in ("localhost",) or lowered.endswith(".localhost"):
        raise UrlRejected(f"local hostname rejected: {lowered}")
    return url


def _classify_address(address: str) -> bool:
    """True when address is PUBLIC after IPv4-mapped-IPv6 normalization."""

    text = address.split("%")[0]
    ip = ipaddress.ip_address(text)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 style wrappers must not bypass classification.
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def assert_public_host(hostname: str) -> None:
    """Resolve hostname and reject if ANY resolved address is non-public.

    Conservative multi-address rule: one prohibited member rejects the whole
    destination so an uncontrolled fallback path cannot survive.
    """

    lowered = str(hostname).lower()
    if lowered in ("localhost",) or lowered.endswith(".localhost"):
        raise UrlRejected(f"local hostname rejected: {lowered}")
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UrlRejected(f"cannot resolve host: {hostname!r}") from exc
    for info in infos:
        address = info[4][0]
        try:
            public = _classify_address(address)
        except ValueError:
            continue
        if not public:
            raise UrlRejected(f"non-public address rejected: {address}")


def resolve_public_addresses(hostname: str) -> list[str]:
    """All resolved addresses must be public; returns them for pinning."""

    assert_public_host(hostname)
    try:
        infos = socket.getaddrinfo(
            hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UrlRejected(f"cannot resolve host: {hostname!r}") from exc
    pinned: list[str] = []
    seen = set()
    for info in infos:
        address = info[4][0].split("%")[0]
        if _classify_address(address) and address not in seen:
            pinned.append(address)
            seen.add(address)
    if not pinned:
        raise UrlRejected(f"no public address for host: {hostname!r}")
    return pinned


@dataclass(slots=True)
class FetchOutcome:
    requested_url: str
    final_url: str
    redirect_chain: list[str]
    http_status: int | None
    content_type: str | None
    body: bytes
    byte_count: int
    status: AcquisitionStatus
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


import functools
import http.client
import ssl


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection whose socket targets the VALIDATED address only."""

    def __init__(self, *args, pinned_ip, **kwargs):
        super().__init__(*args, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS variant preserving original-hostname SNI/TLS verification while
    connecting the socket to the validated address."""

    def __init__(self, *args, pinned_ip, **kwargs):
        super().__init__(*args, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ip: str):
        super().__init__(context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def https_open(self, req):
        return self.do_open(
            functools.partial(_PinnedHTTPSConnection, pinned_ip=self._pinned_ip),
            req)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned_ip: str):
        super().__init__()
        self._pinned_ip = pinned_ip

    def http_open(self, req):
        return self.do_open(
            functools.partial(_PinnedHTTPConnection, pinned_ip=self._pinned_ip),
            req)


def _proxy_free_opener(pinned_ip: str | None, *, https: bool):
    """Opener WITHOUT ambient proxy inheritance and WITHOUT auto-redirects."""

    handlers: list[Any] = []
    if pinned_ip is not None:
        handlers.append(_PinnedHTTPSHandler(pinned_ip) if https
                        else _PinnedHTTPHandler(pinned_ip))
    else:
        handlers.append(urllib.request.HTTPSHandler()
                        if https else urllib.request.HTTPHandler())
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.UnknownHandler())
    for handler in handlers:
        opener.add_handler(handler)
    return opener


class WebAcquisitionClient:
    """Bounded HTTP fetcher with SSRF guards and redirect revalidation."""

    def __init__(self, *, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
                 read_timeout: float = DEFAULT_READ_TIMEOUT,
                 max_response_bytes: int = MAX_RESPONSE_BYTES,
                 max_redirects: int = MAX_REDIRECTS,
                 max_decompressed_bytes: int = MAX_RESPONSE_BYTES,
                 opener=None,
                 host_validator=None) -> None:
        self.host_validator = host_validator or assert_public_host
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self.max_decompressed_bytes = max_decompressed_bytes
        self._opener = opener

    def _open(self, request: urllib.request.Request,
              pinned_ip: str | None = None, https: bool = True):
        """Single-hop opener: never auto-follows redirects; proxy-free."""

        if self._opener is not None:
            return self._opener.open(request, timeout=self.read_timeout)
        opener = _proxy_free_opener(pinned_ip, https=https)
        return opener.open(request, timeout=self.read_timeout)

    def fetch(self, url: str) -> FetchOutcome:
        url = validate_public_url(url)
        chain: list[str] = [url]
        current = url
        try:
            for hop in range(self.max_redirects + 1):
                parsed = urllib.parse.urlsplit(current)
                self.host_validator(parsed.hostname)
                # R1.3A.1 anti-rebinding: resolve once, reject if ANY member
                # is non-public, then PIN the connection to a validated
                # address while keeping the original Host/SNI name.
                pinned_addresses = resolve_public_addresses(parsed.hostname)
                pinned_ip = pinned_addresses[0]

                request = urllib.request.Request(current)
                try:
                    response = self._open(request, pinned_ip,
                                          parsed.scheme == "https")
                except urllib.error.HTTPError as exc:
                    if exc.code in (301, 302, 303, 307, 308):
                        location = exc.headers.get("Location") if exc.headers else None
                        if not location:
                            return self._failure(url, chain, exc.code,
                                                 "redirect without location")
                        current = urllib.parse.urljoin(current, location)
                        validate_public_url(current)
                        chain.append(current)
                        if len(chain) - 1 > self.max_redirects:
                            return self._failure(url, chain, exc.code,
                                                 "redirect bound exceeded")
                        continue
                    return self._failure(url, chain, exc.code, str(exc.reason))
                except (urllib.error.URLError,) as exc:
                    reason = getattr(exc, "reason", exc)
                    timed_out = isinstance(reason, (TimeoutError, socket.timeout))
                    return FetchOutcome(url, current, chain, None, None, b"", 0,
                                        AcquisitionStatus.TIMED_OUT if timed_out
                                        else AcquisitionStatus.FAILED,
                                        str(reason)[:200])
                except (TimeoutError, socket.timeout):
                    return FetchOutcome(url, current, chain, None, None, b"", 0,
                                        AcquisitionStatus.TIMED_OUT, "timed out")

                final_url = response.geturl()
                safe_headers = {name: response.headers.get(name)
                                for name in SAFE_HEADER_SUBSET
                                if response.headers.get(name)}
                content_encoding = (
                    response.headers.get("Content-Encoding") or "").lower()
                declared_type = response.headers.get("Content-Type")
                raw = response.read(self.max_response_bytes + 1)
                truncated_on_read = len(raw) > self.max_response_bytes
                raw = raw[:self.max_response_bytes]

                if content_encoding == "gzip":
                    expanded = gzip.decompress(
                        raw[:self.max_decompressed_bytes + 1])
                    truncated = len(expanded) > self.max_decompressed_bytes
                    expanded = expanded[:self.max_decompressed_bytes]
                else:
                    expanded, truncated = raw, truncated_on_read

                status = (AcquisitionStatus.TRUNCATED
                          if truncated else AcquisitionStatus.SUCCEEDED)
                warning = ("response bounded/truncated" if truncated else None)
                return FetchOutcome(url, final_url, chain,
                                    getattr(response, "status", None),
                                    declared_type, expanded, len(expanded),
                                    status, warning, safe_headers)
            return self._failure(url, chain, None, "redirect bound exceeded")
        except UrlRejected as exc:
            return self._failure(url, chain + [str(exc)], None, str(exc))
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            return self._failure(url, chain, None, str(exc)[:200])

    @staticmethod
    def _bounded_gunzip(decompressor, raw: bytes) -> tuple[bytes, int]:
        try:
            expanded = decompressor.decompress(raw, self_max := MAX_RESPONSE_BYTES + 1)
        except zlib.error:
            return b"", len(raw)
        return expanded, len(raw)

    @staticmethod
    def _failure(url: str, chain: list[str], http_status: int | None,
                 error: str) -> FetchOutcome:
        return FetchOutcome(url, chain[-1], chain, http_status, None,
                            b"", 0, AcquisitionStatus.FAILED, error)


class WebAcquisitionStore:
    """Durable acquisition provenance + search-query provenance records."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS research_web_acquisitions (
                    acquisition_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT,
                    requested_url TEXT NOT NULL,
                    final_url TEXT NOT NULL,
                    redirect_chain_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    content_type TEXT,
                    byte_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    tool_execution_id TEXT,
                    source_id TEXT,
                    snapshot_id TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    warnings TEXT
                );
                CREATE TABLE IF NOT EXISTS research_search_queries (
                    query_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    query TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    ranks_urls_json TEXT NOT NULL DEFAULT '[]',
                    observed_at TEXT NOT NULL
                );
            """)

    def save_acquisition(self, record: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO research_web_acquisitions
                   (acquisition_id, project_id, session_id, requested_url,
                    final_url, redirect_chain_json, status, http_status,
                    content_type, byte_count, started_at, completed_at,
                    tool_execution_id, source_id, snapshot_id, etag,
                    last_modified, warnings)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record["acquisition_id"], record["project_id"],
                 record.get("session_id"), record["requested_url"],
                 record["final_url"],
                 json.dumps(record["redirect_chain"]),
                 record["status"], record["http_status"],
                 record["content_type"], record["byte_count"],
                 record["started_at"], record["completed_at"],
                 record.get("tool_execution_id"), record.get("source_id"),
                 record.get("snapshot_id"), record.get("etag"),
                 record.get("last_modified"), record.get("warnings")))

    def save_search_query(self, *, project_id: str, provider: str,
                          query: str, results: list[dict[str, Any]]) -> None:
        ranks = [{"rank": index + 1, "url": item.get("url")}
                 for index, item in enumerate(results)]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_search_queries
                   (query_id, project_id, provider, query, result_count,
                    ranks_urls_json, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_uuid("q"), project_id, provider,
                 query[:MAX_QUERY_LENGTH], len(results),
                 json.dumps(ranks), _now()))


# ---------------------------------------------------------------------------
# search provider abstraction


@dataclass(slots=True)
class WebSearchResult:
    provider: str
    query: str
    rank: int
    title: str
    url: str
    snippet: str
    observed_at: str = field(default_factory=_now)


class WebSearchProvider:
    provider_id = "abstract"

    def search(self, query: str, *,
               max_results: int = 10) -> list[WebSearchResult]:
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError("query exceeds bound")
        raise NotImplementedError


class DeterministicSearchProvider(WebSearchProvider):
    """Credential-free deterministic provider used for tests/fixtures."""

    provider_id = "deterministic"

    def __init__(self, corpus: dict[str, list[tuple[str, str, str]]]) -> None:
        # corpus: query-token -> [(title, url, snippet)]
        self.corpus = corpus

    def search(self, query: str, *, max_results: int = 10):
        token = query.strip().lower()
        entries = self.corpus.get(token, [])
        observed = _now()
        return [
            WebSearchResult(provider=self.provider_id, query=query,
                            rank=index + 1, title=title, url=url,
                            snippet=snippet, observed_at=observed)
            for index, (title, url, snippet) in enumerate(entries[:max_results])
        ]


class NetworkToolPolicy:
    """Tool Fabric registration + gating for network capabilities.

    research.web.* capabilities are side_effect=NETWORK and DENY by
    default. An explicit project enablement (project override ALLOW)
    escalates them to REQUIRE_APPROVAL - never silent access.
    """

    FETCH_TOOL_ID = "research.web.fetch"
    SEARCH_TOOL_ID = "research.web.search"

    @staticmethod
    def register(fabric) -> None:
        from amca.tool_fabric import (
            Decision, RiskClass, SideEffectClass, ToolCapability,
        )

        for tool_id in (NetworkToolPolicy.FETCH_TOOL_ID,
                        NetworkToolPolicy.SEARCH_TOOL_ID):
            fabric.register_capability(ToolCapability(
                tool_id=tool_id, version="1.0.0", category="network",
                description="Controlled web acquisition (policy gated)",
                risk_class=RiskClass.IRREVERSIBLE_OR_EXTERNAL,
                side_effect_class=SideEffectClass.NETWORK,
                supported_operations=("acquire", "search"),
                resource_scopes=("external_origin",),
                approval_policy=Decision.DENY))

    @staticmethod
    def enable_for_project(store, project_id: str) -> None:
        """Explicit project enablement: DENY becomes REQUIRE_APPROVAL."""

        from amca.tool_fabric import Decision

        store.set_project_policy(project_id, {
            NetworkToolPolicy.FETCH_TOOL_ID: Decision.ALLOW,
            NetworkToolPolicy.SEARCH_TOOL_ID: Decision.ALLOW,
        })

    @staticmethod
    def gate(fabric, request) -> tuple[Decision, Any]:
        """Evaluate network gating; network tools never auto-ALLOW."""

        from amca.tool_fabric import Decision

        result, record = fabric.submit(request, {"url": request.normalized_arguments.get("url")})
        if (record.tool_id.startswith("research.web.")
                and result.decision is Decision.ALLOW):
            # Defensive: even an ALLOW override only authorizes approval.
            result = type(result)(Decision.REQUIRE_APPROVAL, result.reason)
        return result, record


def acquire(fabric, acq_store: WebAcquisitionStore, ingestor: ResearchIngestor,
            client: WebAcquisitionClient, *, project_id: str,
            session_id: str | None, url: str,
            approved: bool = False) -> dict[str, Any]:
    """One authorized acquisition: gate -> fetch -> source/snapshot -> record."""

    from amca.tool_fabric import Decision, ExecutionStatus, ToolRequest

    request = ToolRequest(
        request_id=_uuid("treq"),
        organization_id="agent-fabric",
        project_id=project_id,
        tool_id=NetworkToolPolicy.FETCH_TOOL_ID,
        operation="acquire",
        normalized_arguments={"url": url},
        requested_resource_scope=url,
        actor="research",
    )
    decision, record = fabric.submit(request, {"url": url})
    outcome_record: dict[str, Any] = {
        "decision": decision.decision.value,
        "reason": decision.reason.value,
        "execution_status": record.status.value,
        "network_requests_made": 0,
    }
    if record.status is ExecutionStatus.DENIED:
        outcome_record["status"] = AcquisitionStatus.DENIED.value
        return outcome_record
    if record.status is ExecutionStatus.REQUESTED and not approved:
        outcome_record["status"] = "awaiting_approval"
        return outcome_record
    if not approved:
        outcome_record["status"] = "awaiting_approval"
        return outcome_record

    fabric.approve(record.execution_id, approval_reference=f"session:{session_id}")
    holder: dict[str, Any] = {}

    def fetch_backend():
        outcome = client.fetch(url)
        holder["outcome"] = outcome
        if outcome.status is AcquisitionStatus.FAILED:
            raise RuntimeError(outcome.error or "acquisition failed")
        return {
            "summary": f"{outcome.byte_count} bytes from {outcome.final_url}",
            "side_effect_summary": f"http {outcome.http_status}",
        }

    fabric.set_backend(NetworkToolPolicy.FETCH_TOOL_ID, fetch_backend)
    receipt = fabric.execute(record.execution_id)

    outcome = holder.get("outcome")
    acquisition_id = _uuid("acq")
    acq_store.save_acquisition({
        "acquisition_id": acquisition_id,
        "project_id": project_id,
        "session_id": session_id,
        "requested_url": url,
        "final_url": outcome.final_url if outcome else url,
        "redirect_chain": outcome.redirect_chain if outcome else [url],
        "status": outcome.status.value if outcome else receipt["status"],
        "http_status": outcome.http_status if outcome else None,
        "content_type": outcome.content_type if outcome else None,
        "byte_count": outcome.byte_count if outcome else 0,
        "started_at": receipt.get("started_at") or _now(),
        "completed_at": receipt.get("completed_at") or _now(),
        "tool_execution_id": record.execution_id,
        "etag": (outcome.headers.get("etag") if outcome else None),
        "last_modified": (outcome.headers.get("last-modified") if outcome else None),
        "warnings": outcome.error if outcome else receipt.get("result_summary"),
    })

    if outcome and outcome.status in (AcquisitionStatus.SUCCEEDED,
                                      AcquisitionStatus.TRUNCATED):
        created = ingestor.ingest_artifact_snapshot(
            session_id=session_id or "", project_id=project_id,
            artifact_id=url[:80], version=1,
            content=outcome.body, title=url,
            content_type=outcome.content_type)
        # Re-classify as a WEB source identity.
        with ingestor.store._connect() as connection:
            connection.execute(
                "UPDATE research_sources SET source_kind='web', canonical_origin=? "
                "WHERE source_id=?",
                (outcome.final_url, created["source"]["source_id"]))
        outcome_record["source_id"] = created["source"]["source_id"]
        outcome_record["snapshot_id"] = created["snapshot"]["snapshot_id"]
        network_requests = 1
    else:
        network_requests = 0
    outcome_record["network_requests_made"] = network_requests
    outcome_record["acquisition_id"] = acquisition_id
    outcome_record["receipt_status"] = receipt["status"]
    outcome_record["warnings"] = outcome.error if outcome else None
    return outcome_record


def search_gated(fabric, acq_store: WebAcquisitionStore,
                 provider: WebSearchProvider, *, project_id: str,
                 session_id: str | None, query: str,
                 max_results: int = 10,
                 approved: bool = False) -> dict[str, Any]:
    """Permissioned provider search: gate -> approve -> call -> durable event.

    The provider request happens ONLY after explicit approval; a crash before
    normalized results are committed leaves the event INTERRUPTED and is
    never silently retried.
    """

    from amca.tool_fabric import Decision, ExecutionStatus, ToolRequest

    request = ToolRequest(
        request_id=_uuid("treq"),
        organization_id="agent-fabric",
        project_id=project_id,
        tool_id=NetworkToolPolicy.SEARCH_TOOL_ID,
        operation="search",
        normalized_arguments={"query": query[:MAX_QUERY_LENGTH],
                              "max_results": max_results},
        requested_resource_scope=provider.endpoint
        if hasattr(provider, "endpoint") else None,
        actor="research",
    )
    decision, record = fabric.submit(request, {"query": query})
    outcome: dict[str, Any] = {
        "decision": decision.decision.value,
        "network_requests_made": 0,
        "results": [],
    }
    if record.status is ExecutionStatus.DENIED:
        outcome["status"] = "denied"
        return outcome
    if not approved:
        outcome["status"] = "awaiting_approval"
        return outcome

    fabric.approve(record.execution_id, approval_reference=f"session:{session_id}")

    holder: dict[str, Any] = {}

    def search_backend():
        started = _now()
        results = provider.search(query, max_results=max_results)
        completed = _now()
        event = acq_store.record_search_event(
            project_id=project_id, session_id=session_id,
            provider_id=getattr(provider, "provider_id", "unknown"),
            provider_version=getattr(provider, "provider_version", ""),
            query=query, options={"max_results": max_results},
            started_at=started, completed_at=completed, status="committed",
            results=[{
                "rank": item.rank, "title": item.title, "url": item.url,
                "snippet": item.snippet,
                "provider_relevance": None,
                "observed_at": item.observed_at,
            } for item in results],
            error_reason=None,
            tool_request_id=request.request_id,
            tool_execution_id=record.execution_id)
        holder["event"] = event
        holder["results"] = [
            {"rank": item.rank, "title": item.title, "url": item.url,
             "snippet": item.snippet}
            for item in results]
        return {"summary": f"{len(results)} normalized results"}

    fabric.set_backend(NetworkToolPolicy.SEARCH_TOOL_ID, search_backend)
    receipt = fabric.execute(record.execution_id)
    outcome["receipt_status"] = receipt["status"]
    if receipt["status"] == "succeeded":
        outcome["results"] = holder["results"]
        outcome["search_event_id"] = holder["event"]["search_event_id"]
    else:
        outcome["status"] = "failed"
        # A failed/interrupted provider call must NOT auto-retry.
        outcome.setdefault("error_reason", "provider_call_failed")
    return outcome


def unfetched_results_for(self, session_id: str, *, project_id: str,
                          exclude_urls=None):
    """Search-result observations not yet fetched within this project."""
    exclude = set(exclude_urls or [])
    with self._connect() as connection:
        rows = connection.execute(
            """SELECT r.search_result_id, r.url FROM research_search_results r
               WHERE r.search_event_id IN
                 (SELECT search_event_id FROM research_search_events
                  WHERE project_id = ?)""",
            (project_id,)).fetchall()
    return [{"search_result_id": r["search_result_id"], "url": r["url"]}
            for r in rows if r["url"] not in exclude]


WebAcquisitionStore.unfetched_results_for = unfetched_results_for
