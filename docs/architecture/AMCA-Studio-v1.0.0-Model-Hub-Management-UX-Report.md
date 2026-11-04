# AMCA Studio v1.0.0 — Model Hub Management UX Report

**Task:** ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-MANAGEMENT-UX-AND-LOCAL-MODEL-OPERATIONS-01
**Date:** 2026-09-03
**Owner:** ZCode (Frontend Product Lead)
**Baseline:** `bf045a46` on `zcode/amca-studio-z1-shell-completion-01`
**Scope:** Transform the Model Hub ecosystem foundation into a professional model management experience. Management architecture, provider handling, local model workflow, limitations. No Model Router algorithms, Agent Fabric execution, Decision Engine, Adaptive Coordination, Execution Planning, or telemetry changes.

---

## 1. Management architecture

`modelHubManagementUxModel.ts` is a pure, dependency-free model exposing four contract surfaces over recorded fields:

1. **Management dashboard rows** (`managementRows`) — one row per recorded resource (model/provider/agent): ref, kind, locality (recorded ref prefix: "Local" for `ollama:*`, "Provider" otherwise, "Recorded agent" for agents), availability (recorded; "Not recorded" when absent — never a false zero), capability tags (recorded tags only), and connection status for providers (connected/disconnected/Not recorded).
2. **Provider connection status** (`providerConnectionStatus`) — connected state, provider type, credential **fingerprint presence** (boolean, never the credential), and last manual verification timestamp.
3. **Local model operations visibility** (`localModelOperations`) — recorded runtime kind (Ollama/llama.cpp/vLLM recognized from the recorded ref), location, runtime availability, supported formats. **Visibility only — no automatic download, no automatic activation, no automatic routing.**
4. **Capability evidence panel** (`capabilityEvidence`) — Capability → Evidence Source → Verified/Unknown: "Verified" only when a recorded evidence source exists; "No recorded evidence source" otherwise. No generated explanations.

## 2. Provider handling — credential privacy

`CREDENTIAL_PRIVACY_NOTE`: "Credentials never leave the operating-system vault. This surface never displays API keys, secrets, or tokens — only a fingerprint presence indicator." The connection row carries a boolean fingerprint-presence flag and a recorded verification timestamp; no key material, no secret values, no tokens can appear in any rendering (test-asserted).

## 3. Local model workflow

Recorded local runtimes (Ollama, llama.cpp, vLLM) are recognized from recorded refs; each shows location, runtime availability, and supported formats as recorded. Unrecorded fields render "Not recorded"; unknown runtimes render kind "unknown" rather than being guessed. Support is visibility-only — no download, activation, or routing behavior exists in this surface.

## 4. Limitations

- The management dashboard renders recorded state; it does not discover new providers, does not modify routing, and does not rank or recommend models ("no ranking, no 'best model' suggestions, no routing modification").
- Local runtime support is recognition for visibility — llama.cpp and vLLM endpoints are displayed as recorded; no download/activation behavior exists.
- Backend decision engine for adaptive selection does not exist; no claim of intelligent management is made.

## 5. Validation

- `tests/modelHubManagementUxModel.test.mjs` (4 tests, registered in `test:frontend` — 48 files): registry rendering without ranking, credential privacy (no key/secret/token material in any row; fingerprint-presence null vs recorded), local runtime recognition with honest gaps (unknown runtime, no formats, "Not recorded" availability), capability honesty (Verified only with a recorded source).
- Backend contract test `software/tests/test_studio_v100_model_hub_management.py` (4 checks): credential privacy rule, capability honesty, no-routing rule, and recorded local runtime support are present in the model source.
- **Frontend suite: all registered files green** (48 files incl. this one). `tsc -b` clean. Backend baseline 1808 stands (no backend files changed).
