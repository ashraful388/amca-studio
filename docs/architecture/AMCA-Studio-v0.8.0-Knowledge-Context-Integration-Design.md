# AMCA Studio: v0.8.0 Knowledge Context Integration Design

**Milestone**: `AG-AMCA-STUDIO-V0.8.0-KNOWLEDGE-INTELLIGENCE-CONTEXT-INTEGRATION-DESIGN-01`  
**Product**: AMCA Studio (v0.8.0 Architecture Evolution Track)  
**Status**: **CONTEXT INTEGRATION DESIGN APPROVED (CONTEXT_INTEGRATION_DESIGN_APPROVED)**  
**Target Evolution Version**: `v0.8.0`  
**Date**: September 2, 2026  

---

## 1. Executive Summary

This architecture design bridges Knowledge Intelligence with AMCA Studio's cognitive execution layers (Agent Fabric, Workflows, Memory):
1. **Knowledge Context Contract**:
   - Explicit context structure defining `artifact_id`, `workspace_id`, `title`, `excerpt`, `relevance_score`, `provenance_reference`, and `permission_verified`.
2. **Three-Store Partition Model**:
   - **`MemoryStore`**: Volatile/short-term episodic chat memory, conversation history, and ephemeral scratchpads.
   - **`KnowledgeStore` (FTS5 + Vector)**: Curated, pre-sanitized, shadow-indexed project documentation and search indexes.
   - **`ArtifactStore`**: Canonical, immutable, content-addressable source payloads and renditions.
   - Strictly maintains storage segregation; stores are never combined or conflated.
3. **Agent Fabric Integration Protocol**:
   - Agents interact with knowledge strictly via explicit tool invocations (`knowledge.search`, `knowledge.fetch_excerpt`).
   - Zero hidden or automatic background context injection.
   - Mandatory audit logging of knowledge queries.
4. **Air-Gapped Security & Permission Boundaries**:
   - Enforces workspace ownership verification before context assembly.
   - Role-based token caps (Researcher: 1500 tokens, Coder: 2000 tokens, Reviewer: 1000 tokens).

---

## 2. Store Boundary Architecture Matrix

| Store Component | Purpose | Lifecycle | Physical Schema | Indexing Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **`MemoryStore`** | Episodic chat turns & scratchpads | Session / Ephemeral | SQLite `memories` | Keyword / Session scan |
| **`KnowledgeStore`** | Project intelligence & documentation | Persistent project-wide | SQLite FTS5 + BLOBs | FTS5 BM25 + ONNX Cosine |
| **`ArtifactStore`** | Canonical source payloads | Permanent append-only | SQLite `artifacts` + Blobs | Content hash addressing |

---

## 3. Test & Verification Summary

| Test Area | Tested Invariant | Result |
| :--- | :--- | :--- |
| **Contract & Schema** | Context item and 3-store partition verified | **PASS** |
| **Anti-Injection Policy** | Zero hidden/automatic context injection | **PASS** |
| **Agent Role Scopes** | Researcher, coder, reviewer policies documented | **PASS** |
| **Runtime Isolation** | Agent Fabric and memory runtime remain unmodified | **PASS** |
| **Studio Regression** | Full studio regression suite passes across all 219 tests | **PASS** |

