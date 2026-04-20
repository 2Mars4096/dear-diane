# 3-3: Hugo Preview Bridge

**Parent:** [3-content-mode-and-live-preview](3-content-mode-and-live-preview.md)
**Status:** in-progress
**Goal:** Provide a managed Hugo preview lifecycle for Content mode so edits refresh a real rendered page inside DAN rather than a Markdown approximation.

## Context

- The user wants live preview for a Hugo-based knowledge base, and the existing site behavior depends on Hugo templates plus client-side scripts for math, search, citations, and graph rendering.
- DAN's Electron desktop already has the right trust boundary for local-process management and native file access.
- The current browser fallback story elsewhere in the shell is intentionally honest; Content mode should follow the same rule rather than pretending managed local preview exists in a plain browser tab.
- The trickiest product seam is mapping a selected content file to the correct preview URL, especially when a site uses Hugo defaults plus occasional slug/url overrides.

## No-Change Zones

- Keep Hugo as the preview renderer; do not add a parallel DAN preview pipeline.
- Keep the preview bridge desktop-first and capability-gated.
- Keep the preview target constrained to local managed URLs instead of opening arbitrary external sites in the embedded preview by default.

## Tasks

- [x] 1. Define the preview-session lifecycle
  - [x] 1-1. Detect whether the active content project is a Hugo project and whether `hugo` is available locally
  - [x] 1-2. Start, stop, and restart a managed local Hugo dev server from the desktop runtime
  - [x] 1-3. Surface clear status for missing Hugo, start failures, rebuild failures, and stale preview state
- [ ] 2. Resolve page-to-preview mapping
  - [x] 2-1. Map standard content bundle paths to their expected preview routes
  - [x] 2-2. Handle common slug/url overrides without forcing the user to guess the preview URL manually
  - [ ] 2-3. Persist the current preview target per page/workspace so reloads feel stable
- [ ] 3. Build live refresh behavior
  - [x] 3-1. Refresh the preview after successful saves and Hugo rebuild completion
  - [x] 3-2. Avoid jarring reload loops during rapid typing by coordinating with the autosave cadence
  - [ ] 3-3. Preserve useful preview context when possible (scroll/anchor or simple reload heuristics)
- [x] 4. Keep browser fallback and security explicit
  - [x] 4-1. Show a truthful unsupported/degraded state in browser preview when managed Hugo lifecycle is unavailable
  - [x] 4-2. Restrict the embedded preview to the managed local preview origin by default
  - [x] 4-3. Expose manual retry/open-in-browser affordances without turning the preview pane into a generic web browser
- [ ] 5. Verify preview robustness
  - [x] 5-1. Add focused tests for preview lifecycle state and route resolution helpers
  - [ ] 5-2. Manually verify start/restart/rebuild behavior on a real Hugo workspace once the bridge lands

## User-Facing Acceptance

- The preview pane shows the real rendered page for the active content file.
- Starting Content mode on a valid Hugo workspace either attaches to or launches a usable local preview without extra terminal work.
- Missing-Hugo or failed-preview states are obvious and recoverable instead of silent.

## Decisions

- The preview contract is "real Hugo page in an Electron-hosted preview surface", not "render markdown locally and hope it matches".
- Desktop manages the Hugo lifecycle in Phase 1; browser preview gets an honest degraded path.
- Route resolution should start with the common Hugo leaf-bundle cases, then add explicit fallback handling for custom URL overrides where needed.
- The managed preview origin stays locked to DAN-started local `127.0.0.1:<port>` URLs even when frontmatter route overrides are present; route overrides can shape the path, but not the embedded origin.

## Notes

- A dedicated preview manifest or page-path resolver may become necessary if the site's permalink logic turns out to be more complex than standard bundle-path mapping.
- This plan is about local authoring fidelity, not deployment/publishing. Push/deploy actions are follow-up work.
- 2026-04-19: `editor/electron/hugoPreviewManager.ts` now manages per-project `hugo server` sessions behind `contentPreview:*` IPC, `ContentMode.tsx` now auto-starts preview for Hugo roots, embeds the real rendered page in an Electron preview surface (`webview` for desktop on-page editing, plain iframe for fallback paths), supports start/restart/stop/open-in-browser actions, and refreshes the preview after successful saves or autosave-driven disk syncs.
- 2026-04-19: direct smoke against `/Volumes/data/Dropbox/Projects/my-knowledge-base` exposed one real seam: the earlier `20s` readiness budget was too short for the repo's cold Hugo build (`~28.7s`). The preview manager now probes with `HEAD`, resolves on the first HTTP response, and defaults to a `60s` readiness window with `DAN_CONTENT_PREVIEW_READY_TIMEOUT_MS` as an escape hatch. Remaining gaps are route persistence/manual overrides, preview-context preservation, and full in-app restart/rebuild verification.
- 2026-04-20: packaged-app Hugo discovery no longer depends only on the app launch `PATH`. The preview manager resolves `hugo` from `PATH`, `DAN_HUGO_BIN`, and common macOS Homebrew/MacPorts locations before spawning the managed server.
- 2026-04-20: embedded preview fidelity now includes a DAN-only reader-layout layer in the guest preload. This does not replace Hugo rendering; it only adapts the already-rendered page for DAN's split-pane viewport by collapsing the knowledge-base article template into one column and hiding full-browser side chrome.
- 2026-04-20: the preview bridge now also carries an explicit page-owned embedded-mode signal. `buildManagedPreviewUrl(..., { embedded: true })` appends `dan_preview=1` for the in-app guest only, and the target knowledge-base theme now uses that flag to skip its heavy page-mutation script and switch into a deterministic embedded reader layout instead of forcing DAN to out-style the whole page from outside.
