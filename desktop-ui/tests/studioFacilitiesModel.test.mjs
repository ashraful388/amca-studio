// ZCode-style tabbed panel model for the AMCA right panel
// (pure logic - no browser, no fetch, no backend). Run with:
// node --test tests/studioFacilitiesModel.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import {
  BROWSER_SCOPE_NOTE,
  FILES_SCOPE_NOTE,
  NEW_TAB_FACILITIES,
  SITES_LOCAL_NOTE,
  SIDE_CONVERSATION_TILE,
  TERMINAL_SCOPE_NOTE,
  panelFacilityById,
  HOOK_EVENTS,
  HOOK_ACTIONS,
  isHookAction,
  isHookEvent,
} from "../src/studioFacilitiesModel.ts";

test("the New tab page carries exactly the six ZCode-screenshot facilities in order", () => {
  assert.deepEqual(
    NEW_TAB_FACILITIES.map((facility) => facility.id),
    ["files", "browser", "terminal", "review", "sites"],
  );
  const byId = new Map(NEW_TAB_FACILITIES.map((facility) => [facility.id, facility]));
  assert.equal(byId.get("files")?.description, "Select a file from the navigation");
  assert.equal(byId.get("browser")?.description, "Browse websites and preview apps.");
  assert.equal(byId.get("terminal")?.description, "Run commands and view output.");
  assert.equal(byId.get("review")?.description, "Review code changes and diffs.");
  assert.equal(byId.get("sites")?.description, "View sites, deployments, and usage.");
});

test("side conversation is the sixth tile: an action, not a tab", () => {
  assert.equal(SIDE_CONVERSATION_TILE.title, "Side conversation");
  assert.equal(SIDE_CONVERSATION_TILE.description, "Continue a conversation in the side pane.");
  // The legacy Round 22 facility tabs are gone from the panel model.
  const ids = NEW_TAB_FACILITIES.map((facility) => facility.id);
  for (const legacy of ["memory", "tasks", "mcp", "skills", "memories", "commands", "agents", "hooks"]) {
    assert.ok(!ids.includes(legacy), `${legacy} must not be a panel tab`);
  }
});

test("panelFacilityById resolves every tab id and refuses unknown ones", () => {
  for (const facility of NEW_TAB_FACILITIES) {
    assert.equal(panelFacilityById(facility.id)?.id, facility.id);
  }
  assert.equal(panelFacilityById("side-conversation"), null);
  assert.equal(panelFacilityById("plugins"), null);
  assert.equal(panelFacilityById(""), null);
});

test("hook enums still match the backend fixed enums (Settings surfaces use them)", () => {
  assert.deepEqual([...HOOK_EVENTS], [
    "run_started",
    "run_completed",
    "run_failed",
    "approval_pending",
    "artifact_created",
  ]);
  assert.ok(isHookEvent("run_completed"));
  assert.ok(!isHookEvent("on_everything"));
  assert.deepEqual([...HOOK_ACTIONS], ["notify", "append-memory", "run-command"]);
  assert.ok(isHookAction("notify"));
  assert.ok(!isHookAction("execute"));
});

test("honest-state notes describe what the workspaces really are", () => {
  assert.match(FILES_SCOPE_NOTE, /Live view/i);
  assert.match(BROWSER_SCOPE_NOTE, /refuse embedding/i);
  assert.match(TERMINAL_SCOPE_NOTE, /your own PowerShell session/i);
  assert.match(TERMINAL_SCOPE_NOTE, /Tool Approval/i);
  assert.match(SITES_LOCAL_NOTE, /no publish step/i);
});
