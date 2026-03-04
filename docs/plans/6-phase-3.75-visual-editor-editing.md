# 6: Phase 3.75 — Visual Editor Full Editing

**Status:** completed
**Goal:** Make the visual editor a complete authoring surface with undo/redo, copy/paste, port editing, context menus, inline rename, validation feedback, import/export, command palette, InputNode support, execution-time UX (loop visualization, streaming output, human input), and workflow-as-node reuse — so users can build and run workflows entirely from the UI without touching JSON or Python.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [6-1](6-1-history-multiselect.md) | History and Multi-Select | Undo/redo stack, multi-select ops | `useGraphStore.ts`, `GraphCanvas.tsx`, `useKeyboardShortcuts.ts`, `ConfigPanel.tsx` |
| [6-2](6-2-clipboard-context-menu.md) | Clipboard, Context Menu, Edge Reconnect | Copy/paste, right-click menus, rewiring | `useGraphStore.ts`, `GraphCanvas.tsx`, new `ContextMenu.tsx` |
| [6-3](6-3-node-port-editing.md) | Node and Port Editing | Port editor, inline rename, schema builder | `ConfigPanel.tsx`, `DanNode.tsx`, `useGraphStore.ts` |
| [6-4](6-4-validation.md) | Validation | Port-aware connection, validation badges | `connectionValidation.ts`, `DanNode.tsx`, `app.py` |
| [6-5](6-5-graph-io-input-node.md) | Input Node, Graph I/O, Search | InputNode, import/export, Cmd+K, sub-graph from selection | `EditorToolbar.tsx`, new `CommandPalette.tsx`, backend model files |
| [6-6](6-6-execution-ux.md) | Execution UX Gaps | Loop visualization, streaming output, human-in-the-loop popup | `DanNode.tsx`, `LogPanel.tsx`, `OutputPreview.tsx`, `useGraphStore.ts`, `events.py`, `llm.py`, `run_manager.py`, `app.py` |
| [6-7](6-7-workflow-as-node.md) | Workflow as Reusable Node | Insert saved workflows from palette as composite nodes | `NodePalette.tsx`, `GraphCanvas.tsx`, `useGraphStore.ts`, `graphAdapter.ts`/`graphImporter.ts`, `api.ts` |
| [6-8](6-8-patch-up.md) | Patch-Up | Drill-in feedback arrows, port cleanup, multi-entry run readiness | `useGraphStore.ts`, `graphImporter.ts`, `control_flow.py`, `scheduler.py` |
| [6-9](6-9-multi-tab-workflow-sessions.md) | Multi-Tab Workflow Sessions | Multiple workflow tabs with per-tab run state and reconnect-on-activation | `useGraphStore.ts`, `EditorToolbar.tsx`, new `TabBar.tsx`, `App.tsx` |
| [6-10](6-10-gate-loop-condition-redesign.md) | Gate Loop + Condition Redesign | Visible loop flow via gate nodes, condition routing cleanup, collapsible loop groups | `control_flow.py`, `scheduler.py`, `graph.py`, `DanNode.tsx`, `paletteTemplates.ts` |
| [6-11](6-11-workflow-ux-polish.md) | Workflow UX Polish | Run summary (tokens + time), drill-in auto-layout, multi-tab duplicate graphs | `llm.py`, `scheduler.py`, `useGraphStore.ts`, `TabBar.tsx`, `LogPanel.tsx` |
| [6-12](6-12-control-flow-consolidation.md) | Control Flow Consolidation | Drop legacy if_else/while_loop; palette shows only If/Else Gate, While Gate, For Each | `graph.ts`, `graphAdapter.ts`, `paletteTemplates.ts`, `app.py`, `nodeIcons.tsx` |
| [6-13](6-13-multi-dept-visualization.md) | Multi-Dept Visualization | Orchestrator/department visibility, loop_groups, backend layout for vibe research | `layout.py`, `useGraphStore.ts`, `graphAdapter.ts` |

## Dependencies / Sequencing

```
6-1 (History & Multi-Select) ──→ 6-2 (Clipboard, Context Menu)
  │                                  Multi-select state required for
  │                                  copy/paste and "Group into Composite".
  │
6-3 (Node & Port Editing) ──→ 6-4 (Validation)
  │                                Port schemas from 6-3 enable
  │                                schema-aware connection validation.
  │
6-1 (History & Multi-Select) ──→ 6-5 (Input Node, Graph I/O, Search)
                                     Multi-select needed for sub-graph
                                     creation from selection.
```

**Recommended execution order:**
1. **6-1** (History & Multi-Select) — foundational; all other sub-plans benefit from undo/redo and multi-select
2. **6-2** and **6-3** in parallel — 6-2 depends on 6-1; 6-3 is independent
3. **6-4** (Validation) — depends on 6-3 port schemas
4. **6-5** (Input Node, Graph I/O, Search) — depends on 6-1 multi-select; most complex sub-plan
5. **6-6** (Execution UX Gaps) — follow-up polish for run-time UX; can be split into parallel tracks (loop viz, streaming, human input)
6. **6-7** (Workflow as Reusable Node) — reusable composition primitive; can run in parallel with 6-6
7. **6-8** (Patch-Up) — stabilize loop drill-in visuals, workflow-node port derivation, and multi-entry runtime readiness
8. **6-9** (Multi-Tab Workflow Sessions) — extend editor to multi-workflow tab sessions with per-tab state isolation and reconnect-on-activation
9. **6-10** (Gate Loop + Condition Redesign) — replace hidden loop authoring with gate-based visible loops and fix condition-node UX/runtime semantics

## Shared Decisions

- **Run-state isolation:** The history stack must strictly exclude execution state (`runId`, `runStatus`, `nodeStatuses`, `logs`, `nodeTimings`, `ws`, `activeExecutionPath`). `GraphSnapshot` captures only `{ nodes, edges, danGraph }`. All mutating operations (and undo/redo themselves) are disabled when `runStatus === "running"`.
- **Layer-aware mutations:** All editing operations — paste, undo/redo, add-node (context menu or palette drop), delete — must read `currentGraphKey` derived from `layerStack` and apply changes to the active sub-graph, not hardcoded to root `danGraph.nodes`/`danGraph.edges`.
- **Multi-select ripple effects:** The current UI is single-node-centric (`selectedNodeId`). When multiple nodes are selected, `ConfigPanel` shows a "N nodes selected" summary (no single-node editing), `OutputPreview` is hidden, and log-driven `selectNodeFromLog()` clears multi-selection before focusing.
- **Sub-graph grouping scope lock (v1):** Grouping only applies to selections where all cut edges are **data edges**. If any control or context edge crosses the selection boundary, the "Group into Composite" option is disabled with a tooltip explaining why.
- **InputNode value persistence:** The `InputNode` schema stores *variable definitions* (name, type, default, description) in the graph JSON. The *runtime values* the user types before hitting Run are ephemeral UI state only (stored in Zustand or localStorage), passed to `/api/runs` at execution time. Values never pollute the saved graph JSON.
- **Import hard-reset:** Loading a graph via Import must clear `history` (past/future stacks), `layerStack` (pop to root), all execution state (`logs`, `nodeStatuses`, `nodeTimings`, `runId`), `validationErrors`, and `clipboard` to prevent ghost data from the previous graph.
- **State shape:** All sub-plans extending `useGraphStore.ts` must preserve the existing Zustand flat-state pattern. New slices (history, clipboard, selectedNodeIds, validationErrors, inputNodeValues) are added as top-level keys.

## Notes

- 6-1 and 6-3 have no dependencies on other sub-plans and can be started immediately.
- Backend changes include 6-4 (validation API endpoint), 6-5 (InputNode model/executor/registry), and 6-6 (event contracts, LLM streaming, run manager human-input callback, human-input API endpoint). 6-7 is frontend-heavy, with optional lightweight graph-summary API support if needed.
- Edge reconnection (6-2) and multi-select (6-1) leverage built-in React Flow capabilities (`edgesReconnectable`, `selectionOnDrag`).
