import test from "node:test";
import assert from "node:assert/strict";
import { APP_VERSION } from "../src/versionLabel.ts";
import { NAV_WORK, NAV_RESOURCES, NAV_ADVANCED } from "../src/studioPolishModel.ts";
import { NAVIGABLE_VIEWS } from "../src/productivityModel.ts";
import { EMPTY_STATE_COPY } from "../src/studioPolishModel.ts";
import { COMMAND_CENTER_EMPTY_STATES } from "../src/workspaceCommandModel.ts";
import {
  SECONDARY_VIEWS,
  viewChromeLabel,
  auditNavigation,
  NAV_EXIT_LINKS,
  BETA_STATUS,
  CANONICAL_WORDING,
  auditWording,
  EMPTY_STATE_COVERAGE,
  auditEmptyStates,
  HELP_RESOURCES,
} from "../src/consolidationUxModel.ts";

const SIDEBAR_GROUPS = [
  { group: "Work", items: NAV_WORK },
  { group: "Resources", items: NAV_RESOURCES },
  { group: "Advanced", items: NAV_ADVANCED },
];

test("navigation audit is clean on the live sidebar data", () => {
  const audit = auditNavigation({ sidebarGroups: SIDEBAR_GROUPS, palette: NAVIGABLE_VIEWS });
  const issues = audit.findings.filter((finding) => finding.severity === "issue");
  assert.deepEqual(issues, []);
  assert.equal(audit.sidebarCount, 17); // 6 work + 7 resources (incl. help) + 4 advanced
  assert.equal(audit.secondaryCount, 8);
  assert.deepEqual(audit.deadEnds, []);
});

test("every registered view has declared exits — no dead ends by construction", () => {
  const ids = [
    ...NAV_WORK.map((item) => item.id),
    ...NAV_RESOURCES.map((item) => item.id),
    ...NAV_ADVANCED.map((item) => item.id),
    ...SECONDARY_VIEWS.map((item) => item.id),
  ];
  for (const id of ids) {
    assert.ok((NAV_EXIT_LINKS[id] ?? []).length > 0, `${id} has no declared exit`);
  }
});

test("chrome labels resolve sidebar first, then secondary, then AMCA fallback", () => {
  const sidebar = [{ id: "workspace", label: "Command Center" }];
  assert.equal(viewChromeLabel("workspace", sidebar), "Command Center");
  assert.equal(viewChromeLabel("cost", sidebar), "Cost & compute");
  assert.equal(viewChromeLabel("mystery-view", sidebar), "AMCA");
});

test("beta status carries recorded version and channel only — no released claim", () => {
  assert.equal(BETA_STATUS.appVersion, APP_VERSION);
  // 17H2: version derived from package.json via versionLabel source of truth.
  assert.match(BETA_STATUS.channel, /(Stable|Maintenance) release/);
  const text = JSON.stringify(BETA_STATUS).toLowerCase();
  assert.ok(!text.includes("released"));
  assert.ok(!text.includes("generally available"));
});

test("five canonical wording terms exist with usage rules", () => {
  assert.deepEqual(
    CANONICAL_WORDING.map((term) => term.term),
    ["Not recorded", "Unknown", "Unavailable", "Local only", "No models configured"],
  );
  for (const term of CANONICAL_WORDING) assert.ok(term.usageRule.length > 0);
});

test("live model constants conform to the wording standard", () => {
  const usages = [
    { term: "Not recorded", text: "Not recorded" }, // the Validation Center status pill, rendered from scenario.status
    { term: "No models configured", text: COMMAND_CENTER_EMPTY_STATES.models },
    { term: "No models configured", text: `${EMPTY_STATE_COPY.models.title}. ${EMPTY_STATE_COPY.models.detail}` },
    { term: "Local only", text: "Local only" },
  ];
  for (const finding of auditWording(usages)) {
    assert.equal(finding.severity, "ok", finding.detail);
  }
});

test("wording audit flags non-conforming usage and unknown terms", () => {
  const findings = auditWording([
    { term: "Not recorded", text: "Nothing here" },
    { term: "Mystery", text: "anything" },
  ]);
  assert.equal(findings[0].severity, "issue");
  assert.equal(findings[1].severity, "issue");
});

test("empty-state coverage covers all seven mandated scenarios", () => {
  assert.deepEqual(
    EMPTY_STATE_COVERAGE.map((state) => state.scenario),
    ["no projects", "no models", "no artifacts", "no agents", "no workflows", "no validation records", "no feedback"],
  );
});

test("every empty state is actionable or carries an honest reason", () => {
  for (const finding of auditEmptyStates()) {
    assert.equal(finding.severity, "ok", finding.detail);
  }
});

test("validation and feedback empty states stay honest instead of promising actions", () => {
  const validation = EMPTY_STATE_COVERAGE.find((state) => state.scenario === "no validation records");
  assert.equal(validation.actionLabel, null);
  assert.match(validation.honestReason, /no action can create validation records/);
  const feedback = EMPTY_STATE_COVERAGE.find((state) => state.scenario === "no feedback");
  assert.match(feedback.honestReason, /no feedback persistence backend/);
});

test("mandated empty-state copy matches the recorded sources verbatim", () => {
  const models = EMPTY_STATE_COVERAGE.find((state) => state.scenario === "no models");
  assert.equal(models.detail, "Connect a provider or enable local models.");
  const workflows = EMPTY_STATE_COVERAGE.find((state) => state.scenario === "no workflows");
  assert.equal(workflows.detail, "Start a new AMCA workflow.");
  const artifacts = EMPTY_STATE_COVERAGE.find((state) => state.scenario === "no artifacts");
  assert.equal(artifacts.detail, "Artifacts appear after completed tasks.");
  assert.equal(COMMAND_CENTER_EMPTY_STATES.artifacts, artifacts.detail);
});

test("help resources cover the six mandated entries plus cognitive workflow docs", () => {
  const titles = HELP_RESOURCES.map((resource) => resource.title);
  // the mandated B20 entries stay; v0.8.0 final consolidation adds the
  // three cognitive workflow documentation entries after them
  assert.deepEqual(
    titles.slice(0, 6),
    ["Getting Started", "User Guide", "Troubleshooting", "Known Limitations", "Beta Feedback", "Validation Center"],
  );
  assert.equal(titles.length, 9);
  assert.ok(titles.every((title) => title.includes("Cognitive Workflow") === (titles.indexOf(title) >= 6)));
});

test("help resources point only at real targets — docs paths or registered views", () => {
  const viewIds = new Set([
    ...NAV_WORK.map((item) => item.id),
    ...NAV_RESOURCES.map((item) => item.id),
    ...NAV_ADVANCED.map((item) => item.id),
    ...SECONDARY_VIEWS.map((item) => item.id),
  ]);
  for (const resource of HELP_RESOURCES) {
    if (resource.targetView !== null) {
      assert.ok(viewIds.has(resource.targetView), `${resource.title} targets unknown view ${resource.targetView}`);
      assert.equal(resource.docsPath, null);
    } else {
      assert.match(resource.docsPath, /^software\/docs\//);
    }
  }
});

test("documentation hub discloses the missing in-app reader", () => {
  assert.match(BETA_STATUS.documentationNote, /no in-app documentation reader/);
});

test("palette includes the help entry with the sidebar label", () => {
  const help = NAVIGABLE_VIEWS.find((entry) => entry.id === "help");
  assert.ok(help, "help missing from palette");
  const sidebarLabel = NAV_RESOURCES.find((item) => item.id === "help")?.label;
  assert.equal(help.label, sidebarLabel);
});
