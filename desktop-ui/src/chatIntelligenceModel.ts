// v0.6.0 M0.1 in-chat intelligence controls: pure presentation model.
//
// Adaptive Hybrid needs two experimental intelligence gates enabled for the
// CURRENT SESSION. This module decides what the Main Chat architecture
// selector should show and which exact mutation payloads the existing
// secure POST /api/intelligence/configure endpoint receives. It never talks
// to the network, never mutates anything by itself, and never touches
// nano_agents (not required by the Adaptive Hybrid chat architecture).

export type AdaptiveHybridGate = "adaptive_planner" | "hybrid_execution";

export const ADAPTIVE_HYBRID_REQUIRED_GATES: AdaptiveHybridGate[] = [
  "adaptive_planner",
  "hybrid_execution",
];

export interface GateCapabilityState {
  implemented: boolean;
  enabled: boolean;
  runtime_ready: string;
}

export interface ConfigureCall {
  contract_version: 1;
  changes: Record<string, boolean>;
}

export type GateViewPhase =
  | "checking"
  | "enabled"
  | "enable-offered"
  | "not-ready"
  | "error";

export interface GateView {
  phase: GateViewPhase;
  /** Gates currently OFF among the required ones (empty when enabled). */
  missing: AdaptiveHybridGate[];
  /** Human-bounded status line. No tokens, no internal security data. */
  message: string;
  /** Extra compact reminder that enablement is session-scoped. */
  sessionNote?: string;
}

export function shouldShowAdaptiveHybridGateControls(
  architectureMode: string,
): boolean {
  return architectureMode === "adaptive_hybrid";
}

/**
 * v1.1.8 Phase 3: the manual collaboration architectures (Solo, Joint,
 * Independent Discussion, Parallel, Adaptive Hybrid) belong to manual model
 * selection only.  Under Auto Intelligence the router owns the architecture,
 * so a mode selector offered there is a control the run discards.
 *
 * The chat composer names the two policies "manual"/"auto" and the Studio
 * composer names them "manual_model_set"/"auto_intelligence"; both express
 * the same rule.
 */
export function shouldShowManualCollaborationControls(
  selectionPolicy: string,
): boolean {
  return selectionPolicy === "manual" || selectionPolicy === "manual_model_set";
}

/**
 * The collaboration architectures the operator may choose, given how many
 * models are selected.
 *
 * The architecture is a property of the SELECTION, so the selector offers
 * only what the current selection can actually run:
 *
 * - ONE model: only "Solo". Every other architecture needs at least two
 *   participants, and offering them would offer a run that cannot start.
 * - MORE THAN ONE model: every multi-model architecture, and NOT "Solo" —
 *   Solo runs on exactly one model, so with two selected it would silently
 *   drop one of them.
 *
 * "Auto · AMCA decides" belongs to the multi-model set: the router picks
 * the architecture itself, which is the sensible default once the operator
 * has chosen a team.
 */
export type CollaborationArchitecture =
  | "auto"
  | "solo"
  | "joint"
  | "independent_discuss"
  | "parallel"
  | "adaptive_hybrid";

export const COLLABORATION_ARCHITECTURES: ReadonlyArray<{
  value: CollaborationArchitecture;
  label: string;
}> = [
  { value: "auto", label: "Auto · AMCA decides" },
  { value: "solo", label: "Solo" },
  { value: "joint", label: "Joint" },
  { value: "independent_discuss", label: "Independent → Discuss" },
  { value: "parallel", label: "Parallel" },
  { value: "adaptive_hybrid", label: "Adaptive Hybrid" },
];

export function collaborationArchitectureOptions(
  selectedModelCount: number,
): CollaborationArchitecture[] {
  const count = Math.max(0, Number(selectedModelCount) || 0);
  if (count <= 1) {
    return ["solo"];
  }
  return COLLABORATION_ARCHITECTURES
    .map((entry) => entry.value)
    .filter((value) => value !== "solo");
}

export function describeGate(gate: AdaptiveHybridGate): string {
  return gate === "adaptive_planner" ? "Adaptive planning" : "Hybrid execution";
}

function missingRequiredGates(
  capabilities: Record<string, GateCapabilityState>,
): AdaptiveHybridGate[] {
  return ADAPTIVE_HYBRID_REQUIRED_GATES.filter((gate) => {
    const state = capabilities[gate];
    return !state || state.enabled !== true;
  });
}

/**
 * Read-only view of the current intelligence status for the Adaptive
 * Hybrid selector note. Selecting Adaptive Hybrid only ever CALLS this; a
 * mutation payload is produced exclusively by buildEnableCalls /
 * buildDisableCall for an explicit user-confirmed action.
 */
export function adaptiveHybridGateView(
  capabilities: Record<string, GateCapabilityState> | null | undefined,
): GateView {
  if (!capabilities) {
    return { phase: "checking", missing: [], message: "Checking intelligence gate state…" };
  }
  const missing = missingRequiredGates(capabilities);
  if (missing.length === 0) {
    return {
      phase: "enabled",
      missing: [],
      message:
        "Adaptive Hybrid is enabled for this session: a gated local planner advises phase 0; deterministic supervision stays authoritative.",
      sessionNote: "Experimental intelligence resets to Off when AMCA restarts.",
    };
  }
  const notReady = missing.filter((gate) => {
    const state = capabilities[gate];
    return !state || !state.implemented || state.runtime_ready !== "ready";
  });
  if (notReady.length > 0) {
    const reason = notReady
      .map((gate) => {
        const state = capabilities[gate];
        const detail =
          state && typeof state.runtime_ready === "string"
            ? state.runtime_ready
            : "unknown";
        return `${describeGate(gate)} (${detail})`;
      })
      .join(", ");
    return {
      phase: "not-ready",
      missing,
      message: `Adaptive Hybrid is not ready because a required intelligence runtime is unavailable: ${reason}. Enablement is not offered while readiness is not verified.`,
    };
  }
  const label = missing.map(describeGate).join(" + ");
  return {
    phase: "enable-offered",
    missing,
    message: `Adaptive Hybrid requires experimental intelligence capabilities that are currently disabled (${label}). Enable them for this session?`,
    sessionNote: "Experimental intelligence resets to Off when AMCA restarts.",
  };
}

/**
 * Dependency-safe enable sequence for the missing gates. The M5.7
 * controller never auto-enables dependencies inside one payload, so the
 * sequence is one call per gate in dependency order. Nano is never
 * included.
 */
export function buildEnableCalls(
  missing: AdaptiveHybridGate[],
): ConfigureCall[] {
  return ADAPTIVE_HYBRID_REQUIRED_GATES.filter((gate) => missing.includes(gate)).map(
    (gate) => ({
      contract_version: 1 as const,
      changes: { [gate]: true },
    }),
  );
}

/**
 * One atomic disable call for the required gates only. The controller
 * accepts combined disable in a single payload and never touches
 * nano_agents (which may have been enabled independently elsewhere).
 */
export function buildDisableCall(): ConfigureCall {
  return {
    contract_version: 1 as const,
    changes: { adaptive_planner: false, hybrid_execution: false },
  };
}

/** Bounded, user-safe text for a backend rejection. No raw dumps. */
export function describeConfigureRejection(error: unknown): string {
  const raw =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const compact = raw.replace(/\s+/g, " ").trim().slice(0, 200);
  if (/active_execution|run is active/i.test(compact)) {
    return "Intelligence gates cannot change while a run is active. Wait for it to finish, then try again.";
  }
  if (/not_ready|readiness/i.test(compact)) {
    return "The intelligence runtime is not ready, so the gate change was refused. Check the local model runtime and try again.";
  }
  if (/bootstrap|mutation is prohibited|insecure/i.test(compact)) {
    return "This session cannot change intelligence gates securely. Restart AMCA from the desktop shell and try again.";
  }
  // Never echo internal dumps (tracebacks, file paths, stack frames) to the
  // user; only short, plain backend rejections pass through verbatim.
  const internalDump = /Traceback|File "|at line|node:internal|\{"/.test(compact);
  return !internalDump && compact.length > 0 && compact.length <= 160
    ? `The gate change was refused: ${compact}`
    : "The gate change was refused by the backend.";
}
