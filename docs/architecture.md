# AMCA Reference Architecture

Status: reference architecture reconciled with the R0 capability audit

Implementation note: sections below describe either current behavior or an
explicit target. Current code includes deterministic replay, all five
collaboration modes, evidence/decision records, persisted checkpoint
dispositions/deadlines, durable cross-process cost reservations, recovery,
export, bounded submitted-Python evaluation, exact-scope Private Hybrid
excerpting, a loopback service, and an experimental Tauri/React client. The
[capability matrix](current-capability-matrix.md) is authoritative about what is
tested, merely implemented, unvalidated with real models, or missing.

## 1. Architectural intent

AMCA is a local-first orchestration system for heterogeneous model and tool
participants. The core owns collaboration semantics; provider SDKs, local
model servers, databases, and the desktop client remain replaceable adapters.

The executable system is a Python CLI and loopback service with a Tauri/React
client. It runs fully offline with scripted participants, persists and resumes
tasks, executes all five collaboration modes, evaluates evidence behind an
explicit boundary, and reports cost. Conformance does not depend on credentials
or network access. Real-model behavior beyond one bounded R1 Solo validation,
broad multimodal ingestion,
unrestricted desktop tools, and a learned meta-controller remain outside the
validated baseline.

## 2. Design rules

1. The domain core depends on ports and domain types, never provider SDKs.
2. All canonical state changes originate from validated semantic events.
3. Reducers are deterministic and side-effect free.
4. Network, filesystem, model, database, clock, and tool behavior sit behind
   explicit ports.
5. A provider-neutral ModelAdapter is the only model boundary seen by the
   collaboration kernel.
6. Structured conclusions, evidence, objections, and concise rationale
   summaries are shared; hidden chain-of-thought is neither requested nor
   required.
7. Persistence precedes publication of canonical state changes.
8. Permission, privacy, capability, and budget eligibility are checked before
   model ranking.
9. Quorum and hard gates coordinate decisions but never substitute model count
   for evidence.
10. Every external side effect is separately authorized, idempotent where
    possible, and auditable.

## 3. Runtime topology

Current:

    User, benchmark runner, or Tauri 2/React desktop
              |
       Python CLI or loopback HTTP service
              |
    Collaboration kernel and domain
       |        |         |
    adapters  storage   tools
       |
    scripted/local/provider endpoints

No mandatory central AMCA server exists. Project state, event logs, artifacts,
permissions, and cost data remain local by default.

## 4. Logical components

### 4.1 CLI and application service

Responsibilities:

- parse commands and configuration;
- create, run, inspect, resume, and stop tasks;
- translate user input into a Goal Contract;
- select an application workflow;
- render user-visible status and final structured results.

The CLI must not contain collaboration or provider-specific rules. It invokes
application services and renders returned view models.

The current offline CLI supports:

- start a scripted task from inline text;
- choose Solo or Joint for the current benchmark runner and all five modes for
  configured kernel execution;
- choose scripted participants, or configured local providers through `run`;
- inspect state, events, goal progress, and cost;
- replay already-durable pending events without restarting model calls or
  external actions;
- export current state, immutable snapshots, and a redacted hash-chained log;
- create/list/show/stop/resume strict project definitions;
- manage exact-scope approvals, Windows credentials, and local Ollama
  inventory/measurements.

Automatic re-invocation of an interrupted paid/model call is intentionally not
a recovery behavior.

### 4.2 Project Manager

Responsibilities:

- create/open a project-scoped workspace;
- load the effective user policy;
- locate the event store, snapshots, artifacts, and configuration;
- maintain project identity and schema migrations;
- prevent path escape from the granted workspace.

### 4.3 Goal Manager

Current code treats synchronized model attestations only as evaluation triggers.
An explicit evaluator must produce verified provenance-bearing evidence, and a
persisted synchronization checkpoint must permit the resulting DecisionRecord
before a criterion can pass. The scripted demo has narrow deterministic checks;
the benchmark evaluator produces machine results for every declared fixture
criterion and routes submitted Python through a bounded sandbox. General
semantic rubric and human-review workflows remain pending.

Criterion-evidence payloads from a contribution whose `focus_resolved` value is
false are not admitted as evaluator attestations. This is distinct from
checkpoint attendance: an objecting participant can satisfy an attendance
requirement, but its unresolved evidence cannot independently trigger criterion
acceptance. This prevents a verified goal from being reported while its only
model evidence comes from a participant that still declares the focus open.

Responsibilities:

- validate Goal Contracts;
- run criterion evaluators;
- maintain progress without mutating criteria implicitly;
- identify unmet, blocked, or waived criteria;
- produce legal task status transitions;
- request the required final synchronization gate.

The Goal Manager is the only component that may propose SUCCEEDED. The state
reducer accepts that transition only if all contract invariants pass.

### 4.4 ModelAdapter port

The collaboration kernel consumes this conceptual asynchronous interface:

    generate(request) -> ModelResult
    stream(request) -> AsyncIterator of transport chunks
    cancel(call_id)
    capabilities() -> ModelCapabilities
    estimate_usage(request) -> UsageEstimate
    estimate_cost(request) -> CostEstimate
    context_limit() -> integer
    provider_metadata() -> ProviderMetadata

ModelRequest contains:

- call and participant identities;
- current focus;
- compact canonical state;
- relevant recent delta;
- exact evidence/artifact excerpts with provenance;
- requested structured response shape;
- allowed tools;
- deadline and cancellation handle;
- privacy classification.

ModelResult contains:

- externally reportable response text or structured fields;
- normalized contributions;
- usage;
- finish status;
- provider request identity;
- observed latency;
- recoverable error details.

Transport chunks are an adapter concern. The semantic event extractor may use
them to update visible status, but it must not broadcast each token to peers or
treat hidden chain-of-thought as canonical state.

Initial adapter families:

- ScriptedAdapter for deterministic offline tests and demonstrations;
- Generic OpenAI-compatible adapter for local or permitted remote endpoints;
- direct OpenAI, Anthropic, Kimi, and Gemini adapters as credentials are
  supplied;
- Ollama, llama.cpp, vLLM, and SGLang local adapters.

An adapter reports capability and availability. It does not choose
collaboration mode, authorize data transmission, accept claims, or declare goal
success.

### 4.5 Model Registry and router

The registry stores declared and observed metadata:

- provider, model id, endpoint, and local/remote status;
- modalities, context limit, and tool/structured-output support;
- reasoning/coding/task-class profiles;
- expected and observed latency;
- current pricing and cache behavior;
- reliability and historical benchmark results;
- privacy eligibility and local hardware requirements.

The first router uses deterministic policy:

1. filter by privacy and permission;
2. filter by required capability and context fit;
3. filter by availability and hard budget;
4. respect explicit user include/exclude/require directives;
5. rank remaining models by capability, expected quality, cost, latency, local
   burden, and privacy risk;
6. record the considered set and selection reason.

Brand assumptions are defaults, not permanent facts. Learned selection is a
later phase.

### 4.6 Collaboration Kernel

The kernel is the orchestration state machine. It owns:

- participant lifecycle and cancellation;
- active-focus enforcement;
- Solo execution;
- same-focus Joint Deliberation;
- semantic contribution flow;
- disagreement detection;
- staleness and rebase;
- synchronization checkpoints;
- mode-switch proposals;
- tool and evidence requests;
- goal checks.

Current Solo flow:

1. pin the active state and focus;
2. reserve call cost;
3. invoke one adapter;
4. normalize meaningful output into events;
5. reduce provisional state;
6. verify criteria;
7. close or escalate.

Current Joint flow:

1. pin the common focus and compact context.
2. reserve budget atomically for eligible calls.
3. launch participants with separate asynchronous tasks.
4. normalize each completed contribution into semantic events.
5. integrate compatible contributions immediately as provisional state.
6. rebase or quarantine stale contributions.
7. create exact disagreement records and evidence requests.
8. open the configured checkpoint.
9. accept, reopen, or block the focus.
10. evaluate goal progress and either continue or terminate.

Independent-then-Discuss adds sealed submissions and explicit reveal/synthesis.
Parallel Decomposition adds validated work-unit DAGs and gated integration.
Adaptive Hybrid currently makes deterministic, auditable selections among the
base modes; a learned selector is a future target.

### 4.7 Semantic Event Bus

The first implementation uses in-process asyncio queues and typed event
objects. Redis, NATS, or another distributed broker is intentionally excluded.

Responsibilities:

- validate event envelopes and payload schemas;
- allocate a monotonic project sequence number;
- append events durably;
- invoke the deterministic reducer;
- atomically commit state/snapshot metadata;
- publish the semantic event plus canonical delta;
- reject malformed, unauthorized, or conflicting duplicates.

Subscribers receive meaningful events, not provider token traffic.

### 4.8 Shared State Manager and reducer

The state manager owns canonical ProjectState. The reducer:

- accepts previous state plus one validated event;
- checks base version, authorization, and invariants;
- returns a new immutable state and zero or more effect requests;
- increments the state version only for a canonical mutation;
- never performs I/O.

Effect requests, such as calling a model, running a tool, asking permission, or
writing an artifact, return to the application layer. Their eventual result is
another event.

Snapshots accelerate recovery. Events after the snapshot remain the source of
truth. Replay of the same ordered log must produce the same state digest.

### 4.9 Synchronization and staleness controller

Current code persists checkpoint IDs, participant dispositions, deadlines,
expiry and authorized release records, applies distinct decision-class gates,
and uses cognitive revision for bounded-staleness/rebase decisions.

The controller evaluates checkpoints under the specification's decision-class
minimums. It tracks:

- eligible and required participants;
- dispositions and abstentions;
- critical objections;
- required tests/evidence;
- participant state-version lag;
- checkpoint deadline;
- artifact digests;
- approval requirements.

Bounded staleness is a guard around a base synchronization policy. When a
required participant exceeds the bound, canonical advancement of protected
decisions pauses; noncanonical exploration may continue. Participant release is
an explicit policy/user decision, never a silent timeout.

### 4.10 Context Builder and semantic extractor

The Context Builder assembles:

- stable task context;
- current canonical claims and assumptions;
- active focus;
- relevant evidence and artifact excerpts;
- recent canonical delta;
- active objections and requested response shape.

It deduplicates material and preserves exact code/equations when required. It
does not resend the complete chat transcript.

The semantic extractor maps a reportable model response to typed proposals,
claims, objections, evidence references, tool requests, and concise rationale
summaries. Extraction failures preserve the source call result, emit a
structured error, and do not guess a canonical conclusion.

### 4.11 Evidence and tool manager

Tool requests pass through:

1. schema validation;
2. permission and privacy evaluation;
3. user approval where required;
4. sandbox/resource bounds;
5. idempotent execution when possible;
6. capture of output, provenance, changed paths, and resource use;
7. TOOL_RESULT or EXPERIMENT_RESULT event.

Current tools are restricted arithmetic and a bounded Python evaluator that
rejects network/process imports and enforces a deadline. There is no general
shell, filesystem mutation, browser, or desktop automation tool.

### 4.12 Cost Ledger

The ledger atomically reserves and reconciles cost for each call. It keeps:

- API currency and provider-token details;
- model/tool call counts;
- wall-clock latency;
- local CPU/GPU time and memory burden;
- optional energy estimate;
- budget amendments and warnings.

The router reads the ledger through a query port. It does not rewrite entries.
Cost exhaustion creates an explicit terminal or approval state, never a false
success.

### 4.13 Persistence and telemetry

SQLite is the initial durable store. A compatible implementation SHOULD expose
logical tables or collections for:

- projects;
- tasks and Goal Contract versions;
- append-only events;
- state snapshots;
- participants and model calls;
- cost reservations and reconciliations;
- claims, evidence, and disagreements;
- synchronization checkpoints and dispositions;
- artifact metadata and versions;
- permissions and user interventions.

Artifact contents remain in project-scoped files with content digests. Large
analytical telemetry may later move to DuckDB. A vector database is not needed
for the first slice.

Telemetry records correlation ids, state versions, mode/sync transitions,
latency, cost, failures, decisive evidence, goal result, and user feedback.
Secrets and disallowed sensitive content are redacted.

## 5. Dependency direction

The intended dependency graph is:

    domain types and invariants
              ^
              |
    application services and ports
       ^          ^          ^
       |          |          |
    CLI/UI     adapters    persistence/tools

Allowed:

- adapters import port/domain types;
- application services import domain types and ports;
- tests replace every external port with fakes.

Forbidden:

- domain importing a provider SDK, CLI, UI, or database driver;
- a model adapter writing canonical state;
- a reducer performing I/O;
- a router bypassing privacy, permission, or budget filters;
- UI state becoming the source of truth.

## 6. Core data flow

For a normal model contribution:

1. The kernel creates MODEL_CALL_STARTED after policy checks and cost
   reservation.
2. An adapter receives a ModelRequest pinned to a state version.
3. The adapter returns a ModelResult.
4. Usage is reconciled and MODEL_CALL_COMPLETED is persisted.
5. The semantic extractor emits one or more contribution events.
6. The event bus validates and appends each event.
7. The reducer creates a new canonical state or a rebase/rejection outcome.
8. Subscribers receive the semantic delta.
9. The synchronization controller evaluates the checkpoint.
10. The Goal Manager updates criterion status.

For a critical disagreement:

1. OBJECTION identifies a competing claim and why it is critical.
2. The reducer opens or updates a Disagreement record.
3. The kernel requests a discriminating check.
4. Tool or source evidence is recorded with provenance.
5. Participants disposition the evidence at the required gate.
6. One claim is accepted, both remain provisional, or the task is blocked.

At no point does model count alone select the accepted claim.

## 7. Consistency and concurrency

- Canonical writes use optimistic concurrency against state version.
- The event append and resulting state-version update are atomic.
- Every side-effect request has an idempotency key.
- Duplicate identical events are acknowledged without a second mutation.
- Duplicate identifiers with different payload digests are rejected.
- Parallel model calls reserve budget in one transaction.
- Artifact writes use expected-version checks and content digests.
- Cancellation is cooperative; any late result is still recorded and then
  evaluated for relevance/staleness.

## 8. Recovery

On startup:

1. load the latest compatible snapshot;
2. verify its digest and last sequence;
3. replay subsequent events;
4. reconcile incomplete call/cost records;
5. mark interrupted calls as unknown or failed according to adapter evidence;
6. restore pending approvals and checkpoints;
7. resume only actions permitted by the Goal Contract and user policy.

An operation that may charge money or change files is not automatically replayed
unless its idempotency and authorization remain valid.

## 9. Security and privacy boundaries

The project workspace is the default filesystem boundary. Network egress is
denied in OFFLINE mode and filtered by sensitivity/provider policy otherwise.
PRIVATE_HYBRID transmits only an exact user-reviewed excerpt after a matching,
single-use approval and records a content-free transmission audit row. It fails
closed when the excerpt, scope, approval, pricing, or budget does not match.

Secrets live in the OS credential store or ephemeral process injection. They
are not copied into SQLite, event payloads, prompts, benchmark files, artifacts,
or logs.

Tool sandboxes enforce time, filesystem, process, and resource limits. Delete,
external send, upload, purchase, and system-setting actions follow the
specification's explicit permission policy.

## 10. Testing seams

Every nondeterministic dependency must be replaceable:

- ScriptedAdapter controls response content, delay, failure, and usage;
- FakeClock controls deadlines and latency;
- InMemoryEventStore verifies replay and idempotency;
- FakePermissionPort records requested authority;
- FakeToolPort returns provenance-bearing results;
- FakePricingCatalog pins token rates.

Required test scenarios include:

- faster Joint participants advance provisionally while a slower participant
  remains within the staleness bound;
- a late compatible result merges;
- a partially stale result receives a rebase delta;
- a late critical objection reopens the focus;
- a hard gate blocks on an unresolved critical objection;
- duplicate delivery does not double-count cost or mutate state twice;
- restart reconstructs the same state digest;
- OFFLINE mode never invokes a remote adapter;
- no test fixture or canonical state requires hidden chain-of-thought.

## 11. Evolution path

The architecture adds features without replacing the core:

- Independent-then-Discuss, Parallel Decomposition, deterministic Adaptive
  Hybrid, and the Tauri/React loopback client are current increments.
- The consolidated local asset store uses one bounded extractor for text, PDF,
  modern Office, HTML, image, audio, and video inputs. Optional fixed-command
  Tesseract OCR and a pinned whisper.cpp model add local printed-text and
  timestamped speech representations without changing the immutable native
  asset. R6 must still add parent/child graph edges, handwriting/visual and
  video-frame semantics, and approved native multimodal delivery without
  weakening provenance or privacy.
- Local model management must progress from profiled/discovered inventory to a
  reproducible real-model acceptance run.
- Desktop tools add permissioned MCP-style adapters.
- The learned meta-controller consumes telemetry but remains constrained by
  Goal Contract, safety, privacy, and budget filters.
