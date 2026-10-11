# Desktop releases

The private release feed is `2Mars4096/deep-agent-network`. Version numbers in `editor/package.json` and its lockfile must increase for each published release.

## Publish through GitHub Actions

Push the reviewed release source and a matching `vX.Y.Z` tag. `.github/workflows/desktop-release.yml` builds on an Apple Silicon `macos-15` runner, runs updater/publisher tests and build budgets, and uploads the signed ZIP/DMG/manifests. A separate job receives `contents: write` and publishes using GitHub's automatic job token. No local GitHub CLI login, provider keys, signing certificates or Keychain changes are required for publication.

- Keep `editor/package.json`, its lockfile and the tag version aligned; add `docs/releases/vX.Y.Z.md`.
- The publisher validates checksums, asset sizes and exact tag commit before creating a draft. It publishes only after verifying uploaded assets. A rerun can resume a draft from that same source; published releases are never overwritten.
- Git SSH push authentication is independent of `gh` API authentication. This workflow does not repair the installed app's private-feed login; downloading private releases still requires valid GitHub access.
- View runs at https://github.com/2Mars4096/deep-agent-network/actions/workflows/desktop-release.yml . GitHub Actions must be enabled with available runner quota.
- Runner labels: https://docs.github.com/en/actions/reference/runners/github-hosted-runners .

## Build and publish on Apple Silicon

1. Finish tests and commit the intended release source. Preserve unrelated work in its own branch or checkout. Push the release commit to the feed repository.
2. From `editor/`, run `npm run dist:mac`. The script checks frontend budgets, compiles Electron, packages the app, ad-hoc signs it **before** creating ZIP/DMG assets, and omits the local build-machine path. It does not use or modify Keychain.
3. Write release notes describing the actual release scope. Then run:
   ```sh
   node scripts/publish-mac-release.cjs release /absolute/path/release-notes.md FULL_SOURCE_COMMIT_SHA
   ```
   The script verifies asset names, sizes and SHA-512 values, checks GitHub access and the exact source commit, uploads a draft, verifies uploaded names/sizes, then publishes. It never signs in or edits credential storage. Authentication or validation failures leave any draft unpublished. An existing tag is not overwritten automatically.
4. Verify the published release through a separate installed test copy: discovery, download, idle install, relaunch and rollback data. Keep the production app idle before an intentional update.

## In-app behavior

- Check after startup, every 15 minutes, and on focus when overdue. Background failures remain quiet. Available/downloaded updates remain visible and do not trigger automatic downloads or restarts.
- Settings and its download/install action show dots for available/ready updates.
- On macOS, electron-updater verifies download checksums; the app verifies bundle identity, architecture, code-signature integrity and release version before staging through the existing detached installer. The private GitHub channel controls release provenance; ad-hoc signing is **not** Developer ID notarization.
- The installer waits for the owned app/backend to exit, retains a rollback bundle, and preserves chats, settings and icons. Active or queued work blocks installation.
- Older Mac clients using the Squirrel install path require one local bootstrap to 0.1.1. Afterwards this private Mac channel does not require a Developer ID identity.
- Releases currently update the desktop shell for machines with an existing configured Python backend. They do not install/update Python or its dependencies. Standalone backend packaging is a separate prerequisite for distribution to new machines.

## Current release

0.1.1 is prepared locally. Local GitHub API authentication returns HTTP 401, but Git SSH access works. Publication now uses the Actions workflow; remote completion must be verified in Actions. The local release was built from an isolated source snapshot containing the update/credential-discovery changes, excluding concurrent reader changes in the main workspace. A corresponding source commit must be pushed before publication.
