import test from "node:test";
import assert from "node:assert/strict";
import {
  parseSemVer,
  compareSemVer,
  isUpdateAvailable,
  validateManifest,
} from "../src/updateCheckModel.ts";
import {
  fetchUpdateManifest,
  DEFAULT_UPDATE_ENDPOINT,
} from "../src/updateNetworking.ts";

const VALID_MANIFEST_FIXTURE = {
  schema_version: 1,
  channel: "stable",
  version: "1.1.8",
  release_date: "2026-09-20",
  download_url: "https://github.com/amca-project/releases/download/v1.1.8/AMCA-Studio-Setup-1.1.8.exe",
  sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  size: 78500000,
  release_notes_summary: "Maintenance release with performance fixes.",
};

// ---------------------------------------------------------------------------
// 1. SemVer Parsing & Comparison
// ---------------------------------------------------------------------------
test("SemVer parsing handles valid and invalid versions accurately", () => {
  assert.deepEqual(parseSemVer("1.1.7"), {
    major: 1, minor: 1, patch: 7, prerelease: null, build: null
  });
  assert.deepEqual(parseSemVer("v1.1.8"), {
    major: 1, minor: 1, patch: 8, prerelease: null, build: null
  });
  assert.deepEqual(parseSemVer("1.2.0-rc.1"), {
    major: 1, minor: 2, patch: 0, prerelease: "rc.1", build: null
  });

  assert.equal(parseSemVer("invalid"), null);
  assert.equal(parseSemVer("1.2"), null);
  assert.equal(parseSemVer("1.2.3.4"), null);
  assert.equal(parseSemVer(""), null);
});

test("SemVer comparison correctly determines version order", () => {
  assert.equal(compareSemVer("1.1.8", "1.1.7"), 1);
  assert.equal(compareSemVer("1.1.7", "1.1.7"), 0);
  assert.equal(compareSemVer("1.1.6", "1.1.7"), -1);
  assert.equal(compareSemVer("1.2.0", "1.1.9"), 1);
  assert.equal(compareSemVer("2.0.0", "1.99.99"), 1);
  assert.equal(compareSemVer("1.2.0", "1.2.0-rc.1"), 1);
});

// ---------------------------------------------------------------------------
// 2. Acceptance Criteria: Update Availability Logic
// ---------------------------------------------------------------------------
test("TC-CR05-01: Update Available state triggered when manifest version > current", () => {
  assert.equal(isUpdateAvailable("1.1.7", "1.1.8"), true);
  assert.equal(isUpdateAvailable("v1.1.7", "1.2.0"), true);
});

test("TC-CR05-02: Already Latest state when manifest version equals current", () => {
  assert.equal(isUpdateAvailable("1.1.7", "1.1.7"), false);
  assert.equal(isUpdateAvailable("v1.1.7", "1.1.7"), false);
});

test("TC-CR05-03: Older server version / Downgrade strictly rejected from offering update", () => {
  assert.equal(isUpdateAvailable("1.1.7", "1.1.6"), false);
  assert.equal(isUpdateAvailable("1.2.0", "1.1.7"), false);
});

// ---------------------------------------------------------------------------
// 3. Manifest Validation & Schema Rejections
// ---------------------------------------------------------------------------
test("TC-CR05-04A: Valid standard manifest is accepted", () => {
  const res = validateManifest(VALID_MANIFEST_FIXTURE, "stable");
  assert.equal(res.valid, true);
  if (res.valid) {
    assert.equal(res.manifest.version, "1.1.8");
    assert.equal(res.manifest.size, 78500000);
  }
});

test("TC-CR05-04B: HTTP URLs are strictly rejected (HTTPS only)", () => {
  const invalid = { ...VALID_MANIFEST_FIXTURE, download_url: "http://insecure.example/setup.exe" };
  const res = validateManifest(invalid);
  assert.equal(res.valid, false);
  assert.match(res.reason, /HTTPS URL/i);
});

test("TC-CR05-04C: Invalid SHA256 checksums are strictly rejected", () => {
  const shortSha = { ...VALID_MANIFEST_FIXTURE, sha256: "deadbeef" };
  assert.equal(validateManifest(shortSha).valid, false);

  const nonHexSha = { ...VALID_MANIFEST_FIXTURE, sha256: "z".repeat(64) };
  assert.equal(validateManifest(nonHexSha).valid, false);
});

test("TC-CR05-04D: Unknown schema versions are rejected", () => {
  const badSchema = { ...VALID_MANIFEST_FIXTURE, schema_version: 2 };
  const res = validateManifest(badSchema);
  assert.equal(res.valid, false);
  assert.match(res.reason, /Unsupported schema_version/);
});

test("TC-CR05-04E: Channel mismatch is rejected", () => {
  const betaChannel = { ...VALID_MANIFEST_FIXTURE, channel: "beta" };
  const res = validateManifest(betaChannel, "stable");
  assert.equal(res.valid, false);
  assert.match(res.reason, /Channel mismatch/);
});

test("TC-CR05-04F: Invalid or zero file sizes are rejected", () => {
  assert.equal(validateManifest({ ...VALID_MANIFEST_FIXTURE, size: 0 }).valid, false);
  assert.equal(validateManifest({ ...VALID_MANIFEST_FIXTURE, size: -100 }).valid, false);
  assert.equal(validateManifest({ ...VALID_MANIFEST_FIXTURE, size: 3000000000 }).valid, false);
});

test("TC-CR05-04G: Unrecognized extra properties are rejected", () => {
  const injected = { ...VALID_MANIFEST_FIXTURE, tracking_token: "xyz123" };
  const res = validateManifest(injected);
  assert.equal(res.valid, false);
  assert.match(res.reason, /Unrecognized property forbidden/);
});

// ---------------------------------------------------------------------------
// 4. Networking Isolation & Error Handling
// ---------------------------------------------------------------------------
test("TC-CR05-05A: Network failure (HTTP 404) transitions safely to error", async () => {
  const mockFetch = async () => ({
    ok: false,
    status: 404,
  });

  const res = await fetchUpdateManifest({
    endpointUrl: "https://updates.amca.dev/channels/stable/latest.json",
    fetchFn: mockFetch,
  });

  assert.equal(res.success, false);
  assert.equal(res.statusCode, 404);
  assert.match(res.error, /HTTP error 404/);
});

test("TC-CR05-05B: Network failure (HTTP 500) transitions safely to error", async () => {
  const mockFetch = async () => ({
    ok: false,
    status: 500,
  });

  const res = await fetchUpdateManifest({
    endpointUrl: "https://updates.amca.dev/channels/stable/latest.json",
    fetchFn: mockFetch,
  });

  assert.equal(res.success, false);
  assert.equal(res.statusCode, 500);
  assert.match(res.error, /HTTP error 500/);
});

test("TC-CR05-05C: Offline / connection refused transitions safely to error", async () => {
  const mockFetch = async () => {
    throw new Error("ECONNREFUSED");
  };

  const res = await fetchUpdateManifest({
    endpointUrl: "https://updates.amca.dev/channels/stable/latest.json",
    fetchFn: mockFetch,
  });

  assert.equal(res.success, false);
  assert.match(res.error, /ECONNREFUSED/);
});

test("TC-CR05-05D: Timeout handling triggers clean failure without hanging", async () => {
  const mockFetch = async () => {
    const err = new Error("The operation was aborted");
    err.name = "AbortError";
    throw err;
  };

  const res = await fetchUpdateManifest({
    endpointUrl: "https://updates.amca.dev/channels/stable/latest.json",
    timeoutMs: 1000,
    fetchFn: mockFetch,
  });

  assert.equal(res.success, false);
  assert.match(res.error, /timed out/);
});

// ---------------------------------------------------------------------------
// 5. Zero Telemetry & Header Privacy Verification
// ---------------------------------------------------------------------------
test("TC-CR05-06: Zero telemetry - fetch request carries only Accept header and zero auth/tracking headers", async () => {
  let capturedHeaders = null;
  let capturedMethod = null;
  let capturedUrl = null;

  const mockFetch = async (url, opts) => {
    capturedUrl = url;
    capturedMethod = opts.method;
    capturedHeaders = opts.headers;
    return {
      ok: true,
      json: async () => VALID_MANIFEST_FIXTURE,
    };
  };

  const res = await fetchUpdateManifest({
    endpointUrl: "https://updates.amca.dev/channels/stable/latest.json",
    fetchFn: mockFetch,
  });

  assert.equal(res.success, true);
  assert.equal(capturedMethod, "GET");
  assert.equal(capturedUrl, "https://updates.amca.dev/channels/stable/latest.json");

  // Ensure zero tracking or authorization headers are passed
  assert.deepEqual(capturedHeaders, { Accept: "application/json" });
  assert.equal(capturedHeaders["Authorization"], undefined);
  assert.equal(capturedHeaders["Cookie"], undefined);
  assert.equal(capturedHeaders["X-Device-Id"], undefined);
  assert.equal(capturedHeaders["User-Agent"], undefined);
});

// ---------------------------------------------------------------------------
// 6. Non-HTTPS Endpoint Enforcement
// ---------------------------------------------------------------------------
test("TC-CR05-07: Insecure non-HTTPS endpoint URL is blocked before socket initiation", async () => {
  let fetchCalled = false;
  const mockFetch = async () => {
    fetchCalled = true;
    return { ok: true };
  };

  const res = await fetchUpdateManifest({
    endpointUrl: "http://updates.amca.dev/channels/stable/latest.json",
    fetchFn: mockFetch,
  });

  assert.equal(res.success, false);
  assert.equal(fetchCalled, false);
  assert.match(res.error, /Security Violation: Update endpoint must strictly use HTTPS/);
});
