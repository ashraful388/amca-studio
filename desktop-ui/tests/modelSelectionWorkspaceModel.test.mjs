import test from 'node:test';
import assert from 'node:assert/strict';

import {
  validateManualSelectionDraft,
  auditZeroComparisonBadges,
  FORBIDDEN_COMPARISON_BADGES,
} from '../src/modelSelectionWorkspaceModel.ts';

test('validateManualSelectionDraft accepts complete valid draft', () => {
  const draft = {
    modelId: 'ollama-qwen2.5-coder-7b',
    projectContext: 'AMCA-Studio-Core',
    evidenceReferencesReviewed: ['benchmark:HumanEval', 'eval_run_99.log'],
    operatorRationaleNotes: 'Selected for offline code refactoring tasks',
  };
  const res = validateManualSelectionDraft(draft);
  assert.equal(res.valid, true);
});

test('validateManualSelectionDraft rejects missing model or empty context', () => {
  const noModel = {
    modelId: '',
    projectContext: 'AMCA-Studio-Core',
    evidenceReferencesReviewed: ['benchmark:HumanEval'],
    operatorRationaleNotes: 'Some notes',
  };
  assert.equal(validateManualSelectionDraft(noModel).valid, false);

  const noContext = {
    modelId: 'm1',
    projectContext: '',
    evidenceReferencesReviewed: ['benchmark:HumanEval'],
    operatorRationaleNotes: 'Some notes',
  };
  assert.equal(validateManualSelectionDraft(noContext).valid, false);
});

test('validateManualSelectionDraft rejects short or missing operator rationale notes', () => {
  const shortNotes = {
    modelId: 'm1',
    projectContext: 'P1',
    evidenceReferencesReviewed: ['ref1'],
    operatorRationaleNotes: 'abc', // < 5 chars
  };
  const res = validateManualSelectionDraft(shortNotes);
  assert.equal(res.valid, false);
  assert.ok(res.error.includes('at least 5 characters'));
});

test('auditZeroComparisonBadges flags winner or best pick badges', () => {
  const cleanBadges = ['Local Daemon', 'GGUF Quant', 'Ready'];
  assert.equal(auditZeroComparisonBadges(cleanBadges), true);

  const badBadges = ['Local Daemon', 'Overall Winner', 'Top Recommendation'];
  assert.equal(auditZeroComparisonBadges(badBadges), false);
});

test('forbidden comparison badges inventory has at least 5 strict entries', () => {
  assert.ok(FORBIDDEN_COMPARISON_BADGES.length >= 5);
});

