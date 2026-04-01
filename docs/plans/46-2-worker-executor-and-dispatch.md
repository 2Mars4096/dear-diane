# 46-2: Worker Executor & Auto-Detection

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Build a universal `WorkerExecutor` that auto-detects execution mode from the Worker's configuration, prefers script/code paths over LLM API calls, and reuses existing executor internals.

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
Worker has validation_rules? → rule evaluation and routing
Worker has nothing?     → pass inputs through to outputs (contract-only)
```

Multiple capabilities can combine. A Worker with `code` AND `model` runs the code first, then feeds the result to the LLM. A Worker with `body_graph` AND `control_flow` is a loop.

**Dispatch priority (script-first):**
1. Code/script (cheapest, fastest, deterministic)
2. Direct tool invocation (no LLM involved)
3. LLM with tools
4. LLM only
5. Composite / orchestration
6. Control flow routing
7. Validation
8. Pass-through (no execution, just forward data)

## Tasks

- [ ] 1. Create `src/dan/worker/executor.py`
  - [ ] 1-1. `WorkerExecutor` implements `NodeExecutor` protocol
  - [ ] 1-2. `execute(node: Worker, inputs: dict, context: ExecutionContext) -> NodeResult`
- [ ] 2. Implement auto-detection
  - [ ] 2-1. `_detect_modes(worker: Worker) -> list[ExecutionMode]` — inspect config, return ordered mode list
  - [ ] 2-2. `ExecutionMode` enum: `SCRIPT`, `TOOL`, `LLM_WITH_TOOLS`, `LLM`, `COMPOSITE`, `ORCHESTRATE`, `GATE`, `VALIDATE`, `PASSTHROUGH`
  - [ ] 2-3. A Worker with no capabilities configured → `PASSTHROUGH`
  - [ ] 2-4. Combined modes resolve by priority (code first, then LLM, etc.)
- [ ] 3. Implement SCRIPT mode
  - [ ] 3-1. Reuse `CodeExecutor` internals (sandbox, multi-language)
  - [ ] 3-2. Input port data injected as variables into the script scope
  - [ ] 3-3. Script return value mapped to output ports
  - [ ] 3-4. Structured error on script failure
- [ ] 4. Implement TOOL mode (no LLM)
  - [ ] 4-1. Resolve `tool_ids` from `ToolRegistry` via `ExecutionContext`
  - [ ] 4-2. Map input port data to tool arguments
  - [ ] 4-3. Reuse `ToolExecutor` internals for invocation
  - [ ] 4-4. Tool output mapped to output ports
- [ ] 5. Implement LLM modes (LLM and LLM_WITH_TOOLS)
  - [ ] 5-1. Reuse `LLMExecutor` internals (provider dispatch, prompt assembly, output normalization)
  - [ ] 5-2. Build prompt from `persona` + `llm.prompt_template` + input port data
  - [ ] 5-3. If `llm.tools` is set: run tool-calling loop with `llm.max_tool_rounds` bound
  - [ ] 5-4. Respect `llm.temperature`, `llm.output_json_schema`, `llm.task_tier`
  - [ ] 5-5. If code ran first (combined mode): inject code output into prompt context
- [ ] 6. Implement COMPOSITE and ORCHESTRATE modes
  - [ ] 6-1. COMPOSITE (`body_graph` set): reuse `CompositeExecutor` internals, apply `input_mappings`/`output_mappings`
  - [ ] 6-2. ORCHESTRATE (`sub_workers` set): reuse `OrchestratorExecutor` internals, run teams concurrently
  - [ ] 6-3. Apply composite contract (projections, compaction, failure policy)
  - [ ] 6-4. Respect `parallelism` and `merge_strategy`
  - [ ] 6-5. Use `run_subgraph` callback from `ExecutionContext`
- [ ] 7. Implement GATE mode
  - [ ] 7-1. If `control_flow` is set: evaluate `control_flow.condition` on inputs
  - [ ] 7-2. Route to appropriate output port based on `control_flow.gate_mode`
  - [ ] 7-3. Reuse `GateExecutor` internals
  - [ ] 7-4. Support `control_flow.max_iterations` for while-mode
- [ ] 8. Implement VALIDATE mode
  - [ ] 8-1. If `validation_rules` is non-empty: run rules sequentially
  - [ ] 8-2. Route to `valid` or `invalid` output port
  - [ ] 8-3. Reuse `ValidatorExecutor` internals
- [ ] 9. Implement PASSTHROUGH mode
  - [ ] 9-1. Forward all input port data to matching output ports by name
  - [ ] 9-2. If port names don't match: forward first input to first output
  - [ ] 9-3. This is the "bare Worker" behavior — zero config, still useful
- [ ] 10. Register in `executor_defaults.py`
  - [ ] 10-1. Import `WorkerExecutor` from `dan.worker`
  - [ ] 10-2. Add `("worker", WorkerExecutor())` to defaults
  - [ ] 10-3. Existing 20 registrations remain unchanged
- [ ] 11. Tests
  - [ ] 11-1. Auto-detection: verify correct mode resolution for each config combination
  - [ ] 11-2. PASSTHROUGH: bare Worker forwards data
  - [ ] 11-3. SCRIPT: code executes, inputs → outputs
  - [ ] 11-4. TOOL: direct tool invocation, no LLM
  - [ ] 11-5. LLM: mock provider, verify prompt assembly from `persona` + `llm` hints
  - [ ] 11-6. LLM_WITH_TOOLS: mock provider + tool loop
  - [ ] 11-7. COMPOSITE: sub-graph execution with port mapping
  - [ ] 11-8. GATE: condition evaluation, if_else and while modes
  - [ ] 11-9. VALIDATE: rule evaluation, valid/invalid routing
  - [ ] 11-10. Combined: code + LLM (code runs first, output feeds LLM)
  - [ ] 11-11. Error paths: script failure, tool failure, LLM timeout
  - [ ] 11-12. Retry policy: Worker inherits `NodeBase.retry_policy`, executor respects it

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

## Notes

- The `ToolRegistry` is available through `ExecutionContext`. The executor resolves `tool_ids` against it for TOOL mode.
- For COMPOSITE/ORCHESTRATE modes, the executor needs the `run_subgraph` callback from `ExecutionContext`. This is the same callback used by `CompositeExecutor` and `OrchestratorExecutor` today.
- The `persona` field on Worker serves double duty: it's the Worker's identity label AND the default LLM system prompt (when `llm.system_prompt` is not explicitly set). This is the "it just works" behavior — set `persona` once, it applies everywhere.
