# 20-2: Chat & Editor UX Polish

**Parent:** [20-patch-polish](20-patch-polish.md)
**Status:** completed
**Goal:** Land five deferred UX features that make the chat and editor feel production-ready: error-to-debug shortcut, collapsible run output, fuzzy mention search, markdown export, and sortable log columns.

## Tasks

### "Fix This" Shortcut (from 12-2 task 6-5)

- [x] 1. One-click from run error → Debug mode with error pre-filled
  - [x] 1-1. `LogPanel.tsx` / error event rendering: add "Fix this" button (wrench icon or similar) on error-type events in the log panel
  - [x] 1-2. `DanNode.tsx`: add "Fix this" action to error state nodes (right-click context menu or inline button on error badge)
  - [x] 1-3. Add store/UI action `openDebugWithError(errorContext: string)`: switches chat mode to `"debug"`, opens chat panel if collapsed, and pre-fills the chat input with a structured error summary (node name, error type, truncated message)
  - [x] 1-4. Pre-fill format: `Fix error in [NodeName]: [error_type] — [first_line_of_message]` — user can edit before sending
  - [x] 1-5. Preserve current thread context when opening Debug (do not clear existing messages/thread state)

### Collapse Verbose Run Output (from 12-1 task 4-4)

- [x] 2. Default-collapsed run events in chat, expand on click
  - [x] 2-1. `RunOutputBlock.tsx`: default to collapsed (`expanded=false`) for run event sequences; show compact summary line (e.g., "Run started — 5 nodes completed — 2.3s")
  - [x] 2-2. Summary line: extract key metrics from the run event batch — node count, elapsed time, success/failure status, total tokens
  - [x] 2-3. Expand on click: show full event list (existing `RunEventBlock` rendering)
  - [x] 2-4. Auto-expand on error: if any event in the batch is an error, expand automatically so the user sees the failure
  - [x] 2-5. Persist collapse state per message (not globally) — re-collapsing is possible

### Fuzzy Search in Mention Autocomplete (from 12-3 task 8-3)

- [x] 3. Add fuzzy matching to `@` mention autocomplete
  - [x] 3-1. Add lightweight fuzzy scoring: subsequence match with gap penalty (no new dependency — implement inline or use a ~50-line utility). Match against item `label` and `identifier`.
  - [x] 3-2. `MentionAutocomplete.tsx`: replace exact prefix filter with fuzzy scorer. Sort results by score (best match first). Highlight matched characters in the dropdown.
  - [x] 3-3. Threshold: suppress results below a minimum score to avoid noise (tune empirically — start with 0.3)
  - [x] 3-4. Performance: debounce the filter by 50ms to avoid jank on fast typing (mention list is typically < 200 items, so scoring is fast)

### Frontend Export Buttons (from 8-4 tasks 2-3, 2-4)

- [x] 4. "Export as Markdown" toolbar button + preview modal
  - [x] 4-1. `EditorToolbar.tsx`: add "Export" dropdown button with two options: "Export as Markdown" and "Export as Python" (`GET /api/graphs/{id}/export/markdown`, `GET /api/graphs/{id}/export/python`)
  - [x] 4-2. Markdown path: endpoint returns `{ files: [{path, content}], diagnostics }`; open preview modal with file list + diagnostics panel
  - [x] 4-3. Python path: endpoint returns `{ code }`; show single-file preview (`workflow.py`) using same modal shell
  - [x] 4-4. Modal layout: left sidebar listing filenames, right pane showing selected file content. "Download All" for markdown uses client-side zip from `files[]`; Python uses single-file download.
  - [x] 4-5. Error handling: show toast only on endpoint failure; decompiler diagnostics render in-modal as warnings (not hard failure)
  - [x] 4-6. Optional: "Copy to Clipboard" per-file button in the preview modal

### Sortable Log Columns (from 7-4 task 5-2)

- [x] 5. Click column headers to sort log entries
  - [x] 5-1. `LogPanel.tsx`: add a table-header row above node groups with columns: Node Name, Duration, Tokens, Cost
  - [x] 5-2. Click header → toggle sort direction (ascending/descending). Default: execution order (unsorted).
  - [x] 5-3. Sort operates on the node-group level (not individual events within a group) — reorder the groups array
  - [x] 5-4. Visual indicator: arrow icon on active sort column (▲/▼)
  - [x] 5-5. Persist sort preference in component state (not across sessions — reset on new run)

### Validation

- [ ] 6. UX verification coverage — skipped (no frontend test framework yet); TypeScript compilation verified clean
  - [ ] 6-1. Frontend tests: "Fix this" opens Debug mode and preserves active thread/messages
  - [ ] 6-2. Frontend tests: run output starts collapsed, auto-expands on error, and remembers per-message expand state
  - [ ] 6-3. Frontend tests: fuzzy mention ranking produces best-match ordering and avoids noisy low-score matches
  - [ ] 6-4. Frontend tests: export modal handles markdown `{files,diagnostics}` and python `{code}` payloads correctly
  - [ ] 6-5. Frontend tests: sortable log columns toggle direction and preserve unsorted default on new run

## Decisions

- Task 1: `openDebugWithError` increments `chatFocusTrigger` to open panel, but when `chatPrefill` is set, the existing thread/messages are preserved (only cleared when no prefill).
- Task 2: Used React state initializer with `hasError` derived from `deriveNodeStates` to auto-expand on error. Per-message collapse state is naturally handled since each `RunOutputBlock` has its own state.
- Task 3: Implemented fuzzy scoring inline (~40 lines). Exact substring matches score highest (near 1.0), subsequence matches score by match ratio minus gap penalty. Threshold 0.3 filters noise. No debounce added since scoring is synchronous and the item list is typically <200.
- Task 4: No JSZip dependency — "Download All" triggers individual file downloads. Modal follows existing `GraphDiffPreview` pattern (fixed overlay, white card, max-h-[80vh]).
- Task 5: Sort cycle is null→asc→desc→null (back to execution order). Uses `nodeTimings`, `nodeUsage`, `nodeCosts` from store for duration/tokens/cost columns.

## Notes

- Tasks 1–5 are implementation tracks with no hard ordering dependencies; Task 6 is validation and should follow implementation.
- Task 1 ("Fix this") and Task 2 (collapse run output) both affect chat UX but in different components (`ChatPanel.tsx` vs `RunOutputBlock.tsx`), so they can still be parallelized.
- Task 3 (fuzzy search) should avoid adding a dependency like `fuse.js` — a simple subsequence scorer is sufficient for the mention list size.
- Task 4 (export) is primarily frontend wiring — the backend endpoints (`/export/markdown`, `/export/python`) are fully functional.
- Task 5 (sortable columns) is a standard table-sort pattern. The main design question is whether to show a flat table or keep the grouped view with sortable groups. Recommendation: keep grouped view, sort the groups.
