# 6-3: Open projects on a remote host

**Parent:** [6-remote-control](6-remote-control.md)
**Status:** completed
**Goal:** Open existing folders on mini as DAN projects without a Mac folder picker or copying files.

## Tasks
- [x] Add an inline remote folder chooser to project setup, using the authenticated host's existing root suggestions API.
- [x] Identify the execution machine and retain local desktop folder/drop behavior.
- [x] Verify folder selection, failure/retry, and remote project persistence; build and update mini's deployment.

## Decisions
- Keep remote work in its existing authenticated browser origin. Desktop host switching in one window is separate work.
- Opening a folder registers its remote path; it does not upload, clone, or relocate files.
- Reuse the deployed backend API. Mini's older release predates other current frontend features, so deploy a matching frontend/backend together after checking for active/queued work; retain previous releases and durable state.

## Verification, 2026-09-28
- Four focused project-dialog tests and production build/bundle budgets pass.
- Mini deployment completed through the existing installer with provider provisioning disabled. No active/queued records before updating; execution/relay active and boot-persistent afterward.
- Live authenticated desktop and 390px browser chooser checks pass. Temporary project registration persisted on mini and was removed from DAN afterward; no project files modified.
- At the user's request, opened `/home/adam/Dropbox/Projects/fig-to-markdown` as `fig-to-markdown`. Server registry and selected project after reload verified.
- Screenshots: `output/playwright/mini-project-folders-{desktop,phone}.png` and `mini-dropbox-project.png`.
