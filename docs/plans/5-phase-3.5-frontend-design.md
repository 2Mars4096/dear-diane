# 5: Phase 3.5 — Frontend Design

**Status:** completed
**Goal:** Transform the baseline visual editor into a production-quality workflow builder with multi-layered graph navigation, live execution visualization, rich logging, a categorized build palette, and comprehensive UI/UX polish. Layout inspired by LangFlow / Flowise / Coze.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [5-1](5-1-multi-layered-graph.md) | Multi-Layered Graph Navigation | Backend `CompositeExecutor` + canvas drill-in replacing modal | `scheduler.py`, `executors/`, `CompositePreview.tsx`, `GraphCanvas.tsx`, `useGraphStore.ts` |
| [5-2](5-2-live-execution-viz.md) | Live Execution Visualization | CSS animations, execution path highlighting, timeline/playback | `DanNode.tsx`, `GraphCanvas.tsx`, `useGraphStore.ts` |
| [5-3](5-3-rich-logging.md) | Rich Logging Window | New backend event types + structured collapsible log UI | `events.py`, `run_manager.py`, `LogPanel.tsx`, `useGraphStore.ts` |
| [5-4](5-4-build-palette.md) | Build Palette | Searchable categorized sidebar, templates, edge selector | `NodePalette.tsx`, `graphAdapter.ts`, `types/graph.ts` |
| [5-5](5-5-ui-polish.md) | UI/UX Polish | Error handling, loading states, validation, resizable panels, keyboard shortcuts, auto-layout | `App.tsx`, `ConfigPanel.tsx`, `GraphCanvas.tsx`, all components |

## Dependencies / Sequencing

```
5-3 (Rich Logging) ──→ 5-2 (Live Execution Viz)
  │                        New event types (llm_thinking, tool_call_*,
  │                        code_output) must exist before execution
  │                        visualization can consume them.
  │
5-1 (Multi-Layered Graph) ──→ 5-2 (Live Execution Viz)
  │                              Drill-in navigation changes what
  │                              "active node" means across layers.
  │
5-5 (UI/UX Polish) ── independent, can run in parallel with any sub-plan
5-4 (Build Palette) ── independent, can run in parallel with any sub-plan
```

**Recommended execution order:**
1. **5-3** (Rich Logging) — establishes the event contract that 5-2 depends on
2. **5-1** (Multi-Layered Graph) — backend + frontend, foundational for nested execution
3. **5-2** (Live Execution Viz) — depends on 5-3 events and 5-1 drill-in
4. **5-4** and **5-5** — independent, interleave freely around the above

## Shared Decisions

- **Event contract lockstep:** New `EventType` values added in 5-3 must be propagated through `events.py` → `run_manager.py` → WebSocket → `useGraphStore.ts` → UI components. All sub-plans consuming events must reference the same contract.
- **Unified run stream:** Sub-graph node events are emitted under the parent `run_id` (single WebSocket stream). To preserve hierarchy without splitting streams, event payloads include context such as `graph_key`, `layer_path`, and `parent_node_id`.
- **Animation library:** CSS keyframes preferred over JS animation libraries for node/edge effects (5-2). Keeps bundle small and leverages GPU compositing.
- **State shape:** All sub-plans extending `useGraphStore.ts` must preserve the existing Zustand flat-state pattern. New slices (breadcrumb stack, timeline data, log structure) are added as top-level keys.
- **Composite node identity:** `is_blackbox` field (5-1) affects both drill-in behavior and palette display (5-4). Field added to Pydantic model in 5-1, consumed in 5-4.
- **5-1 scope lock:** Drill-in in 5-1 is navigation-only (read-only sub-graph view). Editable sub-graph authoring is deferred to a future plan to avoid persistence/write-back complexity in this phase.
- **Template strategy:** 5-4 starts with a small template set (ReAct, Plan-Execute) but uses an extensible factory contract that supports multi-level sub-graphs when needed.

## Notes

- Phase 3.5-A absorbs old Phase 4 (Composite Nodes); Phase 3.5-B absorbs old Phase 5 (Execution Visualization); Phase 3.5-E absorbs old Phase 2.5 non-bug-fix items.
- No backend API endpoint changes required for 5-2, 5-4, or 5-5 — they are purely frontend. 5-1 and 5-3 require backend changes.
- Editable drill-in is intentionally out of scope for 5-1; all sub-graph mutation flows stay at the root layer during this phase.
