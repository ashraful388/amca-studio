/**
 * AMCA Studio v0.9.0 Release Candidate UX and Documentation Audit Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V0.9.0-RELEASE-CANDIDATE-UX-AND-DOCUMENTATION-AUDIT-01
 *
 * Scope:
 * 1. User-Facing Capability Matrix (Available vs Unclaimed).
 * 2. Navigation surface audit results (Zero dead ends, zero inconsistent states).
 * 3. Release notes wording & boundary verifier.
 */

export const CANONICAL_TERMINOLOGY = {
  LOCAL_ONLY: "Local only",
  APPROVAL_REQUIRED: "Approval required",
  EXPLICITLY_ATTACHED: "Explicitly attached",
  NOT_RECORDED: "Not recorded",
  NO_AUTOMATIC_RETRIEVAL: "No automatic retrieval",
} as const;

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

export interface CapabilityEntry {
  id: string;
  name: string;
  category: "available" | "unclaimed";
  description: string;
  governanceContractRef: string;
}

export interface AuditedSurface {
  surfaceId: string;
  name: string;
  status: "AUDITED_CLEAN";
  navigationVerified: boolean;
  emptyStateVerified: boolean;
  guidanceAttached: boolean;
}

export function getCapabilityMatrix(): {
  available: CapabilityEntry[];
  unclaimed: CapabilityEntry[];
} {
  return {
    available: [
      {
        id: "cap-local-first",
        name: "Local-First Desktop Workflow",
        category: "available",
        description: "Local execution environment where code, prompts, and database state remain on the operator's machine.",
        governanceContractRef: "LOCAL_ONLY_INVARIANT",
      },
      {
        id: "cap-multi-model",
        name: "Multi-Model Collaboration",
        category: "available",
        description: "Governed peer-review and lead-agent coordination between multiple registered local or cloud models.",
        governanceContractRef: "AGENT_COLLABORATION_CONTRACT",
      },
      {
        id: "cap-governed-exec",
        name: "Governed Execution",
        category: "available",
        description: "Strict human approval gates on planning, resource allocation, and plan dispatch.",
        governanceContractRef: "OPERATOR_APPROVAL_REQUIRED",
      },
      {
        id: "cap-knowledge-assisted",
        name: "Knowledge-Assisted Workflows",
        category: "available",
        description: "Local hybrid retrieval over explicitly attached document collections with full provenance tracing.",
        governanceContractRef: "EXPLICIT_KNOWLEDGE_PROVENANCE",
      },
      {
        id: "cap-evidence-tracking",
        name: "Evidence Tracking & Transparency",
        category: "available",
        description: "Read-only decision transparency records and artifact hashes without hidden reasoning or subjective scores.",
        governanceContractRef: "AUDIT_PROVENANCE_INVARIANT",
      },
    ],
    unclaimed: [
      {
        id: "unclaimed-auto-employee",
        name: "Autonomous AI Employee",
        category: "unclaimed",
        description: "AMCA does not claim to replace human employees, conduct unsupervised work, or execute unreviewed decisions.",
        governanceContractRef: "COMMERCIAL_BOUNDARY_EXCLUSION_01",
      },
      {
        id: "unclaimed-unrestricted-auto",
        name: "Unrestricted Automation",
        category: "unclaimed",
        description: "AMCA does not support recursive, unbounded agent spawning or ungated background script execution.",
        governanceContractRef: "COMMERCIAL_BOUNDARY_EXCLUSION_02",
      },
      {
        id: "unclaimed-hidden-decisions",
        name: "Hidden Decision Making",
        category: "unclaimed",
        description: "AMCA does not generate private chain-of-thought diagnostics or synthetic subjective ranking scores.",
        governanceContractRef: "COMMERCIAL_BOUNDARY_EXCLUSION_03",
      },
      {
        id: "unclaimed-cloud-dependency",
        name: "Cloud-Only Dependency",
        category: "unclaimed",
        description: "AMCA does not require cloud SaaS subscriptions, centralized telemetry servers, or remote databases.",
        governanceContractRef: "COMMERCIAL_BOUNDARY_EXCLUSION_04",
      },
    ],
  };
}

export function getAuditedSurfaces(): AuditedSurface[] {
  return [
    {
      surfaceId: "dashboard",
      name: "Dashboard & Overview",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
    {
      surfaceId: "model_hub",
      name: "Model Hub",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
    {
      surfaceId: "knowledge_center",
      name: "Knowledge Center",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
    {
      surfaceId: "cognitive_workflow",
      name: "Cognitive Workflow Panel",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
    {
      surfaceId: "review_center",
      name: "Review Center",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
    {
      surfaceId: "artifact_center",
      name: "Artifact Center",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
    {
      surfaceId: "automation_center",
      name: "Automation Center",
      status: "AUDITED_CLEAN",
      navigationVerified: true,
      emptyStateVerified: true,
      guidanceAttached: true,
    },
  ];
}

export function verifyReleaseNotesWording(text: string): {
  verified: boolean;
  issues: string[];
} {
  const issues: string[] = [];
  const lower = text.toLowerCase();

  // 1. Check for forbidden hype terms in positive promotional claims
  const lines = text.split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (
      trimmed.startsWith("- **No ") ||
      trimmed.startsWith("No ") ||
      trimmed.toLowerCase().includes("does not claim") ||
      trimmed.toLowerCase().includes("not claimed") ||
      trimmed.toLowerCase().includes("exclusion")
    ) {
      // Explicit negative boundary disclaimers are allowed and encouraged
      continue;
    }
    const lineLower = line.toLowerCase();
    for (const term of FORBIDDEN_HYPE_TERMS) {
      if (lineLower.includes(term)) {
        issues.push(`Forbidden hype term detected: "${term}"`);
      }
    }
  }

  // 2. Check for required canonical governance terminology
  const requiredTerms = [
    CANONICAL_TERMINOLOGY.LOCAL_ONLY,
    CANONICAL_TERMINOLOGY.APPROVAL_REQUIRED,
    CANONICAL_TERMINOLOGY.EXPLICITLY_ATTACHED,
  ];

  for (const req of requiredTerms) {
    if (!lower.includes(req.toLowerCase())) {
      issues.push(`Missing canonical terminology: "${req}"`);
    }
  }

  return {
    verified: issues.length === 0,
    issues,
  };
}

