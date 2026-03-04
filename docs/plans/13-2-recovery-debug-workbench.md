# 13-2: Recovery & Debug Workbench

**Parent:** [13-observe-recover](13-observe-recover.md)
**Status:** completed
**Goal:** Turn persisted execution data into practical debugging workflows: partial reruns from checkpoints, upstream variable visibility, and node-level test case loops.

## Existing Baseline

- **Engine checkpoints:** `FileSystemCheckpointStore` (`engine/checkpoint.py`) saves `{run_id}/checkpoint.json` with `state`, `shared_context`, `artifacts`, `local_state` after each topological level and on halt. `list_runs()` method exists but is unused.
- **Resume endpoint:** `POST /api/runs/{run_id}/resume` (`app.py:1610–1617`) calls `RunManager.resume_run()` which loads the checkpoint and restarts the scheduler from the halted point. Works for full-graph resume.
- **Chat checkpoints:** `ChatStore.save_checkpoint()` writes `{thread_id}_{msg_id}_{ts}.json` with graph snapshot. Called after mutation apply. No restore endpoint or listing UI (deferred from 12-5).
- **OutputPreview:** (`OutputPreview.tsx`) shows the selected node's JSON output during/after a run. Only *outputs* — no upstream/input view.
- **DanNode badges:** (`DanNode.tsx:315–339`) show status, duration, token count, cost on canvas nodes — live run only.
- **ConfigPanel:** Edit-only. No run-time data display (no inputs, no upstream variables, no test cases).
- **No variable inspector or node test case infrastructure exists anywhere.**

## Tasks

### Checkpoint Portals (depends on 13-1)

- [x] 1. Design checkpoint portal model and rerun contracts
  - [x] 1-1. Extend engine `CheckpointData` with `graph_revision` binding and `completed_node_ids` set so the portal knows which nodes can be skipped vs. must rerun.
  - [x] 1-2. Define allowed rerun scopes: **downstream-of-node** (rerun everything after a selected node), **single-node** (rerun one node with its last inputs), **subgraph** (rerun a composite body). Document safety constraints (e.g., side-effecting tool nodes require user confirmation).
  - [x] 1-3. Define invalidation rules: portal is stale when `graph_revision` differs from current graph (nodes/edges changed since checkpoint). Specify UX states: stale (amber warning + "re-run full"), missing artifacts (error toast), compatible (green "Resume from here").

- [x] 2. Implement checkpoint-based partial rerun execution
  - [x] 2-1. Extend `POST /api/runs/{run_id}/resume` (or add sibling `POST /api/runs/{run_id}/rerun`) to accept `scope` parameter (`downstream_of: node_id`, `single_node: node_id`, `subgraph: sub_graph_key`). Validate scope against checkpoint data.
  - [x] 2-2. In the scheduler, rehydrate `PortDataStore` and `SharedContextStore` from checkpoint, mark completed nodes as `SKIPPED` (not re-executed), and schedule only the scoped subset. Reuse existing `resume()` path where possible.
  - [x] 2-3. Tag rerun events with `provenance: {source_checkpoint_id, rerun_scope}` so results are traceable. Persist the rerun as a new `run_id` linked to the source checkpoint in `RunStore` (from 13-1).

### Variable Inspector (no 13-1 dependency — can start early)

- [x] 3. Add variable inspector in node config/debug surfaces
  - [x] 3-1. Add `computeUpstreamVariables(nodeId, graph)` utility: walk incoming edges (data + context), collect source node/port names, infer types from `output_schema`/port definitions. Return `{variable_name, source_node, source_port, type_hint, required}[]`.
  - [x] 3-2. Add "Inputs" tab or collapsible section in `ConfigPanel` (or `OutputPreview`) showing upstream variable table with source provenance, type hints, and a "likely missing" diagnostic when an expected input has no incoming edge.
  - [x] 3-3. When a recent run exists (from 13-1 `RunStore` or live `nodeOutputs`), show actual values inline as read-only JSON preview next to each variable. Source: `nodeOutputs[sourceNodeId]` for live runs, or loaded from persisted event stream for historical runs.

### Node Test Cases (no 13-1 dependency — can start early)

- [x] 4. Add node test cases and annotations
  - [x] 4-1. Define `NodeTestCase` schema: `{id, name, node_id, inputs: Record<port, value>, expected_outputs: Record<port, value> | null, assertions: string[] | null, tags: string[], notes: string}`. Persist as `test_cases/{workflow_id}/{node_id}.json` (array of test cases per node).
  - [x] 4-2. Add right-click context menu action "Add test case" on canvas nodes. Opens modal to define input fixtures and optional expected outputs. Edit/delete existing test cases from same modal. Persistence via new `POST/GET/DELETE /api/test-cases/{workflow_id}/{node_id}` endpoints.
  - [x] 4-3. Add "Run test" action per test case: executes the single node in isolation (engine schedules only that node with injected inputs). Result view shows pass/fail, output diff against expected (when expected is defined), and execution metadata (tokens, duration). Reuse `RunManager.start_run()` with a synthetic single-node graph.

### Integration with Existing UX

- [x] 5. Wire workbench features into existing surfaces
  - [x] 5-1. Add "Rerun from Here" and "Rerun This Node" context menu actions on canvas nodes (visible when a run exists). Entry point into checkpoint portal flow via `rerunFromNode` store action.
  - [x] 5-2. Add entry points from 13-1 run history panel: click a historical run → see checkpoint markers → click marker → "Rerun from here". *(implemented in Plan 20-3: CheckpointSection component, expandable per-run "▸ CP" toggle, staleness badges, inline scope picker)*
  - [x] 5-3. Add entry points from LogPanel: node group header actions — "Inspect inputs" (selects node for variable inspector), "Add test case from this run" (fires `dan:open-test-case-modal` with prefill), "Rerun from here" (calls `rerunFromNode`).
  - [x] 5-4. Add guardrails/toasts: stale checkpoint warning (409 from backend), scope validation errors (error toast on failure). Side-effecting node confirmation deferred.
  - [ ] 5-5. Ensure multi-tab consistency: test cases and checkpoint portals are workflow-scoped, not tab-scoped. Tab switching should not lose inspector state. *(deferred — requires tab state coordination)*

### Validation and Docs

- [ ] 6. Testing and documentation
  - [x] 6-1. Backend tests: checkpoint rerun with downstream/single-node/subgraph scopes, stale graph rejection, provenance tagging, single-node test execution, test case CRUD. (`tests/test_server/test_recovery_workbench.py` — 13 tests)
  - [ ] 6-2. Frontend tests: inspector rendering with various edge topologies, test case modal create/edit/delete, rerun UX states (stale/compatible/missing), empty states.
  - [x] 6-3. Update `docs/architecture.md` (checkpoint portal model, variable inspector, test case schema), `docs/changelog.md`.

## Decisions

- **Partial rerun is checkpoint-anchored**, not ad-hoc state injection. Reruns always start from a persisted checkpoint; the user picks the scope.
- **Reruns create new `run_id`s** linked to the source checkpoint — never mutate historical run data.
- **Variable inspector is read-only diagnostic UX.** Editing runtime data is out of scope; editing node config is in ConfigPanel.
- **Node test cases are workflow-local artifacts** stored alongside the graph. Cross-workflow sharing / registry is out of scope.
- **Single-node test execution reuses `RunManager`** with a synthetic one-node graph — no separate execution path to maintain.

## Notes

- Tasks 1–2 (checkpoint portals) depend on 13-1 persistence contracts for `RunStore` and `EventLog`.
- Tasks 3–4 (variable inspector, node test cases) have no 13-1 dependency and can start in parallel with 13-1 work.
- Chat checkpoint restore (deferred from 12-5) can be added as a follow-on once portal UX patterns are established here.
