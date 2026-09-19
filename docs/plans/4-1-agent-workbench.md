# 4-1: Lightweight agent workbench

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Make conversation the primary surface, with spatial project/session navigation and inspectable agent activity.

## Tasks
- [x] Replace persistent work framing with a quiet conversation shell; retain Notes, attachments, steering, stop, and file previews.
- [x] Add project carousel and session wheel with keyboard, pointer, touch, focus restoration, and reduced motion support.
- [x] Keep wheel slots stable across edits and new sessions; use creation order and replace the oldest eligible session.
- [x] Show real message/tool/event data with collapsible detail and selection-to-reply.
- [x] Document shared DAN/Codex/Claude worker presentation and backend integration limits.
- [x] Verify build, navigation invariants, and browser interactions.

## Decisions
- User chose project carousel + session wheel. Projects act like browser windows, sessions like tabs.
- Preserve the existing Agent V2 execution and persistence paths; extract independent presentation modules.
- No fabricated worker sessions or controls for unimplemented backends.

## Validation
- `npm run build:verify`: TypeScript, Vite production build, bundle budgets, and archived-surface exclusions pass.
- Focused workbench/workspace suite: 152 tests pass. Full editor suite: 194 pass, two existing happy-dom sanitizer tests fail (see `docs/bugs.md`). Equivalent sanitizer assertions pass in Chromium.
- Isolated Playwright browser fixtures: project/session shortcuts, modal focus wrapping/restoration, numeric session selection, cross-project isolation, draft persistence, quote insertion, tool disclosure, current-project shelf, 390px wheel layout, and reduced motion pass.
- Screenshots and repeatable fixture/check scripts: `output/playwright/`. These are UI fixtures, not a live provider or mixed-native-agent run.
- No dependency additions. Existing Notes layout preferences migrate; Work starts with panels closed.

## September 17 — conventional project management
- [x] Replace the current-project card and scope tabs with a default sidebar: New chat, search, collapsible project folders, and indented chat titles.
- [x] Keep per-project new-chat/settings actions, archived chats, and spatial-switcher shortcuts.
- [x] Replace Work's legacy settings header with a small name/folder dialog; cancel creates nothing and project management does not open Files.
- [x] Migrate Work layout to v3 with the sidebar visible and Files closed; preserve Notes preferences.
- [x] Verify production build/budgets, 152 focused tests, and fixture-based browser navigation/settings/search checks.

## Folder drop and wheel chords
- [x] Folder field highlights on drag and accepts one native directory through Electron webUtils + a directory stat check; unsupported browser paths leave the field intact with a fallback hint.
- [x] Three wheel controls: numeric direct-open, arrow chords, WASD chords/direct QEZC. Keep the selected diagonal on key release; clear held keys on close/window blur.
- [x] Thirty workbench tests, Electron compile, production bundle checks, and browser arrow/WASD/direct-diagonal checks pass. Packaged OS drag delivery remains unverified.

- [x] Move the sidebar toggle immediately before the project name so it stays accessible when closed; add Cmd+B/Ctrl+B and verify click/keyboard reopening.

- [x] Make folder dragging discoverable with a permanent dashed drop box, folder icon, clear instruction, browse link, and manual path fallback.

- [x] Restore conventional native header dragging/double-click behavior with Electron drag regions and interactive-control exclusions; leave browser headers unchanged.

- [x] Retire Scratch as a displayed project and new-chat namespace; preserve old unassigned history in Other chats and recover new-chat ownership from its project ID.

- [x] Align folder-drop icon beside a compact left-aligned instruction block; use equal box insets and explicit spacing above the path input.

- [x] Default new project names to the selected folder name; allow manual overrides and enable creation immediately after folder selection.

- [x] Fix project-carousel arrow positions with a constant-width control row and truncated centered names; add A/W previous and D/S next alongside arrow/scroll controls.

- [x] Add project action menus with highlighted Remove from DAN, explicit confirmation, and persistent non-destructive hiding of projects/chats.

- [x] Give recovered project groups the same dots/new-chat controls and safe removal semantics as registered projects.

- [x] Move native-session import to project menus, add multi-select/Select all with compact titles and retry-safe batch handling, replace the footer with global DAN settings, and remove the empty-history status.

- [x] Use a Select all checkbox with checked/unchecked/partial states in native-session import.

- [x] Group native-session imports into populated source tabs with counts, keyboard navigation, and tab-scoped Select all; retain selections across tabs and hide the tab bar for a single source.

- [x] Limit session-delete confirmation titles to one line and 80 characters.

- [x] Tighten Work composer inner padding and input/control spacing.

- [x] Separate Lead, lead model, and Team in the composer; identify native agents as team-only until lead integration is connected.

- [x] Connect native lead adapters and DAN team delegation; package and replace `/Applications/DAN.app` while retaining the previous app backup and user data.

- [x] Consolidate agent configuration into two composer controls, Lead and Team; keep agent/account/model/reasoning/fast settings inside each dropdown and show one team agent at a time.

- [x] Match Lead/Team button styling and make dropdowns mutually exclusive using a shared native details group.

- [x] Keep loaded agent settings mounted when switching native leads; reuse the catalog and remove repetitive fast-mode reminders.

- [x] Close Lead/Team dropdowns on outside click/touch or keyboard focus leaving, retaining interactions inside the panel.

- [x] Add Delete all archived chats to the archive footer, with count/scope confirmation, progress, retryable failures, and a backend guard preserving unarchived chats.

## Selection and sidecar chat
- [x] Preserve selection through quote-toolbar updates and avoid auto-scrolling selected text.
- [x] Open separate persisted sidecar threads from the header or selected text; use the current lead, save parent lineage, and recover pending runs.
- [x] Verify 38 workbench tests, 3 API tests, production build/budgets, and Chromium selection/sidecar behavior.

- [x] Stage selected references separately, attach source IDs to requests, and grow input from one to six lines before scrolling; verify in Chromium and production build.

- [x] Hide lone active-run queue, use compact expandable waiting-message list, preserve full responses against truncated status previews, and recover affected saved messages on reopen.
