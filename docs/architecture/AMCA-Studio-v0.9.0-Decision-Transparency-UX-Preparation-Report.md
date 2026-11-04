# AMCA Studio v0.9.0 — Decision Transparency UX Preparation Report

**Task:** ZCODE-AMCA-STUDIO-V0.9.0-DECISION-TRANSPARENCY-UX-PREPARATION-01
**Date:** 2026-09-03
**Owner:** ZCode (Frontend Product Lead)
**Baseline:** `b5f57766` on `zcode/amca-studio-z1-shell-completion-01`
**Scope:** UX preparation for future decision evidence records. Preparation only — no adaptive decision engine, no decision execution, no backend changes.

---

## 1. What was delivered

1. **Future decision transparency panel design** (`decisionTransparencyUxPreparationModel.ts`) — five planned sections (Decision status, Evidence references, Selected resources, Approval references, Override availability), each with its planned contract fields (from the accepted evidence registry design) and the honest current rendering: "Decision records unavailable. No adaptive decision exists in this build."
2. **Evidence provenance viewer** — read-only rows built from recorded evidence references: evidence reference ID, source category, timestamp, linked workflow, linked artifacts. Gaps stay null — never invented. Viewer note: "no reasoning, no AI-generated explanations, no confidence scores, no rankings."
3. **Human control visibility** — four control placeholders (Approve / Modify / Override / Reject), all `implemented: false` with the reason "Designed control only. No decision engine is active." — rendered as disabled buttons in the surface.
4. **Integration location** — the preparation panel renders inside the **Review Center** (existing surface), before the workflow review groups. No new navigation is created: "Panels integrate into existing surfaces only."
5. **Wording consistency** — the preparation model duplicates the accepted decision-trace boundary verbatim ("Decision transparency design only — no adaptive decision exists in this build.") and the test cross-checks the two constants are identical.

## 2. Constraints honored

Desktop-UI-only diff: `decisionTransparencyUxPreparationModel.ts` (pure, dependency-free model), Review Center rendering (`ReviewCenter.tsx`), CSS. No decision engine, Model Router, Agent Fabric, Adaptive Coordination, Execution Planning, backend services, or telemetry files touched. The control placeholders are disabled buttons — no actions are implemented.

## 3. Tests

- New `tests/decisionTransparencyUxPreparationModel.test.mjs` (6 tests, registered in `test:frontend` — 46 files): absent-record handling (all five sections render the mandated unavailable wording), evidence-reference rendering with honest null gaps, no hidden-reasoning/confidence/ranking fields anywhere in the panel or controls, all four control placeholders designed-only and inactive, integration on existing surfaces only, and the boundary cross-check with the accepted design model.
- **Full frontend suite: 563 tests · 563 pass · 0 fail · 0 skipped** (was 557; +6 new). `tsc -b` clean; `vite build` ✓ (1.71 s, pre-existing advisory only).
- Backend regression: not re-run — zero backend files changed; the 1808-passed/11-skipped baseline stands.

## 4. Conclusion

The UI surfaces are now prepared for future decision evidence records: the panel structure exists, the provenance viewer contract is defined, the control placeholders state their own inactivity, and everything renders the honest "no adaptive decision exists in this build" wording until backend records arrive. When backend persistence is ordered, the panel binds to the records without structural changes.
