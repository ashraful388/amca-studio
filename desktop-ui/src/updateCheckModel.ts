// CR-05 Minimal Update Check Model
// Strict SemVer 2.0.0 local comparison, manifest validation, and state modeling.
// Negative invariants: No auto-download, no auto-install, no telemetry, no device IDs.

export interface UpdateManifest {
  schema_version: 1;
  channel: "stable" | "beta" | "preview";
  version: string;
  release_date: string;
  download_url: string;
  sha256: string;
  size: number;
  release_notes_url?: string;
  release_notes_summary?: string;
  minimum_supported_version?: string;
  publisher?: string;
  mandatory?: boolean;
}

export type UpdateCheckStatus =
  | "idle"
  | "checking"
  | "up_to_date"
  | "update_available"
  | "error";

export interface UpdateCheckState {
  status: UpdateCheckStatus;
  currentVersion: string;
  latestManifest: UpdateManifest | null;
  errorMessage: string | null;
  lastCheckedAt: string | null;
}

export interface SemVerComponents {
  major: number;
  minor: number;
  patch: number;
  prerelease: string | null;
  build: string | null;
}

const SEMVER_REGEX = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;
const DATE_REGEX = /^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$/;
const SHA256_REGEX = /^[a-fA-F0-9]{64}$/;

export function parseSemVer(versionStr: string): SemVerComponents | null {
  if (!versionStr || typeof versionStr !== "string") return null;
  const clean = versionStr.startsWith("v") ? versionStr.slice(1) : versionStr;
  const match = clean.match(SEMVER_REGEX);
  if (!match) return null;
  return {
    major: parseInt(match[1], 10),
    minor: parseInt(match[2], 10),
    patch: parseInt(match[3], 10),
    prerelease: match[4] || null,
    build: match[5] || null,
  };
}

export function compareSemVer(v1Str: string, v2Str: string): number {
  const v1 = parseSemVer(v1Str);
  const v2 = parseSemVer(v2Str);
  if (!v1 || !v2) {
    throw new Error(`Invalid SemVer comparison: '${v1Str}' vs '${v2Str}'`);
  }

  if (v1.major !== v2.major) return v1.major > v2.major ? 1 : -1;
  if (v1.minor !== v2.minor) return v1.minor > v2.minor ? 1 : -1;
  if (v1.patch !== v2.patch) return v1.patch > v2.patch ? 1 : -1;

  // Prerelease comparison: normal release > prerelease
  if (!v1.prerelease && v2.prerelease) return 1;
  if (v1.prerelease && !v2.prerelease) return -1;
  if (v1.prerelease && v2.prerelease) {
    if (v1.prerelease !== v2.prerelease) {
      return v1.prerelease.localeCompare(v2.prerelease);
    }
  }

  return 0;
}

export function isUpdateAvailable(currentVersion: string, manifestVersion: string): boolean {
  try {
    return compareSemVer(manifestVersion, currentVersion) > 0;
  } catch {
    return false;
  }
}

export function validateManifest(
  raw: unknown,
  expectedChannel: string = "stable"
): { valid: true; manifest: UpdateManifest } | { valid: false; reason: string } {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { valid: false, reason: "Manifest must be a valid JSON object" };
  }

  const m = raw as Record<string, any>;

  if (m.schema_version !== 1) {
    return { valid: false, reason: `Unsupported schema_version: expected 1, got ${m.schema_version}` };
  }

  if (m.channel !== expectedChannel) {
    return { valid: false, reason: `Channel mismatch: expected '${expectedChannel}', got '${m.channel}'` };
  }

  if (typeof m.version !== "string" || !parseSemVer(m.version)) {
    return { valid: false, reason: `Invalid SemVer version string: '${m.version}'` };
  }

  if (typeof m.release_date !== "string" || !DATE_REGEX.test(m.release_date)) {
    return { valid: false, reason: `Invalid release_date format: '${m.release_date}' (must be YYYY-MM-DD)` };
  }

  if (typeof m.download_url !== "string" || !m.download_url.startsWith("https://")) {
    return { valid: false, reason: "download_url must be an absolute HTTPS URL (HTTP rejected)" };
  }

  if (typeof m.sha256 !== "string" || !SHA256_REGEX.test(m.sha256)) {
    return { valid: false, reason: "sha256 must be exactly 64 hexadecimal characters" };
  }

  if (typeof m.size !== "number" || !Number.isInteger(m.size) || m.size <= 0 || m.size > 2147483648) {
    return { valid: false, reason: `size must be a positive integer <= 2GB (got ${m.size})` };
  }

  const allowedKeys = new Set([
    "schema_version", "channel", "version", "release_date", "download_url",
    "sha256", "size", "release_notes_url", "release_notes_summary",
    "minimum_supported_version", "publisher", "mandatory"
  ]);

  for (const key of Object.keys(m)) {
    if (!allowedKeys.has(key)) {
      return { valid: false, reason: `Unrecognized property forbidden: '${key}'` };
    }
  }

  return { valid: true, manifest: m as UpdateManifest };
}
