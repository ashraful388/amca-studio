"""Model settings surface contract tests.

ZCODE-AMCA-STUDIO-NEXT-MODEL-SETTINGS-SURFACE-01/02

The Model Hub and BYOK provider surface were reorganised into a settings page
in the style of the operator's reference design:

* provider mode: on the BYOK surface the options navigation is REPLACED by the
  operator's provider list (grouped known/custom), with a Back to Model Hub
  control that restores the options navigation;
* the provider detail page carries live rows (Base URL, API format, API key,
  Model list), an include/exclude toggle for the provider and for each model,
  and icon actions instead of text buttons;
* adding a model happens in a dialog with Smart configuration (a bounded
  verification), operator-recorded context window / max output tokens, and an
  Advanced settings disclosure;
* "+ Add provider" opens a draft that writes nothing until it is saved.

These tests pin the parts of that redesign which are contracts rather than
styling, so a later visual change cannot quietly undo them:

* providers are reachable from the left navigation in provider mode;
* there is a real way back out of a provider page;
* a stored credential is never reconstructed or redisplayed;
* capability is not asserted from a name -- badges follow recorded health
  evidence only;
* operator-recorded metadata is labelled as recorded, never as measured;
* the add/remove-model actions reuse the existing provider update contract
  rather than a new backend surface.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "desktop-ui", "src")


def read(name: str) -> str:
    with open(os.path.join(UI, name), "r", encoding="utf-8") as handle:
        return handle.read()


def strip_comments(source: str) -> str:
    """Comments legitimately describe the reference design; tests scan code."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", without_block)


class ModelSettingsSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nav = read("modelSettingsNavModel.ts")
        cls.shell = read("ModelSettingsShell.tsx")
        cls.detail = read("ProviderDetail.tsx")
        cls.modal = read("AddModelModal.tsx")
        cls.hub = read("ModelHub.tsx")
        cls.css = read("modelSettings.css")
        cls.main = read("main.tsx")
        cls.nav_code = strip_comments(cls.nav)
        cls.detail_code = strip_comments(cls.detail)
        cls.modal_code = strip_comments(cls.modal)

    # -- navigation modes ---------------------------------------------------

    def test_providers_are_first_class_navigation_entries(self):
        # In provider mode the provider list REPLACES the options navigation;
        # a provider is a navigation target in its own right.
        self.assertIn('kind: "provider"', self.nav)
        self.assertIn("PROVIDER_NAV_GROUPS", self.nav)
        self.assertIn("buildProviderNav", self.nav)
        self.assertIn("showProviderNav", self.nav)
        # The shell renders the provider list when provider mode is active.
        self.assertIn("buildProviderNav", self.shell)
        self.assertIn("showProviderNav", self.shell)
        self.assertIn("msettings-nav-row", self.shell)

    def test_the_options_navigation_lists_sections_only(self):
        # The mirror of the replacement contract: hub mode must not also list
        # providers, or the two modes would double the surface.
        self.assertNotIn("PROVIDER_NAV_GROUPS", strip_comments(
            self.nav.split("export function buildModelSettingsNav")[1]
            .split("export function buildProviderNav")[0]
        ))
        self.assertIn("byok", self.nav)
        self.assertIn("overview", self.nav)

    def test_there_is_a_way_back_out_of_provider_mode(self):
        self.assertIn("backToHubTarget", self.nav)
        self.assertIn("Back to Model Hub", self.nav)
        self.assertIn("msettings-back", self.shell)
        # The back control restores the hub's own navigation.
        self.assertIn('section: "overview"', self.nav)
        # And the Model Hub actually passes its target through to the shell.
        self.assertIn("onNavigate=", self.hub)
        self.assertIn("target={", self.hub)

    # -- honest state -------------------------------------------------------

    def test_a_stored_credential_is_never_reconstructed_or_redisplayed(self):
        # The API never returns a stored key, so the detail page shows a
        # presence indicator plus a write-only replacement field. It must not
        # synthesise a mask from a placeholder and must not read
        # provider.secret.
        self.assertIn("Stored", self.detail)
        self.assertNotIn("maskSecret", self.detail_code)
        self.assertNotRegex(self.detail_code, r"maskSecret\(")
        self.assertNotIn("provider.secret", self.detail_code)
        # The eye control toggles the key being TYPED, and says so.
        self.assertIn("the key you are typing", self.detail)
        # The write-only key input lives on the provider card
        # (ProviderDetail.tsx) since Round 27 removed the draft form; it
        # toggles between text and password while typing.
        self.assertIn('secretRevealed ? "text" : "password"', self.detail)

    def test_capability_is_not_asserted_from_a_name(self):
        # The reference design shows Vision / IM pills. AMCA may not: a badge
        # is emitted only for a recorded health state.
        for forbidden in (r"\bVision\b", r"\bImage\b", r"\bTools?\b", r"\bReasoning\b"):
            self.assertNotRegex(self.nav_code, forbidden)
            self.assertNotRegex(self.detail_code, forbidden)
            self.assertNotRegex(self.modal_code, forbidden)
        # The badge is driven by the persisted health record.
        self.assertIn("evidenceBadge", self.detail)
        self.assertIn("ModelHealthRecord", self.detail)

    def test_exclusion_is_presented_as_a_decision_not_a_health_state(self):
        # The provider toggle and the per-model toggles are stored operator
        # decisions; the status line must say so neutrally.
        self.assertIn("Excluded from chat", self.nav)
        self.assertIn("onToggleProvider", self.detail)
        self.assertIn("role=\"switch\"", self.detail)
        self.assertIn("mstoggle", self.css)

    def test_recorded_metadata_is_labelled_as_recorded_never_measured(self):
        # Context window / max output tokens are what the operator typed.
        # The UI must show them as recorded values, and must not present them
        # as measurements or route on them.
        self.assertIn("formatTokenCount", self.nav)
        self.assertIn("AMCA has not measured", self.detail)
        self.assertIn("AMCA has not measured", self.modal)
        self.assertIn("routing never reads it", self.modal)

    # -- API format ---------------------------------------------------------

    def test_api_format_is_derived_and_labelled_as_such(self):
        # The provider schema stores no api_format column. The select is
        # derived from provider_kind, and an unknown kind must fall back rather
        # than claim a format nobody recorded.
        self.assertIn("apiFormatLabel", self.nav)
        self.assertIn("API_FORMAT_OPTIONS", self.nav)
        self.assertIn("OpenAI-compatible", self.nav)
        # The detail renders the derived select, not a free-text format field.
        self.assertIn("API format", self.detail)
        self.assertIn("API_FORMAT_OPTIONS.map", self.detail)

    # -- model management ----------------------------------------------------

    def test_model_add_and_remove_reuse_the_existing_update_contract(self):
        self.assertIn("updateHubProvider", self.hub)
        self.assertIn("model_ids", self.hub)
        self.assertIn("onOpenAddModel", self.detail)
        self.assertIn("onRemoveModel", self.detail)
        # A duplicate model id is refused rather than silently appended twice.
        self.assertIn("already in this provider", self.hub)

    def test_the_add_model_dialog_matches_the_reference_options(self):
        # Smart configuration (bounded verification after saving), the model
        # id, the two recorded fields, the advanced disclosure, and the
        # Reset form / Cancel / Save footer.
        self.assertIn("Smart configuration", self.modal)
        self.assertIn("Model ID", self.modal)
        self.assertIn("Context window", self.modal)
        self.assertIn("Max output tokens", self.modal)
        self.assertIn("Advanced settings", self.modal)
        self.assertIn("Reset form", self.modal)
        self.assertIn("Cancel", self.modal)
        self.assertIn("Save", self.modal)
        # Smart configuration is the bounded verify, off unless asked for.
        self.assertIn("verify", self.modal)

    def test_the_dialog_save_always_applies_the_include_decision(self):
        # The add step materializes a visibility row enabled=1, so a save that
        # skipped the visibility call whenever the pre-add snapshot happened
        # to match would publish a model the operator asked to keep out of
        # chat. The call must run unconditionally, and a retry after a
        # partial failure must complete instead of wedging on the duplicate
        # guard.
        body = self.hub.split("const saveModelModal", 1)[1].split("const openAddModel", 1)[0]
        self.assertIn("await amca.setHubModelVisibility(providerId, payload.modelId, payload.include);", body)
        self.assertNotIn("payload.include !== currentlyIncluded", body)
        self.assertIn("!item.model_ids.includes(payload.modelId)", body)

    def test_a_failed_dialog_save_is_visible_inside_the_dialog(self):
        # The page-level failure messages render behind the overlay, so a
        # silent false would look like a dead Save button.
        self.assertIn("Save failed", self.modal)
        self.assertIn("Your draft is kept", self.modal)

    def test_the_api_key_commits_on_enter_not_blur(self):
        # Clicking the eye to double-check what was typed must not store the
        # key first — blur fires before the click lands.
        self.assertNotIn("onBlur={commitSecret}", self.detail)
        self.assertIn('if (event.key === "Enter") commitSecret()', self.detail)

    def test_an_empty_base_url_is_not_saved_as_a_success(self):
        # Wiping the endpoint by accident must not be reported as a routine
        # save; the saved endpoint is kept.
        self.assertIn("Base URL was left empty", self.detail)
        self.assertIn("commitEndpoint", self.detail)

    def test_the_endpoint_draft_resyncs_after_a_save(self):
        # The backend normalizes the stored endpoint, so a draft that is
        # never re-synced would re-commit the same patch on every blur.
        self.assertIn("setEndpointDraft(provider.endpoint);", self.detail)
        self.assertIn("[provider.endpoint]", self.detail)

    def test_no_draft_discard_prompt_is_shown(self):
        # The system child message "Leave the new provider draft?" is
        # removed by owner decision.
        self.assertNotIn("Leave the new provider draft?", self.hub)
        self.assertNotIn("Start a new provider draft?", self.hub)
        self.assertNotIn("providerDraftDirty", self.hub)

    def test_a_rejected_model_save_keeps_the_dialog_draft(self):
        # A rejected save resolves without closing, so everything the operator
        # typed stays on screen.
        self.assertIn(".then((ok) => {", self.modal)
        self.assertIn("if (ok) {", self.modal)
        self.assertIn("onClose();", self.modal)

    def test_edit_mode_cannot_rename_a_model_id(self):
        # A model id is identity on the provider; edit adjusts the recorded
        # details, it does not silently re-key the model.
        self.assertIn('mode === "edit" ? modelId ?? "" : idDraft', self.modal)
        self.assertIn('disabled={mode === "edit" || busy}', self.modal)

    def test_removing_a_model_keeps_the_provider_and_its_credential(self):
        self.assertIn("The provider and its credential are kept", self.hub)
        # Removal only rewrites the catalog; no delete/credential call.
        self.assertNotIn("deleteHubProvider(item.id", self.hub.split("removeModelFromProvider")[1][:800])

    # -- provider draft -------------------------------------------------------

    def test_add_provider_creates_the_card_immediately(self):
        # Round 27 (owner order): the old fill-in-then-save draft is GONE.
        # "+ Add provider" calls createHubProvider right away with NO secret
        # and hands over to the created provider's card, which is honestly
        # "not configured" until a key is saved on the card.
        self.assertNotIn("providerDraftOpen", self.hub)
        self.assertNotIn("msdraft", self.hub)
        self.assertNotIn("Not saved yet", self.hub)
        self.assertIn("await amca.createHubProvider({", self.hub)
        self.assertIn('name: preset?.name || "New provider"', self.hub)
        # no secret is passed at create time (the create block only)
        create_block = self.hub.split("createHubProvider({", 1)[1].split("})", 1)[0]
        self.assertNotIn("secret", create_block)

    def test_creating_a_provider_hands_over_to_its_real_id(self):
        # The create response carries the provider id; the draft must not
        # guess which row it created by name/endpoint matching.
        self.assertIn("outcome.provider.id", self.hub)

    # -- surface & styling contracts ------------------------------------------

    def test_icon_buttons_carry_accessible_labels(self):
        # Icons replace text buttons on model rows; each needs a name.
        for icon in ("plug", "pencil", "trash", "kebab", "box", "refresh"):
            self.assertIn(f'name="{icon}"', self.detail + self.shell + self.hub, icon)
        # The key-visibility icon switches between two states.
        self.assertIn('"eyeOff" : "eye"', self.detail)
        self.assertIn("aria-label", self.detail)

    def test_the_design_system_is_scoped_and_loaded(self):
        self.assertIn("modelSettings.css", self.main)
        self.assertIn(".msettings", self.css)
        # Scoped tokens on .msettings, not a repaint of the global shell.
        self.assertIn("--ms-accent", self.css)
        self.assertNotRegex(self.css, r"^\s*body\s*\{", )
        # A light theme is declared explicitly: an accent tuned for a near-black
        # canvas is not legible on white.
        self.assertIn(':root[data-theme="light"] .msettings', self.css)

    def test_active_navigation_is_filled_so_the_location_is_unambiguous(self):
        self.assertIn(".msettings-nav-row.active", self.css)
        self.assertIn("background: var(--ms-surface-3)", self.css)

    def test_the_hub_still_routes_its_own_sections(self):
        # The redesign replaced the horizontal tab rail; every hub section must
        # still have rendered content in ModelHub, or the redesign would be a
        # regression. Checking only the nav model is not enough — a section can
        # be listed and still have no content. `utilities` is the final `else`
        # of the render chain rather than an explicit comparison, so it is
        # checked by its own content markers.
        for section in (
            "overview",
            "providers",
            "amca",
            "byok",
            "local",
            "selection",
            "teams",
        ):
            self.assertIn(f'"{section}"', self.nav, f"{section} missing from nav model")
            self.assertIn(f'tab === "{section}"', self.hub, f"{section} has no rendered branch")
        self.assertIn('"utilities"', self.nav, "utilities missing from nav model")
        for marker in ("Model Matchmaker", "VRAM Calculator", "Context Explorer"):
            self.assertIn(marker, self.hub, f"utilities content missing: {marker}")

    def test_the_use_in_chat_button_is_gone(self):
        # The owner removed it: model selection happens in Chat, not here.
        self.assertNotIn("Use this provider in Chat", self.detail)
        self.assertNotIn("onUseProvider", self.detail)
        self.assertNotIn("onUseProvider", self.hub)

    def test_no_draft_discard_prompt_is_shown(self):
        # The system child message "Leave the new provider draft?" is removed
        # by owner decision.
        self.assertNotIn("Leave the new provider draft?", self.hub)
        self.assertNotIn("Start a new provider draft?", self.hub)
        self.assertNotIn("providerDraftDirty", self.hub)

    def test_the_legacy_direct_profiles_ui_is_removed(self):
        # The owner ordered the legacy direct-profile cards and the landing
        # options removed completely: no pane, no verify-all section, no
        # legacy navigation group. Entering BYOK lands on a provider instead.
        self.assertNotIn("Existing direct model profiles", self.hub)
        self.assertNotIn("Verify all configured models", self.hub)
        self.assertNotIn("verifyAllModels", self.hub)
        self.assertNotIn("Legacy profiles", self.nav)
        self.assertNotIn("legacyProfiles", self.hub)
        self.assertNotIn("removeDirectModel", self.hub)

    def test_deleting_a_provider_clears_its_scoped_state(self):
        # The recorded results must not outlive the provider they belong to,
        # and the view leaves the deleted provider's page.
        body = self.hub.split("const removeProvider", 1)[1].split("const createTeam", 1)[0]
        self.assertIn("delete next[key]", body)
        self.assertIn('setTarget({ kind: "section", section: "byok" })', body)

    def test_changing_the_endpoint_invalidates_recorded_results(self):
        # A health record is fingerprinted by endpoint, so an old READY badge
        # must not survive a reconfiguration that changed where it points.
        # (Round 27: the invalidation lives in patchProvider now that the
        # addProvider draft is gone; the badge lookup is endpoint-scoped.)
        body = self.hub.split("const patchProvider", 1)[1].split("const discoverProvider", 1)[0]
        self.assertIn("patch.endpoint !== before.endpoint", body)
        self.assertIn("patch.provider_kind !== before.provider_kind", body)
        self.assertIn("delete next[key]", body)
        self.assertIn("record.endpoint === provider.endpoint", self.detail)

    def test_the_model_draft_is_scoped_to_one_provider(self):
        # Without a key, a half-typed model id follows the user to the next
        # provider and can be submitted to the wrong account.
        self.assertIn("key={selected.id}", self.hub)


class SettingsShellAndChromeTests(unittest.TestCase):
    """Round-3 contracts outside the Model settings files proper: the settings
    chrome, the composer, and the main sidebar."""

    @classmethod
    def setUpClass(cls):
        app = os.path.join(UI, "App.tsx")
        with open(app, "r", encoding="utf-8") as handle:
            cls.app = handle.read()
        css = os.path.join(UI, "studio.css")
        with open(css, "r", encoding="utf-8") as handle:
            cls.studio_css = handle.read()

    def test_models_and_providers_page_has_no_stacked_sections(self):
        # The category previously stacked Model settings, Tools & Integrations,
        # Plugins and Skills under one heading — the same words three times
        # over. The category owns exactly one section now.
        self.assertIn('"Models & Providers": ["Models & Providers"]', self.app)
        self.assertNotIn('"Models & Providers": ["Models & Providers", "Tools & Integrations"', self.app)

    def test_tools_and_integrations_is_not_a_settings_section(self):
        # The Permissioned Tool Fabric panel is not part of the settings page
        # users see under Models & Providers.
        for group in re.findall(r'"([^"]+)": \[([^\]]*)\]', self.app):
            self.assertNotIn("Tools & Integrations", group[1])

    def test_skills_and_plugins_are_separate_settings_entries(self):
        self.assertIn('Skills: ["Skills"]', self.app)
        self.assertIn('Plugins: ["Plugins"]', self.app)

    def test_the_model_settings_page_supplies_its_own_heading(self):
        # With the single-section group, the outer category h1 would repeat
        # the shell's own "Model settings" header. Skills and Plugins are
        # suppressed for the same reason: their bodies carry their own h1.
        self.assertIn('!["Models & Providers", "Skills", "Plugins"].includes(settingsCategory)', self.app)
        self.assertNotIn('<h1>{settingsCategory}</h1>\n        {SETTINGS_GROUPS', self.app)

    def test_the_composer_no_longer_shows_a_project_folder_control(self):
        # The folder is chosen once, at Project creation (defaulting to
        # Documents\<name>) — never in the messagebox. Exactly one folder
        # input remains: the Project creation form's.
        self.assertNotIn("chat-workspace-chip", self.app)
        self.assertEqual(self.app.count('aria-label="Project folder"'), 1)
        # The access-level control stays: it is not a folder chooser.
        self.assertIn('aria-label="Local PC Access"', self.app)

    def test_sidebar_new_chat_is_always_standalone(self):
        # One control, one destination: the sidebar behaved differently inside
        # a project, which read as "New Chat Standalone is broken".
        self.assertIn('createNewConversation("standalone")', self.app)
        self.assertNotIn("new-chat-alternate", self.app)
        self.assertNotIn('createNewConversation(activeStudioProject ? "project" : "standalone")', self.app)

    def test_opening_a_project_chat_does_not_collapse_the_project(self):
        # Expansion is its own state, driven only by the project row.
        self.assertIn("expandedProjectId", self.app)
        self.assertNotIn('view === "project";\n                return <div key={item.project_id}', self.app)
        self.assertIn("if (expanded) { setExpandedProjectId(null); }", self.app)

    def test_the_sidebar_footer_is_compact_with_icon_actions(self):
        # Avatar, profile name, and two icon buttons — no version text, no
        # text buttons, reduced height.
        self.assertIn("sidebar-footer-compact", self.app)
        self.assertEqual(self.app.count('aria-label="Check for Updates"'), 2)
        self.assertEqual(self.app.count('aria-label="Settings"'), 2)
        self.assertNotIn("<small>Version {BETA_STATUS.appVersion}</small>", self.app)

    def test_the_text_size_preference_actually_applies(self):
        css = os.path.join(UI, "styles.css")
        with open(css, "r", encoding="utf-8") as handle:
            source = handle.read()
        for scale in ("compact", "standard", "large"):
            self.assertIn(f':root[data-ui-scale="{scale}"]', source, scale)
        self.assertIn('font-family: "Segoe UI"', source)

    def test_navigation_and_headers_are_bold(self):
        css = os.path.join(UI, "studio.css")
        with open(css, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(".studio-primary-nav button { font-weight:600; }", source)
        self.assertIn(".conversation-section-heading { font-weight:650; }", source)
        self.assertIn(".studio-settings-nav button { font-weight:600; }", source)

    def test_the_model_list_spans_the_full_detail_width(self):
        # The stacked row's value must span both grid columns, or the model
        # table auto-places into the 180px label column and renders as a
        # sliver.
        css = read("modelSettings.css")
        self.assertIn(".msettings .msrow-stack > .msrow-value { grid-column: 1 / -1; }", css)

    def test_the_provider_navigation_scrolls_on_its_own(self):
        css = read("modelSettings.css")
        self.assertIn("position: sticky; top: 0;", css)
        self.assertIn("overflow-y: auto;", css)

    def test_projects_and_chats_hold_a_third_of_the_sidebar_each(self):
        # A stable placeholder third for each section, instead of Chats
        # sliding up under a short Projects list. The third is a flex BASIS,
        # not a minimum, so both sections shrink on short windows and the
        # pinned footer can never be pushed off or cover rows.
        self.assertIn(".studio-shell .sidebar-project-section { flex:0 1 33%;", self.studio_css)
        self.assertIn(".studio-shell .conversation-section { flex:1 1 33%;", self.studio_css)
        self.assertNotIn("min-height:33%", self.studio_css)

    def test_entering_byok_lands_on_a_provider(self):
        hub = os.path.join(UI, "ModelHub.tsx")
        with open(hub, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('next.kind === "section" && next.section === "byok"', source)
        self.assertIn('setTarget({ kind: "provider", providerId: first.id });', source)

    def test_default_provider_presets_are_offered(self):
        nav = read("modelSettingsNavModel.ts")
        for kind in ("openai", "anthropic", "openrouter", "moonshot", "deepseek", "zai"):
            self.assertIn(f'kind: "{kind}"', nav, kind)
        hub = os.path.join(UI, "ModelHub.tsx")
        with open(hub, "r", encoding="utf-8") as handle:
            source = handle.read()
        # A preset row opens the unsaved draft pre-filled with the preset.
        self.assertIn('next.kind === "preset"', source)


if __name__ == "__main__":
    unittest.main()
