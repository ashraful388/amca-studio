# AMCA Studio: v0.9.0 Professional Workflow Onboarding and Guidance UX Report

**Milestone**: `ZCODE-AMCA-STUDIO-V0.9.0-PROFESSIONAL-WORKFLOW-ONBOARDING-AND-GUIDANCE-UX-01`  
**Product**: AMCA Studio (v0.9.0 Frontend Onboarding & Guidance Track)  
**Status**: **PROFESSIONAL WORKFLOW ONBOARDING AND GUIDANCE VERIFIED (PASS)**  
**Target Version**: `v0.9.0`  
**Date**: September 2, 2026  

---

## 1. Executive Summary

This milestone establishes a professional onboarding and contextual guidance layer for AMCA Studio, enabling new users to navigate and operate the platform without needing internal architectural knowledge:
1. **First-Launch Guidance Flow**:
   - Implemented `onboardingAndGuidanceModel.ts` and `OnboardingModal.tsx` in `software/desktop-ui/src/`.
   - Structures 5 onboarding steps:
     1. Welcome & Platform Overview (Local-First Platform)
     2. Local-First & Privacy Architecture (Offline Ollama / Cloud Gated Flags)
     3. Model & Agent Capability Governance (Declared Capabilities Only)
     4. Strict Human Approval Boundaries (Operator Review & Sign-Off)
     5. Guided Workflow Creation (Goal, Knowledge, and Timeline)
2. **Contextual Help Layer**:
   - Comprehensive guidance covering 6 core surfaces: `Model Hub`, `Knowledge Center`, `Cognitive Workflow`, `Review Center`, `Artifact Center`, `Automation Center`.
   - Every surface clearly specifies: Purpose, Guidance Points, and **What AMCA Does NOT Do Automatically**.
3. **Empty-State Guidance**:
   - Audited empty states across 5 surfaces: `projects`, `workflows`, `models`, `knowledge`, `artifacts`.
   - Each empty state explicitly details: What is missing, Next action prompt, and Boundary reminder.
4. **Anti-Hype Validation**:
   - Strict validation verifies zero usage of unsupported hype terms: *autonomous employee*, *unlimited swarm*, *replaces human judgment*, *self-healing autonomous*.
5. **Decoupling**:
   - Zero modifications to Model Router, Agent Fabric, Decision Engine, Adaptive Coordination, Execution Planning, Backend services, or Telemetry.

---

## 2. Test & Verification Evidence

| Test Suite | Scope | Result |
| :--- | :--- | :--- |
| `tests/onboardingAndGuidanceModel.test.mjs` | Onboarding flow, anti-hype claims, governance explanations, help coverage, empty states | **5/5 PASS** |
| `tests/decisionTransparencyModel.test.mjs` | Missing record handling, evidence rendering, anti-reasoning, anti-ranking, disabled controls | **6/6 PASS** |
| Total Frontend Unit Suite | `cmd /c npm run test:frontend` in `desktop-ui/` (6 test suites) | **49/49 PASS** |
| Frontend TypeScript Build | `cmd /c npm run build` in `desktop-ui/` | **0 Errors, PASS** |
| Total Backend Architecture Suite | `python -m pytest -k "v080 or v090" tests/` (24 test suites) | **119/119 PASS** |

