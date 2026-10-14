# 4-2: In-app desktop updates

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** in-progress
**Goal:** Install prepared local builds and receive configured published desktop releases from DAN settings without requiring a second agent application.

## Tasks
- [x] Install October 14 Team tree/capacity/context/activity/font/reconnection update; idle preflight, signature, exact archive equality and both health endpoints pass.
- [x] October 14: install folder-link/current-source build under explicit user authorization; verify idle state, signature, installed/source archive equality and healthy owned backend/proxy. Initial launch stalled during Python directory enumeration; subsequent launch healthy, cause unresolved.
- [x] Add Settings update status, local build selection, release checks/downloads, and explicit install/restart.
- [x] Validate and stage local macOS bundles; use a detached installer that waits for app/backend exit, preserves user data, and rolls back replacement failures.
- [x] Refuse installation during active runs/queued work or while using an externally owned backend.
- [x] Wire the standard release updater with explicit download/install and honest unconfigured-channel state.
- [x] Test handoff failures, active-work checks, UI states, preload contracts, and production packaging.
- [x] Document release signing/feed requirements and the one-time bootstrap for older installed builds.

## Decisions
- Local development builds automatically discover the recorded/remembered or `DAN_LOCAL_UPDATE_PATH` build and validate its signature before installation; do not run Git pull or dependency installers in the app.
- Remote releases use electron-updater and configured app-update.yml; no channel is fabricated or published.
- Current Python backend remains separately installed; local source fixes take effect on backend restart. Standalone backend bundling is a separate release prerequisite.
- Installation is a user action. Checking/downloading never quits the app automatically.

## Validation and release boundary
- 15 focused update/backend lifecycle tests pass, including rollback, active work, IPC origin, manual download/install, and UI state. Production typecheck/build/budgets and Electron compilation pass.
- The existing installed app lacks update IPC. `editor/scripts/install-local-update.cjs` provides a one-time native prompt after work finishes; it stages the same validated bundle and uses the same detached installer.
- The private GitHub channel is configured for `2Mars4096/deep-agent-network`. Browser login and repository access were verified as `2Mars4096`; no releases exist yet. Production signing and a real signed remote update acceptance run remain release prerequisites.
- Replacement/open-command failures restore the previous bundle. A later application crash is not automatically rolled back; the previous bundle remains available.
- September 20 bootstrap: signed arm64 build and staged copy verified; detached helper started with verified app-owned backend PIDs. It is waiting for active/queued work before showing the native install prompt. Installation/relaunch has not yet occurred.

- [x] Add GitHub browser sign-in/device code/cancel/connection status to Updates using the installed GitHub CLI as a credential helper; load its token only inside Electron for private release checks.
- Validation: nine focused update/auth/UI tests pass; private repository access rechecked successfully after browser authorization.
- September 20 follow-up: GitHub ADMIN access confirmed; no releases published. Eight auth/updater/UI tests pass. Installed `/Applications/DAN.app` still lacks update/sign-in modules; prepared arm64 bundle contains both. Existing bootstrap helper is running with the correct app-owned backend; do not start a duplicate. Keychain check outside the sandbox confirms zero valid signing identities.
- [ ] Complete Developer ID signing/notarization after operator supplies Apple Developer account availability; verify a signed remote release on a test installation.

## September 24 update usability
- [x] Record the local build path during Electron compilation; discover changed app archives even when the version number is unchanged. Cache comparisons until archive metadata changes.
- [x] Offer Install local update with no file picker; Check for updates prefers prepared local builds. Keep manual selection under Other update options.
- [x] Replace GitHub HTTP/header/stack dumps with short actionable messages; retain the active-work and rollback guards.
- [x] Move Usage above Archived chats and DAN settings in the bottom-left footer.
- [x] Eleven focused tests, production build/bundle checks, Electron compilation, signed package verification, and live footer ordering pass.
- [x] User explicitly approved installation. Verified no active/queued work and checked full process arguments; installed/launched `/Applications/DAN.app` with rollback copy, verified app-owned backend health, and confirmed the installed archive matches the prepared build.

- [x] Fix packaged-Electron discovery: use `original-fs` for physical ASAR stats/hashes; keep Electron-aware filesystem reads for the marker inside the app archive. Plain Node tests had missed Electron treating `.asar` as a directory.
- [x] Add real-Electron archive regression for changed/identical builds, including the module extracted at runtime from the installed app. Keep Install local update visible when unavailable, with a no-GitHub-required explanation.
- [x] Install corrected updater and PDF repairs under existing approval. Relaunch after interruption passes installed proxy health, exact Unicode PDF byte-range delivery, and decoder availability. Seven focused tests, real Electron regression, frontend build/budgets, compilation, and signed bundle verification pass.

- 2026-10-02 release installed at user request after feature commits/push and idle checks. Signature/archive equality and owned backend/proxy health pass; app PID 46879, backend PID 46899. Rollback retained at `/Applications/.DAN-previous-e50968db-b9e1-4fa8-9963-72f081840563.app`. Existing-session title transfers stay disabled.

## October 10 published update follow-up
- [x] Check published releases at startup and every 15 minutes; catch up on focus, skip overlapping/downloaded work and keep background failures quiet.
- [x] Show a dot on Settings and the download/install button while an update is available or ready.
- [x] Stage checksum-verified Mac ZIPs through the existing identity/architecture/signature/version checks, idle guard and rollback installer; no Developer ID/Keychain writes needed for this private distribution.
- [x] Package/sign the app before generating ZIP/DMG/checksum assets; remove local build-machine paths from published markers.
- Superseded by public releases through v0.1.5; authenticated private-feed acceptance is retired.
- [x] Bootstrap the installed older Mac build once using the existing local installer; installed 0.1.1 on October 11 after confirming the app/backend were stopped and its default data store idle.

Release scope remains the desktop shell for existing configured Python-backend installations. Standalone backend distribution is not bundled by this change.

- 2026-10-10 validation: 20 focused frontend/updater tests and nine Python tests pass. Isolated 0.1.1 package passes archive SHA-512/size/signature checks and a real disposable 0.1.0 → 0.1.1 installation with rollback and preserved data. Artifacts and source patch: `editor/release/published-0.1.1/`. GitHub HTTP 401 still prevents publication.

## October 11 Actions publication
- [x] Add tag-triggered Mac build/test/artifact workflow and separate publisher using GitHub's automatic token.
- [x] Validate exact tag source and checksums; allow resuming only matching drafts; protect published releases. Six publisher tests pass.
- [x] Verify Git SSH access independently of the invalid local GitHub API token.
- [x] Push isolated release source `11ab4e44` to main and tag `v0.1.1` through existing SSH access.
- [x] Subsequent public publication runs verified; see 0.1.2–0.1.5 release receipts below.
- Superseded: public updater requires no client GitHub login. Signed installed-client upgrade acceptance remains open.

- [x] Add manual dispatch for an existing tag after the initial push produced zero runs; propagate the resolved source SHA into publication.

- [x] Add automatic workflow-change push trigger on main; resolve the package-version tag and build that immutable source without operator dispatch.

- 2026-10-11 installed `/Applications/Dear Diane.app` 0.1.1 at user request. Signature and source/installed ASAR equality pass; backend 8000 and app proxy 45173 health return HTTP 200. Rollback: `/Applications/.Diane-previous-f1607e81-45de-4360-bd64-073783c1b8bc.app`. Private-feed authentication and remote publication remain unverified.

- [x] Confirm remote Mac build/tests/package succeeded; improve suppressed publication diagnostics and use canonical repository for automatic retry.
- [x] Confirm corrected publication succeeds for v0.1.2 (run 38133765309).

## October 11 anonymous updates
- [x] Remove GitHub sign-in/account controls, auth actions, and credential lookup from update checks.
- [x] Align client, packaged feed, and publisher with the user-supplied public `2Mars4096/dear-diane` repository; explicitly disable the private provider.
- [x] Verify anonymous repository visibility; latest-release endpoint returns 404, so no published installer was verified.
- [x] Replace auth prompts in errors with no-release/network/rate-limit feedback; retain manual download/install and rollback safeguards.
- [x] Public release assets/anonymous manifest verified in later release receipts.
- [ ] Verify a complete published-feed upgrade in the installed client; local reinstalls do not establish this.

## README review
- [x] Apply humanizer to rewrite README around setup, a first task, agent/model selection, reading, files, remote work, and settings.
- [x] Remove personal machine/account details and internal operational history; consolidate duplicate sections and correct outdated settings/update instructions. No secrets, private data, or local paths added; no remote writes.

- Anonymous-update validation: 16 updater/UI regression tests and seven publisher tests pass; frontend production build/budgets and Electron compilation pass. README code fences/setup-file references and personal/secret-pattern checks pass.

- [x] Correct release creation 403: use verified existing tag, no redundant target; test exact-source marked draft retries.

## Combined 0.1.2 release
- [x] Include pending EPUB, Settings, README and public-feed changes at user request.
- [x] Validate combined source: 224 reader/document/workbench/updater tests and nine publisher tests pass; production build/budgets and Electron compilation pass.
- [x] Publish v0.1.2 and verify release assets: run 38133765309 succeeded, draft=false, DMG/ZIP/blockmaps/manifest present.

- Combined release source `13aae0df`, tag `v0.1.2`, is pushed. Actions run `38132743457` passed build/tests/packaging but failed publication; release endpoint remains 404. Exact latest publisher error awaits operator-visible log; no further permission assumptions.

- [x] Fix post-creation draft lookup 404 and add a regression matching GitHub’s draft endpoint behavior.

## Single update action, October 11
- [x] Replace simultaneous Check/Download/Install buttons with one state-aware action, including disabled checking/downloading/preparing/restarting labels. Keep local build actions under Update options.
- [x] Verify explicit check → download → install transitions and exactly one primary button; six update UI tests and production build/budgets pass.

## Combined download/install action, October 11
- [x] Correct the interaction: Check for updates when none is known; Download and install once detected. One click downloads, verifies/stages, installs, and restarts through main-process IPC.
- [x] Check idle/ownership before downloading and again before installation. Download/staging failures never restart; newly active work retains the prepared update for retry.
- [x] Fourteen UI/IPC tests cover one-click success, download failure, initially active work, and work starting during download.

## 0.1.3 release
- [x] Validate combined updater and Sticky Todo changes, commit and reconcile main: 28 desktop/updater tests, ten publisher tests and 20 Python tests pass; production build/budgets and Electron compilation pass.
- [x] Push v0.1.3 (`41e5ad31`) and verify publication: run 38135780419 succeeded; non-draft release includes DMG/ZIP/blockmaps; anonymous manifest confirms 0.1.3.

- [x] Clean up 13 old rollback app bundles to Trash at user request; preserve installed app and staged update. Resolve the real macOS home through the user database because the agent HOME is redirected.

## October 11 download progress
- [x] Review Sticky Todo’s update controller/component; expose transferred/total bytes through Diane update status and display percentage plus MB, with indeterminate progress before size is known.
- [x] Verify progress IPC, live UI updates, unknown-size and staging transitions: 15 focused tests pass.

## 0.1.4 release
- [x] Validate download-progress improvements: 24 updater/UI tests, ten publisher tests, production build/budgets and Electron compilation pass.
- [x] Publish v0.1.4 (`0065ab2b`), run 38136805347; verify non-draft release, five assets and anonymous 0.1.4 update manifest.

- [x] Install local 0.1.4 progress build at user request; isolated build/budgets and Electron compile pass. Verified signature/archive equality and healthy app-owned backend after launch; rollback retained.

## 0.1.5 release
- [x] Validate isolated source: 73 reader/document tests, ten publisher tests, frontend/Electron compilation and bundle budgets pass.
- [x] Publish v0.1.5 (`501685ed`); Actions run 38138382110 succeeded, all five assets present and anonymous 0.1.5 manifest verified.

## October 11 update cleanup repair
- [x] Use original-fs for physical bundle operations and staged-app deletion; verify extracted ASAR cleanup in real Electron on success and validation rejection.
- [x] Ten focused updater tests, production build/budgets and Electron compilation pass.
- [x] Install fixed local 0.1.5 with rollback; signature/archive equality and both health endpoints pass after a startup retry.

## October 13 — startup investigation and prepared fix
- [x] Confirm installed app/proxy/backend are healthy while a real run is active; do not restart it.
- [x] Address the documented synchronous NSAlert quit blockage with `dialog.showMessageBox` and explicit Quit. Retain ownership of failed-start backend processes so ordinary shutdown reaps them.
- [x] Write a bounded 32 KiB latest-launch receipt at `userData/logs/startup.log` with launch/spawn/health/failure events and startup stderr; omit inherited environment values. Log failure must not prevent startup/quit.
- [x] Centralize `original-fs` selection for physical archive hashing/removal. Targeted lint and real Electron success/failure cleanup checks pass.
- [x] Build and ad-hoc sign the isolated arm64 snapshot at `/private/tmp/diane-priority-editor/release/mac-arm64/Dear Diane.app`; strict deep signature verification passes.
- [x] Install after idle check; later October 13 installation receipts below verify signature/archive/health.
- [ ] Establish repeatable LaunchServices relaunch reliability using startup receipts. Root cause of earlier backend exits/timeouts remains unproven; successful installs alone do not close this investigation.
- Validation: 23 focused startup/update/health tests and the separately permission-enabled shutdown regression pass. Production build/budgets and Electron compilation pass.

- [x] October 13: install the current local build at user request; signed bundle/archive equality, idle checks and launched backend/proxy health pass. Plan mode is now included in the installed app. Live model MCQ acceptance remains separate from the synthetic preview.

- [x] October 13 final planning follow-up installed after work became idle: batched questions, Other/comments on one row and answer boundaries. Signature/archive equality and backend/proxy health pass; rollback `.Diane-previous-7a29b1da-ed0b-4bfc-b6ca-5a773e9da7c1.app` retained.

- [x] Install concise choice labels and auto-select Other follow-up; seven focused tests, build/budgets, signature/archive equality and app health pass. Rollback retained at `/Applications/.Diane-previous-6c40a7e2-9cdb-4ebd-917c-a673be863601.app`.

## 0.1.6 release
- [x] Validate current installed-source snapshot: 506 frontend tests and 61 focused backend tests pass.
- [x] Prepare 0.1.6 notes and version metadata, including the matching-backend requirement for Plan mode.
- [x] Publish v0.1.6 from `ee2d1cbe`; workflow `38415664390` built and published all five Mac assets. Anonymous update manifest and non-draft release verified.


## October 14 — Settings category notice
- [x] Show the existing accent dot on About & updates for available/ready phases using the shared status subscription, even before visiting the panel.
- [x] Install updated frontend; signed bundle/archive equality and backend/proxy health pass.

- October 14 local installation: current 0.1.6 bundle installed and launched after idle checks; signature/archive equality and backend/proxy health verified. Visual installed acceptance remains separate.

- [x] October 14: install worker roster and Opus 5.5 follow-up; signature/archive verified and both health endpoints pass after startup retry. Preserve rollback and keep startup reliability investigation open.

- [x] Build and sign child-session/compact-header follow-up; production budgets pass.
- [x] Install when idle: October 14 follow-up includes child controls, roster inclusion and Claude HOME fix; signature/archive/backend/proxy verified.

- [x] October 14: install Team chronological/collapsed history, content-width status pill and worker streaming-detail cleanup. Idle checks, signature, archive equality and both health endpoints pass.

- [x] October 14: user-approved cleanup removed five superseded rollbacks, five stale staged releases and old 0.1.0 packages/build snapshots (5.20 GiB measured). Keep latest rollback `384b1236-2d31-4e48-8734-e30f11c1fa60` and published-0.1.1 source patch/records. Older backup paths in historical receipts are no longer retained.
