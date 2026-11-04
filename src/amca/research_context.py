"""R2.3 local relevance reranking + research context builder.

Turns accepted R2.2 retrieval results into a bounded, reproducible
research-context assembly layer. Frozen semantics (manager-reply60):

- A reranker sees ONLY bounded candidates already produced by the accepted
  retrieval layer. It never searches, fetches, creates evidence, changes
  claim relations or approves tools - it is a pure scoring function.
- Scores are RETRIEVAL RELEVANCE heuristics. Never truth, evidence quality,
  authority or credibility: a highly relevant document may still be wrong.
- Duplicate control uses authoritative digests/identities, never vector
  similarity alone. Diversity caps are deterministic bookkeeping only.
- Contradiction-safe selection: for a contested claim BOTH supporting and
  contradicting sides are preserved within bounds; dropping one side yields
  explicit PARTIAL + CONFLICT_CONTEXT_TRUNCATED, never silent one-sidedness.
- ResearchContextPackage is immutable once created: changed inputs produce a
  NEW deterministic digest/context id; staleness is computed against current
  authoritative segment digests. Deletion touches ONLY derived rows.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from amca.research_semantic import (
    MAX_QUERY_LENGTH,
    PREVIEW_CHARS,
    EmbeddingError,
    SemanticRetrievalStore,
    segment_digest,
)

MAX_RERANK_CANDIDATES = 100
MAX_CHARS_PER_CANDIDATE = 8000
MAX_TOTAL_CANDIDATE_CHARS = 200_000
MAX_CONTEXT_ITEMS = 50
MAX_CONTEXT_ITEM_CHARS = 8000
MAX_CONTEXT_TOTAL_CHARS = 60_000
MAX_CONTEXT_SOURCES = 20


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _tokens(text: str) -> list[str]:
    out = []
    token = ""
    for ch in (text or "").lower():
        if ch.isalnum():
            token += ch
        elif token:
            out.append(token)
            token = ""
    if token:
        out.append(token)
    return out


class RerankError(ValueError):
    """Bounds/usage violations in the reranking layer."""


class RerankerUnavailable(RuntimeError):
    """No reranker configured; baseline retrieval order remains usable."""


class RetrievalMode(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class ContextItemType(str, Enum):
    CLAIM_EVIDENCE = "claim_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    RETRIEVAL_HIT = "retrieval_hit"
    QUESTION_CONTEXT = "question_context"
    SOURCE_CONTEXT = "source_context"


class SelectionTier(str, Enum):
    TIER1_CLAIM_EVIDENCE = "tier1_claim_evidence"
    TIER2_RETRIEVAL = "tier2_retrieval"
    TIER3_CONTEXTUAL = "tier3_contextual"


@dataclass(slots=True)
class ContextBounds:
    max_items: int = 20
    max_total_chars: int = MAX_CONTEXT_TOTAL_CHARS
    max_chars_per_item: int = MAX_CONTEXT_ITEM_CHARS
    max_sources: int = MAX_CONTEXT_SOURCES
    max_segments_per_source: int = 3
    max_segments_per_snapshot: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_items": self.max_items,
            "max_total_chars": self.max_total_chars,
            "max_chars_per_item": self.max_chars_per_item,
            "max_sources": self.max_sources,
            "max_segments_per_source": self.max_segments_per_source,
            "max_segments_per_snapshot": self.max_segments_per_snapshot,
        }


# ---------------------------------------------------------------------------
# rerankers


class ResearchReranker:
    """Provider-neutral LOCAL relevance reranking interface.

    Implementations are pure functions over (query, candidates). They must
    NOT perform network work, create evidence, mutate relations or interact
    with the Tool Fabric."""

    provider_id = "abstract"
    version = "0"
    model_id = "abstract"
    is_local = True

    def rerank(self, query: str,
               candidates: list[dict[str, Any]]) -> list[float]:
        """Return ONE relevance score per candidate, input order aligned."""
        raise NotImplementedError


class DeterministicOverlapReranker(ResearchReranker):
    """Offline deterministic baseline: normalized query-token overlap with
    the candidate's text.

    Score semantics: a QUERY-CONTEXT RELEVANCE HEURISTIC only. It says
    nothing about factual correctness, source authority or evidence
    strength."""

    provider_id = "deterministic-overlap"
    version = "r2.3-1.0.0"
    model_id = "token-overlap"

    @staticmethod
    def _candidate_text(candidate: dict[str, Any]) -> str:
        for key in ("text", "excerpt", "preview"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
        return str(candidate.get("preview", ""))

    def rerank(self, query: str,
               candidates: list[dict[str, Any]]) -> list[float]:
        query_tokens = set(_tokens(query))
        scores: list[float] = []
        for candidate in candidates:
            cand_tokens = set(_tokens(self._candidate_text(candidate)))
            if not query_tokens or not cand_tokens:
                scores.append(0.0)
                continue
            overlap = len(query_tokens & cand_tokens)
            scores.append(round(overlap / len(query_tokens), 6))
        return scores


class LocalServiceReranker(ResearchReranker):
    """Adapter for an ALREADY-RUNNING local rerank service (e.g. an
    Ollama-style endpoint on loopback).

    No model weights are bundled or downloaded: the operator configures a
    reachable local endpoint; AMCA POSTs bounded JSON and parses
    {"scores": [...]} aligned to the submitted order. Tests inject a fake
    opener so zero real network occurs."""

    provider_id = "local-service"
    version = "r2.3-1.0.0"

    def __init__(self, *, endpoint: str, model_id: str,
                 timeout_seconds: float = 5.0, opener=None) -> None:
        lowered = (endpoint or "").lower()
        if not (lowered.startswith("http://127.0.0.1")
                or lowered.startswith("http://localhost")
                or lowered.startswith("http://[::1]")):
            raise RerankError(
                "local service reranker requires a loopback endpoint")
        self.endpoint = endpoint
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self._opener = opener
        self.dimension = 0

    def rerank(self, query: str,
               candidates: list[dict[str, Any]]) -> list[float]:
        payload = json.dumps({
            "model": self.model_id,
            "query": query[:MAX_QUERY_LENGTH],
            "documents": [str(c.get("preview", c.get("excerpt", "")))[:2000]
                          for c in candidates],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=payload,
            headers={"Content-Type": "application/json"})
        if self._opener is not None:
            response = self._opener.open(request,
                                         timeout=self.timeout_seconds)
        else:
            response = urllib.request.urlopen(
                request, timeout=self.timeout_seconds)
        body = json.loads(response.read().decode("utf-8"))
        scores = body.get("scores")
        if not isinstance(scores, list) or len(scores) != len(candidates):
            raise RerankError("local reranker returned malformed scores")
        return [float(s) for s in scores]


# ---------------------------------------------------------------------------
# durable context packages (derived, immutable)


class ContextStore:
    """Durable ResearchContextPackage metadata in canonical amca.db.

    Packages are DERIVED and reproducible: deleting them must never touch
    sources, snapshots, evidence, citations, claims or synthesis."""

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
                CREATE TABLE IF NOT EXISTS research_context_packages (
                    context_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    digest TEXT NOT NULL,
                    package_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    def save_package(self, package: dict[str, Any]) -> dict[str, Any]:
        """Immutable insert: an existing context_id is returned unchanged."""
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM research_context_packages WHERE context_id=?",
                (package["context_id"],)).fetchone()
            if existing is not None:
                record = dict(existing)
                record["package"] = json.loads(record.pop("package_json"))
                return record
            connection.execute(
                """INSERT INTO research_context_packages
                   (context_id, project_id, session_id, purpose, status,
                    digest, package_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (package["context_id"], package["project_id"],
                 package.get("session_id"), package["purpose"],
                 package.get("status", "active"), package["digest"],
                 json.dumps(package, sort_keys=True), package["created_at"]))
        record = dict(self.get_package(package["context_id"]))
        return record

    def get_package(self, context_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_context_packages "
                "WHERE context_id = ?", (context_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["package"] = json.loads(record.pop("package_json"))
        return record

    def refresh_staleness(self, context_id: str, research_store) \
            -> str | None:
        """Compare selected segment digests against CURRENT authoritative
        text; any drift marks the whole package STALE (still readable)."""
        record = self.get_package(context_id)
        if record is None:
            return None
        package = record["package"]
        stale = False
        with research_store._connect() as connection:
            for item in package.get("items", []):
                segment_id = item.get("segment_id")
                expected = item.get("segment_digest")
                if not segment_id or not expected:
                    continue
                row = connection.execute(
                    "SELECT text FROM research_extracted_segments "
                    "WHERE segment_id = ?", (segment_id,)).fetchone()
                if row is None or segment_digest(row["text"]) != expected:
                    stale = True
                    break
        status = "stale" if stale else record["status"]
        if status != record["status"]:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE research_context_packages SET status = ? "
                    "WHERE context_id = ?", (status, context_id))
        return status

    def delete_project_context(self, project_id: str) -> int:
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM research_context_packages WHERE project_id = ?",
                (project_id,)).rowcount
        return deleted


# ---------------------------------------------------------------------------
# context builder


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Deterministic truncation at a word boundary with disclosure."""
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    cut = window.rfind(" ")
    if cut < int(limit * 0.5):
        cut = limit
    return window[:cut].rstrip(), True


class ResearchContextBuilder:
    """Bounded, reproducible context assembly over accepted retrieval."""

    def __init__(self, *, reranker: ResearchReranker | None = None,
                 allow_fallback: bool = True) -> None:
        if reranker is not None and not getattr(reranker, "is_local", False):
            raise RerankError(
                "refusing non-local reranker: no cloud fallback in R2.3")
        self.reranker = reranker
        self.allow_fallback = allow_fallback

    # -- public entry ---------------------------------------------------

    def build(self, *, retrieval_store: SemanticRetrievalStore,
              research_store, synthesis_store=None,
              project_id: str, session_id: str | None,
              purpose: str, focus_query: str,
              candidates: list[dict[str, Any]],
              requested_mode: str, effective_mode: str,
              claim_ids: list[str] | None = None,
              question_text: str | None = None,
              bounds: ContextBounds | None = None) -> dict[str, Any]:
        bounds = bounds or ContextBounds()
        if len(focus_query) > MAX_QUERY_LENGTH:
            raise RerankError("focus query exceeds length bound")

        warnings: list[str] = []
        omitted = {"candidates_over_bound": 0, "oversized_candidates": 0,
                   "duplicates_removed": 0, "diversity_omitted": 0,
                   "budget_omitted": 0}

        # Stage 1: candidate bounds.
        pool = candidates[:MAX_RERANK_CANDIDATES]
        omitted["candidates_over_bound"] = max(
            0, len(candidates) - len(pool))
        usable: list[dict[str, Any]] = []
        total_chars = 0
        for index, candidate in enumerate(pool):
            text = str(candidate.get("preview", "")
                       or candidate.get("excerpt", ""))
            if len(text) > MAX_CHARS_PER_CANDIDATE:
                omitted["oversized_candidates"] += 1
                continue
            total_chars += len(text)
            if total_chars > MAX_TOTAL_CANDIDATE_CHARS:
                omitted["candidates_over_bound"] += 1
                continue
            entry = dict(candidate)
            entry["pre_rerank_rank"] = index + 1
            usable.append(entry)

        # Stage 2: optional local reranking (truthful states only).
        rerank_effective = False
        reranker_identity = None
        if self.reranker is None:
            warnings.append("RERANKER_UNAVAILABLE")
        else:
            try:
                scores = self.reranker.rerank(focus_query, usable)
                if len(scores) != len(usable):
                    raise RerankError("score/candidate count mismatch")
                for entry, score in zip(usable, scores):
                    entry["rerank_relevance"] = float(score)
                usable.sort(key=lambda e: (-e["rerank_relevance"],
                                           str(e.get("segment_id")),
                                           e["pre_rerank_rank"]))
                rerank_effective = True
                reranker_identity = {
                    "provider_id": self.reranker.provider_id,
                    "model_id": self.reranker.model_id,
                    "version": self.reranker.version,
                    "score_label": "retrieval_relevance",
                }
            except Exception as exc:  # noqa: BLE001 - truthful fallback
                if not self.allow_fallback:
                    raise
                warnings.append("RERANKER_FAILED")
                warnings.append(f"reranker_error:{type(exc).__name__}")
            finally:
                retrieval_store._bump(project_id, "rerank_requests", 1)
                retrieval_store._bump(project_id, "rerank_candidates",
                                      len(usable))
        for post, entry in enumerate(usable, start=1):
            entry.setdefault("rerank_relevance", None)
            entry["post_rerank_rank"] = post
        if not rerank_effective and "RERANKER_FAILED" in warnings:
            retrieval_store._bump(project_id, "rerank_failures", 1)

        # Stage 3: duplicate suppression on authoritative identity/digest.
        seen_digests: set[str] = set()
        seen_identity: set[tuple] = set()
        deduped: list[dict[str, Any]] = []
        for entry in usable:
            identity = (entry.get("source_id"), entry.get("snapshot_id"),
                        entry.get("segment_id"))
            digest = entry.get("segment_digest")
            if digest:
                if digest in seen_digests:
                    omitted["duplicates_removed"] += 1
                    continue
            elif all(identity) and identity in seen_identity:
                omitted["duplicates_removed"] += 1
                continue
            if digest:
                seen_digests.add(digest)
            if all(identity):
                seen_identity.add(identity)
            deduped.append(entry)

        # Stage 4: deterministic diversity caps.
        per_source: dict[str, int] = {}
        per_snapshot: dict[str, int] = {}
        diversified: list[dict[str, Any]] = []
        for entry in deduped:
            source_key = str(entry.get("source_id") or id(entry))
            snapshot_key = str(entry.get("snapshot_id") or id(entry))
            if per_source.get(source_key, 0) >= bounds.max_segments_per_source:
                omitted["diversity_omitted"] += 1
                continue
            if per_snapshot.get(snapshot_key, 0) >= \
                    bounds.max_segments_per_snapshot:
                omitted["diversity_omitted"] += 1
                continue
            per_source[source_key] = per_source.get(source_key, 0) + 1
            per_snapshot[snapshot_key] = per_snapshot.get(snapshot_key, 0) + 1
            diversified.append(entry)
        if omitted["diversity_omitted"]:
            retrieval_store._bump(project_id, "source_diversity_adjustments",
                                  omitted["diversity_omitted"])

        # Stage 5: Tier-1 linked claim evidence (contradiction-safe).
        items: list[dict[str, Any]] = []
        used_chars = 0
        used_sources: set[str] = set()
        status = "complete"
        relations_present: set[str] = set()
        relations_available: set[str] = set()

        if claim_ids and synthesis_store is not None:
            for claim_id in claim_ids:
                for link in synthesis_store.claim_links(claim_id):
                    evidence = synthesis_store._evidence_chain(
                        link["evidence_id"])
                    if evidence is None:
                        continue
                    relation = str(link.get("relation"))
                    relations_available.add(relation)
                    text = str(evidence.get("excerpt", ""))
                    rendered, truncated = _truncate(
                        text, bounds.max_chars_per_item)
                    cost = len(rendered)
                    fits = (len(items) < bounds.max_items
                            and used_sources.__len__() <= bounds.max_sources
                            and used_chars + cost <= bounds.max_total_chars)
                    if not fits:
                        continue
                    relations_present.add(relation)
                    is_conflict = relation == "contradicts"
                    items.append({
                        "context_item_type":
                            ContextItemType.CONFLICTING_EVIDENCE.value
                            if is_conflict
                            else ContextItemType.CLAIM_EVIDENCE.value,
                        "tier": SelectionTier.TIER1_CLAIM_EVIDENCE.value,
                        "evidence_id": link["evidence_id"],
                        "claim_id": claim_id,
                        "relation": relation,
                        "snapshot_id": evidence.get("snapshot_id"),
                        "source_id": evidence.get("source_id"),
                        "segment_id": evidence.get("segment_id"),
                        "segment_digest": evidence.get("segment_digest"),
                        "locator": evidence.get("locator"),
                        "text": rendered,
                        "truncated": truncated,
                        "original_length": len(text),
                        "rendered_length": len(rendered),
                        "provenance": {},
                    })
                    used_chars += cost
                    if evidence.get("source_id"):
                        used_sources.add(str(evidence["source_id"]))

        contested = {"supports", "contradicts"} <= relations_available
        if contested and not {"supports", "contradicts"} <= relations_present:
            status = "partial"
            warnings.append("CONFLICT_CONTEXT_TRUNCATED")
        elif contested:
            retrieval_store._bump(project_id,
                                  "conflict_preservation_adjustments", 1)

        # Stage 6: Tier-2 selection under hard budgets.
        for entry in diversified:
            if len(items) >= bounds.max_items:
                omitted["budget_omitted"] += 1
                status = "partial"
                continue
            text = str(entry.get("preview", "") or entry.get("excerpt", ""))
            rendered, truncated = _truncate(text, bounds.max_chars_per_item)
            source_key = str(entry.get("source_id") or "")
            if (used_chars + len(rendered) > bounds.max_total_chars
                    or (source_key and len(used_sources) >= bounds.max_sources
                        and source_key not in used_sources)):
                omitted["budget_omitted"] += 1
                status = "partial"
                continue
            provenance_keys = ("requested_mode", "effective_mode",
                               "lexical_rank", "semantic_rank",
                               "semantic_similarity", "hybrid_rank",
                               "fusion_method", "pre_rerank_rank",
                               "post_rerank_rank", "rerank_relevance")
            items.append({
                "context_item_type": ContextItemType.RETRIEVAL_HIT.value,
                "tier": SelectionTier.TIER2_RETRIEVAL.value,
                "source_id": entry.get("source_id"),
                "snapshot_id": entry.get("snapshot_id"),
                "extraction_id": entry.get("extraction_id"),
                "segment_id": entry.get("segment_id"),
                "segment_digest": entry.get("segment_digest"),
                "locator": entry.get("locator"),
                "text": rendered,
                "truncated": truncated,
                "original_length": len(text),
                "rendered_length": len(rendered),
                "provenance": {k: entry.get(k) for k in provenance_keys},
            })
            used_chars += len(rendered)
            if source_key:
                used_sources.add(source_key)

        if omitted["budget_omitted"]:
            warnings.append("CONTEXT_BUDGET_TRUNCATED")
            retrieval_store._bump(project_id, "context_partial_count", 1)

        # Stage 7: stable ids + deterministic digest.
        for number, item in enumerate(items, start=1):
            item["context_item_id"] = f"CTX-{number}"
        digest_payload = {
            "project_id": project_id,
            "session_id": session_id,
            "purpose": purpose,
            "focus_query": focus_query,
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
            "bounds": bounds.to_dict(),
            "reranker": reranker_identity,
            "items": [{
                "type": i["context_item_type"],
                "tier": i["tier"],
                "identity": [i.get("source_id"), i.get("snapshot_id"),
                             i.get("extraction_id"), i.get("segment_id"),
                             i.get("evidence_id")],
                "text_sha256": hashlib.sha256(
                    i["text"].encode("utf-8")).hexdigest(),
                "truncated": i["truncated"],
            } for i in items],
        }
        digest = hashlib.sha256(_canonical(digest_payload)).hexdigest()

        package = {
            "context_id": f"ctx-{digest[:16]}",
            "project_id": project_id,
            "session_id": session_id,
            "purpose": purpose,
            "focus_query": focus_query,
            "question": question_text,
            "requested_retrieval_mode": requested_mode,
            "effective_retrieval_mode": effective_mode,
            "reranker": reranker_identity,
            "rerank_effective": rerank_effective,
            "selected_items": len(items),
            "omitted": omitted,
            "distinct_sources": len({str(i.get("source_id"))
                                     for i in items if i.get("source_id")}),
            "bounds": bounds.to_dict(),
            "warnings": warnings,
            "status": status,
            "digest": digest,
            "created_at": _now(),
            "items": items,
        }
        retrieval_store._bump(project_id, "context_packages_created", 1)
        retrieval_store._bump(project_id, "context_items_selected", len(items))
        total_omitted = sum(omitted.values())
        retrieval_store._bump(project_id, "context_items_omitted",
                              total_omitted)
        retrieval_store._bump(project_id, "duplicate_segments_removed",
                              omitted["duplicates_removed"])
        return package

    # -- rendering / adapters --------------------------------------------

    @staticmethod
    def render(package: dict[str, Any], *,
               workflow_instructions: str = "") -> str:
        """Structured model-facing rendering. Source material stays in
        delimited UNTRUSTED blocks, structurally separated from workflow
        instructions."""
        lines: list[str] = ["WORKFLOW INSTRUCTIONS"]
        if workflow_instructions:
            lines.append(workflow_instructions)
        lines.append("")
        lines.append("RESEARCH QUESTION")
        lines.append(str(package.get("question")
                         or package.get("focus_query", "")))
        lines.append("")
        lines.append("SOURCE MATERIAL — UNTRUSTED DATA")
        for item in package.get("items", []):
            lines.append(f"[{item['context_item_id']}] "
                         f"type={item['context_item_type']} "
                         f"tier={item['tier']}")
            lines.append(
                f"source={item.get('source_id')} "
                f"snapshot={item.get('snapshot_id')} "
                f"segment={item.get('segment_id')} "
                f"locator={json.dumps(item.get('locator') or {}, sort_keys=True)}")
            if item.get("truncated"):
                lines.append(
                    f"(truncated=true original_length="
                    f"{item['original_length']} rendered_length="
                    f"{item['rendered_length']})")
            lines.append(item["text"])
            lines.append("")
        return "\n".join(lines)


def build_from_local_search(builder: "ResearchContextBuilder", *,
                            retrieval_store: SemanticRetrievalStore,
                            research_store, project_id: str,
                            session_id: str | None, purpose: str,
                            search_result: dict[str, Any],
                            claim_ids: list[str] | None = None,
                            question_text: str | None = None,
                            bounds: ContextBounds | None = None,
                            context_store: "ContextStore | None" = None) \
        -> dict[str, Any]:
    """Adapter over an R2.1/R2.2 HybridLocalSearch result: lexical-only
    hits are first enriched with full authoritative identity. When a
    context store is supplied the package is durably persisted so the
    orchestrator's journal reference stays resolvable across restarts."""
    hits = [dict(h) for h in search_result.get("hits", [])]
    if search_result.get("effective_mode") == RetrievalMode.LEXICAL.value:
        normalized = [{"segment_ref": h.get("segment_id"), **h}
                      for h in hits]
        enriched = retrieval_store.enrich_lexical_hits(
            research_store, normalized, project_id=project_id)
        for entry in enriched:
            entry.setdefault("preview", entry.get("excerpt", ""))
            if not entry.get("segment_id"):
                entry["segment_id"] = entry.get("segment_ref")
        hits = enriched
    package = builder.build(
        retrieval_store=retrieval_store, research_store=research_store,
        project_id=project_id, session_id=session_id, purpose=purpose,
        focus_query=str(search_result.get("query", "")), candidates=hits,
        requested_mode=str(search_result.get("requested_mode", "lexical")),
        effective_mode=str(search_result.get("effective_mode", "lexical")),
        claim_ids=claim_ids, question_text=question_text, bounds=bounds)
    if context_store is not None:
        context_store.save_package(package)
    return package


def synthesis_inputs_from_package(package: dict[str, Any]) \
        -> list[dict[str, Any]]:
    """ZCode-side adapter: flatten a package into grounded inputs for a
    later R1.4 integration slice (identities preserved, nothing executed)."""
    return [{
        "item_type": item["context_item_type"],
        "tier": item["tier"],
        "text": item["text"],
        "source_id": item.get("source_id"),
        "snapshot_id": item.get("snapshot_id"),
        "segment_id": item.get("segment_id"),
        "evidence_id": item.get("evidence_id"),
        "locator": item.get("locator"),
    } for item in package.get("items", [])]
