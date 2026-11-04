import React, { useState } from 'react';
import {
  LocalModelOperationRecord,
  ProviderConnectionStatusRecord,
  CapabilityEvidenceRecord,
  formatWeightFormatBadge,
  formatRuntimeAvailability,
  formatFileSizeGb,
} from './modelHubManagementModel';
import { EcosystemModelCard, formatLocalityBadge, formatCredentialStatus } from './modelHubEcosystemModel';

interface ModelHubManagementPanelProps {
  models?: EcosystemModelCard[];
  localOperations?: LocalModelOperationRecord[];
  providers?: ProviderConnectionStatusRecord[];
  evidenceRecords?: CapabilityEvidenceRecord[];
  onRefreshLocalModels?: () => void;
  onVerifyProvider?: (providerId: string) => void;
}

export const ModelHubManagementPanel: React.FC<ModelHubManagementPanelProps> = ({
  models = [
    {
      modelId: 'ollama-llama3.1-8b',
      modelName: 'Llama 3.1 8B Instruct',
      provider: 'ollama',
      locality: 'local',
      version: 'q4_K_M',
      capabilityTags: ['chat', 'code', 'json_format'],
      availabilityState: 'ready',
      userNotes: 'General-purpose local reasoning model',
      verifiedCapabilities: ['chat', 'code'],
      unknownCapabilities: ['json_format'],
      evidenceSource: 'operator_eval_2026_08',
    },
  ],
  localOperations = [
    {
      modelId: 'ollama-llama3.1-8b',
      name: 'llama3.1:8b-instruct-q4_K_M',
      localPath: 'C:\\Users\\user\\.ollama\\models\\blobs\\sha256-abc123',
      format: 'GGUF',
      sizeBytes: 4920000000,
      runtimeEngine: 'ollama',
      runtimeAvailable: true,
      lastScannedAt: '2026-09-02 23:30 UTC',
    },
  ],
  providers = [
    {
      providerId: 'local-ollama-daemon',
      providerType: 'ollama',
      status: 'connected',
      lastManualVerificationAt: '2026-09-02 23:00 UTC',
      endpointUrl: 'http://127.0.0.1:11434',
    },
    {
      providerId: 'azure-openai-fallback',
      providerType: 'openai_compatible',
      status: 'disconnected',
      credentialFingerprint: 'sk-...a1b2',
      lastManualVerificationAt: '2026-08-28 10:00 UTC',
      endpointUrl: 'https://operator-proxy.corp.internal/v1',
    },
  ],
  evidenceRecords = [
    {
      modelId: 'ollama-llama3.1-8b',
      capabilityTag: 'code',
      verificationStatus: 'verified',
      evidenceSourceRef: 'tests/benchmarks/eval_run_99.log',
      testedByOperator: 'Operator QA',
      verifiedAt: '2026-08-30',
    },
    {
      modelId: 'ollama-llama3.1-8b',
      capabilityTag: 'json_format',
      verificationStatus: 'unknown',
      evidenceSourceRef: 'None recorded (daemon tag only)',
    },
  ],
  onRefreshLocalModels,
  onVerifyProvider,
}) => {
  const [activeTab, setActiveTab] = useState<'registered' | 'local_ops' | 'providers' | 'evidence'>('registered');

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-200 pb-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Model Hub Management & Operations</h2>
          <p className="text-xs text-slate-500 mt-1">
            Local model registry, daemon discovery, credential privacy, and capability evidence tracking.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRefreshLocalModels}
            className="px-3 py-1.5 text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 rounded border border-slate-300"
          >
            Scan Local Models
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-200 gap-4 text-xs font-medium">
        <button
          type="button"
          onClick={() => setActiveTab('registered')}
          className={`pb-2 border-b-2 ${
            activeTab === 'registered'
              ? 'border-indigo-600 text-indigo-600'
              : 'border-transparent text-slate-500 hover:text-slate-800'
          }`}
        >
          Registered Models ({models.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('local_ops')}
          className={`pb-2 border-b-2 ${
            activeTab === 'local_ops'
              ? 'border-indigo-600 text-indigo-600'
              : 'border-transparent text-slate-500 hover:text-slate-800'
          }`}
        >
          Local Model Operations ({localOperations.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('providers')}
          className={`pb-2 border-b-2 ${
            activeTab === 'providers'
              ? 'border-indigo-600 text-indigo-600'
              : 'border-transparent text-slate-500 hover:text-slate-800'
          }`}
        >
          Provider Connections ({providers.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('evidence')}
          className={`pb-2 border-b-2 ${
            activeTab === 'evidence'
              ? 'border-indigo-600 text-indigo-600'
              : 'border-transparent text-slate-500 hover:text-slate-800'
          }`}
        >
          Capability Evidence Panel ({evidenceRecords.length})
        </button>
      </div>

      {/* Registered Models View */}
      {activeTab === 'registered' && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                <th className="py-2 px-3">Model Name</th>
                <th className="py-2 px-3">Locality</th>
                <th className="py-2 px-3">Provider</th>
                <th className="py-2 px-3">Version / Quant</th>
                <th className="py-2 px-3">Verified Capabilities</th>
                <th className="py-2 px-3">User Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {models.map((m) => {
                const locBadge = formatLocalityBadge(m.locality);
                return (
                  <tr key={m.modelId} className="hover:bg-slate-50/50">
                    <td className="py-2.5 px-3 font-medium text-slate-900">{m.modelName}</td>
                    <td className="py-2.5 px-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold border ${locBadge.tone}`}>
                        {locBadge.label}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-slate-600">{m.provider}</td>
                    <td className="py-2.5 px-3 text-slate-500">{m.version}</td>
                    <td className="py-2.5 px-3">
                      <div className="flex flex-wrap gap-1">
                        {m.verifiedCapabilities.map((cap) => (
                          <span key={cap} className="px-1.5 py-0.5 bg-emerald-50 text-emerald-700 rounded text-[10px]">
                            {cap}
                          </span>
                        ))}
                        {m.unknownCapabilities.map((cap) => (
                          <span key={cap} className="px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded text-[10px]" title="Unverified tag">
                            {cap} (?)
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-2.5 px-3 text-slate-500 italic max-w-xs truncate">{m.userNotes || '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Local Operations View */}
      {activeTab === 'local_ops' && (
        <div className="space-y-4">
          <div className="p-3 bg-amber-50 rounded border border-amber-200 text-xs text-amber-800">
            <strong>Local Model Governance Policy:</strong> Discovered weights are inspected on the local filesystem. AMCA never performs automatic model downloading, activation, or silent routing changes.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                  <th className="py-2 px-3">Discovered Identifier</th>
                  <th className="py-2 px-3">Weight Format</th>
                  <th className="py-2 px-3">Runtime Engine</th>
                  <th className="py-2 px-3">Size</th>
                  <th className="py-2 px-3">Runtime Availability</th>
                  <th className="py-2 px-3">Local Filesystem Path</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {localOperations.map((loc) => {
                  const fmtBadge = formatWeightFormatBadge(loc.format);
                  const runBadge = formatRuntimeAvailability(loc.runtimeAvailable);
                  return (
                    <tr key={loc.modelId} className="hover:bg-slate-50/50">
                      <td className="py-2.5 px-3 font-medium text-slate-900">{loc.name}</td>
                      <td className="py-2.5 px-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold border ${fmtBadge.tone}`}>
                          {fmtBadge.label}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-slate-600">{loc.runtimeEngine}</td>
                      <td className="py-2.5 px-3 text-slate-600">{formatFileSizeGb(loc.sizeBytes)}</td>
                      <td className="py-2.5 px-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold border ${runBadge.tone}`}>
                          {runBadge.label}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-slate-400 font-mono text-[10px] max-w-xs truncate">{loc.localPath}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Provider Connections View */}
      {activeTab === 'providers' && (
        <div className="space-y-4">
          <div className="p-3 bg-slate-50 rounded border border-slate-200 text-xs text-slate-700">
            <strong>Credential Privacy Invariant:</strong> Raw secrets, passwords, and API keys are strictly masked and scrubbed. Only cryptographic fingerprints (e.g. <code>sk-...a1b2</code>) are displayed.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                  <th className="py-2 px-3">Provider ID</th>
                  <th className="py-2 px-3">Type</th>
                  <th className="py-2 px-3">Status</th>
                  <th className="py-2 px-3">Credential State</th>
                  <th className="py-2 px-3">Endpoint URL</th>
                  <th className="py-2 px-3">Last Manual Verification</th>
                  <th className="py-2 px-3">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {providers.map((p) => {
                  const hasCred = Boolean(p.credentialFingerprint);
                  return (
                    <tr key={p.providerId} className="hover:bg-slate-50/50">
                      <td className="py-2.5 px-3 font-medium text-slate-900">{p.providerId}</td>
                      <td className="py-2.5 px-3 text-slate-600">{p.providerType}</td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold border ${
                            p.status === 'connected'
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                              : 'bg-slate-100 text-slate-600 border-slate-300'
                          }`}
                        >
                          {p.status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-slate-600 font-mono text-[10px]">
                        {formatCredentialStatus(hasCred, p.credentialFingerprint)}
                      </td>
                      <td className="py-2.5 px-3 text-slate-500 font-mono text-[10px]">{p.endpointUrl}</td>
                      <td className="py-2.5 px-3 text-slate-500">{p.lastManualVerificationAt}</td>
                      <td className="py-2.5 px-3">
                        <button
                          type="button"
                          onClick={() => onVerifyProvider?.(p.providerId)}
                          className="px-2 py-0.5 text-[10px] bg-slate-100 hover:bg-slate-200 text-slate-700 rounded border border-slate-300"
                        >
                          Verify
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Capability Evidence View */}
      {activeTab === 'evidence' && (
        <div className="space-y-4">
          <div className="p-3 bg-blue-50 rounded border border-blue-200 text-xs text-blue-800">
            <strong>Evidence Provenance Policy:</strong> Capability tags must be backed by concrete evidence records or test artifacts. AMCA does not infer capabilities or show synthetic rankings.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 text-slate-600 border-b border-slate-200">
                  <th className="py-2 px-3">Model ID</th>
                  <th className="py-2 px-3">Capability Tag</th>
                  <th className="py-2 px-3">Verification State</th>
                  <th className="py-2 px-3">Evidence Source Reference</th>
                  <th className="py-2 px-3">Verified By</th>
                  <th className="py-2 px-3">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {evidenceRecords.map((ev, idx) => (
                  <tr key={`${ev.modelId}-${ev.capabilityTag}-${idx}`} className="hover:bg-slate-50/50">
                    <td className="py-2.5 px-3 font-medium text-slate-900">{ev.modelId}</td>
                    <td className="py-2.5 px-3 font-mono text-[11px] text-indigo-700">{ev.capabilityTag}</td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold border ${
                          ev.verificationStatus === 'verified'
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : 'bg-amber-50 text-amber-700 border-amber-200'
                        }`}
                      >
                        {ev.verificationStatus}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono text-[10px] text-slate-600">{ev.evidenceSourceRef}</td>
                    <td className="py-2.5 px-3 text-slate-500">{ev.testedByOperator || '—'}</td>
                    <td className="py-2.5 px-3 text-slate-500">{ev.verifiedAt || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

