// ZCODE-AMCA-STUDIO-V0.8.0-CONSENT-AND-GOVERNANCE-UX-ENHANCEMENT-01 tests.
// Order minimum: no hidden actions, no automatic approvals, no inferred
// permissions, canonical wording, empty states.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  governanceSummary,
  approvalContexts,
  approvalContextFromRequest,
  APPROVAL_EMPTY,
  USER_CONTROL_BOUNDARY,
  privacyIndicator,
  indicatorForModelRef,
  indicatorForKnowledge,
  runGovernanceLine,
} from "../src/consentGovernanceUxModel.ts";

// ---------- governance summary (recorded fields only) ----------

test("governance summary reports recorded states and excludes scores", () => {
  const summary = governanceSummary({
    knowledgeVerdicts: [
      { shareability: "shareable", shareabilityReason: "x" },
      { shareability: "private", shareabilityReason: "y" },
    ],
    providerApprovalState: "Provider approval required before any network call",
    localModelCount: 2,
    providerModelCount: 1,
  });
  assert.equal(summary.knowledgePermissionState, "1 shareable · 1 private/restricted (recorded verdicts)");
  assert.ok(summary.localModelStatus.includes("2 local model(s)"));
  assert.ok(summary.providerModelStatus.includes("approval required"));
  assert.ok(summary.workflowGovernanceState.includes("exact approval before any network call"));
  assert.equal(summary.note, "Recorded fields only — no security score, no compliance score.");
  // the note is the boundary statement itself; scan only the data fields
  const { note, ...dataFields } = summary;
  const rendered = JSON.stringify(dataFields).toLowerCase();
  assert.ok(!/security score|compliance score|success probability/.test(rendered));
});

test("unrecorded inputs render the canonical Not recorded, never a guess", () => {
  const summary = governanceSummary({
    knowledgeVerdicts: [],
    providerApprovalState: null,
    localModelCount: 0,
    providerModelCount: 0,
  });
  assert.equal(summary.knowledgePermissionState, "Not recorded");
  assert.equal(summary.providerApprovalState, "Not recorded");
  assert.equal(summary.localModelStatus, "Not recorded");
  assert.equal(summary.providerModelStatus, "Not recorded");
});

// ---------- approval context: existing records only ----------

test("approval contexts map recorded requests verbatim — no automation", () => {
  const records = approvalContexts([
    { approval_id: "apr-1", resource: "provider call: openai", status: "pending", created_at: "2026-09-03T10:00:00Z" },
    { status: "approved", created_at: null },
  ]);
  assert.equal(records[0].whatRequiresApproval.includes("apr-1"), true);
  assert.equal(records[0].affectedResource, "provider call: openai");
  assert.equal(records[0].currentState, "pending");
  assert.equal(records[0].recordedTimestamp, "2026-09-03T10:00:00Z");
  assert.equal(records[1].affectedResource, "Not recorded");
  assert.equal(records[1].currentState, "approved");
  assert.equal(records[1].recordedTimestamp, null);
  const rendered = JSON.stringify(records).toLowerCase();
  assert.ok(!/auto-approv|automatically approv|predicted|pre-approved/.test(rendered));
});

test("recorded ApprovalRequest contract maps without invention", () => {
  const mapped = approvalContextFromRequest({
    id: "apr-9",
    action: "provider_call",
    requested_by: "agent-2",
    status: "pending",
    created_at: "2026-09-03T11:00:00Z",
  });
  assert.ok(mapped.whatRequiresApproval.includes("provider_call"));
  assert.ok(mapped.whatRequiresApproval.includes("agent-2"));
  assert.equal(mapped.currentState, "pending");
  assert.equal(mapped.recordedTimestamp, "2026-09-03T11:00:00Z");
});

test("empty approval state predicts nothing", () => {
  assert.equal(APPROVAL_EMPTY.title, "No approval records available.");
  assert.ok(APPROVAL_EMPTY.detail.includes("nothing is predicted or pre-decided"));
});

// ---------- user control boundary ----------

test("user control boundary states the recorded split exactly", () => {
  assert.deepEqual(USER_CONTROL_BOUNDARY.usersControl.map((line) => line.split(" — ")[0]), [
    "Knowledge attachment",
    "Execution initiation",
    "Provider/network approval",
  ]);
  assert.equal(USER_CONTROL_BOUNDARY.amcaDoesNot.length, 3);
  const rendered = USER_CONTROL_BOUNDARY.amcaDoesNot.join(" ").toLowerCase();
  assert.ok(rendered.includes("retrieve hidden context"));
  assert.ok(rendered.includes("execute hidden actions"));
  assert.ok(rendered.includes("modify workflow state automatically"));
  assert.ok(USER_CONTROL_BOUNDARY.note.includes("Recorded behavior, not promises"));
});

// ---------- privacy indicators (canonical wording) ----------

test("privacy indicators use the canonical wording", () => {
  assert.deepEqual(
    [privacyIndicator("local_only").label, privacyIndicator("approval_required").label, privacyIndicator("explicitly_attached").label, privacyIndicator("not_recorded").label],
    ["Local only", "Approval required", "Explicitly attached", "Not recorded"],
  );
  assert.equal(indicatorForModelRef("ollama:qwen").label, "Local only");
  assert.equal(indicatorForModelRef("cred-7").label, "Approval required");
  assert.equal(indicatorForModelRef("").label, "Not recorded");
  assert.equal(indicatorForKnowledge({ shareability: "shareable" }).label, "Explicitly attached");
  assert.equal(indicatorForKnowledge({ shareability: "private" }).label, "Not recorded");
});

// ---------- run governance line ----------

test("run governance line keeps recorded privacy/mode with the approval rule", () => {
  const line = runGovernanceLine({ privacy: "local-only", mode: "solo" });
  assert.ok(line.includes("local-only"));
  assert.ok(line.includes("solo"));
  assert.ok(line.includes("exact approval before any network call"));
  const unrecorded = runGovernanceLine({ privacy: "", mode: "" });
  assert.ok(unrecorded.includes("Not recorded"));
});
