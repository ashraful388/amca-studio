"""R2.2 local semantic + hybrid retrieval over the research substrate.

ISOLATED derived-index layer (manager-reply58): reads the authoritative R1
tables through read-only queries and the public store interfaces; never
modifies them. Frozen semantics:

- SourceSnapshot / ExtractedSegment stay THE authoritative content;
  embeddings are a rebuildable derived representation.
- Every embedding carries full identity: source/snapshot/extraction/segment
  ids + segment_digest + provider/model/version/dimension. A vector from one
  embedding space is NEVER compared against another.
- RETRIEVAL HIT != EVIDENCE != SOURCE QUALITY != TRUTH. Similarity is
  labelled retrieval similarity - never confidence, truth probability or
  authority. Relations still belong to R1.4.
- Evidence chunks are created ONLY by explicit pinning after revalidation of
  the current segment digest/locator (the R1.2 offset-verified path).
- Local-first: the bundled deterministic provider is fully offline; a
  provider that is not marked local is refused outright. No cloud fallback.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

MAX_QUERY_LENGTH = 512
MAX_DIMENSION = 4096
MAX_CHARS_PER_ITEM = 8000
MAX_SEGMENTS_PER_OPERATION = 500
MAX_BATCH_SIZE = 64
MAX_TOTAL_VECTOR_BYTES = 64 * 1024 * 1024
MAX_RESULT_LIMIT = 100
DEFAULT_RRF_K = 60
VECTOR_DTYPE = "float32-le"
PREVIEW_CHARS = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class EmbeddingError(ValueError):
    """Bounds/dimension/value violations in the derived-vector layer."""


class SemanticBackendUnavailable(RuntimeError):
    """No embedding backend is configured; semantic mode cannot run."""


class RetrievalMode(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


# ---------------------------------------------------------------------------
# embedding providers


def _assert_finite_vector(vector: list[float], dimension: int | None,
                          where: str) -> list[float]:
    if not isinstance(vector, (list, tuple)) or not vector:
        raise EmbeddingError(f"{where}: empty/non-list vector")
    if len(vector) > MAX_DIMENSION:
        raise EmbeddingError(f"{where}: dimension above bound")
    if dimension is not None and len(vector) != dimension:
        raise EmbeddingError(
            f"{where}: dimension mismatch ({len(vector)} != {dimension})")
    for value in vector:
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise EmbeddingError(f"{where}: non-finite vector component")
    return [float(v) for v in vector]


class EmbeddingProvider:
    """Provider-neutral embedding interface (vendor/model agnostic)."""

    provider_id = "abstract"
    version = "0"
    model_id = "abstract"
    dimension = 0
    #: Local providers keep project text on-device; non-local providers are
    #: refused by this layer entirely in R2.2.
    is_local = True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """Offline, order-stable hashed bag-of-features embeddings.

    Tokens map to fixed buckets via SHA-256 (stable across processes),
    counts are accumulated and the result L2-normalized. Identical input
    text always yields an identical vector for a given dimension.
    """

    provider_id = "deterministic-local"
    version = "r2.2-1.0.0"
    model_id = "hashed-bof"

    def __init__(self, dimension: int = 64) -> None:
        if not 1 <= int(dimension) <= MAX_DIMENSION:
            raise EmbeddingError("dimension out of range")
        self.dimension = int(dimension)

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "little") % self.dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            if not isinstance(text, str):
                raise EmbeddingError("embedding input must be text")
            if len(text) > MAX_CHARS_PER_ITEM:
                raise EmbeddingError(
                    f"text exceeds per-item character bound "
                    f"({len(text)} > {MAX_CHARS_PER_ITEM})")
            counts = [0.0] * self.dimension
            token = ""
            for ch in text.lower():
                if ch.isalnum():
                    token += ch
                elif token:
                    counts[self._bucket(token)] += 1.0
                    token = ""
            if token:
                counts[self._bucket(token)] += 1.0
            norm = math.sqrt(sum(c * c for c in counts))
            vector = ([c / norm for c in counts] if norm > 0.0
                      else [0.0] * self.dimension)
            vectors.append(_assert_finite_vector(
                vector, self.dimension, f"embed:{text[:40]!r}"))
        return vectors


def encode_vector(vector: list[float]) -> tuple[bytes, str]:
    """Bounded binary representation + integrity digest."""
    checked = _assert_finite_vector(vector, None, "encode")
    blob = struct.pack(f"<{len(checked)}f", *checked)
    digest = hashlib.sha256(
        blob + str(len(checked)).encode()).hexdigest()
    return blob, digest


def decode_vector(blob: bytes, dimension: int, digest: str) -> list[float]:
    if not isinstance(blob, bytes) or len(blob) != dimension * 4:
        raise EmbeddingError("vector blob length does not match dimension")
    if hashlib.sha256(blob + str(dimension).encode()).hexdigest() != digest:
        raise EmbeddingError("vector digest mismatch")
    values = list(struct.unpack(f"<{dimension}f", blob))
    return _assert_finite_vector(values, dimension, "decode")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Explicit baseline metric: cosine similarity over float vectors.

    Zero-norm inputs safely yield 0.0 (never NaN, never an error)."""
    if len(a) != len(b):
        raise EmbeddingError("cosine dimension mismatch")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def segment_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class SemanticSearchResult:
    source_id: str
    snapshot_id: str
    extraction_id: str
    segment_id: str
    locator: dict[str, Any]
    #: Retrieval similarity ONLY - never confidence, truth or authority.
    semantic_similarity: float | None
    segment_digest: str
    preview: str
    embedding_provenance: dict[str, Any]
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    hybrid_rank: int | None = None
    hybrid_score: float | None = None
    fusion_method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "snapshot_id": self.snapshot_id,
            "extraction_id": self.extraction_id,
            "segment_id": self.segment_id,
            "locator": self.locator,
            "semantic_similarity": self.semantic_similarity,
            "segment_digest": self.segment_digest,
            "preview": self.preview,
            "embedding_provenance": self.embedding_provenance,
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "hybrid_rank": self.hybrid_rank,
            "hybrid_score": self.hybrid_score,
            "fusion_method": self.fusion_method,
        }


class SemanticRetrievalStore:
    """Durable derived embedding records + retrieval metrics in amca.db."""

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
                CREATE TABLE IF NOT EXISTS research_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT,
                    source_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    extraction_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    segment_digest TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    provider_version TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    dtype TEXT NOT NULL,
                    vector_blob BLOB NOT NULL,
                    vector_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, segment_id, provider_id, model_id,
                           provider_version, dimension)
                );
                CREATE TABLE IF NOT EXISTS research_retrieval_metrics (
                    project_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (project_id, metric)
                );
            """)

    # -- indexing --------------------------------------------------------

    def _segments_query(self, *, session_id: str | None,
                        snapshot_id: str | None) -> str:
        query = """
            SELECT seg.segment_id AS segment_id, seg.text AS text,
                   ext.extraction_id AS extraction_id,
                   ext.project_id AS project_id,
                   snap.snapshot_id AS snapshot_id,
                   src.source_id AS source_id,
                   src.session_id AS session_id,
                   src.source_kind AS source_kind
            FROM research_extracted_segments seg
            JOIN research_extractions ext
              ON ext.extraction_id = seg.extraction_id
            JOIN research_source_snapshots snap
              ON snap.snapshot_id = ext.snapshot_id
            JOIN research_sources src ON src.source_id = snap.source_id
            WHERE ext.status = 'ok' AND ext.project_id = ?"""
        if session_id is not None:
            query += " AND src.session_id = ?"
        if snapshot_id is not None:
            query += " AND snap.snapshot_id = ?"
        query += " ORDER BY seg.segment_id LIMIT ?"
        return query

    def index_segments(self, research_store, provider: EmbeddingProvider,
                       *, project_id: str, session_id: str | None = None,
                       snapshot_id: str | None = None,
                       batch_size: int = MAX_BATCH_SIZE) \
            -> dict[str, Any]:
        """Embed ok-extraction segments into durable derived records.

        Read-only against R1 tables; repeated runs are idempotent for the
        same segment + embedding identity."""
        if not getattr(provider, "is_local", False):
            raise EmbeddingError(
                "refusing non-local embedding provider in R2.2")
        if not 1 <= int(batch_size) <= MAX_BATCH_SIZE:
            raise EmbeddingError("batch size out of range")
        params: list[Any] = [project_id]
        if session_id is not None:
            params.append(session_id)
        if snapshot_id is not None:
            params.append(snapshot_id)
        params.append(MAX_SEGMENTS_PER_OPERATION)
        with research_store._connect() as connection:
            rows = connection.execute(
                self._segments_query(session_id=session_id,
                                     snapshot_id=snapshot_id),
                tuple(params)).fetchall()

        summary: dict[str, Any] = {
            "segments_scanned": len(rows), "indexed": 0, "failures": 0,
            "batches": 0, "failures_detail": []}
        pending: list[dict[str, Any]] = []

        def flush() -> None:
            if not pending:
                return
            try:
                vectors = provider.embed_texts(
                    [item["text"] for item in pending])
            except EmbeddingError as exc:
                summary["failures"] += len(pending)
                summary["failures_detail"].append({"error": str(exc)})
                pending.clear()
                return
            summary["batches"] += 1
            for item, vector in zip(pending, vectors):
                self._store_embedding(item, vector, provider, project_id)
                summary["indexed"] += 1
            pending.clear()

        for row in rows:
            record = dict(row)
            text = record.pop("text")
            record["segment_digest_value"] = segment_digest(text)
            record["text_length"] = len(text)
            if len(text) > MAX_CHARS_PER_ITEM:
                summary["failures"] += 1
                summary["failures_detail"].append({
                    "segment_id": record["segment_id"],
                    "error": "segment exceeds per-item character bound"})
                continue
            pending.append({**record, "text": text})
            if len(pending) >= int(batch_size):
                flush()
        flush()
        total_bytes = self.vector_bytes(project_id)
        if total_bytes > MAX_TOTAL_VECTOR_BYTES:
            raise EmbeddingError("total vector storage exceeds bound")
        self._bump(project_id, "segments_indexed", summary["indexed"])
        self._bump(project_id, "embedding_items",
                   summary["indexed"])
        self._bump(project_id, "embedding_batches", summary["batches"])
        self._bump(project_id, "embedding_failures", summary["failures"])
        return summary

    def _store_embedding(self, item: dict[str, Any],
                         vector: list[float],
                         provider: EmbeddingProvider,
                         project_id: str) -> None:
        blob, digest = encode_vector(vector)
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO research_embeddings
                   (embedding_id, project_id, session_id, source_id,
                    snapshot_id, extraction_id, segment_id, segment_digest,
                    provider_id, model_id, provider_version, dimension,
                    dtype, vector_blob, vector_digest, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_uuid("emb"), project_id, item.get("session_id"),
                 item["source_id"], item["snapshot_id"],
                 item["extraction_id"], item["segment_id"],
                 item["segment_digest_value"], provider.provider_id,
                 provider.model_id, provider.version, len(vector),
                 VECTOR_DTYPE, blob, digest, _now()))

    def enrich_lexical_hits(self, research_store, hits: list[dict[str, Any]],
                            *, project_id: str) -> list[dict[str, Any]]:
        """Fill full authoritative identity into lexical FTS hits so fused
        results always carry source/snapshot/extraction ids + current
        digest (read-only against R1 tables)."""
        enriched = []
        for hit in hits or []:
            segment_id = str(hit.get("segment_ref"))
            with research_store._connect() as connection:
                row = connection.execute(
                    """SELECT seg.text AS text,
                              seg.locator_json AS locator_json,
                              ext.extraction_id AS extraction_id,
                              snap.snapshot_id AS snapshot_id,
                              src.source_id AS source_id
                       FROM research_extracted_segments seg
                       JOIN research_extractions ext
                         ON ext.extraction_id = seg.extraction_id
                       JOIN research_source_snapshots snap
                         ON snap.snapshot_id = ext.snapshot_id
                       JOIN research_sources src
                         ON src.source_id = snap.source_id
                      WHERE seg.segment_id = ?""", (segment_id,)).fetchone()
            if row is None:
                continue
            record = dict(hit)
            record.update({
                "source_id": row["source_id"],
                "snapshot_id": row["snapshot_id"],
                "extraction_id": row["extraction_id"],
                "locator": json.loads(row["locator_json"] or "{}"),
                "segment_digest": segment_digest(row["text"]),
                "preview": row["text"][:PREVIEW_CHARS],
            })
            enriched.append(record)
        return enriched

    def vector_bytes(self, project_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(vector_blob)), 0) AS total "
                "FROM research_embeddings WHERE project_id = ?",
                (project_id,)).fetchone()
        return int(row["total"])

    def delete_project_vectors(self, project_id: str) -> int:
        """Derived-data deletion ONLY: authoritative rows are untouched."""
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM research_embeddings WHERE project_id = ?",
                (project_id,)).rowcount
        return deleted

    # -- semantic query ---------------------------------------------------

    def semantic_search(self, research_store, provider: EmbeddingProvider,
                        *, project_id: str, query: str,
                        session_id: str | None = None,
                        source_kind: str | None = None,
                        limit: int = 10) -> list[SemanticSearchResult]:
        if provider is None:
            raise SemanticBackendUnavailable(
                "no embedding backend configured")
        if len(query) > MAX_QUERY_LENGTH:
            raise EmbeddingError("query exceeds length bound")
        limit = max(1, min(int(limit), MAX_RESULT_LIMIT))
        query_vector = provider.embed_texts([query])[0]

        sql = """
            SELECT e.segment_id AS segment_id,
                   e.segment_digest AS stored_digest,
                   e.dimension AS dimension, e.dtype AS dtype,
                   e.vector_blob AS vector_blob,
                   e.vector_digest AS vector_digest,
                   e.provider_id AS provider_id, e.model_id AS model_id,
                   e.provider_version AS provider_version,
                   seg.text AS current_text,
                   seg.locator_json AS locator_json,
                   ext.extraction_id AS extraction_id,
                   snap.snapshot_id AS snapshot_id,
                   src.source_id AS source_id,
                   src.session_id AS session_id,
                   src.source_kind AS source_kind
            FROM research_embeddings e
            JOIN research_extracted_segments seg
              ON seg.segment_id = e.segment_id
            JOIN research_extractions ext
              ON ext.extraction_id = seg.extraction_id
            JOIN research_source_snapshots snap
              ON snap.snapshot_id = ext.snapshot_id
            JOIN research_sources src ON src.source_id = snap.source_id
            WHERE e.project_id = ? AND e.provider_id = ?
              AND e.model_id = ? AND e.provider_version = ?"""
        params: list[Any] = [project_id, provider.provider_id,
                             provider.model_id, provider.version]
        if session_id is not None:
            sql += " AND src.session_id = ?"
            params.append(session_id)
        if source_kind is not None:
            sql += " AND src.source_kind = ?"
            params.append(source_kind)

        results: list[SemanticSearchResult] = []
        stale = 0
        with research_store._connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        for row in rows:
            record = dict(row)
            current_digest = segment_digest(record["current_text"])
            if current_digest != record["stored_digest"]:
                stale += 1
                continue
            try:
                vector = decode_vector(record["vector_blob"],
                                       int(record["dimension"]),
                                       record["vector_digest"])
            except EmbeddingError:
                stale += 1
                continue
            similarity = round(cosine_similarity(query_vector, vector), 6)
            locator = json.loads(record["locator_json"] or "{}")
            results.append(SemanticSearchResult(
                source_id=record["source_id"],
                snapshot_id=record["snapshot_id"],
                extraction_id=record["extraction_id"],
                segment_id=record["segment_id"],
                locator=locator,
                semantic_similarity=max(-1.0, min(1.0, similarity)),
                segment_digest=current_digest,
                preview=record["current_text"][:PREVIEW_CHARS],
                embedding_provenance={
                    "provider_id": record["provider_id"],
                    "model_id": record["model_id"],
                    "provider_version": record["provider_version"],
                    "dimension": int(record["dimension"]),
                    "dtype": record["dtype"],
                    "metric": "cosine",
                    "similarity_label": "retrieval_similarity",
                },
            ))
        self._bump(project_id, "semantic_queries", 1)
        self._bump(project_id, "stale_vectors_excluded", stale)
        ordered = sorted(results, key=lambda r: (-r.semantic_similarity,
                                                 r.snapshot_id))
        return ordered[:limit]

    def _bump(self, project_id: str, metric: str, amount: int = 1) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_retrieval_metrics
                   (project_id, metric, value) VALUES (?, ?, ?)
                   ON CONFLICT(project_id, metric)
                   DO UPDATE SET value = value + excluded.value""",
                (project_id, metric, int(amount)))

    def retrieval_metrics(self, project_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT metric, value FROM research_retrieval_metrics "
                "WHERE project_id = ?", (project_id,)).fetchall()
        return {row["metric"]: int(row["value"]) for row in rows}


# ---------------------------------------------------------------------------
# hybrid fusion + explicit evidence pinning


def hybrid_fuse(lexical_hits: list[dict[str, Any]],
                semantic_hits: list[SemanticSearchResult], *,
                limit: int = 10, rrf_k: int = DEFAULT_RRF_K) \
        -> list[SemanticSearchResult]:
    """Reciprocal Rank Fusion over incomparable rank lists.

    Deterministic; single-path survivors keep their own provenance and never
    receive fabricated scores from the other path."""
    if not 1 <= int(rrf_k) <= 1000:
        raise EmbeddingError("rrf_k out of range")
    scores: dict[str, float] = {}
    merged: dict[str, SemanticSearchResult] = {}

    for rank, hit in enumerate(lexical_hits, start=1):
        segment_id = str(hit.get("segment_ref"))
        entry = merged.setdefault(segment_id, SemanticSearchResult(
            source_id=str(hit.get("source_id", "")),
            snapshot_id=str(hit.get("snapshot_id", "")),
            extraction_id=str(hit.get("extraction_id", "")),
            segment_id=segment_id, locator=dict(hit.get("locator") or {}),
            semantic_similarity=None, segment_digest="", preview=str(
                hit.get("excerpt", ""))[:PREVIEW_CHARS],
            embedding_provenance={}))
        entry.lexical_rank = rank
        scores[segment_id] = scores.get(segment_id, 0.0) + 1.0 / (rrf_k + rank)

    for rank, hit in enumerate(semantic_hits, start=1):
        entry = merged.setdefault(hit.segment_id, hit)
        entry.semantic_rank = rank
        entry.semantic_similarity = hit.semantic_similarity
        if entry.source_id == "":
            entry.source_id = hit.source_id
        if entry.snapshot_id == "":
            entry.snapshot_id = hit.snapshot_id
        if entry.extraction_id == "":
            entry.extraction_id = hit.extraction_id
        if not entry.locator:
            entry.locator = dict(hit.locator)
        if entry.segment_digest == "":
            entry.segment_digest = hit.segment_digest
        scores[hit.segment_id] = scores.get(hit.segment_id, 0.0) \
            + 1.0 / (rrf_k + rank)

    ordered_ids = sorted(scores, key=lambda s: (-scores[s], s))
    fused: list[SemanticSearchResult] = []
    for hybrid_rank, segment_id in enumerate(ordered_ids[:limit], start=1):
        entry = merged[segment_id]
        entry.hybrid_rank = hybrid_rank
        entry.hybrid_score = round(scores[segment_id], 8)
        entry.fusion_method = f"rrf(k={int(rrf_k)})"
        fused.append(entry)
    return fused


def evidence_from_retrieval_hit(research_store, ingestor,
                                hit: SemanticSearchResult, *,
                                project_id: str) -> dict[str, Any]:
    """EXPLICIT pin: revalidate the CURRENT segment digest and locator
    occurrence, then create the evidence chunk via the accepted R1.2
    offset-validated path. Stale/unknown segments can never become
    evidence through this route."""
    with research_store._connect() as connection:
        row = connection.execute(
            """SELECT seg.text AS text, ext.snapshot_id AS snapshot_id,
                      ext.status AS status
               FROM research_extracted_segments seg
               JOIN research_extractions ext
                 ON ext.extraction_id = seg.extraction_id
              WHERE seg.segment_id = ?""",
            (hit.segment_id,)).fetchone()
    if row is None or row["status"] != "ok":
        raise EmbeddingError("retrieval hit references unknown segment")
    current_digest = segment_digest(row["text"])
    if current_digest != hit.segment_digest:
        raise EmbeddingError(
            "stale retrieval hit: segment changed since indexing")
    excerpt = hit.preview
    start = row["text"].find(excerpt[:120]) if excerpt else -1
    if start < 0:
        raise EmbeddingError("preview no longer present in segment")
    locator = dict(hit.locator or {})
    locator.update({"start": start, "end": start + len(excerpt)})
    return research_store.add_evidence(
        row["snapshot_id"], project_id=project_id, locator=locator,
        excerpt=excerpt)


class HybridLocalSearch:
    """Facade the R2.1 orchestrator uses for gated local retrieval."""

    def __init__(self, *, ingestor, retrieval_store: SemanticRetrievalStore,
                 provider: EmbeddingProvider | None = None) -> None:
        self.ingestor = ingestor
        self.retrieval_store = retrieval_store
        self.provider = provider

    def local_search(self, query: str, *, project_id: str,
                     mode: str = RetrievalMode.LEXICAL.value,
                     session_id: str | None = None,
                     source_kind: str | None = None,
                     limit: int = 10) -> dict[str, Any]:
        mode = str(mode).lower()
        if mode not in (RetrievalMode.LEXICAL.value,
                        RetrievalMode.SEMANTIC.value,
                        RetrievalMode.HYBRID.value):
            raise EmbeddingError(f"unknown retrieval mode: {mode!r}")
        lexical_hits = self.ingestor.search(query, project_id=project_id,
                                            session_id=session_id,
                                            limit=limit)
        self.retrieval_store._bump(project_id, "lexical_queries", 1)

        effective_mode = mode
        warning = None
        embedding_model = None
        semantic_hits: list[SemanticSearchResult] = []
        if mode in (RetrievalMode.SEMANTIC.value,
                    RetrievalMode.HYBRID.value):
            try:
                if self.provider is None:
                    raise SemanticBackendUnavailable(
                        "no embedding backend configured")
                semantic_hits = self.retrieval_store.semantic_search(
                    self.ingestor.store, self.provider,
                    project_id=project_id, query=query,
                    session_id=session_id, source_kind=source_kind,
                    limit=limit)
                embedding_model = (f"{self.provider.provider_id}:"
                                   f"{self.provider.model_id}")
            except SemanticBackendUnavailable:
                self.retrieval_store._bump(
                    project_id, "semantic_backend_unavailable", 1)
                if mode == RetrievalMode.SEMANTIC.value:
                    raise
                effective_mode = RetrievalMode.LEXICAL.value
                warning = "SEMANTIC_BACKEND_UNAVAILABLE"

        if mode == RetrievalMode.HYBRID.value \
                and effective_mode == RetrievalMode.HYBRID.value:
            lexical_enriched = self.retrieval_store.enrich_lexical_hits(
                self.ingestor.store, lexical_hits, project_id=project_id)
            hits = [r.to_dict() for r in hybrid_fuse(
                lexical_enriched, semantic_hits, limit=limit)]
            self.retrieval_store._bump(project_id, "hybrid_queries", 1)
        elif effective_mode == RetrievalMode.SEMANTIC.value:
            hits = [r.to_dict() for r in semantic_hits]
        else:
            hits = [{
                "segment_id": h.get("segment_ref"),
                "preview": h.get("excerpt", ""),
                "lexical_rank": index + 1,
            } for index, h in enumerate(lexical_hits)]

        return {
            "requested_mode": mode,
            "effective_mode": effective_mode,
            "warning": warning,
            "embedding_model": embedding_model,
            "query": query,
            "filters": {"session_id": session_id,
                        "source_kind": source_kind},
            "hits": hits,
            "hit_count": len(hits),
        }
