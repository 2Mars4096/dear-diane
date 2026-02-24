# Architecture

## Tech Stack

- **Core engine:** Python
- **Visual editor/UI:** TypeScript + React + React Flow
- **Schema validation:** JSON Schema (typed edges)
- **Shared contract:** versioned graph JSON (`dan_graph_v1`) between Python and TypeScript

## Directory Structure

```
deep-agent-network/
  docs/                          # All project tracking and documentation
    development-plan.md          # Vision, roadmap, research landscape
    architecture.md              # This file — tech stack, conventions, decisions
    changelog.md                 # Append-only log of completed work
    todo.md                      # High-level task list, links to plan files
    bugs.md                      # Known issues and failed approaches
    plans/                       # Numbered detailed plans (just-in-time)
  src/dan/                       # Python package
    models/                      # Phase 0 — formal type system
      ports.py                   # InputPort, OutputPort
      context.py                 # NodeLocalState, SharedContextDeclaration, ArtifactRef, ContextProjection, policies
      nodes.py                   # NodeBase, LLMOperator, ToolOperator, CodeOperator
      control_flow.py            # IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop, CompositeNode
      edges.py                   # DataEdge, ControlEdge, ContextEdge
      graph.py                   # Graph container, Node/Edge discriminated unions, dan_graph_v1 contract
    validation/
      schema.py                  # Port schema compatibility (MVP structural check)
      graph.py                   # Graph well-formedness validation
    registry.py                  # NodeTypeRegistry — maps node_type strings to classes
    engine/                      # Phase 1 — async execution engine
      __init__.py                # Public API: Engine, EngineConfig, RunResult, etc.
      state.py                   # NodeStatus, PortDataStore, ExecutionState
      context_runtime.py         # SharedContextStore, ArtifactStore, LocalStateManager (Layers 2-4)
      executor.py                # EngineConfig, NodeExecutor protocol, ExecutionContext, ExecutorRegistry
      conditions.py              # Safe expression evaluator for IfElse/WhileLoop conditions
      normalizer.py              # OutputNormalizer — JSON extraction, schema validation, re-prompt
      checkpoint.py              # CheckpointStore protocol, FileSystemCheckpointStore
      scheduler.py               # Topological sort, parallel dispatch, Engine.run()/resume()
    executors/                   # Phase 1 — built-in node executors
      llm.py                     # LLMExecutor — OpenAI-compatible (vectorengine.ai default)
      tool.py                    # ToolExecutor + ToolRegistry — function dispatch
      code.py                    # CodeExecutor — sandboxed Python exec
      control_flow.py            # IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop
  tests/                         # pytest suite (140 tests)
    test_models/                 # Unit tests for all model types
    test_validation/             # Validation logic tests
    test_examples/               # Paper-writing motivating example
    test_engine/                 # Engine unit + integration tests
  pyproject.toml                 # Pydantic v2 + OpenAI SDK + pytest
  .env.example                   # Environment variable template
  .cursor/rules/                 # AI agent rules
```

## Core Abstractions

### Two-Level Node Model (inspired by AFlow)

- **Operator (atomic):** Single LLM call, API call, code execution, database query, or conditional. The fundamental unit. Each operator independently specifies its model.
- **Agent (composite):** A group of operators wired into a sub-graph that behaves as a single unit with a defined interface (input schema → output schema). Inspectable — double-click to zoom into internal graph.

### Typed Edges (inspired by supply chain management)

| Edge Type | Purpose |
|-----------|---------|
| **Data edge** | Structured output of node A feeds node B. Validated with JSON Schema at design time. |
| **Control edge** | Conditional routing (if/else), loops (for-each, while), parallel fan-out/fan-in, retry logic. |
| **Context edge** | Shared memory or state (conversation history, accumulated knowledge, file system) readable/writable by multiple agents. |

### Control-Flow Primitives

| Primitive | Behavior |
|-----------|----------|
| If/Else | Route based on condition evaluated on upstream data |
| While Loop | Repeat until condition met or max iterations reached |
| For-Each / Map | Fan-out: apply sub-graph to each item in a list, in parallel |
| Reduce | Fan-in: aggregate results from parallel branches |
| Router | LLM-powered routing — model decides which branch |
| Human-in-the-Loop | Pause execution, wait for human input, resume |

### Model Heterogeneity

Each operator node independently specifies its model. Cheap/fast for classification, strong for reasoning, code-specialized for generation. First-class design principle, not afterthought.

### Output Normalization (built-in)

Like batch normalization in DNNs, every LLM operator has a deterministic, built-in output normalization layer: parse → validate against output schema → re-prompt with error on failure → retry up to N times. This is automatic (not a user-visible node) and guarantees every data edge carries schema-valid data or an explicit error.

### Error Handling / Retry Policy

Every operator carries a `retry_policy`: `max_retries`, `backoff`, `fallback_model`, `on_failure` (error / skip / halt). Separate from output normalization — this handles call-level failures (rate limits, timeouts, network errors).

### Checkpointing / Resumability

After each node completes, the engine persists output, artifact state, context store snapshot, and execution pointer. On restart, resumes from the last completed node. Targeted for Phase 1 (core engine).

### Four-Layer Context Model

Direct edge data handles simple input/output. Growing payloads, shared state, and dynamic updates are handled by four distinct layers:

| Layer | What It Holds | Scope | Mutability |
|-------|--------------|-------|------------|
| **1. Edge Data** | Typed, bounded payloads on data edges | Between two nodes | Immutable per edge |
| **2. Node-Local State** | Private working memory (iteration history, convergence metrics) | Scoped to a composite agent / loop | Mutable within scope, invisible to parent |
| **3. Shared Context Store** | Namespaced key-value blackboard (`context.outline`, `context.bibliography`) | Graph-wide, opt-in via declared `read_set` / `write_set` | Mutable; write modes: `write`, `append` |
| **4. Artifact Store** | Large objects (drafts, datasets, figures) stored by reference | Graph-wide | Immutable (new version per revision) |

### Context Projection

At every scope boundary (entering a sub-graph, entering a loop iteration), a **projection function** extracts only what the next consumer needs. Each consumer gets a minimal view — the loop controller sees only iteration count + convergence metrics, the reviser sees only current draft + latest comments, the parent graph sees only the final output.

### Composite Node Contract

Every composite/loop node declares:
- `external_input_schema` / `external_output_schema` — what the parent sees
- `control_state` — iteration count, stop flags, thresholds (loop controller only)
- `local_working_set` — latest working data, not full history
- `read_set` / `write_set` — declared dependencies on shared context store
- `compaction_rule` — how local history is summarized between iterations

### Context Policies

- **Mutation**: nodes read shared context by default; writes require declaration
- **Parallel merge**: fan-out branches must specify merge rules (append, last-write-wins, or reducer node)
- **Compaction**: configurable per composite node (sliding window, summarization gate, diff-based)
- **Failure exits**: `max_iterations`, `stagnation`, `timeout`

## Execution Engine (Phase 1)

### Engine API

```python
from dan.engine import Engine, EngineConfig

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",
    llm_api_key="...",
    llm_default_model="claude-sonnet-4-6",
)
engine = Engine(config)
result = await engine.run(graph, inputs={"idea": "..."})
result = await engine.resume(graph, run_id="abc123")
```

### Scheduling

- Async-first: `Engine.run()` is async; parallel fan-out uses `asyncio.gather()`
- Kahn's algorithm groups nodes into topological levels; nodes in the same level execute concurrently
- Sub-graph execution is recursive: WhileLoop/ForEach/Composite executors call back into the scheduler

### Executor Protocol

- `NodeExecutor` is a `Protocol` with `async execute(node, inputs, context) -> NodeResult`
- `ExecutorRegistry` maps `node_type` strings to executor instances; users can register custom executors
- Built-in executors for all 10 node types auto-registered on Engine creation

### LLM Integration

- Default executor uses `openai.AsyncOpenAI(base_url=...)` — supports any OpenAI-compatible endpoint
- Output normalization built into LLM executor: extract JSON -> validate against schema -> re-prompt with error -> retry
- Transient API errors (rate limits, timeouts) retried with exponential backoff

### Condition Evaluation

- IfElse/WhileLoop `condition` strings evaluated as Python expressions via restricted `eval()`
- No `__builtins__`; whitelist of safe functions (len, min, max, all, any, etc.)
- Variables populated from upstream port data

### Checkpointing

- `CheckpointStore` protocol with filesystem default (`FileSystemCheckpointStore`)
- Checkpoint written after each topological level completes
- `Engine.resume()` loads checkpoint and continues from pending nodes

## Key Decisions

- **Build, don't buy.** Existing tools (Langflow, Flowise, Dify) cannot handle while-loops, composable sub-graphs, or typed edges natively. See development-plan.md sections 3-4 for full analysis.
- **Language split.** Python for orchestration runtime and validation; TypeScript for the visual editor and interaction layer.
- **Roadmap resequencing.** Build the core engine first, then immediately build a full visual editor baseline to test the system early via UI.
- **Hierarchical plan numbering.** Plan files use hierarchical numbering (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree.
