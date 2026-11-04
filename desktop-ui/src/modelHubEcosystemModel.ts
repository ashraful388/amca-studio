/**
 * AMCA Studio v1.0.0 Model Hub Ecosystem Presentation Model.
 *
 * Milestone: ZCODE-AMCA-STUDIO-V1.0.0-MODEL-HUB-ECOSYSTEM-EXPANSION-FOUNDATION-01
 */

export interface EcosystemModelCard {
  modelId: string;
  modelName: string;
  provider: 'ollama' | 'llamacpp' | 'vllm' | 'openai_compatible' | 'anthropic_compatible';
  locality: 'local' | 'cloud';
  version: string;
  capabilityTags: string[];
  availabilityState: 'ready' | 'offline' | 'unreachable';
  userNotes: string;
  verifiedCapabilities: string[];
  unknownCapabilities: string[];
  evidenceSource: string;
}

export interface ProviderConnectionCard {
  providerId: string;
  providerType: string;
  endpointUrl: string;
  hasCredential: boolean;
  credentialFingerprint?: string;
  status: 'connected' | 'disconnected' | 'unreachable';
}

export function formatLocalityBadge(locality: 'local' | 'cloud'): { label: string; tone: string } {
  if (locality === 'local') {
    return { label: 'Local only', tone: 'bg-emerald-50 text-emerald-700 border-emerald-300' };
  }
  return { label: 'Cloud API (Operator authorized)', tone: 'bg-amber-50 text-amber-700 border-amber-300' };
}

export function formatCredentialStatus(hasCredential: boolean, fingerprint?: string): string {
  if (!hasCredential) {
    return 'No credential configured';
  }
  if (fingerprint) {
    return `Configured (${fingerprint})`;
  }
  return 'Configured (Masked)';
}

export function separateCapabilities(
  allCapabilities: string[],
  verifiedCapabilities: string[],
): { verified: string[]; unknown: string[] } {
  const verified = allCapabilities.filter((c) => verifiedCapabilities.includes(c));
  const unknown = allCapabilities.filter((c) => !verifiedCapabilities.includes(c));
  return { verified, unknown };
}

export const FORBIDDEN_SUBJECTIVE_MARKETING_TERMS = [
  'best-in-class',
  'superior intelligence',
  'smartest model',
  'superhuman',
  'unmatched accuracy',
  'infinite reasoning',
  'flawless generation',
  'world-leading',
];

export function auditAntiHypeInNotes(userNotes: string): { compliant: boolean; violations: string[] } {
  const lower = userNotes.toLowerCase();
  const violations = FORBIDDEN_SUBJECTIVE_MARKETING_TERMS.filter((term) => lower.includes(term));
  return {
    compliant: violations.length === 0,
    violations,
  };
}

