import { test } from "node:test";
import assert from "node:assert/strict";
import {
  COMPOSER_TEMPLATES,
  templateById,
  templateToDraftPatch,
  KEYBOARD_SHORTCUTS,
  PRODUCTIVITY_FINDINGS,
  betaUxRecommendations,
  shippedProductivityWins,
} from "../src/productivityModel.ts";

// ---------- Composer templates ----------

test("five quick-start templates exist with unique ids", () => {
  const ids = COMPOSER_TEMPLATES.map((template) => template.id);
  assert.equal(new Set(ids).size, COMPOSER_TEMPLATES.length);
  assert.equal(COMPOSER_TEMPLATES.length, 5);
});

test("every template references valid task type and mode ids", () => {
  const taskTypes = new Set(["general_reasoning", "research", "coding", "analysis", "document_creation", "agent_collaboration"]);
  const modes = new Set(["solo", "joint", "independent_discuss", "parallel", "adaptive_hybrid"]);
  for (const template of COMPOSER_TEMPLATES) {
    assert.ok(taskTypes.has(template.taskTypeId), template.id);
    assert.ok(modes.has(template.mode), template.id);
    assert.ok(template.goalSeed.includes("["), template.id);
    assert.ok(template.description.length > 0, template.id);
  }
});

test("template lookup finds known ids and rejects unknown", () => {
  assert.equal(templateById("coding")?.label, "Build or fix code");
  assert.equal(templateById("nope"), null);
});

test("template patches only prefill task type, mode, and goal", () => {
  const template = templateById("research");
  const patch = templateToDraftPatch(template);
  assert.deepEqual(Object.keys(patch).sort(), ["goal", "mode", "taskTypeId"]);
  assert.equal(patch.mode, "adaptive_hybrid");
  assert.match(patch.goal, /Research \[topic\]/);
});

test("multi-agent template uses the safe solo default", () => {
  // H1.1-1: Parallel requires >=2 READY models and manufactured a failed run
  // for fresh users; the safe default is Solo with Auto routing. Explicit
  // Parallel remains selectable in the architecture dropdown.
  const template = templateById("multi_agent");
  assert.equal(template?.taskTypeId, "agent_collaboration");
  assert.equal(template?.mode, "solo");
});

// ---------- Keyboard shortcuts ----------

test("keyboard reference documents the wired accelerators", () => {
  const combos = KEYBOARD_SHORTCUTS.map((shortcut) => shortcut.combo);
  assert.deepEqual(combos, ["Ctrl+K", "Ctrl+1 … Ctrl+6", "Ctrl+,", "Esc"]);
  for (const shortcut of KEYBOARD_SHORTCUTS) {
    assert.ok(shortcut.action.length > 0, shortcut.combo);
    assert.ok(["Global", "Context"].includes(shortcut.scope), shortcut.combo);
  }
});

// ---------- Productivity findings / Beta recommendations ----------

test("productivity findings separate shipped from recommended", () => {
  const shipped = shippedProductivityWins();
  const recommended = betaUxRecommendations();
  assert.equal(shipped.length + recommended.length, PRODUCTIVITY_FINDINGS.length);
  assert.ok(shipped.length >= 2);
  // v0.8.0 FE batch shipped W1/W2/W4; approval_context stays recommended
  // until its backend contract exists, so exactly one recommendation remains.
  assert.equal(recommended.length, 1);
  assert.equal(recommended[0].id, "approval_context");
});

test("every finding has area, friction, and improvement", () => {
  for (const finding of PRODUCTIVITY_FINDINGS) {
    assert.ok(finding.area.length > 0, finding.id);
    assert.ok(finding.friction.length > 0, finding.id);
    assert.ok(finding.improvement.length > 0, finding.id);
  }
});

test("shipped findings describe real mechanisms, not promises", () => {
  const shipped = shippedProductivityWins().map((finding) => finding.improvement).join(" ");
  assert.match(shipped, /quick-start templates/);
  assert.match(shipped, /Ctrl\+K/);
});

test("beta recommendations stay frontend-bounded and honest", () => {
  for (const finding of betaUxRecommendations()) {
    assert.match(finding.improvement, /^Recommended:/);
    // A recommendation that awaits a contract must say so without hiding it.
    assert.ok(
      !/backend|database|architecture/i.test(finding.improvement) || /contract/i.test(finding.improvement),
      finding.id,
    );
  }
});
