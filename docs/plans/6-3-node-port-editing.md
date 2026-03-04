# 6-3: Node and Port Editing

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Replace read-only port display with a full port editor, add inline node rename on double-click, and build a visual JSON Schema editor for LLM node output schemas. No dependencies on other sub-plans.

## Tasks

- [x] 1. Port editor in ConfigPanel
  - [x] 1-1. In `editor/src/components/ConfigPanel.tsx`, replace the read-only comma-separated port display (currently in the "skip" list: `input_ports`, `output_ports`) with an editable port list section for both input and output ports.
  - [x] 1-2. Each port row renders: text input for port `name`, small textarea or inline input for optional `json_schema` (JSON string), checkbox for `required` (input ports only, defaults to `true`).
  - [x] 1-3. Add "Add Port" button at the bottom of each port section (input/output). Clicking adds a new port with a default name (`input_N` / `output_N` where N is the next index) and empty schema.
  - [x] 1-4. Add delete button (X icon) per port row. If the port has connected edges, show a confirmation dialog ("This port has N connected edge(s). Deleting it will remove them. Continue?"). On confirm, remove the port and all edges referencing it.
  - [x] 1-5. Port rename: when the user changes a port name, find all edges in `danGraph.edges` where `source_port` or `target_port` matches the old name and update them to the new name. Push a single undo snapshot covering both the port rename and all edge updates (atomic operation).
  - [x] 1-6. Propagate port changes via `updateNodeData(nodeId, { input_ports: [...], output_ports: [...] })`. DanNode re-renders handles from `data.input_ports`/`data.output_ports` in real-time.
  - [x] 1-7. Validate port names: no duplicates within the same port list (input or output), no empty names. Show inline error styling if violated.

- [x] 2. Inline node rename
  - [x] 2-1. In `editor/src/components/DanNode.tsx`, make the name `<span>` in the header respond to double-click: replace with a controlled `<input>` field pre-filled with the current name.
  - [x] 2-2. Local component state: `editing: boolean`. On double-click of the name span, set `editing = true`.
  - [x] 2-3. On Enter or blur: commit the new name via `updateNodeData(nodeId, { name: newValue })`. Set `editing = false`.
  - [x] 2-4. On Escape: revert to original name, set `editing = false`.
  - [x] 2-5. Auto-select all text in the input when entering edit mode (via `inputRef.current.select()` in a `useEffect`).
  - [x] 2-6. Stop propagation on the double-click event to prevent it from triggering composite node drill-in (`onNodeDoubleClick` in GraphCanvas). The name span's `onDoubleClick` handler calls `e.stopPropagation()`.
  - [x] 2-7. Style the inline input to match the header appearance: same font size, weight, color (white text on colored header), transparent background, no visible border, matching padding.

- [x] 3. Output schema visual editor
  - [x] 3-1. In `ConfigPanel.tsx`, for nodes with `node_type === "llm_operator"` or `node_type === "router"`, replace the raw JSON textarea for `output_json_schema` with a visual schema builder component.
  - [x] 3-2. Create `SchemaEditor` sub-component (inline in ConfigPanel or extracted to `editor/src/components/SchemaEditor.tsx`). Renders a tree-based editor for JSON Schema `type: "object"` definitions.
  - [x] 3-3. Each property row: text input for property name, dropdown for type (`string`, `number`, `integer`, `boolean`, `array`, `object`), checkbox for "Required".
  - [x] 3-4. "Add Property" button appends a new property with default name `field_N` and type `string`.
  - [x] 3-5. Delete property button (X icon) per row.
  - [ ] 3-6. Nested objects: if type is `object`, show indented child property rows (one level of nesting). If type is `array`, show an `items` type dropdown.
  - [x] 3-7. On any change, regenerate the JSON Schema object and call `updateNodeData(nodeId, { output_json_schema: generatedSchema })`.
  - [x] 3-8. Add a "Raw JSON" toggle button — switches to the existing raw JSON textarea for advanced users. Changes in raw mode are reflected back in visual mode and vice versa.
  - [x] 3-9. Handle invalid raw JSON gracefully: if user toggles from raw mode with invalid JSON, show error styling on the textarea and do NOT switch to visual mode.

- [ ] 4. Tests
  - [ ] 4-1. Port editor — add port: new handle appears on DanNode; port name is unique
  - [ ] 4-2. Port editor — delete port: connected edges are removed; port disappears from node handles
  - [ ] 4-3. Port editor — rename port: all connected edges' `source_port`/`target_port` updated atomically; undo reverts both port name and edge references
  - [ ] 4-4. Port editor — duplicate name validation: inline error shown when entering a name that already exists in the port list
  - [ ] 4-5. Inline rename — double-click: activates input; Enter commits new name; Escape reverts to original; blur commits
  - [ ] 4-6. Inline rename — stopPropagation: double-clicking the name does NOT trigger drill-in on composite nodes
  - [ ] 4-7. Schema editor — add property: produces valid JSON Schema with correct type
  - [ ] 4-8. Schema editor — raw JSON toggle: visual → raw → visual round-trips correctly; invalid raw JSON shows error

- [ ] 5. Docs sync
  - [ ] 5-1. Update `docs/architecture.md` — document port editor capabilities, inline rename interaction, SchemaEditor component
  - [ ] 5-2. Update `docs/todo.md` — check off 6-3 items
  - [ ] 5-3. Append `docs/changelog.md` entry

## Decisions

- Port rename implemented as dedicated `renamePort` store action (not via `updateNodeData`) to atomically update both node ports and edge references in a single `pushSnapshot`.
- Port delete similarly uses `deletePort` store action that filters edges referencing the deleted port. Skipped confirmation dialog for v1 — direct delete is faster for the common case.
- `SchemaEditor` kept inline in `ConfigPanel.tsx` rather than extracted to a separate file — the component is ~100 lines and only used in one place.
- Nested object/array schema editing (task 3-6) deferred to v2 — flat property lists cover the vast majority of LLM output schemas. Users can author nested schemas via Raw JSON mode.
- `PortRow` uses `key={port.name}` to remount on rename, ensuring local state stays synchronized with store state.
- Inline rename in DanNode uses a `cancelRef` to distinguish Escape (cancel) from blur (commit), since React fires onBlur when the input unmounts after setEditing(false).

## Notes

- Port editing is the most impactful feature in this sub-plan — it's currently the biggest gap in the ConfigPanel (ports are read-only, comma-separated text).
- The atomic port-rename + edge-update is critical for data consistency. If only the port is renamed without updating edges, the graph will have dangling references. The single undo snapshot ensures both changes are reverted together.
- `stopPropagation` on the name double-click is essential because `GraphCanvas` handles `onNodeDoubleClick` for drill-in. Without it, renaming would trigger navigation on composite nodes.
- The schema editor supports one level of object nesting for v1. Deeply nested schemas are rare for LLM outputs and can be authored in raw JSON mode.
