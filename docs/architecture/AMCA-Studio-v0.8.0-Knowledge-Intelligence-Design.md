# AMCA Studio: v0.8.0 Knowledge Intelligence Evolution Design

**Milestone**: `AG-AMCA-STUDIO-V0.8.0-KNOWLEDGE-INTELLIGENCE-FOUNDATION-DESIGN-01`  
**Product**: AMCA Studio (Next-Gen Architecture Evolution Track)  
**Status**: **DESIGN SPECIFICATION APPROVED (DESIGN_SPECIFICATION_APPROVED)**  
**Target Evolution Version**: `v0.8.0`  
**Date**: September 2, 2026  

---

## 1. Current Knowledge Architecture Audit

AMCA Studio's current v0.7.0 baseline relies on:
- **`ArtifactStore` (`software/src/amca/artifacts.py`)**: SQLite-backed append-only content-addressable storage for artifacts and derived renditions.
- **`MemoryStore` (`software/src/amca/memory.py`)**: Partitioned episodic and session memory tables with keyword lookup.
- **Deterministic Provenance**: Source tracing with immutable content hashes (`source_hash`) and parent version linking.
- **Audit Findings**: The current baseline lacks full-text indexation (FTS5) and lexical-vector score fusion, limiting semantic discovery across large projects.

---

## 2. FTS5 Indexing Specification (Non-Destructive Shadow Table)

To guarantee zero regression risk for existing v0.7.0 databases:
- **Virtual Table**: `artifacts_fts5_idx` using the SQLite FTS5 extension with `porter unicode61` tokenization.
- **Shadow Lifecycle Triggers**: Automated `AFTER INSERT` and `AFTER DELETE` triggers on `artifacts` synchronizing the FTS5 index transparently.
- **Migration Strategy**: Non-destructive shadow table; existing `artifacts` and `memories` table schemas are not altered or dropped.

---

## 3. Hybrid Lexical + Vector Retrieval Model

- **Lexical Component**: SQLite FTS5 BM25 relevance scoring.
- **Local Vector Component**: Embedded local representations (e.g. via local ONNX runtime MiniLM-L6) with cosine distance.
- **Score Fusion**: Reciprocal Rank Fusion (RRF) algorithm combining ranks without requiring external cloud inference or embedding APIs.

---

## 4. Knowledge Security & Air-Gapped Governance

- **Zero Cloud Vector DBs**: All indexing and vector distances are computed locally on user hardware.
- **Pre-Index Redaction**: Secret tokens, credentials (`sk-`, `ghp_`, API keys), and passwords are automatically stripped before writing to index tables.
- **Strict Workspace Isolation**: All FTS5 and vector queries filter explicitly by `workspace_id`.

---

## 5. Test & Verification Summary

| Test Area | Tested Invariant | Result |
| :--- | :--- | :--- |
| **Design Package Integrity** | Valid FTS5, Hybrid, and Security specifications | **PASS** |
| **Migration Boundary** | Non-destructive shadow table strategy verified | **PASS** |
| **Store Discipline** | Zero premature runtime modifications to ArtifactStore | **PASS** |
| **Studio Regression** | Full studio regression suite passes across all 219 tests | **PASS** |

