# 46-2: Worker Executor & Auto-Detection

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
**Goal:** Build a universal `WorkerExecutor` for Worker-native compute behaviors that auto-detects execution mode from the Worker's configuration, prefers script/code paths over LLM API calls, and reuses existing executor internals, while allowing specialized control/runtime executors to remain first-class where that is clearer and more stable.

## Design: Auto-Detection, Not Mode Flags

The Worker never declares "I am an LLM node" or "I am a tool node." The executor looks at what's configured and figures it out:

```
Worker has code?        → run script
Worker has tool_ids?    → invoke tools directly (no LLM)
Worker has model + tools? → LLM with tool-calling loop
Worker has model?       → pure LLM completion
Worker has body_graph?  → composite sub-graph execution
Worker has sub_workers? → multi-team orchestration
Worker has control_flow? → condition evaluation and routing
Worker has validator metadata? → rule evaluation and routing
Worker has nothing?     → pass inputs through to outputs (contract-only)
```

Multiple capabilities can combine. A Worker with `code` AND `model` runs the code first, then feeds the result to the LLM.

This plan should be read carefully: `WorkerExecutor` is the primary dispatch for Worker-native compute composition. It does **not** have to subsume every existing scheduler/control primitive. For branch/loop/team semantics, delegation to specialized executors is acceptable and may remain the canonical runtime path.

**Dispatch priority (script-first):**
1. Code/script (cheapest, fastest, deterministic)
2. Direct tool invocation (no LLM involved)
3. LLM with tools
4. LLM only
5. Composite / orchestration
6. Control flow routing
7. Validation
8. Pass-through (no execution, just forward data)

## Design: Async Runtime, Blocking, and Locks

The execution model must be explicit:

- The engine/scheduler remains **async**.
- Workers become runnable when their required upstream dependencies are satisfied.
- Workers may execute **concurrently** when dependencies allow and no resource locks conflict.
- Sequential behavior comes from graph/control dependencies, not from “worker-ness” itself.
- Additional serialization comes from explicit named locks or blocking policies.
- The tiered linter sits on the **handoff publication path**: downstream workers do not see an output until linting/fixes have completed and any required locks are released.

## Current State

- `WorkerExecutor` is live for pass-through, code, direct tool, LLM, `body_graph`, `sub_workers`, Worker-shaped `control_flow`, and the bridged specialized surfaces (`router`, `validator`, `reflection`, `rag`, `human`, `vote`).
- The first-class Worker composition surface is now exercised end-to-end: `body_graph` execution honors `input_mappings` / `output_mappings`, `sub_workers` honor Worker `parallelism` / `merge_strategy`, and delegation can be bounded by `spawn_policy.max_spawns_per_node`.
- Ordered `ExecutionMode` detection is live, including the conservative staged `code -> llm/tool` path and model-less LLM execution when `llm_hints` plus shared defaults imply LLM mode.
- Shared instruction, memory-policy, provider, retry, toolset, and authority refs are resolved at execution time from `graph.worker_resources`, and authority/lock checks are enforced before privileged tool use, memory writes, or delegation.
- The hot simple-compute path is now lighter too: no-shared-resource Workers use a cached effective-config fast path, static LLM Workers reuse a fully resolved legacy `LLMOperator` template instead of rebuilding it per call, and the code/tool/llm/specialized compatibility projections now reuse cached legacy templates instead of rebuilding full Pydantic node models on every run.
- `WorkerExecutor` no longer hard-pulls the LLM/OpenAI stack just to import the Worker runtime surface. `LLMExecutor` is now loaded lazily when no explicit LLM executor is injected, which keeps code/tool-only Worker paths more portable and easier to test in isolation.
- The executor test matrix now covers graph-default resolution and `inherit_defaults` behavior, direct resolution of default/bundle/ref memory policies, real Worker `LLM_WITH_TOOLS` tool-loop execution through `LLMExecutor`, first-class validator rules, `body_graph` input/output mappings, `sub_workers` merge behavior, and failure propagation for script, direct-tool, and LLM timeout paths.
- A deterministic dispatch benchmark now exists at `tests/eval/worker_dispatch_benchmark.py` and currently passes the strict local `--max-overhead-ratio 1.25` gate for `code_only`, `tool_only`, `llm_only`, `code -> tool -> llm`, `body_graph` input/output mapping, and sub-worker merge/orchestration cases. The harness now measures legacy and Worker runs in an interleaved pairwise order so dispatch ratios are not biased by two separate timing phases inside one Python process.
- When both `body_graph` and `sub_workers` are present, the current runtime treats `body_graph` as the executable surface; named `sub_workers` are composition assets for that surface rather than an automatic second execution phase.
- The remaining out-of-band compute registration seams are gone too. Default engine wiring, the local client, and the server run-manager now all feed bridged compute node kinds through the same Worker-backed registry path, with custom tool registries injected into `register_default_executors(..., tool_registry=...)` instead of bypassing Worker compaction with ad hoc `tool_operator` registrations.

## Tasks

- [x] 1. Create `src/dan/worker/executor.py`
  - [x] 1-1. `WorkerExecutor` implements `NodeExecutor` protocol
  - [x] 1-2. `execute(node: Worker, inputs: dict, context: ExecutionContext) -> NodeResult`
- [x] 2. Implement auto-detection
  - [x] 2-1. `_detect_modes(worker: Worker) -> list[ExecutionMode]` — inspect config, return ordered mode list
  - [x] 2-2. `ExecutionMode` enum: `SCRIPT`, `TOOL`, `LLM_WITH_TOOLS`, `LLM`, `COMPOSITE`, `ORCHESTRATE`, `GATE`, `VALIDATE`, `PASSTHROUGH`
  - [x] 2-3. A Worker with no capabilities configured → `PASSTHROUGH`
  - [x] 2-4. Combined modes resolve by priority (code first, then LLM, etc.)
- [x] 3. Implement SCRIPT mode
  - [x] 3-1. Reuse `CodeExecutor` internals (sandbox, multi-language)
  - [x] 3-2. Input port data injected as variables into the script scope
  - [x] 3-3. Script return value mapped to output ports
  - [x] 3-4. Structured error on script failure
- [x] 4. Implement TOOL mode (no LLM)
  - [x] 4-1. Resolve `tool_ids` from `ToolRegistry` via `ExecutionContext`
  - [x] 4-2. Map input port data to tool arguments
  - [x] 4-3. Reuse `ToolExecutor` internals for invocation
  - [x] 4-4. Tool output mapped to output ports
- [x] 5. Implement LLM modes (LLM and LLM_WITH_TOOLS)
  - [x] 5-1. Reuse `LLMExecutor` internals (provider dispatch, prompt assembly, output normalization)
  - [x] 5-2. Build prompt from resolved instruction/profile context + `llm.prompt_template` + input port data
  - [x] 5-3. If `llm.tools` is set: run tool-calling loop with `llm.max_tool_rounds` bound
  - [x] 5-4. Respect `llm.temperature`, `llm.output_json_schema`, `llm.task_tier`
  - [x] 5-5. If code ran first (combined mode): inject code output into prompt context
  - [x] 5-6. Resolve referenced instruction profiles, context bundles, provider policies, and retry defaults through `ExecutionContext` before execution, without copying those heavy structures into the Worker model
- [x] 6. Implement COMPOSITE and ORCHESTRATE modes
  - [x] 6-1. COMPOSITE (`body_graph` set): reuse `CompositeExecutor` internals, apply `input_mappings`/`output_mappings`
  - [x] 6-2. ORCHESTRATE (`sub_workers` set): reuse `OrchestratorExecutor` internals, run teams concurrently
  - [x] 6-3. Apply composite contract (projections, compaction, failure policy)
  - [x] 6-4. Respect `parallelism` and `merge_strategy`
  - [x] 6-5. Use `run_subgraph` callback from `ExecutionContext`
  - [x] 6-6. Enforce authority caps before delegation/spawn: Worker cannot launch child work above its tier/authority policy
- [x] 7. Implement GATE mode
  - [x] 7-1. If `control_flow` is set: evaluate `control_flow.condition` on inputs where Worker-native gating is appropriate
  - [x] 7-2. Route to appropriate output port based on `control_flow.gate_mode`
  - [x] 7-3. Reuse `GateExecutor` internals or delegate directly to retained specialized control executors
  - [x] 7-4. Support `control_flow.max_iterations` for Worker-native while-style coordination, but do not force specialized loop executors to disappear
- [x] 8. Implement VALIDATE mode
  - [x] 8-1. If validator-style metadata/config is present: run rules sequentially or delegate to the retained validator executor
  - [x] 8-2. Route to `valid` or `invalid` output port
  - [x] 8-3. Reuse `ValidatorExecutor` internals
- [x] 9. Implement PASSTHROUGH mode
  - [x] 9-1. Forward all input port data to matching output ports by name
  - [x] 9-2. If port names don't match: forward first input to first output
  - [x] 9-3. This is the "bare Worker" behavior — zero config, still useful
- [x] 10. Register in `executor_defaults.py`
  - [x] 10-1. Import `WorkerExecutor` from `dan.worker`
  - [x] 10-2. Add `("worker", WorkerExecutor())` to defaults
  - [x] 10-3. Existing specialized control/runtime registrations remain valid until there is a clear reason to collapse them
- [x] 11. Tests
  - [x] 11-1. Auto-detection: verify correct mode resolution for each config combination
  - [x] 11-2. PASSTHROUGH: bare Worker forwards data
  - [x] 11-3. SCRIPT: code executes, inputs → outputs
  - [x] 11-4. TOOL: direct tool invocation, no LLM
  - [x] 11-5. LLM: mock provider, verify prompt assembly from `persona` + `llm` hints
  - [x] 11-6. LLM_WITH_TOOLS: mock provider + tool loop
  - [x] 11-7. COMPOSITE: sub-graph execution with port mapping
  - [x] 11-8. GATE: condition evaluation, if_else and while modes
  - [x] 11-9. VALIDATE: rule evaluation, valid/invalid routing
  - [x] 11-10. Combined: code + LLM (code runs first, output feeds LLM)
  - [x] 11-11. Error paths: script failure, tool failure, LLM timeout
  - [x] 11-12. Retry policy: Worker inherits `NodeBase.retry_policy`, executor respects it
  - [x] 11-13. Reference resolution: instruction/memory/context/provider/retry refs are resolved from shared runtime registries
  - [x] 11-14. Authority enforcement: Worker cannot exceed spawn/task-tier caps
  - [x] 11-15. Lock semantics: workers with conflicting `resource_locks` serialize, while unrelated workers still run concurrently

## Likely Files

**New:**
- `src/dan/worker/executor.py`

**Modified:**
- `src/dan/executor_defaults.py` — add WorkerExecutor registration

**Test:**
- `tests/test_worker/test_executor.py` (new)

## Decisions

- The `WorkerExecutor` **delegates**, it does not reimplement. It holds internal references to `CodeExecutor`, `LLMExecutor`, `ToolExecutor`, `GateExecutor`, `CompositeExecutor`, `ValidatorExecutor` and calls their core logic. Zero duplication.
- PASSTHROUGH mode is intentional and useful. A Worker that just forwards data acts as a named checkpoint, a contract boundary, or a future expansion point. "It just works" means even doing nothing is valid.
- Combined modes follow priority ordering. Code always runs first if set. Its output is available to the LLM prompt (if model is also set) or to the composite sub-graph (if body_graph is also set). This enables hybrid deterministic+LLM execution within a single node.
- Specialized control semantics are allowed to stay specialized. If `while_loop`, `goal_loop`, `if_else`, or team-turn protocol behavior is clearer through dedicated executors, `WorkerExecutor` should delegate rather than pretend to reimplement them generically.
- `WorkerExecutor` resolves shared references; it does not own the underlying heavy systems. Tools, memory contents, long instruction packs, and provider defaults remain external.
- Blocking semantics must stay visible: edges/control-flow determine readiness, locks determine serialization, and lint determines handoff publication.

## Notes

- `ToolRegistry` still comes through `ExecutionContext`; Worker only carries tool ids and toolset refs, not embedded tool definitions.
- `run_subgraph` remains the honest composite/orchestration seam. Worker uses it for `body_graph` and named `sub_workers` instead of inventing a second runtime substrate.
- The remaining executor-unification tail is follow-up work, not missing scope for this plan. Heavier orchestration/composite families can keep using compatibility delegation until there is a concrete reason to collapse more of that behavior into a cleaner Worker-native runtime.
- The clean boundary is unchanged: Worker-native compute and contract handling live here; branch/loop/team scheduler semantics may still live in specialized runtime code even when authoring surfaces present them cohesively.
- Review follow-up hardening is also in place: the old `id(node)` cache aliasing bug is fixed with stable Worker fingerprints, and code/tool-only imports no longer need the optional LLM dependency chain unless a default `LLMExecutor` is actually requested.
- Detailed per-slice runtime history now lives in `docs/changelog.md`; this plan tracks the remaining executor-unification tail, not every landed dispatch improvement.
