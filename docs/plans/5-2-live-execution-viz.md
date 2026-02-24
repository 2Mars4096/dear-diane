# 5-2: Live Execution Visualization

**Parent:** [5-phase-3.5-frontend-design](5-phase-3.5-frontend-design.md)
**Status:** completed
**Goal:** Add live execution feedback to the graph canvas — pulse/glow on active nodes, particle flow along edges during data passing, dimmed inactive nodes/edges, a timeline scrubber with node start/end markers, and per-node duration badges.

## Tasks

- [ ] 1. Active node pulse/glow animation (CSS keyframes)
  - [ ] 1-1. Add `@keyframes dan-node-pulse` and `dan-node-glow` in `editor/src/index.css` (or a new `editor/src/styles/execution.css` module). Animation: subtle ring scale/opacity for active state, stronger for `node_started`.
  - [ ] 1-2. In `editor/src/components/DanNode.tsx`, apply animation class only when `status === "node_started"`. Use `animate-pulse`-like keyframe; avoid JS-based animation. Ensure `STATUS_RING` classes remain for completed/failed/skipped, but add `animation: dan-node-pulse 1.5s ease-in-out infinite` when active.
  - [ ] 1-3. Stretch: add a softer glow for `node_completed` (one-shot fade-out); core deliverable is `node_started` pulse only

- [ ] 2. Node timings derivation in store
  - [ ] 2-1. In `editor/src/store/useGraphStore.ts`, add `nodeTimings: Record<string, { start: number; end?: number }>` to the state interface and initial state.
  - [ ] 2-2. In `handleRunEvent`, on `node_started` set `nodeTimings[nodeId] = { start: ts }`. On `node_completed` or `node_failed`, set `nodeTimings[nodeId].end = ts`. Reset `nodeTimings` when `startRun` or `resumeRun` (alongside existing `nodeStatuses` reset).
  - [ ] 2-3. Ensure timestamps from events use seconds (backend `timestamp`). Convert to ms if needed for UI (e.g. duration display: `(end - start) * 1000` ms).

- [ ] 3. Per-node duration badges on completed nodes
  - [ ] 3-1. In `editor/src/components/DanNode.tsx`, read `nodeTimings` from `useGraphStore`. When `status === "node_completed"` and `nodeTimings[id]?.end` exists, compute duration `(end - start) * 1000` ms.
  - [ ] 3-2. Render a small badge (e.g. bottom-right or below status text): `{duration}ms` with muted styling (`text-[10px] text-gray-400`). Format: `123ms`, `1.2s` for values ≥ 1000.
  - [ ] 3-3. Do not show duration for `node_failed` or `node_skipped`. Stretch: show partial duration for failed nodes

- [ ] 4. Custom animated edge with particle flow
  - [ ] 4-1. Create `editor/src/components/AnimatedEdge.tsx` — a custom React Flow edge component. Use `getSmoothStepPath()` from `@xyflow/react` with `sourceX`, `sourceY`, `targetX`, `targetY`, `sourcePosition`, `targetPosition` from edge props to get the SVG path; render via `BaseEdge`.
  - [ ] 4-2. Render the base edge (stroke) plus an animated circle/dot traveling along the path. Use SVG `<circle>` with `offset-path: path(...)` and `animation: moveAlongPath 1.5s linear infinite` (CSS), or a `<path>`-based `stroke-dasharray` / `stroke-dashoffset` animation. Prefer CSS keyframes for performance.
  - [ ] 4-3. Only show particles when the edge is "active" — i.e. when data is passing. Infer from store: edge `source→target` is active if `nodeStatuses[source] === "node_completed"` and `nodeStatuses[target] === "node_started"` (data just passed). Alternatively, listen for `node_output` events and track which edges had recent output. Start with the simpler heuristic: show particles on any edge where target is `node_started` and source is `node_completed`.
  - [ ] 4-4. In `editor/src/components/GraphCanvas.tsx`, add `edgeTypes={{ animatedEdge: AnimatedEdge }}` and pass `type: "animatedEdge"` for edges during run, or always use `animatedEdge` and let component decide visibility based on active state. Simpler: use a single edge type that conditionally animates.
  - [ ] 4-5. Preserve `graphAdapter.ts` edge styling (color by `edge_type`, `smoothstep`, `markerEnd`). The custom edge must accept and pass through `style`, `markerEnd`, `data.danEdge` from `danEdgeToReactFlow`. Update `danEdgeToReactFlow` or GraphCanvas edge mapping so custom edges receive the same props.

- [ ] 5. Execution path highlighting (dim inactive)
  - [ ] 5-1. In `editor/src/store/useGraphStore.ts`, add `activeExecutionPath: Set<string>` (node IDs) and optionally `activeEdges: Set<string>` (edge IDs). These are derived in `handleRunEvent`: during a run, the active path = nodes with `node_started` or recently `node_completed` (e.g. in last N ms or until run ends) + their upstream/downstream edges. For MVP: active = `{ nodeId where nodeStatuses[nodeId] === "node_started" } ∪ { nodeId where nodeStatuses[nodeId] === "node_completed" and has downstream node_started }` (simplified: all nodes that have ever received status during this run, with "current" = node_started; dim those not on path from roots to current). Simpler heuristic: active = all nodes with non-empty status; inactive = nodes with no status. Refinement: inactive = nodes that will run later (not yet started) or that ran long ago (completed, no active downstream). Start with: **inactive = nodes with no status**; **active = nodes with any status**. Dim the rest.
  - [ ] 5-2. Refine: "active trail" = path from graph roots to current `node_started` plus completed predecessors. Nodes not on this trail get `opacity: 0.4`. Requires topological context — may need to compute from `nodes` + `edges` in the store. Add helper `getActiveExecutionPath(nodes, edges, nodeStatuses, runStatus)`.
  - [ ] 5-3. In `editor/src/components/DanNode.tsx`, read `activeExecutionPath` (or equivalent). When `runStatus === "running"` and node is not in active path, add `opacity-40` or `opacity-50` class. When in path, keep full opacity.
  - [ ] 5-4. In `editor/src/components/AnimatedEdge.tsx` (or a wrapper), apply same dimming: edges not on active path get `opacity: 0.4` when `runStatus === "running"`.
  - [ ] 5-5. When run is not active (`runStatus` is `null`, `"completed"`, or `"failed"`), do not dim — show full opacity for all nodes/edges.

- [ ] 6. Execution timeline / playback scrubber
  - [ ] 6-1. Create `editor/src/components/ExecutionTimeline.tsx`. Layout: horizontal bar with node "blocks" as colored segments. Each node that has `nodeTimings[id]` gets a segment from `start` to `end` (or `start` to `now` if still running). Order segments by start time. Use node names or truncate IDs for labels.
  - [ ] 6-2. Add a scrubber thumb: vertical line or draggable handle. Position = current playback time. For live runs, current time = `Date.now() / 1000` (or latest event timestamp). For completed runs, current time = last `node_completed` timestamp.
  - [ ] 6-3. On scrubber click/drag: set `timelinePosition: number | null` in store (or `scrubberTime`). When set, highlight the node(s) that were active at that time (e.g. node with `start <= scrubberTime <= end`). Optionally scroll canvas to that node or show a "jump to" highlight. Start with: clicking a segment jumps scrubber to that node's start and selects that node (`setSelectedNode(id)`).
  - [ ] 6-4. Integrate into bottom panel or RunPanel area. Per `App.tsx`, the bottom panel has Logs/Output tabs. Add a collapsible "Timeline" tab or a compact timeline bar above the tabs (e.g. only visible when `runStatus` is `"running"` or when `nodeTimings` has entries). Decide placement in task 6-4.
  - [ ] 6-5. When run completes, timeline remains visible so user can scrub through past execution. Clear `nodeTimings` only on new `startRun` / `resumeRun`.

- [ ] 7. Docs sync
  - [ ] 7-1. Update `docs/architecture.md` — add `AnimatedEdge.tsx`, `ExecutionTimeline.tsx` to directory structure; note `nodeTimings`, `activeExecutionPath` in store
  - [ ] 7-2. Update `docs/todo.md` — mark Section B items complete
  - [ ] 7-3. Append `docs/changelog.md` entry on completion

## Decisions

- (filled in during execution)

## Notes

- **Dependencies:** 5-2 depends on 5-3 (Rich Logging) for extended event types — but core `node_started` / `node_completed` already exist. Pulse, timings, duration badges, and path highlighting can proceed with current events. Timeline scrubber and edge particles rely on same events. If 5-3 adds `llm_thinking`, `tool_call_started`, etc., consider extending timeline to show sub-phases per node in a later iteration.
- **5-1 Multi-Layered Graph:** When drill-in is implemented, "active node" is scoped to the current view layer. For 5-2 MVP, assume single-layer view; when 5-1 lands, `activeExecutionPath` and dimming must respect the visible subgraph (only dim nodes in current view).
- **Edge particle activation:** The heuristic "source completed + target started" may miss parallel branches. A more accurate approach: track `node_output` events and map outputs to edges by port. Defer to 5-3 integration if needed.
- **CSS over JS:** All animations use CSS keyframes (`@keyframes`) to leverage GPU compositing and avoid reflows. No `requestAnimationFrame` or animation libraries.
- **React Flow custom edges:** `@xyflow/react` supports `BaseEdge` and `EdgeProps`. Use `getSmoothStepPath()` for path geometry. Custom edge receives `source`, `target`, `sourceX`, `sourceY`, `targetX`, `targetY`, `sourcePosition`, `targetPosition`, `style`, `data`, etc.
