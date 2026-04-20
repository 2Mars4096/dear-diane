# 3-4: Save, Autosave, and Validation

**Parent:** [3-content-mode-and-live-preview](3-content-mode-and-live-preview.md)
**Status:** in-progress
**Goal:** Make Content mode safe enough for real writing by combining debounced autosave, atomic writes, validation, draft recovery, and clear error states.

## Context

- Live preview only works well if the save loop is trustworthy; otherwise the preview becomes a source of confusion rather than confidence.
- The target content workflow includes YAML frontmatter, Hugo conventions, citations, and page-level authoring patterns that can fail in subtle ways even when the file still "looks" like Markdown.
- DAN already has file watching and write confirmation concepts in the desktop shell, but Content mode needs a much quieter, faster, writer-oriented version of that contract.
- The product risk is not just failed writes. Silent draft loss, unnoticed invalid frontmatter, and stale preview state would all break trust quickly.

## No-Change Zones

- Never silently discard user edits.
- Never treat a failed save as if preview is current.
- Keep validation layered: generic Markdown/YAML checks first, project-specific knowledge-base rules second.

## Tasks

- [x] 1. Define the save pipeline
  - [x] 1-1. Add explicit dirty, queued, saving, saved, and failed states to the page editor lifecycle
  - [x] 1-2. Support both debounced autosave and explicit manual save (`Cmd+S`)
  - [x] 1-3. Use atomic write semantics so partial saves do not corrupt the page file
- [x] 2. Add draft resilience and conflict handling
  - [x] 2-1. Persist unsaved drafts locally so app refreshes/restarts do not lose work
  - [x] 2-2. Detect external file changes and offer reload/compare/reapply choices
  - [x] 2-3. Keep the last-known-good disk version available for recovery/diffing
- [ ] 3. Add validation layers
  - [x] 3-1. Generic validation: YAML parse errors, malformed frontmatter boundaries, and missing required save preconditions
  - [x] 3-2. Hugo/content validation: expected bundle/page-path rules and page-file assumptions
  - [ ] 3-3. Knowledge-base profile validation: duplicate or missing `pageID`, unresolved citations, and other project-specific authoring checks when that profile is active
- [x] 4. Surface trustworthy feedback
  - [x] 4-1. Add a quiet save-status strip plus a richer diagnostics drawer when something is wrong
  - [x] 4-2. Mark preview state as stale whenever the current draft is not successfully saved/rendered
  - [x] 4-3. Keep validation and save errors actionable rather than generic toast spam
- [ ] 5. Verify the safety contract
  - [ ] 5-1. Add focused tests for autosave timing, failed saves, draft restore, and conflict handling
  - [ ] 5-2. Manually verify recovery flows on a real content workspace before calling Phase 1 usable

## User-Facing Acceptance

- Typing feels live, but the system never lies about whether the preview reflects the saved file.
- Bad frontmatter or failed writes do not destroy the current draft.
- When another tool edits the same page, the user gets a clear choice instead of a silent overwrite.

## Decisions

- Autosave is the default Phase 1 behavior, but explicit save remains available and visible.
- Validation should block unsafe saves when necessary, but should otherwise prefer warning-with-context over heavy modal interruptions.
- Project-specific authoring checks should be pluggable so Content mode is not hardcoded forever to one knowledge-base repo's rules.

## Notes

- If the save loop feels unreliable, the whole mode will feel unreliable, even if the editor and preview look good.
- The first validation pass should bias toward a small set of high-signal diagnostics rather than an exhaustive linter.
- 2026-04-19: the main safety loop is now landed. `ContentMode.tsx` runs debounced autosave plus explicit Save/`Cmd+S`, tracks queued/saving/saved/error/conflict states, blocks unsafe saves on validation errors, watches for external file changes, and offers reload-vs-keep-draft recovery with last-known-disk snapshots in the diagnostics rail.
- 2026-04-19: `editor/electron/main.ts` now writes page saves through an atomic temp-file rename and watches parent directories instead of individual files, so Content-mode watchers survive those atomic writes instead of silently detaching.
- 2026-04-19: remaining open work is the deeper knowledge-base profile layer (`pageID`-plus citation/reference checks) and real manual smoke/recovery verification against the target Hugo workspace.
