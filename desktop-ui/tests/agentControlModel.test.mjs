import { test } from "node:test";
import assert from "node:assert/strict";
import {
  modelAssignmentLabel,
  agentCardSummary,
  agentCatalogStats,
  agentSafetyProfile,
  agentDetailSections,
  agentUxState,
  nanoLifecycle,
  nanoRelationshipLabel,
  agentWorkLink,
} from "../src/agentControlModel.ts";

const BASE_AGENT = {
  id: "ag-1",
  name: "Contract Evidence Reviewer",
  description: "Reviews contract clauses against gathered evidence.",
  category: "Analysis",
  agent_class: "specialist_worker",
  origin: "built_in",
  enabled: true,
  preferred_model_policy: "auto_intelligence",
  allowed_tools: ["filesystem.read", "evidence.search"],
  permissions: ["read_project_files"],
  instructions: "Review clauses strictly against the gathered evidence.",
  output_schema: "",
  success_criteria: "Every cited clause has an evidence reference.",
  can_spawn: true,
  max_children: 2,
};

// ---------- Model assignment visibility ----------

test("model policy labels describe router-time choice, not pinned models", () => {
  assert.match(modelAssignmentLabel("auto_intelligence"), /router picks/);
  assert.match(modelAssignmentLabel("manual_only"), /chosen explicitly at task time/);
  assert.match(modelAssignmentLabel("local_preferred"), /on-device/);
  assert.match(modelAssignmentLabel("local_only"), /never selects network/);
});

test("unknown policy falls back to an honest generic label", () => {
  const label = modelAssignmentLabel("custom_policy");
  assert.match(label, /custom policy/);
  assert.match(label, /existing router/);
});

// ---------- Card summary ----------

test("card summary exposes counts, origin, and spawn bounds", () => {
  const summary = agentCardSummary(BASE_AGENT);
  assert.equal(summary.originLabel, "Built-in");
  assert.equal(summary.classLabel, "specialist worker");
  assert.equal(summary.toolCount, 2);
  assert.equal(summary.permissionCount, 1);
  assert.equal(summary.spawnLabel, "Can spawn · max 2");
});

test("non-spawning agents say so plainly", () => {
  const summary = agentCardSummary({ ...BASE_AGENT, can_spawn: false, max_children: 0 });
  assert.equal(summary.spawnLabel, "No spawning");
});

test("custom origin renders as Custom", () => {
  assert.equal(agentCardSummary({ ...BASE_AGENT, origin: "custom" }).originLabel, "Custom");
});

// ---------- Catalog statistics ----------

test("catalog stats count origins, tools, permissions, spawning", () => {
  const agents = [
    BASE_AGENT,
    { ...BASE_AGENT, id: "ag-2", origin: "custom", allowed_tools: [], can_spawn: false, enabled: false },
    { ...BASE_AGENT, id: "ag-3", origin: "installed", permissions: [] },
  ];
  const stats = agentCatalogStats(agents);
  assert.equal(stats.total, 3);
  assert.equal(stats.builtIn, 1);
  assert.equal(stats.custom, 1);
  assert.equal(stats.installed, 1);
  assert.equal(stats.disabled, 1);
  assert.equal(stats.withTools, 2);
  assert.equal(stats.withPermissions, 2);
  assert.equal(stats.spawnCapable, 2);
});

test("catalog stats rank categories by count then name", () => {
  const agents = [
    { ...BASE_AGENT, id: "a", category: "Research" },
    { ...BASE_AGENT, id: "b", category: "Analysis" },
    { ...BASE_AGENT, id: "c", category: "Analysis" },
    { ...BASE_AGENT, id: "d", category: "Writing" },
  ];
  const stats = agentCatalogStats(agents);
  assert.deepEqual(
    stats.categories.map((item) => item.name),
    ["Analysis", "Research", "Writing"],
  );
  assert.equal(stats.categories[0].count, 2);
});

test("empty catalog yields zeroed stats", () => {
  const stats = agentCatalogStats([]);
  assert.equal(stats.total, 0);
  assert.deepEqual(stats.categories, []);
});

// ---------- Safety profile ----------

test("safety profile lists tools and permissions verbatim", () => {
  const safety = agentSafetyProfile(BASE_AGENT);
  assert.equal(safety.allowedTools.join("|"), "filesystem.read|evidence.search");
  assert.equal(safety.permissionsLabel, "read_project_files");
});

test("agents without grants state default governor limits instead of pretending", () => {
  const safety = agentSafetyProfile({ ...BASE_AGENT, allowed_tools: [], permissions: [] });
  assert.match(safety.toolsLabel, /No explicit tool grants/);
  assert.match(safety.permissionsLabel, /default governor limits apply/);
});

test("spawning bounds are shown and tied to the governor", () => {
  const safety = agentSafetyProfile(BASE_AGENT);
  assert.match(safety.spawnLabel, /bounded to 2 children/);
  const single = agentSafetyProfile({ ...BASE_AGENT, max_children: 1 });
  assert.match(single.spawnLabel, /bounded to 1 child \(/);
});

test("no-spawn agents state it without inventing restrictions", () => {
  assert.equal(
    agentSafetyProfile({ ...BASE_AGENT, can_spawn: false }).spawnLabel,
    "This agent cannot spawn child agents",
  );
});

// ---------- Detail sections ----------

test("detail sections appear in the manager-ordered sequence", () => {
  const sections = agentDetailSections(BASE_AGENT);
  assert.deepEqual(
    sections.map((section) => section.id),
    ["identity", "purpose", "model", "permissions", "recent_tasks", "performance", "cost"],
  );
});

test("identity and purpose carry the real template fields", () => {
  const [identity, purpose] = agentDetailSections(BASE_AGENT);
  assert.equal(identity.rows.find((row) => row.label === "Name")?.value, BASE_AGENT.name);
  assert.equal(purpose.rows.find((row) => row.label === "Description")?.value, BASE_AGENT.description);
});

test("model section says the router chooses at task time", () => {
  const model = agentDetailSections(BASE_AGENT).find((section) => section.id === "model");
  assert.match(model?.rows.find((row) => row.label === "Concrete model")?.value ?? "", /router/);
});

test("untracked sections stay honest with not-tracked notes", () => {
  const sections = agentDetailSections(BASE_AGENT);
  for (const id of ["recent_tasks", "performance", "cost"]) {
    const section = sections.find((item) => item.id === id);
    assert.equal(section?.rows.length, 0, id);
    assert.match(section?.note ?? "", /Not tracked per agent/, id);
  }
});

test("missing optional purpose fields degrade to recorded honest values", () => {
  const purpose = agentDetailSections({ ...BASE_AGENT, success_criteria: "", output_schema: "" })
    .find((section) => section.id === "purpose");
  assert.match(purpose?.rows.find((row) => row.label === "Success criteria")?.value ?? "", /Not specified/);
  assert.equal(purpose?.rows.find((row) => row.label === "Output schema")?.value, "Unstructured");
});

// ---------- UX states ----------

test("no agents state offers create action", () => {
  const view = agentUxState(0, { failed: 0, cancelled: 0, running: 0, queued: 0 });
  assert.equal(view.state, "no_agents");
  assert.equal(view.action?.target, "create_agent");
});

test("failed tasks outrank idle state and never auto-retry", () => {
  const view = agentUxState(5, { failed: 2, cancelled: 1, running: 0, queued: 0 });
  assert.equal(view.state, "has_failures");
  assert.match(view.title, /^2 tasks failed/);
  assert.match(view.detail, /nothing auto-retries/);
});

test("cancelled tasks surface after failures are resolved", () => {
  const view = agentUxState(5, { failed: 0, cancelled: 3, running: 0, queued: 0 });
  assert.equal(view.state, "has_cancellations");
  assert.match(view.title, /^3 tasks cancelled/);
});

test("idle catalog with no work shows the mandated wording", () => {
  const view = agentUxState(5, { failed: 0, cancelled: 0, running: 0, queued: 0 });
  assert.equal(view.state, "no_active_tasks");
  assert.equal(view.title, "No active tasks");
  assert.equal(view.detail, "Start a new AMCA workflow.");
  assert.equal(view.action?.target, "compose");
});

test("healthy state reports running and queued counts", () => {
  const view = agentUxState(5, { failed: 0, cancelled: 0, running: 2, queued: 1 });
  assert.equal(view.state, "healthy");
  assert.match(view.detail, /2 running · 1 queued/);
});

// ---------- Nano lifecycle ----------

test("lifecycle maps each nano status to the furthest reached stage", () => {
  assert.equal(nanoLifecycle("proposed").reachedIndex, 0);
  assert.equal(nanoLifecycle("queued").reachedIndex, 1);
  assert.equal(nanoLifecycle("running").reachedIndex, 2);
  assert.equal(nanoLifecycle("waiting").reachedIndex, 2);
  assert.equal(nanoLifecycle("verifying").reachedIndex, 3);
  assert.equal(nanoLifecycle("completed").reachedIndex, 4);
  assert.equal(nanoLifecycle("retired").reachedIndex, 4);
});

test("failed and cancelled are terminal branches, not stages", () => {
  assert.equal(nanoLifecycle("failed").terminal, "failed");
  assert.equal(nanoLifecycle("cancelled").terminal, "cancelled");
  assert.equal(nanoLifecycle("running").terminal, null);
  assert.equal(nanoLifecycle("failed").reachedIndex, 0);
});

test("unknown statuses degrade to the first stage without crashing", () => {
  const view = nanoLifecycle("mystery");
  assert.equal(view.reachedIndex, 0);
  assert.equal(view.terminal, null);
  assert.equal(view.stages.length, 5);
});

// ---------- Nano relationships ----------

test("depth-0 or root-parent workers are labeled root", () => {
  assert.match(nanoRelationshipLabel(0, "task-9"), /Root worker/);
  assert.match(nanoRelationshipLabel(1, "root"), /Root worker/);
  assert.match(nanoRelationshipLabel(2, ""), /Root worker/);
});

test("child workers name their depth and parent task", () => {
  assert.equal(
    nanoRelationshipLabel(2, "task-9"),
    "Child worker at depth 2 — parent task task-9",
  );
});

// ---------- Work link ----------

test("agent work link points to the monitor without inventing history", () => {
  const link = agentWorkLink("ag-1");
  assert.equal(link.label, "Agent Monitor");
  assert.match(link.detail, /only while a task that needs it is live/);
});

// ---------- Library filtering (imported above via destructure) ----------

const { filterAgents } = await import("../src/agentControlModel.ts");

test("filterAgents matches name, description, and category case-insensitively", () => {
  const agents = [
    BASE_AGENT,
    { ...BASE_AGENT, id: "x", name: "Draft Writer", description: "Writes drafts.", category: "Writing" },
  ];
  assert.equal(filterAgents(agents, "evidence", "all").length, 1);
  assert.equal(filterAgents(agents, "WRITER", "all").length, 1);
  assert.equal(filterAgents(agents, "", "Analysis").length, 1);
  assert.equal(filterAgents(agents, "", "Writing").length, 1);
  assert.equal(filterAgents(agents, "", "all").length, 2);
  assert.equal(filterAgents(agents, "   ", "all").length, 2);
});

test("filterAgents returns nothing on a nonsense query", () => {
  assert.deepEqual(filterAgents([BASE_AGENT], "zzz-not-there", "all"), []);
});
