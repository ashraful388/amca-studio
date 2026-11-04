// M5.2 Studio Intelligence UI - pure presentation model.
//
// All state derivation for the Intelligence panel lives here as pure
// functions so the changed surfaces stay unit-testable without a browser.
// No probing, no fetching, no execution: this module only shapes data that
// the v2 intelligence status/validation contracts already provide.

export interface CapabilityState {
  implemented: boolean;
  enabled: boolean;
  runtime_ready: string;
  /**
   * The backend's stated reason readiness is not "ready" - the dependency or
   * environment fact behind it.  Surfacing it is the difference between "this
   * setting cannot be enabled" and knowing why.
   */
  readiness_reason?: string;
}

export interface IntelligenceStatusShape {
  contract_version: number;
  seam_id: string;
  capability_contract_version: number;
  capabilities: Record<string, CapabilityState>;
  execution_authority: string;
}

export interface ValidationDecisionShape {
  requested: boolean;
  implemented?: boolean;
  enabled: boolean;
  runtime_ready?: string;
  available?: boolean;
  decision: string;
  reason?: string;
}

export interface IntelligenceValidationShape {
  contract_version: number;
  requested: string[];
  decisions: Record<string, ValidationDecisionShape>;
  execution_authority: string;
}

export const CAPABILITY_ORDER = [
  "deterministic",
  "adaptive_planner",
  "hybrid_execution",
  "nano_agents",
  "model_router",
] as const;

export const CAPABILITY_OPTIONS: Array<{ id: string; label: string }> = [
  { id: "deterministic", label: "Deterministic planner" },
  { id: "adaptive_planner", label: "Adaptive planning" },
  { id: "hybrid_execution", label: "Hybrid execution" },
  { id: "nano_agents", label: "Nano Agents" },
  { id: "model_router", label: "Model Router" },
];

export function capabilityRows(
  status: IntelligenceStatusShape,
): Array<{ id: string; state: CapabilityState }> {
  const capabilities = status?.capabilities ?? {};
  return CAPABILITY_ORDER.map((id) => ({
    id,
    state:
      capabilities[id] ?? {
        implemented: false,
        enabled: false,
        runtime_ready: "unknown",
      },
  }));
}

/**
 * The reason a capability cannot be enabled, or "" when there is none.
 *
 * A capability that is simply disabled by the user needs no explanation; one
 * that is enabled but not ready, or not ready at all, does.
 */
export function readinessReason(entry: CapabilityState): string {
  const reason = (entry.readiness_reason ?? "").trim();
  if (!reason) return "";
  if (entry.runtime_ready === "ready") return "";
  return reason;
}

export function readinessLabel(runtimeReady: string): string {
  if (runtimeReady === "ready") return "Ready";
  if (runtimeReady === "unknown") return "Readiness unknown";
  if (runtimeReady === "not_ready") return "Not ready";
  return `Readiness ${runtimeReady}`;
}

export function stateLabel(entry: CapabilityState): string {
  if (!entry.implemented) return "Not implemented";
  if (!entry.enabled) return "Implemented · disabled";
  return "Implemented · enabled";
}

export function validationRows(
  validation: IntelligenceValidationShape,
): Array<{ id: string; decision: ValidationDecisionShape }> {
  const decisions = validation?.decisions ?? {};
  const ids = new Set<string>([
    ...CAPABILITY_ORDER,
    ...Object.keys(decisions),
  ]);
  return Array.from(ids).map((id) => ({
    id,
    decision: decisions[id] ?? {
      requested: false,
      enabled: false,
      decision: "not_requested",
      reason: "capability was not part of the validation request",
    },
  }));
}

export function decisionLabel(decision: ValidationDecisionShape): string {
  const map: Record<string, string> = {
    allowed: "Allowed",
    disabled_feature: "Disabled by feature gate",
    unavailable_or_disabled: "Unavailable (disabled)",
    conflicting: "Conflicting request",
    not_requested: "Not requested",
  };
  return map[decision.decision] ?? decision.decision;
}

// M5.7 secure controlled enablement (session-scoped, hardened backend
// route only). Pure derivation so the panel cannot invent a control the
// backend would refuse.
export type ControlAction = "disable" | "enable" | "unavailable";

export function controlState(entry: CapabilityState): {
  action: ControlAction;
  hint: string;
} {
  if (!entry.implemented) {
    return { action: "unavailable", hint: "Not implemented" };
  }
  if (entry.enabled) {
    return { action: "disable", hint: "Disabling is always safe" };
  }
  if (entry.runtime_ready === "ready") {
    return { action: "enable", hint: "Enable available" };
  }
  return { action: "unavailable", hint: "Enable unavailable" };
}

export function confirmationText(id: string, label: string): string {
  const details: Record<string, string> = {
    nano_agents:
      "This experimental capability uses governed parallel task execution.",
    adaptive_planner:
      "This experimental capability changes planning behavior.",
    hybrid_execution:
      "This experimental capability changes planning behavior.",
    model_router: "This experimental capability changes model selection.",
  };
  return `Enable ${label} for this AMCA session? ${details[id] ?? "This experimental capability changes runtime behavior."}`;
}

export const SESSION_SCOPE_NOTE =
  "Your Enable/Disabled choice is saved and survives a restart. A fresh installation starts every gate OFF, and readiness checks still decide whether a gate can be enabled.";

const CAPABILITY_LABELS: Record<string, string> = {
  deterministic: "Deterministic planner",
  adaptive_planner: "Adaptive planning",
  hybrid_execution: "Hybrid execution",
  nano_agents: "Nano Agents",
  model_router: "Model Router",
};

export function humanCapabilityLabel(id: string): string {
  return CAPABILITY_LABELS[id] ?? id;
}
