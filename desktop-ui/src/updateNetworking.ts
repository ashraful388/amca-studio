// CR-05 Minimal Update Networking Layer
// Invariants: HTTPS GET only, zero telemetry, zero auth tokens, zero device identifiers.

import { validateManifest, type UpdateManifest } from "./updateCheckModel.ts";

export const DEFAULT_UPDATE_ENDPOINT = "https://updates.amca.dev/channels/stable/latest.json";

export interface FetchUpdateOptions {
  endpointUrl?: string;
  timeoutMs?: number;
  fetchFn?: typeof fetch;
}

export type FetchUpdateResult =
  | { success: true; manifest: UpdateManifest }
  | { success: false; error: string; statusCode?: number };

export async function fetchUpdateManifest(
  options: FetchUpdateOptions = {}
): Promise<FetchUpdateResult> {
  const endpoint = options.endpointUrl || DEFAULT_UPDATE_ENDPOINT;
  const timeoutMs = options.timeoutMs ?? 10000;
  const runFetch = options.fetchFn ?? fetch;

  // Strict Invariant: HTTPS Only
  if (!endpoint.startsWith("https://")) {
    return {
      success: false,
      error: "Security Violation: Update endpoint must strictly use HTTPS."
    };
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await runFetch(endpoint, {
      method: "GET",
      headers: {
        "Accept": "application/json"
      },
      signal: controller.signal,
      cache: "no-cache"
    });

    clearTimeout(timeoutId);

    if (!res.ok) {
      return {
        success: false,
        error: `Server returned HTTP error ${res.status}`,
        statusCode: res.status
      };
    }

    const data = await res.json();
    const validation = validateManifest(data, "stable");
    if (!validation.valid) {
      return {
        success: false,
        error: `Invalid update manifest: ${validation.reason}`
      };
    }

    return {
      success: true,
      manifest: validation.manifest
    };
  } catch (err: any) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      return {
        success: false,
        error: `Update check timed out after ${Math.round(timeoutMs / 1000)} seconds.`
      };
    }
    return {
      success: false,
      error: `Network error during update check: ${err.message || "Connection refused"}`
    };
  }
}
