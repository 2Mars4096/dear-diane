# 4-1: Lightweight agent workbench

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Make conversation the primary surface, with spatial project/session navigation and inspectable agent activity.

## Tasks
- [x] Prevent white startup/closing surfaces: gate native reveal on themed load and first paint, match native/page backgrounds, and stop services after windows close.
- [x] Verify and install window appearance update: six focused tests, production build/budgets, Electron compilation, real Electron dark/light first-visible pixels, live background changes and cancelled-close checks; installed archive/signature/owned-backend health pass.
- [x] Keep new/empty projects without auto-created chats; prevent last-session restore across an empty project selection.
- [x] Resolve native and legacy placeholder session names from the first nonempty request in the shared store; preserve established titles.
- [x] Expand Up next by default and recognize legacy checkpoint-append queue receipts without showing waiting pairs in the transcript.
- [x] Live Codex lead steering via persistent app-server, active-run capability, acknowledgement-gated queue delivery, and per-entry Steer now controls.
- [x] Remove routine running/queued status above the composer and its empty row; trace CLI-default mismatch to the legacy model placeholder while preserving lead-account routing and exceptional feedback.
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
- September 20 session/queue fixes: 21 backend and 148 frontend tests plus production build/budgets pass. Prepared local arm64 app: `editor/release/session-fixes/mac-arm64/DAN.app`; signature verified. Browser loopback was denied by the sandbox; installed-app acceptance remains pending.
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

## September 20 — edit latest request
- [x] Add inline Edit, Cancel, and Save & resend on the latest user message; preserve attachments and disable during active/pending replies.
- [x] Replace the last request and following answer through existing persistence/run paths; restore the transcript and retain the draft when admission fails.
- [x] Verify component failure/cancel handling, isolated browser resend/completion and 390px layout, and production build/budgets. Installed-app validation awaits a new client build.

## Stop and subsequent requests
- [x] Clear task interruption metadata on new/continued runs while retaining the stopped run's audit record.
- [x] Prevent old-run terminal events from settling a newer run on the same task.
- [x] Resolve no-run admission replies with their explanation and clear the pending indicator.
- [x] Validate 74 backend tests, 144 workspace tests, and production build/budgets.

## Open saved message attachments
- [x] Replace filename-only attachment rows with keyboard-accessible links, file sizes, captions, and missing-file feedback.
- [x] Open all saved file types in their desktop default application; use the existing preview/download endpoint in browser mode. Large files are not embedded or eagerly loaded into the transcript.
- [x] Test PNG/MP4/DOCX/PDF/TXT opening dispatch, escaped filenames, unavailable originals, and failed native opening; production build/budgets pass. Native application launch acceptance awaits updated client.

## Session identity and lookup
- [x] Expose the existing durable session ID beside conversation actions and copy it to clipboard; clarify that sidebar search accepts IDs. No new IDs or migrations.
- [x] Add optional ID/title/workflow filtering to the existing session-list API; preserve workflow-qualified transcript retrieval and disambiguate duplicate titles.
- [x] Give native leads their session/run identity and read-only discovery locations, without automatically importing unrelated histories or messaging peers.

## Queue-only waiting messages
- [x] Keep waiting follow-ups and queue acknowledgements out of the main Work transcript; maintain durable message records for restart/history.
- [x] Link new queue commands to client user/reply IDs; show the user request once delivered and bind a promoted run to its own reply placeholder. Support legacy queued-message pairs by matching text plus queue acknowledgement.
- [x] Test waiting/delivered transitions and legacy history filtering; 146 focused tests and production build/budgets pass.

## Quieter activity tracking
- [x] Replace mechanical activity counts with an observed current-action summary during a written reply, and Work details after completion. Keep commands/event records behind the existing disclosures.
- [x] Hide routine transcript state footers; retain failed, stopped, blocked, and input-needed feedback.
- [x] Verify action classification, production build/budgets, and browser disclosure access.

## Stable session titles
- [x] Stop substituting the newest task request for placeholder titles in session cards.
- [x] Save the first user message as the title for unnamed/default sessions; preserve established names and explicit rename requests.
- [x] Test initial naming, follow-up persistence, manual rename, and task-history display.

## Queue FIFO audit
- [x] Verify composer follow-up rows and checkpoint claims preserve persisted enqueue order.
- [x] Fix terminal continuation promotion: lane-local, reused position values must not reorder messages; iterate the durable enqueue list.
- [x] Test partial drain, new arrivals, mixed lanes at terminal promotion, and store reload. Explicit live steering retains its separate checkpoint behavior.

## September 28 — main workspace folder drops
- [x] Create/select a project directly from a native folder drop, using the folder name and reusing an already listed project with the same path.
- [x] Outline the main workspace during external drags with folder/project and file-opening instructions; clear on drop, leave, Escape, drag end, or blur.
- [x] Preserve PDF/file opening and project-settings drop ownership; reject multi-folder drops, unavailable browser paths, and local folders on remote connections.
- [x] Eight focused tests and production build/bundle budgets pass.
- [x] Install the signed arm64 update with rollback; verify installed archive equality and desktop-proxy health with an app-owned backend.
- [ ] User acceptance: verify a Finder folder drop in the installed desktop app.
