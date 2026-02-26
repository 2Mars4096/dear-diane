# 6-12: Control Flow Consolidation

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Drop legacy if_else and while_loop node types; expose only gate-style control flow: If/Else Gate, While Gate, and For Each.

## Tasks

- [x] 1. Palette: Replace legacy catalog entries with gate-specific ones
  - [x] 1-1. Remove `if_else` and `while_loop` from NODE_TYPE_CATALOG
  - [x] 1-2. Add `gate_if_else` (If/Else Gate) and `gate_while` (While Gate)
  - [x] 1-3. Remove generic `gate` entry (replaced by the two specific entries)
  - [x] 1-4. Keep `for_each` as-is

- [x] 2. graphAdapter + types
  - [x] 2-1. Add `createDefaultNode` cases for `gate_if_else` and `gate_while`
  - [x] 2-2. Add NODE_DESCRIPTIONS for new types
  - [x] 2-3. Update NodeTypeString and any type guards

- [x] 3. Templates
  - [x] 3-1. Remove "IfElse Gate" and "While Gate" from PREDEFINED_AGENT_TEMPLATES (redundant with catalog)
  - [x] 3-2. Update ReAct template: use CompositeNode with GateNode(while) body instead of WhileLoopNode

- [x] 4. Backend + migration
  - [x] 4-1. Enable `DAN_GATE_MIGRATION_ENABLED` by default (or always migrate on graph load)
  - [x] 4-2. Keep IfElseExecutor/WhileLoopExecutor for backward compat when loading un-migrated graphs

- [x] 5. Editor: node icons, ConfigPanel, ContextMenu
  - [x] 5-1. Add nodeIcons for gate_if_else and gate_while (reuse gate icon)
  - [x] 5-2. Ensure ConfigPanel and ContextMenu handle gate nodes (already do)

- [x] 6. Docs
  - [x] 6-1. Update changelog, architecture, todo

## Decisions

- `gate_if_else` and `gate_while` are synthetic catalog types; both create `node_type: "gate"` with the appropriate `gate_mode`.
- ForEach remains container-based (body_graph); no change to its model.
- ReAct template becomes a CompositeNode whose body contains a flat gate loop (LLM → Tool → Gate(while) with back-edge).
