# 5-4: Build Palette

**Parent:** [5-phase-3.5-frontend-design](5-phase-3.5-frontend-design.md)
**Status:** completed
**Goal:** Transform the NodePalette into a LangFlow-style searchable, categorized sidebar with pre-defined agent templates, MCP placeholder, edge type selector, and mini-preview tooltips.

## Tasks

- [ ] 1. Searchable categorized sidebar (NodePalette.tsx)
  - [ ] 1-1. Add a text input at the top of the palette for search; filter `NODE_TYPE_CATALOG` by matching `label` and `type` (case-insensitive)
  - [ ] 1-2. Make category sections collapsible — store expanded/collapsed state in local component state (or a simple `useState<Record<string, boolean>>`); collapsed by default for categories with no matches when filtering
  - [ ] 1-3. Render filtered results: show only categories that have matches; within each category, show only items matching the search string
  - [ ] 1-4. Style search input: rounded, subtle border, placeholder "Search nodes..."; preserve existing `w-52` sidebar width, add `min-h-0` and `flex flex-col` for proper overflow behavior when content grows

- [ ] 2. Pre-defined Agents category (editor/src/lib/paletteTemplates.ts + NodePalette.tsx + useGraphStore.ts)
  - [ ] 2-1. Create `editor/src/lib/paletteTemplates.ts` — define `PREDEFINED_AGENT_TEMPLATES` as array of `{ id, label, description, factory }` where `factory(position)` returns an extensible payload like `{ node: DanNode, rootSubGraphKey: string, subGraphs: Record<string, DanGraph> }` (supports single-level now, multi-level later)
  - [ ] 2-2. Implement ReAct template: factory produces a `WhileLoopNode` with `body_graph` key; sub-graph contains LLM node (think step) + Tool node (act step) + edges; external ports: `input` (prompt), `output` (final result); use `createDefaultNode` patterns for nested nodes with unique IDs (`react_llm_${ts}`, etc.)
  - [ ] 2-3. Implement Plan-Execute template: start with a simple composite flow (planner + executor path) while keeping factory contract compatible with nested generation. Stretch: include nested ForEach body by returning multiple keyed sub-graphs in `subGraphs`
  - [ ] 2-4. Add `addTemplateNode(templateId: string, position: Position)` to `useGraphStore.ts` — resolve template factory, merge all returned `subGraphs` into `danGraph.sub_graphs`, add `node` via `addNode`; ensure `danGraph` exists (guard if null)
  - [ ] 2-5. Add "Pre-defined Agents" category section in NodePalette — render each template as a draggable/clickable pill; on click/drop, call `addTemplateNode` with position (200, 200) or drop coordinates; templates use same `application/dan-node-type` drag format with a `template:` prefix (e.g. `template:react`) so GraphCanvas drop handler can distinguish and call `addTemplateNode`

- [ ] 3. MCP / Wrapped Agents category (NodePalette.tsx)
  - [ ] 3-1. Add "MCP / Wrapped Agents" category section below Pre-defined Agents
  - [ ] 3-2. Render 2–3 placeholder entries (e.g. "MCP Tool", "Custom Wrapper") as disabled, grayed-out pills with `cursor-not-allowed` and `opacity-60`
  - [ ] 3-3. Add "Coming soon" label or badge above/beside the placeholder entries
  - [ ] 3-4. No backend or store changes — purely presentational

- [ ] 4. Edge type selector (useGraphStore.ts, NodePalette.tsx or toolbar)
  - [ ] 4-1. Add `selectedEdgeType: "data" | "control" | "context"` and `setSelectedEdgeType(type)` to `useGraphStore.ts`
  - [ ] 4-2. Update `onConnect` to use `selectedEdgeType` instead of hardcoded `"data"`; for control edges use `condition: null`, for context edges use `context_key: "default"`, `mode: "read"` (user can edit in ConfigPanel later)
  - [ ] 4-3. Add edge type selector UI: small section in NodePalette (or a compact toolbar row above the canvas) with three pill/toggle buttons for Data / Control / Context; use `EDGE_COLORS` from graphAdapter for visual consistency; show selected state (filled vs outline)
  - [ ] 4-4. Persist selection across palette interactions — store in Zustand so it survives re-renders

- [ ] 5. Mini-preview on hover (NodePalette.tsx, types/graph.ts)
  - [ ] 5-1. Extend `NODE_TYPE_CATALOG` (or create a parallel `NODE_DESCRIPTIONS` map) in `types/graph.ts` with `description` and optional `input_ports` / `output_ports` schema summaries for each node type — reuse existing port definitions from `createDefaultNode` where possible
  - [ ] 5-2. Add tooltip to each node pill in NodePalette: on hover, show a small card (e.g. Radix Tooltip, Tippy.js, or native `title` + custom CSS card) with: node `description`, list of input ports with `name` + `schema` summary, list of output ports
  - [ ] 5-3. For template items, show template `description` and a simplified "Pre-built sub-graph" summary instead of raw port schemas
  - [ ] 5-4. Style tooltip: max-w-xs, subtle shadow, rounded corners; avoid blocking palette interactions (position to the right of the sidebar or use a delay before showing)

- [ ] 6. Palette layout and drop handling refinements
  - [ ] 6-1. Update `GraphCanvas.tsx` drop handler: when `application/dan-node-type` starts with `template:`, parse template ID and call `addTemplateNode(templateId, dropPosition)` instead of `addNode(createDefaultNode(...))`
  - [ ] 6-2. Ensure click-to-add for templates uses `addTemplateNode` with fixed (200, 200) position
  - [ ] 6-3. Reorder palette categories for clarity: Operators, Control Flow, Pre-defined Agents, MCP / Wrapped Agents, Composite, Edge Type Selector (or place Edge Type Selector at top as a compact toolbar)

- [ ] 7. Docs sync
  - [ ] 7-1. Update `docs/architecture.md` — add NodePalette subsection under Visual Editor Frontend describing: searchable sidebar, categories, pre-defined templates, edge type selector, hover preview
  - [ ] 7-2. Update `docs/todo.md` — mark Build Palette items complete per plan checklist
  - [ ] 7-3. Append `docs/changelog.md` entry on plan completion

## Decisions

- (filled in during execution)

## Notes

- **body_graph semantics:** `body_graph` is a string key into `DanGraph.sub_graphs`, not serialized JSON. Templates must add both the parent node and the `DanGraph` sub-graph entry. See `graphs/paper_writing.json` and `CompositePreview.tsx` for reference.
- **Template IDs:** Use slugs like `react`, `plan_execute` for `template:` data transfer; store in `PREDEFINED_AGENT_TEMPLATES` with matching `id`.
- **Sub-graph key uniqueness:** Use `{templateId}_body_{timestamp}` or similar to avoid collisions when adding multiple instances of the same template.
- **Simple first, extensible contract:** Initial templates should stay understandable and stable; complexity (multi-level nested sub-graphs such as composite + foreach-body) is supported by the factory return shape and can be added incrementally without API refactor.
- **Edge type selector placement:** Prefer a compact row at the top of the palette (above search) or as a floating toolbar above the canvas — choose based on UX during implementation. Parent plan 5-5 (UI Polish) may refine panel layout.
- **No new dependencies:** Prefer native HTML tooltips or lightweight Tailwind-based hover cards. If tooltip UX is poor, consider `@radix-ui/react-tooltip` (check if already in package.json).
