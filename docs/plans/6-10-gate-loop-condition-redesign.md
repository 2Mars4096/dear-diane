# 6-10: Gate Loop + Condition Node Redesign

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Replace hidden loop-container authoring with a visible gate-based loop pattern, redesign condition routing as first-class branch ports, and add collapsible loop groups so users can choose whether to show loop internals.

## Context

Current behavior has two UX/architecture gaps:

1. `while_loop` is modeled as a container with `body_graph` hidden in `sub_graphs`, so top-level workflows do not show loop internals unless users drill in.
2. `if_else` works at runtime but is hard to use in the editor (no first-class template/UX, branching depends on `ControlEdge.condition` + `_should_skip` indirection).

The user request is to move toward a gate-style, flat workflow where loop internals are first-class nodes, while still allowing users to collapse/expand that region visually.

## Tasks

- [x] 1. Freeze scope and compatibility strategy
  - [x] 1-1. Scope 6-10 to **while-loop + condition routing** redesign; keep `for_each` and `composite` container models unchanged in this phase.
  - [x] 1-2. Define `GateNode` contract (`gate_mode: if_else | while`, branch outputs, condition, max_iterations).
  - [x] 1-3. Backward compatibility policy: legacy `if_else` and `while_loop` graphs continue to execute during transition.

- [x] 2. Backend model + executor foundations
  - [x] 2-1. Add `GateNode` model in `src/dan/models/control_flow.py`.
  - [x] 2-2. Implement `GateExecutor` in `src/dan/executors/control_flow.py`:
    - evaluate condition safely,
    - emit active branch output only,
    - track iteration counters for `while` mode (reads `gate_iteration` from `local_state`),
    - auto-derive `output_ports` from `gate_mode` via `model_post_init`.
  - [x] 2-3. Add compatibility shim: legacy `IfElseExecutor`/`WhileLoopExecutor` delegate or map to new gate semantics where feasible, without breaking existing graphs.

- [x] 3. Cycle-aware scheduling (targeted, not full rewrite)
  - [x] 3-1. Extend scheduler in `src/dan/engine/scheduler.py` to support gate-controlled back-edges for while loops.
  - [x] 3-2. Implement cycle-region detection for while-gate loops and bounded re-execution (`max_iterations` guard).
  - [x] 3-3. Preserve DAG fast-path for graphs without gate back-edges.
  - [x] 3-4. Add event isolation so loop iteration events remain stable for timeline/log UI.

- [x] 4. Validation + condition semantics cleanup
  - [x] 4-1. Update `src/dan/validation/graph.py` to allow only valid gate-controlled cycles.
  - [x] 4-2. Reject invalid cycles (no gate control, ambiguous multi-gate cycles in one loop region, missing iteration bounds).
  - [x] 4-3. Deprecate `ControlEdge.condition` for new branching flows; keep legacy compatibility checks for old graphs.

- [x] 5. Editor UX for gate + visible loop flow
  - [x] 5-1. Add gate templates in `editor/src/lib/paletteTemplates.ts`:
    - IfElse Gate (`true`/`false`)
    - While Gate (`continue`/`done`)
  - [x] 5-2. Add gate rendering in `editor/src/components/DanNode.tsx` (condition badge + branch-handle affordances).
  - [x] 5-3. Add back-edge visual styling (dashed curved feedback edge) in graph adapter/store/canvas flow.
  - [x] 5-4. Add collapsible loop groups (show/hide internals) using persisted graph metadata and a minimal group UI.

- [x] 6. Migration path (safe rollout)
  - [x] 6-1. Add graph migration helpers for legacy node/edge forms:
    - `if_else` -> `gate(if_else)` mapping
    - legacy branch edges -> gate branch-port mappings
  - [x] 6-2. For legacy `while_loop` containers, provide deterministic transition path:
    - runtime compatibility first,
    - explicit conversion tool for flattening `body_graph` into visible loop regions.
  - [x] 6-3. Ensure import/export and adapter layers preserve both legacy and migrated graphs during transition.
  - [x] 6-4. Update builder/decompiler compatibility (`dan.builder`, importer/decompiler paths) so gate nodes can be authored/round-tripped without breaking existing `while_loop`/`if_else` workflows.
  - [x] 6-5. Add rollout guard (feature flag or config gate) so gate-default authoring can be enabled after migration tests pass.

- [ ] 7. Tests
  - [x] 7-1. Engine tests for gate branching and while back-edge execution.
  - [x] 7-2. Validation tests for allowed/rejected cycle patterns.
  - [x] 7-3. Migration tests for legacy `if_else`/`while_loop` graphs.
  - [x] 7-4. Builder/decompiler round-trip tests for new gate nodes and legacy-node compatibility.
  - [ ] 7-5. Editor tests for gate templates, branch handles, back-edge visuals, and collapse/expand behavior.
  - [ ] 7-6. End-to-end scenario: visible flat loop with gate feedback edge runs correctly and remains readable when collapsed.

- [x] 8. Docs sync
  - [x] 8-1. Update `docs/architecture.md` with gate model, cycle scheduling behavior, and migration policy.
  - [x] 8-2. Update parent/todo/changelog tracking docs after planning and implementation.

## Decisions

- Replace hidden while-loop authoring with gate-based visible flow as the **target model**, but roll out with compatibility to avoid breaking existing graphs.
- Condition routing for new graphs is branch-port based (not `ControlEdge.condition` based).
- `for_each` and `composite` remain container-based in 6-10.
- Collapsible loop groups are a **visual layer** (UX readability), not a runtime container boundary.
- Keep DAG scheduling path intact for non-cyclic graphs; add cycle handling only for gate-controlled loops.

## Notes

- This is an architectural phase and should be implemented behind a migration-aware rollout, not as a single hard cut.
- The most failure-prone area is scheduler correctness under cycles; test coverage is mandatory before flipping defaults.
- Existing phase-3.75 loop visualization work (6-6/6-8) should be reused for edge styling and iteration cues where possible.
