// M5.2 unit tests for the Intelligence panel presentation model (pure
// functions only - no browser, no fetch, no backend). Run with:
//   node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  capabilityRows,
  decisionLabel,
  readinessLabel,
  stateLabel,
  validationRows,
} from "../src/intelligencePanelModel.ts";

const STATUS = {
  contract_version: 2,
  seam_id: "amca.intelligence.runtime_bridge",
  capability_contract_version: 2,
  execution_authority: "existing_runtime",
  gates: {},
  capabilities: {
    deterministic: {
      implemented: true,
      enabled: true,
      runtime_ready: "ready",
    },
    adaptive_planner: {
      implemented: true,
      enabled: false,
      runtime_ready: "unknown",
    },
    hybrid_execution: {
      implemented: true,
      enabled: false,
      runtime_ready: "unknown",
    },
    nano_agents: {
      implemented: true,
      enabled: false,
      runtime_ready: "ready",
    },
    model_router: {
      implemented: true,
      enabled: false,
      runtime_ready: "unknown",
    },
  },
};

test("capability rows render all five capabilities in contract order", () => {
  const rows = capabilityRows(STATUS);
  assert.deepEqual(
    rows.map((row) => row.id),
    [
      "deterministic",
      "adaptive_planner",
      "hybrid_execution",
      "nano_agents",
      "model_router",
    ],
  );
});

test("deterministic renders as ready and enabled", () => {
  const row = capabilityRows(STATUS)[0];
  assert.equal(row.state.runtime_ready, "ready");
  assert.equal(stateLabel(row.state), "Implemented · enabled");
  assert.equal(readinessLabel(row.state.runtime_ready), "Ready");
});

test("adaptive/hybrid/router render as implemented but disabled with unknown readiness", () => {
  for (const index of [1, 2, 4]) {
    const row = capabilityRows(STATUS)[index];
    assert.equal(row.state.implemented, true);
    assert.equal(row.state.enabled, false);
    assert.equal(row.state.runtime_ready, "unknown");
    assert.equal(stateLabel(row.state), "Implemented · disabled");
    assert.equal(readinessLabel(row.state.runtime_ready), "Readiness unknown");
  }
});

test("nano execution pipeline is implemented, gate-disabled, and truthfully ready", () => {
  const row = capabilityRows(STATUS)[3];
  assert.equal(row.state.implemented, true);
  assert.equal(row.state.enabled, false);
  // since M5.6 the governed local Nano execution pipeline genuinely
  // exists, so readiness is truthfully "ready" while the gate stays OFF
  assert.equal(row.state.runtime_ready, "ready");
  assert.equal(stateLabel(row.state), "Implemented · disabled");
  assert.equal(readinessLabel(row.state.runtime_ready), "Ready");
});

test("missing capabilities fall back to an explicit not-implemented state", () => {
  const rows = capabilityRows({
    ...STATUS,
    capabilities: {},
  });
  for (const row of rows) {
    assert.equal(row.state.implemented, false);
    assert.equal(row.state.enabled, false);
    assert.equal(row.state.runtime_ready, "unknown");
  }
});

test("validation rows cover requested and unrequested capabilities", () => {
  const validation = {
    contract_version: 2,
    requested: ["adaptive_planner"],
    decisions: {
      adaptive_planner: {
        requested: true,
        implemented: true,
        enabled: false,
        runtime_ready: "unknown",
        decision: "disabled_feature",
        reason: "feature gate disabled",
      },
    },
    execution_authority: "existing_runtime",
  };
  const rows = validationRows(validation);
  const adaptive = rows.find((row) => row.id === "adaptive_planner");
  const deterministic = rows.find((row) => row.id === "deterministic");
  assert.equal(adaptive.decision.decision, "disabled_feature");
  assert.equal(adaptive.decision.reason, "feature gate disabled");
  assert.equal(deterministic.decision.decision, "not_requested");
});

test("decision labels are truthful for disabled and allowed outcomes", () => {
  assert.equal(
    decisionLabel({ requested: true, enabled: false, decision: "disabled_feature" }),
    "Disabled by feature gate",
  );
  assert.equal(
    decisionLabel({ requested: true, enabled: true, decision: "allowed" }),
    "Allowed",
  );
  assert.equal(
    decisionLabel({ requested: false, enabled: false, decision: "not_requested" }),
    "Not requested",
  );
});

test("unknown decisions are never labelled as allowed", () => {
  const rows = validationRows({
    contract_version: 2,
    requested: ["adaptive_planner"],
    decisions: {
      adaptive_planner: {
        requested: true,
        enabled: false,
        runtime_ready: "unknown",
        decision: "disabled_feature",
      },
    },
    execution_authority: "existing_runtime",
  });
  for (const row of rows) {
    assert.notEqual(decisionLabel(row.decision), "Allowed");
  }
});

// M5.7 controlled enablement: control states, confirmation wording, and
// truthful not-ready handling (pure model only).
import {
  controlState,
  confirmationText,
  humanCapabilityLabel,
  SESSION_SCOPE_NOTE,
} from "../src/intelligencePanelModel.ts";

test("ready disabled capability offers enable", () => {
  const control = controlState({
    implemented: true,
    enabled: false,
    runtime_ready: "ready",
  });
  assert.equal(control.action, "enable");
  assert.equal(control.hint, "Enable available");
});

test("enabled capability offers disable without confirmation", () => {
  const control = controlState({
    implemented: true,
    enabled: true,
    runtime_ready: "ready",
  });
  assert.equal(control.action, "disable");
});

test("unknown readiness makes enable unavailable", () => {
  for (const ready of ["unknown", "not_ready", ""]) {
    const control = controlState({
      implemented: true,
      enabled: false,
      runtime_ready: ready,
    });
    assert.equal(control.action, "unavailable");
    assert.equal(control.hint, "Enable unavailable");
  }
});

test("unimplemented capability never offers a control", () => {
  const control = controlState({
    implemented: false,
    enabled: false,
    runtime_ready: "unknown",
  });
  assert.equal(control.action, "unavailable");
  assert.equal(control.hint, "Not implemented");
});

test("enable confirmation is deliberate, calm, and session-scoped", () => {
  const text = confirmationText("nano_agents", "Nano Agents");
  assert.match(text, /Enable Nano Agents for this AMCA session\?/);
  assert.match(text, /experimental/);
  assert.match(text, /governed parallel task execution/);
  assert.doesNotMatch(text, /danger|warning|WARNING|destructive/);
});

test("capability labels resolve for every listed capability", () => {
  for (const id of [
    "deterministic",
    "adaptive_planner",
    "hybrid_execution",
    "nano_agents",
    "model_router",
  ]) {
    assert.ok(humanCapabilityLabel(id).length > 0);
  }
  assert.equal(humanCapabilityLabel("mystery"), "mystery");
});

test("session scope is labeled truthfully", () => {
  assert.match(SESSION_SCOPE_NOTE, /saved and survives a restart/);
  assert.match(SESSION_SCOPE_NOTE, /fresh installation starts every gate OFF/);
  assert.match(SESSION_SCOPE_NOTE, /readiness/);
});

test("backend not_ready readiness renders truthfully", () => {
  assert.equal(readinessLabel("not_ready"), "Not ready");
});
