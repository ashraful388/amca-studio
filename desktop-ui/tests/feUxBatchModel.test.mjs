// ZCODE-AMCA-STUDIO-V0.8.0-FE-UX-IMPROVEMENT-BATCH-01 tests.
// Covers the approved frontend-only batch: W1 deep links, W2 save-as-team,
// W3 honest export empty state, W4 local format memory, U3 automation
// wording, D1 planned-card explanations, D2 Review Center explanation.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ARTIFACT_CENTER_FILTERS,
  artifactCenterFilterForType,
  artifactCenterFocus,
  EXPORT_EMPTY_STATE,
  LAST_EXPORT_FORMAT_KEY,
  localStorageFormatStore,
  MAX_CHAT_TEAM_MEMBERS,
  validateSaveSelection,
  defaultSavedTeamName,
  AUTOMATION_TRIGGER_EXPLANATION,
  unsupportedTriggerLabel,
  PLANNED_CARD_EXPLANATIONS,
  plannedCardExplanation,
  REVIEW_CENTER_EXPLANATION,
} from "../src/feUxBatchModel.ts";
import { PRODUCTIVITY_FINDINGS, betaUxRecommendations } from "../src/productivityModel.ts";
import { UX_AUDIT_FINDINGS } from "../src/userExperienceModel.ts";

// ---------- W1: recorded-type → filter mapping ----------

test("W1 maps recorded artifact types onto the existing filter tabs only", () => {
  assert.equal(artifactCenterFilterForType("docx"), "document");
  assert.equal(artifactCenterFilterForType("xlsx"), "spreadsheet");
  assert.equal(artifactCenterFilterForType("pptx"), "presentation");
  assert.equal(artifactCenterFilterForType("text"), "text");
  assert.equal(artifactCenterFilterForType("all"), "all");
  // Unrecorded/unfiltered types fall back to "all" — no invented categories.
  assert.equal(artifactCenterFilterForType("markdown"), "all");
  assert.equal(artifactCenterFilterForType("zip"), "all");
  assert.equal(artifactCenterFilterForType("  PDF  "), "all");
  for (const value of Object.values(artifactCenterFocus("p1", "docx"))) {
    assert.ok(value !== undefined);
  }
  const focus = artifactCenterFocus("proj-9", "xlsx");
  assert.deepEqual(focus, { projectId: "proj-9", filter: "spreadsheet", source: "dashboard_recent_outputs" });
  const unknown = artifactCenterFocus(null, "weird_type");
  assert.equal(unknown.projectId, null);
  assert.equal(unknown.filter, "all");
  assert.ok(ARTIFACT_CENTER_FILTERS.includes(unknown.filter));
});

// ---------- W2: save-selection validation uses the existing contract ----------

test("W2 validates the chat selection against the existing team contract", () => {
  assert.equal(validateSaveSelection({ name: "", modelRefs: ["ollama:q"] }).ok, false);
  assert.ok(validateSaveSelection({ name: "", modelRefs: ["ollama:q"] }).errors.some((e) => e.includes("name")));
  assert.equal(validateSaveSelection({ name: "T", modelRefs: [] }).ok, false);
  assert.ok(validateSaveSelection({ name: "T", modelRefs: [] }).errors.some((e) => e.includes("member")));
  const tooMany = validateSaveSelection({
    name: "T",
    modelRefs: Array.from({ length: MAX_CHAT_TEAM_MEMBERS + 1 }, (_, i) => `m${i}`),
  });
  assert.equal(tooMany.ok, false);
  assert.equal(validateSaveSelection({ name: "My team", modelRefs: ["ollama:q", "prov:m"] }).ok, true);
  assert.equal(defaultSavedTeamName(["ollama:q"]), "Chat selection · single model");
  assert.equal(defaultSavedTeamName(["a", "b", "c"]), "Chat selection · 3 models");
});

// ---------- W3: honest export empty state ----------

test("W3 empty state states the recorded next step without promising exports", () => {
  assert.equal(EXPORT_EMPTY_STATE.title, "No exports yet.");
  assert.equal(EXPORT_EMPTY_STATE.detail, "Create a task to generate artifacts.");
  assert.equal(EXPORT_EMPTY_STATE.actionLabel, "Open Projects");
});

// ---------- W4: per-project format memory is local-only ----------

test("W4 remembers the last export format per project in a local store only", () => {
  const backing = new Map();
  const storage = {
    getItem: (key) => backing.get(key) ?? null,
    setItem: (key, value) => void backing.set(key, value),
  };
  const store = localStorageFormatStore(storage);
  assert.equal(store.read("p1"), null);
  store.write("p1", "pdf");
  store.write("p2", "docx");
  assert.equal(store.read("p1"), "pdf");
  assert.equal(store.read("p2"), "docx");
  store.write("p1", "xlsx");
  assert.equal(store.read("p1"), "xlsx");
  assert.equal(backing.get(LAST_EXPORT_FORMAT_KEY), JSON.stringify({ p1: "xlsx", p2: "docx" }));
  // Blank inputs are ignored, not stored.
  store.write("", "pdf");
  store.write("p3", "  ");
  assert.equal(store.read("p3"), null);
  // Corrupt storage reads as empty instead of throwing.
  const broken = localStorageFormatStore({
    getItem: () => "{not json",
    setItem: () => {},
  });
  assert.equal(broken.read("p1"), null);
  // A failing write must not throw (preference loss is not an export failure).
  const readonly = localStorageFormatStore({ getItem: () => null, setItem: () => { throw new Error("full"); } });
  assert.doesNotThrow(() => readonly.write("p1", "pdf"));
});

// ---------- U3: automation trigger honesty ----------

test("U3 replaces the dead label with a manual-execution explanation", () => {
  assert.equal(unsupportedTriggerLabel(), "Manual only · trigger unsupported");
  assert.ok(AUTOMATION_TRIGGER_EXPLANATION.includes("manual execution"));
  assert.ok(AUTOMATION_TRIGGER_EXPLANATION.includes("scheduler"));
  assert.ok(!AUTOMATION_TRIGGER_EXPLANATION.toLowerCase().includes("coming soon"));
  assert.ok(!/\b\d{4}\b/.test(AUTOMATION_TRIGGER_EXPLANATION));
});

// ---------- D1: planned cards carry purpose + dependency, no promises ----------

test("D1 planned-card explanations state purpose, availability, dependency", () => {
  assert.equal(PLANNED_CARD_EXPLANATIONS.length, 2);
  for (const card of PLANNED_CARD_EXPLANATIONS) {
    assert.ok(card.purpose.length > 10, card.cardId);
    assert.ok(card.whyUnavailable.includes("Not implemented yet"), card.cardId);
    assert.ok(card.expectedDependency.includes("Depends on"), card.cardId);
    const text = `${card.purpose} ${card.whyUnavailable} ${card.expectedDependency}`.toLowerCase();
    assert.ok(!text.includes("soon"), card.cardId);
    assert.ok(!/\bq[1-4]\b/.test(text), card.cardId);
    assert.ok(!/\b\d{4}\b/.test(text), card.cardId);
  }
  assert.equal(plannedCardExplanation("model_matchmaker")?.cardId, "model_matchmaker");
  assert.equal(plannedCardExplanation("context_explorer")?.cardId, "context_explorer");
  assert.equal(plannedCardExplanation("nonexistent"), null);
});

// ---------- D2: Review Center explanation is honest ----------

test("D2 Review Center explanation names its evidence source and no AI judgment", () => {
  assert.ok(REVIEW_CENTER_EXPLANATION.includes("What this panel shows"));
  assert.ok(REVIEW_CENTER_EXPLANATION.includes("recorded"));
  assert.ok(REVIEW_CENTER_EXPLANATION.includes("No hidden AI judgment"));
});

// ---------- Roadmap status reconciliation ----------

test("delivered roadmap items are recorded as shipped/addressed with traceable wording", () => {
  const byId = (id) => PRODUCTIVITY_FINDINGS.find((f) => f.id === id);
  assert.equal(byId("manual_switch_after_run")?.status, "shipped");
  assert.ok(byId("manual_switch_after_run")?.improvement.includes("W1"));
  assert.equal(byId("team_rebuild")?.status, "shipped");
  assert.ok(byId("team_rebuild")?.improvement.includes("W2"));
  assert.equal(byId("repeat_export")?.status, "shipped");
  assert.ok(byId("repeat_export")?.improvement.includes("W4"));
  // approval_context stays recommended — it is BE-dependent, not in this batch.
  assert.equal(byId("approval_context")?.status, "recommended");
  assert.ok(betaUxRecommendations().some((f) => f.id === "approval_context"));

  const uxBy = (screen) => UX_AUDIT_FINDINGS.find((f) => f.screen === screen);
  assert.equal(uxBy("Artifact Center")?.status, "addressed");
  assert.ok(uxBy("Artifact Center")?.improvement.includes("W3"));
  assert.equal(uxBy("Automation Center")?.status, "addressed");
  assert.ok(uxBy("Automation Center")?.improvement.includes("U3"));
  assert.equal(uxBy("Review Center")?.status, "addressed");
  assert.ok(uxBy("Review Center")?.improvement.includes("D2"));
  assert.equal(uxBy("Model Hub")?.status, "addressed");
});
