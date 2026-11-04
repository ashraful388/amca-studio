# AMCA Studio v0.6.0 — Release Notes

Version 0.6.0 · Source baseline `56b678cd` · Branch `zcode/amca-v0.6.0-common-integration-01` → `zcode/amca-v0.6.0-rc-package-release-01`

v0.6.0 is the integrated intelligent collaborative core: every accepted
v0.6.0 feature track is assembled, integration-audited, and validated into
one common baseline.

## Major new capabilities

- **Five collaboration architectures through Main Chat** — Solo, Joint,
  Independent → Discuss, Parallel, and Adaptive Hybrid are selectable from
  the normal Main Chat path (Auto Intelligence default preserved).
- **Adaptive Hybrid contextual activation** — selecting Adaptive Hybrid
  shows truthful gate/readiness state and offers explicit, confirmed,
  session-scoped enablement through the existing secure intelligence
  configuration API. Nothing auto-enables; Nano stays untouched;
  deterministic supervision remains authoritative.
- **Provider/model BYOK workflow** — add, test, edit, and delete
  provider/model profiles with credentials stored in the OS vault
  (Windows Credential Manager + DPAPI fallback). Credentials are
  write-only and never returned to the UI.
- **Cross-provider Model Teams** — create and edit teams mixing local
  Ollama models with API models from different providers; stale members
  are marked and repairable (Replace/Remove), never silently substituted.
- **Cross-provider Router** — provider-neutral routing candidate
  resolution from Model Hub state (`intelligence/cross_provider_router.py`).
- **Nano Agent observability** — read-only Nano runtime surface
  (`NanoAgents.tsx`, `/api/nano-agents`); opening it never enables Nano.
- **Provider timeout/error normalization** — bounded request-timeout
  ceilings and a stable error taxonomy (authentication, rate-limit,
  invalid-request, provider-unavailable, timeout, cancelled,
  malformed-response) across adapters; DeepSeek/Mistral/xAI ride the
  shared OpenAI-compatible transport.
- **Cost & Budget Studio** — real backend cost state with truthful
  known/unknown/local-$0 distinctions and session-scoped budget
  configuration.
- **Pre-dispatch hard-budget enforcement** — a hard budget blocks
  billable dispatch before the provider is invoked; unknown price under a
  hard budget fails closed; no budget configured means behavior unchanged.
- **Local Ollama support** — discovery, hardware-fit evaluation, and
  local execution with exactly $0 external API cost.
- **Repository hygiene** — generated build artifacts (`target/`,
  `.build-logs/`) are no longer tracked.

## Known limitations (explicit)

- The Adaptive local planner readiness probe can be intermittent on small
  reasoning models such as `qwen3:1.7b`; enablement is refused (fail-closed)
  until a probe verifies readiness.
- Strict goal-contract scoring may mark an otherwise completed Solo
  response as `partial`.
- Nano Agents remain experimental and default OFF.
- Adaptive/Hybrid intelligence remains experimental and default OFF after
  restart (session-scoped enablement only).
- Project budgets are not persistently stored (session-only).
- BYOK capability badges are limited to backend-confirmed information.
- DeepSeek/Mistral/xAI use the shared OpenAI-compatible transport rather
  than dedicated adapter classes.
- `$0` API cost means no external API charge; no claim is made that local
  execution has zero electricity/hardware cost.
- Strict goal-contract scoring aside, Solo runs report `partial` status
  under conservative completion scoring even when the answer completed.

## Integrated lineage (verified)

- ZCode: `11ad4e34` → `e4450eb9` → `d3ff71aa` → `b5236fe0`
- Goose: `a3d6e607` → `9fff82f7` → `bcb63366` → `c5ed0826`
  (integrated as cherry-picks `e7079d8e` → `50b6beb1` → `c036080a` →
  `ec947d46` on the integration branch)
- AutoClaw: `128a4e24` → `3c114e89` → `bcb079a3` → `bd577c75`
- OpenCode Router: `cf43f78e` (on top of Nano `098a86de`)
- OpenCode Nano: `098a86de`
- Repository hygiene: `2de1f51`
- Integration map: `655e5b2b`
- Integration fixes: nano import-path/slots/scheduler corrections,
  anthropic SDK test-stub compatibility (see
  `docs/v060/AMCA-v0.6.0-Integration-Baseline-Record.md`)
