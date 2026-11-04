# AMCA Delivery Roadmap

Status: staged implementation plan  
Planning principle: prove orchestration and evidence discipline before desktop
polish or broad modality support

Current status after the R0 audit: the repository contains tested M0 contracts;
M1-M3 lifecycle, persistence, evidence, synchronization, and budget controls;
all five collaboration-mode implementations; a bounded submitted-Python
evaluator; exact-scope Private Hybrid controls; local-runtime inventory; and an
experimental packaged Tauri/React client. These facts do not complete later
research or production gates. R1/R2 validation is complete for the bounded
required slices, and the R3 heterogeneous single-task pilot is closed as a
negative/inconclusive result. Real-model validation of all collaboration modes,
broad-format completion, learned routing, clean-machine signed release
validation, and the multi-task comparative study remain outstanding. See the
[current capability matrix](current-capability-matrix.md).

## 1. Delivery boundary

### Current repository iteration

This iteration delivers:

1. all Phase 0 contracts:
   - five collaboration-mode semantics;
   - five synchronization semantics;
   - canonical state, claim, evidence, disagreement, and decision records;
   - Goal Contract;
   - semantic event protocol;
   - cost and value accounting;
   - privacy, permission, recovery, and observability rules;
   - eight self-contained benchmark fixtures;
2. an executable Phase 1 orchestration prototype:
   - Python command-line execution;
   - fully offline deterministic/scripted demonstration;
   - provider-neutral adapters and configurable provider adapters;
   - local persistence and resume;
   - provisional goal-attestation collection plus an explicit evaluator hook
     and a narrow deterministic evaluator for the scripted demo;
   - deterministic model/collaboration routing sufficient for the slice;
   - API and local-compute cost tracking;
   - Solo and asynchronous same-focus Joint Deliberation;
   - sealed Independent-then-Discuss and Parallel Decomposition;
   - deterministic Adaptive Hybrid selection and switching;
   - staleness/rebase plus persisted soft, quorum, hard, and bounded-staleness
     checkpoint dispositions tied to evidence-backed decisions.

This iteration does not promise production quality or proof that collaboration
outperforms a single model. It creates the smallest auditable system with which
that hypothesis can begin to be tested.

### Explicitly subsequent

The following acceptance work remains later even though some supporting code
already exists:

- retain the completed R1 local-model evidence and avoid regressions in its
  redirect, criterion-link, runtime-cancellation, persistence, and cost gates;
- extend the now-consolidated broad local asset path with parent/child graph
  edges, OCR/transcription/frame semantics, and permissioned native multimodal
  delivery before claiming a complete canonical Asset Graph;
- add general semantic/human evaluation and permissioned filesystem, Git,
  LaTeX, database, browser, and desktop tools;
- validate each collaboration mode with real models and preregistered measures;
- implement and validate learned routing/meta-control;
- complete a signed, clean-machine desktop install/restart/uninstall gate;
- run the full 20-50 task comparative research study and production evaluation.

## 2. Gate policy

A milestone is complete only when every required gate passes. A date, call
count, or code-complete declaration does not waive a gate.

Each gate records:

- evidence produced;
- command or evaluator used;
- accepted artifact versions;
- unresolved risks;
- user waiver, if the gate permits waiver.

Security, privacy, data-loss, and required correctness gates are not silently
waivable by a model or router.

## 3. Milestone M0 — Phase 0 contract freeze

Deliverables:

- specification.md;
- architecture.md;
- roadmap.md;
- tasks.json with eight fixtures across four domains.

Required gates:

| Gate | Acceptance |
| --- | --- |
| M0.1 Terminology | Every mode, sync policy, task status, claim status, and decision class has one stable name |
| M0.2 No-hidden-reasoning boundary | State/events require structured conclusions, evidence, assumptions, objections, and concise rationale only |
| M0.3 Decision integrity | A critical objection blocks acceptance and no rule treats majority support as truth |
| M0.4 Goal contract | Required criteria, deliverables, budgets, termination, and legal success are machine-representable |
| M0.5 Event determinism | Envelope, idempotency, versioning, persistence-before-publication, and replay rules are defined |
| M0.6 Cost/privacy | API and local cost are distinct; hard budget and OFFLINE behavior are explicit |
| M0.7 Fixtures | JSON parses, ids are unique, exactly eight tasks exist, and each domain has two |
| M0.8 Scope | Tested core, experimental Tauri client, unintegrated broad ingestion, and future research gates are visibly separated |

Exit evidence:

- JSON parser success for the benchmark file;
- a documentation review against the checklist in specification.md;
- no secrets, copied long-form source text, or network dependency in fixtures.

## 4. Milestone M1 — Offline domain kernel

Goal: prove canonical state and event behavior before any provider call.

Deliverables:

- Python package and CLI entry point;
- typed domain records;
- Goal Contract validator and evaluators;
- append-only event store plus snapshots;
- deterministic reducer and replay;
- ScriptedAdapter;
- cost ledger;
- test doubles for clock, permission, pricing, and tools.

Required gates:

1. Fresh project, task creation, run, inspection, and export work offline.
2. Duplicate identical events are idempotent.
3. A duplicate event id with a different digest is rejected.
4. Event replay produces the same state digest.
5. Restart resumes goal, state, events, cost, mode, and open issues.
6. Required unmet criteria prevent SUCCEEDED.
7. OFFLINE policy makes remote adapter invocation impossible.
8. Canonical state contains no field that requires hidden chain-of-thought.
9. Hard budget reservation blocks a call before launch.
10. Secret-like values are redacted from logs and never stored in fixtures.

Stretch work must not delay this gate.

## 5. Milestone M2 — Solo vertical slice

Goal: complete one real task through the full orchestration path.

Deliverables:

- explicit or deterministic model selection;
- compact Context Builder;
- ScriptedAdapter plus at least one configurable provider adapter;
- semantic result extraction;
- Solo collaboration state machine;
- criterion verification and final structured report.

Required gates:

1. The CLI accepts a complex Goal Contract and active focus.
2. Cost is reserved, measured, reconciled, and shown.
3. Model output becomes structured claim/evidence/objection events.
4. A verified result can satisfy the goal and create a decision record.
5. A failed check, critical objection, budget stop, or user stop cannot report
   success.
6. A provider failure preserves state and produces a useful degraded/error
   report.
7. The same demonstration runs with ScriptedAdapter and no credentials.

## 6. Milestone M3 — Asynchronous Joint Deliberation

Goal: demonstrate the distinctive same-focus collaboration behavior with
different response speeds.

Deliverables:

- two or more concurrently scheduled participants;
- one shared active focus;
- semantic proposal, objection, evidence, and correction exchange;
- explicit disagreement records;
- staleness classifier and compact rebase;
- synchronization controller;
- final collaboration log and cost report.

Required gates:

1. All participants receive the same canonical focus and pinned state version.
2. Fast participants may advance provisional work without waiting at every
   response.
3. A compatible late result merges once and is attributed.
4. A partially stale result receives the canonical delta for reconsideration.
5. A plausible late critical contradiction reopens the focus.
6. Bounded staleness pauses protected canonical decisions at the configured
   lag while allowing noncanonical verification work.
7. Soft, quorum, and hard checkpoint tests show distinct behavior.
8. A quorum with a critical objection does not accept a claim.
9. A hard-gate timeout blocks or explicitly releases a participant; it never
   silently succeeds.
10. Different completion order produces an equivalent accepted conclusion when
    the same commutative evidence events are supplied.
11. Cost is not double-counted across retries, duplicate events, or resume.
12. The final report names accepted conclusions, evidence, assumptions,
    objections and their dispositions, unmet criteria, state/artifact digests,
    per-participant usage, and total cost.

First serious demonstration:

- user supplies a difficult mathematical or scientific goal;
- GPT, Claude, Kimi, local, or scripted equivalents are selected under policy;
- the system runs them asynchronously on the same focus;
- evidence or a bounded local calculation resolves disputes;
- state survives restart;
- completion is driven by Goal Contract criteria.

Real external providers are optional for the offline gate and require user
credentials, privacy permission, and pricing configuration.

## 7. Milestone M4 — Benchmark pilot

Goal: test functionality and collect directional evidence without overstating
research conclusions.

Protocol:

- run all eight Phase 0 fixtures;
- include at least Solo and static Joint conditions;
- pin model versions, prompts, policies, pricing, temperature, seeds where
  supported, and evaluator versions;
- record correctness/rubric score, goal completion, latency, tokens, API cost,
  local-compute burden, calls, objections, rework, and failures;
- preserve run manifests and raw reportable outputs under privacy policy.

Required gates:

1. Every fixture has machine-readable criterion results.
2. Coding submissions run only in a bounded sandbox.
3. Numeric scoring uses declared tolerances.
4. Rubric scoring records criterion-level evidence, not only one opaque score.
5. A failed required criterion caps the task as incomplete.
6. Results distinguish engineering validation from statistical evidence.
7. No claim of superiority is made from eight tasks alone.

Exit decision:

- fix domain/state/event defects before adding modes;
- proceed when failures are ordinary capability limitations rather than
  orchestration or evaluation corruption.

## 8. Milestone M5 — Remaining static collaboration modes

### M5A Independent-then-Discuss

Add:

- sealed peer-isolated submissions;
- content digests and reveal barrier;
- normalized alternative comparison;
- transition into Joint synthesis.

Gate: tests prove that peers cannot observe provisional submissions before the
reveal event and that timeouts/releases are explicit.

### M5B Parallel Decomposition

Add:

- work-unit dependency graph;
- explicit input/output contracts and assignees;
- concurrent ready-unit scheduling;
- artifact ownership/version checks;
- integration and conflict checkpoint.

Gate: all child completions without integration success cannot complete the
parent goal.

### M5C Adaptive Hybrid

Add deterministic transitions among all four base modes using complexity,
coupling, novelty, disagreement, stagnation, time, cost, and user policy.

Gate: every transition is reproducible from logged features and policy; user
mode locks are honored; no learned policy is required.

## 9. Milestone M6 — Provider and local-model breadth

Build order:

1. provider-neutral ModelAdapter;
2. one frontier API;
3. Claude;
4. Kimi;
5. Ollama;
6. one measured local open-weight model;
7. Gemini;
8. llama.cpp;
9. vLLM/SGLang and other OpenAI-compatible endpoints.

Add:

- hardware profiler;
- local model capability and benchmark profiles;
- local-first escalation cascade;
- mixed local/cloud Joint Deliberation;
- privacy-aware excerpt selection;
- provider retry, substitution, and degraded-mode policy.

Gates:

- Local Only/Offline cannot reach a remote endpoint.
- Mixed and all-local sessions obey the same collaboration contracts.
- Local inference records time, memory, and optional energy rather than zero
  cost.
- Provider adapter contract tests run without real billing.

Model-family recommendations remain configuration, not hard-coded product truth.

## 10. Milestone M7 — Desktop MVP

Only after the Python kernel gates pass, add:

- Tauri 2 desktop shell;
- React/TypeScript interface;
- local IPC or WebSocket bridge;
- project, research-room, whiteboard, artifact, cost, approvals, logs, and
  settings views;
- visible model status, mode, synchronization, active focus, disagreement,
  budget, and goal progress.

Desktop gates:

- the Python core remains the sole source of canonical truth;
- closing/restarting the UI cannot lose task state;
- secrets use the OS credential store;
- approval prompts show exact action and data scope;
- CLI and desktop produce equivalent state for the same scripted run.

The desktop milestone does not yet imply universal multimodal support.

Current progress through this milestone: TXT/Markdown/PDF/Word/Excel/
PowerPoint/HTML extraction is wired; common raster containers have optional
Tesseract English printed-text OCR; and FLAC/MP3/Ogg/WAV have optional local
whisper.cpp timestamped transcription. Real generated image/audio validation
passes. Visual semantics, video-frame/audio extraction, M4A transcription,
mixed folders/archives, and the full Asset Graph remain later gates.

## 11. Milestone M8 — Local multimodal ingestion

Build in this order:

1. TXT, Markdown, and code;
2. PDF;
3. Word;
4. CSV/Excel;
5. images;
6. audio;
7. video;
8. PowerPoint;
9. mixed project folders and archives.

Add a canonical Asset Graph preserving source path, page/time/coordinates,
parent-child relationships, modality, hashes, derived representations, and
provenance. Retrieval starts with keyword/metadata filters before local
embeddings.

Gates:

- derived content always resolves to an exact source locator;
- archives cannot escape the project workspace;
- external APIs receive only policy-permitted selected assets/excerpts;
- deterministic/local extraction is attempted before costly escalation;
- ingestion failure cannot silently fabricate content.

## 12. Milestone M9 — Permissioned tools and automation

Add narrowly scoped tools first:

- project filesystem;
- bounded Python/SymPy;
- Git and LaTeX;
- then document editing, databases, browser, and media transforms.

Every tool requires a typed request, policy decision, sandbox/resource limit,
provenance-bearing result, and changed-file record. Delete, send, purchase, and
system-setting defaults remain restrictive.

## 13. Milestone M10 — Learning and research platform

After sufficient trustworthy telemetry:

- maintain observed performance by model, task class, mode, and sync policy;
- compare single-model, independent aggregation, fixed decomposition, debate,
  static Joint, deterministic Adaptive Hybrid, and cost-aware Adaptive Hybrid;
- expand to 20-50 or more preregistered benchmark tasks;
- train or optimize a contextual ranking/meta-control policy;
- keep deterministic safety, privacy, budget, and user-policy filters outside
  the learned component.

Research metrics:

- correctness and scientific validity;
- novelty with human/evidence review;
- code/test and experiment success;
- robustness and contradiction resolution;
- goal completion;
- latency, tokens, API cost, local burden, and calls;
- user intervention and satisfaction.

Gate: report uncertainty, task sampling, model/version drift, evaluator
limitations, and negative results. Learned routing must be reproducible,
auditable, and safely overridable.

## 14. Definition of the first usable release

The repository implements the following M1–M3 capabilities, but R0 does not
declare a production release because real-model and release-environment gates
remain open. A usable validated release must:

- create and resume a local task;
- run without network credentials;
- use provider adapters when explicitly configured;
- execute all five collaboration modes under their documented gates;
- preserve structured conclusion/evidence state;
- manage staleness and synchronization;
- track goal progress, cost, latency, and local burden;
- enforce privacy, permission, and hard budgets;
- stop with an honest success, partial, blocked, or exhausted result;
- export the final result plus an auditable collaboration log.

Independent, Parallel, deterministic Hybrid, and the experimental Tauri client
are implemented increments. They become validated release claims only after
their real-model, security, and packaging gates pass. Broad multimodal ingestion
is not yet a product capability.
