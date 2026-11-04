// The composer model menu and the collaboration-architecture rule (pure
// logic — no browser, no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import { buildComposerModelMenu } from "../src/composerModelMenu.ts";
import {
  collaborationArchitectureOptions,
  COLLABORATION_ARCHITECTURES,
} from "../src/chatIntelligenceModel.ts";

function candidate(overrides = {}) {
  return {
    ref: "provider.x:model-a",
    model: "model-a",
    provider: "X",
    label: "Model A",
    verified: true,
    ...overrides,
  };
}

test("models are grouped by provider, in the order they were configured", () => {
  const menu = buildComposerModelMenu([
    candidate({ ref: "p1:a", provider: "OpenRouter", label: "A" }),
    candidate({ ref: "p2:b", provider: "OpenRouter", label: "B" }),
    candidate({ ref: "p3:c", provider: "Nvidia", label: "C" }),
  ]);
  assert.deepEqual(menu.groups.map((group) => group.provider), ["OpenRouter", "Nvidia"]);
  assert.deepEqual(menu.groups[0].models.map((model) => model.label), ["A", "B"]);
  assert.deepEqual(menu.groups[1].models.map((model) => model.label), ["C"]);
  assert.equal(menu.unverifiedCount, 0);
});

test("only verified models are offered", () => {
  const menu = buildComposerModelMenu([
    candidate({ ref: "p1:a", label: "Good" }),
    candidate({ ref: "p1:b", label: "Never measured", verified: false }),
  ]);
  assert.equal(menu.groups.length, 1);
  assert.deepEqual(menu.groups[0].models.map((model) => model.label), ["Good"]);
  // The one that was not measured is counted, so the menu can say so.
  assert.equal(menu.unverifiedCount, 1);
});

test("a provider whose models are all unverified does not appear as an empty group", () => {
  const menu = buildComposerModelMenu([
    candidate({ ref: "p1:a", provider: "OpenRouter", verified: false }),
    candidate({ ref: "p2:b", provider: "Nvidia", verified: true }),
  ]);
  assert.deepEqual(menu.groups.map((group) => group.provider), ["Nvidia"]);
  assert.equal(menu.unverifiedCount, 1);
});

test("an empty candidate list produces an empty menu", () => {
  const menu = buildComposerModelMenu([]);
  assert.deepEqual(menu.groups, []);
  assert.equal(menu.unverifiedCount, 0);
});

test("degraded (transient-failure) models sort below healthy ones", () => {
  const menu = buildComposerModelMenu([
    candidate({ ref: "p1:a", label: "Flaky", degraded: true, healthNote: "Temporarily unavailable" }),
    candidate({ ref: "p1:b", label: "Healthy" }),
  ]);
  assert.deepEqual(menu.groups[0].models.map((model) => model.label), ["Healthy", "Flaky"]);
  assert.equal(menu.groups[0].models[1].healthNote, "Temporarily unavailable");
});

test("one selected model can only run Solo", () => {
  assert.deepEqual(collaborationArchitectureOptions(1), ["solo"]);
  assert.deepEqual(collaborationArchitectureOptions(0), ["solo"]);
});

test("more than one selected model offers every architecture except Solo", () => {
  const options = collaborationArchitectureOptions(2);
  assert.ok(!options.includes("solo"));
  assert.deepEqual(options, [
    "auto",
    "joint",
    "independent_discuss",
    "parallel",
    "adaptive_hybrid",
  ]);
  assert.deepEqual(collaborationArchitectureOptions(5), options);
});

test("every offered architecture is a known one", () => {
  const known = COLLABORATION_ARCHITECTURES.map((entry) => entry.value);
  for (const count of [0, 1, 2, 7]) {
    for (const value of collaborationArchitectureOptions(count)) {
      assert.ok(known.includes(value), value);
    }
  }
});
