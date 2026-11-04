"""R1.3B production search-provider integration (Exa first adapter).

Verified against Exa's official API documentation at implementation time:

- endpoint: POST https://api.exa.ai/search
- auth: x-api-key header (or Bearer); key attached ONLY to this request
- request: query (required), numResults (1..100), includeDomains/
  excludeDomains, startPublishedDate/endPublishedDate (ISO 8601)
- response: {requestId, results:[{title, url, publishedDate?, author?, id?,
  score?...}], costDollars:{total:<estimate>}}

Truth rules:
- provider results are OBSERVATIONS - never snapshots, evidence or claims;
- provider relevance stays provider_relevance (never truth/authority);
- Exa's costDollars.total is an estimate, so authoritative per-search billing
  remains UNKNOWN -> actual_search_cost_usd = null;
- credentials live only inside the request built here; they never reach
  research tables, logs, provenance or UI structures.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from amca.web_acquisition import (
    MAX_QUERY_LENGTH,
    WebSearchProvider,
    WebSearchResult,
)

EXA_SEARCH_ENDPOINT = "https://api.exa.ai/search"
MAX_EXA_RESULTS = 100


class ProviderFailure(Exception):
    """Normalized bounded provider-failure reasons (no raw dumps)."""

    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    INTERRUPTED_UNCERTAIN = "interrupted_uncertain"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class ProviderCapabilities:
    domain_include: bool = True
    domain_exclude: bool = True
    published_date_filter: bool = True
    max_results_bound: int = MAX_EXA_RESULTS


class UnsupportedOption(ValueError):
    """A generic AMCA option the configured provider does not support."""


class ExaSearchProvider(WebSearchProvider):
    """Production adapter over Exa's documented Search API contract."""

    provider_id = "exa"
    provider_version = "r1.3b-exa-1.0.0"

    def __init__(self, credential_resolver, *,
                 transport=None,
                 endpoint: str = EXA_SEARCH_ENDPOINT) -> None:
        self._credential_resolver = credential_resolver
        self.endpoint = endpoint
        self._transport = transport or self._default_transport
        self.capabilities = ProviderCapabilities()

    @staticmethod
    def _default_transport(url: str, body: bytes, headers: dict[str, str],
                           timeout: float) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(url, data=body, method="POST")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (response.status, dict(response.headers.items()),
                        response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()

    def build_request(self, query: str, *, max_results: int = 10,
                      domain_include: list[str] | None = None,
                      domain_exclude: list[str] | None = None,
                      start_published_date: str | None = None,
                      end_published_date: str | None = None) \
            -> tuple[dict[str, str], bytes]:
        """Canonical neutral options -> verified Exa request contract."""

        capped = max(1, min(int(max_results), self.capabilities.max_results_bound))
        body: dict[str, Any] = {"query": query[:MAX_QUERY_LENGTH],
                                "numResults": capped}
        if domain_include:
            body["includeDomains"] = list(domain_include)
        if domain_exclude:
            body["excludeDomains"] = list(domain_exclude)
        if start_published_date:
            body["startPublishedDate"] = start_published_date
        if end_published_date:
            body["endPublishedDate"] = end_published_date

        api_key = self._credential_resolver()
        if not api_key:
            raise ProviderFailure(ProviderFailure.AUTH_FAILED)
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        return headers, json.dumps(body).encode("utf-8")

    def search(self, query: str, *, max_results: int = 10,
               domain_include: list[str] | None = None,
               domain_exclude: list[str] | None = None,
               start_published_date: str | None = None,
               end_published_date: str | None = None) \
            -> list[WebSearchResult]:
        headers, body = self.build_request(
            query, max_results=max_results,
            domain_include=domain_include, domain_exclude=domain_exclude,
            start_published_date=start_published_date,
            end_published_date=end_published_date)

        http_status, _, response_bytes = self._transport(
            EXA_SEARCH_ENDPOINT, body, headers, timeout=30.0)

        if http_status == 401 or http_status == 403:
            raise ProviderFailure(ProviderFailure.AUTH_FAILED)
        if http_status == 429:
            raise ProviderFailure(ProviderFailure.RATE_LIMITED)
        if http_status == 400:
            raise ProviderFailure(ProviderFailure.INVALID_REQUEST)
        if http_status != 200:
            raise ProviderFailure(ProviderFailure.PROVIDER_UNAVAILABLE)

        try:
            document = json.loads(response_bytes.decode("utf-8"))
            raw_results = document["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderFailure(ProviderFailure.MALFORMED_RESPONSE) from exc

        normalized: list[WebSearchResult] = []
        observed = _now()
        for index, item in enumerate(raw_results):
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            if not url:
                continue
            normalized.append(WebSearchResult(
                provider=self.provider_id,
                query=query,
                rank=index + 1,
                title=title,
                url=url,
                snippet=str(item.get("text", "") or "")[:400],
                observed_at=observed))
        return normalized

    # Cost truth: Exa reports costDollars.total as an ESTIMATE and states
    # billing is computed from usage counters - so no authoritative per-search
    # billing exists in the response and actual cost stays null/unknown.


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()
