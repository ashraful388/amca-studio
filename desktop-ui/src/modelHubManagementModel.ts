/**
 * AMCA Studio v1.0.0 Model Hub Management UX & Local Model Operations Presentation Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-MANAGEMENT-UX-AND-LOCAL-MODEL-OPERATIONS-01
 */

export type ModelWeightFormat = 'GGUF' | 'Safetensors' | 'AWQ' | 'CloudAPI';

export interface LocalModelOperationRecord {
  modelId: string;
  name: string;
  localPath: string;
  format: ModelWeightFormat;
  sizeBytes: number;
  runtimeEngine: 'ollama' | 'llamacpp' | 'vllm';
  runtimeAvailable: boolean;
  lastScannedAt: string;
}

export interface ProviderConnectionStatusRecord {
  providerId: string;
  providerType: 'ollama' | 'llamacpp' | 'vllm' | 'openai_compatible' | 'anthropic_compatible';
  status: 'connected' | 'disconnected' | 'unreachable';
  credentialFingerprint?: string; // e.g. "sk-...a1b2", NEVER raw API key
  lastManualVerificationAt: string;
  endpointUrl: string;
}

export interface CapabilityEvidenceRecord {
  modelId: string;
  capabilityTag: string;
  verificationStatus: 'verified' | 'unknown' | 'untested';
  evidenceSourceRef: string; // File path, audit log ID, or operator test run ID
  testedByOperator?: string;
  verifiedAt?: string;
}

export function formatWeightFormatBadge(format: ModelWeightFormat): { label: string; tone: string } {
  switch (format) {
    case 'GGUF':
      return { label: 'GGUF (Quantized)', tone: 'bg-blue-50 text-blue-700 border-blue-300' };
    case 'Safetensors':
      return { label: 'Safetensors (FP16/BF16)', tone: 'bg-purple-50 text-purple-700 border-purple-300' };
    case 'AWQ':
      return { label: 'AWQ (4-bit GPU)', tone: 'bg-indigo-50 text-indigo-700 border-indigo-300' };
    case 'CloudAPI':
      return { label: 'Cloud Hosted API', tone: 'bg-amber-50 text-amber-700 border-amber-300' };
  }
}

export function formatRuntimeAvailability(available: boolean): { label: string; tone: string } {
  if (available) {
    return { label: 'Runtime active', tone: 'text-emerald-700 bg-emerald-50 border-emerald-200' };
  }
  return { label: 'Runtime offline', tone: 'text-rose-700 bg-rose-50 border-rose-200' };
}

export function formatFileSizeGb(bytes: number): string {
  if (bytes <= 0) return '0 GB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

export function auditNoRankingOrScores(record: Record<string, any>): boolean {
  // Verifies that presentation models contain no leaderboards, subjective ranks, or synthetic scores
  const forbiddenKeys = ['rank', 'ranking', 'score', 'iq_score', 'benchmark_rank', 'best_model', 'overall_grade'];
  for (const k of Object.keys(record)) {
    if (forbiddenKeys.includes(k.toLowerCase())) {
      return false;
    }
  }
  return true;
}

export function auditProviderZeroSecrets(provider: ProviderConnectionStatusRecord): boolean {
  if (!provider.credentialFingerprint) return true;
  // Raw API keys typically start with sk- and have > 20 characters without ellipsis
  if (provider.credentialFingerprint.startsWith('sk-') && !provider.credentialFingerprint.includes('...')) {
    return false;
  }
  return true;
}

