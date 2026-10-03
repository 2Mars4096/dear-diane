# Todo

- [x] Fix blank menu-bar icon with a native monochrome template and generate two updated app-icon concepts; retain current app icon pending choice.

- [x] Rename repository source files/folders and package imports to Diane; validate and commit by feature — [migration](diane-name-migration.md).

- [x] Add selected-crop vision transcription and offline-first comments; fix Chinese selection geometry, page arrangement and Notes resizing — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Install the reader follow-up and apply/verify six existing quote repairs; comment bodies/geometry unchanged, persisted quotes and app/backend health verified after restart.
- [ ] Complete runtime/profile Diane naming through the [reviewable migration](diane-name-migration.md); broad rename rejected by automatic review, partial edits undone.

- [x] Fix OCR selections spanning both pages: detect/crop at the gutter, retain each page's reading order, invalidate old caches, and add layout override. Shorten Ref and make only Ask primary — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Install the OCR/selection-toolbar follow-up; signature, archive equality and owned backend/proxy health pass.


- [x] Restore Reading entries after restart, including browser copies, library links, active document, and older recent-file history — [document workspace](plans/4-6-document-workspace.md).
- [x] Install restart-persistence follow-up; signed archive matches and relaunched app/backend/proxy health pass.
- [x] Use Diane in remaining visible skill/browser prompts; retain compatible package, environment, and storage identifiers.
- [x] Investigate GitGuardian Authentication Tuple alert: historical import-map false positive, removed August 2; no credential on the flagged line. GitGuardian incident disposition remains external.


- [x] PDF selections and anchored Refs: select scanned areas without OCR, stage cropped images in chat, and preview saved text/area highlights at their location — [reader](UI-plans/3-reader-and-side-panel.md). Installed and health-verified on October 3.


- [x] Separate five recent open reading tabs from project chats with Show all; add native desktop Back/Forward event routing and Go menu.
- [ ] User acceptance: test Logitech Back/Forward buttons after the native navigation update.

- [x] Main-pane document tabs, Back/Forward mouse navigation, resizable side tools, header/menu cleanup and streaming activity deduplication — [document workspace](plans/4-6-document-workspace.md).
- [x] Install and health-verify pane/navigation update after idle checks; signature and archive match.

- [x] Isolate Diane Codex sessions and migrate legacy continuations through saved history; preserve account selection, child tracking, imports and usage — [native workers](UI-plans/2-native-agent-workers.md).
- [x] Activate Codex isolation with an idle restart; backend/proxy/setup health pass.

- [x] Settle stale Thinking indicators from terminal task polling and preserve current messages on execution save — [native workers](UI-plans/2-native-agent-workers.md).

- [x] General chat attachments, terminal queue receipts, and refresh without a replacement setup screen implemented and regression-tested — [files](plans/4-6-document-workspace.md), [workers](UI-plans/2-native-agent-workers.md), [setup](plans/4-14-openrouter-setup.md).
- [x] Install attachment, queue receipt, refresh, and failed-run fixes after idle checks; signature, archive, backend/proxy health, and reported-session browser checks pass.

- [x] Default file sidecar, in-panel opening failures, native file actions, and PDF/text annotations installed and health-verified — [4-6](plans/4-6-document-workspace.md).

- [x] Fix accepted steer/answer ordering, promoted reply identity, and awaited owned-process shutdown; installed and health-verified — [native workers](UI-plans/2-native-agent-workers.md).

- [x] Thinking duration and observed activity installed and verified — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] [4-14-openrouter-setup](plans/4-14-openrouter-setup.md) — first-run gate, key validation, and optional future-session titles installed and verified.

- IC-043 pane layouts/shared composer and IC-044 floating Diane command bar are exploratory in the [idea cart](../.agents/idea-cart.md); not checked out.

- [x] First-request title summaries installed; opt-in for future sessions only, existing-session transfers disabled — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] Restore response-end Edit/Regenerate/Fork controls — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] Resizable sidebar and persistent project scope/session restoration — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] Direct permanent session deletion with danger styling and confirmation — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] Restore sidebar project actions and native-session import entry point — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] Refine unified navigation controls, palette badges, and single-line rows; verified and installed 2026-10-02 — [4-13](plans/4-13-unified-workspace-navigation.md).

- [x] [4-13-unified-workspace-navigation](plans/4-13-unified-workspace-navigation.md) — one list for sessions/documents, clear project labels, and native Cmd hints; verified and installed on 2026-10-01.

- [x] [4-12-session-actions](plans/4-12-session-actions.md) — shared session menu, optional pins, one-row session tabs, and independent windows. Installed and launch-verified on 2026-10-01.

- [x] Reinstall automatic recent-session navigation; lower Chat/document tabs preserved — [4-11](plans/4-11-workflow-friction.md).

- [x] Install and verify the reviewed workflow/session-tabs build — [4-11](plans/4-11-workflow-friction.md).

- [x] [4-11-workflow-friction](plans/4-11-workflow-friction.md) — IC-034–037 implemented and revised after one review round: 381 frontend/132 backend tests, browser layouts, production build, and live Codex isolation/application/recovery pass. Also adds ordered cross-project session tabs and browser-style number shortcuts. IC-038/039 remain in the [idea cart](../.agents/idea-cart.md).

- [x] Compact Settings update rows and progressively disclose secondary options — [6-2](plans/6-2-ssh-connection-setup.md).

- [x] Restrict Connections to SSH setup/checks and organize Settings into named sections with jump navigation — [6-2](plans/6-2-ssh-connection-setup.md).

- [x] Show remaining quota as a blue fill and shorten the settings entry/title to Settings — [4-1](plans/4-1-agent-workbench.md).

- [x] Make Edit, Regenerate, and Fork icon-only with tooltips and accessible names — [4-1](plans/4-1-agent-workbench.md).

- [x] Align the session wheel with project-card styling and verify keyboard/narrow-screen behavior — [4-1](plans/4-1-agent-workbench.md).

- [x] Move project switching to left-hand Cmd/Ctrl+Shift+E — [4-1](plans/4-1-agent-workbench.md).

- [x] Install and verify the side-panel tool-switcher desktop update — [reader and side panel](UI-plans/3-reader-and-side-panel.md).

- Paper-search concept recall and Cmd+K result-limit improvements are carted as IC-033 in the [idea cart](../.agents/idea-cart.md); deferred, not committed work.

- [x] Strengthen Cmd/Ctrl+K paper-search selection with theme-accent fill, outline, and icons — [4-7](plans/4-7-paper-library.md).

- [x] Keep `docs/plans/` and specialized `docs/*-plans/` local and excluded from future commits; plan links refer to local working copies.

- [x] [4-10-dear-diane](plans/4-10-dear-diane.md) — Dear Diane product/Diane assistant rename; compatibility tests and signed local desktop build verified; installed Dear Diane and moved old DAN.app to Trash.

- [x] Fix Mac top-bar double-click expansion/restoration and center window controls; verified in isolated Electron — [4-1](plans/4-1-agent-workbench.md).
- [x] Install the top-bar update; user interaction check remains pending — [4-1](plans/4-1-agent-workbench.md).

Ideas awaiting checkout: [idea cart](../.agents/idea-cart.md). IC-040–042 are implemented in [4-12](plans/4-12-session-actions.md) and removed from the active cart. IC-031/032 are now planned in [7-phone-personal-agent](plans/7-phone-personal-agent.md); remaining ideas stay exploratory.

- [x] Reinstall and verify the Literature desktop update — [4-8](plans/4-8-literature-import.md).

- [x] Verify and install startup/closing flash fix — [4-1](plans/4-1-agent-workbench.md).

- [x] [4-8-literature-import](plans/4-8-literature-import.md) — Literature side panel with persistent batch import, citation matching, notes, and safe KB ingestion.

- [x] Complete [code consolidation](plans/4-9-code-consolidation.md): reading ownership, shared requests/polling, generated preload, workspace/file targets, and backend helpers; isolated validation passes and implementation batches pushed.

- [x] Simplify SSH settings to compact rows and a host details panel — [6-2](plans/6-2-ssh-connection-setup.md).

- [x] Make Codex/Claude conversation file paths openable with native context actions — [4-6](plans/4-6-document-workspace.md).
- [x] Install and verify conversation file-link update — [4-6](plans/4-6-document-workspace.md).

- [x] Main workspace folder drops create/select projects with a visible drag boundary — [4-1](plans/4-1-agent-workbench.md).
- [x] Install the main workspace folder-drop update and verify signature/archive/backend health — [4-1](plans/4-1-agent-workbench.md).
- [ ] User acceptance: drag a Finder folder into the updated main workspace — [4-1](plans/4-1-agent-workbench.md).

- [x] Verify a read-only Codex turn on mini's imported Dropbox project and save the result in its DAN chat — [6-3](plans/6-3-remote-project-folders.md).

- [x] [4-7-paper-library](plans/4-7-paper-library.md) — host-wide paper search, Hugo metadata/notes, and resumable reading sessions; browser verified, signed desktop update installed, and 304-paper catalogue healthy.

- [x] Open existing mini folders as remote DAN projects with an inline folder chooser; deployed and opened a Dropbox project — [6-3](plans/6-3-remote-project-folders.md).

- [x] Restore mini in the desktop Settings connection list and verify existing SSH/private-relay services — [private relay](plans/6-1-private-relay.md).

- [x] Simplify adding SSH hosts to a Codex-style dialog; support manual hosts, optional ports/keys, and separate phone setup — [6-2](plans/6-2-ssh-connection-setup.md).

- [x] Add local PDF font compatibility mode and verify an embedded-font fixture — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Apply PDF font compatibility automatically and remove the Repair text button and per-file preference — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Reinstall and verify the automatic PDF font update — [reader](UI-plans/3-reader-and-side-panel.md).
- [ ] Private PDF appearance remains unverified; no file access requested again.

- [x] Preserve right-panel state and add a dedicated close-tab shortcut; verify desktop and phone switching — [reader and side panel](UI-plans/3-reader-and-side-panel.md).

- [x] Repair packaged Electron update discovery and install the PDF fixes; verify actual app health and Unicode PDF delivery — [desktop updates](plans/4-2-desktop-updates.md).

- [x] Fix Chinese PDF filename headers and scanned-page decoder paths; verify the user book and prepare a signed local update — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Automatically find prepared desktop updates, shorten update errors, and place Usage first in the sidebar footer — [desktop updates](plans/4-2-desktop-updates.md).

- [x] Reinstall DAN with ivory v7; verify launch and backend health; remove rejected icon variants and retain selected source/prompt.

- [x] Adopt selected ivory v7 DAN icon for new desktop packages and web favicon; source assets in `editor/resources/icons/`.

- [x] Explore purple-free impressionist DAN icon palettes; selected ivory v7 retained in `editor/resources/icons/`, other variants removed.

- [x] Explore colorful gouache and impressionist DAN icons; completed exploration, unused variants removed.

- [x] Revise DAN icon to represent agent coordination without alphabet forms; design carried into selected v7.

- [x] [DAN icon concept](UI-plans/1-work-notes-gui.md) — generated retro-futuristic artwork and saved its prompt in `output/imagegen/`.

## Universal product stack
- [x] Replace crowded side-panel tabs with a labeled tool switcher; verify narrow layouts and keyboard access — [reader and side panel](UI-plans/3-reader-and-side-panel.md).

- [ ] [7-phone-personal-agent](plans/7-phone-personal-agent.md) — staged plan: both phones/browser first; commitment capture, durable tasks, approvals, Google then broader providers, phone pilot, and follow-on workflows. Implementation not started.

- [ ] [6-remote-control](plans/6-remote-control.md) — Settings profiles, SSH installer, authenticated `ny` relay, and shared projects deployed/tested on mini; direct VPN verified; physical-phone acceptance pending, s600 reserved for user testing.
  - [ ] [6-1-private-relay](plans/6-1-private-relay.md) — mini/ny services and native/OpenRouter live tests pass; direct VPN verified; physical-phone acceptance pending.

- [x] [1-universal-cell](plans/1-universal-cell.md) — one typed, brief-driven execution cell.
- [x] [2-universal-organism](plans/2-universal-organism.md) — one dependency-aware organism runtime.
- [x] [3-super-dan](plans/3-super-diane.md) — general live runtime, tools, hooks, blueprints, and TUI.
- [ ] [4-work-notes-gui](plans/4-work-notes-gui.md) — active Work/Notes UX refinement.
  - [x] [4-6-document-workspace](plans/4-6-document-workspace.md) — drop/open PDFs, editable text and Markdown tabs, media viewers, guarded Save, and browser-copy downloads.
  - [x] [4-4-agent-model-selection](plans/4-4-agent-model-selection.md) — independent harness/model-source/model choices; live Codex/Claude OpenRouter routing verified before SSH work.
  - [ ] [UI plan](UI-plans/1-work-notes-gui.md)
  - [x] [4-1-agent-workbench](plans/4-1-agent-workbench.md) — lightweight conversation, native window header, conventional project/chat sidebar with persistent Cmd+B toggle, optional carousel/wheel with directional chords, visible folder drop box, activity disclosures, quote reply, stable selection, source-linked reference staging, one-to-six-line input, compact follow-up queue, full-response recovery, and saved sidecar chat.
  - [x] Stop → follow-up lifecycle: fresh control state, run-scoped terminal events, and honest no-run feedback — [4-1](plans/4-1-agent-workbench.md).
  - [x] Open saved message attachments by filename with sizes and unavailable-file feedback — [4-1](plans/4-1-agent-workbench.md).
  - [x] Copyable session IDs, ID/title lookup, and native-agent session discovery context — [4-1](plans/4-1-agent-workbench.md).
  - [x] Waiting follow-ups appear only in Up next until delivery; preserve durable history and bind promoted replies — [4-1](plans/4-1-agent-workbench.md).
  - [x] Quieter activity tracking with current-action summaries and on-demand work details — [4-1](plans/4-1-agent-workbench.md).
  - [x] Stable session titles: name from first request once, never display the latest task as the title — [4-1](plans/4-1-agent-workbench.md).
  - [x] Codex live steering and per-message Steer now in Up next; acceptance-gated delivery — [native worker plan](UI-plans/2-native-agent-workers.md).
  - [x] FIFO continuation delivery across partial draining/new arrivals/reload; remove reused-rank sorting — [4-1](plans/4-1-agent-workbench.md).
  - [x] Latest-message Edit with attachment-preserving resend, cancellation, active-run guard, and failed-send recovery — [4-1](plans/4-1-agent-workbench.md).
  - [x] [4-3-token-usage-analyzer](plans/4-3-token-usage-analyzer.md) — per-session and combined token analyzer in Settings with Jev stage labelling.
  - [ ] [Reader and side panel](UI-plans/3-reader-and-side-panel.md) — persistent side panel, phone layout, and PDF reader delivered; live Ask answer run and private-PDF font verification remain.
  - [ ] [Native agent workers](UI-plans/2-native-agent-workers.md) — Codex/Claude/Antigravity worker baseline and opt-in forked imports delivered; team progress strip/panel delivered; live acceptance, restart-resume, approval transport, and browser thumbnails remain.
- [x] [5-universal-product-cutover](plans/5-universal-product-cutover.md) — deleted the backed-up legacy tree, renumbered docs, validated, committed, and pushed.

## Live evaluation

- [x] [1-super-dan-capability](live-test-plans/1-super-diane-capability.md)
- [x] [2-super-dan-flagship](live-test-plans/2-super-diane-flagship.md)
- [ ] [3-human-assist](live-test-plans/3-human-assist.md)

## Backlog
- [x] [4-5-browser-research](plans/4-5-browser-research.md) — ordinary-chat browser tools and native bridge; visible sessions, cleanup, 422 tests, live Codex → Scholar → BibTeX, and installed-app chat acceptance verified.
- [ ] Verify native Claude browser use in the installed app after the selected account signs in; current default account returns `/login` before browsing.
- [ ] Include Claude's isolated OpenRouter transcript directory in native discovery/token-analysis coverage.
- [x] Raise native import limit to 256 MB, resolve native/legacy placeholder titles, leave new projects empty, and expand Up next with checkpoint-receipt filtering — [workbench](plans/4-1-agent-workbench.md), [native workers](UI-plans/2-native-agent-workers.md).
- [ ] Complete Mac release signing/notarization and live private-release validation after Apple Developer account setup — [desktop updates](plans/4-2-desktop-updates.md).
- [x] Add all eight supplied light/dark palettes to Settings, apply changes immediately, and retain the selection across launches.
- [x] Resume waiting follow-ups automatically after restart; preserve account/policy and acknowledge completed delivery without replaying it on later restarts.
- [x] Hide injected messages from the composer queue, remove waiting-list truncation, and restyle as Up next with expandable previews.

- [x] Refresh Work dark mode with a warm charcoal/sand palette across conversation, sidebar, composer, menus, and switchers.

- [x] Remove the misleading CLI-default running/queued label above the Work composer and collapse its empty row; retain exceptional feedback.

- [x] Remove the legacy Scratch pseudo-project; retain unassigned chat history and store new chats under project IDs.
- [ ] Share project registry and legacy chat bindings between browser and desktop; currently each profile persists them locally.

- [ ] Continue accessibility and phone-width Work/Notes QA.
- [ ] Reduce Super DAN time/token waste while preserving evidence and validation.
- [ ] Run approved academic and market human-assist cases.

- [x] Align project folder-drop contents and normalize input spacing.

- [x] Default new project names to the selected folder name; allow manual overrides and enable creation immediately after folder selection.

- [x] Install Google Antigravity CLI (`agy` 1.2.5) and verify DAN discovery; first sign-in required.

- [x] Fix project-carousel arrow positions with a constant-width control row and truncated centered names; add A/W previous and D/S next alongside arrow/scroll controls.

- [x] Add project action menus with highlighted Remove from DAN, explicit confirmation, and persistent non-destructive hiding of projects/chats.

- [x] Give recovered project groups the same dots/new-chat controls and safe removal semantics as registered projects.

- [x] Move native-session import to project menus, add multi-select/Select all with compact titles and retry-safe batch handling, replace the footer with global DAN settings, and remove the empty-history status.

- [x] Use a Select all checkbox with checked/unchecked/partial states in native-session import.

- [x] Group native-session imports into populated source tabs with counts, keyboard navigation, and tab-scoped Select all; retain selections across tabs and hide the tab bar for a single source.

- [x] Raise native transcript import size limit from 32 MB to 128 MB.

- [x] Limit session-delete confirmation titles to one line and 80 characters.

- [x] Tighten Work composer inner padding and input/control spacing.

- [x] Separate Lead, lead model, and Team in the composer; identify native agents as team-only until lead integration is connected.

- [x] Connect native lead adapters and DAN team delegation; package and replace `/Applications/DAN.app` while retaining the previous app backup and user data.

- [x] Consolidate agent configuration into two composer controls, Lead and Team; keep agent/account/model/reasoning/fast settings inside each dropdown and show one team agent at a time.

- [x] Match Lead/Team button styling and make dropdowns mutually exclusive using a shared native details group.

- [x] Keep loaded agent settings mounted when switching native leads; reuse the catalog and remove repetitive fast-mode reminders.

- [x] Close Lead/Team dropdowns on outside click/touch or keyboard focus leaving, retaining interactions inside the panel.

- [x] Add Delete all archived chats to the archive footer, with count/scope confirmation, progress, retryable failures, and a backend guard preserving unarchived chats.

- [x] Hide idle/raw Work status text; open Lead/Team menus on hover.

- [x] Unify idle/running sidebar chat rows; always show progress and Stop for running chats.

- [x] Regenerate/Fork for the latest request; reattach live runs after refresh; replace dev-style run status with readable live actions.
- [ ] Live-verify Regenerate/Fork and refresh reattach against a real Codex/Claude lead run.

- [x] Plan/Auto/Full access modes mapped to Codex/Claude/DAN settings; drop duplicate conversation title.
- [ ] Live-verify Claude Auto mode sandboxed Bash and Antigravity permission flags.

- [ ] Verify the live Team display with Codex enabled before turn start; built-in Codex collaboration currently does not populate it.
- [ ] Fix Cursor fallback exclusion handling and explicit large-transcript limit reporting (see native workers plan).

- [x] Show direct built-in Codex subagents in Team without requiring enabled DAN delegates; validate real-record replay and lifecycle/control tests.
- [ ] Verify a fresh built-in Codex spawn in the installed app after deploying the Team observer/client update.

- [x] [In-app desktop updates](plans/4-2-desktop-updates.md): staged local app replacement/relaunch, update settings, and standard configured release downloads.
- [ ] Configure signed published desktop releases and validate a real remote upgrade; package/version the Python backend for standalone distribution.

- [x] Connect private GitHub release updates and in-app browser sign-in; verify account/repository access. First signed release and live download/install acceptance remain pending.

- [x] Keep queued messages out of the transcript; show them in Up next with Remove; no fake queued replies.
- [ ] Live-verify Up next delivery (append and continue lanes) against a real run in the installed app.

- [x] Paper search follow-up: verified and installed the pointer/keyboard selection fix; user confirmed working ([4-7](plans/4-7-paper-library.md)).

- [x] Minimal model-selector update: Default label, Native/API grouping, direct provider choices, local Settings key entry, and capability-aware reasoning controls.
