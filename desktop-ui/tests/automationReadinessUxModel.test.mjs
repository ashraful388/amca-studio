// ZCODE-AMCA-STUDIO-V0.8.0-PROFESSIONAL-WORKFLOW-AUTOMATION-READINESS-UX-01 tests.
// Order minimum: record-only rendering, no automation claims, no execution
// controls, no inferred schedules, canonical wording, navigation safety.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AUTOMATION_EMPTY_STATES,
  AUTOMATION_APPROVAL_REQUIREMENT,
  AUTOMATION_EXECUTION_BOUNDARY,
  AUTOMATION_READINESS_NOTE,
  CONTINUITY_UNAVAILABLE,
  automationContinuity,
  automationGovernanceFields,
  occurrenceSummary,
} from "../src/automationReadinessUxModel.ts";

// ---------- continuity: recorded last_run_id only ----------

test("continuity uses the recorded last_run_id; missing degrades honestly", () => {
  const linked = automationContinuity({ last_run_id: "run-abc-123" });
  assert.equal(linked.hasWorkflowLink, true);
  assert.equal(linked.lastRunId, "run-abc-123");
  assert.equal(linked.unavailableReason, null);
  assert.ok(linked.artifactsNote.includes("Artifact Center"));

  const unlinked = automationContinuity({ last_run_id: null });
  assert.equal(unlinked.hasWorkflowLink, false);
  assert.equal(unlinked.unavailableReason, CONTINUITY_UNAVAILABLE);
  const blank = automationContinuity({ last_run_id: "   " });
  assert.equal(blank.hasWorkflowLink, false);
  assert.equal(blank.unavailableReason, "No recorded workflow link available.");
  // no inferred relationships: the unlinked output carries no run id at all
  assert.equal(blank.lastRunId, null);
  assert.ok(!JSON.stringify(blank).includes("run-abc-123"));
});

// ---------- governance fields ----------

test("manual-only triggers state the scheduler boundary verbatim", () => {
  const monitor = automationGovernanceFields({ enabled: true, triggerType: "monitor", schedulerRunning: true });
  assert.ok(monitor.triggerCapability.includes("Manual execution only"));
  assert.ok(monitor.triggerCapability.includes("scheduler does not support this trigger type yet"));

  const scheduled = automationGovernanceFields({ enabled: true, triggerType: "recurring", schedulerRunning: true });
  assert.equal(scheduled.triggerCapability, "Scheduler-supported trigger.");

  const stopped = automationGovernanceFields({ enabled: true, triggerType: "recurring", schedulerRunning: false });
  assert.ok(stopped.triggerCapability.includes("Scheduler stopped"));
});

test("approval requirement and execution boundary are recorded statements", () => {
  // 28B: paid models are governed by provider/privacy authorization only;
  // cost is telemetry and never a separate budget approval.
  assert.ok(AUTOMATION_APPROVAL_REQUIREMENT.includes("exact provider and privacy authorization"));
  assert.ok(AUTOMATION_APPROVAL_REQUIREMENT.includes("Cost is recorded as telemetry only"));
  assert.ok(AUTOMATION_EXECUTION_BOUNDARY.includes("never automatically beyond the recorded trigger type"));
  const rendered = (AUTOMATION_APPROVAL_REQUIREMENT + AUTOMATION_EXECUTION_BOUNDARY).toLowerCase();
  assert.ok(!/auto-enabl|automatic enabling|recommend/.test(rendered));
});

test("enabled state is verbatim enabled/disabled", () => {
  assert.equal(automationGovernanceFields({ enabled: true, triggerType: "once", schedulerRunning: true }).enabledState, "enabled");
  assert.equal(automationGovernanceFields({ enabled: false, triggerType: "once", schedulerRunning: true }).enabledState, "disabled");
});

// ---------- occurrence history (counts only) ----------

test("occurrence summary counts recorded statuses; null history states the fact", () => {
  const summary = occurrenceSummary([{ status: "succeeded" }, { status: "succeeded" }, { status: "failed" }, { status: " " }]);
  assert.equal(summary.total, 4);
  assert.equal(summary.byStatus.succeeded, 2);
  assert.equal(summary.byStatus.failed, 1);
  assert.equal(summary.byStatus.unknown, 1);

  const unrecorded = occurrenceSummary(null);
  assert.equal(unrecorded.emptyReason, AUTOMATION_EMPTY_STATES.no_history);
  assert.equal(unrecorded.emptyReason, "Automation execution history is not recorded.");
  const empty = occurrenceSummary([]);
  assert.equal(empty.emptyReason, "No recorded occurrences yet.");
  const rendered = JSON.stringify([summary]).toLowerCase();
  assert.ok(!/predicted|upcoming|next run|probability/.test(rendered));
});

// ---------- mandated empty states ----------

test("mandated empty states are exact", () => {
  assert.equal(AUTOMATION_EMPTY_STATES.no_records, "No automation records available yet.");
  assert.equal(AUTOMATION_EMPTY_STATES.no_history, "Automation execution history is not recorded.");
  assert.equal(AUTOMATION_EMPTY_STATES.scheduler_unavailable, "Scheduler capability is unavailable for this trigger.");
});

// ---------- readiness note: no claims ----------

test("readiness note states records-only with no automation claims", () => {
  const rendered = AUTOMATION_READINESS_NOTE.toLowerCase();
  assert.ok(rendered.includes("no prediction"));
  assert.ok(rendered.includes("no scheduling intelligence"));
  assert.ok(rendered.includes("no automatic enabling or execution"));
  assert.ok(rendered.includes("does not add any"));
});
