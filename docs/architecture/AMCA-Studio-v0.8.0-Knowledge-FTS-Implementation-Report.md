# AMCA Studio: v0.8.0 Knowledge FTS5 Retrieval Implementation Report

**Milestone**: `AG-AMCA-STUDIO-V0.8.0-KNOWLEDGE-INTELLIGENCE-IMPLEMENTATION-FOUNDATION-01`  
**Product**: AMCA Studio (v0.8.0 Implementation Track)  
**Status**: **KNOWLEDGE FTS5 FOUNDATION IMPLEMENTED & VERIFIED (PASS)**  
**Target Version**: `v0.8.0-dev`  
**Date**: September 2, 2026  

---

## 1. Executive Summary

This milestone delivers the first production component of v0.8.0 Knowledge Intelligence:
1. **FTS5 Storage Layer**: Implemented `KnowledgeFtsStore` (`software/src/amca/knowledge_fts.py`) backed by native SQLite FTS5 virtual shadow tables with `porter unicode61` stemming and tokenization.
2. **Deterministic Synchronization**: Integrated idempotent `index_artifact(...)` and `remove_artifact(...)` lifecycle handlers preserving synchronization with underlying artifact records.
3. **Workspace Isolation**: Verified that search operations strictly filter by `workspace_id`, ensuring complete isolation between tenant/client projects.
4. **Pre-Index Security Sanitization**: Incorporated regex-based credential stripping (`sk-`, `ghp_`, `xoxb-`, private keys, passwords) before text reaches the FTS5 index.
5. **Zero Regression Guarantee**: Confirmed standard `ArtifactStore` and `MemoryStore` operations remain completely untouched and unaltered.

---

## 2. Capability & Security Matrix

| Feature / Gate | Specification | Verified Status |
| :--- | :--- | :--- |
| **Native Engine** | SQLite FTS5 with BM25 ranking | **PASS** |
| **Workspace Partitioning** | Strict `workspace_id` filtering | **PASS** |
| **Secret Sanitization** | `sanitize_knowledge_text` pre-index redaction | **PASS** |
| **Lifecycle Synchronization**| Insert/Update/Delete reflection | **PASS** |
| **Air-Gap Compliance** | 100% offline, zero cloud calls | **PASS** |

---

## 3. Test & Verification Summary

| Test Area | Tested Invariant | Result |
| :--- | :--- | :--- |
| **Index Creation** | `artifacts_fts5_idx` table exists and initializes | **PASS** |
| **Isolation** | Workspace A cannot retrieve Workspace B records | **PASS** |
| **Synchronization** | Create -> searchable, Update -> updated, Delete -> removed | **PASS** |
| **Security** | Raw credentials redacted, never indexed in plain text | **PASS** |
| **Regression** | Standard ArtifactStore functions unaltered | **PASS** |
| **Studio Regression** | Full studio regression suite passes across all 219 tests | **PASS** |

