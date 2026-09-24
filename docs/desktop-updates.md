# Desktop update operations

- Local flow: Settings → Updates → Install local update. The compiled app records its build location; remembered selections and `DAN_LOCAL_UPDATE_PATH` are also supported. Changed app archives are detected even at the same version. Check for updates prefers a local build; Other update options retains a file picker.
- Requires a packaged macOS client, matching bundle ID/architecture, a writable app parent directory, an app-owned backend, and no running/queued work. Ad-hoc signed local development bundles are supported.
- Preparation stages a verified copy beside the installed app, preserves the old icon, and re-signs the staged local copy. It never modifies the source build or user data.
- The detached installer waits up to two minutes for app/backend exit before touching the installed app. Replacement/open-command failures restore the prior app. Later startup crashes require manual rollback using the retained `.DAN-previous-*.app` next to the installation. Installation status/logs are in the profile's `updates/` directory.

## First installation from an older DAN

An agent running inside the existing app can start `editor/scripts/install-local-update.cjs` detached with six arguments: absolute source app, installed target app, active graphs directory, profile updates directory, app PID, and its owned backend PID. Compile Electron first. The helper validates/stages the build, waits up to 30 minutes for active/queued work to finish, then shows a native Install and restart/Later dialog. It rechecks work before quitting. No second agent app is needed. Verify the backend PID belongs to the target app before launch.

## Published releases

- The configured provider is private GitHub Releases at `2Mars4096/deep-agent-network`. Settings → Updates → Sign in to GitHub opens device authorization in the browser; enter the displayed code. Switch account, refresh connection, and cancel sign-in are supported.
- Current personal-build authentication uses installed `gh` as the browser/credential helper; no terminal interaction is required in DAN. A future CLI-free client would need its own registered OAuth application. Git SSH access alone does not authenticate the releases API.
- Credentials remain in the local GitHub login store and Electron memory. Inherited automation tokens are ignored by this login flow. Token output is never returned through IPC or included in artifacts; updater logs are disabled and displayed updater errors redact the active token.
- Configure a stable Apple Developer signing identity/notarization for production macOS delivery. The current `identity: null` development setting is not a production release setup.
- Bump `editor/package.json` version for every published release and build both ZIP and DMG plus `latest-mac.yml`. Publish the signed artifacts and generated metadata together through the selected provider; no release is published by the normal local build command.
- `build/github-update.yml` is copied as `app-update.yml` so directory-only local packages also contain the configured channel. Keep it aligned with package.json publish metadata. The client checks that channel, downloads only on request, and installs only on explicit Install and restart. It does not accept arbitrary feed URLs from renderer IPC or embed access tokens in the frontend.
- The separately installed Python backend is **not** replaced by desktop app updates. Bundle/version it (or establish an explicit compatible backend deployment contract) before distributing standalone full-product updates.
- Verify actual signed update delivery on a test installation before enabling a public channel. Current coverage uses updater event mocks and filesystem handoff tests, not a live remote release.

References: [electron-builder auto-update](https://www.electron.build/v26/docs/features/auto-update/), [Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater).

## Packaged-runtime verification

- Electron mounts `.asar` as a virtual directory. Physical archive comparisons use `original-fs`; marker reads inside an archive keep Electron filesystem semantics.
- After `npm run electron:compile`, run `electron scripts/check-prepared-build.cjs`. An optional absolute module path tests `preparedBuild.js` inside the installed app. This exercises real ASAR archives; ordinary Node tests are insufficient.
