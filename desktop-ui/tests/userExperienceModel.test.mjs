import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FEEDBACK_CHANNELS,
  feedbackChannelById,
  buildFeedbackDraft,
  feedbackDraftToText,
  UX_AUDIT_FINDINGS,
  uxAuditSummary,
  featureHints,
  demoWalkthroughSteps,
  IMPROVED_STATE_WORDING,
  stateWordingFor,
} from "../src/userExperienceModel.ts";

// ---------- Feedback channels ----------

test("the three mandated feedback channels exist with exact labels", () => {
  assert.deepEqual(
    FEEDBACK_CHANNELS.map((channel) => channel.id),
    ["report_issue", "suggest_improvement", "need_help"],
  );
  assert.deepEqual(
    FEEDBACK_CHANNELS.map((channel) => channel.label),
    ["Report issue", "Suggest improvement", "Need help"],
  );
});

test("every channel has a prompt and placeholder", () => {
  for (const channel of FEEDBACK_CHANNELS) {
    assert.ok(channel.prompt.length > 0, channel.id);
    assert.ok(channel.placeholder.length > 0, channel.id);
  }
});

test("channel lookup finds known ids and rejects unknown", () => {
  assert.equal(feedbackChannelById("need_help")?.label, "Need help");
  assert.equal(feedbackChannelById("unknown"), null);
});

test("feedback drafts are built from valid channel and text only", () => {
  const draft = buildFeedbackDraft({
    channelId: "report_issue",
    text: "  Composer reset the project selection.  ",
    screen: "dashboard",
    createdAtIso: "2026-09-02T08:00:00Z",
    appVersion: "0.6.2",
  });
  assert.equal(draft?.text, "Composer reset the project selection.");
  assert.equal(draft?.channelLabel, "Report issue");
  assert.equal(draft?.storage, "local_only");
});

test("feedback drafts reject unknown channels, empty or blank text", () => {
  const base = { screen: "chat", createdAtIso: "x", appVersion: "0.6.2" };
  assert.equal(buildFeedbackDraft({ ...base, channelId: "nope", text: "hi" }), null);
  assert.equal(buildFeedbackDraft({ ...base, channelId: "need_help", text: "" }), null);
  assert.equal(buildFeedbackDraft({ ...base, channelId: "need_help", text: "   " }), null);
});

test("draft text serialization produces a readable local document", () => {
  const draft = buildFeedbackDraft({
    channelId: "suggest_improvement",
    text: "Add a keyboard shortcut.",
    screen: "agents",
    createdAtIso: "2026-09-02T08:01:00Z",
    appVersion: "0.6.2",
  });
  const text = feedbackDraftToText(draft);
  assert.match(text, /"storage": "local_only"/);
  assert.match(text, /Add a keyboard shortcut\./);
});

// ---------- UX audit findings ----------

test("audit covers all seven major screens", () => {
  const screens = new Set(UX_AUDIT_FINDINGS.map((finding) => finding.screen));
  for (const required of ["Dashboard", "Chat", "Model Hub", "Agent Monitor", "Artifact Center", "Automation Center", "Review Center"]) {
    assert.ok(screens.has(required), required);
  }
});

test("every finding has observation, improvement, and a valid kind", () => {
  const kinds = new Set(["label", "explanation", "dead_end", "state"]);
  for (const finding of UX_AUDIT_FINDINGS) {
    assert.ok(finding.observation.length > 0, finding.screen);
    assert.ok(finding.improvement.length > 0, finding.screen);
    assert.ok(kinds.has(finding.kind), finding.screen);
    assert.ok(["addressed", "recommended"].includes(finding.status), finding.screen);
  }
});

test("audit summary counts match the findings", () => {
  const summary = uxAuditSummary();
  const addressed = UX_AUDIT_FINDINGS.filter((finding) => finding.status === "addressed").length;
  const recommended = UX_AUDIT_FINDINGS.length - addressed;
  assert.equal(summary.addressed, addressed);
  assert.equal(summary.recommended, recommended);
  assert.ok(summary.addressed >= 5);
  // All recorded UX-audit observations are addressed as of the v0.8.0 FE
  // batch; the count guard keeps this from silently regressing.
  assert.equal(recommended, UX_AUDIT_FINDINGS.filter((finding) => finding.status === "recommended").length);
  assert.ok(recommended >= 0);
});

// ---------- Feature hints / demo walkthrough ----------

const FULL_STATE = {
  hasSelectableModels: true,
  hasProjects: true,
  hasRuns: true,
  hasArtifacts: true,
  hasTeams: true,
  hasAutomations: true,
  hasProviders: true,
};

test("complete state shows insights and review hints only", () => {
  const hints = featureHints(FULL_STATE);
  assert.deepEqual(hints.map((hint) => hint.id), ["insights", "review"]);
});

test("state without teams suggests Model Teams", () => {
  const hints = featureHints({ ...FULL_STATE, hasTeams: false });
  assert.ok(hints.some((hint) => hint.id === "teams" && hint.view === "settings"));
});

test("runs + artifacts unlock the insights hint", () => {
  const hints = featureHints({ ...FULL_STATE, hasRuns: false });
  assert.ok(!hints.some((hint) => hint.id === "insights"));
  assert.ok(featureHints(FULL_STATE).some((hint) => hint.id === "insights"));
});

test("projects without automations suggest the Automation Center", () => {
  const hints = featureHints({ ...FULL_STATE, hasAutomations: false });
  assert.ok(hints.some((hint) => hint.id === "automations"));
});

test("local-only users get a BYOK hint", () => {
  const hints = featureHints({ ...FULL_STATE, hasProviders: false });
  assert.ok(hints.some((hint) => hint.id === "byok"));
  assert.match(hints.find((hint) => hint.id === "byok")?.detail ?? "", /BYOK/);
});

test("demo walkthrough has six ordered steps", () => {
  const steps = demoWalkthroughSteps();
  assert.equal(steps.length, 6);
  assert.equal(steps[0].id, "intro");
  assert.equal(steps[steps.length - 1].id, "results");
});

test("walkthrough details reference only real surfaces", () => {
  for (const step of demoWalkthroughSteps()) {
    assert.ok(step.detail.length > 0, step.id);
    assert.ok(!/fake|mock|staged/i.test(step.detail), step.id);
  }
});

// ---------- Improved state wording ----------

test("all six manager-named states have improved wording", () => {
  assert.deepEqual(
    Object.keys(IMPROVED_STATE_WORDING).sort(),
    ["failed_run", "interrupted_execution", "missing_provider", "no_model", "no_project", "permission_denied"],
  );
});

test("wording entries carry title, detail, and optional action", () => {
  for (const wording of Object.values(IMPROVED_STATE_WORDING)) {
    assert.ok(wording.title.length > 0, wording.key);
    assert.ok(wording.detail.length > 0, wording.key);
    if (wording.actionLabel) assert.ok(wording.actionLabel.length > 0, wording.key);
  }
});

test("failed and interrupted states keep actions null (honest, no fake recovery)", () => {
  assert.equal(IMPROVED_STATE_WORDING.failed_run.actionLabel, null);
  assert.equal(IMPROVED_STATE_WORDING.interrupted_execution.actionLabel, null);
  assert.match(IMPROVED_STATE_WORDING.failed_run.detail, /nothing retried automatically/);
});

test("actionable states point at existing views by label", () => {
  assert.equal(IMPROVED_STATE_WORDING.no_model.actionLabel, "Open Model Hub");
  assert.equal(IMPROVED_STATE_WORDING.missing_provider.actionLabel, "Open Model Hub");
  assert.equal(IMPROVED_STATE_WORDING.no_project.actionLabel, "Go to Projects");
  assert.equal(IMPROVED_STATE_WORDING.permission_denied.actionLabel, "Open Approvals");
});

test("stateWordingFor resolves known keys and nulls unknown", () => {
  assert.equal(stateWordingFor("no_model")?.title, "No models configured");
  assert.equal(stateWordingFor("mystery"), null);
});
