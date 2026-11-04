import test from "node:test";
import assert from "node:assert/strict";
import { LIMITATION_RECORDS } from "../src/validationCenterModel.ts";
import {
  FEEDBACK_CATEGORIES,
  categoryById,
  addFeedbackRecord,
  resolveFeedbackRecord,
  limitationRefinementChains,
  feedbackRecordChain,
  improvementSummary,
  feedbackModules,
  FEEDBACK_PRIVACY,
} from "../src/feedbackRefinementModel.ts";

test("all eight mandated categories exist in order", () => {
  assert.deepEqual(
    FEEDBACK_CATEGORIES.map((category) => category.label),
    ["UX", "Performance", "Reliability", "Documentation", "Model Configuration", "Workflow", "Security", "Feature Request"],
  );
});

test("categoryById resolves known ids and rejects unknown without guessing", () => {
  assert.equal(categoryById("ux")?.label, "UX");
  assert.equal(categoryById("nonexistent"), null);
});

test("affected-module choices derive from recorded limitation modules only", () => {
  const modules = feedbackModules(LIMITATION_RECORDS);
  assert.ok(modules.includes("Model Hub"));
  assert.ok(modules.includes("Other / unlisted"));
  assert.ok(!modules.includes("Invented Module"));
});

test("addFeedbackRecord creates a session-local open record", () => {
  const record = addFeedbackRecord({ categoryId: "ux", module: "Model Hub", text: "Cost preview needs clearer labeling.", createdAt: "2026-09-02T12:00:00Z" });
  assert.ok(record);
  assert.equal(record.status, "open");
  assert.equal(record.storage, "session_local");
  assert.equal(record.categoryId, "ux");
  assert.equal(record.resolution, "not recorded");
});

test("empty text or empty module is rejected", () => {
  assert.equal(addFeedbackRecord({ categoryId: "ux", module: "Model Hub", text: "   ", createdAt: "x" }), null);
  assert.equal(addFeedbackRecord({ categoryId: "ux", module: "  ", text: "text", createdAt: "x" }), null);
});

test("unknown category id stays literally unclassified — no automatic classification", () => {
  const record = addFeedbackRecord({ categoryId: "mystery", module: "Model Hub", text: "something broke", createdAt: "x" });
  assert.equal(record.categoryId, "unclassified");
  assert.equal(record.categoryLabel, "Unclassified");
});

test("resolveFeedbackRecord marks only the target record", () => {
  const a = addFeedbackRecord({ categoryId: "ux", module: "M1", text: "a", createdAt: "x" });
  const b = addFeedbackRecord({ categoryId: "ux", module: "M2", text: "b", createdAt: "x" });
  const resolved = resolveFeedbackRecord([a, b], a.id);
  assert.equal(resolved[0].status, "resolved");
  assert.equal(resolved[1].status, "open");
  assert.match(resolved[0].resolution, /no backend record exists/);
});

test("limitation refinement chains expose recorded links only", () => {
  const chains = limitationRefinementChains(LIMITATION_RECORDS);
  assert.equal(chains.length, 8);
  for (const chain of chains) {
    assert.equal(chain.linksRecorded, true);
    assert.ok(chain.knownLimitation.length > 0);
    assert.ok(chain.targetRelease.length > 0);
    assert.ok(chain.affectedModule.length > 0);
  }
});

test("feedback record chain degrades unrecorded links to not recorded", () => {
  const record = addFeedbackRecord({ categoryId: "workflow", module: "Task composer", text: "Task type disappears from the goal.", createdAt: "x" });
  const chain = feedbackRecordChain(record);
  assert.equal(chain.linksRecorded, false);
  assert.match(chain.knownLimitation, /not recorded/);
  assert.equal(chain.targetRelease, "not recorded");
  assert.equal(chain.affectedModule, "Task composer");
});

test("improvement summary counts records and limitations only", () => {
  const a = addFeedbackRecord({ categoryId: "ux", module: "M", text: "a", createdAt: "x" });
  const b = addFeedbackRecord({ categoryId: "ux", module: "M", text: "b", createdAt: "x" });
  const summary = improvementSummary(resolveFeedbackRecord([a, b], a.id), LIMITATION_RECORDS.length);
  assert.equal(summary.feedbackCount, 2);
  assert.equal(summary.openItems, 1);
  assert.equal(summary.resolvedItems, 1);
  assert.equal(summary.limitationCount, 8);
  assert.match(summary.line, /2 feedback records this session · 1 open · 1 resolved · 8 recorded limitations/);
});

test("empty session summary discloses the missing persistence store", () => {
  const summary = improvementSummary([], LIMITATION_RECORDS.length);
  assert.equal(summary.feedbackCount, 0);
  assert.match(summary.line, /no cross-session feedback store exists in this build/);
});

test("summary carries no score, sentiment, or ranking fields", () => {
  const summary = improvementSummary([], LIMITATION_RECORDS.length);
  for (const forbidden of ["score", "sentiment", "ranking", "satisfaction"]) {
    assert.ok(!Object.keys(summary).some((key) => key.toLowerCase().includes(forbidden)), `forbidden key ${forbidden}`);
  }
});

test("privacy disclosure states local-only, no telemetry, no auto-submission", () => {
  assert.match(FEEDBACK_PRIVACY.headline, /Stored locally/);
  assert.match(FEEDBACK_PRIVACY.headline, /No telemetry/);
  assert.match(FEEDBACK_PRIVACY.headline, /No automatic submission/);
  assert.match(FEEDBACK_PRIVACY.detail, /no feedback persistence backend/);
});
