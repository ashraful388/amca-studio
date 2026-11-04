# AMCA Studio v0.8.0 — Intelligent Decision Transparency UX Design

**Task:** ZCODE-AMCA-STUDIO-V0.8.0-INTELLIGENT-DECISION-TRANSPARENCY-UX-DESIGN-01
**Date:** 2026-09-03
**Owner:** ZCode (Frontend Product Lead)
**Status:** Design only — no decision engine, router, agent, backend, or telemetry changes. Nothing in this document is implemented in the current build.

---

## 1. Purpose and boundary

AMCA Studio's transparency layers (cognitive workflow, review center, reuse, readiness) show users what was recorded. When future AMCA versions introduce adaptive decisions — choosing strategies, models, agents, or knowledge — users must be able to inspect, understand, override, and audit those decisions before and after execution.

**This document designs that UX contract. It does not implement it.**

Boundary: "Decision transparency design only — no adaptive decision exists in this build."

Design rule for every surface below: each UI field renders its recorded backend value or a mandated missing-rendering string. **No generated explanations and no hidden reasoning are ever inserted.**

## 2. Future decision trace UX design

The decision trace is a panel (natural home: the run/workflow detail surfaces) showing, in display order:

| # | Section | Contract fields rendered | Design notes |
| --- | --- | --- | --- |
| 1 | Selected strategy | `decision.strategy`, `decision.strategy_reason` | The recorded strategy and its reason string, quoted verbatim — never generated. |
| 2 | Selected models | `decision.selected_model_refs`, `selection_trace.provider_health_references` | Recorded model refs with locality and provider-health references as recorded. |
| 3 | Selected agents | `decision.selected_agent_ids` | Recorded agent participants; agents without recorded participation are simply absent. |
| 4 | Selected knowledge sources | `decision.knowledge_artifact_ids`, `binding.audit_event_ref` | Recorded knowledge bindings, each traceable to an audit event. |
| 5 | Approval checkpoints | `decision.approval_checkpoint_ids`, `checkpoint.state` | The gates the decision will pass, each with its recorded state (pending/granted/denied). |

## 3. Explainability contract

Five UI fields, each with its contract source and mandated missing rendering:

| UI field | Contract source | Required | Missing rendering |
| --- | --- | --- | --- |
| Decision input records | `decision.input_record_ids` | yes | "No recorded decision input available." |
| Evidence used | `decision.evidence_refs` | yes | "No recorded evidence used." |
| Approval state | `decision.approval_state` | yes | "Approval state not recorded." |
| User override option | `decision.override_available` | yes | "Override availability not recorded." |
| Final execution record | `decision.execution_record_id` | no | "Not executed yet." |

Rendering rule: "Each field renders its recorded value or the mandated missing rendering — no generated explanations, no hidden reasoning."

## 4. Human control surface design

Four designed controls — **approve, modify, override, reject** — are specified here but deliberately **not implemented** in this build:

| Control | Design | Target |
| --- | --- | --- |
| approve | Confirm the recorded decision so execution may proceed through the existing approval pipeline. | the recorded decision's approval checkpoint |
| modify | Change a decision parameter (e.g. swap a model ref) before approving; the modification is recorded alongside the decision. | the recorded decision fields |
| override | Replace the recorded decision with the user's own choice; the override and its reason are recorded. | the recorded decision outcome |
| reject | Decline the recorded decision; the rejection and reason are recorded and execution does not proceed. | the recorded decision outcome |

Every control targets a **recorded decision** — never a live engine call. Approve/reject of existing approvals remains in the existing approval surfaces.

## 5. Decision audit model (UX only)

| UI field | Contract source | Missing rendering |
| --- | --- | --- |
| Initiated by | `decision.initiator` | "Initiator not recorded." |
| Evidence references | `decision.evidence_refs` | "No recorded evidence references." |
| Timestamp | `decision.created_at` | "No recorded timestamp." |
| Approval state | `decision.approval_state` | "Approval state not recorded." |

Audit display mirrors the recorded decision record one-to-one — no derived judgment.

## 6. Design validation

`tests/intelligentDecisionTransparencyUxDesignModel.test.mjs` validates the design: forbidden-language scan (no current adaptive claims, no autonomous-execution language), every contract field named explicitly, designed controls marked unimplemented, canonical missing renderings, and the boundary string. The full design text passes `passesDesignLanguageScan`.

## 7. Implementation prerequisites (future orders, not this one)

- Backend decision records with the contract fields above (`decision.*`).
- Explainable selection traces with evidence references.
- Approval boundaries and rollback paths.
- Binding of the trace panel into run/workflow detail surfaces once decision records exist.

Nothing in this design requires changes to the Model Router, Agent Fabric, orchestrator, knowledge runtime, backend services, execution permissions, or telemetry.
