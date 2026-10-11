# Desktop releases

The public release feed is `2Mars4096/dear-diane`. End users do not need GitHub authentication to check or download updates. Version numbers in `editor/package.json` and its lockfile must increase for each published release.

## Publish through GitHub Actions

Push the reviewed release source and a matching `vX.Y.Z` tag. Updating the workflow on main also automatically builds the existing tag matching the package version; manual dispatch is only a fallback. `.github/workflows/desktop-release.yml` builds on an Apple Silicon `macos-15` runner, runs updater/publisher tests and build budgets, and uploads the signed ZIP/DMG/manifests. A separate job receives `contents: write` and publishes using GitHub's automatic job token. No local GitHub CLI login, provider keys, signing certificates or Keychain changes are required for publication.

- Keep `editor/package.json`, its lockfile and the tag version aligned; add `docs/releases/vX.Y.Z.md`.
- The publisher validates checksums, asset sizes and exact tag commit before creating a draft. It publishes only after verifying uploaded assets. A rerun can resume a draft from that same source; published releases are never overwritten.
- Git SSH push authentication is independent of `gh` API authentication. Workflow credentials authorize publication only; the app reads releases anonymously.
- If the first tag push produces no run, open **Desktop release → Run workflow**, use branch `main`, and enter the existing release tag (for this release, `v0.1.1`). The workflow builds that tag and passes its resolved source commit to publication.
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
- On macOS, electron-updater verifies download checksums; the app verifies bundle identity, architecture, code-signature integrity and release version before staging through the existing detached installer. The configured GitHub repository controls release provenance; ad-hoc signing is **not** Developer ID notarization.
- The installer waits for the owned app/backend to exit, retains a rollback bundle, and preserves chats, settings and icons. Active or queued work blocks installation.
- Older Mac clients using the Squirrel install path require one local bootstrap to 0.1.1. Afterwards this Mac channel does not require a Developer ID identity.
- Releases currently update the desktop shell for machines with an existing configured Python backend. They do not install/update Python or its dependencies. Standalone backend packaging is a separate prerequisite for distribution to new machines.

## Current release

Release source `11ab4e44` and tag `v0.1.1` were pushed to main on 2026-10-11 using existing SSH authentication. GitHub reports the repository's canonical name is now `2Mars4096/dear-diane`; the original remote redirects there. [View Actions](https://github.com/2Mars4096/dear-diane/actions/workflows/desktop-release.yml) to confirm completion. The local API token is invalid, so remote publication has not yet been verified.

The release uses an isolated snapshot excluding concurrent reader work in the original workspace. Twenty updater tests, six publisher tests, production build/budgets and Electron compilation pass locally. Prepared local 0.1.1 assets previously passed checksum/signature and disposable installation/rollback checks. Installed and relaunched the prepared local 0.1.1 build on October 11 at user request; signature, archive equality and app/backend health pass. The one-time bootstrap is complete. Remote publication and valid private-feed client authentication remain before in-app update acceptance.

## Public-feed transition, October 11

The user supplied `https://github.com/2Mars4096/dear-diane`. An anonymous repository request confirmed `private: false`; the latest-release request returned 404. Source now uses the public feed with no GitHub login or credential lookup. This change has not been published or installed, and an end-to-end public release download remains unverified. Repository visibility was not changed.

## Published 0.1.2

[Version 0.1.2](https://github.com/2Mars4096/dear-diane/releases/tag/v0.1.2) is published, confirmed non-draft with installer and updater assets. [Run 38133765309](https://github.com/2Mars4096/dear-diane/actions/runs/38133765309) succeeded after draft lookup was corrected. This release includes EPUB, redesigned Settings, anonymous public updates and refreshed documentation. Local installed 0.1.1 was not replaced during publication.
