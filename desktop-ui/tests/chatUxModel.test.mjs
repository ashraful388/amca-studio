// Gate-2 follow-up (manager decisions 2b/3b/7) + R6: deterministic coverage
// for the composer + menu model, the Effort selector, and the PDF-preview
// classification — testing the underlying UI model rather than automating
// native popups. Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  CLOCK_TICK_MS,
  COMPOSER_MENU_ACTIONS,
  EFFORT_LEVELS,
  DEFAULT_EFFORT,
  EFFORT_LABELS,
  effortLabel,
  PDF_PREVIEW_UNAVAILABLE,
} from "../src/chatUxModel.ts";

test("the + menu holds /, @, $ and Attachments — one entry point, nothing beside it", () => {
  // R6 operator decision: Attachments, Commands, Context and Skills live
  // INSIDE the + menu; the toolbar carries no separate / @ $ buttons.
  assert.deepEqual(
    COMPOSER_MENU_ACTIONS.map((item) => item.id),
    ["attach", "commands", "context", "skills"],
  );
  for (const item of COMPOSER_MENU_ACTIONS) {
    assert.ok(item.label.length > 0, "every menu entry has a label");
    assert.ok(item.description.length > 0, "every menu entry has a description");
  }
});

test("the Effort selector is real: three levels with a working default", () => {
  assert.deepEqual([...EFFORT_LEVELS], ["low", "high", "max"]);
  assert.equal(DEFAULT_EFFORT, "low");
  assert.equal(effortLabel("low"), "Low effort");
  assert.equal(effortLabel("high"), "High effort");
  assert.equal(effortLabel("max"), "Max effort");
  // An unknown effort falls back to the baseline rather than guessing.
  assert.equal(effortLabel("turbo"), "Low effort");
});

test("inline PDF preview is explicitly classified unavailable", () => {
  assert.match(PDF_PREVIEW_UNAVAILABLE, /unavailable/);
  assert.match(PDF_PREVIEW_UNAVAILABLE, /Download/);
  assert.match(PDF_PREVIEW_UNAVAILABLE, /unchanged/);
});

test("the clock tick keeps relative timestamps live at a bounded interval", () => {
  assert.equal(CLOCK_TICK_MS, 30000);
  assert.ok(CLOCK_TICK_MS < 60000, "one interval must be enough to cross a displayed minute boundary");
});
