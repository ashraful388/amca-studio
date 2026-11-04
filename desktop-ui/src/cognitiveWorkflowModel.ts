// Presentation model and pure transformation functions for the AMCA Studio
// Cognitive Workflow Loop UI (v0.8.0).
//
// Governed by AMCA Core Principles:
// 1. Observable, auditable lifecycle states only.
// 2. Strictly NO chain-of-thought, NO hidden reasoning exposure.
// 3. Explicit knowledge attachment disclosure.
// 4. No subjective AI confidence scores or self-ratings.

export type CognitiveWorkflowState =
  | "INITIALIZED"
  | "GOAL_UNDERSTOOD"
  | "PLANNED"
  | "RESOURCES_SELECTED"
  | "KNOWLEDGE_BOUND"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED_PLANNING"
  | "FAILED_RESOURCE_SELECTION"
  | "FAILED_KNOWLEDGE_ACCESS"
  | "FAILED_EXECUTION"
  | "CANCELLED";

export interface GoalUnderstandingRecord {
  goal_id: string;
  raw_goal: string;
  structured_intent: string;
  constraints: string[];
  domain: string;
  required_outputs: string[];
  timestamp: string;
}

export interface CognitivePlanRecord {
  plan_id: string;
  planned_steps: string[];
  required_capabilities: string[];
  strategy: string;
  dependencies: string[];
  timestamp: string;
}

export interface ModelAgentSelectionTrace {
  selection_id: string;
  selected_models: string[];
  selected_agents: string[];
  provider_health_references: string[];
  decision_reason: string;
  timestamp: string;
}

export interface KnowledgeBindingTrace {
  binding_id: string;
  query_intent: string;
  retrieved_artifact_ids: string[];
  permission_outcome: string;
  audit_event_ref: string;
  timestamp: string;
}

export interface VerificationRecord {
  verification_id: string;
  execution_completed: boolean;
  artifacts_produced: string[];
  validation_result: string;
  unresolved_issues: string[];
  timestamp: string;
}

export interface LearningSignalRecord {
  signal_id: string;
  workflow_id: string;
  execution_duration_ms: number;
  failure_count: number;
  retry_count: number;
  resource_metrics: Record<string, unknown>;
  user_correction_count: number;
  timestamp: string;
}

export interface CognitiveWorkflowRecord {
  workflow_id: string;
  workspace_id: string;
  state: CognitiveWorkflowState;
  goal?: GoalUnderstandingRecord | null;
  plan?: CognitivePlanRecord | null;
  selection?: ModelAgentSelectionTrace | null;
  knowledge?: KnowledgeBindingTrace | null;
  verification?: VerificationRecord | null;
  learning?: LearningSignalRecord | null;
  failure_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CognitiveStateBadge {
  status: "success" | "warning" | "error" | "info" | "neutral";
  label: string;
  isTerminal: boolean;
  isFailure: boolean;
}

export interface CognitiveTimelineStep {
  id: string;
  label: string;
  status: "completed" | "active" | "failed" | "pending";
  timestamp: string | null;
  detail: string;
}

export function resolveCognitiveStateBadge(state: CognitiveWorkflowState): CognitiveStateBadge {
  switch (state) {
    case "INITIALIZED":
      return { status: "neutral", label: "Initialized", isTerminal: false, isFailure: false };
    case "GOAL_UNDERSTOOD":
      return { status: "info", label: "Goal Understood", isTerminal: false, isFailure: false };
    case "PLANNED":
      return { status: "info", label: "Plan Formulated", isTerminal: false, isFailure: false };
    case "RESOURCES_SELECTED":
      return { status: "info", label: "Resources Selected", isTerminal: false, isFailure: false };
    case "KNOWLEDGE_BOUND":
      return { status: "info", label: "Knowledge Bound", isTerminal: false, isFailure: false };
    case "EXECUTING":
      return { status: "warning", label: "Executing", isTerminal: false, isFailure: false };
    case "COMPLETED":
      return { status: "success", label: "Completed", isTerminal: true, isFailure: false };
    case "FAILED_PLANNING":
      return { status: "error", label: "Planning Failed", isTerminal: true, isFailure: true };
    case "FAILED_RESOURCE_SELECTION":
      return { status: "error", label: "Resource Selection Failed", isTerminal: true, isFailure: true };
    case "FAILED_KNOWLEDGE_ACCESS":
      return { status: "error", label: "Knowledge Access Denied", isTerminal: true, isFailure: true };
    case "FAILED_EXECUTION":
      return { status: "error", label: "Execution Failed", isTerminal: true, isFailure: true };
    case "CANCELLED":
      return { status: "warning", label: "Cancelled", isTerminal: true, isFailure: false };
    default:
      return { status: "neutral", label: state || "Unknown", isTerminal: false, isFailure: false };
  }
}

const ORDERED_STEPS = [
  { id: "goal", key: "GOAL_UNDERSTOOD", label: "Understand Goal" },
  { id: "plan", key: "PLANNED", label: "Formulate Plan" },
  { id: "resources", key: "RESOURCES_SELECTED", label: "Select Resources" },
  { id: "knowledge", key: "KNOWLEDGE_BOUND", label: "Bind Knowledge" },
  { id: "execute", key: "EXECUTING", label: "Execute Workflow" },
  { id: "verify", key: "COMPLETED", label: "Verify & Complete" },
] as const;

export function buildCognitiveTimeline(workflow: CognitiveWorkflowRecord): CognitiveTimelineStep[] {
  const currentState = workflow.state;
  const isCancelled = currentState === "CANCELLED";

  let activeIndex = -1;
  if (currentState === "INITIALIZED") activeIndex = 0;
  else if (currentState === "GOAL_UNDERSTOOD") activeIndex = 1;
  else if (currentState === "PLANNED") activeIndex = 2;
  else if (currentState === "RESOURCES_SELECTED") activeIndex = 3;
  else if (currentState === "KNOWLEDGE_BOUND") activeIndex = 4;
  else if (currentState === "EXECUTING") activeIndex = 4;
  else if (currentState === "COMPLETED") activeIndex = 6;

  let failedIndex = -1;
  if (currentState === "FAILED_PLANNING") failedIndex = 1;
  else if (currentState === "FAILED_RESOURCE_SELECTION") failedIndex = 2;
  else if (currentState === "FAILED_KNOWLEDGE_ACCESS") failedIndex = 3;
  else if (currentState === "FAILED_EXECUTION") failedIndex = 4;
  else if (isCancelled) failedIndex = activeIndex >= 0 ? activeIndex : 0;

  return ORDERED_STEPS.map((step, idx) => {
    let status: "completed" | "active" | "failed" | "pending" = "pending";
    let timestamp: string | null = null;
    let detail = "";

    if (failedIndex === idx) {
      status = "failed";
      timestamp = workflow.updated_at || null;
      detail = workflow.failure_reason || "Encountered failure";
    } else if (failedIndex !== -1 && idx > failedIndex) {
      status = "pending";
      detail = "Skipped due to prior failure";
    } else if (idx < activeIndex || (currentState === "COMPLETED" && idx <= 5)) {
      status = "completed";
      if (step.id === "goal" && workflow.goal?.timestamp) {
        timestamp = workflow.goal.timestamp;
        detail = workflow.goal.structured_intent || "Goal analyzed";
      } else if (step.id === "plan" && workflow.plan?.timestamp) {
        timestamp = workflow.plan.timestamp;
        detail = `${workflow.plan.planned_steps.length} steps planned`;
      } else if (step.id === "resources" && workflow.selection?.timestamp) {
        timestamp = workflow.selection.timestamp;
        detail = `${workflow.selection.selected_models.length} model(s), ${workflow.selection.selected_agents.length} agent(s)`;
      } else if (step.id === "knowledge" && workflow.knowledge?.timestamp) {
        timestamp = workflow.knowledge.timestamp;
        detail = `${workflow.knowledge.retrieved_artifact_ids.length} knowledge artifact(s) bound`;
      } else if (step.id === "execute") {
        timestamp = workflow.updated_at || null;
        detail = "Execution dispatched";
      } else if (step.id === "verify" && workflow.verification?.timestamp) {
        timestamp = workflow.verification.timestamp;
        detail = workflow.verification.validation_result || "Verified";
      }
    } else if (idx === activeIndex) {
      status = "active";
      timestamp = workflow.updated_at || null;
      detail = "In progress";
    }

    return {
      id: step.id,
      label: step.label,
      status,
      timestamp,
      detail,
    };
  });
}

export interface TaskUnderstandingPresentation {
  hasData: boolean;
  goalId: string;
  rawGoal: string;
  structuredIntent: string;
  domain: string;
  constraints: string[];
  requiredOutputs: string[];
  timestamp: string | null;
}

export function formatTaskUnderstanding(
  goal?: GoalUnderstandingRecord | null
): TaskUnderstandingPresentation {
  if (!goal) {
    return {
      hasData: false,
      goalId: "—",
      rawGoal: "No goal registered yet",
      structuredIntent: "AWAITING_INPUT",
      domain: "general",
      constraints: [],
      requiredOutputs: [],
      timestamp: null,
    };
  }

  return {
    hasData: true,
    goalId: goal.goal_id || "—",
    rawGoal: goal.raw_goal || "",
    structuredIntent: goal.structured_intent || "UNKNOWN",
    domain: goal.domain || "general",
    constraints: Array.isArray(goal.constraints) ? [...goal.constraints] : [],
    requiredOutputs: Array.isArray(goal.required_outputs) ? [...goal.required_outputs] : [],
    timestamp: goal.timestamp || null,
  };
}

export interface PlanningTransparencyPresentation {
  hasData: boolean;
  planId: string;
  plannedSteps: string[];
  requiredCapabilities: string[];
  strategy: string;
  dependencies: string[];
  timestamp: string | null;
}

export function formatPlanningTransparency(
  plan?: CognitivePlanRecord | null
): PlanningTransparencyPresentation {
  if (!plan) {
    return {
      hasData: false,
      planId: "—",
      plannedSteps: [],
      requiredCapabilities: [],
      strategy: "none",
      dependencies: [],
      timestamp: null,
    };
  }

  return {
    hasData: true,
    planId: plan.plan_id || "—",
    plannedSteps: Array.isArray(plan.planned_steps) ? [...plan.planned_steps] : [],
    requiredCapabilities: Array.isArray(plan.required_capabilities) ? [...plan.required_capabilities] : [],
    strategy: plan.strategy || "standard",
    dependencies: Array.isArray(plan.dependencies) ? [...plan.dependencies] : [],
    timestamp: plan.timestamp || null,
  };
}

export interface FormattedModelSelection {
  name: string;
  locality: "Local Offline" | "Cloud BYOK" | "Local Custom";
  governanceStatus: "VERIFIED" | "HEALTHY" | "UNCHECKED";
}

export interface ResourceSelectionPresentation {
  hasData: boolean;
  selectionId: string;
  models: FormattedModelSelection[];
  agents: string[];
  providerHealthReferences: string[];
  decisionReason: string;
  timestamp: string | null;
}

export function formatResourceSelectionExplanation(
  selection?: ModelAgentSelectionTrace | null
): ResourceSelectionPresentation {
  if (!selection) {
    return {
      hasData: false,
      selectionId: "—",
      models: [],
      agents: [],
      providerHealthReferences: [],
      decisionReason: "No resources selected",
      timestamp: null,
    };
  }

  const models: FormattedModelSelection[] = (selection.selected_models || []).map((name) => {
    const isLocal =
      name.toLowerCase().includes("local") ||
      name.toLowerCase().includes("llama") ||
      name.toLowerCase().includes("qwen") ||
      name.toLowerCase().includes("ollama");
    return {
      name,
      locality: isLocal ? "Local Offline" : "Cloud BYOK",
      governanceStatus: "HEALTHY",
    };
  });

  return {
    hasData: true,
    selectionId: selection.selection_id || "—",
    models,
    agents: Array.isArray(selection.selected_agents) ? [...selection.selected_agents] : [],
    providerHealthReferences: Array.isArray(selection.provider_health_references)
      ? [...selection.provider_health_references]
      : [],
    decisionReason: selection.decision_reason || "Matched capabilities",
    timestamp: selection.timestamp || null,
  };
}

export interface KnowledgeUsagePresentation {
  hasData: boolean;
  bindingId: string;
  queryIntent: string;
  retrievedArtifactIds: string[];
  permissionOutcome: string;
  auditEventRef: string;
  isExplicit: boolean;
  timestamp: string | null;
}

export function formatKnowledgeUsage(
  knowledge?: KnowledgeBindingTrace | null
): KnowledgeUsagePresentation {
  if (!knowledge) {
    return {
      hasData: false,
      bindingId: "—",
      queryIntent: "No knowledge query requested",
      retrievedArtifactIds: [],
      permissionOutcome: "NOT_REQUESTED",
      auditEventRef: "—",
      isExplicit: true,
      timestamp: null,
    };
  }

  return {
    hasData: true,
    bindingId: knowledge.binding_id || "—",
    queryIntent: knowledge.query_intent || "",
    retrievedArtifactIds: Array.isArray(knowledge.retrieved_artifact_ids)
      ? [...knowledge.retrieved_artifact_ids]
      : [],
    permissionOutcome: knowledge.permission_outcome || "AUTHORIZED",
    auditEventRef: knowledge.audit_event_ref || "—",
    isExplicit: true,
    timestamp: knowledge.timestamp || null,
  };
}

export interface VerificationPresentation {
  hasData: boolean;
  verificationId: string;
  executionCompleted: boolean;
  artifactsProduced: string[];
  validationResult: string;
  unresolvedIssues: string[];
  timestamp: string | null;
}

export function formatVerificationResults(
  verification?: VerificationRecord | null
): VerificationPresentation {
  if (!verification) {
    return {
      hasData: false,
      verificationId: "—",
      executionCompleted: false,
      artifactsProduced: [],
      validationResult: "AWAITING_EXECUTION",
      unresolvedIssues: [],
      timestamp: null,
    };
  }

  return {
    hasData: true,
    verificationId: verification.verification_id || "—",
    executionCompleted: Boolean(verification.execution_completed),
    artifactsProduced: Array.isArray(verification.artifacts_produced)
      ? [...verification.artifacts_produced]
      : [],
    validationResult: verification.validation_result || "UNKNOWN",
    unresolvedIssues: Array.isArray(verification.unresolved_issues)
      ? [...verification.unresolved_issues]
      : [],
    timestamp: verification.timestamp || null,
  };
}

export interface LearningSignalsPresentation {
  hasData: boolean;
  signalId: string;
  executionDurationMs: number;
  failureCount: number;
  retryCount: number;
  resourceMetrics: Record<string, unknown>;
  userCorrectionCount: number;
  timestamp: string | null;
}

export function formatLearningSignals(
  learning?: LearningSignalRecord | null
): LearningSignalsPresentation {
  if (!learning) {
    return {
      hasData: false,
      signalId: "—",
      executionDurationMs: 0,
      failureCount: 0,
      retryCount: 0,
      resourceMetrics: {},
      userCorrectionCount: 0,
      timestamp: null,
    };
  }

  return {
    hasData: true,
    signalId: learning.signal_id || "—",
    executionDurationMs: learning.execution_duration_ms || 0,
    failureCount: learning.failure_count || 0,
    retryCount: learning.retry_count || 0,
    resourceMetrics: learning.resource_metrics ? { ...learning.resource_metrics } : {},
    userCorrectionCount: learning.user_correction_count || 0,
    timestamp: learning.timestamp || null,
  };
}

