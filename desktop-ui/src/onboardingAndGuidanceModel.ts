/**
 * AMCA Studio v0.9.0 Professional Workflow Onboarding and Guidance Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V0.9.0-PROFESSIONAL-WORKFLOW-ONBOARDING-AND-GUIDANCE-UX-01
 *
 * Requirements:
 * 1. First-launch guidance flow (local-first, model selection, approval boundaries).
 * 2. Guided workflow creation (goal, task type, models, knowledge, governance review).
 * 3. Contextual help references (Model Hub, Knowledge Center, Cognitive Workflow, Review Center, Artifact Center, Automation Center).
 * 4. Empty-state guidance (what is missing, what user can do next, what AMCA does NOT do automatically).
 * 5. Strict anti-hype validation (zero claims of autonomous employee, swarm, or self-governing AI).
 */

export interface OnboardingStep {
  stepIndex: number;
  stepId: string;
  title: string;
  category: "intro" | "local_first" | "model_selection" | "approval_boundaries" | "workflow_creation";
  summary: string;
  details: string[];
  userActionLabel: string;
}

export interface ContextualHelpEntry {
  surfaceId: "model_hub" | "knowledge_center" | "cognitive_workflow" | "review_center" | "artifact_center" | "automation_center";
  title: string;
  summary: string;
  guidancePoints: string[];
  whatAmcaDoesNotDo: string;
}

export interface EmptyStateGuidance {
  surface: "projects" | "workflows" | "models" | "knowledge" | "artifacts";
  title: string;
  whatIsMissing: string;
  nextActionPrompt: string;
  whatAmcaDoesNotDo: string;
}

export const FORBIDDEN_HYPE_TERMS = [
  "autonomous ai employee",
  "autonomous employee",
  "unlimited agent swarm",
  "unlimited swarm",
  "replaces human judgment",
  "replaces human decision",
  "self-healing autonomous",
  "unsupervised intelligence",
];

export function getOnboardingSteps(): OnboardingStep[] {
  return [
    {
      stepIndex: 1,
      stepId: "step-intro",
      title: "Welcome to AMCA Studio",
      category: "intro",
      summary: "A local-first, governed multi-model collaborative cognition desktop platform.",
      details: [
        "AMCA Studio orchestrates multiple models and agents through explicit governance contracts.",
        "Your data and reasoning traces remain strictly on your local machine.",
        "No hidden background telemetry or unapproved network requests.",
      ],
      userActionLabel: "Explore Local Architecture",
    },
    {
      stepIndex: 2,
      stepId: "step-local-first",
      title: "Local-First & Privacy Architecture",
      category: "local_first",
      summary: "Run entirely offline or connect hybrid models with verifiable boundary enforcement.",
      details: [
        "Local inference engines (e.g. Ollama, llama.cpp) run with zero network egress.",
        "Cloud models are strictly gated by explicit provider authorization flags.",
        "All coordination context and logs are stored in local SQLite databases.",
      ],
      userActionLabel: "Review Model Governance",
    },
    {
      stepIndex: 3,
      stepId: "step-model-selection",
      title: "Model & Agent Capability Governance",
      category: "model_selection",
      summary: "Models and agents are matched based solely on declared, verifiable capabilities.",
      details: [
        "Zero subjective AI benchmark rankings or automated secret model switching.",
        "Deterministic capability matching ensures predictable and reproducible execution.",
        "Collaboration contracts establish lead agents and assigned peer reviewers explicitly.",
      ],
      userActionLabel: "Understand Approval Boundaries",
    },
    {
      stepIndex: 4,
      stepId: "step-approval-boundaries",
      title: "Strict Human Approval Boundaries",
      category: "approval_boundaries",
      summary: "Proposals and execution plans require explicit human operator authorization.",
      details: [
        "Recommendations are recorded as proposals requiring operator review and sign-off.",
        "Execution plans must be approved before reaching the ready-for-execution state.",
        "Operators retain full override, modification, and cancellation controls at all times.",
      ],
      userActionLabel: "Create First Workflow",
    },
    {
      stepIndex: 5,
      stepId: "step-workflow-creation",
      title: "Guided Workflow Creation",
      category: "workflow_creation",
      summary: "Structure your task, attach knowledge assets, and review the execution trace.",
      details: [
        "Define your goal with explicit domain and constraints.",
        "Attach local knowledge corpora for verifiable citation provenance.",
        "Monitor execution progress through the transparent cognitive timeline.",
      ],
      userActionLabel: "Get Started",
    },
  ];
}

export function getContextualHelp(
  surfaceId: "model_hub" | "knowledge_center" | "cognitive_workflow" | "review_center" | "artifact_center" | "automation_center"
): ContextualHelpEntry {
  const directory: Record<string, ContextualHelpEntry> = {
    model_hub: {
      surfaceId: "model_hub",
      title: "Model Hub & Provider Governance",
      summary: "Manage local and external inference backends with strict locality constraints.",
      guidancePoints: [
        "Register local model profiles with declared context windows and capabilities.",
        "Verify provider approval status before deploying models in collaborative workflows.",
        "Review locality affinities (LOCAL_ONLY vs HYBRID vs CLOUD_ALLOWED).",
      ],
      whatAmcaDoesNotDo: "AMCA does not automatically switch providers or route prompts without declared capability matches.",
    },
    knowledge_center: {
      surfaceId: "knowledge_center",
      title: "Knowledge Center & Hybrid Retrieval",
      summary: "Index local documentation and code corpora for deterministic fact retrieval.",
      guidancePoints: [
        "Attach markdown, PDF, or text files to build local searchable knowledge collections.",
        "Hybrid search combines exact keyword matching with semantic embedding indices.",
        "All retrieved snippets preserve auditable citation references and provenance IDs.",
      ],
      whatAmcaDoesNotDo: "AMCA does not upload your documents to external cloud storage or training pipelines.",
    },
    cognitive_workflow: {
      surfaceId: "cognitive_workflow",
      title: "Cognitive Workflow Trace",
      summary: "Inspect multi-step cognitive execution lifecycles with full factual transparency.",
      guidancePoints: [
        "Follow the 6-stage lifecycle: Goal -> Planning -> Resources -> Knowledge -> Verification -> Learning.",
        "Review timeline nodes for recorded execution milestones and error events.",
        "Inspect decision transparency and evidence provenance under dedicated trace tabs.",
      ],
      whatAmcaDoesNotDo: "AMCA does not record hidden chain-of-thought, private reasoning, or speculative AI self-diagnoses.",
    },
    review_center: {
      surfaceId: "review_center",
      title: "Review Center & Operator Sign-Off",
      summary: "Authorize or reject coordination proposals and execution plan handoffs.",
      guidancePoints: [
        "Review pending proposals for resource changes, reviewer additions, or plan strategies.",
        "Approve or reject items with recorded operator notes for complete audit compliance.",
        "Monitor operator override status and execution safety gate evaluations.",
      ],
      whatAmcaDoesNotDo: "AMCA does not bypass human operator review or auto-approve high-consequence plan modifications.",
    },
    artifact_center: {
      surfaceId: "artifact_center",
      title: "Artifact Center & Traceability",
      summary: "Track generated code, reports, and documentation artifacts with version history.",
      guidancePoints: [
        "Filter artifacts by type, creation date, source workflow, and memory links.",
        "Inspect artifact provenance hashes and linked evidence references.",
        "Export or open artifacts directly in your local desktop workspace.",
      ],
      whatAmcaDoesNotDo: "AMCA does not overwrite or delete previous artifact versions without operator consent.",
    },
    automation_center: {
      surfaceId: "automation_center",
      title: "Automation Center & Recurring Tasks",
      summary: "Configure scheduled and recurring workflows under controlled boundaries.",
      guidancePoints: [
        "Schedule cron-based or one-shot workflows for repetitive data or code tasks.",
        "Set strict execution limits, resource quotas, and timeout thresholds.",
        "Inspect past occurrence execution logs and error recovery reports.",
      ],
      whatAmcaDoesNotDo: "AMCA does not initiate autonomous recursive agent spawns or uncontrolled background tasks.",
    },
  };

  return directory[surfaceId] || directory.cognitive_workflow;
}

export function getEmptyStateGuidance(
  surface: "projects" | "workflows" | "models" | "knowledge" | "artifacts"
): EmptyStateGuidance {
  const states: Record<string, EmptyStateGuidance> = {
    projects: {
      surface: "projects",
      title: "No Projects Found",
      whatIsMissing: "No workspace projects have been created or registered in this session.",
      nextActionPrompt: "Click 'New Project' to create a localized directory workspace.",
      whatAmcaDoesNotDo: "AMCA does not automatically create or sync cloud projects without explicit user direction.",
    },
    workflows: {
      surface: "workflows",
      title: "No Active Workflows",
      whatIsMissing: "No cognitive workflows are currently running or recorded in this project.",
      nextActionPrompt: "Enter a task goal above to initialize a governed workflow loop.",
      whatAmcaDoesNotDo: "AMCA does not autonomously initiate background tasks or speculative workflows.",
    },
    models: {
      surface: "models",
      title: "No Models Configured",
      whatIsMissing: "No local or cloud model providers have been registered in the Model Hub.",
      nextActionPrompt: "Open Model Hub to detect your local Ollama instance or configure API keys.",
      whatAmcaDoesNotDo: "AMCA does not download weights or make network requests without operator authorization.",
    },
    knowledge: {
      surface: "knowledge",
      title: "No Knowledge Corpora Attached",
      whatIsMissing: "No document collections or code directories are currently indexed.",
      nextActionPrompt: "Attach documents in the Knowledge Center to enable factual retrieval.",
      whatAmcaDoesNotDo: "AMCA does not crawl external internet pages or unapproved local directories.",
    },
    artifacts: {
      surface: "artifacts",
      title: "No Artifacts Generated",
      whatIsMissing: "No output documents, code files, or reports have been produced by workflows.",
      nextActionPrompt: "Execute an approved workflow plan to generate project deliverables.",
      whatAmcaDoesNotDo: "AMCA does not generate unverified synthetic artifacts without a governed plan.",
    },
  };

  return states[surface] || states.workflows;
}

export function validateGuidanceClaims(text: string): { hasHype: boolean; forbiddenTerms: string[] } {
  const lower = text.toLowerCase();
  const matched: string[] = [];
  for (const term of FORBIDDEN_HYPE_TERMS) {
    if (lower.includes(term)) {
      matched.push(term);
    }
  }
  return {
    hasHype: matched.length > 0,
    forbiddenTerms: matched,
  };
}

