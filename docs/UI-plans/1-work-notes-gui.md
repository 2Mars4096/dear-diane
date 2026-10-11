# 1: Work and Notes GUI

**Status:** in-progress
**Goal:** Refine one calm workspace for Super DAN sessions, task state, artifacts, previews, and notes.

## Tasks
- [x] Delete rejected icon variants, retain v7 source/prompt, reinstall signed DAN with v7, and verify launch/backend health.
- [x] Adopt user-selected ivory v7 for desktop packaging (PNG/ICNS/ICO) and browser favicon; retain existing local-updater icon preservation.
- [x] Replace v4's purple palette with three painted-icon alternatives: blush pink v5, slate blue v6, and warm ivory v7; save artwork and prompts.
- [x] Generate colorful gouache (v3) and impressionist (v4) variants of the letter-free agent-network icon, with saved prompts.
- [x] Revise icon direction to a letter-free agent network with a shared coordination core; save v2 artwork and prompt.
- [x] Generate a retro-futuristic DAN app-icon candidate; save artwork and prompt in `output/imagegen/` for review.
- [x] Add eight operator-specified paired light/dark color schemes to DAN Settings with live preview, independent Light/Dark/System mode, and persisted selection.
- [x] Show only waiting queue entries, with accurate counts, numbered previews, keyboard-expandable full text, and matching Work surface styling.

- [x] Replace olive-tinted Work dark mode with charcoal/graphite surfaces, soft white text, sand focus/activity accents, and matching native/utility controls.

- [x] Use Work and Notes as the only top-level destinations.
- [x] Show durable sessions, active work, task graph state, evidence, and previews.
- [x] Support note collections, Markdown editing, course/progress metadata, and attachments.
- [x] Remove legacy editor modes and native integrations not used by this workspace.
- [ ] Continue accessibility, responsive, and interaction-polish passes.

## Decisions

- User selected ivory v7; retain original artwork, prompt, and platform exports under `editor/resources/icons/`. Earlier variant-generation tasks are historical; unused artwork was deleted on request.

- New capabilities extend Work or Notes instead of introducing another mode.
- Active work must remain recoverable from the Agent V2 store after restart.

## Settings review, 2026-10-11
- [x] Review Diane's Settings source/CSS and compare official OpenAI/Claude documentation and OpenCode's public settings implementation.
- [x] Replace jump buttons with category navigation and one visible category; preserve drafts and focus when switching.
- [x] Standardize preference rows: label/optional description left, control right, faint bottom divider, no card around each row; stack controls on narrow screens.
- [x] Separate Appearance, Notifications, Providers, Agents, Skills, SSH connections, Library, Usage, and About & updates. Group navigation under Preferences, Agent setup, and Workspace; keep About at the bottom.
- [x] Replace palette-role swatches with a small contextual preview; retain all existing palettes and independent Notes appearance.
- [x] Show provider/host name, status, and one primary action in list rows; reveal credential forms and advanced configuration on demand. Preserve truthful save/error states and host/profile scope.
- [x] Verify desktop/light/dark and 390px layouts, category navigation, modal Escape, and unsaved credential retention in a browser fixture; 13 settings/SSH/updater tests and production build/budgets pass.
- [ ] Installed desktop acceptance with real host/provider data; full keyboard/contrast audit remains separate.

### Findings and evidence
- `DianeSettings.tsx` mounts every section in a single scroll surface. Jump buttons have no selected-category state. Agents & skills combines credentials, Team controls, and a potentially long skills table.
- `workbench.css` uses stacked 12px labels and selects up to 360px wide; Appearance and child components do not share one row structure. Section dividers exist, but individual preference rows have inconsistent rhythm. Palette tokens (Base, border, muted) are implementation vocabulary.
- Library currently redirects to another settings surface; expose a clear source summary/action rather than a nearly empty category. Usage should open the detailed analyzer from a compact summary.
- Proposed starting dimensions, not measured competitor values: 180–200px category rail, 640–720px content, 14px labels, 12–13px descriptions, 12–16px vertical row padding, 32–36px controls. Use content-driven heights and visible keyboard focus.
- [OpenCode navigation](https://github.com/anomalyco/opencode/blob/dev/packages/app/src/components/dialog-settings.tsx): vertical categories, separate desktop/server groups. [General settings](https://github.com/anomalyco/opencode/blob/dev/packages/app/src/components/settings-general.tsx): shared SettingsRow, title/description, right-aligned controls, subtle bottom borders, responsive wrapping. Strongest row-layout reference.
- [OpenAI settings](https://developers.openai.com/codex/app/settings) currently redirects to [ChatGPT desktop settings](https://learn.chatgpt.com/docs/reference/settings): separate General, Keyboard shortcuts, Notifications, Appearance and other capabilities; contextual theme previews. Do not present this as a visual inspection of the installed Codex build.
- [Claude Code settings](https://code.claude.com/docs/en/settings): `/config` exposes a short set of personal preferences and auto-saves; user/project/managed scope is explicit. [Desktop](https://code.claude.com/docs/en/desktop) has its own settings surfaces. Borrow concise options and scope clarity, not terminal styling.
- Review limitation: no rendered screenshot obtained. The Playwright CLI launch did not complete and was cancelled; local port 8765 did not return an HTTP response within five seconds. Product code was not changed.

## September workbench redesign
- [x] Replace Work’s default blueprint grid and project accordion with conversation, an optional project-local session shelf, project carousel, and stable session wheel. See [4-1](../plans/4-1-agent-workbench.md).
- [x] Browser-fixture checks: keyboard focus, drafts, project isolation, selection reply, mobile sizing, reduced motion. Live mixed-agent acceptance remains in [native worker integration](2-native-agent-workers.md).

### Settings implementation, 2026-10-11
- Local category rail, one visible panel, visited-panel retention, and lazy first loading. Separate Providers/Agents/Skills; direct agent fields replace the nested Team disclosure.
- Scoped `settings.css` aligns labels and right-side controls; narrow layouts stack controls and scroll category navigation horizontally. Theme preview uses existing palette tokens, without changing stored palettes.
- Provider status rows reveal Connect/Manage forms. Appearance changes still save automatically; credentials retain explicit Save/Cancel/Remove actions.
- Browser evidence: `output/playwright/settings-{light,dark,mobile,providers}.png`. Real backend writes and desktop installation were not performed.
