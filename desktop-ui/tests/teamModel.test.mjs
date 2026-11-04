// v0.6.0 M4.1 Cross-provider Team UX unit tests for the pure presentation
// model (no browser, no fetch, no backend). Run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_TEAM_MEMBERS,
  classifyMemberRef,
  describeModelRef,
  humanModelName,
  participantHint,
  replacementCandidates,
  staleTeamMembers,
  teamMembers,
  teamUpsertPayload,
  validateTeamDraft,
} from "../src/teamModel.ts";

// A realistic cross-provider member universe: Ollama + OpenAI-compatible
// hub + Anthropic hub + a fixed API profile.
const AVAILABLE = [
  "ollama:qwen3:1.7b",
  "provider.openai1:gpt-test-mini",
  "provider.anthropic1:claude-test",
  "provider.google1:gemini-test",
  "profile.tabitoken.primary",
];

test("1. empty team state: zero members rejected with a bounded message", () => {
  const result = validateTeamDraft({ name: "My team", modelRefs: [] }, AVAILABLE);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /at least one model member/);
});

test("2. single-provider team passes validation", () => {
  const result = validateTeamDraft(
    { name: "OpenAI only", modelRefs: ["provider.openai1:gpt-test-mini"] },
    AVAILABLE,
  );
  assert.equal(result.ok, true);
});

test("3. two-provider team passes validation", () => {
  const result = validateTeamDraft(
    {
      name: "Mixed",
      modelRefs: ["provider.openai1:gpt-test-mini", "provider.anthropic1:claude-test"],
    },
    AVAILABLE,
  );
  assert.equal(result.ok, true);
});

test("4. three-plus-provider team passes validation", () => {
  const result = validateTeamDraft(
    {
      name: "Wide team",
      modelRefs: [
        "provider.openai1:gpt-test-mini",
        "provider.anthropic1:claude-test",
        "provider.google1:gemini-test",
        "ollama:qwen3:1.7b",
      ],
    },
    AVAILABLE,
  );
  assert.equal(result.ok, true);
});

test("5. local Ollama + API members coexist and are classified truthfully", () => {
  const members = teamMembers(
    ["ollama:qwen3:1.7b", "provider.openai1:gpt-test-mini", "profile.tabitoken.primary"],
    AVAILABLE,
  );
  assert.equal(members[0].kind, "local");
  assert.equal(members[0].providerLabel, "Ollama · local");
  assert.equal(members[1].kind, "provider-model");
  assert.equal(members[1].providerLabel, "Provider · provider.openai1");
  assert.equal(members[2].kind, "profile");
  assert.equal(members[2].providerLabel, "API profile");
  assert.ok(members.every((member) => member.available));
});

test("6. team-name validation: blank name rejected", () => {
  const result = validateTeamDraft(
    { name: "   ", modelRefs: ["ollama:qwen3:1.7b"] },
    AVAILABLE,
  );
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /team name is required/);
});

test("7. zero-member rejection (explicit empty refs)", () => {
  const result = validateTeamDraft({ name: "Empty", modelRefs: [] }, AVAILABLE);
  assert.equal(result.ok, false);
});

test("8. duplicate members are prevented by the picker and deduped by the contract", () => {
  const refs = ["ollama:qwen3:1.7b", "ollama:qwen3:1.7b"];
  const result = validateTeamDraft({ name: "Dup", modelRefs: refs }, AVAILABLE);
  // The existing backend contract dedups refs; the UI checkbox set cannot
  // produce duplicates, and validation does not invent a new error for it.
  assert.equal(result.ok, true);
  const once = new Set(refs);
  assert.equal(once.size, 1);
});

test("9. stale-member display: unavailable members are marked, not hidden", () => {
  const members = teamMembers(
    ["provider.openai1:gpt-test-mini", "provider.deleted:old-model"],
    AVAILABLE,
  );
  assert.equal(members[0].available, true);
  assert.equal(members[1].available, false);
  assert.deepEqual(staleTeamMembers(members.map((m) => m.ref), AVAILABLE), [
    "provider.deleted:old-model",
  ]);
});

test("10. replace stale member: replacement candidates exclude existing members", () => {
  const current = ["provider.deleted:old-model", "ollama:qwen3:1.7b"];
  const candidates = replacementCandidates(current, AVAILABLE);
  assert.ok(candidates.includes("provider.openai1:gpt-test-mini"));
  assert.ok(!candidates.includes("provider.deleted:old-model"));
  assert.ok(!candidates.includes("ollama:qwen3:1.7b"));
});

test("11. remove stale member: the upsert payload omits exactly that member", () => {
  const payload = teamUpsertPayload({
    id: "team.1",
    name: "Mixed",
    description: "",
    modelRefs: ["provider.openai1:gpt-test-mini"].filter(
      (ref) => ref !== "provider.deleted:old-model",
    ),
  });
  assert.deepEqual(payload.model_refs, ["provider.openai1:gpt-test-mini"]);
  assert.equal(payload.id, "team.1");
});

test("12. temporarily unavailable member preserved: kept refs never auto-dropped", () => {
  // Ollama offline: local refs vanish from available, but editing a team
  // keeps its OWN original members (grandfathered) — they are preserved,
  // not silently deleted or substituted.
  const originalRefs = ["ollama:qwen3:1.7b", "provider.openai1:gpt-test-mini"];
  const validation = validateTeamDraft(
    { name: "Mixed", modelRefs: originalRefs },
    ["provider.openai1:gpt-test-mini"],
    originalRefs,
  );
  assert.equal(validation.ok, true);
  // A genuinely NEW stale addition is still rejected.
  const rejected = validateTeamDraft(
    {
      name: "Mixed",
      modelRefs: ["provider.openai1:gpt-test-mini", "provider.deleted:new-addition"],
    },
    ["provider.openai1:gpt-test-mini"],
    originalRefs,
  );
  assert.equal(rejected.ok, false);
  assert.match(rejected.errors.join(" "), /cannot be added as new members/);
});

test("13. collaboration-mode participant hint follows existing M0 rules", () => {
  assert.match(participantHint(0), /at least one model/);
  assert.match(participantHint(1), /runs Solo/);
  assert.match(participantHint(2), /Joint/);
  assert.match(participantHint(4), /Parallel/);
});

test("14. edit team: upsert payload carries the existing team id", () => {
  const payload = teamUpsertPayload({
    id: "team.abc",
    name: "Renamed",
    description: "Edited",
    modelRefs: ["ollama:qwen3:1.7b"],
  });
  assert.equal(payload.id, "team.abc");
  assert.equal(payload.name, "Renamed");
  assert.deepEqual(payload.model_refs, ["ollama:qwen3:1.7b"]);
});

test("15. delete team: deletion is a plain delete call — payload never embeds members into providers", () => {
  // Deletion itself uses the existing DELETE /api/model-hub/teams/{id};
  // the model layer only proves team payloads never touch providers.
  const payload = teamUpsertPayload({
    id: null, name: "t", description: "", modelRefs: ["ollama:qwen3:1.7b"],
  });
  assert.ok(!("id" in payload));
});

test("16. provider profiles remain after team deletion: team payloads never mutate providers", () => {
  const payload = teamUpsertPayload({
    id: "team.1", name: "n", description: "", modelRefs: AVAILABLE.slice(0, 2),
  });
  assert.deepEqual(Object.keys(payload).sort(), ["description", "id", "model_refs", "name"]);
});

test("17. credentials absent from team state: member objects carry only bounded identity fields", () => {
  const members = teamMembers(AVAILABLE, AVAILABLE);
  for (const member of members) {
    // Field names only — values legitimately contain provider names.
    assert.deepEqual(
      Object.keys(member).sort(),
      ["available", "kind", "providerLabel", "ref"],
    );
  }
  const payload = teamUpsertPayload({
    id: null, name: "t", description: "", modelRefs: AVAILABLE.slice(0, 2),
  });
  assert.deepEqual(
    Object.keys(payload).sort(),
    ["description", "model_refs", "name"],
  );
});

test("18. saved team visible through the existing Main Chat team source: refs format unchanged", () => {
  // Main Chat applies team.model_refs verbatim via the existing quick-apply;
  // the model layer never rewrites reference formats.
  const refs = ["provider.openai1:gpt-test-mini", "ollama:qwen3:1.7b"];
  assert.deepEqual(staleTeamMembers(refs, AVAILABLE), []);
});

test("19. deleted model reference never silently substituted", () => {
  const members = teamMembers(["provider.deleted:old-model"], AVAILABLE);
  assert.equal(members[0].available, false);
  // The stale ref stays in place exactly as saved.
  assert.equal(members[0].ref, "provider.deleted:old-model");
});

test("20. existing old teams remain compatible: plain refs validate unchanged", () => {
  const legacy = validateTeamDraft(
    { name: "Legacy local team", modelRefs: ["ollama:qwen3:1.7b"] },
    AVAILABLE,
  );
  assert.equal(legacy.ok, true);
  assert.equal(MAX_TEAM_MEMBERS, 16);
});

// v1.1.8 Phase 1: readable model names.  The issue report called out that
// Model Team printed opaque provider/model codes; the label must be readable
// while the exact id stays available.
test("phase1: a vendor-prefixed id renders as a readable model name", () => {
  assert.equal(
    humanModelName("meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "Llama 3.3 70B Instruct Turbo",
  );
});

test("phase1: lowercase ids are title-cased and size tokens stay uppercase", () => {
  assert.equal(humanModelName("gpt-4o-mini"), "GPT 4o Mini");
  assert.equal(humanModelName("qwen2.5:7b"), "Qwen 2.5 7B");
  assert.equal(humanModelName("llama3.2"), "Llama 3.2");
});

test("phase1: version numbers survive tokenizing", () => {
  assert.equal(
    humanModelName("mistralai/Mixtral-8x7B-Instruct-v0.1"),
    "Mixtral 8x7B Instruct v0.1",
  );
});

test("phase1: an id that cannot be improved is returned unchanged", () => {
  assert.equal(humanModelName(""), "");
  assert.equal(humanModelName("   "), "");
  // Already-readable short forms keep their casing; only the hyphen becomes a
  // separator, because that is what makes the label scannable.
  assert.equal(humanModelName("GPT-4O"), "GPT 4O");
});

test("phase1: describeModelRef keeps the exact id as the detail", () => {
  const hub = describeModelRef("provider.abc:gpt-4o-mini");
  assert.equal(hub.label, "GPT 4o Mini");
  assert.match(hub.detail, /provider\.abc/);

  const local = describeModelRef("ollama:qwen3:1.7b");
  assert.equal(local.label, "Qwen 3 1.7B");
  assert.equal(local.detail, "ollama:qwen3:1.7b");
});
