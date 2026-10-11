# 4-2: In-app desktop updates

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** in-progress
**Goal:** Install prepared local builds and receive configured published desktop releases from DAN settings without requiring a second agent application.

## Tasks
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
- [ ] Publish release 0.1.1 and verify authenticated in-app discovery/download after GitHub access is restored (current API responses are HTTP 401).
- [x] Bootstrap the installed older Mac build once using the existing local installer; installed 0.1.1 on October 11 after confirming the app/backend were stopped and its default data store idle.

Release scope remains the desktop shell for existing configured Python-backend installations. Standalone backend distribution is not bundled by this change.

- 2026-10-10 validation: 20 focused frontend/updater tests and nine Python tests pass. Isolated 0.1.1 package passes archive SHA-512/size/signature checks and a real disposable 0.1.0 → 0.1.1 installation with rollback and preserved data. Artifacts and source patch: `editor/release/published-0.1.1/`. GitHub HTTP 401 still prevents publication.

## October 11 Actions publication
- [x] Add tag-triggered Mac build/test/artifact workflow and separate publisher using GitHub's automatic token.
- [x] Validate exact tag source and checksums; allow resuming only matching drafts; protect published releases. Six publisher tests pass.
- [x] Verify Git SSH access independently of the invalid local GitHub API token.
- [x] Push isolated release source `11ab4e44` to main and tag `v0.1.1` through existing SSH access.
- [ ] Verify Actions completion (local API token invalid; operator can view the private run).
- [ ] Verify installed private-feed authentication separately; workflow authentication does not repair client login.

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
- [ ] Verify discovery and download against a published public release, then installed-client acceptance. No publication or installation authorized by this change.

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
