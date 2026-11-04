# AMCA Studio: v0.8.0 Agent Capability Negotiation & Governance Report

**Milestone**: `AG-AMCA-STUDIO-V0.8.0-AGENT-CAPABILITY-NEGOTIATION-AND-GOVERNANCE-FOUNDATION-01`  
**Product**: AMCA Studio (v0.8.0 Cognition Runtime & Architecture Track)  
**Status**: **AGENT CAPABILITY GOVERNANCE VERIFIED (PASS)**  
**Target Version**: `v0.8.0-dev`  
**Date**: September 2, 2026  

---

## 1. Executive Summary

This milestone creates the foundation for governed agent and model capability discovery and collaboration:
1. **Agent Capability Registry**:
   - Implemented `AgentCapabilityProfile` in `software/src/amca/agent_capability_governance.py`.
   - Records declared capabilities, permission scopes, and locality affinities (`LOCAL_ONLY`, `HYBRID`, `CLOUD_ALLOWED`).
   - **Fact-Based Invariant**: Recorded capabilities only; strictly zero inferred, assumed, or speculative abilities.
2. **Model Capability Registry**:
   - Implemented `ModelCapabilityProfile` capturing supported capabilities, providers, offline/cloud locality, context window, and governance requirements.
   - Strictly zero best-model selection, autonomous routing, or optimization scores.
3. **Deterministic Capability Matching Foundation**:
   - Implemented `DeterministicMatchCandidate` and matching engines for both agents and models.
   - Evaluates exact capability subsets/intersections with deterministic tie-breaking (sorted by exact match, matched count, candidate ID).
   - Strictly zero probabilistic ranking or ML selection.
4. **Collaboration Contract Lifecycle**:
   - Defined `CollaborationContractRecord` capturing agreed capabilities, assigned models, lead agent, and participants under explicit status transitions (`DRAFT` -> `AGREED` -> `ACTIVE` -> `TERMINATED`).
   - **Anti-Spawning Invariant**: Strictly zero autonomous agent creation or delegation.
5. **Decoupling**:
   - Model Router and Agent Fabric remain 100% decoupled and independent.

---

## 2. Test & Verification Evidence

| Test Suite | Scope | Result |
| :--- | :--- | :--- |
| `test_studio_v080_agent_capability_governance.py` | Profiles, deterministic matching, locality filtering, contracts, decoupling | **6/6 PASS** |
| `test_studio_v080_runtime_observability.py` | Session snapshots, event stream, recovery states, failure boundary | **6/6 PASS** |
| `test_studio_v080_runtime_intelligence_integration.py` | Runtime session binding, multi-model sessions, governance gates | **6/6 PASS** |
| Total v0.8.0 Architecture Suite | `python -m pytest -k "v080" tests/` (18 test suites) | **83/83 PASS** |
| Frontend Test Suite | `node --test tests/*.test.mjs` | **38/38 PASS** |
| Frontend TypeScript Build | `tsc -b && vite build` in `software/desktop-ui/` | **0 Errors, PASS** |

