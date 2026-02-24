# 6-8: Patch-Up — Drill-In Feedback Arrows, Workflow-Node Port Cleanup, Run Readiness

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Fix gaps surfaced during manual testing of 6-6 and 6-7: loop drill-in views lack visual feedback arrows, imported workflow nodes expose too many ports from multi-entry/multi-exit graphs, composite-node runs may stall on entry-point input injection, and synthetic edges can leak into persisted graph JSON.

## Context

After implementing 6-6 (execution UX) and 6-7 (workflow as node), manual testing of the `paper_writing` workflow-as-node revealed concrete issues:

1. **Drill-in view has no loop feedback arrows** (6-6 task 1-7, 1-9 — not implemented). When drilling into a `while_loop` or `for_each`, the body sub-graph renders as a flat DAG with no visual cue that outputs loop back to inputs.

2. **Workflow-as-node port explosion**. `paper_writing` has two entry points (`check_latex_deps`, `idea_gen`) and two exit points (`check_latex_deps`, `package_submission`). The importer exposes *all* input ports from all entry nodes and all output ports from all exit nodes. This creates a cluttered node when the user only cares about `topic` in / `bundle_path` + `title` out.

3. **Run-readiness for multi-entry composites**. The `CompositeExecutor` maps incoming data to entry-point ports. But `check_latex_deps` has zero input ports — it runs independently. The engine must handle entry points that need no external input without stalling or failing validation.

4. **Synthetic edges leak on save**. Current `saveGraph()` serializes whatever React Flow edges are in the active layer — including any injected synthetic feedback edges. Without a filter, drilling into a loop body and saving would persist fake edges into `sub_graphs`.

5. **Port mapping routing bug for duplicate port names**. `derivePorts()` maps `inputMappings[outerName] = port.name`, losing the target node context. Two entry nodes that both have a port named `input` would collide in the mapping dict, silently routing data to only one of them.

## Tasks

- [ ] 1. Virtual feedback arrows in loop drill-in view
  - [ ] 1-1. In `drillIn` action (`useGraphStore.ts`), detect if the parent node is `while_loop` or `for_each`.
  - [ ] 1-2. Read the body sub-graph's `entry_points` and `exit_points` to identify feedback boundary.
  - [ ] 1-3. Inject synthetic dashed edges from exit-point output ports back to entry-point input ports (matching by port name). These are visual-only React Flow edges, not persisted to `danGraph.edges`.
  - [ ] 1-4. Tag every synthetic edge with `data: { synthetic: true }` so other code paths can identify them.
  - [ ] 1-5. Style feedback edges distinctly: dashed stroke, muted color (e.g. `#9ca3af`), optional animated dash-offset, tooltip "feedback loop".
  - [ ] 1-6. Fallback: when port names don't match between exit outputs and entry inputs, render a single generic feedback arrow from exit node to entry node with a tooltip explaining the ambiguity.
  - [ ] 1-7. On `drillOut`, discard synthetic edges (automatic — `danGraphToReactFlow` re-renders from data model which excludes them).
  - [ ] 1-8. In `saveGraph()`, filter out any edge where `edge.data?.synthetic === true` before passing to `reactFlowToDanGraph()`. This prevents accidental persistence if a user saves while drilled into a loop body.

- [ ] 2. Workflow-node port cleanup — smart port derivation
  - [ ] 2-1. In `derivePorts()` (`graphImporter.ts`), classify entry-point nodes as "externally wired" (has at least one `input_port`) vs "autonomous" (has zero `input_ports`). V1 uses only the zero-input-ports check — no deeper graph analysis of "internally satisfied" inputs, to keep the logic deterministic and simple.
  - [ ] 2-2. Only derive Composite input ports from externally-wired entry nodes. Autonomous entry nodes (like `check_latex_deps` with zero input ports) contribute no outer input ports.
  - [ ] 2-3. For exit-point output ports, keep all for now (pruning requires user annotation), but sort them so the last exit point in `exit_points` order has its ports listed first (convention: final exit = primary).
  - [ ] 2-4. Fix `inputMappings` to be node-aware: change from `inputMappings[outerName] = port.name` to `inputMappings[outerName] = `${node.id}::${port.name}`` (or equivalent structured value). Update `CompositeExecutor` to parse the `nodeId::portName` format so inputs route to the correct entry-point node, not just by port name.
  - [ ] 2-5. Apply the same node-aware fix to `outputMappings`: change from `outputMappings[port.name] = outerName` to `outputMappings[`${node.id}::${port.name}`] = outerName`. Update `CompositeExecutor` output collection to match.
  - [ ] 2-6. Update `graphAsCompositeNode()` to use the new derivation logic.
  - [ ] 2-7. Deferred: `source_node` metadata tag on derived ports for ConfigPanel display. Not in scope for 6-8 — tracked as backlog item to avoid orphaned code with no consumer.

- [ ] 3. Run-readiness for multi-entry composites
  - [ ] 3-1. In `CompositeExecutor` (`control_flow.py`), ensure entry-point nodes with zero input ports still execute (they receive no mapped inputs and run using their own defaults/code).
  - [ ] 3-2. In `Engine._inject_inputs()` (`scheduler.py`), skip entry points that have no matching input keys rather than injecting empty port data.
  - [ ] 3-3. Audit `validate_graph()` (`graph.py`): confirm `_check_required_ports` already skips entry-point nodes (it does — line 106 `if node.id in entry_set: continue`). Document this in a code comment if not already clear.
  - [ ] 3-4. Verify entry/exit bookkeeping during import: when `graphAsCompositeNode()` builds the body graph, confirm that `entry_points` and `exit_points` arrays on the namespaced body graph are correct. Add a validation assertion that every entry/exit ID exists in the body graph's node list.

- [ ] 4. Tests
  - [ ] 4-1. Frontend: drill into a `while_loop` and verify synthetic feedback edges appear with correct styling, port matching, and `data.synthetic === true` tag.
  - [ ] 4-2. Frontend: drill into a `for_each` and verify synthetic feedback edges appear (not just while_loop).
  - [ ] 4-3. Frontend: drill into a loop where port names don't match between exit outputs and entry inputs — verify the generic fallback arrow renders.
  - [ ] 4-4. Frontend: save while drilled into a loop body — verify no synthetic edges appear in the persisted `sub_graphs` JSON.
  - [ ] 4-5. Unit test `derivePorts`: given a graph with one autonomous entry (zero input ports) and one wired entry (has `topic`), only `topic` appears as an outer input port.
  - [ ] 4-6. Unit test `derivePorts`: given two entry nodes that both have an `input` port, verify mappings route to correct nodes (no collision).
  - [ ] 4-7. Unit test `graphAsCompositeNode`: verify namespaced body graph has valid entry/exit point IDs.
  - [ ] 4-8. Backend: `CompositeExecutor` runs a body graph whose entry_points include a node with zero input ports — confirm it completes without error.
  - [ ] 4-9. Backend: `Engine._inject_inputs` with inputs that only partially cover entry points — confirm non-covered entries still run.
  - [ ] 4-10. End-to-end: import `paper_writing` as a node, wire only `topic`, hit Run, and confirm both `check_latex_deps` (autonomous) and `idea_gen` (wired) start correctly.

- [ ] 5. Docs sync
  - [ ] 5-1. Update `docs/architecture.md` with feedback-arrow rendering strategy, synthetic-edge filtering in save, and port derivation rules.
  - [ ] 5-2. Update `docs/todo.md` and parent plan with 6-8 entry.
  - [ ] 5-3. Append `docs/changelog.md` after implementation.

## Decisions

- Feedback arrows are visual-only React Flow edges tagged `data.synthetic = true`; `saveGraph()` strips them before serialization.
- Autonomous entry-node classification uses **zero input ports** only (no deeper "internally satisfied" analysis) — deterministic and simple.
- Port mappings are node-aware (`nodeId::portName`) to prevent routing collisions when multiple entry/exit nodes share port names.
- Exit-point ports are all exposed for now; selective hiding is deferred to a future "port visibility" feature.
- `source_node` metadata on derived ports is deferred to backlog — no UI consumer exists yet.
- Multi-entry composites must tolerate partial input injection without validation failures.

## Notes

- These are targeted fixes for issues found in real usage, not new features.
- Task 1 closes 6-6 tasks 1-7 and 1-9 which were deferred during implementation.
- Task 2 directly addresses the `paper_writing` "port explosion" visible in the user's screenshot.
- Task 3 is a runtime correctness fix — without it, workflow-as-node cannot actually execute for multi-entry graphs like `paper_writing`.
- The save-leak fix (1-8) is critical — without it, drilling into a loop and hitting Cmd+S would corrupt the graph JSON with phantom edges.
- The node-aware mapping fix (2-4, 2-5) is a correctness fix for any workflow with duplicate port names across entry/exit nodes. The existing `paper_writing` graph has this pattern (`check_latex_deps` and `package_submission` share output port names like `deps_ok`).
