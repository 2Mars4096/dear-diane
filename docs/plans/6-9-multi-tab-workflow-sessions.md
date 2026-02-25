# 6-9: Multi-Tab Workflow Sessions

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Let users open multiple workflow tabs in the visual editor, keep each workflow's editing/execution state isolated per tab, and allow server-side runs to continue while the user switches tabs or refreshes.

## Context

Current editor state is single-graph (`graphId`, `nodes`, `edges`, `runId`, `logs`, etc.). Selecting another graph replaces the active state, so users cannot keep multiple workflows open concurrently. We need tabbed workflow sessions where:

1. each tab has isolated graph + run state,
2. only the active tab keeps a live WebSocket connection,
3. background runs continue on the backend and catch up when the tab is reactivated.

## Tasks

- [x] 1. Define tab state model in `useGraphStore.ts`
  - [x] 1-1. Add `tabs`, `activeTabId`, and `tabCache` state fields.
  - [x] 1-2. Define `TabInfo` and `TabSnapshot` types; include all per-workflow editor/execution slices (`graphId`, `danGraph`, `dirty`, `nodes`, `edges`, selection, `layerStack`, validation, `inputNodeValues`, history, run/log slices, `nodeIterations`, `streamingOutputs`, `pendingHumanInput`).
  - [x] 1-3. Keep truly global slices unchanged (`graphList`, toasts, clipboard, palette UI toggles).

- [x] 2. Implement tab lifecycle actions
  - [x] 2-1. `openTab(graphId)`: deduplicate by `graphId` (switch to existing tab if already open).
  - [x] 2-2. `switchTab(tabId)`: snapshot active tab, close active WS, restore target tab snapshot (or load from graph API on first open), reconnect WS if run is active (`pending`/`running`).
  - [x] 2-3. `closeTab(tabId)`: block closing if it is the only remaining tab; prompt save if dirty.
  - [x] 2-4. Ensure tab close cleans `tabCache` and per-tab persisted run metadata.

- [x] 3. Integrate run lifecycle with tabs
  - [x] 3-1. Keep existing run actions (`startRun`, `resumeRun`, `disconnectRun`, `handleRunEvent`) scoped to the active tab snapshot.
  - [x] 3-2. On tab switch, close old WS and reconnect for the new active tab only.
  - [x] 3-3. Preserve run identity/status for inactive tabs via cached snapshot + persisted tab metadata.
  - [x] 3-4. Add event safety guard in `handleRunEvent`: ignore WS events whose `run_id` does not match the active tab's `runId` (prevents cross-tab log/status contamination during fast tab switches).

- [x] 4. Persist and recover tabs across refresh
  - [x] 4-1. Replace single-run persistence with a tab-aware key (e.g., `dan_open_tabs`) in `sessionStorage`.
  - [x] 4-2. Persist `{ tabs, activeTabId, perTabRunRefs }` after open/close/switch/start/resume.
  - [x] 4-3. Add `restoreTabs()` startup action: rebuild tab list, restore active tab state, reconnect active run via existing `_catchup`.
  - [x] 4-4. Add migration fallback: if old `dan_active_run` exists, convert once to the new tab-aware schema and clear legacy key.
  - [x] 4-5. Persist only tab metadata + run refs (not full `nodes/edges/logs`) to keep `sessionStorage` lightweight and avoid quota risk.

- [x] 5. Build tab UI in toolbar
  - [x] 5-1. Create `editor/src/components/TabBar.tsx` (workflow tabs with active styling, close button, run-state badge).
  - [x] 5-2. Replace graph `<select>` in `EditorToolbar.tsx` with tab bar + graph picker for opening new tabs.
  - [x] 5-3. Keep current create/delete graph controls, but route graph-open behavior through tab actions.
  - [x] 5-4. Ensure deleting a graph closes any matching open tab and switches safely.
  - [x] 5-5. Ensure `createGraph` opens the new graph in a tab (instead of replacing active state without tab bookkeeping).

- [ ] 6. Validation and tests (deferred — manual testing recommended)
  - [ ] 6-1. Store tests: open/switch/close tab behavior, dedup, dirty close guard, single-remaining-tab guard.
  - [ ] 6-2. Store tests: WS lifecycle on tab switch (old closed, target reconnected).
  - [ ] 6-3. Recovery tests: restore tabs + active run from `sessionStorage`, including legacy-key migration.
  - [ ] 6-4. Manual scenario: run workflow A, open workflow B and run it, switch back/forth, refresh on each tab, verify status catch-up and logs.
  - [ ] 6-5. Regression test: when switching tabs during active runs, events from run A never append to tab B logs/status.

- [x] 7. Docs sync
  - [x] 7-1. Update `docs/todo.md` with 6-9 sub-plan entry and parent phase status.
  - [x] 7-2. Update parent plan `6-phase-3.75-visual-editor-editing.md` sub-plan table.
  - [x] 7-3. Append `docs/changelog.md` with plan creation/review entry.

## Decisions

- Background tabs do **not** keep live WS streaming; backend processing continues, and UI reconnects + catches up when tab becomes active.
- Only one WS connection is maintained at a time (active tab), reducing client complexity and connection churn.
- One graph is opened at most once (`graphId` dedup across tabs) to avoid conflicting unsaved states.
- Last remaining tab cannot be closed (v1) to avoid null-workspace edge cases during this iteration.
- Use `sessionStorage` (tab-scoped in browser) instead of `localStorage` for per-tab run/session persistence.

## Notes

- This plan intentionally avoids backend changes; `RunManager` already supports reconnect catch-up.
- "Live status badges for inactive tabs" is out of scope for v1 because inactive tabs are disconnected by design.
- The virtual-tab approach minimizes blast radius: most existing components continue reading active store fields without refactoring.
