# AMCA Phase 0 Specification

Status: normative for the first implementation  
Specification version: 0.1.0  
Source: the two AMCA planning documents, normalized into testable contracts

## 1. Scope

This document defines the behavior that every AMCA implementation must share:

- the five collaboration modes;
- asynchronous, soft, quorum, hard, and bounded-staleness synchronization;
- canonical project state and versioning;
- structured claims, conclusions, evidence, and disagreements;
- goal contracts and termination;
- the semantic event protocol;
- evidence-based decision acceptance;
- API and local-compute cost accounting;
- privacy, permissions, and user control.

Phase 0 defines contracts and benchmark fixtures. It does not provide a desktop
UI, multimodal ingestion, unrestricted automation, a learned router, or a claim
that multi-model collaboration improves quality. Those are later implementation
and evaluation questions.

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT, and MAY are normative.

## 2. Safety and epistemic boundary

AMCA coordinates externally reportable work products. It does not depend on,
request, expose, or persist a provider's hidden chain-of-thought.

Model contributions MUST be represented as one or more of:

- a concise conclusion or proposal;
- a concise rationale summary;
- explicit assumptions;
- evidence or artifact references;
- confidence and uncertainty;
- an objection or open question;
- a requested tool call or experiment;
- a structured result produced by a tool.

The canonical state MUST NOT contain hidden reasoning tokens or imply access to
private model internals. A provider's ordinary response text MAY be retained in
an access-controlled call log when policy allows it, but canonicalization MUST
extract only the structured, externally reportable fields above. Token streams
MUST NOT be broadcast to other models merely because they are available.

AMCA's "shared reasoning" means shared structured conclusions, evidence,
assumptions, objections, artifact revisions, and focus. It does not mean sharing
hidden chain-of-thought.

## 3. Stable terminology

### 3.1 Core entities

- Project: a local workspace, policy, ledger, and one or more tasks.
- Task: an execution governed by one Goal Contract.
- Participant: a model or user that may emit semantic events.
- Required participant: a participant whose disposition is needed at a hard
  synchronization gate unless explicitly released.
- Active focus: the single canonical question currently being advanced.
- Work unit: a bounded subproblem with declared inputs, output contract, and
  dependencies.
- Contribution: a structured proposal, objection, evidence item, answer, or
  other semantic event.
- Claim: a falsifiable or verifiable statement in canonical state.
- Conclusion: a claim accepted through the synchronization and evidence rules.
- Evidence: a provenance-bearing observation, calculation, tool result, test
  result, or source locator that supports or contradicts claims.
- Critical objection: an unresolved objection that could invalidate a required
  goal criterion, final claim, safety decision, or irreversible action.
- Canonical state: the versioned, reducer-produced state used for decisions.
- Provisional state: useful work that has not passed its required decision gate.
- Artifact: a versioned user-visible file, equation set, design, program, report,
  or other deliverable.

### 3.2 Enumerations

CollaborationMode:

- SOLO
- JOINT_DELIBERATION
- PARALLEL_DECOMPOSITION
- INDEPENDENT_DISCUSS
- ADAPTIVE_HYBRID

SynchronizationPolicy:

- ASYNC
- SOFT
- QUORUM
- HARD
- BOUNDED_STALENESS

DecisionClass:

- PROVISIONAL
- REVERSIBLE
- SIGNIFICANT
- CRITICAL
- IRREVERSIBLE

TaskStatus:

- CREATED
- RUNNING
- WAITING_FOR_APPROVAL
- BLOCKED
- SUCCEEDED
- FAILED
- STOPPED
- BUDGET_EXHAUSTED

ClaimStatus:

- PROPOSED
- ACCEPTED
- REJECTED
- SUPERSEDED

## 4. Global invariants

1. A task has exactly one Goal Contract and one monotonically increasing
   canonical state version.
2. SOLO, JOINT_DELIBERATION, and the independent phase of
   INDEPENDENT_DISCUSS have exactly one active focus.
3. PARALLEL_DECOMPOSITION may have one focus per work unit, but the parent task
   retains one integration focus.
4. Every canonical mutation is caused by a persisted, validated event.
5. Event identifiers are globally unique within a project. Replaying an event
   with the same identifier is idempotent.
6. Accepted claims cite evidence or an explicit verification method unless the
   claim is marked as a user-supplied premise.
7. A critical objection blocks acceptance regardless of how many models support
   the competing claim.
8. Collaboration routing and synchronization gates coordinate work; neither is
   a truth oracle.
9. User constraints and mandatory safety rules cannot be weakened by a model or
   router proposal.
10. Hard budgets are checked before a call is launched and reconciled after it
    completes.
11. Model-supplied criterion evidence becomes an evaluator attestation only
    when that contribution declares the active focus resolved. An unresolved
    contribution remains inspectable semantic evidence but cannot trigger goal
    evaluation, even when its checkpoint attendance requirement passes.
12. External data transmission and consequential tool actions are auditable.
13. A terminal success status is legal only when every required goal criterion
    passes and no terminal blocker remains.

## 5. Collaboration-mode semantics

All mode transitions MUST emit MODE_CHANGE_PROPOSED and, when accepted,
MODE_CHANGED. A change record includes the previous mode, new mode, trigger,
authorizing policy, state version, participating models, and expected cost.

### 5.1 SOLO

Purpose: low-complexity, routine, inexpensive, or latency-sensitive work.

Entry conditions:

- one active participant is selected;
- the focus and output contract are explicit;
- no policy requires multi-model review.

Processing:

1. The participant receives compact canonical context and recent relevant delta.
2. It emits structured contributions or tool requests.
3. The result is verified against the Goal Contract.

Exit:

- complete when the work-unit criteria pass;
- escalate to another mode on material uncertainty, a critical objection,
  stagnation, verification failure, or an explicit user request.

Invariant: exactly one model is authorized to advance the work. Review by tools
or deterministic checks does not turn SOLO into a multi-model mode.

### 5.2 JOINT_DELIBERATION

Purpose: strongly coupled conceptual work, contradictions, mathematics,
scientific claims, architecture, and final review.

Entry conditions:

- two or more participating models;
- one common active focus;
- an explicit synchronization policy;
- a declared decision class.

Processing:

1. Participants receive the same canonical focus and relevant context.
2. Contributions are launched concurrently where possible.
3. Meaningful output becomes semantic events, not token-level broadcasts.
4. Participants may propose, object, ask, correct, cite evidence, or request a
   discriminating tool/experiment.
5. The reducer creates provisional state.
6. Stale contributions are merged, rebased, or rejected under section 7.
7. A synchronization checkpoint decides whether the focus is resolved.

Exit:

- resolved when the applicable gate passes;
- reopened when new evidence or a critical contradiction invalidates the
  decision record;
- switched when the router or user accepts a mode-change proposal.

Invariant: participants work on the same active focus. Unrelated ideas go to the
pending queue unless they justify a focus change.

### 5.3 PARALLEL_DECOMPOSITION

Purpose: weakly coupled work that can be divided into explicit work units.

Entry conditions:

- a decomposition graph exists;
- every work unit declares identifiers, inputs, output criteria, dependencies,
  assignees, and an integration owner;
- shared mutable artifacts have an ownership or merge policy.

Processing:

1. Ready work units are dispatched concurrently.
2. Each unit advances against a pinned parent-state version.
3. Results are versioned and emitted as WORK_UNIT_COMPLETED or
   WORK_UNIT_BLOCKED.
4. An integration checkpoint checks dependency satisfaction, interface
   compatibility, contradictory assumptions, and artifact conflicts.

Exit:

- complete when every required work unit and integration criterion passes;
- return to JOINT_DELIBERATION when results conflict or coupling becomes strong;
- re-plan when a dependency or output contract changes.

Invariant: completion of all child units is not sufficient; integration must
also pass.

### 5.4 INDEPENDENT_DISCUSS

Purpose: reduce anchoring and premature convergence for high-novelty or stuck
problems.

This mode has two mandatory phases.

Independent phase:

- all participants receive the same focus and stable context;
- provisional submissions are sealed from peers;
- each submission records its base state version and a content digest;
- the phase closes when every required participant submits, is released, or the
  declared deadline policy fires.

Discuss phase:

- sealed submissions are revealed together;
- alternatives are normalized into comparable claims, assumptions, and
  evidence;
- participants critique and combine them;
- unresolved disputes use the same evidence-resolution rules as Joint
  Deliberation.

Exit:

- complete at the applicable synchronization gate;
- normally transition to JOINT_DELIBERATION for synthesis;
- restart an independent phase only with a new focus or a logged anti-anchoring
  reason.

Invariant: no participant may see another participant's provisional submission
before the reveal event.

### 5.5 ADAPTIVE_HYBRID

Purpose: use different base modes during one task.

ADAPTIVE_HYBRID is a supervised meta-mode, not a fifth execution algorithm.
Exactly one of the four base modes is active at any instant. The transition
record MUST contain:

- observed trigger;
- alternative modes considered;
- selected mode and sync policy;
- cost/latency estimate;
- policy rule or user instruction;
- state version at transition.

Initial deterministic triggers SHOULD include:

- low complexity to SOLO;
- weakly coupled work to SOLO or PARALLEL_DECOMPOSITION;
- strongly coupled high-complexity work to JOINT_DELIBERATION;
- high novelty or stagnation to INDEPENDENT_DISCUSS;
- conflicting parallel results to JOINT_DELIBERATION;
- routine execution after agreement to PARALLEL_DECOMPOSITION;
- any mode to SOLO when only routine residual work remains.

Automatic learned switching is outside Phase 0. User locks override router
preferences within mandatory safety limits.

## 6. Synchronization semantics

Synchronization is selected per checkpoint, not permanently per project.
Policy may strengthen a gate but MUST NOT weaken these minimums:

| Decision class | Minimum gate |
| --- | --- |
| PROVISIONAL | ASYNC |
| REVERSIBLE | SOFT |
| SIGNIFICANT | QUORUM |
| CRITICAL | HARD |
| IRREVERSIBLE | HARD plus explicit permission when required |

### 6.1 ASYNC

- no barrier and no checkpoint solicitation;
- valid events are integrated provisionally on arrival;
- late events follow staleness rules;
- ASYNC alone MUST NOT finalize a significant, critical, or irreversible
  decision.

### 6.2 SOFT

- the coordinator announces a checkpoint and the candidate state;
- participants are invited to respond but no participant is mandatory;
- the checkpoint may close at its deadline or when no response remains pending;
- unresolved noncritical issues are carried forward explicitly;
- only provisional or reversible decisions may finalize.

SOFT differs from ASYNC because it creates an explicit review opportunity and
decision record without imposing a mandatory attendance threshold.

### 6.3 QUORUM

Default threshold:

    max(2, ceiling(2 * eligible_participants / 3))

For a one-participant task the threshold is one. A project policy MAY set a
stronger threshold.

A quorum passes only when:

- the threshold number of eligible participants has a recorded disposition;
- required evidence/verification checks pass;
- no unresolved critical objection exists;
- the decision remains within policy and budget.

Abstention counts as a disposition but not as support. Failure, timeout, and
release are recorded separately. Participant count is a coordination condition,
not evidence of truth.

### 6.4 HARD

A hard gate passes only when:

- every required participant has contributed, explicitly abstained, or been
  released by an authorized user/policy decision;
- every critical objection is resolved, withdrawn with rationale, or converted
  into an explicit blocker;
- required deterministic checks, tests, calculations, or evidence reviews pass;
- final artifact digests match the reviewed versions;
- required user approval exists for consequential or irreversible actions.

A timeout does not silently pass a hard gate. It creates a blocked checkpoint or
an explicit participant-release decision.

### 6.5 BOUNDED_STALENESS

BOUNDED_STALENESS is a guard applied with one of the four gates above. Its
configuration contains:

- base_policy;
- max_version_lag, default 2 in Joint Deliberation;
- optional max_age_seconds;
- protected_decision_classes;
- required_participants.

When a required contribution exceeds the allowed lag, fast participants MAY
continue calculations, tests, and alternative exploration, but canonical
advancement for protected decisions pauses. The participant is then rebased,
released by policy, or marked failed. A critical contradiction always reopens
the focus even if the contribution is older than the configured bound.

## 7. Versioning, staleness, and rebase

Every model call records:

- call_id;
- participant_id;
- focus_id;
- start_state_version;
- start_time;
- expected latency;
- context digest.

On arrival:

1. If base version equals current version, validate and reduce normally.
2. If stale but its referenced claims/artifacts are unchanged, merge it and
   annotate the observed lag.
3. If partially stale, emit REBASE_REQUIRED with a compact delta and request a
   concise reconsideration.
4. If incompatible, preserve it in history but do not mutate canonical state.
5. If it contains a plausible critical contradiction, emit CRITICAL_CONFLICT,
   reopen the focus, and strengthen synchronization if policy requires.

Rebase MUST use canonical deltas, not the full raw conversation. Artifact edits
use optimistic concurrency: the expected artifact version must match, otherwise
an explicit merge is required.

## 8. Canonical state contract

The persisted ProjectState has these required top-level fields:

| Field | Type | Meaning |
| --- | --- | --- |
| project_id | UUID/string | Stable project identity |
| task_id | UUID/string | Active task identity |
| schema_version | string | State schema version |
| version | nonnegative integer | Monotonic canonical revision |
| status | TaskStatus | Task lifecycle |
| goal_contract | object | Contract from section 10 |
| active_focus | object or null | Focus id, question, decision class, owner |
| collaboration | object | Mode, base mode, participants, locks |
| synchronization | object | Policy, thresholds, staleness bounds |
| claims | map | Claim records keyed by id |
| evidence | map | Evidence records keyed by id |
| assumptions | map | Explicit assumptions keyed by id |
| disagreements | map | Disagreement records keyed by id |
| work_units | map | Decomposition graph and child status |
| pending_ideas | array | Deferred focus candidates |
| rejected_approaches | array | Rejected items with rationale summary |
| artifacts | map | Version, digest, path, provenance |
| recent_event_ids | array | Bounded event index, not raw transcript |
| user_constraints | object | Policy and explicit overrides |
| progress | object | Criterion results and completion fraction |
| cost_state | object | Reservations, actuals, and limits |
| terminal_reason | object or null | Machine-readable stop reason |

A state snapshot MUST include the last reduced event sequence number and a
content digest. State can be reconstructed by replaying later events.

### 8.1 Claim record

Required fields:

- claim_id;
- statement;
- status;
- confidence from 0 through 1 or null;
- source_actor_ids;
- evidence_ids;
- assumption_ids;
- objection_ids;
- verification_method;
- created_version;
- updated_version;
- supersedes_claim_id or null.

Confidence is metadata, not proof. Accepted status requires a decision record.

### 8.2 Evidence record

Required fields:

- evidence_id;
- kind: SOURCE, CALCULATION, TOOL_RESULT, TEST_RESULT, OBSERVATION, or
  USER_PREMISE;
- summary;
- provenance containing source, locator, timestamp, and content digest where
  available;
- method;
- supports_claim_ids;
- contradicts_claim_ids;
- strength: UNKNOWN, WEAK, MODERATE, or STRONG;
- verification_status: UNVERIFIED, VERIFIED, FAILED, or NOT_APPLICABLE;
- sensitivity classification.

Evidence MUST be attributable. A bare model assertion is a claim, not evidence.

### 8.3 Disagreement record

Required fields:

- disagreement_id;
- focus_id;
- competing_claim_ids;
- exact_disputed_question;
- criticality;
- discriminating_action;
- evidence_ids;
- status: OPEN, TESTING, RESOLVED, or BLOCKING;
- resolution and decision_record_id when resolved.

The discriminating action SHOULD name a calculation, test, experiment,
retrieval, or user decision that could distinguish the alternatives.

### 8.4 Decision record

Every accepted conclusion records:

- decision_id;
- claim_id;
- decision_class;
- synchronization checkpoint id;
- dispositions by participant;
- evidence and verification results;
- disposition of every critical objection;
- state and artifact digests;
- concise acceptance rationale;
- authorizing policy or user approval.

## 9. Evidence-based resolution; no majority-vote truth

AMCA MUST NOT accept a scientific, mathematical, factual, or safety claim solely
because a majority of models support it.

When claims conflict, the coordinator MUST:

1. name the exact disputed proposition;
2. list assumptions that differ;
3. identify evidence or a calculation that could discriminate;
4. execute or request that check when permitted;
5. update evidence and claim status;
6. record the resolution or preserve the blocker.

Quorum controls when a checkpoint is sufficiently attended. It never supplies
epistemic weight by itself. One well-supported objection can defeat many
unsupported proposals, and one unresolved critical objection blocks acceptance.

## 10. Goal Contract

A Goal Contract is immutable after task start except through
GOAL_CONTRACT_AMENDMENT, which records the user or policy authority and resets
affected criterion results.

Required fields:

- goal_id and version;
- objective;
- deliverables;
- success_criteria;
- constraints;
- allowed_actions;
- required_approvals;
- budget;
- termination;
- acceptance.

Each deliverable contains an id, type, expected path or response channel, and
format requirements.

Each success criterion contains:

- criterion_id;
- description;
- required boolean;
- evaluator kind;
- expected value or threshold;
- evidence requirement;
- status: NOT_RUN, PASS, FAIL, BLOCKED, or WAIVED;
- waiver authority when applicable.

Supported initial evaluator kinds SHOULD include:

- EXACT_VALUE;
- NUMERIC_TOLERANCE;
- JSON_SCHEMA;
- FILE_EXISTS;
- CONTENT_PREDICATE;
- COMMAND_EXIT;
- TEST_SUITE;
- HUMAN_REVIEW.

Budget includes:

- API currency hard and warning limits;
- model-call cap;
- input/output token cap;
- wall-clock deadline;
- iteration cap;
- optional local-compute limits.

Termination includes user stop, safety stop, stagnation policy, provider
unavailability, budget exhaustion, and deadline behavior.

The task becomes SUCCEEDED only when:

- every required criterion is PASS or explicitly WAIVED by authorized policy;
- every required deliverable exists in its accepted version;
- no critical objection or permission request remains open;
- the final synchronization gate passes.

Budget exhaustion or a deadline does not imply success. The task stops with its
current partial results and a machine-readable unmet-criteria report.

## 11. Semantic event protocol

### 11.1 Envelope

Every event contains:

- event_id;
- event_type;
- schema_version;
- project_id and task_id;
- actor_id and actor_kind;
- emitted_at in UTC;
- base_state_version;
- focus_id or null;
- correlation_id;
- causation_event_id or null;
- idempotency_key;
- sensitivity;
- payload;
- payload_digest.

Events are append-only. Corrections supersede prior events; they do not erase
history.

### 11.2 Initial event types

Contribution:

- HYPOTHESIS
- OBJECTION
- QUESTION
- ANSWER
- ALTERNATIVE
- CORRECTION
- EVIDENCE_ADDED
- EXPERIMENT_REQUEST
- EXPERIMENT_RESULT
- TOOL_REQUEST
- TOOL_RESULT
- BLOCKER
- CRITICAL_CONFLICT

Coordination:

- FOCUS_CHANGED
- MODE_CHANGE_PROPOSED
- MODE_CHANGED
- SYNC_CHECKPOINT_OPENED
- PARTICIPANT_DISPOSITION
- SYNC_CHECKPOINT_CLOSED
- REBASE_REQUIRED
- WORK_UNIT_CREATED
- WORK_UNIT_COMPLETED
- WORK_UNIT_BLOCKED
- ARCHITECTURE_PROPOSAL

State and goal:

- STATE_UPDATE_PROPOSED
- STATE_UPDATED
- CLAIM_ACCEPTED
- CLAIM_REJECTED
- GOAL_PROGRESS
- GOAL_CONTRACT_AMENDMENT
- TASK_STATUS_CHANGED
- ARTIFACT_VERSION_CREATED

Operations:

- MODEL_CALL_STARTED
- MODEL_CALL_COMPLETED
- MODEL_ERROR
- COST_RESERVED
- COST_RECONCILED
- PERMISSION_REQUESTED
- PERMISSION_DECIDED
- USER_INTERVENTION

Every contribution payload MUST contain summary and MAY contain rationale_summary,
claim_refs, evidence_refs, assumption_refs, artifact_refs, confidence, and
requested_next_action. It MUST NOT require hidden chain-of-thought.

### 11.3 Lifecycle

1. Producer emits an event against a base version.
2. The bus validates identity, schema, permission, digest, and size.
3. The event is persisted before externally visible state mutation.
4. A deterministic reducer returns the next state or a rejection reason.
5. The new state and sequence number are atomically committed.
6. Subscribers receive the semantic event and relevant state delta.

Malformed, unauthorized, duplicate-with-different-payload, or policy-violating
events are rejected and audited. A reducer MUST NOT perform network or tool side
effects; it only describes requested effects through events.

## 12. Cost and value accounting

### 12.1 Ledger rules

Every model or tool call uses one immutable call_id and two entries:

1. COST_RESERVED before launch using an upper-bound estimate.
2. COST_RECONCILED after completion, cancellation, timeout, or failure.

The actual record includes:

- provider, model, endpoint class, and local/remote flag;
- pricing version and currency;
- uncached input tokens;
- cached/read input tokens;
- cache-write tokens when charged;
- output tokens;
- provider and tool charges;
- start/end time and latency;
- retry_of and deduplication key;
- CPU/GPU seconds, peak RAM/VRAM, and optional energy estimate for local work;
- estimated privacy risk;
- billing status and whether the amount is estimated.

API cost is calculated from the rates pinned when the call begins:

    input cost + cache-read cost + cache-write cost + output cost
    + fixed provider/tool charges

Local-compute burden is reported separately and MUST NOT be described as free.
It MAY be converted to currency only when the user configures a rate.

Retries receive new call identifiers and link to the original call. Successful
expensive results MUST NOT be repeated when an idempotent stored result is
usable. Failed calls record any amount actually billed.

### 12.2 Budget enforcement

- A call MUST NOT start if its reservation would exceed a hard limit.
- Parallel calls reserve atomically against the same remaining budget.
- Warning thresholds emit GOAL_PROGRESS or a budget-warning event.
- Unknown pricing sets unpriced=true. Strict policy blocks the call; permissive
  policy requires a call cap and warns the user.
- User-approved budget amendments are versioned.

### 12.3 Routing objective

Routers MAY rank eligible calls using:

    expected quality gain
    - alpha * expected API cost
    - beta * expected latency
    - gamma * expected local-compute burden
    - delta * expected privacy risk

Weights come from Economy, Balanced, Research, Maximum Intelligence, or Custom
policy. The score selects resources; it does not determine whether a claim is
true. Mandatory privacy, permission, capability, and budget filters run before
ranking.

## 13. Privacy, secrets, and permissions

### 13.1 Policy precedence

The effective precedence is:

1. non-overridable platform and legal safety constraints;
2. the user's explicit decision for the current action;
3. the user's saved project policy and budget;
4. collaboration and routing policy;
5. model-proposed changes.

The user remains authoritative over model behavior within the mandatory safety
envelope. Models cannot grant themselves broader access.

### 13.2 Data classifications and privacy modes

Initial sensitivity classes:

- PUBLIC
- INTERNAL
- CONFIDENTIAL
- RESTRICTED

Privacy modes:

- OFFLINE: no external network; deterministic tools and local models only.
- PRIVATE_HYBRID: files stay local; only selected, provenance-bearing excerpts
  may leave the machine under policy.
- FULL_FRONTIER: necessary context may be sent to allowed providers, still
  subject to classification, minimization, and audit.

Provider routing MUST check privacy mode and sensitivity before cost/quality
ranking. External requests record exactly which excerpt or artifact was sent,
its digest, destination, purpose, and authorizing policy. Retrieval and
redaction SHOULD minimize transmitted context.

Secrets MUST be loaded from the OS credential store or process-local secret
injection. They MUST NOT appear in project files, event payloads, prompts, logs,
or benchmark fixtures.

### 13.3 Default permission matrix

| Action | Default |
| --- | --- |
| Read inside project workspace | Allow and log when sensitive |
| Create inside project workspace | Allow, version, and log |
| Modify inside project workspace | Allow with version backup and log |
| Delete | Explicit confirmation |
| Read outside workspace | Explicit folder grant or confirmation |
| Write outside workspace | Explicit confirmation |
| Run Python | Sandboxed, bounded, and logged |
| Run shell | Restricted sandbox, bounded, and logged |
| Upload or transmit project data | Privacy policy plus confirmation when required |
| Email or send externally | Explicit confirmation |
| Purchase or financial action | Disabled |
| Change system settings | Blocked by default |

Tool execution records requester, purpose, command or structured input, granted
scope, files changed, output digest, exit status, and resource use.

## 14. Failure, recovery, and observability

The event log, state snapshots, pending approvals, model calls, cost ledger,
artifact versions, current mode, and unresolved issues MUST be persisted for
restart.

Provider failure handling:

- preserve partial state;
- retry only within provider and budget policy;
- avoid duplicating successful calls;
- optionally substitute a compatible permitted participant;
- mark degraded operation;
- disclose when quality or a hard gate may be affected.

Every task exposes correlation identifiers, state versions, active focus, mode,
sync policy, participant status, cost totals, outstanding objections, and goal
progress. Logs MUST redact secrets and respect sensitivity policy.

## 15. Phase 0 conformance checklist

An implementation conforms to this specification when automated tests show:

- all five collaboration-mode state machines obey their entry, exit, and
  invariants;
- all synchronization gates enforce their minimum decision classes;
- duplicate events are idempotent and state replay is deterministic;
- stale, partially stale, and critical-conflict paths behave as section 7;
- structured state contains conclusions/evidence, not hidden chain-of-thought;
- a critical objection blocks acceptance without regard to model count;
- goal success cannot occur with unmet required criteria;
- API and local-compute costs remain distinct and hard budgets prevent launch;
- OFFLINE mode produces no external request;
- consequential permissions cannot be self-granted by a model;
- benchmark fixtures parse and expose machine-readable criteria.
