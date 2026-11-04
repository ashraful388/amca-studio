import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { NAV_WORK, NAV_RESOURCES, NAV_ADVANCED } from "../src/studioPolishModel.ts";
import {
  RC_MODULE_INVENTORY,
  auditRcCompliance,
  RELEASE_DOC_REFERENCES,
  ALPHA_WORDING_FIXES,
} from "../src/rcComplianceModel.ts";

const SIDEBAR_GROUPS = [
  { group: "Work", items: NAV_WORK },
  { group: "Resources", items: NAV_RESOURCES },
  { group: "Advanced", items: NAV_ADVANCED },
];

test("all eight Beta UX milestones are recorded in the inventory", () => {
  assert.deepEqual(
    RC_MODULE_INVENTORY.map((record) => record.milestone),
    ["B4", "B11", "B12", "B13", "B14", "B18", "B19", "B20"],
  );
});

test("every module passes navigation, surface, and documentation checks on live nav data", () => {
  const findings = auditRcCompliance({ sidebarGroups: SIDEBAR_GROUPS });
  const failures = findings.filter((finding) => finding.severity === "fail");
  assert.deepEqual(failures, []);
  assert.equal(findings.length, RC_MODULE_INVENTORY.length * 3);
});

test("every referenced milestone report exists on disk", () => {
  const base = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs");
  for (const record of RC_MODULE_INVENTORY) {
    const relative = record.docPath.replace(/^software\/docs\//, "");
    assert.ok(existsSync(join(base, relative)), `missing report: ${record.docPath}`);
  }
});

test("every module names a concrete surface and empty-state source", () => {
  for (const record of RC_MODULE_INVENTORY) {
    assert.ok(record.surface.length > 20, `${record.milestone} surface too vague`);
    assert.ok(record.emptyStateSource.length > 0, `${record.milestone} missing empty-state record`);
  }
});

test("all five release documentation references resolve to sidebar-hosted surfaces", () => {
  const navIds = new Set(SIDEBAR_GROUPS.flatMap((group) => group.items.map((item) => item.id)));
  assert.deepEqual(
    RELEASE_DOC_REFERENCES.map((reference) => reference.label),
    ["Help & Resources", "Known Limitations", "Validation Center", "Feedback Center", "Beta status information"],
  );
  for (const reference of RELEASE_DOC_REFERENCES) {
    assert.ok(navIds.has(reference.host), `${reference.label} hosts on unknown view ${reference.host}`);
    assert.ok(reference.entry.length > 0);
  }
});

test("the recorded Alpha-wording fixes cover all four renamed references", () => {
  assert.equal(ALPHA_WORDING_FIXES.length, 4);
  for (const fix of ALPHA_WORDING_FIXES) {
    assert.match(fix, /Alpha feedback.*Beta feedback/);
  }
});
