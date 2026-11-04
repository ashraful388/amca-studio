// Z2 composerModel — unit tests for task types, payload building,
// validation, execution timeline, and artifact grouping (pure model, no
// browser, no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  COMPOSER_MODES,
  TASK_TYPES,
  TERMINAL_RUN_STATES,
  buildComposerPayload,
  executionTimeline,
  goalWithTag,
  groupArtifacts,
  taskTypeById,
  toggleComposerSelection,
} from "../src/composerModel.ts";

const DRAFT = (over = {}) => ({
  goal: "Summarize the quarterly report",
  taskTypeId: "research",
  mode: "solo",
  selectionPolicy: "auto_intelligence",
  modelRefs: [],
  workspaceProjectId: null,
  ...over,
});

test("Auto model click selects only the clicked cloud model despite a suspended team", () => {
  const ref = "provider.test:deepseek-v4.1-flash";
  for (const current of [[], [ref], [ref, "provider.test:glm-5.3-flash"]]) {
    const original = [...current];
    assert.deepEqual(toggleComposerSelection(current, ref, "auto"), [ref]);
    assert.deepEqual(current, original);
  }
});

test("Manual model clicks retain add/remove behavior", () => {
  assert.deepEqual(toggleComposerSelection(["cloud:a"], "cloud:b", "manual"), ["cloud:a", "cloud:b"]);
  assert.deepEqual(toggleComposerSelection(["cloud:a", "cloud:b"], "cloud:a", "manual"), ["cloud:b"]);
});

// ---------- task types & goal tag ----------

test("TASK_TYPES covers the six declared task types with labels and hints", () => {
  const ids = TASK_TYPES.map((type) => type.id).sort();
  assert.deepEqual(ids, [
    "agent_collaboration",
    "analysis",
    "coding",
    "document_creation",
    "general_reasoning",
    "research",
  ]);
  for (const type of TASK_TYPES) {
    assert.ok(type.label.length > 0, `label for ${type.id}`);
    assert.ok(type.hint.length > 0, `hint for ${type.id}`);
  }
});

test("taskTypeById resolves known ids and rejects unknown ones", () => {
  assert.equal(taskTypeById("coding")?.id, "coding");
  assert.equal(taskTypeById("nonexistent"), null);
  assert.equal(taskTypeById(""), null);
});

test("goalWithTag prefixes the type label for a known task type", () => {
  assert.equal(goalWithTag("Draft the outline", "document_creation"), "[Task type: Document Creation] Draft the outline");
});

test("goalWithTag returns trimmed text unchanged for unknown type", () => {
  assert.equal(goalWithTag("  Just do it  ", "nope"), "Just do it");
});

// ---------- payload building & validation ----------

test("buildComposerPayload auto strategy maps to empty participants and auto policy", () => {
  const decision = buildComposerPayload(DRAFT());
  assert.equal(decision.ok, true);
  if (decision.ok) {
    assert.deepEqual(decision.payload.participant_ids, []);
    assert.equal(decision.payload.selection_policy, "auto_intelligence");
    assert.equal(decision.payload.workspace_project_id, null);
  }
});

test("buildComposerPayload carries the task-type tag into the goal", () => {
  const decision = buildComposerPayload(DRAFT({ taskTypeId: "coding" }));
  assert.equal(decision.ok, true);
  if (decision.ok) assert.match(decision.payload.goal, /^\[Task type: Coding\] Summarize the quarterly report$/);
});

test("buildComposerPayload manual single ref maps to solo with the ref", () => {
  const decision = buildComposerPayload(DRAFT({ selectionPolicy: "manual_model_set", modelRefs: ["ollama:qwen3:1.7b"] }));
  assert.equal(decision.ok, true);
  if (decision.ok) {
    assert.deepEqual(decision.payload.participant_ids, ["ollama:qwen3:1.7b"]);
    assert.equal(decision.payload.mode, "solo");
    assert.equal(decision.payload.selection_policy, "manual_model_set");
  }
});

test("buildComposerPayload manual multi-ref honors the explicitly chosen mode", () => {
  const decision = buildComposerPayload(DRAFT({
    selectionPolicy: "manual_model_set",
    modelRefs: ["ollama:a", "ollama:b"],
    mode: "independent_discuss",
  }));
  assert.equal(decision.ok, true);
  if (decision.ok) {
    assert.deepEqual(decision.payload.participant_ids, ["ollama:a", "ollama:b"]);
    assert.equal(decision.payload.mode, "independent_discuss");
  }
});

test("buildComposerPayload keeps the draft's selected mode verbatim", () => {
  const decision = buildComposerPayload(DRAFT({ selectionPolicy: "manual_model_set", modelRefs: ["ollama:a", "ollama:b"], mode: "solo" }));
  assert.equal(decision.ok, true);
  if (decision.ok) {
    assert.equal(decision.payload.mode, "solo");
    assert.equal(decision.payload.conversation_mode, "solo");
  }
});

test("buildComposerPayload rejects an empty goal", () => {
  const decision = buildComposerPayload(DRAFT({ goal: "   " }));
  assert.equal(decision.ok, false);
  if (!decision.ok) assert.ok(decision.errors.some((message) => message.includes("goal")));
});

test("buildComposerPayload rejects an invalid task type", () => {
  const decision = buildComposerPayload(DRAFT({ taskTypeId: "welding" }));
  assert.equal(decision.ok, false);
  if (!decision.ok) assert.ok(decision.errors.some((message) => message.includes("task type")));
});

test("buildComposerPayload rejects an invalid collaboration mode", () => {
  const decision = buildComposerPayload(DRAFT({ mode: "telepathy" }));
  assert.equal(decision.ok, false);
  if (!decision.ok) assert.ok(decision.errors.some((message) => message.includes("mode")));
});

test("buildComposerPayload rejects manual strategy with zero model refs", () => {
  const decision = buildComposerPayload(DRAFT({ selectionPolicy: "manual_model_set", modelRefs: [] }));
  assert.equal(decision.ok, false);
  if (!decision.ok) assert.ok(decision.errors.some((message) => message.includes("Manual model strategy")));
});

test("buildComposerPayload manual multi-ref mode always comes from COMPOSER_MODES", () => {
  const decision = buildComposerPayload(DRAFT({ selectionPolicy: "manual_model_set", modelRefs: ["ollama:a", "ollama:b"] }));
  assert.equal(decision.ok, true);
  if (decision.ok) assert.equal(COMPOSER_MODES.some((mode) => mode.id === decision.payload.mode), true);
});

test("buildComposerPayload keeps a chosen workspace project id", () => {
  const decision = buildComposerPayload(DRAFT({ workspaceProjectId: "proj-9" }));
  assert.equal(decision.ok, true);
  if (decision.ok) assert.equal(decision.payload.workspace_project_id, "proj-9");
});

test("buildComposerPayload blank workspace id normalizes to null", () => {
  const decision = buildComposerPayload(DRAFT({ workspaceProjectId: "  " }));
  assert.equal(decision.ok, true);
  if (decision.ok) assert.equal(decision.payload.workspace_project_id, null);
});

// ---------- execution timeline ----------

test("executionTimeline queued run: Queued done, Running pending", () => {
  const steps = executionTimeline({ status: "queued" });
  assert.equal(steps.length, 2);
  assert.deepEqual(steps.map((step) => step.phase), ["done", "pending"]);
  assert.deepEqual(steps.map((step) => step.label), ["Queued", "Running"]);
});

test("executionTimeline running run: Running is active", () => {
  const steps = executionTimeline({ status: "running" });
  assert.deepEqual(steps.map((step) => step.phase), ["done", "active"]);
});

test("executionTimeline cancelling run keeps Running active until terminal", () => {
  const steps = executionTimeline({ status: "cancelling" });
  assert.deepEqual(steps.map((step) => step.phase), ["done", "active"]);
});

test("executionTimeline succeeded run appends a Completed done step", () => {
  const steps = executionTimeline({ status: "succeeded" });
  assert.equal(steps.length, 3);
  assert.equal(steps[2].label, "Completed");
  assert.equal(steps[2].phase, "done");
});

test("executionTimeline partial run reads as Completed (done phase)", () => {
  const steps = executionTimeline({ status: "partial" });
  assert.equal(steps[2].label, "Completed");
  assert.equal(steps[2].phase, "done");
});

test("executionTimeline failed run appends a Failed failed step", () => {
  const steps = executionTimeline({ status: "failed" });
  assert.equal(steps[2].label, "Failed");
  assert.equal(steps[2].phase, "failed");
});

test("executionTimeline cancelled run appends a Cancelled cancelled step", () => {
  const steps = executionTimeline({ status: "cancelled" });
  assert.equal(steps[2].label, "Cancelled");
  assert.equal(steps[2].phase, "cancelled");
});

test("TERMINAL_RUN_STATES enumerates exactly the registry terminal states", () => {
  assert.deepEqual([...TERMINAL_RUN_STATES], ["succeeded", "partial", "failed", "cancelled"]);
});

// ---------- artifact grouping ----------

test("groupArtifacts groups by existing type with friendly labels", () => {
  const groups = groupArtifacts([
    { artifact_id: "a1", artifact_type: "markdown", title: "Notes", current_version: 2 },
    { artifact_id: "a2", artifact_type: "pdf", title: "Report", current_version: 1 },
    { artifact_id: "a3", artifact_type: "markdown", title: "Minutes", current_version: 1 },
  ]);
  assert.deepEqual(groups.map((group) => group.label), ["Reports & notes", "PDF outputs"]);
  const markdown = groups.find((group) => group.type === "markdown");
  assert.equal(markdown.items.length, 2);
  assert.deepEqual(markdown.items.map((item) => item.artifact_id), ["a1", "a3"]);
});

test("groupArtifacts sorts groups alphabetically and keeps unknown types visible", () => {
  const groups = groupArtifacts([
    { artifact_id: "z", artifact_type: "zip", title: "Bundle", current_version: 3 },
    { artifact_id: "x", artifact_type: "weird_new_type", title: "Mystery", current_version: 1 },
    { artifact_id: "a", artifact_type: "docx", title: "Doc", current_version: 1 },
  ]);
  assert.deepEqual(groups.map((group) => group.type), ["docx", "weird_new_type", "zip"]);
  assert.equal(groups.find((group) => group.type === "docx").label, "Documents");
  assert.equal(groups.find((group) => group.type === "weird_new_type").label, "weird_new_type");
});

test("groupArtifacts returns an empty array for no artifacts", () => {
  assert.deepEqual(groupArtifacts([]), []);
});

test("groupArtifacts preserves item order inside each group", () => {
  const groups = groupArtifacts([
    { artifact_id: "first", artifact_type: "image", title: "One", current_version: 1 },
    { artifact_id: "second", artifact_type: "image", title: "Two", current_version: 2 },
  ]);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].items.map((item) => item.artifact_id), ["first", "second"]);
});
