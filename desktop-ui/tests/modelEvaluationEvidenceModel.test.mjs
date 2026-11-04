import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatMetricSummary,
  auditNoRankingLanguage,
  FORBIDDEN_RANKING_TERMS,
} from '../src/modelEvaluationEvidenceModel.ts';

test('formatMetricSummary formats accuracy, latency, and tokens cleanly', () => {
  const metrics = {
    pass_rate: 0.854,
    latency_ms: 240,
    tokens_per_sec: 42.5,
  };
  const summary = formatMetricSummary(metrics);

  assert.ok(summary.includes('pass_rate: 85.4%'));
  assert.ok(summary.includes('latency_ms: 240ms'));
  assert.ok(summary.includes('tokens_per_sec: 42.5 t/s'));
});

test('auditNoRankingLanguage passes clean factual benchmark descriptions', () => {
  const factual = 'Measured latency of 220ms on HumanEval benchmark dataset using local Ollama instance.';
  const res = auditNoRankingLanguage(factual);

  assert.equal(res.compliant, true);
  assert.equal(res.violations.length, 0);
});

test('auditNoRankingLanguage flags winner and recommended model phrasing', () => {
  const biased = 'Declared winner and recommended model on the coding leaderboard.';
  const res = auditNoRankingLanguage(biased);

  assert.equal(res.compliant, false);
  assert.ok(res.violations.includes('winner'));
  assert.ok(res.violations.includes('recommended model'));
  assert.ok(res.violations.includes('leaderboard'));
});

test('auditNoRankingLanguage flags superiority claims', () => {
  const claim = 'Demonstrates clear superiority and automatic choice for production.';
  const res = auditNoRankingLanguage(claim);

  assert.equal(res.compliant, false);
  assert.ok(res.violations.includes('superiority'));
  assert.ok(res.violations.includes('automatic choice'));
});

test('forbidden ranking terms inventory has at least 8 strict entries', () => {
  assert.ok(FORBIDDEN_RANKING_TERMS.length >= 8);
});

