import { test } from "node:test";
import assert from "node:assert/strict";
import {
  humanizeLabel,
  onboardingSteps,
  firstRunExperience,
  settingsReadiness,
  SCREENSHOT_MANIFEST,
  screenshotTargetById,
} from "../src/releasePolishModel.ts";

// ---------- Shared humanizer ----------

test("humanizeLabel title-cases snake_case values", () => {
  assert.equal(humanizeLabel("adaptive_hybrid"), "Adaptive Hybrid");
  assert.equal(humanizeLabel("independent_discuss"), "Independent Discuss");
});

test("humanizeLabel keeps small words lowercase after the first word", () => {
  assert.equal(humanizeLabel("cost_of_the_run"), "Cost of the Run");
  assert.equal(humanizeLabel("time_to_live"), "Time to Live");
});

test("humanizeLabel passes through empty and plain values", () => {
  assert.equal(humanizeLabel(""), "");
  assert.equal(humanizeLabel("Models"), "Models");
});

// ---------- Onboarding steps ----------

const FRESH = {
  hasProjects: false,
  hasConversations: false,
  hasSelectableModels: false,
  hasRuns: false,
  hasArtifacts: false,
};

const COMPLETE = {
  hasProjects: true,
  hasConversations: true,
  hasSelectableModels: true,
  hasRuns: true,
  hasArtifacts: true,
};

test("onboarding steps follow the manager's five-step sequence", () => {
  assert.deepEqual(
    onboardingSteps(FRESH).map((step) => step.key),
    ["project", "models", "chat", "task", "artifact"],
  );
});

test("every onboarding step targets an existing view", () => {
  const views = new Set(["dashboard", "overview", "settings", "chat", "artifacts", "agents"]);
  for (const step of onboardingSteps(FRESH)) {
    assert.ok(views.has(step.view), step.key);
    assert.ok(step.label.length > 0);
    assert.ok(step.detail.length > 0);
  }
});

test("fresh state marks all steps pending; complete state marks all done", () => {
  assert.ok(onboardingSteps(FRESH).every((step) => !step.done));
  assert.ok(onboardingSteps(COMPLETE).every((step) => step.done));
});

test("partial state marks exactly the finished steps done", () => {
  const state = { ...FRESH, hasProjects: true, hasSelectableModels: true };
  const done = onboardingSteps(state).filter((step) => step.done).map((step) => step.key);
  assert.deepEqual(done, ["project", "models"]);
});

// ---------- First-run experience ----------

test("brand-new install gates the welcome headline", () => {
  const experience = firstRunExperience(FRESH);
  assert.equal(experience.isFirstRun, true);
  assert.equal(experience.headline, "Welcome to AMCA Studio");
  assert.match(experience.detail, /stays on this device/);
});

test("complete state announces full-loop usage", () => {
  const experience = firstRunExperience(COMPLETE);
  assert.equal(experience.isFirstRun, false);
  assert.equal(experience.headline, "Setup complete");
});

test("partial state names the next step", () => {
  const experience = firstRunExperience({ ...FRESH, hasProjects: true, hasConversations: true });
  assert.equal(experience.headline, "Getting started — 2 of 5 done");
  assert.match(experience.detail, /Next: Enable a model/);
});

// ---------- Settings readiness ----------

test("settings readiness checks models, providers, local, teams, pricing", () => {
  const items = settingsReadiness({
    selectableModels: 3,
    connectedProviders: 1,
    localRuntimeReachable: true,
    savedTeams: 2,
    verifiedPrices: 5,
  });
  assert.deepEqual(
    items.map((item) => item.key),
    ["models", "providers", "local", "teams", "pricing"],
  );
  assert.ok(items.every((item) => item.done));
  assert.match(items[0].detail, /3 models selectable/);
});

test("empty settings surface honest not-yet details", () => {
  const items = settingsReadiness({
    selectableModels: 0,
    connectedProviders: 0,
    localRuntimeReachable: false,
    savedTeams: 0,
    verifiedPrices: 0,
  });
  assert.ok(items.every((item) => !item.done));
  assert.match(items[0].detail, /Connect a provider or enable local models/);
  assert.match(items[2].detail, /Ollama not reachable/);
  assert.match(items[4].detail, /unknown until prices are verified/);
});

test("teams and pricing are optional-flavoured, not failures", () => {
  const items = settingsReadiness({
    selectableModels: 1,
    connectedProviders: 0,
    localRuntimeReachable: true,
    savedTeams: 0,
    verifiedPrices: 0,
  });
  const teams = items.find((item) => item.key === "teams");
  assert.equal(teams?.done, false);
  assert.match(teams?.detail ?? "", /Optional:/);
  const models = items.find((item) => item.key === "models");
  assert.equal(models?.done, true);
});

test("singular model count is grammatical in readiness detail", () => {
  const items = settingsReadiness({
    selectableModels: 1,
    connectedProviders: 0,
    localRuntimeReachable: true,
    savedTeams: 1,
    verifiedPrices: 1,
  });
  assert.match(items[0].detail, /1 model selectable/);
  assert.match(items[3].detail, /1 saved combination/);
  assert.match(items[4].detail, /1 verified record/);
});

// ---------- Screenshot manifest ----------

test("screenshot manifest covers the eight release surfaces", () => {
  const ids = SCREENSHOT_MANIFEST.map((target) => target.id);
  assert.deepEqual(ids, [
    "dashboard", "projects", "model_hub", "selection_cost",
    "monitor", "insights", "artifacts", "review",
  ]);
});

test("every screenshot target has title, focus, and a real view", () => {
  const views = new Set(["dashboard", "overview", "settings", "agents", "artifacts", "review", "chat"]);
  for (const target of SCREENSHOT_MANIFEST) {
    assert.ok(target.title.length > 0, target.id);
    assert.ok(target.focus.length > 0, target.id);
    assert.ok(views.has(target.view), target.id);
  }
});

test("screenshot lookup finds known ids and rejects unknown", () => {
  assert.equal(screenshotTargetById("monitor")?.title, "Agent Control Panel");
  assert.equal(screenshotTargetById("nonexistent"), null);
});

test("manifest never promises staged content — all live-data focus", () => {
  for (const target of SCREENSHOT_MANIFEST) {
    assert.ok(!/staged|mock|fake/i.test(target.focus), target.id);
  }
});

// ---------- Additional edge coverage ----------

test("humanizeLabel handles multiple spaces and mixed separators", () => {
  assert.equal(humanizeLabel("run   status_pending"), "Run Status Pending");
  assert.equal(humanizeLabel("  leading"), "Leading");
});

test("first word starting with a small word is capitalized", () => {
  assert.equal(humanizeLabel("the_run"), "The Run");
});

test("onboarding models step copy mentions both paths honestly", () => {
  const step = onboardingSteps(FRESH).find((item) => item.key === "models");
  assert.match(step?.detail ?? "", /Ollama/);
  assert.match(step?.detail ?? "", /BYOK/);
});

test("first-run detection requires no projects AND no conversations", () => {
  assert.equal(firstRunExperience({ ...FRESH, hasProjects: true }).isFirstRun, false);
  assert.equal(firstRunExperience({ ...FRESH, hasConversations: true }).isFirstRun, false);
});

test("getting-started headline counts completed steps correctly", () => {
  const experience = firstRunExperience({ ...FRESH, hasProjects: true, hasConversations: true, hasSelectableModels: true });
  assert.equal(experience.headline, "Getting started — 3 of 5 done");
  assert.match(experience.detail, /Next: Run your first task/);
});

test("local runtime detail changes with reachability only", () => {
  const reachable = settingsReadiness({ selectableModels: 0, connectedProviders: 0, localRuntimeReachable: true, savedTeams: 0, verifiedPrices: 0 });
  const unreachable = settingsReadiness({ selectableModels: 0, connectedProviders: 0, localRuntimeReachable: false, savedTeams: 0, verifiedPrices: 0 });
  assert.equal(reachable[2].done, true);
  assert.equal(unreachable[2].done, false);
  assert.match(reachable[2].detail, /reachable on this PC/);
});

test("onboarding artifact step points at export capability", () => {
  const step = onboardingSteps(COMPLETE).find((item) => item.key === "artifact");
  assert.match(step?.detail ?? "", /Artifact Center/);
});

test("screenshot targets stay unique per view+id pair", () => {
  const ids = new Set(SCREENSHOT_MANIFEST.map((target) => target.id));
  assert.equal(ids.size, SCREENSHOT_MANIFEST.length);
});

test("settings readiness detail never fabricates counts", () => {
  const items = settingsReadiness({ selectableModels: 7, connectedProviders: 2, localRuntimeReachable: true, savedTeams: 0, verifiedPrices: 3 });
  assert.match(items[1].detail, /^2 provider connections configured$/);
  assert.match(items[4].detail, /^3 verified records in the registry$/);
});

test("humanizeLabel collapses underscores inside each word", () => {
  assert.equal(humanizeLabel("pending__review_state"), "Pending Review State");
});

test("task step detail references the governed pipeline", () => {
  const step = onboardingSteps(FRESH).find((item) => item.key === "task");
  assert.match(step?.detail ?? "", /governed run/);
  assert.equal(step?.view, "dashboard");
});

test("welcome detail keeps the privacy promise wording", () => {
  assert.match(firstRunExperience(FRESH).detail, /runs and stays on this device/);
});
