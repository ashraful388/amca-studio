import type {
  ArtifactItem,
  ArtifactVersionItem,
} from "./types";

export type ArtifactSortKey = "newest" | "version" | "source" | "linked_memory";
export type ArtifactTypeFilter = "all" | "document" | "spreadsheet" | "presentation" | "text" | "code" | "bib";

export interface ArtifactCardPresentation {
  artifactId: string;
  title: string;
  artifactType: string;
  versionNumber: number;
  updatedAt: string;
  sourceRunId: string | null;
  linkedMemoriesCount: number;
  typeBadge: {
    color: string;
    label: string;
  };
}

export function resolveArtifactTypeBadge(artifactType: string): { color: string; label: string } {
  const type = (artifactType || "text").toLowerCase();
  switch (type) {
    case "bib":
      return { color: "#3182ce", label: "Bibliography" };
    case "document":
      return { color: "#3182ce", label: "Document" };
    case "spreadsheet":
      return { color: "#38a169", label: "Spreadsheet" };
    case "presentation":
      return { color: "#d69e2e", label: "Presentation" };
    case "code":
      return { color: "#805ad5", label: "Code" };
    case "text":
    default:
      return { color: "#718096", label: "Text" };
  }
}

export function formatArtifactCard(item: ArtifactItem): ArtifactCardPresentation {
  const badge = resolveArtifactTypeBadge(item.artifact_type);
  return {
    artifactId: item.artifact_id,
    title: item.title || "Untitled Artifact",
    artifactType: item.artifact_type,
    versionNumber: item.current_version_number || 1,
    updatedAt: item.updated_at,
    sourceRunId: (item as any).source_run_id || null,
    linkedMemoriesCount: ((item as any).linked_memory_ids?.length) || 0,
    typeBadge: badge,
  };
}

export function filterArtifacts(
  artifacts: ArtifactItem[],
  filterType: ArtifactTypeFilter,
  searchQuery: string
): ArtifactItem[] {
  const query = searchQuery.trim().toLowerCase();
  return artifacts.filter((a) => {
    const matchesType =
      filterType === "all" ||
      a.artifact_type.toLowerCase() === filterType.toLowerCase();
    const matchesSearch =
      !query ||
      (a.title && a.title.toLowerCase().includes(query)) ||
      (a.artifact_id && a.artifact_id.toLowerCase().includes(query)) ||
      (a.artifact_type && a.artifact_type.toLowerCase().includes(query));
    return Boolean(matchesType && matchesSearch);
  });
}

export function sortArtifacts(
  artifacts: ArtifactItem[],
  sortKey: ArtifactSortKey,
  ascending = false
): ArtifactItem[] {
  const copy = [...artifacts];
  copy.sort((a, b) => {
    let comparison = 0;
    switch (sortKey) {
      case "newest": {
        const timeA = new Date(a.updated_at || a.created_at || 0).getTime();
        const timeB = new Date(b.updated_at || b.created_at || 0).getTime();
        comparison = timeA - timeB;
        break;
      }
      case "version": {
        const vA = a.current_version_number || 0;
        const vB = b.current_version_number || 0;
        comparison = vA - vB;
        break;
      }
      case "source": {
        const sA = (a as any).source_run_id || "";
        const sB = (b as any).source_run_id || "";
        comparison = sA.localeCompare(sB);
        break;
      }
      case "linked_memory": {
        const mA = ((a as any).linked_memory_ids?.length) || 0;
        const mB = ((b as any).linked_memory_ids?.length) || 0;
        comparison = mA - mB;
        break;
      }
      default:
        comparison = 0;
    }
    return ascending ? comparison : -comparison;
  });
  return copy;
}

export function formatVersionItem(item: ArtifactVersionItem): {
  versionNumber: number;
  createdAt: string;
  sourceRunId: string | null;
  sizeBytes: number;
  integritySha: string | null;
} {
  return {
    versionNumber: item.version_number,
    createdAt: item.created_at,
    sourceRunId: (item as any).source_run_id || null,
    sizeBytes: (item as any).size_bytes || 0,
    integritySha: (item as any).content_sha256 || null,
  };
}

export function validateMemoryLinkDraft(draft: {
  memoryId: string;
  relationType?: string;
}): { valid: boolean; errors: string[] } {
  const errors: string[] = [];
  if (!draft.memoryId || !draft.memoryId.trim()) {
    errors.push("Memory record ID is required to establish linkage.");
  }
  return {
    valid: errors.length === 0,
    errors,
  };
}

