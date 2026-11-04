import React, { useState } from 'react';
import {
  ModelComparisonColumn,
  ManualSelectionDraft,
  validateManualSelectionDraft,
} from './modelSelectionWorkspaceModel';

interface ModelComparisonWorkspaceProps {
  columns?: ModelComparisonColumn[];
  currentProjectContext?: string;
  onConfirmManualSelection?: (draft: ManualSelectionDraft) => void;
}

export const ModelComparisonWorkspace: React.FC<ModelComparisonWorkspaceProps> = ({
  columns = [
    {
      modelId: 'ollama-llama3.1-8b',
      modelName: 'Llama 3.1 8B Instruct',
      provider: 'ollama',
      locality: 'local',
      version: 'q4_K_M',
      availabilityState: 'ready',
      verifiedCapabilities: ['code', 'chat'],
      unknownCapabilities: ['json_format'],
      userNotes: 'Local baseline for fast code assistance',
      measuredBenchmarks: [
        {
          taskCategory: 'coding',
          datasetReference: 'HumanEval subset',
          metricSummary: 'pass_at_1: 72.0% | latency: 210ms',
          verificationStatus: 'verified',
        },
      ],
    },
    {
      modelId: 'ollama-qwen2.5-coder-7b',
      modelName: 'Qwen 2.5 Coder 7B',
      provider: 'ollama',
      locality: 'local',
      version: 'q4_K_M',
      availabilityState: 'ready',
      verifiedCapabilities: ['code', 'refactoring'],
      unknownCapabilities: ['math'],
      userNotes: 'Local coding specialist for complex multi-file tasks',
      measuredBenchmarks: [
        {
          taskCategory: 'coding',
          datasetReference: 'HumanEval subset',
          metricSummary: 'pass_at_1: 79.5% | latency: 260ms',
          verificationStatus: 'verified',
        },
      ],
    },
  ],
  currentProjectContext = 'AMCA-Studio-Core',
  onConfirmManualSelection,
}) => {
  const [selectedModelId, setSelectedModelId] = useState<string>(columns[0]?.modelId || '');
  const [operatorRationale, setOperatorRationale] = useState<string>('');
  const [reviewedRefs, setReviewedRefs] = useState<string[]>(['benchmark:HumanEval subset']);
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleSelect = (modelId: string) => {
    setSelectedModelId(modelId);
  };

  const handleSubmit = () => {
    const draft: ManualSelectionDraft = {
      modelId: selectedModelId,
      projectContext: currentProjectContext,
      evidenceReferencesReviewed: reviewedRefs,
      operatorRationaleNotes: operatorRationale,
    };
    const res = validateManualSelectionDraft(draft);
    if (!res.valid) {
      setValidationError(res.error || 'Invalid selection draft');
      return;
    }
    setValidationError(null);
    onConfirmManualSelection?.(draft);
  };

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm p-6 space-y-6">
      {/* Header */}
      <div className="border-b border-slate-200 pb-4">
        <h2 className="text-lg font-semibold text-slate-900">Side-by-Side Model Comparison Workspace</h2>
        <p className="text-xs text-slate-500 mt-1">
          Compare empirical capability evidence and benchmarks across candidate models. All selections are strictly manual.
        </p>
      </div>

      {/* Comparison Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {columns.map((col) => {
          const isSelected = col.modelId === selectedModelId;
          return (
            <div
              key={col.modelId}
              className={`rounded border p-4 space-y-4 ${
                isSelected ? 'border-indigo-600 bg-indigo-50/20 ring-1 ring-indigo-600' : 'border-slate-200 bg-slate-50/30'
              }`}
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">{col.modelName}</h3>
                  <div className="text-[11px] text-slate-500 font-mono mt-0.5">{col.modelId}</div>
                </div>
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                    col.locality === 'local'
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-300'
                      : 'bg-amber-50 text-amber-700 border-amber-300'
                  }`}
                >
                  {col.locality === 'local' ? 'Local Daemon' : 'Cloud API'}
                </span>
              </div>

              {/* Attributes */}
              <div className="text-xs space-y-1.5 border-t border-slate-200 pt-3 text-slate-600">
                <div className="flex justify-between">
                  <span className="text-slate-400">Provider:</span>
                  <span className="font-medium text-slate-800">{col.provider}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Version:</span>
                  <span className="font-mono text-slate-700">{col.version}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Availability:</span>
                  <span className="text-emerald-700 font-medium">{col.availabilityState}</span>
                </div>
              </div>

              {/* Verified Capabilities */}
              <div className="border-t border-slate-200 pt-3 space-y-1">
                <div className="text-[11px] font-medium text-slate-700">Verified Capabilities:</div>
                <div className="flex flex-wrap gap-1">
                  {col.verifiedCapabilities.map((c) => (
                    <span key={c} className="px-1.5 py-0.5 bg-emerald-100 text-emerald-800 rounded text-[10px]">
                      {c}
                    </span>
                  ))}
                  {col.unknownCapabilities.map((c) => (
                    <span key={c} className="px-1.5 py-0.5 bg-slate-200 text-slate-600 rounded text-[10px]">
                      {c} (untested)
                    </span>
                  ))}
                </div>
              </div>

              {/* Empirical Benchmarks */}
              <div className="border-t border-slate-200 pt-3 space-y-1">
                <div className="text-[11px] font-medium text-slate-700">Measured Benchmarks:</div>
                {col.measuredBenchmarks.map((b, i) => (
                  <div key={i} className="text-[11px] p-2 bg-white rounded border border-slate-200">
                    <div className="font-medium text-slate-800">{b.taskCategory} ({b.datasetReference})</div>
                    <div className="text-slate-500 font-mono text-[10px] mt-0.5">{b.metricSummary}</div>
                  </div>
                ))}
              </div>

              {/* Selection Button */}
              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => handleSelect(col.modelId)}
                  className={`w-full py-1.5 text-xs font-medium rounded ${
                    isSelected
                      ? 'bg-indigo-600 text-white'
                      : 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-100'
                  }`}
                >
                  {isSelected ? 'Selected Candidate' : 'Choose for Comparison'}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Manual Selection Confirmation Panel */}
      <div className="bg-slate-50 rounded border border-slate-200 p-4 space-y-4">
        <h4 className="text-xs font-semibold text-slate-900 uppercase tracking-wider">
          Manual Operator Selection Confirmation ({currentProjectContext})
        </h4>
        <div className="space-y-2">
          <label className="block text-xs font-medium text-slate-700">
            Selected Model ID: <span className="font-mono text-indigo-700">{selectedModelId}</span>
          </label>
          <label className="block text-xs font-medium text-slate-700">
            Reviewed Evidence References (Comma-separated):
          </label>
          <input
            type="text"
            value={reviewedRefs.join(', ')}
            onChange={(e) =>
              setReviewedRefs(
                e.target.value
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
            className="w-full text-xs p-2 rounded border border-slate-300 bg-white font-mono"
          />
          <label className="block text-xs font-medium text-slate-700">
            Operator Selection Rationale (Required):
          </label>
          <textarea
            value={operatorRationale}
            onChange={(e) => setOperatorRationale(e.target.value)}
            placeholder="Document why this model was chosen for the current project (e.g. verified coding capability, local data privacy requirement)..."
            rows={2}
            className="w-full text-xs p-2 rounded border border-slate-300 bg-white"
          />
        </div>

        {validationError && (
          <div className="text-xs text-rose-600 bg-rose-50 p-2 rounded border border-rose-200">
            {validationError}
          </div>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            onClick={handleSubmit}
            className="px-4 py-2 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded shadow-sm"
          >
            Confirm Manual Model Selection
          </button>
        </div>
      </div>
    </div>
  );
};

