# 2: Phase 1 Orchestration Engine

**Status:** completed
**Goal:** Build the async Python execution engine on top of Phase 0 types — schedule and execute graphs with typed nodes, typed edges, while-loops, fan-out/fan-in, checkpointing/resumability.

## Tasks
- [x] 1. Project setup
  - [x] 1-1. Add `openai` dependency to `pyproject.toml`
  - [x] 1-2. Create `.env` / `.env.example`
  - [x] 1-3. Create `engine/` and `executors/` package structure
- [x] 2. Runtime state management (`engine/state.py`, `engine/context_runtime.py`)
  - [x] 2-1. `NodeStatus` enum, `PortDataStore`, `ExecutionState`
  - [x] 2-2. `SharedContextStore` runtime (Layer 3)
  - [x] 2-3. `ArtifactStore` runtime (Layer 4)
  - [x] 2-4. `LocalStateManager` runtime (Layer 2)
- [x] 3. Executor protocol and registry (`engine/executor.py`)
  - [x] 3-1. `NodeExecutor` protocol
  - [x] 3-2. `ExecutionContext` wrapper
  - [x] 3-3. `NodeResult` dataclass
  - [x] 3-4. `ExecutorRegistry`
- [x] 4. Safe expression evaluator (`engine/conditions.py`)
  - [x] 4-1. `evaluate_condition()` with restricted namespace
- [x] 5. Output normalization (`engine/normalizer.py`)
  - [x] 5-1. JSON extraction from LLM text
  - [x] 5-2. Schema validation at runtime
  - [x] 5-3. Re-prompt message builder
  - [x] 5-4. `OutputNormalizer` pipeline
- [x] 6. Operator executors (`executors/llm.py`, `tool.py`, `code.py`)
  - [x] 6-1. `LLMExecutor` — OpenAI SDK, prompt rendering, normalization, retry
  - [x] 6-2. `ToolExecutor` — function registry dispatch
  - [x] 6-3. `CodeExecutor` — sandboxed Python exec
- [x] 7. Control-flow executors (`executors/control_flow.py`)
  - [x] 7-1. `IfElseExecutor`
  - [x] 7-2. `WhileLoopExecutor`
  - [x] 7-3. `ForEachExecutor`
  - [x] 7-4. `ReduceExecutor`
  - [x] 7-5. `RouterExecutor`
  - [x] 7-6. `HumanInTheLoopExecutor`
- [x] 8. Graph scheduler (`engine/scheduler.py`)
  - [x] 8-1. Topological sort with parallel-level detection
  - [x] 8-2. Edge resolver
  - [x] 8-3. Ready-queue dispatch loop (asyncio)
  - [x] 8-4. Sub-graph execution (recursive)
  - [x] 8-5. `Engine.run()` public API
- [x] 9. Checkpointing (`engine/checkpoint.py`)
  - [x] 9-1. `CheckpointStore` protocol
  - [x] 9-2. `FileSystemCheckpointStore`
  - [x] 9-3. `Engine.resume()` API
  - [ ] 9-4. Mid-loop checkpoint support (deferred — WhileLoop checkpoints at loop boundaries, not mid-iteration)
- [x] 10. Tests
  - [x] 10-1. Unit tests: state stores, expression evaluator, normalizer
  - [x] 10-2. Unit tests: executor mocks
  - [x] 10-3. Integration: linear chain, IfElse branching
  - [x] 10-4. Integration: WhileLoop, ForEach fan-out/fan-in
  - [ ] 10-5. Integration: paper-writing workflow (mock LLM) — deferred to Phase 3
  - [x] 10-6. Integration: checkpoint and resume
- [x] 11. Sync tracking docs

## Decisions
- Async-first engine (asyncio). All executors are async.
- Abstract `NodeExecutor` protocol + concrete `ExecutorRegistry` dispatch by `node_type`.
- Default LLM executor uses OpenAI SDK with `base_url=https://api.vectorengine.ai/v1`, model `claude-sonnet-4-6`.
- Restricted Python `eval()` for IfElse/WhileLoop condition expressions.
- Callback-based HumanInTheLoop: engine calls user-provided `async Callable`.
- Filesystem-based default checkpointing, pluggable via `CheckpointStore` protocol.

## Notes
- Engine treats untyped-schema validation messages as non-fatal warnings (design-time hints, not runtime blockers).
- Entry-point nodes receive injected inputs via virtual `__input__<node_id>` source in PortDataStore.
- Mid-loop checkpointing (9-4) deferred: WhileLoop checkpoints between iterations at the scheduler level. Full mid-iteration resume would require saving the loop executor's internal state, which adds complexity for marginal benefit. Can revisit if long-running loop iterations become a problem.
- Paper-writing integration test (10-5) deferred to Phase 3 when the full workflow is built end-to-end.
