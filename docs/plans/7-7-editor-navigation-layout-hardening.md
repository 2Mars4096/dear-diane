# 7-7: Editor Navigation & Layout Hardening

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** in-progress
**Goal:** Fix nested drill-in/out/save (capped at depth 3), make port ordering deterministic via logic+rules, and clean up edge routing with optimized connection rules. All changes are editor-only (frontend).

## Background

The vibe-research multi-department workflow exposed two classes of issues:

1. **Nested navigation is broken beyond depth 1.** `drillIn`, `drillOut`, `jumpToLayer`, and `saveGraph` all resolve sub-graphs by looking up a single key in the *root* graph's `sub_graphs`. For 2+ level nesting (root → iteration body → department body), the inner key only exists in the intermediate graph's `sub_graphs`, so lookup returns `undefined`. `PortMappingOverlay` has the same root-only parent lookup.

2. **Port order is non-deterministic and edges are visually messy.** Ports render in raw array order. When connected nodes define the same ports in different orders, handles don't align and edges cross unnecessarily. There is no canonical ordering or connection-aware sorting.

## Tasks

- [x] 1. `resolveGraphAtStack` helper — single source of truth
  - [x] 1-1. Create `resolveGraphAtStack(root: DanGraph, stack: LayerStackEntry[]): DanGraph | null` in `graphAdapter.ts`. Traverses `stack` by walking `sub_graphs` at each level. Returns the graph at the target depth, or `null` if any key is missing or depth exceeds 3. **Fail-closed:** callers that get `null` must auto-reset to root (clear `layerStack`, show toast). Never return a partial/wrong graph.
  - [x] 1-2. Export and import in `useGraphStore.ts` and `PortMappingOverlay.tsx`.

- [x] 2. Fix `drillIn` — resolve from current layer
  - [x] 2-1. In `drillIn`, replace `danGraph.sub_graphs?.[bodyGraphKey]` with: resolve current graph via `resolveGraphAtStack(danGraph, layerStack)`, then look up `bodyGraphKey` in that graph's `sub_graphs`.
  - [x] 2-2. Guard: if `layerStack.length >= 3`, show toast "Maximum drill-in depth reached" and return early.
  - [x] 2-3. Rest of `drillIn` (feedback arrows, auto-layout, loop groups) stays unchanged — it already operates on the resolved sub-graph.

- [x] 3. Fix `drillOut` — traverse full stack
  - [x] 3-1. Replace the `danGraph.sub_graphs?.[lastKey]` lookup with `resolveGraphAtStack(danGraph, newStack)`.

- [x] 4. Fix `jumpToLayer` — traverse full stack
  - [x] 4-1. Replace `danGraph.sub_graphs?.[lastKey]` with `resolveGraphAtStack(danGraph, newStack)`.

- [x] 5. Fix `saveGraph` — nested deep merge
  - [x] 5-1. Create `deepSetSubGraph(root: DanGraph, stack: LayerStackEntry[], updatedSub: DanGraph): DanGraph` helper. Walks the stack from depth 0 to N-1, building a new root with the updated sub-graph nested at the correct level. Returns a new root `DanGraph` (immutable).
  - [x] 5-2. In `saveGraph`'s drilled-in branch, replace the flat `{ ...danGraph, sub_graphs: { ...danGraph.sub_graphs, [activeKey]: updatedSub } }` with `deepSetSubGraph(danGraph, layerStack, updatedSub)`.
  - [x] 5-3. The `subBase` lookup (line 522) must also resolve via `resolveGraphAtStack` to find the correct base graph for `reactFlowToDanGraph`.

- [x] 6. Fix `PortMappingOverlay` — nested parent lookup
  - [x] 6-1. Replace `danGraph.nodes.find(...)` (root-only) with: resolve the *parent* graph via `resolveGraphAtStack(danGraph, layerStack.slice(0, -1))`, then find the parent node in that graph's `nodes`.

- [x] 7. Port ordering — logic + rules
  - [x] 7-1. Create `orderPorts(ports, edges, nodes, nodeId, direction)` utility in `portOrdering.ts`. Deterministic scoring pipeline — each port gets a numeric score; stable sort by score, then by name as tie-breaker. **Precedence (highest → lowest):**
    - **P0 — Gate pins (hardcoded):** For gate nodes, output ports `true`/`continue` get score 0, `false`/`done` get score 1. Input port `input` gets score 0. (scores 0–9)
    - **P1 — Connected, sorted by peer Y:** Score = `1000 + clamp(peerY, 0, 999)`. Clamped to guarantee non-overlap with P0 and P2 bands. When `portReorder` hint from crossing minimization is available, uses reorder index instead of raw Y.
    - **P2 — Unconnected ports:** Score = `10000` (sorts after all connected ports). Among themselves, sub-sort alphabetically by name.
    - **Stable tie-breaker:** When two ports have the same score, sort alphabetically by `port.name`. This guarantees deterministic order across renders.
  - [x] 7-2. In `DanNode.tsx`, wrap `d.input_ports` and `d.output_ports` through `orderPorts(...)` before rendering. Pass current edges, nodes, and optional `portReorder` hint from `computePortReorder`.
  - [x] 7-3. In `ConfigPanel.tsx` port editor sections, keep the raw array order (authoring order) — do NOT sort there. The sort is display-only in the canvas node.

- [x] 8. Edge routing — optimized connection rules
  - [x] 8-1. **Dagre port-aware layout:** In `layout.ts`, when setting dagre edges, pass `{ minlen: 1 }` and optionally port-specific weighting. When two connected nodes share same-name ports, add dagre edge weight to encourage alignment.
  - [x] 8-2. **Smoothstep offset per port:** In `AnimatedEdge.tsx` (or a new custom edge component), compute `borderRadius` and `offset` from the vertical index of the source/target port. React Flow's `getSmoothStepPath` accepts `borderRadius` and `offset` — use these to separate edges that share a source/target node but connect to different ports.
  - [x] 8-3. **Edge label deduplication (data edges only):** When source→target has multiple *data* edges, only show edge label on the first; collapse remaining labels or show a count badge. Control and context edges always keep their labels (they carry distinct semantics).
  - [x] 8-4. **Crossing minimization heuristic (once, post-layout only):** After dagre layout, if two edges between the same source/target pair cross (source port A above B, but target port B above A), swap the target port render order to uncross. Runs once after `layoutGraph` or `drillIn` auto-layout — NOT on every render. Results are cached until next layout pass to prevent oscillation.

- [x] 9. Depth-3 guard in breadcrumb
  - [x] 9-1. In `BreadcrumbBar.tsx`, when `layerStack.length >= 3`, visually indicate max depth (e.g., dim the last segment, add tooltip "Max drill-in depth").

- [ ] 10. Tests
  - [ ] 10-1. Unit test `resolveGraphAtStack`: root (empty stack), depth-1, depth-2, depth-3, depth-4 (caps at 3).
  - [ ] 10-2. Unit test `deepSetSubGraph`: verify immutable update at depth 1, 2, 3.
  - [ ] 10-3. Unit test `orderPorts`: connected ports sort by peer Y, gate ports pinned, unconnected last, fallback alphabetical.
  - [ ] 10-4. Integration: load vibe-research graph, drill root → iteration → dept_REV (depth 2). Verify nodes render and breadcrumb shows 3 segments.
  - [ ] 10-5. Integration: drill to depth 2, save, reload — verify dept body edits persist at correct nested path.

- [ ] 11. Docs sync
  - [ ] 11-1. Update `docs/architecture.md` — document `resolveGraphAtStack`, depth-3 cap, port ordering rules, edge routing.
  - [ ] 11-2. Update `docs/changelog.md` after implementation.
  - [ ] 11-3. Update `docs/bugs.md` — mark nested drill-in and port alignment as resolved.

## Decisions

- **Depth cap = 3, fail-closed.** Root → level-1 body → level-2 body → level-3 body. `resolveGraphAtStack` returns `null` on invalid path or depth > 3. Callers auto-reset to root (`layerStack = []`) and show toast. Never navigate/save against a wrong graph.
- **Port ordering is display-only.** The underlying `input_ports` / `output_ports` arrays in the data model are NOT mutated. Ordering is computed at render time in `DanNode.tsx`. ConfigPanel port editor keeps raw order for authoring control.
- **Port ordering uses deterministic scoring with clamped bands.** P0 gate-pins (0–9) > P1 connected (1000–1999, peer Y clamped) > P2 unconnected (10000+). Bands never overlap. `computePortReorder` hint from crossing minimization is used as sub-sort within P1 when available.
- **`resolveGraphAtStack` is the single entry point.** All code paths that need "the graph at current layer" go through this helper. No more ad-hoc `danGraph.sub_graphs?.[key]` lookups.
- **`deepSetSubGraph` produces a new root.** Immutable update — no mutation of `danGraph`. Matches existing save pattern.
- **Edge routing is rule-based, not smoothing.** No bezier/spline changes. Focus: port-aware dagre weights, per-port smoothstep offsets, label dedup, crossing minimization via port reorder.
- **Label dedup is data-edges only.** Control and context edges always show labels.
- **Crossing minimization runs once post-layout**, not on every render. `computePortReorder` reads actual port indices from node data and computes real crossings. Results are consumed by `orderPorts` as a `portReorder` hint for input ports. Wired through `DanNode.tsx` via `useMemo`.

## Primary Files

| File | Changes |
|------|---------|
| `editor/src/lib/graphAdapter.ts` | `resolveGraphAtStack`, `deepSetSubGraph`, `orderPorts` |
| `editor/src/store/useGraphStore.ts` | `drillIn`, `drillOut`, `jumpToLayer`, `saveGraph` — use new helpers |
| `editor/src/components/DanNode.tsx` | Port rendering through `orderPorts` |
| `editor/src/components/PortMappingOverlay.tsx` | Nested parent resolution |
| `editor/src/components/BreadcrumbBar.tsx` | Depth-3 visual indicator |
| `editor/src/lib/layout.ts` | Port-aware dagre edge weights |
| `editor/src/components/AnimatedEdge.tsx` | Per-port smoothstep offset |
| `editor/src/components/ConfigPanel.tsx` | No change (keeps raw order) |

## Notes

- The root cause of the drill-in bug is that `sub_graphs` is a flat dict at each graph level, not a global registry. Each composite's body graph lives in the *parent* graph's `sub_graphs`. This is by design (encapsulation), but all navigation code assumed root-level lookup.
- `drillOut` and `jumpToLayer` had a partial fix for depth-1 (they do check `newStack.length === 0` vs root), but the else branch is single-level only.
- `saveGraph` has the most complex fix because it needs to produce a new root graph with the update merged at arbitrary depth. The immutable `deepSetSubGraph` helper avoids mutation bugs.
- Port ordering rule 1 (peer Y-position) requires access to the node positions of connected peers. This is available from the store's `nodes` array. We pass a minimal lookup map rather than the full store.
- The crossing minimization heuristic (task 8-4) is a post-layout refinement. If it proves too complex for this milestone, it can be deferred — the other ordering rules handle the common cases.
