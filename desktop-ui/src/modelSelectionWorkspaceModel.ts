/**
 * AMCA Studio v1.0.0 Model Selection Workspace & Comparison UX Presentation Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-SELECTION-WORKSPACE-AND-COMPARISON-UX-01
 */

export interface ModelComparisonColumn {
  modelId: string;
  modelName: string;
  provider: string;
  locality: 'local' | 'cloud';
  version: string;
  availabilityState: string;
  verifiedCapabilities: string[];
  unknownCapabilities: string[];
  userNotes: string;
  measuredBenchmarks: Array<{
    taskCategory: string;
    datasetReference: string;
    metricSummary: string;
    verificationStatus: string;
  }>;
}

export interface ManualSelectionDraft {
  modelId: string;
  projectContext: string;
  evidenceReferencesReviewed: string[];
  operatorRationaleNotes: string;
  providerApprovalContext?: string;
}

export function validateManualSelectionDraft(draft: ManualSelectionDraft): { valid: boolean; error?: string } {
  if (!draft.modelId || draft.modelId.trim() === '') {
    return { valid: false, error: 'A valid model must be explicitly selected' };
  }
  if (!draft.projectContext || draft.projectContext.trim() === '') {
    return { valid: false, error: 'Project context is required' };
  }
  if (!draft.operatorRationaleNotes || draft.operatorRationaleNotes.trim().length < 5) {
    return { valid: false, error: 'Operator rationale notes must be at least 5 characters' };
  }
  if (!draft.evidenceReferencesReviewed || draft.evidenceReferencesReviewed.length === 0) {
    return { valid: false, error: 'At least one evidence reference must be marked as reviewed' };
  }
  return { valid: true };
}

export const FORBIDDEN_COMPARISON_BADGES = [
  'winner',
  'best pick',
  'overall winner',
  'top recommendation',
  'score leader',
  'champion',
];

export function auditZeroComparisonBadges(badges: string[]): boolean {
  for (const b of badges) {
    const lower = b.toLowerCase();
    if (FORBIDDEN_COMPARISON_BADGES.some((forbidden) => lower.includes(forbidden))) {
      return false;
    }
  }
  return true;
}

