import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatLocalityBadge,
  formatCredentialStatus,
  separateCapabilities,
  auditAntiHypeInNotes,
  FORBIDDEN_SUBJECTIVE_MARKETING_TERMS,
} from '../src/modelHubEcosystemModel.ts';

test('formatLocalityBadge handles local and cloud correctly', () => {
  const localBadge = formatLocalityBadge('local');
  assert.equal(localBadge.label, 'Local only');

  const cloudBadge = formatLocalityBadge('cloud');
  assert.equal(cloudBadge.label, 'Cloud API (Operator authorized)');
});

test('formatCredentialStatus masks raw credentials and shows fingerprint', () => {
  const unconfigured = formatCredentialStatus(false);
  assert.equal(unconfigured, 'No credential configured');

  const withFp = formatCredentialStatus(true, 'sk-...a1b2');
  assert.equal(withFp, 'Configured (sk-...a1b2)');

  const masked = formatCredentialStatus(true);
  assert.equal(masked, 'Configured (Masked)');
});

test('separateCapabilities partitions capabilities into verified and unknown', () => {
  const all = ['chat', 'code', 'json_format', 'reasoning'];
  const verified = ['chat', 'code'];
  const res = separateCapabilities(all, verified);

  assert.deepEqual(res.verified, ['chat', 'code']);
  assert.deepEqual(res.unknown, ['json_format', 'reasoning']);
});

test('auditAntiHypeInNotes flags subjective marketing and hype claims', () => {
  const clean = auditAntiHypeInNotes('Local 8B quant for code analysis and refactoring.');
  assert.equal(clean.compliant, true);
  assert.equal(clean.violations.length, 0);

  const hyped = auditAntiHypeInNotes('Best-in-class model with superhuman accuracy and infinite reasoning.');
  assert.equal(hyped.compliant, false);
  assert.ok(hyped.violations.includes('best-in-class'));
  assert.ok(hyped.violations.includes('superhuman'));
});

test('forbidden subjective marketing terms contains at least 5 strict entries', () => {
  assert.ok(FORBIDDEN_SUBJECTIVE_MARKETING_TERMS.length >= 5);
});

