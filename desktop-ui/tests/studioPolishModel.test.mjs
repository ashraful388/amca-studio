import { test } from "node:test";
import assert from "node:assert/strict";
import {
  GUIDED_JOURNEY,
  journeyStepById,
  EMPTY_STATE_COPY,
  emptyStateFor,
  NAV_WORK,
  NAV_RESOURCES,
  NAV_ADVANCED,
  NAV_KNOWN_IDS,
  navLabel,
  providerCardGuidance,
  costBasisExplanation,
  modelComparisonRows,
  SOURCE_LABELS,
  unavailableModelSummary,
  runDurationLabel,
  activityBarSegments,
  DEMO_CHECKLIST,
  KNOWN_LIMITATIONS,
} from "../src/studioPolishModel.ts";

// ---------- Guided journey ----------

test("journey has the nine manager-ordered steps in order", () => {
  assert.equal(GUIDED_JOURNEY.length, 9);
  assert.deepEqual(
    GUIDED_JOURNEY.map((step) => step.id),
    [
      "new_user", "create_project", "open_workspace", "compose_task",
      "select_models", "run_collaboration", "monitor_agents", "review_artifact", "export_result",
    ],
  );
});

test("every journey step has label, detail, and a view", () => {
  for (const step of GUIDED_JOURNEY) {
    assert.ok(step.label.length > 0, step.id);
    assert.ok(step.detail.length > 0, step.id);
    assert.ok(step.view.length > 0, step.id);
  }
});

test("journey step lookup finds known ids and rejects unknown ones", () => {
  assert.equal(journeyStepById("compose_task")?.label, "Compose Task");
  assert.equal(journeyStepById("nonexistent"), null);
});

test("journey view targets cover the real screens used by the demo", () => {
  const views = new Set(GUIDED_JOURNEY.map((step) => step.view));
  for (const required of ["dashboard", "overview", "settings", "agents", "artifacts"]) {
    assert.ok(views.has(required), `journey should touch ${required}`);
  }
});

// ---------- Empty-state catalog ----------

test("models empty state uses the exact mandated wording", () => {
  const copy = emptyStateFor("models");
  assert.equal(copy.title, "No models configured");
  assert.equal(copy.detail, "Connect a provider or enable local models.");
  assert.equal(copy.action?.label, "Open Model Hub");
  assert.equal(copy.action?.view, "settings");
});

test("active-tasks empty state uses the exact mandated wording", () => {
  const copy = emptyStateFor("active_tasks");
  assert.equal(copy.title, "No active tasks");
  assert.equal(copy.detail, "Start a new AMCA workflow.");
  assert.equal(copy.action?.label, "Open composer");
  assert.equal(copy.action?.view, "dashboard");
});

test("every catalog entry has a title and detail", () => {
  for (const [key, copy] of Object.entries(EMPTY_STATE_COPY)) {
    assert.ok(copy.title.length > 0, key);
    assert.ok(copy.detail.length > 0, key);
  }
});

test("unknown context falls back to the generic empty state", () => {
  const copy = emptyStateFor("mystery_context");
  assert.equal(copy.title, "Nothing here yet");
  assert.equal(copy.action, null);
});

test("empty-state actions only target views the nav knows", () => {
  for (const copy of Object.values(EMPTY_STATE_COPY)) {
    if (copy.action) assert.ok(NAV_KNOWN_IDS.includes(copy.action.view) || copy.action.view === "overview");
  }
});

// ---------- Navigation ----------

test("work nav holds dashboard, command center, chat, projects, research, agents in order", () => {
  assert.deepEqual(
    NAV_WORK.map((item) => item.id),
    ["dashboard", "workspace", "chat", "overview", "research", "agents"],
  );
});

test("resources nav includes the previously missing Artifacts entry", () => {
  assert.ok(NAV_RESOURCES.some((item) => item.id === "artifacts"));
  assert.deepEqual(
    NAV_RESOURCES.map((item) => item.id),
    ["workbench", "artifacts", "tools", "settings", "validation", "feedback", "help"],
  );
});

test("advanced nav keeps automation-heavy views out of the primary path", () => {
  assert.deepEqual(
    NAV_ADVANCED.map((item) => item.id),
    ["automations", "review", "permissions", "plugins"],
  );
});

test("nav ids are unique across all groups", () => {
  const ids = NAV_KNOWN_IDS;
  assert.equal(new Set(ids).size, ids.length);
});

test("navLabel resolves every known id and nulls unknown views", () => {
  assert.equal(navLabel("chat"), "Chat");
  assert.equal(navLabel("artifacts"), "Artifacts");
  assert.equal(navLabel("room"), null);
});

// ---------- Provider card guidance ----------

test("connected provider with models reports the count", () => {
  const guidance = providerCardGuidance("GPT (OpenAI)", true, 4);
  assert.equal(guidance.tone, "ok");
  assert.equal(guidance.line, "Connected — 4 models selectable.");
});

test("connected provider without models stays honest about discovery", () => {
  const guidance = providerCardGuidance("Kimi (Moonshot)", true, 0);
  assert.equal(guidance.tone, "ok");
  assert.match(guidance.line, /discovery has not reported/);
});

test("unconnected provider says add a key and never invents status", () => {
  const guidance = providerCardGuidance("Gemini (Google)", false, 0);
  assert.equal(guidance.tone, "empty");
  assert.match(guidance.line, /Not connected — add a Gemini \(Google\) key/);
});

test("singular model count is grammatical", () => {
  const guidance = providerCardGuidance("Claude (Anthropic)", true, 1);
  assert.equal(guidance.line, "Connected — 1 model selectable.");
});

// ---------- Cost explanation ----------

test("each cost basis has a distinct explanation", () => {
  const bases = ["free_local", "direct_profile", "pricing_registry", "unknown"];
  const explanations = bases.map((basis) => costBasisExplanation(basis));
  assert.equal(new Set(explanations.map((item) => item.title)).size, bases.length);
});

test("free-local explanation states the no-charge guarantee", () => {
  const explanation = costBasisExplanation("free_local");
  assert.match(explanation.detail, /no external API charge/);
});

test("unknown pricing refuses to invent an estimate", () => {
  const explanation = costBasisExplanation("unknown");
  assert.match(explanation.detail, /does not invent/);
});

test("registry explanation mentions the newest-record rule", () => {
  assert.match(costBasisExplanation("pricing_registry").detail, /newest verified record/);
});

// ---------- Model comparison rows ----------

test("comparison rows flatten groups with family and source labels", () => {
  const rows = modelComparisonRows([
    {
      familyId: "openai",
      label: "GPT (OpenAI)",
      options: [
        { ref: "prov1:gpt-4o", label: "gpt-4o", source: "byok" },
        { ref: "cred2", label: "gpt-4o-mini", source: "direct" },
      ],
    },
    {
      familyId: "ollama",
      label: "Ollama (Local)",
      options: [{ ref: "ollama:qwen3:1.7b", label: "qwen3:1.7b", source: "local" }],
    },
  ]);
  assert.equal(rows.length, 3);
  assert.equal(rows[0].groupLabel, "GPT (OpenAI)");
  assert.equal(rows[0].sourceLabel, "BYOK");
  assert.equal(rows[1].sourceLabel, "Direct profile");
  assert.equal(rows[2].sourceLabel, "Local · free");
});

test("empty groups produce no rows", () => {
  assert.deepEqual(modelComparisonRows([{ familyId: "x", label: "X", options: [] }]), []);
});

test("source labels cover all four selection sources", () => {
  assert.deepEqual(
    Object.keys(SOURCE_LABELS).sort(),
    ["amca_api", "byok", "direct", "local"],
  );
  assert.equal(SOURCE_LABELS.amca_api, "Managed");
});

// ---------- Unavailable-model summary ----------

test("no unavailable sources yields null", () => {
  const summary = unavailableModelSummary({
    directModels: [{ configured: true }],
    byokProviders: [{ configured: true, model_ids: ["m1"] }],
  });
  assert.equal(summary, null);
});

test("unconfigured direct profiles are counted as missing credentials", () => {
  const summary = unavailableModelSummary({
    directModels: [{ configured: true }, { configured: false }],
    byokProviders: [],
  });
  assert.equal(summary?.missingCredentials, 1);
  assert.match(summary?.line ?? "", /1 profile missing a credential/);
});

test("configured providers without models count as pending discovery", () => {
  const summary = unavailableModelSummary({
    directModels: [],
    byokProviders: [{ configured: true, model_ids: [] }, { configured: false, model_ids: [] }],
  });
  assert.equal(summary?.pendingDiscovery, 1);
  assert.match(summary?.line ?? "", /pending discovery/);
  assert.match(summary?.line ?? "", /1 provider not connected/);
});

test("plural sources read grammatically", () => {
  const summary = unavailableModelSummary({
    directModels: [{ configured: false }, { configured: false }],
    byokProviders: [],
  });
  assert.match(summary?.line ?? "", /^2 sources not selectable yet/);
});

// ---------- Run duration ----------

test("running run duration is live and grows with the injected clock", () => {
  const run = { started_at: "2026-09-02T00:00:00Z", finished_at: null };
  assert.deepEqual(runDurationLabel(run, Date.parse("2026-09-02T00:00:42Z")), { label: "42s", live: true });
  assert.deepEqual(runDurationLabel(run, Date.parse("2026-09-02T00:04:05Z")), { label: "4m 05s", live: true });
});

test("finished run pins duration to finished_at", () => {
  const run = { started_at: "2026-09-02T00:00:00Z", finished_at: "2026-09-02T01:06:00Z" };
  assert.deepEqual(runDurationLabel(run, Date.parse("2027-01-01T00:00:00Z")), { label: "1h 06m", live: false });
});

test("missing or invalid started_at yields null", () => {
  assert.equal(runDurationLabel({ started_at: null, finished_at: null }, 1000), null);
  assert.equal(runDurationLabel({ started_at: "not-a-date", finished_at: null }, 1000), null);
});

test("clock before start clamps to zero seconds", () => {
  const run = { started_at: "2026-09-02T00:10:00Z", finished_at: null };
  assert.equal(runDurationLabel(run, Date.parse("2026-09-02T00:00:00Z"))?.label, "0s");
});

// ---------- Activity bar ----------

test("activity bar splits live, completed, and remainder fractions", () => {
  const segments = activityBarSegments({ nanoTotal: 10, nanoLive: 3, nanoCompleted: 5 });
  assert.equal(segments.length, 3);
  assert.equal(segments[0].key, "live");
  assert.equal(segments[0].fraction, 0.3);
  assert.equal(segments[1].key, "completed");
  assert.equal(segments[1].fraction, 0.5);
  assert.equal(segments[2].key, "other");
  assert.equal(segments[2].fraction, 0.2);
});

test("zero total produces no segments", () => {
  assert.deepEqual(activityBarSegments({ nanoTotal: 0, nanoLive: 0, nanoCompleted: 0 }), []);
});

test("counts above total are clamped so fractions stay ≤ 1", () => {
  const segments = activityBarSegments({ nanoTotal: 2, nanoLive: 5, nanoCompleted: 4 });
  const sum = segments.reduce((total, segment) => total + segment.fraction, 0);
  assert.ok(sum <= 1.000001);
  assert.equal(segments.find((segment) => segment.key === "live")?.count, 2);
  assert.equal(segments.find((segment) => segment.key === "completed"), undefined);
});

// ---------- Demo readiness package ----------

test("demo checklist covers service, project, models, composer, monitor, artifact, privacy", () => {
  const ids = DEMO_CHECKLIST.map((item) => item.id);
  for (const required of ["core_running", "project_ready", "models_ready", "composer_ready", "monitor_ready", "artifact_ready", "privacy_ready"]) {
    assert.ok(ids.includes(required), required);
  }
});

test("every checklist item has title, detail, and a real view", () => {
  for (const item of DEMO_CHECKLIST) {
    assert.ok(item.title.length > 0, item.id);
    assert.ok(item.detail.length > 0, item.id);
    assert.ok(item.view.length > 0, item.id);
  }
});

test("known limitations are non-empty and each has a detail", () => {
  assert.ok(KNOWN_LIMITATIONS.length >= 8);
  for (const limitation of KNOWN_LIMITATIONS) {
    assert.ok(limitation.title.length > 0);
    assert.ok(limitation.detail.length > 0);
  }
});

test("limitations repeat only manager-accepted gaps, honestly worded", () => {
  const titles = KNOWN_LIMITATIONS.map((item) => item.title).join(" ");
  assert.match(titles, /discover-derived/);
  assert.match(titles, /status not reported/);
  assert.match(titles, /published-rate preview/);
  assert.match(titles, /delete \+ re-add/);
});
