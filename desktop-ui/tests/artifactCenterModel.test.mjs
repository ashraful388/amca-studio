// AMCA Studio Milestone A5: Artifact Intelligence Workspace model tests
// Pure presentation logic verification (filtering, sorting, card formatting, version resolution, memory linkage validation)
// Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  filterArtifacts,
  formatArtifactCard,
  formatVersionItem,
  resolveArtifactTypeBadge,
  sortArtifacts,
  validateMemoryLinkDraft,
} from "../src/artifactCenterModel.ts";

const SAMPLE_ARTIFACTS = [
  {
    artifact_id: "art-1",
    project_id: "prj-alpha",
    title: "System Architecture Document",
    artifact_type: "document",
    current_version_number: 3,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-02T15:00:00Z",
    source_run_id: "run-arch-01",
    linked_memory_ids: ["mem-1", "mem-2"],
  },
  {
    artifact_id: "art-2",
    project_id: "prj-alpha",
    title: "Q3 Cost Analysis Ledger",
    artifact_type: "spreadsheet",
    current_version_number: 1,
    created_at: "2026-09-01T12:00:00Z",
    updated_at: "2026-09-01T12:00:00Z",
    source_run_id: "run-cost-02",
    linked_memory_ids: [],
  },
  {
    artifact_id: "art-3",
    project_id: "prj-alpha",
    title: "Executive Technical Summary",
    artifact_type: "presentation",
    current_version_number: 5,
    created_at: "2026-08-30T09:00:00Z",
    updated_at: "2026-09-02T18:00:00Z",
    source_run_id: "run-exec-03",
    linked_memory_ids: ["mem-3"],
  },
];

test("resolveArtifactTypeBadge resolves distinct colors and labels for artifact types", () => {
  assert.deepEqual(resolveArtifactTypeBadge("document"), { color: "#3182ce", label: "Document" });
  assert.deepEqual(resolveArtifactTypeBadge("spreadsheet"), { color: "#38a169", label: "Spreadsheet" });
  assert.deepEqual(resolveArtifactTypeBadge("presentation"), { color: "#d69e2e", label: "Presentation" });
  assert.deepEqual(resolveArtifactTypeBadge("code"), { color: "#805ad5", label: "Code" });
  assert.deepEqual(resolveArtifactTypeBadge("text"), { color: "#718096", label: "Text" });
  assert.deepEqual(resolveArtifactTypeBadge("unknown_type"), { color: "#718096", label: "Text" });
});

test("formatArtifactCard transforms raw artifact into card presentation", () => {
  const card = formatArtifactCard(SAMPLE_ARTIFACTS[0]);
  assert.equal(card.artifactId, "art-1");
  assert.equal(card.title, "System Architecture Document");
  assert.equal(card.artifactType, "document");
  assert.equal(card.versionNumber, 3);
  assert.equal(card.sourceRunId, "run-arch-01");
  assert.equal(card.linkedMemoriesCount, 2);
  assert.equal(card.typeBadge.label, "Document");
});

test("filterArtifacts filters by type filter and search query", () => {
  // Filter all with query "ledger"
  const r1 = filterArtifacts(SAMPLE_ARTIFACTS, "all", "ledger");
  assert.equal(r1.length, 1);
  assert.equal(r1[0].artifact_id, "art-2");

  // Filter by type "presentation"
  const r2 = filterArtifacts(SAMPLE_ARTIFACTS, "presentation", "");
  assert.equal(r2.length, 1);
  assert.equal(r2[0].artifact_id, "art-3");

  // Filter no match
  const r3 = filterArtifacts(SAMPLE_ARTIFACTS, "code", "");
  assert.equal(r3.length, 0);
});

test("sortArtifacts sorts artifacts by newest, version, source, and linked_memory", () => {
  // Sort by newest descending
  const byNewest = sortArtifacts(SAMPLE_ARTIFACTS, "newest", false);
  assert.equal(byNewest[0].artifact_id, "art-3"); // 2026-09-02T18:00:00Z

  // Sort by version descending
  const byVersion = sortArtifacts(SAMPLE_ARTIFACTS, "version", false);
  assert.equal(byVersion[0].artifact_id, "art-3"); // v5
  assert.equal(byVersion[1].artifact_id, "art-1"); // v3
  assert.equal(byVersion[2].artifact_id, "art-2"); // v1

  // Sort by linked_memory descending
  const byMem = sortArtifacts(SAMPLE_ARTIFACTS, "linked_memory", false);
  assert.equal(byMem[0].artifact_id, "art-1"); // 2 links
  assert.equal(byMem[1].artifact_id, "art-3"); // 1 link
  assert.equal(byMem[2].artifact_id, "art-2"); // 0 links
});

test("formatVersionItem formats version items with provenance and hashes", () => {
  const vItem = {
    version_number: 2,
    created_at: "2026-09-01T11:00:00Z",
    source_run_id: "run-v2",
    size_bytes: 4096,
    content_sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  };
  const formatted = formatVersionItem(vItem);
  assert.equal(formatted.versionNumber, 2);
  assert.equal(formatted.sourceRunId, "run-v2");
  assert.equal(formatted.sizeBytes, 4096);
  assert.equal(formatted.integritySha, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
});

test("validateMemoryLinkDraft enforces required memory identifier", () => {
  const invalid = validateMemoryLinkDraft({ memoryId: "   " });
  assert.equal(invalid.valid, false);
  assert.equal(invalid.errors.length, 1);

  const valid = validateMemoryLinkDraft({ memoryId: "mem-rec-99" });
  assert.equal(valid.valid, true);
});

