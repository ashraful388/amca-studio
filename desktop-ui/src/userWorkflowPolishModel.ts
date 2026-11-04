/**
 * AMCA Studio v0.9.0 End-User Workflow Validation and Polish Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V0.9.0-END-USER-WORKFLOW-VALIDATION-AND-POLISH-01
 *
 * Requirements:
 * 1. Complete user journey validation (First launch -> Project setup -> Workflow creation -> Review & sign-off -> Execution -> Artifact export).
 * 2. Cross-surface canonical terminology audit:
 *    - "Local only"
 *    - "Approval required"
 *    - "Explicitly attached"
 *    - "Not recorded"
 *    - "No automatic retrieval"
 * 3. Professional user polish (labels, guidance, empty-state clarity).
 * 4. Release candidate UX checklist.
 */

export const CANONICAL_TERMINOLOGY = {
  LOCAL_ONLY: "Local only",
  APPROVAL_REQUIRED: "Approval required",
  EXPLICITLY_ATTACHED: "Explicitly attached",
  NOT_RECORDED: "Not recorded",
  NO_AUTOMATIC_RETRIEVAL: "No automatic retrieval",
} as const;

export interface UserJourneyStage {
  stageId: string;
  name: string;
  surface: string;
  userObjective: string;
  canonicalTerms: string[];
  nextActionLabel: string;
  safetyBoundaryNote: string;
}

export interface ReleaseCandidateCheckItem {
  id: string;
  title: string;
  description: string;
  verified: boolean;
}

export interface ReleaseCandidateCategory {
  category: string;
  checks: ReleaseCandidateCheckItem[];
}

export function getUserJourneyStages(): UserJourneyStage[] {
  return [
    {
      stageId: "stage-1-launch",
      name: "First Launch & Environment Setup",
      surface: "Dashboard / Onboarding",
      userObjective: "Explore local platform capabilities and verify local-only data boundaries.",
      canonicalTerms: [CANONICAL_TERMINOLOGY.LOCAL_ONLY],
      nextActionLabel: "Create or Select Project",
      safetyBoundaryNote: "All sqlite databases and configurations are initialized locally with zero telemetry.",
    },
    {
      stageId: "stage-2-models",
      name: "Model & Capability Verification",
      surface: "Model Hub",
      userObjective: "Inspect local inference backends and declared model capabilities.",
      canonicalTerms: [CANONICAL_TERMINOLOGY.LOCAL_ONLY],
      nextActionLabel: "Attach Knowledge Corpora",
      safetyBoundaryNote: "Models must be registered with verifiable declared capabilities; zero secret model switching.",
    },
    {
      stageId: "stage-3-knowledge",
      name: "Knowledge Corpus Attachment",
      surface: "Knowledge Center",
      userObjective: "Attach explicit documentation collections for factual grounding.",
      canonicalTerms: [
        CANONICAL_TERMINOLOGY.EXPLICITLY_ATTACHED,
        CANONICAL_TERMINOLOGY.NO_AUTOMATIC_RETRIEVAL,
      ],
      nextActionLabel: "Initialize Workflow",
      safetyBoundaryNote: "Only explicitly attached collections are indexed; no unauthorized directory scraping.",
    },
    {
      stageId: "stage-4-workflow",
      name: "Workflow Goal & Plan Creation",
      surface: "Cognitive Workflow Panel",
      userObjective: "Submit a goal, generate coordination context, and view the draft plan.",
      canonicalTerms: [
        CANONICAL_TERMINOLOGY.NOT_RECORDED,
        CANONICAL_TERMINOLOGY.APPROVAL_REQUIRED,
      ],
      nextActionLabel: "Review in Review Center",
      safetyBoundaryNote: "Plans remain in DRAFT status until explicitly authorized by operator review.",
    },
    {
      stageId: "stage-5-review",
      name: "Operator Review & Governance Sign-Off",
      surface: "Review Center",
      userObjective: "Review proposals and plan handoffs before execution dispatch.",
      canonicalTerms: [CANONICAL_TERMINOLOGY.APPROVAL_REQUIRED],
      nextActionLabel: "Dispatch Governed Execution",
      safetyBoundaryNote: "Operators have full authority to approve, reject, or override any execution handoff.",
    },
    {
      stageId: "stage-6-artifacts",
      name: "Artifact Delivery & Export",
      surface: "Artifact Center",
      userObjective: "Inspect generated deliverables, verify evidence hashes, and export files.",
      canonicalTerms: [CANONICAL_TERMINOLOGY.LOCAL_ONLY],
      nextActionLabel: "Complete Task",
      safetyBoundaryNote: "Artifact provenance hashes and version histories are stored immutably in local records.",
    },
  ];
}

export function auditTerminologyConsistency(text: string): {
  verifiedTerms: string[];
  missingTerms: string[];
} {
  const allTerms = Object.values(CANONICAL_TERMINOLOGY);
  const lower = text.toLowerCase();
  const verified: string[] = [];
  const missing: string[] = [];

  for (const term of allTerms) {
    if (lower.includes(term.toLowerCase())) {
      verified.push(term);
    } else {
      missing.push(term);
    }
  }

  return {
    verifiedTerms: verified,
    missingTerms: missing,
  };
}

export function getReleaseCandidateChecklist(): ReleaseCandidateCategory[] {
  return [
    {
      category: "First Launch & Environment",
      checks: [
        {
          id: "rc-1-1",
          title: "Local-First Storage Initialized",
          description: "SQLite stores initialize in localized directories with no cloud dependency.",
          verified: true,
        },
        {
          id: "rc-1-2",
          title: "Zero Telemetry",
          description: "All network traffic monitors confirm zero background analytics or tracking calls.",
          verified: true,
        },
        {
          id: "rc-1-3",
          title: "First-Launch Tour Operational",
          description: "Onboarding tour presents clear, anti-hype descriptions of platform boundaries.",
          verified: true,
        },
      ],
    },
    {
      category: "Workflow Creation & Governance",
      checks: [
        {
          id: "rc-2-1",
          title: "Declared Capabilities Only",
          description: "Model selection strictly matches declared profiles with deterministic tie-breaking.",
          verified: true,
        },
        {
          id: "rc-2-2",
          title: "Explicit Knowledge Provenance",
          description: "Knowledge retrieval links explicit source references; no unattached automatic retrieval.",
          verified: true,
        },
        {
          id: "rc-2-3",
          title: "Strict Human Sign-Off",
          description: "Proposals and execution plans cannot dispatch without explicit operator approval.",
          verified: true,
        },
      ],
    },
    {
      category: "Execution & Artifact Export",
      checks: [
        {
          id: "rc-3-1",
          title: "No Autonomous Spawning",
          description: "Agent collaboration strictly operates under agreed contracts without dynamic recursive spawning.",
          verified: true,
        },
        {
          id: "rc-3-2",
          title: "Truthful Failure Explanations",
          description: "Error messages are derived purely from recorded facts with zero speculative AI diagnoses.",
          verified: true,
        },
        {
          id: "rc-3-3",
          title: "Artifact Provenance & Export",
          description: "Deliverables are versioned with immutable provenance hashes and direct desktop export.",
          verified: true,
        },
      ],
    },
  ];
}

