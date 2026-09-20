# 4-2: In-app desktop updates

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Install prepared local builds and receive configured published desktop releases from DAN settings without requiring a second agent application.

## Tasks
- [x] Add Settings update status, local build selection, release checks/downloads, and explicit install/restart.
- [x] Validate and stage local macOS bundles; use a detached installer that waits for app/backend exit, preserves user data, and rolls back replacement failures.
- [x] Refuse installation during active runs/queued work or while using an externally owned backend.
- [x] Wire the standard release updater with explicit download/install and honest unconfigured-channel state.
- [x] Test handoff failures, active-work checks, UI states, preload contracts, and production packaging.
- [x] Document release signing/feed requirements and the one-time bootstrap for older installed builds.

## Decisions
- Local development builds use an explicitly selected, signature-verified DAN.app; do not run Git pull or dependency installers in the app.
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
