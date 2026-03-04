# 6-5: Input Node, Graph I/O, Search, Sub-Graph from Selection

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Add a dedicated InputNode type for canvas-based workflow inputs, JSON import/export for graph sharing, a Cmd+K command palette for node search, and sub-graph creation from multi-selected nodes. Depends on 6-1 for multi-select.

## Tasks

- [x] 1. InputNode — backend model and executor
  - [x] 1-1. Define `InputVariable` model in `src/dan/models/control_flow.py`: `name: str`, `type: Literal["string", "number", "boolean"]`, `default: Any = None`, `description: str = ""`
  - [x] 1-2. Define `InputNode` model in `src/dan/models/control_flow.py`: `node_type: Literal["input"] = "input"`, `variables: list[InputVariable] = []`, plus inherited `NodeBase` fields (id, name, input_ports, output_ports, position, etc.)
  - [x] 1-3. Add `InputNode` to the `Node` discriminated union in `src/dan/models/graph.py`
  - [x] 1-4. Register `"input"` in `src/dan/registry.py` (`NodeTypeRegistry`)
  - [x] 1-5. Create `InputExecutor` in `src/dan/executors/input.py` — trivial pass-through executor: reads variable values from `inputs` dict (keyed by variable name), returns them as output ports (one output port per variable). If a variable has no input value, use its `default`.
  - [x] 1-6. Register `InputExecutor` in `src/dan/engine/scheduler.py` `_register_defaults`: `("input", InputExecutor())`
  - [x] 1-7. Import `InputExecutor` in `src/dan/executors/__init__.py`

- [x] 2. InputNode — frontend integration
  - [x] 2-1. Add `"input"` to `NodeType` union in `editor/src/types/graph.ts`
  - [x] 2-2. Add entry to `NODE_TYPE_CATALOG` and `NODE_DESCRIPTIONS` in `editor/src/types/graph.ts` — category: "io", description: "Visual entry point for workflow inputs"
  - [x] 2-3. Add `createDefaultNode` case for `"input"` in `editor/src/lib/graphAdapter.ts` — default: one variable `{ name: "input", type: "string", default: "", description: "" }`
  - [x] 2-4. Add icon for `"input"` in `editor/src/lib/nodeIcons.tsx` — play triangle icon
  - [x] 2-5. Add palette entry in `editor/src/components/NodePalette.tsx` under "Input / Output" category

- [x] 3. InputNode — canvas rendering and value persistence
  - [x] 3-1. In `DanNode.tsx`, add special rendering for `node_type === "input"`: display editable fields per variable (text input for string, number input for number, checkbox for boolean) directly on the node body.
  - [x] 3-2. Add `inputNodeValues: Record<string, Record<string, unknown>>` to Zustand store — keyed by node ID, then by variable name.
  - [x] 3-3. Input fields on the node read from and write to `inputNodeValues[nodeId][variableName]`. Initialize from variable `default` values on first render.
  - [x] 3-4. Pre-run behavior: in `startRun()`, if graph contains an InputNode, collect values from `inputNodeValues` and pass them as `inputs` to `POST /api/runs`.
  - [x] 3-5. Variable definitions (name, type, default, description) are part of the node data and saved in graph JSON via normal `updateNodeData`. Only runtime values are ephemeral.

- [x] 4. Import/Export graph JSON
  - [x] 4-1. Add "Export" button to `editor/src/components/EditorToolbar.tsx` — serializes `danGraph` to formatted JSON, triggers browser download.
  - [x] 4-2. Add "Import" button to `EditorToolbar.tsx` — file picker for `.json`, parses, validates `dan_graph_v1` structure.
  - [x] 4-3. On valid import: call `POST /api/graphs` to create a new graph with the imported data, then `loadGraph()` the new graph.
  - [x] 4-4. **Hard-reset on import:** clear `history`, `layerStack`, all execution state, `clipboard`, `inputNodeValues`.
  - [x] 4-5. Error handling: toast on invalid JSON parse, toast on missing `format_version` or unexpected structure.

- [x] 5. Node search / jump (Cmd+K)
  - [x] 5-1. Create `editor/src/components/CommandPalette.tsx` — modal overlay with search input and scrollable filtered node list.
  - [x] 5-2. Add `commandPaletteOpen: boolean` to Zustand store.
  - [x] 5-3. Bind `Cmd/Ctrl+K` in `useKeyboardShortcuts.ts` → toggle `commandPaletteOpen`.
  - [x] 5-4. Search logic: filter by substring match on `name` and `node_type`, show icon + name + type.
  - [x] 5-5. On select: close palette, center viewport on the selected node via `fitView`, set `selectedNodeId`.
  - [x] 5-6. Keyboard navigation: arrow keys to move through results, Enter to select, Escape to close.
  - [x] 5-7. Mount `CommandPalette` in `App.tsx`, conditionally rendered when `commandPaletteOpen` is true.

- [x] 6. Sub-graph creation from selection
  - [x] 6-1. Add `groupIntoComposite()` action to Zustand store — operates on `selectedNodeIds`.
  - [x] 6-2. Step 1 — identify internal edges (both endpoints in selection).
  - [x] 6-3. Step 2 — identify cut edges; abort with toast if any cut edge is non-data.
  - [x] 6-4. Step 3 — generate CompositeNode with `in_`/`out_` port naming and collision suffixes.
  - [x] 6-5. Step 4 — create sub-graph with entry/exit points.
  - [x] 6-6. Step 5 — update parent graph: remove selected nodes/edges, add composite + rewired edges.
  - [x] 6-7. Push undo snapshot before the entire operation.
  - [x] 6-8. After grouping, select the new CompositeNode and deselect everything else.

- [ ] 7. Tests
  - [ ] 7-1. InputNode backend: model round-trips through JSON serialization; executor returns variable values as outputs; missing value falls back to default
  - [ ] 7-2. InputNode frontend: DanNode renders input fields for each variable; values stored in `inputNodeValues` (ephemeral); `startRun` passes values to API
  - [ ] 7-3. Import: valid JSON creates new graph; invalid JSON shows error toast; import clears all ephemeral state (history, layerStack, run state, clipboard)
  - [ ] 7-4. Export: exported JSON matches current `danGraph`; filename uses graph name
  - [ ] 7-5. CommandPalette: search by name matches; search by type matches; selecting centers viewport on node; Escape closes palette
  - [ ] 7-6. Sub-graph grouping: 3 nodes with internal data edges → CompositeNode created with correct input/output mappings; cut control edge → operation aborted with toast; port naming is deterministic and collision-free; undo restores original graph
  - [ ] 7-7. Sub-graph grouping entry/exit points: resulting sub-graph has correct `entry_points` (nodes with inbound cut edges) and `exit_points` (nodes with outbound cut edges)

- [ ] 8. Docs sync
  - [ ] 8-1. Update `docs/architecture.md` — document InputNode (model, executor, registry entries), CommandPalette component, `inputNodeValues` store slice, `groupIntoComposite` algorithm
  - [ ] 8-2. Update `docs/todo.md` — check off 6-5 items
  - [ ] 8-3. Append `docs/changelog.md` entry

## Decisions

- Used `InputNodeType` as the TS interface name to avoid clash with DOM `InputNode`
- Category `"io"` in NODE_TYPE_CATALOG rather than `"input_output"` for brevity
- `commandPaletteOpen` lives in Zustand store (not local state) so keyboard shortcut hook can toggle it
- `Cmd+Shift+G` bound as the group-into-composite shortcut
- Import creates a new graph (via `POST /api/graphs`) rather than overwriting the current one

## Notes

- InputNode is intentionally lightweight — it's not a full HumanInTheLoop node. It doesn't pause execution or wait for user input during a run. It's a pre-run configuration surface. The HumanNode generalization (backlog item) may eventually subsume this, but InputNode serves the immediate need.
- Variable definitions vs runtime values separation is critical. Definitions are part of the graph schema (saved). Values are like environment variables at runtime (ephemeral). This prevents dirty git state from test runs.
- Sub-graph creation is the most complex single feature in Phase 3.75. The algorithm has 5 steps and must handle edge remapping, port generation, and sub-graph bookkeeping atomically. Thorough testing is essential.
- The `entry_points` / `exit_points` fields on the sub-graph must be set correctly for `CompositeExecutor` to work. Entry points are nodes whose input ports receive data from the composite's input mappings. Exit points are nodes whose output ports are mapped to the composite's output ports.
- Port naming uses a deterministic scheme (`in_{port}`, `out_{port}`) with collision suffixes. This ensures consistent results regardless of edge ordering.
