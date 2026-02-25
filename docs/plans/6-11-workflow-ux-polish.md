# 6-11: Workflow UX Polish

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Three targeted UX fixes: (1) show total tokens used and wall-clock time when a workflow finishes, (2) auto-layout sub-graph nodes on drill-in so they do not stack, and (3) allow the same graph template to be opened in multiple tabs independently.

## Tasks

- [x] 1. Run summary: token usage and elapsed time
  - [x] 1-1. Capture token usage from LLM API responses
    - In [`src/dan/executors/llm.py`](../../src/dan/executors/llm.py), `_call_llm` returns `tuple[str, str | None]`. Widen to `tuple[str, str | None, dict | None]` where the third element is the usage dict.
    - **Streaming path:** Pass `stream_options={"include_usage": True}` in kwargs. Track the last chunk; read `chunk.usage` after the loop. If the provider does not support `stream_options`, catch the error and fall back to `usage = None`.
    - **Non-streaming fallback:** Read `resp.usage` directly from the response object.
    - Convert to a plain dict `{"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}`, defaulting to `None` when the provider returns nothing.
  - [x] 1-2. Emit per-node token counts in `node_completed` events
    - In `LLMExecutor.execute()`, propagate the usage dict from `_call_llm` into `NodeResult.metadata` (e.g. `metadata={"model": ..., "attempts": ..., "usage": usage_dict}`).
    - The scheduler already includes `result.metadata` in the `NODE_COMPLETED` event data (scheduler.py line ~667), so token counts will appear in events automatically.
  - [x] 1-3. Accumulate run-level totals in `_build_result`
    - In [`src/dan/engine/scheduler.py`](../../src/dan/engine/scheduler.py), `_build_result` already iterates `state.node_metadata`. Aggregate `usage` dicts from all nodes that have them into `result.metadata["total_usage"]`.
    - Record `_run_start_time = time.time()` at the top of `_execute()` and include `elapsed_seconds` in the `run_completed` / `run_failed` event data alongside the aggregated usage.
  - [x] 1-4. Display run summary in frontend
    - In [`editor/src/store/useGraphStore.ts`](../../editor/src/store/useGraphStore.ts), when `handleRunEvent` sees `run_completed` or `run_failed`, store `event.data` as `runSummary: { ... } | null`. Add to `TabSnapshot` so it persists across tab switches.
    - In [`editor/src/components/LogPanel.tsx`](../../editor/src/components/LogPanel.tsx), render a compact summary bar at the bottom of the log panel: "Completed in 42.3s | 12,450 tokens (8,200 prompt + 4,250 completion)".
    - For `node_completed` events from LLM nodes, show per-node token counts in the expandable log row.

- [x] 2. Auto-layout on drill-in for stacked sub-graphs
  - [x] 2-1. Extract `needsAutoLayout(nodes)` helper
    - Create a small utility function (in `layout.ts` or inline) that returns `true` when positions are degenerate: all nodes share the same `(x, y)`, or bounding-box span < 50px in both axes, or any node has `NaN`/`undefined` position.
  - [x] 2-2. Apply dagre layout in `drillIn`
    - In the `drillIn` action in [`editor/src/store/useGraphStore.ts`](../../editor/src/store/useGraphStore.ts), after `danGraphToReactFlow(sg)`, call `needsAutoLayout(rfNodes)` and if true, `rfNodes = layoutGraph(rfNodes, rfEdges)`.
  - [x] 2-3. Apply to `drillOut` and `jumpToLayer`
    - Same check and layout in `drillOut` and `jumpToLayer` for consistency.

- [x] 3. Allow same graph template in multiple tabs
  - [x] 3-1. Remove the already-open filter from `TabBar.tsx`
    - In [`editor/src/components/TabBar.tsx`](../../editor/src/components/TabBar.tsx) (lines 34-35), remove the `openGraphIds` filter. Show the full `graphList`. Replace the "All graphs are already open" empty state with "No saved graphs" when `graphList` is empty.
  - [x] 3-2. Remove the `existing` short-circuit in `openTab`
    - In [`editor/src/store/useGraphStore.ts`](../../editor/src/store/useGraphStore.ts), remove the `tabs.find(t => t.graphId === graphId)` guard so `openTab` always creates a new independent tab.
  - [x] 3-3. Disambiguate tab names for duplicate graphs
    - In `TabBar.tsx`, compute occurrence counts at render time. First occurrence keeps the plain name; subsequent ones get " (2)", " (3)", etc.
  - [x] 3-4. Allow template switching in the current tab
    - Added `replaceActiveTabGraph(graphId)` action in [`editor/src/store/useGraphStore.ts`](../../editor/src/store/useGraphStore.ts) to replace the active tab's graph/template in-place (with dirty-state confirmation and run/log reset).
    - Updated [`editor/src/components/TabBar.tsx`](../../editor/src/components/TabBar.tsx) with a dedicated "↺" picker mode for "Replace current tab", while keeping "+" for opening a new tab.

## Decisions

- Token usage: use `stream_options={"include_usage": True}` (OpenAI v1.x+). Wrap in try/catch — if the provider rejects `stream_options`, fall back silently to `usage = None`.
- Token aggregation: aggregate from `state.node_metadata` in `_build_result` rather than tracking mutable state on the Engine instance. Cleaner, no new instance fields.
- Layout: reuse existing dagre utility. The degenerate-position check preserves manually-positioned sub-graphs. Also handle `NaN`/`undefined` positions.
- Tabs: each tab is an independent workflow instance with its own snapshot. **Save-conflict note:** two tabs editing the same graph will write to the same server-side `graphId` on save. This is accepted for v1; a future enhancement could add optimistic-lock versioning or "save as copy".

## Notes

- The `fitView` call in `GraphCanvas.tsx` already fires on `layerStack` change, so viewport auto-centers after layout.
- `RunRecord` in `run_manager.py` already tracks `started_at`/`finished_at`, but the `run_completed` engine event is where the frontend reads data, so elapsed must be in the event payload.
