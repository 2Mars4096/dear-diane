# Desktop update operations

- Local flow: Settings → About & updates → Install local update. The compiled app records its build location; remembered selections and `DAN_LOCAL_UPDATE_PATH` are also supported. Changed app archives are detected even at the same version. Check for updates checks published releases; Update options retains a local file picker.
- Requires a packaged macOS client, matching bundle ID/architecture, a writable app parent directory, an app-owned backend, and no running/queued work. Ad-hoc signed local development bundles are supported.
- Preparation stages a verified copy beside the installed app, preserves the old icon, and re-signs the staged local copy. It never modifies the source build or user data.
- The detached installer waits up to two minutes for app/backend exit before touching the installed app. Replacement/open-command failures restore the prior app. Later startup crashes require manual rollback using the retained `.DAN-previous-*.app` next to the installation. Installation status/logs are in the profile's `updates/` directory.

## First installation from an older DAN

An agent running inside the existing app can start `editor/scripts/install-local-update.cjs` detached with six arguments: absolute source app, installed target app, active graphs directory, profile updates directory, app PID, and its owned backend PID. Compile Electron first. The helper validates/stages the build, waits up to 30 minutes for active/queued work to finish, then shows a native Install and restart/Later dialog. It rechecks work before quitting. No second agent app is needed. Verify the backend PID belongs to the target app before launch.

## Published releases

- The configured provider is public GitHub Releases at `2Mars4096/dear-diane`. Checks and downloads need no GitHub account, local `gh`, or token. The main process selects the anonymous provider explicitly; no account actions or login state cross updater IPC.
- The packaged `app-update.yml` and package publish metadata use the same public repository. Renderer IPC cannot choose another feed. Publishing still requires maintainer authorization, separate from end-user update checks.
- Configure a stable Apple Developer signing identity/notarization for production macOS delivery. The current `identity: null` development setting is not a production release setup.
- Bump `editor/package.json` version for every published release and build both ZIP and DMG plus `latest-mac.yml`. Publish the signed artifacts and generated metadata together through the selected provider; no release is published by the normal local build command.
- `build/github-update.yml` is copied as `app-update.yml` so directory-only local packages also contain the configured channel. Keep it aligned with package.json publish metadata. The client checks that channel, downloads and installs on an explicit Download and install click, then restarts automatically after idle/ownership checks. It does not accept arbitrary feed URLs from renderer IPC or embed access tokens in the frontend.
- The separately installed Python backend is **not** replaced by desktop app updates. Bundle/version it (or establish an explicit compatible backend deployment contract) before distributing standalone full-product updates.
- Verify actual update delivery on a test installation after a release is published. Anonymous repository metadata confirms public visibility, but the latest-release endpoint returned 404 on October 11. Current coverage includes event mocks and filesystem handoff tests, not a live public download.

References: [electron-builder auto-update](https://www.electron.build/v26/docs/features/auto-update/), [Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater).

## Packaged-runtime verification

- Electron mounts `.asar` as a virtual directory. Physical archive comparisons use `original-fs`; marker reads inside an archive keep Electron filesystem semantics.
- After `npm run electron:compile`, run `electron scripts/check-prepared-build.cjs`. An optional absolute module path tests `preparedBuild.js` inside the installed app. This exercises real ASAR archives; ordinary Node tests are insufficient.
