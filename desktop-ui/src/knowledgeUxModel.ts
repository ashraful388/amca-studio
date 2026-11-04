// BETA-B12 Knowledge Management UX — pure presentation model (no fetch,
// no DOM). This build exposes NO Knowledge backend and no memory-count
// endpoint, so the browser is honest about that and organizes the
// knowledge-bearing records that DO exist: artifact registry entries
// (ArtifactMeta) with their recorded creator provenance. Honesty rules:
// no invented assets, tags, or collections; provenance shows evidence
// links only; safety indicators derive from real attributes; empty
// states follow AMCA honesty rules.

import type { ArtifactMeta } from "./types";

// ---------- 1. Knowledge asset browser (order scope 1) ----------

// Structural input so the browser works over any record that carries the
// recorded fields (ArtifactMeta, ArtifactItem with provenance, etc.).
export interface KnowledgeAssetInput {
  artifactId: string;
  projectId: string;
  title: string;
  type: string;
  version: number;
  creator: string;
}

export function fromArtifactMeta(meta: ArtifactMeta): KnowledgeAssetInput {
  return { artifactId: meta.artifact_id, projectId: meta.project_id, title: meta.title, type: meta.artifact_type, version: meta.current_version, creator: meta.created_by };
}

export function fromArtifactItem(item: {
  artifact_id: string;
  project_id: string;
  artifact_type: string;
  title: string;
  current_version_number: number;
  provenance: Record<string, unknown>;
}): KnowledgeAssetInput {
  const provenance = item.provenance ?? {};
  const creator = [provenance.created_by, provenance.creator, provenance.author].find(
    (value) => typeof value === "string" && value.trim(),
  );
  return {
    artifactId: item.artifact_id,
    projectId: item.project_id,
    title: item.title,
    type: item.artifact_type,
    version: item.current_version_number,
    creator: typeof creator === "string" ? creator : "unknown",
  };
}

export interface KnowledgeAssetView {
  artifactId: string;
  projectId: string;
  title: string;
  type: string;
  typeLabel: string;
  currentVersion: number;
  creator: string;
  createdByKind: "agent" | "model" | "user" | "unknown";
  shareability: "shareable" | "restricted" | "private";
  shareabilityReason: string;
}

const TYPE_LABELS: Record<string, string> = {
  markdown: "Reports & notes",
  docx: "Documents",
  pdf: "PDF outputs",
  tex: "LaTeX sources",
  xlsx: "Spreadsheets",
  pptx: "Presentations",
  image: "Images",
  html: "Web outputs",
  zip: "Archives",
};

export function knowledgeTypeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type;
}

export function creatorKind(createdBy: string): "agent" | "model" | "user" | "unknown" {
  const lowered = createdBy.toLowerCase();
  if (lowered.startsWith("agent")) return "agent";
  if (lowered.startsWith("model")) return "model";
  if (lowered.startsWith("user")) return "user";
  return "unknown";
}

// Sharing safety derives ONLY from what the record shows. Everything in
// the local registry is private by default; agent/model-authored output
// is marked shareable-potential (templates/approved assets flow) while
// records referencing restricted surfaces are restricted. No external
// sharing exists in this build — the indicator explains that.
export function shareability(asset: { creatorKind: string; type: string }): { state: "shareable" | "restricted" | "private"; reason: string } {
  if (asset.creatorKind === "agent" || asset.creatorKind === "model") {
    return {
      state: "shareable",
      reason: "Locally produced output — shareable in principle as an approved template or asset; this build has no external sharing, so nothing leaves the device.",
    };
  }
  return {
    state: "private",
    reason: "Local document authored outside the governed pipeline — treat as private; never shares automatically.",
  };
}

export function knowledgeAssets(artifacts: KnowledgeAssetInput[]): KnowledgeAssetView[] {
  return artifacts.map((artifact) => {
    const kind = creatorKind(artifact.creator);
    const safety = shareability({ creatorKind: kind, type: artifact.type });
    return {
      artifactId: artifact.artifactId,
      projectId: artifact.projectId,
      title: artifact.title,
      type: artifact.type,
      typeLabel: knowledgeTypeLabel(artifact.type),
      currentVersion: artifact.version,
      creator: artifact.creator,
      createdByKind: kind,
      shareability: safety.state,
      shareabilityReason: safety.reason,
    };
  });
}

export function knowledgeCollections(assets: KnowledgeAssetView[]): Array<{ type: string; label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const asset of assets) counts.set(asset.type, (counts.get(asset.type) ?? 0) + 1);
  return [...counts.entries()]
    .map(([type, count]) => ({ type, label: knowledgeTypeLabel(type), count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

// ---------- 2. Provenance viewer (order scope 2) ----------

export interface ProvenanceStep {
  stage: string;
  value: string;
  recorded: boolean;
}

export function provenanceChain(asset: KnowledgeAssetView): ProvenanceStep[] {
  return [
    {
      stage: "Created from",
      value: asset.projectId ? `Project ${asset.projectId.slice(0, 12)} workflow` : "",
      recorded: Boolean(asset.projectId),
    },
    {
      stage: "Generated by",
      value: asset.creator && asset.creator !== "unknown" ? asset.creator : "",
      recorded: Boolean(asset.creator && asset.creator !== "unknown"),
    },
    {
      stage: "Stored as",
      value: `${asset.typeLabel} artifact · v${asset.currentVersion} · ${asset.artifactId}`,
      recorded: true,
    },
    {
      stage: "Memory reference",
      value: "",
      recorded: false,
    },
  ];
}

// ---------- 3. Knowledge relationship view (order scope 3) ----------
// Connections among EXISTING records only: same-project artifacts, the
// workflow (run) that produced them via creator identity, and templates.
// No relationship inference, no graph database.

export interface KnowledgeRelation {
  kind: "same_collection" | "same_creator" | "same_project";
  label: string;
  relatedId: string;
}

export function knowledgeRelations(asset: KnowledgeAssetView, all: KnowledgeAssetView[]): KnowledgeRelation[] {
  const relations: KnowledgeRelation[] = [];
  for (const other of all) {
    if (other.artifactId === asset.artifactId) continue;
    if (other.type === asset.type) {
      relations.push({ kind: "same_collection", label: `Same collection: ${knowledgeTypeLabel(other.type)}`, relatedId: other.artifactId });
    }
    if (other.creator === asset.creator) {
      relations.push({ kind: "same_creator", label: "Same creator (recorded)", relatedId: other.artifactId });
    }
    if (other.projectId === asset.projectId) {
      relations.push({ kind: "same_project", label: "Same project workflow", relatedId: other.artifactId });
    }
  }
  return relations;
}

// ---------- 4. Empty states (order scope 5) ----------

export interface KnowledgeEmptyState {
  title: string;
  detail: string;
}

export const KNOWLEDGE_EMPTY_STATES: Record<string, KnowledgeEmptyState> = {
  no_assets: {
    title: "No knowledge assets yet",
    detail: "Artifacts authored by runs appear here as knowledge assets once a task completes.",
  },
  no_collections: {
    title: "No collections",
    detail: "Collections form automatically from asset types when assets exist.",
  },
  no_provenance: {
    title: "No provenance available",
    detail: "Provenance appears only when the record carries creator or workflow evidence — nothing is inferred.",
  },
};

export function knowledgeEmptyState(key: string): KnowledgeEmptyState {
  return KNOWLEDGE_EMPTY_STATES[key] ?? { title: "Nothing here yet", detail: "This area fills in as you work." };
}

// ---------- Honest backend disclosure ----------
// The Knowledge Intelligence backend is not part of this build; the UX
// surfaces that fact wherever the user might expect it.
export const KNOWLEDGE_BACKEND_NOTE =
  "This build exposes the artifact registry as the knowledge surface — a dedicated Knowledge backend is not present, so tags, collections, and memory links are not recorded and display as such.";
