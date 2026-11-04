# AMCA Research Visibility API Contract

Backend-to-UI contract for AMCA Studio Alpha (AG-R2.6A UI integration).
Source of truth: `software/src/amca/research_visibility.py` + routes in
`software/src/amca/service.py` (ZC-AMCA-STUDIO-BACKEND-SUPPORT-01 /
ZC-AMCA-STUDIO-BACKEND-CONTRACT-READINESS-02).

All endpoints are **read-only**. They expose data already stored by the
R2.x research stack; nothing here mutates state, triggers workflows, or
touches Tool Fabric permissions.

## Conventions (all endpoints)

| Aspect | Contract |
| --- | --- |
| Base path | `/api/research/runs` |
| Method | `GET` only |
| Authentication | REQUIRED on every call — header `X-AMCA-Session: <token>` (token from `GET /api/session`). Missing/invalid → `401`. |
| Origin | Send `Origin: http://localhost:1420` (CORS allow-list). |
| Project scoping | Run-level reads REQUIRE `?project_id=<id>`. Missing → `400 {"error": "project_id required"}`. Mismatched project → `404` (identical to "not found"; no existence leak across projects). |
| Response format | JSON. Success = `200`; errors = `{"error": "<message>"}` with 400/401/404. |
| Payload caps | runs list ≤ 200 entries; trail ≤ 500 steps; provenance ≤ 100 per kind. |
| Naming | snake_case everywhere; no camelCase; no raw argument payloads or vectors in any response. |

---

## Endpoints

### 1. List research runs (dashboard)

`GET /api/research/runs?project_id=<optional>`

- Auth: required.
- Params: `project_id` (optional filter; omit for cross-project overview).
- Response 200:

```json
{
  "runs": [
    {
      "run_id": "run-...",
      "project_id": "projA",
      "session_id": "rs-...",
      "objective": "...",
      "status": "completed|partial|failed|cancelled|interrupted|waiting_approval|running|planned",
      "stop_reason": "objective_satisfied|insufficient_evidence|...|null",
      "mode": "deterministic|adaptive|hybrid",
      "planner_mode": "adaptive",
      "planner_source": "model|fallback|deterministic-baseline|null",
      "started_at": "ISO-8601",
      "completed_at": "ISO-8601|null"
    }
  ],
  "pagination": {"returned": 2, "total": 2, "limit": 200}
}
```

- Errors: 401 unauthenticated.
- Caps: newest 200 runs by start time.

### 2. Run details

`GET /api/research/runs/{run_id}?project_id=<required>`

- Auth + scoping: required (`project_id` must match the run).
- Response 200: run row as above plus parsed `"limits"` object
  (`max_steps`, `max_searches`, `max_fetches`, `max_sources`,
  `max_evidence_chunks`, `max_synthesis_attempts`,
  `max_elapsed_seconds`) and `fallback_reason`.
- Raw `limits_json` is never exposed.
- Errors: 400 missing `project_id`; 404 unknown/cross-project.

### 3. Execution trail summary

`GET /api/research/runs/{run_id}/trail?project_id=<required>`

- Response 200: `{"steps": [{step_id, ordinal, action_type, reason_code,
  decision, execution_ref, outcome, created_by, started_at,
  completed_at}]}`.
- Deliberately EXCLUDED: argument payloads (no queries, URLs, snippets) —
  the trail proves what happened without leaking content.
- Errors: 400/404 as above. Cap: 500 steps (newest-bounded by ordinal).

### 4. Planning trail summary

`GET /api/research/runs/{run_id}/planning?project_id=<required>`

- Response 200:

```json
{"turns": [{"turn_index": 0, "planning_source": "model|fallback|deterministic-baseline",
  "mode": "adaptive", "model_provider_id": "...", "model_model_id": "...",
  "candidate_count": 2,
  "candidates": [{"candidate_index": 0, "validator_result": "accepted|rejected",
                  "rejection_reason": "limit_exhausted:searches|null"}],
  "created_at": "..."}]}
```

- Answers "why did AMCA take this step / reject that action / stop?".
- Errors: 400/404. No raw model output beyond validated candidate metadata.

### 5. Model participation summary

`GET /api/research/runs/{run_id}/participation?project_id=<required>`

- Response 200:

```json
{"planners": {"<created_by>": {"steps": n, "awaiting_approval": n, "executed": n}},
 "action_outcomes": {"search_web:executed": 2, ...}}
```

- Use for Collaborative Room status ("who did what") without exposing
  prompts or content.

### 6. Collaboration mode history

`GET /api/research/runs/{run_id}/mode-history?project_id=<required>`

- Response 200: `{run_id, mode, planner_mode, planner_source,
  fallback_reason, status, stop_reason}`.
- Drives the mode/fallback display (e.g. "Mode: Hybrid, current planner:
  deterministic baseline, reason: model unavailable").

### 7. Provenance references (incl. Research Workspace metadata)

`GET /api/research/runs/{run_id}/provenance?project_id=<required>`

- Response 200: `{"sources": [...], "snapshots": [...], "evidence": [...], "citations": [...], "claims": [...], "synthesis": [...]}`.
- `sources`: `{source_id, source_kind, canonical_origin, created_at}`.
- `snapshots`: `{snapshot_id, source_id, content_digest, created_at}`.
- `evidence`: `{evidence_id, snapshot_id, extraction_method, created_at}`.
- `citations`: `{citation_id, display_label, evidence_id, created_at}`.
- `claims` (Research Workspace): `{claim_id, claim_text (≤200 chars), claim_type, status, question_id, created_at}`.
- `synthesis` (latest version per plan): `{plan_id, output_form, plan_created_at, version_id|null, seq|null, grounding_status|null, version_created_at|null}` — grounding values per R1.4 (draft / grounding_incomplete / grounded / contested / invalid_citation).
- References ONLY (ids/kinds/digests/labels/bounded text); no excerpt bytes, no full draft bodies.
- Caps: 100 entries per kind.

### 8. Activity summary (Studio Logs panel)

`GET /api/studio/activity?project_id=<required>`

- Auth: required. Missing `project_id` → `400`.
- Response 200:

```json
{"events": [{"kind": "tool_execution|approval_decision|research_step",
             "ref": "<id>", "detail": "<tool/action type>",
             "action": "<operation>", "status": "<status/outcome>",
             "occurred_at": "ISO-8601"}],
 "limit": 50,
 "sources": {"tool_executions": true, "approval_decisions": true,
              "research_steps": true}}
```

- Merges three authoritative sources per project: Tool Fabric executions,
  approval decisions (incl. undecided = pending), research run steps.
- `action` is present on tool-execution events only (the executed operation).
- Timestamp-descending, deterministic tie-break not required (display feed).
- Summaries only: no prompts, reasoning traces, credentials or argument
  payloads. Cap: 50 events per call.
- `sources` reports availability truthfully: a source whose tables do not
  exist yet is skipped (`false`) instead of failing the whole feed.
- Empty project → `{"events": [], "limit": 50, "sources": {...}}`.

### 9. Research Execution Gateway (lifecycle)

ZC-AMCA-RESEARCH-EXECUTION-GATEWAY-01. Isolated facade
(`amca/research_gateway.py`) between the service layer and the frozen
R1/R2 research stack. All endpoints require session auth.

`POST /api/research/runs`

- Body: `{"project_id": str, "objective": str (<=2000), "mode":
  "deterministic|adaptive|hybrid"}`.
- Response 200: `{"run_id", "status": "created", "created_at"}`.
- Validation failures → `400`.

`GET /api/research/runs/{run_id}/status?project_id=<required>`

- Response 200: `{"run_id", "status", "objective", "created_at",
  "updated_at", "stop_reason"}` — read-only projection of persisted
  truth, never cached.
- Missing `project_id` → `400`; wrong or unknown run → identical `404`
  (no existence leak).

`POST /api/research/runs/{run_id}/cancel` — body `{"project_id"}`
→ `{"run_id", "status": "cancelled", "updated_at"}`.
Idempotent: cancelling a cancelled run returns 200 again. Cancelling a
completed/failed/partial-progress run → `409`.

`POST /api/research/runs/{run_id}/resume` — body `{"project_id"}`
→ `{"run_id", "status": "planning", "updated_at"}`.
Guarded: only INTERRUPTED runs re-enter planning; repeat resume on an
already-planning run is a safe no-op (200); all other states → `409`.
Never duplicates execution (the gateway starts no execution itself).

`POST /api/research/runs/{run_id}/execute` — body `{"project_id"}`
→ `{"run_id", "status": "running"}`.
Starts ONE bounded orchestrated pass (plan → step → stop) for a run in
planning state. Duplicate-execution guard is synchronous: only
planning-state runs may start (everything else → `409`), and the
planning→running flip happens before any worker exists, so two
concurrent executes cannot both start. The orchestrator owns all
further transitions; observe via the status endpoint and trail.
Network actions gate through Tool Fabric approval before any backend
runs; runs in adaptive/hybrid mode are rejected (`409`) until a
model-backed planner is configured on the deployment. An unexpected
orchestration failure lands the run in failed/error persisted truth —
never a stuck running row.

Crash reconciliation (ZC-AMCA-RUNTIME-RECOVERY-RECONCILIATION-01):
execution is in-process only, so any run still `running` at service
startup is an orphan of a crashed previous process. Startup marks such
rows `interrupted` with stop reason `interrupted_external_action`;
they recover through the normal guarded resume path
(INTERRUPTED → PLANNING → execute). Runs in `waiting_approval` are
legitimate cross-restart state and are never touched.

Workflow result views (ZC-AMCA-STUDIO-ALPHA-WORKFLOW-01):

`GET /api/research/runs/{run_id}/evidence/{snapshot_id}?project_id=`
→ `{"run_id", "snapshot_id", "source_id", "content_digest",
"content_type", "created_at", "truncated", "content"}` — extracted
source text for a snapshot belonging to the run's own research session
(bounded at 20 000 chars with truthful truncation flag). Ownership is
threefold (session auth + project + run session); any miss returns an
indistinguishable `404`.

`GET /api/research/runs/{run_id}/synthesis?project_id=`
→ `{"run_id", "plans": [{"plan_id", "output_form", "created_at",
"version": {"version_id", "seq", "grounding_status",
"content_digest", "created_at", "truncated", "sections"}}],
"count"}` — latest immutable synthesis version per plan of the run's
session; `sections` are whole well-formed section objects bounded at
20 000 chars total — when sections must be dropped the `truncated`
flag is set and no partial JSON is ever emitted; empty session →
`{"plans": [], "count": 0}`. Export/use flows reuse the existing
project artifact APIs on top of this content.

`GET /api/research/runs/{run_id}/synthesis/history?project_id=`
→ `{"run_id", "plans": [{"plan_id", "output_form", "created_at",
"versions": [{"version_id", "seq", "grounding_status",
"output_form", "content_digest", "created_at"}],
"versions_truncated"}], "count"}` — metadata-only timeline of EVERY
synthesis version per plan of the run's session. Ordering is
deterministic and documented: plans by creation time, versions by
`seq` ASCENDING (chronological evolution order). Bounded: at most 50
versions per plan are returned; a plan whose older tail was dropped
sets `versions_truncated` truthfully. No section text is exposed on
this endpoint — use the synthesis view for the latest rendered
result. Ownership identical to other run-level reads (missing
project → `400`; wrong/unknown run → indistinguishable `404`).

Lifecycle projection (stored RunStatus is never renamed):

| stored | gateway status |
|---|---|
| planned | planning |
| waiting_approval | waiting_approval |
| partial | partial_progress |
| running | running |
| completed | completed |
| failed | failed |
| cancelled | cancelled |
| interrupted | interrupted |

`updated_at` = timestamp of the run's latest lifecycle transition
(creation, cancellation via `completed_at`, resume via refreshed phase
start). Payloads never include prompts, planner state, model traces,
credentials, raw tool arguments, or state blobs.

---

## Error cases (all endpoints)

| Status | Meaning |
| --- | --- |
| 400 | `project_id` missing on a run-level read; over-bound parameters |
| 401 | Missing/invalid `X-AMCA-Session` token |
| 404 | Unknown run/sub-resource OR run belongs to another project (indistinguishable by design) |

## Performance notes (Studio Alpha planning)

- All queries are parameterized SELECTs over canonical `amca.db` via
  read-only connections; no DDL, no migrations performed by this layer.
- Known future optimization: `research_planning_turns/events(run_id)`
  index if turn volume grows — DEFERRED to a schema-touching slice;
  do not add an isolated migration.
- Every endpoint is a cheap bounded SELECT (caps above): safe for
  dashboard polling at human cadence.
- Empty states return empty lists (never errors) when the R2 tables do
  not exist yet.
