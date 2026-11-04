# AMCA current capability matrix

Status date: 2026-08-23
Evidence baseline: `e823c2f` (`amca-baseline-184-tests-passing`); the C1
collaboration-routing milestone landed on the audited R8 working tree with
346 tests passing before commit.

This is the R0 repository audit required by
`AMCA_Remaining_Work_and_Codex_Credit_Policy.txt`. A component is not called
complete merely because its types or adapter exist. `DONE` means the bounded
repository gate is covered by automated tests. `PARTIAL` means a useful,
tested slice exists but an acceptance gate remains. `UNVALIDATED` means code
exists without the required real-environment evidence. Target-only work is
`MISSING`; overlapping implementations are `DUPLICATED`.

| Area | Classification | Evidence in the repository | Remaining gate or risk |
|---|---|---|---|
| Domain contracts, events, deterministic reducer | DONE | `domain.py`, `events.py`, `state.py`; replay, collision, provenance, checkpoint, and secret-canary tests | Schema migration beyond the current SQLite schema remains future work. |
| SQLite snapshots, recovery, export, and audit log | DONE | Atomic transitions, immutable snapshots, pending-event recovery, stable digests, hash-chained export, and CLI workflow tests | The audit chain is not signed or externally anchored. |
| Durable model-call budget ledger | DONE | Cross-connection atomic reservations, settlement, restart reconciliation, rebase accounting, and kernel/storage tests | Provider usage can exceed an estimate after admission; crash reconciliation intentionally overcounts when usage is unknown. |
| Project/Goal Contract lifecycle | DONE | Strict definition validation and `project create/list/show/stop/resume`; persisted-project reuse tests | No rich desktop project editor yet. |
| Solo and Joint modes | DONE / PARTIAL | Kernel tests cover both; R1 validates Solo end-to-end with real `qwen3:1.7b`; R3 v7 completed one exact compliant GPT/Claude/Kimi Solo-versus-Joint comparison, while v8-v10 exercised bounded correction/objection transport and safe failure | The R3 single-task pilot closed negative/inconclusive. V7 found both conditions wrong at 1/4 with no Joint improvement; broader comparative quality remains UNVALIDATED pending R5. |
| Independent-then-Discuss | DONE | Sealed peer-isolated submissions, reveal/synthesis, timeout/release, restart, and atomic-budget tests in `test_modes.py` | Real-model comparative evaluation is UNVALIDATED. |
| Parallel Decomposition | DONE | Typed DAG/work units, concurrent ready units, dependency and artifact gates, integration report, privacy and staleness tests | Real-model comparative evaluation is UNVALIDATED. |
| Adaptive Hybrid | PARTIAL | Deterministic audited selection/switch records and execution of Solo, Joint, Independent, and Parallel bases | The learned meta-controller and measured policy improvement are MISSING. |
| Desktop collaboration routing (C1) | DONE for all-mode desktop routing / PARTIAL overall | Protocol 15 contract (`collaboration.py`) represents all five architectures with separate model-set and architecture decisions; deterministic reviewed decision table (safety → user lock → verification/novelty to Independent→Discuss → decomposable weak coupling to Parallel → high complexity to Joint → ambiguous medium to Adaptive → Solo); feasibility degradation recorded, never count-based promotion; Auto uses task shape plus canonical-state signals only; ALL FIVE architectures execute through existing kernel paths (AMCA 0.2.2); C1.6 deterministic decomposition planner slices sequenced/domain goals into bounded work units with a mandatory kernel-validated integration unit and round-robin assignees; persisted `collaboration_decision` artifact includes plan metadata; conversation store migrated to five modes with regression-tested in-place rebuild | Collaboration inspector UI, "why AMCA chose this" card, DAG progress surfacing (C1.5/C1.7/C1.8) are not built; lexical thresholds await benchmark calibration; real-model quality remains UNVALIDATED pending V1/V2. |
| Synchronization/checkpoints | DONE | Async, Soft, Quorum, Hard, and Bounded Staleness records; dispositions, deadlines, expiry, authorized release, and decision-class gates | Authorization identity is trusted application provenance, not cryptographic identity. |
| Goal/evidence evaluation | PARTIAL | Verified `EvidenceRecord`/`DecisionRecord` gate; deterministic fixture evaluators; bounded math and Python evidence | General semantic rubric and human-review evaluator workflows are MISSING. |
| Eight-fixture benchmark harness | PARTIAL | Eight balanced fixtures; all declared criterion kinds produce machine results; coding fixture uses the bounded sandbox | It is an offline orchestration probe, not evidence that collaboration improves quality. The preregistered 20-50 task study is MISSING. |
| Provider adapters | PARTIAL | Offline contract tests cover all adapters; R1 validates real Ollama; completed bounded OpenAI, native Anthropic, and native Kimi/Moonshot R2 slices live-validate authentication, provider request schemas/mapping, structured parsing, streaming, bounded cancellation, usage/cost handling, zero retries, and credential non-persistence. Native Gemini attempts additionally prove bounded safe failure on HTTP 404. | Gemini streaming/semantics/cancellation remain UNVALIDATED because both authorized calls failed before streaming; Gemini is skipped and other named endpoints also remain UNVALIDATED. |
| Model Hub and model-access Agent Fabric | PARTIAL | AMCA 0.2.1 has one Settings Model Hub; AMCA API/BYOK/Local sources; provider-level credentials; explicit authenticated catalog discovery/import/edit; existing direct profiles; provider-catalog execution; mixed-source Model Teams; deterministic hardware compatibility and Cost-Quality Gate; recommended/installed local catalog; versioned endpoint/pricing/task-cost registries; dormant event-driven MAIA and eight Nano role contracts; task-time Auto Intelligence/Manual selection; protocol 15; focused tests and packaged-backend smoke. | Managed AMCA account provisioning, Bedrock gateway/billing, official live pricing refresh, provider-specific catalog pagination, in-app local weight installation/progress/cancel, and llama.cpp catalog integration require separate deterministic infrastructure and remain inactive. |
| Live Agent Fabric runtime (A1 core) | PARTIAL | `agent_runtime.py` executes planning-layer task graphs through audited lifecycle records (proposed→authorized→queued→starting→running→verifying→completed→merged→retired; failed/cancelled/retry via sanctioned edges), deterministic-first cascade, duplicate-result reuse keyed by canonical focus digest, hard USD budget that cancels instead of overrunning, concurrency ceiling, max-attempt retries; desktop runs derive `live_organization` artifacts from MEASURED kernel calls and surface completed/active/cost in the orchestration panel; all three A1 stop-gate fixtures pass | Service/UI scheduling of standalone agent organizations (not chat-attached), batching layer, verification-cell cascade stage, recursive spawn proposals, and installable packages remain MISSING | |
| Permissioned command execution (X1.4) | DONE for bounded approved tier / PARTIAL for product surface | `propose_argv_command` binds an exact argv under explicit RUN_COMMAND approval; execution is shell-free inside grant-scoped cwd with sanitized environment, hard timeout, output cap, incremental pollable run log, and full audit events; deterministic allowlist policy gates BOTH proposal and execution (ChatGPT-required correction: cwd containment is not a filesystem boundary); frozen deny-list holds even under approval; six security tests including tampering and shell refusal | Terminal UI streaming wiring, broader per-executable policies (build/test/lint runners), OS-level sandboxing, and multi-repo Git write operations remain next | |
| Project workspace files (X1.2) | DONE for managed text files / PARTIAL for product surface | `workspace.py`: contained per-project text files with bounded version history, stale-base conflict fail-closed, unified diffs per save, HTTP list/open/save/delete over protocol 15, traversal and size defenses tested | Files/Changes pane UI wiring, revert-to-version endpoint, multi-repo Git integration are next |
| Complete Master Agent Fabric | PARTIAL | 41 permanent core roles, more than 150 built-in cross-domain specialists, custom-agent persistence, deterministic Economy Manager and Governor, bounded resource envelopes, auditable logical task graphs, deterministic-first Nano policy, per-model role assignments, two-round Joint critique/revision, lifecycle model, Agent Library, and user controls are implemented. | Full live scheduling of Nano/Micro DAGs, bounded recursive spawning/retirement, duplicate-result reuse, marketplace packages, and a distributed worker plane remain future milestones. |
| Credentials and secret persistence | DONE for desktop model vault | Lazy resolution, environment/Windows Credential Manager abstraction, per-user DPAPI fallback, write-only desktop set/status/delete, model-scoped Tabitoken resolution, recursive persistence rejection, and plaintext/SQLite canary tests | The desktop loopback session prevents remote access but does not defend configuration integrity from a hostile process already running as the same Windows user. |
| Offline privacy | DONE | Router/kernel reject cloud adapters; local endpoints require loopback; provider and local-runtime HTTP paths reject redirects | A hostile local process remains inside the trusted-machine boundary. |
| Private Hybrid privacy and permissions | PARTIAL | Exact excerpt scope, one-use approval, content-free transmission ledger, guarded adapter, CLI approval flow, desktop approval queue, bounded live OpenAI transmission/cancellation, and no plaintext-key persistence | The approval identity is local trusted provenance; transmission auditing is in its own store rather than the canonical event chain. Other cloud providers remain unvalidated. |
| Hardware-aware local runtime | DONE for R1.5 | Ollama 0.32.9 and `qwen3:1.7b` Q4_K_M were discovered, measured, completed a persisted Offline Solo goal, and were selected through the desktop model library | Downloads/deletes, local Joint, and broader hardware/model validation remain future work. |
| Restricted tools | PARTIAL | Bounded arithmetic and network/process-denying Python sandbox with evaluator evidence | Filesystem, Git, LaTeX, database, browser, desktop, and general external tools are MISSING. |
| Native artifact engine (W1) | DONE for the v1 contract / PARTIAL for breadth | `artifacts.py` + `artifact_render.py` + protocol-15 endpoints + workbench cards: one engine, immutable append-only SHA-256 versions, restore-as-new-version, mandatory provenance, deterministic local renditions (markdown->html/docx, csv->xlsx via openpyxl, pptx from JSON slides via python-pptx, zip bundle), chat-run bridge authors fenced model blocks into real artifacts with model provenance, tested converter_unavailable honesty; all-four-deliverables stop-gate demo passes | PDF rendition pipeline, rich per-type previewers, artifact authoring beyond workbench cards remain |
| Desktop service and UI | PARTIAL | AMCA 0.2.1 provides loopback protocol 15, AMCA Studio branding and unified navigation, Projects with nested chats plus separate standalone Chats in the sidebar, shared files/instructions, direct file/folder/repository drop, task-driven Auto Intelligence/Manual selection, visible routing rationale/rounds, safe Markdown/GFM/KaTeX rendering, live authorized Agent Fabric visibility, Agent Library, unified Model Hub and Teams, Workbench surfaces, Computer access profiles/grants/activity/revocation, Review Queue, durable automation definitions and permission-gated run-now dispatch, cost display, cancellation, protected credentials, separate governed task/run state, events/assets/approvals views, React build, and Tauri shell. | Automatic trigger evaluation, executable command/browser/application control, live connectors, native rich artifact production/editors, and remaining product acceptance gates are not yet complete; the session token does not defend against a hostile local process. |
| Desktop packaging | PARTIAL / UNVALIDATED | AMCA 0.2.1 PyInstaller backend smoke, production React build, Cargo check, optimized Tauri build, and unsigned NSIS/MSI artifacts are recorded in `packaging/VERIFICATION.md`. | No code signature or clean-machine install/upgrade/restart/uninstall acceptance run is recorded. |
| Basic project assets | DONE | `assets.py` is the canonical immutable store and now consumes the bounded extractor for TXT/Markdown/PDF/DOCX/XLSX/PPTX/HTML, common raster images, audio, and video. Records preserve native bytes, SHA-256, modality, extraction status/method/note, metadata, version, and locators; Chat can attach up to eight files/25 MB. | Project deletion/garbage collection and richer cross-asset graph edges remain missing. |
| Broad multimodal extraction | PARTIAL | Office/HTML text and spreadsheet/slide locators are available to models through bounded untrusted-data context. Image/audio/video containers are locally validated; optional fixed-command Tesseract extracts English printed text and optional pinned whisper.cpp tiny.en produces timestamped FLAC/MP3/Ogg/WAV transcripts under size/time/output limits. Both passed real generated-media validation and fail closed to metadata-only. | Handwriting, visual semantics, M4A transcription, video audio/frame/scene interpretation, coordinate graph, and permissioned native multimodal provider transmission remain missing. Metadata-only media must not be presented as semantically understood. |
| Dependencies | DONE for the wired slice | `pypdf` is the only core Python dependency; Office/HTML/media header parsing is standard-library. Optional Tesseract and hash-pinned whisper.cpp runtimes are installed separately, reported by the UI, and never downloaded implicitly by a chat run; provider, SymPy, and coverage extras are explicit. | Installers do not bundle the third-party media runtimes; a new PC must run the explicit local-media setup script and accept its third-party licenses. |
| Documentation before this R0 audit | OUTDATED DOC | README, architecture, roadmap, and provider notes described implemented modes, desktop, credentials, privacy, and sandbox work as future | Reconciled by this R0 change; future capability changes must update this matrix in the same commit. |
| Repository/generated artifacts | NEEDS REDESIGN | UI `dist`, release backend/installers, local DB, and secrets are excluded, but legacy Tauri `target/debug` outputs are tracked | Verification can dirty those generated files. Remove them from version control only in a separately approved cleanup; `project-materials/` remains intentionally outside this software commit. |

## R1/R1.5 conclusion

The prototype now has one reproducible real local-model success, documented in
[`validation/r1-local-model.md`](validation/r1-local-model.md), plus one real
desktop local-model path documented in
[`validation/r1-5-desktop-local-ui.md`](validation/r1-5-desktop-local-ui.md).
It is not a completed research result or production release. The policy's next required
milestone is R2 live-provider smoke validation. The bounded OpenAI, native
Anthropic, and native Kimi/Moonshot slices are complete and documented in
[`validation/r2-openai.md`](validation/r2-openai.md) and
[`validation/r2-anthropic.md`](validation/r2-anthropic.md), and
[`validation/r2-kimi.md`](validation/r2-kimi.md). The optional Gemini attempt is
recorded in [`validation/r2-gemini.md`](validation/r2-gemini.md) but remains
incomplete after two zero-retry HTTP 404 responses and is now skipped; other providers remain separately
bounded work. Broader multi-turn ChatGPT-style
desktop product hardening remains bounded R7 work.

## R3 preflight

The first formal heterogeneous comparison is frozen in
`experiments/r3-first-heterogeneous.json` and documented in
[`validation/r3-preflight.md`](validation/r3-preflight.md). The manifest fixes
the task, Goal Contract criteria, Kimi Solo comparator, GPT/Claude/Kimi Joint
team, two-round protocol, bounded staleness, metrics, eight-call maximum,
zero-retry/zero-rebase rules, and $0.03 hard API ceiling. No R3 provider call
used one of eight calls, then stopped on a capped invalid Kimi response before
Joint dispatch. The result is documented in
[`validation/r3-first-heterogeneous-attempt.md`](validation/r3-first-heterogeneous-attempt.md);
it is not a collaboration comparison.

The response-bound correction is frozen separately in
`experiments/r3-first-heterogeneous-v2.json` and documented in
[`validation/r3-v2-preflight.md`](validation/r3-v2-preflight.md). Its complete
offline rehearsal passes with the unchanged eight-call and $0.03 ceilings. Its
first live Kimi response nevertheless exhausted 512 output tokens and failed
semantic parsing, so v2 also stopped before Joint dispatch; see
[`validation/r3-v2-attempt.md`](validation/r3-v2-attempt.md).

The evidence-driven staged-prompt correction is frozen in
`experiments/r3-first-heterogeneous-v3.json` and documented in
[`validation/r3-v3-preflight.md`](validation/r3-v3-preflight.md). Its offline
rehearsal proves round-one schema isolation and preserves the eight-call,
zero-retry/zero-rebase, and $0.03 boundaries. Live v3 validated its compact
first round, then stopped when the detailed second response exhausted 512
tokens; see [`validation/r3-v3-attempt.md`](validation/r3-v3-attempt.md).

The compact-review correction is frozen in
`experiments/r3-first-heterogeneous-v4.json` and documented in
[`validation/r3-v4-preflight.md`](validation/r3-v4-preflight.md). It preserves
all v3 execution and budget bounds while strictly limiting round-two semantic
output. Its full offline rehearsal passed. Live v4 consumed its exact eight-call
allocation for $0.01104940 with zero retries and rebases. Solo completed but
left all deterministic criteria `not_run`; Joint completed four calls, then a
capped invalid Claude response and Kimi availability failure blocked hard
synchronization. See [`validation/r3-v4-attempt.md`](validation/r3-v4-attempt.md).
This validates bounded heterogeneous dispatch and safe failure, not comparative
quality or model-collaboration superiority.

The post-v4 offline audit in
[`validation/r3-v4-offline-audit.md`](validation/r3-v4-offline-audit.md) closes
the discovered spending guard gap. A staged condition is complete only when all
planned provider calls finish and every participant supplies every required
criterion attestation. A parsed tool-only review now stops as
`partial_protocol_failure` before the next condition is dispatched. This is an
execution-safety correction, not completion of the R3 research gate.

The separately frozen v5 manifest in
`experiments/r3-first-heterogeneous-v5.json` uses an evidence-only literal
review template and the fail-closed attestation gate. Its exact eight-call
scripted rehearsal passes and is documented in
[`validation/r3-v5-preflight.md`](validation/r3-v5-preflight.md). Live v5 made
two Kimi Solo calls for $0.00296832, then stopped when the review again exhausted
512 tokens and produced invalid JSON. The fail-closed gate prevented all six
Joint calls. See [`validation/r3-v5-attempt.md`](validation/r3-v5-attempt.md).

The offline Kimi contract audit in
[`validation/r3-kimi-contract-audit.md`](validation/r3-kimi-contract-audit.md)
adds versioned native JSON mode to the bounded R3 adapter and records provider
length-cap reasons before semantic parsing while preserving usage accounting.
V6 explicitly opts into that contract and passes the exact eight-call offline
rehearsal; see [`validation/r3-v6-preflight.md`](validation/r3-v6-preflight.md).
The provider behavior was subsequently exercised live as described below.

Live v6 subsequently completed all eight calls for $0.01180518 and validated
native Kimi JSON transport. Solo and Joint were both incorrect with 1/4 criteria
passed and no material correction. A post-run audit also found three forbidden
model-authored Solo review events that the attestation-only compliance gate did
not detect. See [`validation/r3-v6-attempt.md`](validation/r3-v6-attempt.md).
The exact offline review-contract validator is now implemented: v6 review
rounds require complete attestations and zero participant-authored events
between their two synchronization checkpoints. Its negative regression stops
before Joint, while the truly evidence-only eight-call rehearsal completes.
See [`validation/r3-v6-offline-contract-audit.md`](validation/r3-v6-offline-contract-audit.md).
V7 now separately preregisters that exact complete-attestations/empty-events
contract and preserves all v6 execution and budget bounds. Its network-free
preflight and exact eight-call scripted rehearsal pass; see
[`validation/r3-v7-preflight.md`](validation/r3-v7-preflight.md).

The authorized live v7 comparison subsequently completed all eight calls for
$0.01086983 with zero retries/rebases. Both conditions satisfied the exact
review contract, but both were incorrect at 1/4 criteria and Joint made no
material correction or improvement. Because the event-free review contract
also makes explicit correction/objection events structurally unavailable, the
run is valid negative evidence but does not complete the central R3 mechanism
gate. See [`validation/r3-v7-attempt.md`](validation/r3-v7-attempt.md). The next
bounded task was an offline-only protocol correction, not another v7 live run.

V8 now preregisters that correction. Its falsifiable provisional round exposes
a checkable claim, while each review permits at most one bounded correction or
objection alongside complete criterion attestations. The exact eight-call
rehearsal exercises both allowed event types; the violation regression stops
before Joint for excess, disallowed, overlong, or malformed review events. The
network-free preflight preserves the $0.03 ceiling. See
[`validation/r3-v8-preflight.md`](validation/r3-v8-preflight.md).

The authorized live v8 attempt subsequently used all eight calls for
$0.01252352 with zero retries/rebases, but Claude's second Joint response hit
the 512-token cap. Only 7/8 calls completed, so there is no valid quality
comparison. Live OpenAI and Kimi did exercise one bounded objection and one
bounded correction, but both retained an incorrect result. The audit also found
that the report treats the system-generated Claude `model_error` as a
participant-authored review event and reports protocol details that are not
independently evaluable after the failed atomic round. See
[`validation/r3-v8-attempt.md`](validation/r3-v8-attempt.md). The next bounded
task was an offline failure-classification correction, not a v8 retry.

That offline correction now uses the canonical model-authorable event set,
marks review compliance `null` when provider execution is incomplete, clears
non-evaluable contract details, and emits no comparison unless both conditions
are complete. Its exact failure-shaped regression and complete v8 rehearsal
pass. See
[`validation/r3-v8-offline-failure-audit.md`](validation/r3-v8-offline-failure-audit.md).
R3 remained incomplete pending an offline v9 response-budget preregistration;
no further live call was part of that correction.

V9 now preregisters a 640-token response ceiling, a 4,000-token conservative
input allowance above v8's observed 2,604-token maximum, and rebalanced
$0.0065/$0.0235 condition budgets under the unchanged $0.03 aggregate ceiling.
Its calculated preflight cost is $0.029168. The exact eight-call success
rehearsal and failure-classification rehearsal pass, and preflight is
network-free. See [`validation/r3-v9-preflight.md`](validation/r3-v9-preflight.md).
R3 remained incomplete pending a separately authorized live v9 comparison.

The authorized live v9 attempt then completed all eight calls for $0.01313623
with zero retries/rebases. The 640-token response correction worked: Claude
completed at 577 tokens. Joint still failed the evaluable review contract
because Claude's objection was 178 characters and OpenAI/Kimi had no committed
criterion attestations. Solo was incorrect at 1/4 and Joint at 2/4; no
comparison was emitted. See
[`validation/r3-v9-attempt.md`](validation/r3-v9-attempt.md). R3 remains
incomplete; a v9 retry is not justified.

The completed offline contract/staleness forensic audit proves that
`compatible_merge` at cognitive lags one and two retains complete criterion
attestations. It also reproduces the observed omission when a response includes
evidence but leaves `focus_resolved` false, as prohibited by the staged R3
contract. Future call reports expose that sanitized boolean. The historical
OpenAI/Kimi cause cannot be narrowed further because v9 intentionally retained
neither raw responses nor that field. See
[`validation/r3-v9-offline-contract-forensics.md`](validation/r3-v9-offline-contract-forensics.md).
R3 remains incomplete pending an offline architecture/protocol decision before
any v10 preregistration; this finding does not authorize provider calls.

That architecture decision is now accepted. AMCA retains resolution-gated
model attestations: an unresolved participant can count as checkpoint attendance
but cannot trigger criterion evaluation. A kernel regression fixes that safety
boundary. V10 must use a new versioned contract, explicitly require and audit
`focus_resolved`, and raise only the correction/objection content bound from 160
to 200 characters. See
[`validation/r3-v10-protocol-decision.md`](validation/r3-v10-protocol-decision.md).
R3 remains incomplete pending a separate offline v10 preregistration and exact
scripted rehearsal; no provider call is authorized.

V10 is now separately frozen and rehearsed offline. The new versioned contract
raises only the bounded correction/objection content limit to 200 characters
and explicitly audits every completed review's `focus_resolved` value. The
exact eight-call rehearsal passes; 178 characters passes; 201 characters and an
unresolved review fail closed before Joint; and network-free preflight remains
`$0.029168` under the unchanged `$0.03` ceiling. See
[`validation/r3-v10-preflight.md`](validation/r3-v10-preflight.md). R3 remains
incomplete pending a separately authorized live v10 attempt; this
preregistration authorizes no provider calls.

The authorized live v10 attempt completed all eight provider calls for
`$0.01410687` with zero retries/rebases. Every review resolved and supplied all
attestations, but Joint failed the exact contract because OpenAI authored two
events and Kimi's objection contained 358 characters. Solo remained incorrect
at 1/4 criteria and Joint at 2/4; the report correctly withheld the comparison.
See [`validation/r3-v10-attempt.md`](validation/r3-v10-attempt.md). R3 remains
incomplete pending an offline stopping-rule/methodology review, not another v10
retry or output-shaped contract widening.

The offline stopping-rule review closes the ten-attempt R3 single-task pilot as
negative/inconclusive after `$0.08329423` aggregate accounted provider cost. V7
is the sole exact compliant comparison and found no Joint improvement; v8-v10
exercise correction/objection and failure-handling mechanisms but do not yield
a valid positive comparison. No v11 or further reuse of this task as
confirmatory evidence is authorized. See
[`validation/r3-stopping-rule-review.md`](validation/r3-stopping-rule-review.md).
R3 activity is closed without a superiority claim; the next required stage is
an offline R4 real-model mode-validation evidence map and preregistration.
