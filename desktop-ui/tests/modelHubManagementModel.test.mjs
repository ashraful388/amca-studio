import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatWeightFormatBadge,
  formatRuntimeAvailability,
  formatFileSizeGb,
  auditNoRankingOrScores,
  auditProviderZeroSecrets,
} from '../src/modelHubManagementModel.ts';

test('formatWeightFormatBadge formats all supported local and cloud weight formats', () => {
  const gguf = formatWeightFormatBadge('GGUF');
  assert.equal(gguf.label, 'GGUF (Quantized)');

  const safetensors = formatWeightFormatBadge('Safetensors');
  assert.equal(safetensors.label, 'Safetensors (FP16/BF16)');

  const awq = formatWeightFormatBadge('AWQ');
  assert.equal(awq.label, 'AWQ (4-bit GPU)');

  const cloud = formatWeightFormatBadge('CloudAPI');
  assert.equal(cloud.label, 'Cloud Hosted API');
});

test('formatRuntimeAvailability reflects online/offline status truthfully', () => {
  const active = formatRuntimeAvailability(true);
  assert.equal(active.label, 'Runtime active');

  const offline = formatRuntimeAvailability(false);
  assert.equal(offline.label, 'Runtime offline');
});

test('formatFileSizeGb calculates gigabyte representations accurately', () => {
  assert.equal(formatFileSizeGb(0), '0 GB');
  assert.equal(formatFileSizeGb(4920000000), '4.58 GB');
});

test('auditNoRankingOrScores enforces elimination of synthetic ranks and leaderboards', () => {
  const valid = { modelName: 'llama-3.1', sizeBytes: 4000000000, provider: 'ollama' };
  assert.equal(auditNoRankingOrScores(valid), true);

  const ranked = { modelName: 'llama-3.1', benchmark_rank: 1, best_model: true };
  assert.equal(auditNoRankingOrScores(ranked), false);
});

test('auditProviderZeroSecrets confirms raw API keys never leak into status fingerprints', () => {
  const safeProvider = {
    providerId: 'p1',
    providerType: 'openai_compatible',
    status: 'connected',
    credentialFingerprint: 'sk-...a1b2',
    lastManualVerificationAt: '2026-09-02',
    endpointUrl: 'https://api.openai.com',
  };
  assert.equal(auditProviderZeroSecrets(safeProvider), true);

  const leakingProvider = {
    providerId: 'p2',
    providerType: 'openai_compatible',
    status: 'connected',
    credentialFingerprint: 'sk-abcdef1234567890abcdef123456',
    lastManualVerificationAt: '2026-09-02',
    endpointUrl: 'https://api.openai.com',
  };
  assert.equal(auditProviderZeroSecrets(leakingProvider), false);
});

