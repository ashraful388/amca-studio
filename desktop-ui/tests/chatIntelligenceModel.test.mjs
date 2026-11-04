// v0.6.0 M0.1 unit tests for the in-chat intelligence controls model
// (pure functions only - no browser, no fetch, no backend). Run with:
//   node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  ADAPTIVE_HYBRID_REQUIRED_GATES,
  adaptiveHybridGateView,
  buildDisableCall,
  buildEnableCalls,
  describeConfigureRejection,
  shouldShowAdaptiveHybridGateControls,
  shouldShowManualCollaborationControls,
} from "../src/chatIntelligenceModel.ts";

function capability(enabled, runtimeReady = "ready", implemented = true) {
  return { implemented, enabled, runtime_ready: runtimeReady };
}

// All required gates OFF but the runtimes are ready: a fresh backend where
// a real readiness probe verified both capabilities.
const ALL_OFF_READY = {
  adaptive_planner: capability(false),
  hybrid_execution: capability(false),
  nano_agents: capability(false),
  model_router: capability(false),
};

const ALL_ON_READY = {
  adaptive_planner: capability(true),
  hybrid_execution: capability(true),
  nano_agents: capability(false),
  model_router: capability(false),
};

// 1. Adaptive Hybrid selected + required gates OFF (+ ready) -> enable
//    affordance shown.
test("1. gates OFF and ready -> enable affordance shown", () => {
  const view = adaptiveHybridGateView(ALL_OFF_READY);
  assert.equal(view.phase, "enable-offered");
  assert.deepEqual(view.missing, ["adaptive_planner", "hybrid_execution"]);
  assert.match(view.message, /Enable them for this session\?/);
});

// 2. Selecting Adaptive Hybrid alone must not mutate gates: the view is a
//    read-only decision and carries no mutation payload at all.
test("2. view alone carries no mutation payload", () => {
  const view = adaptiveHybridGateView(ALL_OFF_READY);
  assert.ok(!("changes" in view));
  assert.ok(!("calls" in view));
  // Producing the view repeatedly (re-selecting the architecture) yields
  // the identical read-only decision.
  assert.deepEqual(adaptiveHybridGateView(ALL_OFF_READY), view);
});

// 3. Confirm enable produces calls to the EXISTING configure contract in
//    dependency-safe order.
test("3. confirm -> dependency-safe configure calls", () => {
  const calls = buildEnableCalls(["adaptive_planner", "hybrid_execution"]);
  assert.deepEqual(calls, [
    { contract_version: 1, changes: { adaptive_planner: true } },
    { contract_version: 1, changes: { hybrid_execution: true } },
  ]);
});

// 4. Cancel confirmation causes no mutation: with nothing missing the plan
//    is empty, and the offered view never pre-builds a payload.
test("4. cancel / nothing missing -> empty mutation plan", () => {
  assert.deepEqual(buildEnableCalls([]), []);
  const view = adaptiveHybridGateView(ALL_OFF_READY);
  assert.equal(view.phase, "enable-offered");
  assert.deepEqual(buildEnableCalls(view.missing).length, 2);
  // A cancel is the UI simply not calling buildEnableCalls; the model
  // exposes no implicit mutation path.
});

// 5. Ready dependencies enable successfully: after both configure calls the
//    re-read status shows both gates ON and the view flips to enabled.
test("5. enabled status -> view resolves to enabled", () => {
  const view = adaptiveHybridGateView(ALL_ON_READY);
  assert.equal(view.phase, "enabled");
  assert.deepEqual(view.missing, []);
});

// 6. Not-ready dependency cannot be enabled: no enable affordance.
test("6. not-ready runtime -> no misleading enable action", () => {
  const capabilities = {
    adaptive_planner: capability(false, "not_ready"),
    hybrid_execution: capability(false),
  };
  const view = adaptiveHybridGateView(capabilities);
  assert.equal(view.phase, "not-ready");
  assert.match(view.message, /not ready because a required intelligence runtime is unavailable/);
  assert.match(view.message, /Adaptive planning \(not_ready\)/);
});

// 7. Backend/API rejection is surfaced safely (bounded, user-safe).
test("7. rejections surface bounded user-safe text", () => {
  const active = describeConfigureRejection(
    new Error("active_execution: a run is active; gates cannot change now"),
  );
  assert.match(active, /Wait for it to finish/);
  const notReady = describeConfigureRejection(
    new Error("adaptive_planner cannot be enabled: runtime readiness is 'not_ready'"),
  );
  assert.match(notReady, /not ready/);
  const bootstrap = describeConfigureRejection(
    new Error(
      "mutation is prohibited: session was not established through the hardened bootstrap",
    ),
  );
  assert.match(bootstrap, /Restart AMCA from the desktop shell/);
  const raw = describeConfigureRejection(
    new Error("Traceback (most recent call last): very long internal ".repeat(20)),
  );
  assert.ok(!raw.includes("Traceback"));
  assert.ok(raw.length <= 260);
});

// 8. Successful enable removes/resolves the disabled warning: the enabled
//    view no longer offers anything and explains the session scope.
test("8. enabled view shows session-scoped note", () => {
  const view = adaptiveHybridGateView(ALL_ON_READY);
  assert.equal(view.phase, "enabled");
  assert.match(view.sessionNote, /resets to Off when AMCA restarts/);
});

// 9. Disable action returns exactly the required Adaptive/Hybrid-related
//    capabilities OFF via the same secure configure contract.
test("9. disable payload is one atomic call for the two gates", () => {
  assert.deepEqual(buildDisableCall(), {
    contract_version: 1,
    changes: { adaptive_planner: false, hybrid_execution: false },
  });
});

// 10. Nano is not automatically enabled (or disabled) by any payload.
test("10. nano_agents never appears in any payload", () => {
  for (const call of buildEnableCalls(ADAPTIVE_HYBRID_REQUIRED_GATES)) {
    assert.ok(!("nano_agents" in call.changes));
  }
  assert.ok(!("nano_agents" in buildDisableCall().changes));
});

// 11. Auto/Solo/Joint/Independent->Discuss/Parallel show no enable prompt.
test("11. gate controls only for adaptive_hybrid", () => {
  for (const mode of ["auto", "solo", "joint", "independent_discuss", "parallel"]) {
    assert.equal(shouldShowAdaptiveHybridGateControls(mode), false, mode);
  }
  assert.equal(shouldShowAdaptiveHybridGateControls("adaptive_hybrid"), true);
});

// 12. Fresh/restarted backend (all gates OFF) is treated as offered, never
//     enabled. (The backend restart-all-OFF contract itself is proven by
//     the M0 backend suite, test_13_restart_returns_all_experimental_gates_off.)
test("12. fresh all-OFF backend is enable-offered, never enabled", () => {
  const view = adaptiveHybridGateView(ALL_OFF_READY);
  assert.notEqual(view.phase, "enabled");
  assert.equal(view.phase, "enable-offered");
});

// 13. No session tokens/credentials/internal security data are rendered by
//     any user-facing message from this model.
test("13. no secrets in any rendered message", () => {
  const views = [
    adaptiveHybridGateView(null),
    adaptiveHybridGateView(ALL_OFF_READY),
    adaptiveHybridGateView(ALL_ON_READY),
    adaptiveHybridGateView({
      adaptive_planner: capability(false, "unknown"),
      hybrid_execution: capability(false, "not_ready"),
    }),
  ];
  const forbidden = /token|secret|api[_ -]?key|credential|X-AMCA-Session|hmac/i;
  for (const view of views) {
    assert.ok(!forbidden.test(view.message), view.message);
    assert.ok(view.message.length <= 300);
  }
});

// Extra: partial state (adaptive ON, hybrid OFF) enables only the missing
// gate — never a silent retry of the already-enabled one.
test("extra: partial enable state plans only the missing gate", () => {
  const capabilities = {
    adaptive_planner: capability(true),
    hybrid_execution: capability(false),
  };
  const view = adaptiveHybridGateView(capabilities);
  assert.equal(view.phase, "enable-offered");
  assert.deepEqual(view.missing, ["hybrid_execution"]);
  assert.deepEqual(buildEnableCalls(view.missing), [
    { contract_version: 1, changes: { hybrid_execution: true } },
  ]);
});

// v1.1.8 Phase 3: manual collaboration architectures are offered only for
// manual model selection.  Under Auto Intelligence the router owns the mode,
// so the selector must not appear (it used to be rendered and then discarded
// by the backend).
test("phase3: manual collaboration controls are hidden under Auto Intelligence", () => {
  assert.equal(shouldShowManualCollaborationControls("auto"), false);
  assert.equal(shouldShowManualCollaborationControls("auto_intelligence"), false);
});

test("phase3: manual collaboration controls are shown for manual selection", () => {
  assert.equal(shouldShowManualCollaborationControls("manual"), true);
  assert.equal(shouldShowManualCollaborationControls("manual_model_set"), true);
});

test("phase3: an unknown selection policy fails closed to hidden", () => {
  assert.equal(shouldShowManualCollaborationControls(""), false);
  assert.equal(shouldShowManualCollaborationControls("something_else"), false);
});
