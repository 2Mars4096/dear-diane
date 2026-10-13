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

## October 12 — visual appearance choices
- [x] Use dropdown selectors and one shared live workspace preview for colors and all font roles, per user follow-up; remove per-option cards.
- [x] Add Neon/Terminal with independent light/dark selection.
- [x] Add System, Humanist, Bookish, Monospace and six bundled fonts (Inter, Lora, Space Grotesk, Orbitron, Monoton, Tilt Neon), with independently saved normal/headings/highlight-quote fonts and live specimens.
- [x] Preserve original PDF/EPUB typography; apply highlight fonts to saved quotes and reference labels.
- [x] Verify six settings/palette tests, real browser application/reload and 390px layout; inspect desktop screenshot and verify all six bundled font files load.
- [x] Verify production build and reinstall locally. Installed Settings confirms one preview, all six bundled fonts loading, independent roles and narrow fit. Signature/archive equality and app/backend health pass; rollback retained at `/Applications/.Diane-previous-4b8abfec-848e-422b-bbcf-a521554013b1.app`.

- [x] Repeat local reinstall at user request; signature and both health endpoints pass. Rollback: `/Applications/.Diane-previous-c199de61-dde7-4e67-bd20-692847de0015.app`.

- [x] Remove sticky appearance preview; preview and controls share normal document flow.
- [x] Rebuild and reinstall non-sticky appearance layout. Installed browser check: preview moves 220px with 220px scroll; signature and app/backend health pass.

- [x] Add a direct new-chat plus button immediately before the project actions menu, reusing its existing project-scoped handler.
- [x] Build and install project new-chat shortcut.

- [x] Match Reply to selection / Sidecar chat toolbar corners to composer/message bubbles (12px).
- [x] Verify installed selection toolbar corners: selection, message and composer all compute to 12px. Production build/budgets, signature, archive equality and both health endpoints pass. Rollback retained at `/Applications/.Diane-previous-d2001e72-bbe8-4461-b4e7-b7d5a74890a3.app`.

## October 12 — project switcher controls
- [x] Separate All projects text action from the previous/name/next navigation group; wrap groups on narrow windows.
- [x] Verify desktop/narrow layout and reinstall locally: installed Electron checks at 1200/828/390px show no overlap, text fits and arrows/name stay aligned; 390px screenshot inspected. Build/budgets, signature, archive equality and health pass.

- [x] Add New project to sidebar project menu, opening the existing creation dialog directly.
- [x] Verify direct sidebar creation entry and install locally: installed Electron opens a blank New project form and closes the menu without opening the carousel. Build/budgets, signature, archive equality and health pass.

## October 12 — isolated backlog review
- [x] Review search, Settings and project-menu keyboard interaction in a synthetic browser fixture.
- [x] Verify Settings palette/font persistence, modal focus behavior and 390px layout; open result 30 using the keyboard.
- [x] Repair unbroken-paper-title horizontal overflow in the search list; preserve established palette values.
- [x] Post-fix browser verification passes at 390px and desktop width. Screenshots: `output/playwright/low-risk-*.png`.
- Full application contrast audit, physical-device checks and installed-app acceptance are separate; this batch does not restart or replace the app.

## October 12 — project context restoration
- [x] Reproduce old chat and project reappearing after creating a new project.
- [x] Explicit project selection suppresses startup auto-restore and clears remembered previous chat; initial startup restoration remains supported.
- [x] Session/project regressions: 153 tests pass; production build/budgets pass.
- [x] Verify installed creation flow retains new project and blank chat; reinstalled locally. Same isolated Electron reproduction changes from Old project / old selection / one message to ra-neo fixture / no old selection / zero messages. Signature/archive/health verified.

## October 12 — sidebar new-chat folder
- [x] Reproduce scope/active-project mismatch: sidebar ra-neo plus posts chat creation to old project and dispatches old root.
- [x] Default New chat target follows explicit sidebar project scope, retaining active-project fallback for All projects and explicit caller overrides.
- [x] Installed verification intercepts `/api/chats/ra` creation and `/api/v2/agent-runs` with `/Users/lizhi/Downloads/local_projects/ra-neo`. Pre-fix same fixture targeted old project/root. No model task submitted. Build/budgets, signature, archive equality and health pass.

## October 13 — account usage and composer simplification
- [x] Remove the two-account panel limit and shared-session-store deduplication. Include the native CLI home even when desktop defaults inherit the app profile; merge only credential-location aliases.
- [x] Use Codex `account/rateLimits/read` without starting a thread/turn; preserve bucket labels, plan and observation timestamp. Failed reads never borrow another account's rollout quota.
- [x] Show Claude account availability and configured API providers; OpenRouter key spending/remaining key budget is separate from subscription percentages and account balance. No credentials/remote key labels returned.
- [x] Make Enter explicitly steer and Cmd/Ctrl+Enter queue independently of placement-button selection. Preserve Shift+Enter/IME; modified Enter bypasses autocomplete selection.
- [x] Remove “Added to Up next” status and header Stop run; keep queue rows and composer Stop.
- [x] Clean navigation render/effect state synchronization; browser checks cover project/session open, Escape and requested reopen.
- [x] Validate 502-test isolated snapshot, 10 usage backend tests and mobile/desktop account rendering. Read-only live probes: native CLI Pro and app Pro Lite have different quotas; configured OpenRouter usage works, Claude login unreadable.
- [ ] Install and verify the updated composer in the installed app once its active run ends.
- Protocol references: [Codex account quotas](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt), [OpenRouter current key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key).

- [x] Queue reference follow-up: use persisted display text for new requests; convert older reference envelopes to source-labelled multiline quotes. Preserve the execution prompt and queue IDs/actions. 164 focused tests and TypeScript check pass; installed acceptance pending.

## October 14 — compact header and update notice
- [x] Move existing history controls beside the sidebar toggle; remove their standalone row and obsolete wrapper CSS.
- [x] Preserve open-document tabs and history handlers; scope breadcrumb responsive selectors away from history buttons.
- [x] Subscribe Settings navigation to shared update availability and render the existing dot plus accessible status.
- [x] Typecheck, production build and bundle budgets pass.
- [ ] Installed desktop visual acceptance.

- October 14 local installation: current 0.1.6 bundle installed and launched after idle checks; signature/archive equality and backend/proxy health verified. Visual installed acceptance remains separate.

## October 14 — native caption alignment
- [x] Follow the rendered sidebar-toggle center at current zoom; retain native controls and caption dragging.
- [x] Eleven tests and isolated Electron geometry checks at three zoom levels pass; signed build ready.
- [ ] Installation explicitly deferred by user after active-work guard rejected restart.

## October 14 — README editing
- [x] Group task instructions, split reader controls and settings, and remove duplicated/internal commentary.
- [x] Verify command blocks and retained inline code unchanged; preserve user-facing limitations.
