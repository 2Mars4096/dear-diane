# 7-9: Async Parallel Subagents

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** completed
**Goal:** Run subagents **asynchronously** — async is the unifying primitive; it **includes** concurrency (multiple coroutines run in parallel via the event loop) and is **more flexible** than ForEach's concurrency. ForEach: list-based, same body_graph, block-until-all. 7-9: heterogeneous branches, event emission, foundation for orchestrator. Enables workflows where a coordinator spawns several sub-workflows that run simultaneously; results aggregate at fan-in; events stream for downstream "receive at any time" (10-9).

## Async as the Primitive

- **Async includes concurrency:** `async`/`await` + `asyncio.gather` run multiple coroutines concurrently. No blocking; event loop interleaves execution.
- **More flexible than ForEach:** ForEach concurrency is constrained (same body, list iteration, parent blocks until all done). 7-9's async model: heterogeneous branches, per-branch events, optional orchestrator overlay — same async primitive, broader capabilities.
- **Works with 10-9:** 7-9 provides async parallel execution; 10-9 Task 7 adds bidirectional "receive/send at any time" on top. Together they replace rigid fan-out patterns with flexible async orchestration.

## Async Guarantee

- **All subagent execution is async:** `context.run_subgraph` is async; executor uses `asyncio.gather` — no blocking waits. Each branch runs as a concurrent coroutine.
- **Non-blocking fan-out:** Launch all branches without waiting for any to complete; `asyncio.gather` collects results only when all finish (or on first failure if `failure_policy` is halt).
- **Respects global concurrency:** `EngineConfig.max_concurrency` applies across branches via existing semaphore — no unbounded parallelism.

## Context

Today:
- **ForEachNode** fans out a sub-graph over a list (`items`) with `parallelism` control — each item runs the same body_graph.
- **Topological levels** within a graph run nodes in the same level concurrently via `asyncio.gather`.
- **Composite nodes** (sub-graphs) run sequentially when invoked from their parent — no first-class "run these N subagents in parallel and join" primitive.

The gap: workflows that need *heterogeneous* parallel subagents (e.g. "run Researcher, Analyst, and Writer in parallel, then merge") require either:
- Manual fan-out wiring (multiple entry points, complex merge logic), or
- A single ForEach over a synthetic list, which is awkward when subagents have different inputs/outputs.

## Tasks

- [x] 1. **Define parallel-subagent node model**
  - [x] 1-1. Add `ParallelSubagentsNode` (new node type): `branch_graphs: list[str]` — keys into `Graph.sub_graphs`; `parallelism: int = 1` (max concurrent branches); `merge_strategy: MergeStrategy = MergeStrategy.APPEND` (reuse `dan.models.context.MergeStrategy`: APPEND, LAST_WRITE_WINS, REDUCER)
  - [x] 1-2. Input: shared input passed to all branches via `input_mappings: dict[str, str]` (outer_port → inner_entry_port); optional per-branch overrides via `branch_inputs: dict[str, dict[str, str]]` (branch_key → port overrides)
  - [x] 1-3. Output: fan-in uses `merge_strategy` — same semantics as `ForEachExecutor._merge` (APPEND → list of dicts; LAST_WRITE_WINS → dict merge; REDUCER → add optional `reducer: str` field, evaluate over `{"inputs": branch_outputs}` like ReduceNode)
  - [x] 1-4. Add `failure_policy: FailurePolicy` (align with ForEachNode) — halt-on-first-fail vs. collect-errors
  - [x] 1-5. Register in `Node` union (`models/graph.py`), `NodeTypeRegistry` (`registry.py`), `NODE_TYPES` (chat_manager)
  - [x] 1-6. Document in llm-api-guide and architecture
- [x] 2. **Executor**
  - [x] 2-1. `ParallelSubagentsExecutor` in `executors/control_flow.py`: `async def execute` — for each branch, create coroutine `context.run_subgraph(branch_key, inputs)`; launch all via `asyncio.gather(*tasks, return_exceptions=True)` so they run **concurrently** (no sequential await); apply `failure_policy`; merge outputs per `merge_strategy`
  - [x] 2-2. **Async throughout:** No `await` on individual branches before gather — all coroutines start immediately; `gather` is the only sync point. Ensures true parallel execution.
  - [x] 2-3. Respect `EngineConfig.max_concurrency` via existing `_guarded_execute_node` / global semaphore (7-1) — executor runs inside scheduler's node dispatch
  - [x] 2-4. Add `EventType` values: `PARALLEL_BRANCH_STARTED`, `PARALLEL_BRANCH_COMPLETED`, `PARALLEL_FAN_IN_COMPLETED` in `engine/events.py`; include `branch_key` (sub_graph key) in event `data` so downstream consumers (e.g. 10-9 runtime orchestrator) can route events per team
  - [x] 2-5. Register executor in scheduler's executor map: `("parallel_subagents", ParallelSubagentsExecutor())`
- [x] 3. **Checkpoint/resume**
  - [x] 3-1. In-flight parallel branches: checkpoint must capture which branches completed and which are pending; on resume, skip completed, re-dispatch pending
  - [x] 3-2. Reuse `ExecutionState.node_statuses` and `PortDataStore` — parallel node's sub-state may need extension (TBD: store per-branch status in metadata or new structure)
- [x] 4. **Builder / loader**
  - [x] 4-1. Builder DSL: `wf.parallel_subagents(node_id, ...)` with `parallel.branch(key)` context manager in `builder.py`; compiler maps to `ParallelSubagentsNode`. Sub_graphs created via nested context; `parallel.define_branch(key, graph)` for pre-built graphs.
  - [x] 4-2. Loader: markdown flow syntax `source | parallel(team_a, team_b, merge: append, parallel: 2)` in `flow_parser.py`; each branch references an agent (sub_graph).
  - [x] 4-3. Decompiler: emit `wf.parallel_subagents(...)` in builder decompiler; emit `source | parallel(...)` in loader decompiler.
- [ ] 5. **Editor** *(deferred — remaining sub-task 5-3 deferred, core palette/config done)*
  - [x] 5-1. Palette: add Parallel Subagents node type; `nodeTypes.ts` / palette config
  - [x] 5-2. Config panel: branch list (sub_graph keys), input mappings, merge strategy, parallelism, failure_policy
  - [ ] 5-3. ~~Visualization: show parallel branches and fan-in in execution~~ — **deferred** (depends on real running workflow; revisit when runtime orchestrator is live)
- [x] 6. **Tests and docs**
  - [x] 6-1. Unit: executor launches N branches, gathers results, applies merge; failure_policy behavior (10 tests)
  - [ ] 6-2. Integration: graph with parallel subagents runs correctly; checkpoint/resume — deferred until Task 3 (checkpoint) is implemented
  - [x] 6-3. Update changelog, architecture, llm-api-guide

## Sequencing

| Order | Task | Rationale |
|-------|------|------------|
| 1 | Task 1 (model) | Schema must exist before executor, builder, editor |
| 2 | Task 2 (executor) | Core runtime behavior; scheduler needs executor in registry |
| 3 | Task 3 (checkpoint) | Can defer to follow-up if MVP ships without resume support |
| 4 | Task 4 (builder/loader) | Authoring surfaces |
| 5 | Task 5 (editor) | UI for visual authoring |
| 6 | Task 6 (tests/docs) | Throughout |

## Decisions

- **New node type, not CompositeNode extension:** `CompositeNode` has a single `body_graph`. Parallel subagents need multiple `branch_graphs`. Schema is fundamentally different — new `ParallelSubagentsNode` is cleaner than overloading CompositeNode.
- **Merge strategies:** Reuse `MergeStrategy` enum (APPEND, LAST_WRITE_WINS, REDUCER) from `dan.models.context` — same as ForEachNode. Keeps reducer expression format consistent.
- **Executor does the work:** Scheduler dispatches to `ParallelSubagentsExecutor` like any other node. Executor invokes `run_subgraph` for each branch and aggregates. No scheduler changes beyond executor registration.

## Dependencies

- **7-1 (runtime reliability):** `EngineConfig.max_concurrency` and global semaphore apply to parallel branches. Executor runs inside `_execute_node` which is already wrapped by `_guarded_execute_node`.

## Primary Files

`src/dan/models/control_flow.py`, `src/dan/models/graph.py`, `src/dan/registry.py`, `src/dan/executors/control_flow.py`, `src/dan/engine/events.py`, `src/dan/engine/scheduler.py`, `src/dan/builder/builder.py`, `src/dan/builder/compiler.py`, `src/dan/loader/flow_parser.py`, `src/dan/loader/decompiler.py`, `editor/` (palette, ConfigPanel, nodeTypes).

## Notes

- ForEachNode provides list-based fan-out with concurrency. 7-9 does not replace it — ForEach remains for "run same body over list." 7-9 targets *named* parallel branches with heterogeneous inputs/outputs; async makes both work, but 7-9's model is more flexible for orchestrator-style workflows.
- **Foundation for 10-9 runtime async orchestrator:** 10-9 Task 7 (runtime async orchestrator) uses 7-9's parallel subagents as the "teams" — orchestrator receives from and sends to them at any time while they run simultaneously. 7-9's async guarantee ensures subagents truly run in parallel. Engine events (`node_output`, `node_completed`) from nodes *inside* branches also stream to the event callback; with `branch_key` in parallel-specific events and execution context (layer_path) in engine events, the orchestrator can route "receive at any time" per team.
- Overlaps with "Async loop design" backlog (orchestrator as long-running process) — 10-9 Task 7 is the concrete design; this plan provides the parallel execution primitive.
- `ReduceNode` aggregates from multiple upstream data edges; it does not run sub-graphs. Parallel subagents run sub-graphs and merge their outputs — complementary, not overlapping.
