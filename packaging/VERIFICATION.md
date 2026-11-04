# Windows packaging verification record

Verification date: 2026-08-23 (Asia/Dhaka)

Release: **AMCA 0.2.2**

Release inputs were built from the AMCA workspace with Python 3.13.5,
PyInstaller 6.22.0, pypdf 6.16.0, Rust 1.97.1, Tauri CLI 2.11.4,
and Vite 8.2.1.

## Passed gates (0.2.2)

- Full Python suite before packaging: 356 tests passing, 1 skipped
  (`python -m unittest discover -s tests`).
- Desktop protocol handshake: version **15** is required by the Python
  service, Tauri launcher, and React client; an older process on port 8765 is
  rejected.
- C1 desktop collaboration routing included: protocol-15 contract
  (`amca/collaboration.py`) represents all five architectures; Auto
  Intelligence selects model set AND architecture deterministically from task
  shape plus canonical-state signals (never participant count); Solo, Joint,
  Independent→Discuss, Adaptive Hybrid, and Parallel Decomposition all execute
  through the existing kernel paths.
- C1.6 deterministic decomposition planner included: sequenced/domain/single
  lexical slicing with mandatory integration unit, round-robin assignees,
  kernel-validated DAG levels, and deterministic integration report;
  registry-level test executes a real parallel plan end-to-end.
- Conversation store migrated in place from binary mode CHECK to five
  architecture modes via create→copy→drop→rename rebuild; FK integrity,
  index survival, row preservation, and idempotency regression-tested.
- PyInstaller one-file backend build: passed.
- Packaged-backend smoke suite (`verify_backend.ps1`): health/session
  exchange against the copied `0.2.2` backend negotiated protocol 15; the
  offline demo collaboration finished with a terminal `partial` result and
  persisted canonical history: passed.
- `cargo check --locked`: passed (Cargo.lock regenerated for the 0.2.2
  workspace version).
- Optimized Tauri application build with the protocol-15 launcher
  (`BACKEND_PROTOCOL_VERSION = 15`): passed; launcher unit test rejects
  protocol 14 and older health responses.
- NSIS x64 installer build: passed.
- MSI x64 build: passed.

## Current unsigned artifacts (0.2.2, rev B)

Revision B (2026-08-23, post-tag) adds two reviewed changes that were live-
tested against real local Ollama models and then rebuilt into the packaged
backend: (1) approved tolerant semantic extraction — unknown/non-authorable
event types are dropped with a recorded audit event instead of failing the
whole response; malformed content is never integrated and never satisfies any
gate; (2) `collaboration_decision` is carried on every run job so the desktop
"Why AMCA chose this" card has its data. The NSIS/MSI installers from the
tagged build remain valid for the UI shell; the portable backend below
supersedes the tagged one.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `dist/AMCA-0.2.2/amca-backend.exe` (rev B) | 21,486,636 | `ca343eff0de492992677ef0b2a01e470f8ecc8dc652d853171c10e75ef75249a` |
| `target/release/amca-desktop.exe` | 9,685,504 | `a62f5adfa6e2b158926eba32e2ce15d0b242cd08715db5208fdb60eb319945da` |
| `bundle/nsis/AMCA_0.2.2_x64-setup.exe` | 24,087,241 | `f54768a93c0273af15a999249dab0ae154bb5bfa36c6d40ce5088edd3d8e9fbe` |
| `bundle/msi/AMCA_0.2.2_x64_en-US.msi` | 25,051,136 | `44086fe1a83acae18a42faacdd025962bb45ece3a04bc06dd0cfa491b8af2a69` |

Live self-test evidence on this machine (released binary, protocol 15,
local Ollama 0.32.9): Auto Intelligence solo task answered end-to-end;
Parallel Decomposition executed a real two-model, three-call DAG with the
planned topological levels and an honestly failed integration gate surfaced
as `partial`; the hardware-fit gate verifiably blocked an over-limit 8B model
under memory pressure.

These hashes describe this exact local build and will change after rebuilding.
The installers remain deliberately unsigned because no project signing
certificate was supplied; Windows may display an unknown-publisher warning.
Clean-machine install/upgrade/uninstall acceptance is still an open release
gate and has not been claimed.

The prior 0.2.1 verification record follows unchanged for provenance.

---

# Previous record: AMCA 0.2.1 (2026-08-22)

## Passed gates

- PyInstaller analysis and one-file, windowless backend build: passed.
- Standalone backend loopback health/session exchange: passed.
- Bundled benchmark fixture load: 8 tasks.
- Standalone backend offline collaboration: completed with `partial` status,
  a non-null result, one project, and 12 persisted canonical events.
- Isolated SQLite database creation: passed.
- Standalone PDF ingestion through `/api/assets`: `application/pdf`, `page:1`.
- Explicit lazy-import collection of the required `pypdf` and `anthropic`
  runtimes: passed.
- Authorized `/api/approvals` queue availability: passed.
- Protected credential-vault status: four model-scoped entries, no
  secret-bearing fields returned.
- Custom OpenAI-compatible profile create/delete smoke: metadata persisted,
  write-only credential was not returned, and no network request started.
- Desktop protocol handshake: version 14 is required by the Python service,
  Tauri launcher, and React client; an older process on port 8765 is rejected.
- PyInstaller bootloader and worker process-tree shutdown: passed.
- React/TypeScript/Vite production build: passed.
- Chat exposes only Auto Intelligence / Manual model selection. Auto exposes no
  model picker and resolves the eligible participant set from the task;
  Manual accepts exact logical models or saved Model Teams while collaboration
  architecture remains internal: passed.
- The unified Settings → Model Hub exposes AMCA API status, provider-level BYOK
  with authenticated catalog discovery/import/edit and dynamic execution,
  existing direct profiles, hardware-aware recommended/installed local models,
  Model Teams, and dormant MAIA metadata from one backend state: passed.
- Provider credentials remain write-only in Windows protection; Model Hub
  SQLite/API records contain sanitized metadata and opaque references only:
  passed.
- Deterministic Cost-Quality Gate, local compatibility evaluation, Model Team
  persistence, task-cost finalization, and MAIA/Nano role contracts passed.
- The current Agent Fabric milestone is represented by 41 permanent core roles,
  more than 150 built-in cross-domain specialists, locally persisted custom
  agents, deterministic Economy/Governor decisions, resource envelopes,
  bounded lifecycle planning, live project/model-context integration, and a
  first-class Agent Library/control UI. The general dynamic task-graph executor
  remains an open master-specification gate.
- Unified AMCA Studio navigation exposes Projects, Agents, Automations,
  Plugins, and the canonical Model Hub without duplicating model state.
- Studio Projects are durable workspace containers, separate from governed
  execution ProjectState: one Project can contain multiple chats, shared files,
  shared instructions, and a template. Existing chats remain unassigned until
  explicitly moved. Project instructions and shared files enter the bounded
  model context for chats in that Project. Service tests and packaged Windows
  accessibility/visual inspection passed.
- Unified Workbench exposes canonical Files, Changes, run output, Tests, and
  permission-bounded Git state. Review Queue combines exact-scope approvals
  with completed, partial, and failed task outcomes: passed in production build
  and live Windows accessibility testing.
- Durable local automation definitions support Once, Recurring, Conditional,
  Monitor, and Event-triggered categories, remain disabled by default, and can
  dispatch through Auto Intelligence and the existing approval boundary. The
  automatic trigger evaluator remains an explicit open gate.
- Full Python regression suite: 300/300 passed using the bundled dependency
  runtime. Focused Model Hub, Agent Fabric, and desktop service suite: 43/43.
- Durable desktop conversations: create/list/search/rename/delete, saved
  messages and per-chat model preferences, terminal-run release, and restart
  recovery passed in unit, service, and packaged-backend smoke tests.
- Chat composer picker and direct drop target: up to 64 files from individual
  files, folders, or repositories and 25 MB combined;
  pending-file chips/removal, immutable native storage, modality/extraction
  provenance, non-executing code/config/structured-text ingestion, saved
  message filenames, bounded local model context, and exact
  cloud excerpt approval passed Python service tests and the React build.
- The canonical asset store uses the bounded TXT/Markdown/PDF/DOCX/XLSX/PPTX/
  HTML/image/audio/video extractor. Optional local processors were also
  validated end-to-end against generated media: Tesseract returned `AMCA OCR
  VERIFIED 42`, and whisper.cpp returned one timestamped 3.36-second speech
  transcript. Missing/failed processors remain metadata-only and are never
  labeled as understood content.
- The local-media setup pinned whisper.cpp `b4938` archive SHA-256
  `C2A4B60EDB11F7E11A9191FFB50929535527D4D91C9903DBE3E554583BBBC63D`
  and `ggml-tiny.en.bin` SHA-256
  `921E4CF8686FDD993DCD081A5DA5B6C365BFDE1162E72B08D75AC75289920B1F`.
  These optional third-party runtimes are stored in AMCA app data, not bundled
  into the installer and not downloaded by a chat run.
- Credential-shaped Chat input is rejected before any project or conversation
  SQLite write; raw database canary checks passed.
- Local-only Joint Chat admission and execution bridge: passed.
- Configured built-in or custom API models appear in Solo and Joint selection:
  passed.
- Add Model supports OpenAI, Anthropic/Claude, Kimi, fixed-price Tabitoken, and
  generic OpenAI-compatible HTTPS profiles with token, request, or declared
  free pricing: passed.
- Cloud Chat planning is network-free, creates exact-scope approvals bounded to
  the selected participant and collaboration-round count, displays the maximum
  fixed cost including the second Joint review round, and rejects missing saved
  credentials: passed.
- Joint desktop Chat now uses a bounded two-round contribution then
  critique/correction/revision protocol; Solo remains one round. Auto's task
  class, participant target, selected models, rationale, and rounds are persisted
  and visible: passed.
- Assistant messages render safe Markdown/GFM, tables, links, code blocks, and
  inline/display LaTeX through KaTeX; raw HTML remains disabled: production build passed.
- `cargo check --locked --offline`: passed.
- Optimized Tauri application build: passed.
- NSIS x64 installer build: passed.
- Generated NSIS manifest includes `amca-backend.exe` at the application
  resource root.
- R1.5 source service discovered `qwen3:1.7b`, completed one real local Solo
  call with zero API calls/cost, persisted eight events, and restored the run
  after restart.
- The final packaged backend passed health/session exchange and excluded the
  cloud-backed Ollama catalog stub from Offline model inventory.
- The bounded OpenAI R2 cancellation correction is included: the final live
  probe cancelled in 0.002 seconds, reconciled its conservative reservation,
  and left no plaintext credential or live lease.
- The final copied portable `0.2.1` backend negotiated protocol 14 and passed
  the isolated packaged-backend smoke suite. The React/Vite build, Cargo check,
  optimized Tauri build, NSIS bundle, and MSI bundle all passed.

The collaboration run may honestly finish as `partial` while stricter evaluator
gates remain unmet; the packaging gate requires a terminal result and persisted
canonical history, not a false success label.

## Current unsigned artifacts

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `dist/AMCA-0.2.1/amca-backend.exe` | 21,466,217 | `C16C6022EAB93ED7978E01E8AEAD2A415D6AE2C60B6FCCA0BED7B0D491CA2779` |
| `dist/AMCA-0.2.1/amca-desktop.exe` | 9,685,504 | `A6D8981ED957B7D477929A73F3C524A727F6F13D4B2958F45C3CE762B6D6102A` |
| `dist/AMCA-0.2.1-setup.exe` | 24,067,503 | `73A88FCD01A2D52A0EA8A11C4CED2C18E9D8DA412D92FA2EA0480853A2952829` |
| `dist/AMCA-0.2.1-x64.msi` | 25,034,752 | `81E39BE74826A0E53E8A18C028CBDD4ECB6108E663E91A6AFC976BB091B825F7` |

These hashes describe this exact local build and will change after rebuilding.
The installer is deliberately unsigned because no project signing certificate
was supplied; Windows may therefore display an unknown-publisher warning.
The NSIS/MSI files were built and their bundled backend was smoke-tested, but
this record does not claim a clean-machine install/upgrade/uninstall test.
