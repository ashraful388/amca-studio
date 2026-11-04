/**
 * AMCA Studio v1.0.0 Model Evaluation Evidence Presentation Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-EVALUATION-EVIDENCE-AND-BENCHMARKING-FOUNDATION-01
 */

export interface ModelEvaluationEvidenceCard {
  recordId: string;
  modelId: string;
  provider: string;
  locality: 'local' | 'cloud';
  taskCategory: 'coding' | 'reasoning' | 'document_processing' | 'domain_specific' | 'latency_benchmark';
  datasetReference: string;
  testConditions: string;
  measuredMetrics: Record<string, number | string>;
  rawOutputArtifactRef: string;
  verificationStatus: 'verified' | 'unverified' | 'disputed';
  operatorVerifier: string;
  timestamp: string;
}

export function formatMetricSummary(metrics: Record<string, number | string>): string {
  const parts: string[] = [];
  for (const [key, val] of Object.entries(metrics)) {
    if (typeof val === 'number') {
      if (key.includes('rate') || key.includes('accuracy')) {
        parts.push(`${key}: ${(val * 100).toFixed(1)}%`);
      } else if (key.includes('latency') || key.includes('ms')) {
        parts.push(`${key}: ${val}ms`);
      } else if (key.includes('tokens_per_sec')) {
        parts.push(`${key}: ${val.toFixed(1)} t/s`);
      } else {
        parts.push(`${key}: ${val}`);
      }
    } else {
      parts.push(`${key}: ${val}`);
    }
  }
  return parts.join(' | ');
}

export const FORBIDDEN_RANKING_TERMS = [
  'best model',
  'recommended model',
  'winner',
  'automatic choice',
  'superiority',
  'leaderboard',
  'champion',
  'top-ranked',
  'number one',
];

export function auditNoRankingLanguage(text: string): { compliant: boolean; violations: string[] } {
  const lower = text.toLowerCase();
  const violations = FORBIDDEN_RANKING_TERMS.filter((term) => lower.includes(term));
  return {
    compliant: violations.length === 0,
    violations,
  };
}

