"""AMCA Studio v0.8.0 Knowledge Vector Storage Foundation (AG-AMCA-STUDIO-V0.8.0).

Provides local-first offline embedding provider abstraction, float32 BLOB vector storage,
workspace partitioning, and lifecycle synchronization for AMCA Studio artifacts.
Strictly local-first, zero cloud dependencies, zero telemetry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
from pathlib import Path
import sqlite3
import struct
from typing import Any, Sequence

from amca.knowledge_fts import sanitize_knowledge_text


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def pack_vector_blob(vector: Sequence[float]) -> bytes:
    """Serializes a float sequence into a compact float32 binary BLOB."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector_blob(blob: bytes) -> tuple[float, ...]:
    """Deserializes a float32 binary BLOB into a float tuple."""
    count = len(blob) // 4
    return struct.unpack(f"<{count}f", blob)


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Computes cosine similarity between two normalized vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class LocalEmbeddingProvider(ABC):
    """Abstract interface for offline embedding providers."""

    @abstractmethod
    def embed_text(self, text: str) -> tuple[float, ...]:
        """Embeds sanitized text into a normalized float vector."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        ...


class DeterministicLocalEmbeddingProvider(LocalEmbeddingProvider):
    """Deterministic offline reference embedding provider.

    Uses SHA-256 seed hashing to produce deterministic 384-dimensional
    unit-normalized float32 vectors completely offline with 0 exterior dependencies.
    """

    def __init__(self, model_name: str = "amca-offline-minilm-ref", dimension: int = 384) -> None:
        self._model_name = model_name
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_text(self, text: str) -> tuple[float, ...]:
        safe_text = sanitize_knowledge_text(text)
        tokens = safe_text.lower().split()
        if not tokens:
            return tuple(0.0 for _ in range(self._dimension))

        accum = [0.0] * self._dimension
        for token in tokens:
            h = hashlib.sha256(token.encode("utf-8")).digest()
            for dim_idx in range(self._dimension):
                byte_val = h[dim_idx % len(h)]
                sign = 1.0 if (byte_val % 2 == 0) else -1.0
                accum[dim_idx] += sign * (byte_val / 255.0)

        norm = math.sqrt(sum(x * x for x in accum))
        if norm == 0.0:
            return tuple(accum)
        return tuple(float(x / norm) for x in accum)


@dataclass(frozen=True, slots=True)
class StoredVectorRecord:
    artifact_id: str
    workspace_id: str
    model_name: str
    dimension: int
    vector: tuple[float, ...]
    updated_at: str


class KnowledgeVectorStore:
    """SQLite-backed vector BLOB store partitioned by workspace_id."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_vectors (
                    artifact_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, workspace_id)
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_vectors_workspace 
                ON artifact_vectors (workspace_id);
                """
            )

    def store_vector(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        model_name: str,
        vector: Sequence[float],
    ) -> None:
        blob = pack_vector_blob(vector)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO artifact_vectors 
                (artifact_id, workspace_id, model_name, dimension, vector_blob, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(artifact_id).strip(),
                    str(workspace_id).strip(),
                    str(model_name).strip(),
                    len(vector),
                    blob,
                    _now(),
                ),
            )

    def get_vector(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
    ) -> StoredVectorRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT artifact_id, workspace_id, model_name, dimension, vector_blob, updated_at
                FROM artifact_vectors
                WHERE artifact_id = ? AND workspace_id = ?
                """,
                (str(artifact_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if not row:
                return None
            vector = unpack_vector_blob(row["vector_blob"])
            return StoredVectorRecord(
                artifact_id=str(row["artifact_id"]),
                workspace_id=str(row["workspace_id"]),
                model_name=str(row["model_name"]),
                dimension=int(row["dimension"]),
                vector=vector,
                updated_at=str(row["updated_at"]),
            )

    def delete_vector(self, *, artifact_id: str, workspace_id: str) -> bool:
        with sqlite3.connect(self.path, timeout=10) as db:
            cursor = db.execute(
                "DELETE FROM artifact_vectors WHERE artifact_id = ? AND workspace_id = ?",
                (str(artifact_id).strip(), str(workspace_id).strip()),
            )
            return cursor.rowcount > 0

    def list_workspace_vectors(self, *, workspace_id: str) -> list[StoredVectorRecord]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT artifact_id, workspace_id, model_name, dimension, vector_blob, updated_at
                FROM artifact_vectors
                WHERE workspace_id = ?
                """,
                (str(workspace_id).strip(),),
            ).fetchall()
            return [
                StoredVectorRecord(
                    artifact_id=str(r["artifact_id"]),
                    workspace_id=str(r["workspace_id"]),
                    model_name=str(r["model_name"]),
                    dimension=int(r["dimension"]),
                    vector=unpack_vector_blob(r["vector_blob"]),
                    updated_at=str(r["updated_at"]),
                )
                for r in rows
            ]

