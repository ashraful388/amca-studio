// v0.6.0 M4.1 Cross-provider Team UX: pure presentation model.
//
// Everything reuses the EXISTING Model Team contract (model_refs strings,
// name + description + quality policy; backend enforces name-required,
// dedup, max 16 members, description <= 500). This module only derives
// bounded UI state: member identity labels, availability, stale-member
// handling, participant hints, and draft validation. No secrets, no
// invented limits, no team-schema changes.

export const MAX_TEAM_MEMBERS = 16;

export interface MemberRefInfo {
  ref: string;
  kind: "local" | "profile" | "provider-model" | "managed" | "unknown";
  providerLabel: string;
  available: boolean;
}

/** Classify one existing member reference by its real identity format. */
export function classifyMemberRef(ref: string): Omit<MemberRefInfo, "available"> {
  if (ref.startsWith("ollama:")) {
    return { ref, kind: "local", providerLabel: "Ollama · local" };
  }
  const separator = ref.indexOf(":");
  if (separator > 0) {
    return {
      ref,
      kind: "provider-model",
      providerLabel: `Provider · ${ref.slice(0, separator)}`,
    };
  }
  // Plain profile/credential id (fixed model profile or managed id).
  if (ref.includes(".")) {
    return { ref, kind: "profile", providerLabel: "API profile" };
  }
  return { ref, kind: "managed", providerLabel: "Managed model" };
}

/** Well-known short forms that read better uppercase than title-cased. */
const MODEL_ACRONYMS = new Set(["gpt", "llm", "ai", "api", "vl", "moe", "gguf", "tts", "stt", "ocr"]);

/**
 * v1.1.8 Phase 1: a readable display name for a raw provider model id.
 *
 * The model selectors used to print opaque ids like
 * "meta-llama/Llama-3.3-70B-Instruct-Turbo" or "gpt-4o-mini" verbatim, which
 * the issue report called out as unreadable. This derives a title-cased label
 * while the exact id stays available as the element's title/tooltip. It never
 * invents a name: an id it cannot improve is returned unchanged.
 */
export function humanModelName(modelId: string): string {
  const raw = String(modelId ?? "").trim();
  if (!raw) return raw;
  // Drop a vendor prefix path ("meta-llama/..."), keep the model segment.
  const segment = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
  if (!segment) return raw;
  const tokens = segment
    // Split camelCase and a word followed by a version number ("llama3.2").
    // Version dots stay intact: "3.3" must not become "3 3".
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/([A-Za-z]{3,})(\d)/g, "$1 $2")
    .split(/[-_:]+/)
    .flatMap((part) => part.split(" "))
    .map((part) => part.trim())
    .filter(Boolean);
  if (!tokens.length) return raw;
  const words = tokens.map((word) => {
    const lower = word.toLowerCase();
    // "8x7B" already reads correctly; a bare size token is uppercased.
    if (/^\d+x\d+[bmk]?$/i.test(word)) return word;
    if (/^\d+(\.\d+)?[bmk]$/i.test(word)) return word.toUpperCase();
    // A version tag ("v0.1") stays lowercase.
    if (/^v\d/i.test(word)) return word;
    if (MODEL_ACRONYMS.has(lower)) return lower.toUpperCase();
    if (word === word.toUpperCase() && word.length <= 4 && /[A-Z]/.test(word)) return word;
    return word.charAt(0).toUpperCase() + word.slice(1);
  });
  return words.join(" ");
}

/** The readable label plus the exact id, for one model reference. */
export function describeModelRef(ref: string): { label: string; detail: string } {
  const identity = classifyMemberRef(ref);
  if (identity.kind === "local") {
    // An Ollama ref is "model:tag"; both halves are shown readably.
    const tag = ref.slice("ollama:".length);
    const separator = tag.indexOf(":");
    if (separator > 0) {
      return {
        label: `${humanModelName(tag.slice(0, separator))} ${humanModelName(tag.slice(separator + 1))}`,
        detail: ref,
      };
    }
    return { label: humanModelName(tag), detail: ref };
  }
  const separator = ref.indexOf(":");
  if (separator > 0) {
    return {
      label: humanModelName(ref.slice(separator + 1)),
      detail: `Provider ${ref.slice(0, separator)}`,
    };
  }
  return { label: ref, detail: identity.providerLabel };
}

export function teamMembers(
  modelRefs: string[],
  availableRefs: string[],
): MemberRefInfo[] {
  const available = new Set(availableRefs);
  return modelRefs.map((ref) => ({
    ...classifyMemberRef(ref),
    available: available.has(ref),
  }));
}

export function staleTeamMembers(
  modelRefs: string[],
  availableRefs: string[],
): string[] {
  const available = new Set(availableRefs);
  return modelRefs.filter((ref) => !available.has(ref));
}

/**
 * Participant hint derived from the EXISTING M0 collaboration rules:
 * solo runs use exactly one participant; joint / independent-discuss /
 * parallel / adaptive-hybrid require at least two. Purely advisory — the
 * backend's architecture decision remains authoritative.
 */
export function participantHint(memberCount: number): string {
  if (memberCount <= 0) return "Select at least one model.";
  if (memberCount === 1) return "1 participant · runs Solo";
  return `${memberCount} participants · eligible for Joint / Independent → Discuss / Parallel / Adaptive Hybrid`;
}

export interface TeamDraft {
  name: string;
  modelRefs: string[];
}

export interface TeamDraftValidation {
  ok: boolean;
  errors: string[];
}

/**
 * Validate a team draft against the EXISTING backend contract. The
 * backend (model_hub.save_team) requires a non-empty name, accepts
 * non-empty refs, dedups refs, and rejects more than 16 members; selecting
 * a stale/deleted reference as a NEW member is a clearly invalid state the
 * UI prevents without inventing new limits. Stale members that are ALREADY
 * part of an edited team (originalRefs) are allowed to remain — temporarily
 * unavailable members are preserved, never auto-deleted or substituted.
 */
export function validateTeamDraft(
  draft: TeamDraft,
  availableRefs: string[],
  originalRefs: string[] = [],
): TeamDraftValidation {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("A team name is required.");
  if (draft.modelRefs.length === 0) errors.push("Add at least one model member.");
  if (draft.modelRefs.length > MAX_TEAM_MEMBERS) {
    errors.push(`A team can hold at most ${MAX_TEAM_MEMBERS} members.`);
  }
  const available = new Set(availableRefs);
  const original = new Set(originalRefs);
  const staleSelected = draft.modelRefs.filter(
    (ref) => !available.has(ref) && !original.has(ref),
  );
  if (staleSelected.length > 0) {
    errors.push(
      `Unavailable references cannot be added as new members: ${staleSelected.join(", ")}`,
    );
  }
  return { ok: errors.length === 0, errors };
}

/**
 * Replacement candidates for one stale member: any currently available
 * reference the team does not already contain. The user chooses; nothing
 * is silently substituted.
 */
export function replacementCandidates(
  currentRefs: string[],
  availableRefs: string[],
): string[] {
  const current = new Set(currentRefs);
  return availableRefs.filter((ref) => !current.has(ref));
}

export function teamUpsertPayload(input: {
  id: string | null;
  name: string;
  description: string;
  modelRefs: string[];
}): { id?: string; name: string; description: string; model_refs: string[] } {
  const payload: { id?: string; name: string; description: string; model_refs: string[] } = {
    name: input.name,
    description: input.description,
    model_refs: input.modelRefs,
  };
  if (input.id) payload.id = input.id;
  return payload;
}
