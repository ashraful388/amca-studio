// Settings shell: mode-switching left navigation + a single content surface.
//
// This mirrors the desktop settings layout the operator supplied as the
// reference: a page heading with optional right-aligned actions, a hairline
// divider, then navigation and content side by side. The navigation has two
// modes: the hub's own section list, and — on the BYOK surface — the
// operator's provider list, which REPLACES the section list, with a
// Back to Model Hub control that restores it.

import type { ReactNode } from "react";
import { Icon } from "./icons";
import {
  buildModelSettingsNav,
  buildProviderNav,
  backToHubLabel,
  backToHubTarget,
  showProviderNav,
  type SettingsTarget,
} from "./modelSettingsNavModel";
import type { ModelHealthRecord, ModelHubProvider } from "./types";

export function ModelSettingsShell({
  providers,
  health,
  target,
  onNavigate,
  onAddProvider,
  draftActive,
  title,
  description,
  action,
  children,
  loading,
  error,
}: {
  providers: ModelHubProvider[];
  health?: Record<string, ModelHealthRecord>;
  target: SettingsTarget;
  onNavigate: (target: SettingsTarget) => void;
  onAddProvider: () => void;
  draftActive?: boolean;
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
  loading: boolean;
  error: string | null;
}) {
  const providerMode = showProviderNav(target);
  const groups = providerMode
    ? buildProviderNav(providers, target, health, draftActive)
    : buildModelSettingsNav(target);
  const back = backToHubTarget(target);
  const providerCount = providers.length;

  return (
    <section className="msettings">
      <header className="msettings-head">
        <div className="msettings-head-text">
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        {action ? <div className="msettings-head-action">{action}</div> : null}
      </header>

      <div className="msettings-rule" role="presentation" />

      <div className="msettings-body">
        <nav
          className={`msettings-nav${providerMode ? " msettings-nav-providers" : ""}`}
          aria-label={providerMode ? "Provider list" : "Model settings sections"}
        >
          {back ? (
            <button
              type="button"
              className="msettings-back"
              onClick={() => onNavigate(back)}
              title="Return to the Model Hub"
            >
              <span aria-hidden="true">←</span> {backToHubLabel()}
            </button>
          ) : null}

          {groups.map((group) => (
            <div className="msettings-nav-group" key={group.group}>
              <p className="msettings-nav-heading">{group.group}</p>
              {group.entries.length === 0 ? (
                <p className="msettings-nav-empty">No providers yet</p>
              ) : (
                group.entries.map((entry) =>
                  entry.target === "draft" ? (
                    <button
                      type="button"
                      key={entry.key}
                      className="msettings-nav-row active msettings-nav-draft"
                      title={entry.statusLabel}
                    >
                      <span className="msettings-nav-icon" aria-hidden="true">
                        <Icon name="box" />
                      </span>
                      <span className="msettings-nav-label">
                        <strong>{entry.label}</strong>
                      </span>
                      <span className="msettings-dot tone-off" aria-label={entry.statusLabel} role="img" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      key={entry.key}
                      className={`msettings-nav-row${entry.active ? " active" : ""}`}
                      aria-current={entry.active ? "page" : undefined}
                      title={entry.statusLabel || undefined}
                      onClick={() => { if (entry.target !== "draft") onNavigate(entry.target); }}
                    >
                      <span className="msettings-nav-icon" aria-hidden="true">
                        {entry.icon === "box" ? <Icon name="box" /> : entry.icon}
                      </span>
                      <span className="msettings-nav-label">
                        <strong>{entry.label}</strong>
                        {entry.metaLabel ? <small>{entry.metaLabel}</small> : null}
                      </span>
                      {entry.tone !== "none" ? (
                        <span
                          className={`msettings-dot tone-${entry.tone}`}
                          aria-label={entry.statusLabel}
                          role="img"
                        />
                      ) : null}
                    </button>
                  ),
                )
              )}
            </div>
          ))}

          <div className="msettings-nav-group">
            <button type="button" className="msettings-nav-add" onClick={onAddProvider}>
              <span aria-hidden="true">+</span> Add provider
            </button>
          </div>

          {providerMode ? (
            <p className="msettings-nav-foot">
              {providerCount === 0
                ? "No providers configured yet"
                : `${providerCount} provider${providerCount === 1 ? "" : "s"} configured`}
            </p>
          ) : null}
        </nav>

        <div className="msettings-main">
          {error ? (
            <p className="msettings-error" role="alert">
              {error}
            </p>
          ) : null}
          {loading ? (
            <p className="msettings-loading">Loading model settings…</p>
          ) : (
            children
          )}
        </div>
      </div>
    </section>
  );
}
