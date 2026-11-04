# AMCA Studio v0.8.0 — Decision Evidence Registry Design

**Task:** ZCODE-AMCA-STUDIO-V0.8.0-DECISION-EVIDENCE-REGISTRY-DESIGN-01
**Date:** 2026-09-03
**Owner:** ZCode (Frontend Product Lead)
**Status:** Design only — no adaptive decisions, model routing, agent execution, automation, or backend changes. Nothing in this document is implemented.

---

## 1. Purpose and boundary

This is the evidence registry contract that future adaptive decisions will depend on. When adaptive cognition is later introduced behind the governance boundaries designed in AMCA-Studio-v0.8.0-Intelligent-Decision-Transparency-UX-Design.md, every decision will need a recorded evidence trail. This document defines that record, its provenance relationships, the human override traces, and the lifecycle states.

**Registry rule: "Store evidence references, not hidden reasoning."**

The registry stores references to existing records — input records, knowledge artifacts, model/agent refs, approval records, execution records. It never stores reasoning payloads, chain-of-thought, or derived explanations.

## 2. Decision evidence record design

| Field | Type | Required | Note |
| --- | --- | --- | --- |
| decision_id | string (uuid) | yes | Primary identifier of the future decision record. |
| workflow_id | string | yes | The workflow the decision belongs to. |
| timestamp | ISO-8601 string | yes | Recorded creation time. |
| initiator | string (recorded role/ref) | yes | Who or what initiated the decision (user or recorded system role). |
| input_references | string[] | yes | References to the recorded input records the decision was based on. |
| evidence_references | string[] | yes | References to existing evidence records — never reasoning payloads. |
| selected_resources | { models, agents, knowledge } | yes | Selected resources as recorded references. |
| approval_references | string[] | yes | References to the approval records gating this decision. |

## 3. Evidence provenance model

Five reference-only relationships from the decision record:

| Relation | Stored | Design note |
| --- | --- | --- |
| input_records | IDs only | Decision → input record IDs the decision consumed. |
| knowledge_references | IDs only | Decision → knowledge artifact IDs explicitly attached or bound. |
| model_agent_references | IDs only | Decision → recorded model/agent IDs the decision selected. |
| approval_records | IDs only | Decision → approval record IDs gating the decision. |
| execution_record | ID only | Decision → the final execution record ID (when executed). |

Rule: "Provenance stores references only — no inference, no generated explanation." Relationships exist only where a recorded reference exists.

## 4. Human override trace design

First-class trace records for human decisions:

| Field | Note |
| --- | --- |
| override_id | Identifier of the trace record itself. |
| decision_id | The decision being overridden or decided on. |
| trace_type | One of: "user_modification" \| "override_with_reason" \| "rejected_decision" \| "approved_decision". |
| user_ref | Recorded user role/ref who acted. |
| reason | Recorded reason text — required for modifications, overrides, and rejections. |
| changed_fields | Recorded fields the user modified (empty for approve/reject). |
| timestamp | Recorded action time. |

## 5. Decision lifecycle state model

CREATED → EVIDENCE_COLLECTED → AWAITING_APPROVAL → APPROVED / MODIFIED / REJECTED → EXECUTED → VERIFIED

- APPROVED and MODIFIED proceed to EXECUTED.
- REJECTED is terminal — the decision does not execute.
- VERIFIED is terminal.
- All transitions are recorded events; no state is inferred.

## 6. Design validation

`tests/decisionEvidenceRegistryDesignModel.test.mjs` validates: the eight mandated record fields, five reference-only provenance relations (each storing IDs), human override trace completeness (four trace types, reason required for modification/override/rejection), lifecycle transition legality (REJECTED and VERIFIED terminal, no backward transitions), and a schema scan with negation-awareness — the mandated boundary statements ("never reasoning payloads", "not hidden reasoning") pass as negations while actual claims ("stores hidden reasoning", "decides automatically", "chain-of-thought recorded") fail.

## 7. Implementation prerequisites (future orders, not this one)

- Backend persistence for the record, provenance, override, and lifecycle schemas.
- Binding the previously designed decision-trace UX panel (Intelligent-Decision-Transparency-UX-Design.md) to these records.

Nothing in this design requires changes to the Model Router, Agent Fabric, Cognitive Orchestrator, Knowledge Runtime, workflow execution, backend APIs, or telemetry.
