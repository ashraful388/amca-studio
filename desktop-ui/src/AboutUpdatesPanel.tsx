import { useState } from "react";
import { APP_VERSION, RELEASE_CHANNEL } from "./versionLabel.ts";
import {
  type UpdateManifest,
  type UpdateCheckStatus,
  isUpdateAvailable,
} from "./updateCheckModel.ts";
import { fetchUpdateManifest } from "./updateNetworking.ts";
import amcaLogoFull from "./assets/amca-logo-full.png";

export function AboutUpdatesPanel() {
  const [status, setStatus] = useState<UpdateCheckStatus>("idle");
  const [manifest, setManifest] = useState<UpdateManifest | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null);

  const handleCheckForUpdates = async () => {
    setStatus("checking");
    setErrorMessage(null);

    const result = await fetchUpdateManifest();
    setLastCheckedAt(new Date().toLocaleTimeString());

    if (!result.success) {
      setStatus("error");
      setErrorMessage(result.error);
      return;
    }

    setManifest(result.manifest);
    const available = isUpdateAvailable(APP_VERSION, result.manifest.version);
    if (available) {
      setStatus("update_available");
    } else {
      setStatus("up_to_date");
    }
  };

  const formatBytes = (bytes: number): string => {
    if (bytes >= 1024 * 1024 * 1024) {
      return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    }
    if (bytes >= 1024 * 1024) {
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    return `${Math.round(bytes / 1024)} KB`;
  };

  return (
    <article className="panel settings-wide about-updates-panel">
      <img className="about-logo" src={amcaLogoFull} alt="AMCA — Adaptive Multi-Model Collaborative Cognition Architecture" />
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Release &amp; Maintenance</span>
          <h2>About AMCA Studio &amp; Updates</h2>
        </div>
        <span className="channel-badge">{RELEASE_CHANNEL}</span>
      </div>

      <div className="about-version-summary" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <p style={{ margin: 0, fontSize: "1.1rem" }}>
            Installed Version: <strong>{APP_VERSION}</strong>
          </p>
          <small style={{ color: "var(--muted-color, #888)" }}>
            Architecture: Windows x64 · Loopback Engine · Local-First
          </small>
        </div>
        <button
          className="primary-button"
          type="button"
          disabled={status === "checking"}
          onClick={handleCheckForUpdates}
        >
          {status === "checking" ? "Checking for updates..." : "Check for updates"}
        </button>
      </div>

      {lastCheckedAt && (
        <p className="settings-note" style={{ marginTop: 0, marginBottom: "12px" }}>
          Last checked: {lastCheckedAt}
        </p>
      )}

      {status === "up_to_date" && (
        <div className="update-status-box up-to-date" style={{ padding: "12px", background: "rgba(46, 204, 113, 0.1)", border: "1px solid rgba(46, 204, 113, 0.3)", borderRadius: "6px", marginBottom: "12px" }}>
          <strong style={{ color: "#2ecc71" }}>AMCA Studio is up to date.</strong>
          <p style={{ margin: "4px 0 0", fontSize: "0.9rem" }}>
            You are running the latest stable release ({APP_VERSION}).
          </p>
        </div>
      )}

      {status === "update_available" && manifest && (
        <div className="update-status-box update-available" style={{ padding: "16px", background: "rgba(243, 156, 18, 0.1)", border: "1px solid rgba(243, 156, 18, 0.3)", borderRadius: "6px", marginBottom: "12px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <strong style={{ color: "#f39c12", fontSize: "1.05rem" }}>
                Update Available: v{manifest.version}
              </strong>
              <p style={{ margin: "4px 0", fontSize: "0.9rem" }}>
                Released: {manifest.release_date} · Size: {formatBytes(manifest.size)}
              </p>
            </div>
            <a
              className="primary-button"
              href={manifest.download_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ textDecoration: "none", display: "inline-block" }}
            >
              Download Update (Opens Browser)
            </a>
          </div>

          {manifest.release_notes_summary && (
            <div style={{ marginTop: "10px", fontSize: "0.9rem", color: "var(--text-color, #ccc)" }}>
              <strong>Release Notes:</strong>
              <p style={{ margin: "4px 0 0" }}>{manifest.release_notes_summary}</p>
            </div>
          )}

          <div style={{ marginTop: "8px", fontSize: "0.8rem", color: "var(--muted-color, #888)", wordBreak: "break-all" }}>
            <span>SHA-256: <code>{manifest.sha256}</code></span>
          </div>
        </div>
      )}

      {status === "error" && (
        <div className="update-status-box update-error" style={{ padding: "12px", background: "rgba(231, 76, 60, 0.1)", border: "1px solid rgba(231, 76, 60, 0.3)", borderRadius: "6px", marginBottom: "12px" }}>
          <strong style={{ color: "#e74c3c" }}>Unable to check for updates</strong>
          <p style={{ margin: "4px 0 0", fontSize: "0.9rem" }}>{errorMessage}</p>
        </div>
      )}

      <div className="legal-links" style={{ display: "flex", gap: "16px", marginTop: "16px", fontSize: "0.85rem", borderTop: "1px solid var(--border-color, #333)", paddingTop: "12px" }}>
        <a href="https://amca.ai/terms" target="_blank" rel="noopener noreferrer" style={{ color: "var(--link-color, #3498db)" }}>Terms of Service</a>
        <a href="https://amca.ai/privacy" target="_blank" rel="noopener noreferrer" style={{ color: "var(--link-color, #3498db)" }}>Privacy Policy</a>
        <a href="https://amca.ai/licenses" target="_blank" rel="noopener noreferrer" style={{ color: "var(--link-color, #3498db)" }}>Third-Party Licenses</a>
      </div>

      <p className="settings-note" style={{ marginTop: "8px", fontSize: "0.8rem" }}>
        Privacy Contract: Update checks query the official HTTPS manifest only. Zero device identifiers, account tokens, or telemetry metrics are transmitted.
      </p>
    </article>
  );
}
