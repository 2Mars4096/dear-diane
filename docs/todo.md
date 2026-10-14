# Todo

- [x] Fix validator JSON-wrapper parsing found by the built-in pilot; 176 tests and a post-fix live note run pass.
- [ ] Proposed: repeat small-task comparisons against the fixed runtime and add a planner-activating case before changing built-in routing — [study](live-test-plans/1-super-diane-capability.md).

- [x] Run built-in agent direct/current/selective comparison using the existing evaluation framework — [pilot](live-test-plans/1-super-diane-capability.md).

- [x] Add per-worker Allow child sessions, inherited configuration, shared capacity, scoped control, cleanup and nested Team display.
- [x] Compact worker headers into one row with leading expand arrows.
- [x] Install child-session/compact-header update after idle verification; signature/archive and both health endpoints pass.
- [ ] Complete live provider child-delegation acceptance.

- [x] Configure larger context for personal-terminal and app-account Codex launches; back up and validate both TOML files.
- [ ] Verify this terminal conversation reports the larger context after restart/resume.

- [x] Add independently configured named workers with + Add worker, stable persistence, explicit profile routing and one-task-per-worker admission.
- [x] Update Claude picker and opus alias to Opus 5.5.
- [x] Install worker-roster/Opus 5.5 follow-up; signature/archive equality and backend/proxy health verified after startup retry.

- [x] Replace opaque native event details with readable activity, command output and file changes; hide empty bookkeeping and retain opt-in technical JSON. Installed October 14.

- [x] Show lead/worker hierarchy, navigate back to lead replies, and distinguish runtime completion, current-attempt review and integration in the Team tree.

- [x] Add hover opening to the sidebar Chat/tool dropdown; preserve keyboard and touch controls. Installed October 14.

- [x] Increase supported native Codex/Claude context windows; live Astra telemetry confirms 828,400 usable tokens.
- [x] Activate context changes through the installed app/backend restart.
- [ ] Verify Claude live context and installed UI interaction acceptance.
- [x] Clarify Team panel lead/worker hierarchy and separate runtime completion from lead-reviewed acceptance.

- [x] Align Team progress with Session/Task detail in the header; include in the pending local UI update.

- [x] Add shared Team worker capacity (default 2, range 1–4), persist UI preference, pass run policy, enforce concurrent admission and inform leads.
- [x] Install Team capacity with font/tab-reconnect changes; installed signature/archive/health verified.

- [x] Install font/tab-reconnect update after idle verification.

- [x] Increase Work chat/input font sizes and repair same-session stream reattachment after switching tabs; 18 focused tests and production build pass.

- [x] Fix long Codex thread resume exceeding the transport limit; verify original saved thread through production async transport without replaying user work.

- [x] Repair existing Codex thread continuation after default-account home changes; recover/pin the original configured account and activate locally.

- [x] Correct Claude default quota lookup for redirected HOME; preserve actual login and verify live quota.
- [x] Add bounded redacted Codex disconnect diagnostics and verify two installed-backend fixed-reply runs; 47 focused tests pass.
- [ ] Confirm the previously failing Codex conversation continues successfully; diagnostic runs did not replay its work.

- [x] Clarify missing Claude subscription credentials in Usage and support the default credential-file fallback; 12 usage tests pass. Source-only; activation pending.

- [x] Fix desktop Codex startup losing Node from PATH; verify initialization and 33 native/live transport tests — [native workers](UI-plans/2-native-agent-workers.md).

- [x] Fix and locally install desktop folder links opening in the system file manager — [UI](UI-plans/1-work-notes-gui.md). Signature/archive/backend/proxy verified.

- [x] Review cell-derived native management integration; fix built-in lead guidance exposure and document its optional, managed-Team-only scope — [review](UI-plans/2-1-native-session-management.md).

- [x] Archive/prune the simplified-cell worktree and apply its task-contract, compact-handoff and attempt-specific review lessons to native session management — [review](UI-plans/2-1-native-session-management.md).
- [x] Activate native session management changes during the October 14 local reinstall/backend restart.
- [ ] Verify one live delegation/review/follow-up — [acceptance](UI-plans/2-1-native-session-management.md).

- [x] Humanize and structure README; remove repeated descriptions and internal implementation commentary — [UI documentation](UI-plans/1-work-notes-gui.md).

- [x] Align macOS traffic lights with the header across zoom levels; verify native geometry at 90%, 100% and 125%.
- [x] Include title-bar alignment fix in the October 14 reinstall subsequently requested by the user.

- [x] Add About & updates navigation dot and move Back/Forward into the top bar beside the sidebar toggle; remove redundant history row — [UI](UI-plans/1-work-notes-gui.md).
- [x] Install updated Settings/navigation frontend; signed archive equality and backend/proxy health verified.

## Token analysis — October 14 assessment
- [x] Compare CodeBurn and Diane using source review, six synthetic Codex cases and five local numeric-only snapshots — [results](plans/4-3-token-usage-analyzer.md#codeburn-comparison--2026-10-14).
- [x] Fix Codex usage records/cumulative fallbacks and Claude streaming revisions; rebuild stale summaries and verify all six comparison cases plus five saved local snapshots — [accounting](plans/4-3-token-usage-analyzer.md#accounting-implementation--2026-10-14).
- [ ] Activate token-accounting fixes on the next safe backend restart.
- [ ] Proposed: cross-session replay deduplication, historical aggregation and a pinned CodeBurn JSON adapter for broader reporting — [follow-ups](plans/4-3-token-usage-analyzer.md#codeburn-comparison--2026-10-14).

- [x] Remove native-agent 30-minute execution cutoff; verify completion, steering and Stop — [native workers](UI-plans/2-native-agent-workers.md).
- [x] Activate cutoff removal: local reinstall launched the updated backend; module path and removed cutoffs verified.

## Commercial validation — discussion reconciled October 13
- [x] Recover and record actual commercialization preferences and unresolved choices — [business roadmap](business/launch-roadmap.md#october-13-2026--current-commercial-direction).
- [ ] Proposed: define the paid/free boundary and choose one recurring demo workflow before billing or broad new features. $10/month and three paying repeat users are test hypotheses/targets, not validated demand; user decision needed on the offer.

- [x] Publish v0.1.6 with conversational planning and current installed improvements — [release](plans/4-2-desktop-updates.md).

- [x] Clarify single/multiple choice controls, remove secondary selection hints, and select Other automatically when comments are typed — [Plan mode](UI-plans/4-plan-mode.md).

- [x] Add batched checkbox MCQs with Other/comments and remove empty planning greeting — [Plan mode](UI-plans/4-plan-mode.md).
- [x] Install latest planning follow-up, including batched checkbox choices, inline Other/comments and bordered answer rows; signature/archive/health verified.

- [x] Install current local app with conversational Plan mode; verify signature, matching archive and backend/proxy health — [desktop](plans/4-2-desktop-updates.md).

- [x] Simplify Plan mode to chat responses and single/multiple-choice questions; remove the Plan tab and section editing/discussion — [Plan mode](UI-plans/4-plan-mode.md).

- [x] Show readable referenced text in Up next, including previously saved queue entries — [workbench](UI-plans/1-work-notes-gui.md).

## October 13 — authorized priority batch and composer follow-up
- [x] Use `/plan` with a conditional Plan mode status instead of a composer button; retain slash-command discovery separate from skills — [Plan mode](UI-plans/4-plan-mode.md).
- [x] Prepare startup failure diagnostics and nonblocking error/quit handling; targeted regressions pass — [desktop](plans/4-2-desktop-updates.md).
- [x] Fix account-specific quota discovery and show all configured provider/account rows — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Enter steers, Cmd/Ctrl+Enter queues; remove queued status text and duplicate header Stop — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Clean targeted Electron/navigation lint and validate an isolated, signed app snapshot while preserving concurrent work.
- [x] Verify Todo integration is already merged/installed at 2.1.10; rerun current companion fixtures and confirm existing 16 unrelated Star Focus type errors — [handoff](plans/8-sticky-todo-handoff.md).
- [x] Confirm live Codex append/steer delivery from durable completed receipts and user-visible replies on October 13.
- [ ] Complete live continue-after-current queue, Regenerate/Fork, refresh, Team, reader Ask and paired Todo acceptance. Do not mark fixture checks as live acceptance.
- [ ] Reproduce any subsequent LaunchServices startup failure using the new startup receipt; root cause remains open.
- [x] Research Codex, Claude Code, and grill-me; draft a low-question Plan mode UX — [proposal](UI-plans/4-plan-mode.md).
- [x] Implement Plan mode separately from skills: durable scope/specification/steps, section/step refinement, bounded questions and reviewed-revision start; browser and live Codex planning verified — [Plan mode](UI-plans/4-plan-mode.md).
- [x] Verify and install sidebar New chat folder routing — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Verify and install project-selection restoration fix — [workbench](UI-plans/1-work-notes-gui.md).

## October 12 — low-risk backlog batch
- [x] Reconcile superseded release/Team tasks and rank all remaining cart ideas; historical receipts retained.
- [x] Review combined changes and validate frontend/backend/build; lint limitations recorded in `bugs.md`.
- [x] Discover isolated Claude provider histories, preserve history-only continuation, and fix Cursor exclusions/size-limit reporting — [native workers](UI-plans/2-native-agent-workers.md).
- [x] Remove Cmd+K's 12-result cap — [paper library](plans/4-7-paper-library.md). Semantic retrieval remains carted as IC-033.
- [x] Complete isolated keyboard, focus, persistence and narrow-layout checks; repair confirmed overflow — [UI](UI-plans/1-work-notes-gui.md).
- [x] Inspect Sticky Todo companion contracts and run local handoff checks — [handoff](plans/8-sticky-todo-handoff.md). Companion-wide typecheck fails outside handoff; merge/install/paired acceptance remain pending.

## Recent delivery
- [x] Verify and install sidebar New project action — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Verify and install project-switcher control layout — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Install matching 12px selection-toolbar/composer/message corners — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Install project-header new-chat plus shortcut — [workbench](UI-plans/1-work-notes-gui.md).
- [x] Install non-sticky appearance preview — [settings](UI-plans/1-work-notes-gui.md).
- [x] Install visual appearance presets, Neon palette and independent font roles — [settings](UI-plans/1-work-notes-gui.md).
- [x] Install automatic page-counter sizing and clear counting status — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Verify and install EPUB keyboard focus, continuous scrolling and responsive navigation — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Install stable EPUB selection, whole-book page counts, and PDF/EPUB page slider — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Reinstall latest local EPUB/reference build again at user request; startup and archive verification pass.
- [x] Install EPUB iframe file-drop repair and PDF-style reference tray — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Restore and install EPUB reference hover/pin behavior — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Install EPUB fractional-width startup fix and simpler page/size controls — [reader](UI-plans/3-reader-and-side-panel.md).
- [x] Fix physical ASAR bundle cleanup during updates; verify with real Electron — [desktop updates](plans/4-2-desktop-updates.md).
- [x] Install cleanup-fixed local 0.1.5; verify signature/archive equality and app/backend health.
- [x] Publish v0.1.5 reader improvements; verify public release and update manifest — [release](plans/4-2-desktop-updates.md).
- [x] Fix blank EPUB, unify reader controls and add PDF chapters — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Publish v0.1.4 with download progress; verify public installer and updater assets — [4-2](plans/4-2-desktop-updates.md).

- [x] Reinstall local 0.1.4 progress build and verify app/backend health — [desktop updates](plans/4-2-desktop-updates.md).

- [x] Show update download percentage and transferred/total MB using Sticky Todo’s progress pattern — [desktop updates](plans/4-2-desktop-updates.md).

- [x] Commit, push and publish v0.1.3 with one-click updates and Diane-side Sticky Todo handoff; verify public updater assets — [release](plans/4-2-desktop-updates.md).

- [x] Move old Diane rollback backups to Trash; preserve installed/staged apps — [4-2](plans/4-2-desktop-updates.md).

- [x] Use Check when no update is known and one-click Download and install with automatic restart when available; local builds stay under Update options — [4-2](plans/4-2-desktop-updates.md).

- [x] Remove the merged Sticky Todo worktree; retain its branch — [plan](plans/8-sticky-todo-handoff.md).

- [x] Close Sticky Todo pre-execution receipt crash window during merge review — [plan](plans/8-sticky-todo-handoff.md).

- [x] Merge and test local Sticky Todo handoff on main: project/profile inheritance, durable dispatch, status/stop, desktop session opening — [plan](plans/8-sticky-todo-handoff.md).
- [ ] Run paired installed-app acceptance when Diane is idle; companion integration is already merged and installed.

- [x] Publish combined 0.1.2 (EPUB, Settings redesign, anonymous updates, README); 224 frontend and nine publisher tests plus build/budgets pass — [desktop updates](plans/4-2-desktop-updates.md).

- [x] Remove update sign-in and use the public release feed; rewrite README for setup/use without personal or internal operational details — [4-2](plans/4-2-desktop-updates.md). Public-release download and installed-client acceptance remain pending.

- [x] Review Settings against OpenAI, Claude Code, and OpenCode organization/rows — [UI findings](UI-plans/1-work-notes-gui.md#settings-review-2026-10-11).
- [x] Apply Settings category navigation and consistent compact rows; browser-fixture acceptance complete — [UI review](UI-plans/1-work-notes-gui.md#settings-review-2026-10-11).

- [x] [Linked-app credential fallback](plans/4-15-shared-provider-accounts.md): Todo reads Diane keys first, then its own; .env and Keychain unchanged.
- [x] Published-update bootstrap superseded by public releases through v0.1.5; private-feed authentication is retired. Signed remote-upgrade acceptance remains in the release backlog — [desktop updates](plans/4-2-desktop-updates.md).
- [x] EPUB reading with PDF-equivalent reading tools — [reader](UI-plans/3-reader-and-side-panel.md).
- IC-045: Possible Tauri migration is deferred in the [idea cart](../.agents/idea-cart.md); current Electron works well. Preserve feature/data parity if revisited; not committed work.
- [x] Guard OCR selection in line gaps and remove the delayed zoom-position/canvas-size step — [reader](UI-plans/3-reader-and-side-panel.md). Installed/reopened and health-verified.
- [x] Default sidebar/Reading input to two lines while retaining automatic growth — [reader](UI-plans/3-reader-and-side-panel.md). Installed/reopened; signature/archive equality and backend health verified.
- [x] Match sidebar/Reading composer controls to Work, isolate worker settings, fix request validation, and include whole-file context — [reader](UI-plans/3-reader-and-side-panel.md). Installed/reopened; signature/archive equality and direct/proxy health verified.


- [x] Automatically dismiss the PDF OCR-ready badge after two seconds — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Link saved PDF highlight clicks to their Notes entry while preserving selection and unsaved edits — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Optimize PDF expand/restore: coalesce resize redraws, reuse line geometry and preserve reading position; installed and verified — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Save Ref immediately and enrich quotes in the background; avoid redrawing previews for text edits; installed and verified — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Restore continuous scanned-line selection, exclude detached binding noise, and fill missed-word geometry from rendered pixels while preserving OCR caches; installed and verified — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Reuse saved OCR on reopen; recognize only visible/neighboring pages and persist browser-copy results by PDF content; installed and verified — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Restore selection beyond the first 40 scanned pages: prioritize visited pages and save completed OCR incrementally; reinstalled again on October 4 and health-verified — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Reinstall project auto-selection and seconds-only timer fixes; signature, archive equality and backend health pass.

- [x] Show activity durations under one minute in seconds only — [navigation](plans/4-13-unified-workspace-navigation.md).

- [x] Select newly created projects in the project selector after folder drops or dialog creation — [navigation](plans/4-13-unified-workspace-navigation.md).

- [x] Fix PDF embedded-font rendering with Electron-compatible PDF.js main/worker builds; reproduced and visually verified missing ligatures; reinstalled and health-verified — [reader](UI-plans/3-reader-and-side-panel.md).

- [x] Crop existing icon to painted area, retaining white textured background.

- [x] Restore white icon background and remove only the raised outer rim.

- [x] Remove the full ivory icon tile and use the standalone painted radio silhouette.

- [x] Remove white app-icon edges; preserve ivory tile and export transparent outer corners.

- [x] Apply selected coral walkie-talkie artwork and matching menu-bar silhouette; export desktop/browser sizes.

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
  - [ ] [Native agent workers](UI-plans/2-native-agent-workers.md) — Codex/Claude/Antigravity worker baseline and opt-in forked imports delivered; team progress/transcripts, explicit restart-resume and Codex approval transport delivered; mixed-agent live acceptance, adapter-specific gaps and browser thumbnails remain.
- [x] [5-universal-product-cutover](plans/5-universal-product-cutover.md) — deleted the backed-up legacy tree, renumbered docs, validated, committed, and pushed.

## Live evaluation

- [x] [1-super-dan-capability](live-test-plans/1-super-diane-capability.md)
- [x] [2-super-dan-flagship](live-test-plans/2-super-diane-flagship.md)
- [ ] [3-human-assist](live-test-plans/3-human-assist.md)

## Backlog
- [x] [4-5-browser-research](plans/4-5-browser-research.md) — ordinary-chat browser tools and native bridge; visible sessions, cleanup, 422 tests, live Codex → Scholar → BibTeX, and installed-app chat acceptance verified.
- [ ] Verify native Claude browser use in the installed app after the selected account signs in; current default account returns `/login` before browsing.
- [x] Include Claude's isolated API-provider transcript stores in native discovery/token analysis; imported provider histories never resume external native sessions.
- [x] Raise native import limit to 256 MB, resolve native/legacy placeholder titles, leave new projects empty, and expand Up next with checkpoint-receipt filtering — [workbench](plans/4-1-agent-workbench.md), [native workers](UI-plans/2-native-agent-workers.md).
- Mac signing/notarization depends on Apple Developer setup; tracked with the standalone-distribution/remote-upgrade item below. Private-feed validation is superseded by the public feed.
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

- Team observer is implemented; the fresh built-in Codex spawn acceptance item below replaces the earlier enabled-Team workaround.
- [x] Preserve modern Cursor header exclusions across legacy fallback; reject chats above 20,000 bubbles explicitly instead of silently truncating.

- [x] Show direct built-in Codex subagents in Team without requiring enabled DAN delegates; validate real-record replay and lifecycle/control tests.
- [ ] Verify a fresh built-in Codex spawn in the installed app after deploying the Team observer/client update.

- [x] [In-app desktop updates](plans/4-2-desktop-updates.md): staged local app replacement/relaunch, update settings, and standard configured release downloads.
- [ ] Configure signed published desktop releases and validate a real remote upgrade; package/version the Python backend for standalone distribution.

- [x] Connect private GitHub release updates and in-app browser sign-in; verify account/repository access. First signed release and live download/install acceptance remain pending.

- [x] Keep queued messages out of the transcript; show them in Up next with Remove; no fake queued replies.
- [x] Live-verify Up next append/steer delivery; user confirmed October 13.
- [ ] Live-verify Up next continue-after-current delivery in the installed app.

- [x] Paper search follow-up: verified and installed the pointer/keyboard selection fix; user confirmed working ([4-7](plans/4-7-paper-library.md)).

- [x] Minimal model-selector update: Default label, Native/API grouping, direct provider choices, local Settings key entry, and capability-aware reasoning controls.

- [x] Fix visible roster workers silently excluded by the old availability checkbox; settings affect new runs. Installed October 14.

- [x] Fix Claude worker HOME inherited from Codex account wrapper; normal Claude auth status succeeds. Activated by October 14 reinstall/backend restart.

- [x] Reorganize Team run history: current first, newest-first collapsed history, request titles, timestamps and worker counts.
- [x] Install Team-history, compact progress pill and worker-activity fixes; idle/signature/archive/backend/proxy verified.

- [x] Make Team header strip fit its contents with explicit Lead label, worker separators, truncation and existing two-worker/+N summary. Installed October 14.

- [x] Hide repetitive worker streaming fragments in activity details; retain completed content, commands and errors. Installed October 14.

- [x] Clean up approved obsolete Diane packages; 20 items removed, current app/latest rollback/build and release source records preserved.

- [x] Apply Team changes on the next sent message: live steer updates active roster/capacity; queued messages save settings for their next run. Preserve existing workers.
- [x] Install message-applied Team settings, readable activity, caption removal and model dropdown fix; signature/archive/health verified.
- [ ] Verify live provider acceptance of message-applied Team settings.

- [x] Remove the introductory Team sidebar caption.

- [x] Replace raw worker-session events with readable messages/activity; remove technical JSON from main conversation event details.

- [x] Keep model presets selectable alongside custom/older saved model IDs.

- [x] Enforce worktree separation for Git editing workers, shared read-only inspection, reviewed automated integration and current-attempt merge receipts.
- [x] Improve team guidance for file ownership, scope changes, permission failures and focused tests.
- [ ] Install worktree/review policy update when idle; verify one live isolated edit → review → apply cycle.

- [x] Review and harden worktree/read-only/review/merge paths; 93 targeted tests pass. Installed live-provider acceptance remains pending.

- [x] Stabilize streaming conversation layout: retain meaningful activity with stable IDs, bound live detail height and pause auto-follow while inspecting details.

- [ ] Publish v0.1.7 with Team/worktree, session reliability and UI improvements; verify release assets and update manifest.
