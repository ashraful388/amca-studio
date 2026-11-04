// H1.1-2: single source of truth for the visible application version.
// Derived from package.json at build time — no more hard-coded version traps.
// Node ESM exposes JSON modules as default-only; bundlers/tsc synthesize
// named exports. Import the default object so BOTH runtimes resolve it.
import packageJson from "../package.json" with { type: "json" };

const { version } = packageJson;

export const APP_VERSION = `v${version}`;

export const RELEASE_CHANNEL =
  version === "1.0.0"
    ? "Stable release"
    : version.endsWith(".0")
      ? "Stable release"
      : "Maintenance release";
