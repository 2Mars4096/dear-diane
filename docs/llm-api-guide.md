# DAN API Guide (for LLM Callers)

> **Auto-update rule:** This file must be updated whenever nodes, edges, builder methods, engine APIs, executors, or examples change. See `docs/` tracking rules.

## Quick Reference

```python
from dan.builder import workflow, decompile
from dan.engine import Engine, EngineConfig, RunResult
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import MergeStrategy, CompactionStrategy, CompactionRule, FailurePolicy
```

---

## 1. Core Concepts

**Graph** — A directed acyclic graph (with loops expressed as composite nodes) of typed nodes connected by typed edges. Serialized as `dan_graph_v1` JSON.

**Node** — An operator (atomic unit of work) or a composite (sub-graph that behaves as a single node). 15 node types total.

**Edge** — A typed connection between node ports. Three types: data, control, context.

**Port** — Named input/output slots on nodes. Every edge connects a source output port to a target input port.

**Engine** — Async execution runtime. Topological scheduling, parallel fan-out, checkpointing.

**Builder DSL** — Fluent Python API for constructing graphs programmatically.

**InputNode:** Not created via `wf.input()` — `wf.input` is a *property* (returns `PortRef` for sub-graph entry). InputNode is created by the loader (markdown compile) or scoped_run; the builder DSL does not expose a node-creation method for it.

---

## 2. Builder DSL — Creating Workflows

### Initialize a Workflow

```python
from dan.builder import workflow

wf = workflow(
    "my_workflow",              # graph name (required)
    description="What it does", # optional
    tags=["demo", "research"],  # optional
)
```

### Build and Serialize

```python
graph = wf.build()                          # -> Graph (validated Pydantic model)
json_str = wf.to_json(indent=2)             # -> JSON string
d = wf.to_dict()                            # -> dict
code_str = decompile(graph)                 # -> executable Python that reconstructs the graph
```

---

## 3. Node Types

### 3a. LLM Operator

Calls an LLM. The workhorse node.

```python
node = wf.llm(
    "node_id",                              # unique ID (required)
    prompt="Generate ideas about {topic}",  # prompt template with {variable} placeholders
    model="claude-sonnet-4-6",              # model override (optional, uses engine default if empty)
    system_prompt="You are a researcher.",   # system message (optional)
    temperature=0.7,                        # sampling temperature (default: 0.7)
    max_tokens=4096,                        # max output tokens (optional)
    output_schema={                          # JSON Schema for structured output (optional)
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "sections"],
    },
    name="Idea Generator",                  # display name (optional, defaults to node_id)
    description="Generates research ideas", # description (optional)
    input_ports=[{"name": "topic"}],        # explicit input ports (optional, auto-inferred from prompt)
    output_ports=[{"name": "text"}],        # explicit output ports (optional, default: [text])
    # Phase 10 token-optimization controls (all optional):
    target_input_tokens=6000,               # advisory input budget (never hard-enforced)
    summarize_inputs=True,                  # summarize long free-text inputs before injection
    prune_fields=["*.internal_id"],         # prune irrelevant structured fields
    input_format="compact",                 # json | yaml | compact
    jit_tool_loading=True,                  # inject tool catalog; load full schemas on demand
    agent_context_tools=True,               # inject search_context/read_context/read_state/list_available_context
    semantic_cache=True,                    # semantic response cache (deterministic prompts)
    history_policy={                         # conversation-history assembly policy
        "inline_recent": 8,
        "summarize_older": True,
        "keep_system": True,
    },
    memoize=True,                           # node-level deterministic memoization
    cache_ttl=3600,                         # per-node cache TTL in seconds
)
```

**Default output port:** `text` (or structured JSON fields if `output_schema` is set).

**Output normalization:** When `output_schema` is provided, the engine automatically: parses LLM output as JSON -> validates against schema -> re-prompts with error on failure -> retries up to `output_norm_max_retries` (default 3).

### 3b. Code Operator

Executes sandboxed Python code. The code string must set a `result` variable.

```python
node = wf.code(
    "transform",
    code="""\
total = sum(items)
result = {"total": total, "count": len(items)}
""",
    language="python",                      # default: "python"
    input_ports=[{"name": "items"}],        # variables available in code scope
    output_ports=[{"name": "total"}, {"name": "count"}],
)
```

**Default output port:** `result`.

**Scope:** Input port values are injected as local variables. The code must set `result` (dict for multi-port output, or scalar for single `result` port).

**Port defaults:** Optional input ports (`required=False`) with a `json_schema` type receive type-appropriate defaults when not wired: `array` → `[]`, `object` → `{}`, `number`/`integer` → `0`, `string` → `""`, `boolean` → `False`. An `inputs` dict is also injected so code can use `inputs.get("field", fallback)`.

### 3c. Tool Operator

Dispatches to a registered async function via `ToolRegistry`.

```python
node = wf.tool(
    "save_paper",
    tool_id="save_paper",                   # must match ToolRegistry.register() key
    tool_config={"format": "markdown"},     # static args merged with input port values
    input_ports=[{"name": "content"}, {"name": "title"}],
    output_ports=[{"name": "saved_path"}, {"name": "title"}],
)
```

**Default output port:** `result`.

**Tool functions** must be `async def` and return a `dict` (keys become output port values) or a scalar (mapped to `result` port).

### 3d. IfElse (deprecated — use `gate`)

Conditional routing. Evaluates a Python expression against upstream data. **Deprecated:** use `wf.gate(..., gate_mode="if_else", condition="...")` instead.

```python
node = wf.if_else(
    "check_quality",
    condition="score >= 0.8",               # Python expression evaluated against input port values
)
```

**Default output port:** `branch`. Outputs `{"branch": "true"}` or `{"branch": "false"}`.

**Safe eval:** No `__builtins__`; only whitelisted functions (len, min, max, all, any, str, int, float, bool, abs, round, sorted, list, dict, set, tuple).

### 3e. WhileLoop (deprecated — use `gate`)

Iterative loop with a sub-graph body. **Deprecated:** use `wf.gate(..., gate_mode="while", condition="...", ...)` instead. See [Sub-Graph Context Managers](#5-sub-graph-context-managers).

### 3f. ForEach

Parallel fan-out over a list. See [Sub-Graph Context Managers](#5-sub-graph-context-managers).

### 3g. Composite

Group of nodes as a reusable sub-graph. See [Sub-Graph Context Managers](#5-sub-graph-context-managers).

### 3h. Reduce

Fan-in aggregation. Collects results from parallel branches.

```python
node = wf.reduce(
    "aggregate",
    reducer="concat",                       # reducer strategy identifier
)
```

**Default output port:** `result`.

### 3i. Router

LLM-powered routing. The model decides which branch to take.

```python
node = wf.router(
    "route_task",
    model="gpt-4o-mini",
    route_descriptions={
        "research": "Task requires literature search",
        "writing": "Task requires content generation",
        "analysis": "Task requires data analysis",
    },
)
```

**Default output port:** `route`.

### 3j. Human-in-the-Loop

Pauses execution for human input.

```python
node = wf.human_in_the_loop(
    "approve_draft",
    prompt="Review and approve this draft:",
    timeout_seconds=3600,                   # optional timeout
    default_action="approve",               # fallback on timeout (optional)
)
```

**Default output port:** `response`.

### 3k. Parallel Subagents

Runs multiple heterogeneous sub-graphs concurrently and merges results at fan-in. Unlike ForEach (same body over a list), each branch is a distinct sub-graph with its own logic. See [Sub-Graph Context Managers](#5-sub-graph-context-managers) for builder syntax.

| Config Field | Type | Default | Purpose |
|---|---|---|---|
| `branch_graphs` | `list[str]` | *(required)* | Keys into `Graph.sub_graphs`; each runs concurrently |
| `parallelism` | `int` | `1` | Max concurrent branches |
| `merge_strategy` | `MergeStrategy` | `APPEND` | How to combine branch outputs: `APPEND` (list), `LAST_WRITE_WINS` (dict merge), `REDUCER` (expression) |
| `reducer` | `str \| None` | `None` | Expression over `{"inputs": branch_outputs}` when `merge_strategy=REDUCER` |
| `input_mappings` | `dict[str, str]` | `{}` | outer_port → inner_entry_port (shared input to all branches) |
| `branch_inputs` | `dict[str, dict]` | `{}` | branch_key → {port: value} per-branch overrides |
| `failure_policy` | `FailurePolicy` | `FailurePolicy()` | Timeout, max_iterations, stagnation thresholds |

**Ports:** Input: `input` (shared data). Output: `results` (merged branch outputs).

**Events emitted:** `parallel_branch_started`, `parallel_branch_completed` (with `branch_key` in data), `parallel_fan_in_completed`.

**Default output port:** `results`.

### 3l. Orchestrator

Async runtime orchestrator that runs concurrently with subgraph teams. Unlike ParallelSubagents (fire-and-forget fan-out), the orchestrator actively monitors events from teams and can communicate back via shared context — like a real-world manager coordinating parallel teams.

| Config Field | Type | Default | Purpose |
|---|---|---|---|
| `teams` | `dict[str, str]` | *(required)* | Maps team name to sub_graph key; each team runs concurrently |
| `orchestrator_prompt` | `str` | `""` | System prompt for orchestrator LLM decisions |
| `orchestrator_model` | `str \| None` | `None` | LLM model for orchestrator (engine default if None) |
| `completion_condition` | `Literal["all_done", "any_done", "orchestrator_halt"]` | `"all_done"` | When to stop |
| `max_iterations` | `int` | `100` | Safety bound on orchestrator event loop iterations |
| `timeout_seconds` | `float \| None` | `None` | Overall timeout |
| `input_mappings` | `dict[str, str]` | `{}` | outer_port → inner_entry_port (shared input to all teams) |
| `team_inputs` | `dict[str, dict]` | `{}` | team_name → {port: value} per-team overrides |

**Ports:** Input: `input` (shared data). Output: `results` (per-team results dict), `orchestrator_log` (event log), `team_status` (status per team).

**Events emitted:** `parallel_branch_started` (with `team_name` in data), `parallel_fan_in_completed`.

**Default output port:** `results`.

---

## 4. Edge Wiring — Four Mechanisms

### 4a. F-String Magic (Recommended for LLM Prompts)

Using a `NodeRef` inside an f-string automatically creates a data edge.

```python
idea = wf.llm("idea_gen", prompt="Generate ideas about {topic}")
outline = wf.llm("planner", prompt=f"Create outline for: {idea}")
# ^ automatically creates edge: idea_gen:text -> planner (via compile-time marker)
```

The `NodeRef.__format__` method emits a marker `<<dan:node_id:port>>` that the compiler resolves into a `DataEdge` and an input port.

### 4b. `>>` Chain Operator

Connects default output to default input. Chainable.

```python
a = wf.llm("step1", prompt="...")
b = wf.llm("step2", prompt="Process: {input}")
c = wf.llm("step3", prompt="Finalize: {input}")
a >> b >> c
```

### 4c. Port Subscript (`node["port"]`)

Access a specific port by name. Used with `wf.edge()` or as parameter values.

```python
outline = wf.llm("planner", prompt="...", output_schema=SCHEMA)

# Use a specific output port as ForEach items
with wf.for_each("writers", items=outline["sections"]) as body:
    body.llm("write", prompt="Write section: {item}")
```

### 4d. Explicit `wf.edge()`

Full control over source and target ports.

```python
wf.edge(node_a["output_port"], node_b["input_port"])
```

### Control and Context Edges

```python
wf.control_edge(
    source["port"], target["port"],
    condition="score > 0.5",                # optional condition expression
)

from dan.models.context import ContextMode
wf.context_edge(
    source["port"], target["port"],
    context_key="shared_memory",
    mode=ContextMode.WRITE,                 # READ, WRITE, or APPEND
)
```

**Context edge semantics:** For `READ` mode, the **target** node consumes the context (the scheduler injects values on incoming edges of the target). The target must declare the key in its `read_set`. For `WRITE` and `APPEND`, the **source** node produces the context; the source must declare the key in its `write_set`.

### 4e. Spread Edges

A data edge with `spread=True` destructures a source dict into individual target input port values. The target "landing port" still receives the full dict. Explicit scalar edges take precedence over spread fields.

```python
wf.edge(merge["state"], governor["state"], spread=True)
# or shorthand:
wf.spread_edge(merge["state"], governor["state"])
```

---

## 5. Sub-Graph Context Managers

### 5a. WhileLoop

```python
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy

with wf.while_loop(
    "review_loop",
    condition="verdict != 'accept'",        # Python expression; loop continues while True
    max_iterations=5,                        # hard stop (default: 10)
    compaction=CompactionRule(               # optional: how to summarize history between iterations
        strategy=CompactionStrategy.SLIDING_WINDOW,
        window_size=2,
    ),
    failure_policy=FailurePolicy(           # optional: when to force-exit
        max_iterations=5,
        stagnation_threshold=2,             # stop if no progress for N iterations
        timeout_seconds=300,                # total timeout
    ),
    input_ports=[                            # ports that enter the loop from outside
        {"name": "draft"},
        {"name": "verdict"},
        {"name": "feedback"},
    ],
    output_ports=[                           # ports that exit the loop to downstream nodes
        {"name": "draft"},
        {"name": "verdict"},
        {"name": "feedback"},
    ],
) as loop_body:
    loop_body.llm(
        "review_and_revise",
        prompt="Review this draft: {draft}\nFeedback: {feedback}",
        output_schema=REVIEW_SCHEMA,
        input_ports=[{"name": "draft"}, {"name": "feedback"}],
    )

# Wire data into the loop
review_ref = NodeRef("review_loop", "while_loop", wf)
wf.edge(assembler["draft"], review_ref["draft"])
wf.edge(assembler["verdict"], review_ref["verdict"])
```

**Condition variables** are populated from the loop's input/output ports. After each iteration, the body's outputs become the next iteration's inputs.

### 5b. ForEach

```python
from dan.models.context import MergeStrategy

with wf.for_each(
    "section_writers",
    items=outline["sections"],              # PortRef to an array-valued output
    parallelism=3,                          # max concurrent iterations (default: 1)
    merge_strategy=MergeStrategy.APPEND,    # how to combine results: APPEND | LAST_WRITE_WINS | REDUCER
) as section_body:
    section_body.llm(
        "write_section",
        prompt="Write section: {item}",     # {item} is the current element, {index} is position
        input_ports=[{"name": "item"}, {"name": "index"}],
    )

# Reference the ForEach node's output
writers_ref = NodeRef("section_writers", "for_each", wf)
wf.edge(writers_ref["results"], assembler["results"])
```

**Default output port:** `results` (merged list of all iteration outputs).

**Body variables:** `{item}` (current element) and `{index}` (0-based position) are injected automatically.

### 5c. Composite

```python
with wf.composite(
    "research_block",
    input_mappings={"topic": "inner_topic"},    # outer port -> inner port
    output_mappings={"inner_result": "result"}, # inner port -> outer port
) as sub:
    s1 = sub.llm("search", prompt="Search for {inner_topic}")
    s2 = sub.llm("summarize", prompt=f"Summarize: {s1}")
    s1 >> s2
```

### 5d. Parallel Subagents

```python
from dan.models.context import MergeStrategy, FailurePolicy

with wf.parallel_subagents(
    "teams",
    parallelism=3,                          # max concurrent branches (default: 1)
    merge_strategy=MergeStrategy.APPEND,    # APPEND | LAST_WRITE_WINS | REDUCER
    failure_policy=FailurePolicy(timeout_seconds=120),
) as parallel:
    with parallel.branch("researcher") as sub:
        sub.llm("research", prompt="Research: {input}")
    with parallel.branch("analyst") as sub:
        sub.llm("analyze", prompt="Analyze: {input}")
    with parallel.branch("writer") as sub:
        sub.llm("write", prompt="Write about: {input}")

# Reference the parallel node's output
from dan.builder.refs import NodeRef
teams_ref = NodeRef("teams", "parallel_subagents", wf)
wf.edge(teams_ref["results"], downstream["input"])
```

**Default output port:** `results` (merged list or dict depending on `merge_strategy`).

**Per-branch overrides:** Use `branch_inputs` to inject branch-specific values alongside shared inputs from `input_mappings`.

### 5e. Orchestrator

```python
with wf.orchestrator(
    "coordinator",
    orchestrator_prompt="Monitor team progress and coordinate",
    completion_condition="all_done",       # all_done | any_done | orchestrator_halt
    max_iterations=100,
    timeout_seconds=300,
    input_mappings={"input": "topic"},     # route outer "input" to inner "topic"
) as orch:
    with orch.team("researcher") as sub:
        sub.llm("research", prompt="Research: {input}")
    with orch.team("analyst") as sub:
        sub.llm("analyze", prompt="Analyze: {input}")

# Reference the orchestrator node's output
from dan.builder.refs import NodeRef
coord_ref = NodeRef("coordinator", "orchestrator", wf)
wf.edge(coord_ref["results"], downstream["input"])
```

**Default output port:** `results` (dict mapping team names to their outputs).

**Key difference from `parallel_subagents`:** The orchestrator runs an event-processing loop concurrently with teams, receiving events asynchronously via `asyncio.Queue`. It can write back to shared context for bidirectional communication.

### 5f. Import Workflow as Composite Node

```python
# Build a standalone workflow
inner_wf = workflow("data_fetcher")
inner_wf.tool("search", tool_id="web_search", input_ports=[{"name": "query"}],
              output_ports=[{"name": "results"}])
inner_graph = inner_wf.build()

# Import it as a single node in another workflow
outer_wf = workflow("pipeline")
fetcher = outer_wf.import_workflow("fetcher", inner_graph)

# Wire it like any other node
source = outer_wf.llm("gen_query", prompt="Generate search for {topic}")
outer_wf.edge(source["text"], fetcher["query"])
```

**`wf.import_workflow(node_id, graph, ...)`** takes a pre-built `Graph` and embeds it as a composite node. Internal node IDs are auto-namespaced to avoid collisions.

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `node_id` | `str` | *(required)* | Unique ID for the composite node |
| `graph` | `Graph` | *(required)* | Pre-built graph to embed |
| `input_mappings` | `dict[str, str]` | auto-derived | outer_port → inner node::port |
| `output_mappings` | `dict[str, str]` | auto-derived | inner node::port → outer_port |
| `input_ports` | `list[dict]` | auto-derived | Override outer input ports |
| `output_ports` | `list[dict]` | auto-derived | Override outer output ports |
| `name` | `str` | graph name | Display name |
| `description` | `str` | graph description | Description |

**Auto-derivation:** When ports/mappings are omitted, they are derived from the imported graph's entry-point input ports and exit-point output ports (matching the editor's `graphAsCompositeNode()` convention).

**Progressive wrapping:** Build Workflow A, import it into Workflow B, then import B into C — each call to `import_workflow()` namespaces the inner graph's IDs, so nested imports compose cleanly.

```python
level1 = build_simple_workflow().build()
level2_wf = workflow("mid")
level2_wf.import_workflow("inner", level1)
# ... add more nodes ...
level2 = level2_wf.build()

level3_wf = workflow("outer")
level3_wf.import_workflow("mid_block", level2)   # level1 is nested two levels deep
```

**Utility functions** (advanced use):

```python
from dan.builder import namespace_graph, derive_ports

namespaced = namespace_graph(graph, prefix="my_prefix__")
input_ports, output_ports, in_map, out_map = derive_ports(namespaced)
```

---

## 6. Shared Context and Artifacts

### Context Declarations

```python
wf.context(
    "outline",
    json_schema={"type": "object"},
    description="Shared paper outline",
)
```

### Artifact References

```python
wf.artifact_ref(
    "s3://bucket/dataset.parquet",
    content_hash="sha256:abc123",
    media_type="application/parquet",
    description="Training dataset",
)
```

---

## 7. Engine — Running Workflows

### Basic Execution

```python
import asyncio
from dan.engine import Engine, EngineConfig

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",  # any OpenAI-compatible endpoint
    llm_api_key="your-api-key",
    llm_default_model="claude-sonnet-4-6",           # fallback model for nodes without explicit model
    checkpoint_enabled=False,                         # disable checkpointing for simple runs
    output_norm_max_retries=3,                        # retries for JSON schema validation failures
)

engine = Engine(config)
result = await engine.run(
    graph,                                  # Graph from wf.build()
    inputs={"topic": "supply chain"},       # values for {variable} placeholders in entry nodes
)

print(result.success)          # bool
print(result.outputs)          # dict of final node outputs
print(result.node_statuses)    # {"node_id": "completed" | "failed" | "skipped", ...}
print(result.errors)           # {"node_id": "error message", ...} if any
print(result.run_id)           # unique run identifier
```

### With Custom Tools

Tool functions must be `async def` and return a `dict` or scalar.

```python
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.engine.executor import ExecutorRegistry

async def my_search(query: str, limit: int = 10) -> dict:
    results = await do_search(query, limit)
    return {"results": results, "count": len(results)}

tool_registry = ToolRegistry()
tool_registry.register("web_search", my_search)

exec_registry = ExecutorRegistry()
exec_registry.register("tool_operator", ToolExecutor(tool_registry))

engine = Engine(
    config=config,
    executor_registry=exec_registry,
)
```

### With Checkpointing

```python
from dan.engine.checkpoint import FileSystemCheckpointStore

engine = Engine(
    config=EngineConfig(checkpoint_enabled=True, checkpoint_dir="./checkpoints"),
    checkpoint_store=FileSystemCheckpointStore("./checkpoints"),
)

result = await engine.run(graph, inputs={...})

# If interrupted, resume later:
result = await engine.resume(graph, run_id=result.run_id)
```

### With Event Callbacks

```python
from dan.engine import EngineEvent, EventType

async def on_event(event: EngineEvent) -> None:
    if event.event_type == EventType.NODE_STARTED:
        print(f"Starting {event.node_id}")
    elif event.event_type == EventType.NODE_COMPLETED:
        print(f"Completed {event.node_id}")
    elif event.event_type == EventType.RUN_COMPLETED:
        print("Done!")

engine = Engine(config=config, event_callback=on_event)
```

**Event types:** `run_started`, `run_completed`, `run_failed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`, `llm_thinking`, `tool_call_started`, `tool_call_result`, `code_output`, `intermediate_text`, `token_budget_advisory`, `context_deferred`, `input_summarized`, `jit_schema_loaded`, `payload_pruned`, `context_tool_called`, `cache_hit`, `cache_miss`, `cache_invalidated`, `semantic_cache_hit`, `state_externalized`, `loop_compaction_applied`, `budget_advisory`, `token_breakdown_recorded`, `waste_detected`, `optimization_report_ready`, `optimization_applied`.

---

## 7b. Retry Policy & Fallback

Every node type inherits a `retry_policy` field from `NodeBase`. The `RetryPolicy` model controls how transient failures (rate limits, timeouts, network errors) are handled.

### RetryPolicy Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `max_retries` | `int` | `0` | Number of retry attempts after the initial call |
| `backoff` | `float` | `1.0` | Initial backoff delay in seconds |
| `backoff_max` | `float` | `60.0` | Maximum backoff delay (exponential: doubles each retry, capped here) |
| `fallback_model` | `str \| None` | `None` | Alternative model to try on final failure (LLM nodes only) |
| `on_failure` | `"error" \| "skip" \| "halt"` | `"error"` | What to do after all retries are exhausted |

### Failure Modes

- **`error`** — node status is `FAILED`, error propagates (default)
- **`skip`** — node status is `SKIPPED`, downstream nodes see empty outputs
- **`halt`** — node status is `FAILED` with `metadata.halt=True`; the engine stops at the current topological level (already-running parallel nodes finish) and writes a checkpoint for later `Engine.resume()`

### Setting RetryPolicy on Nodes

`RetryPolicy` is set on the node model directly. The builder DSL exposes it through the `metadata` dict or by modifying the compiled graph:

```python
from dan.models.nodes import RetryPolicy

# Option 1: Set on the compiled graph
graph = wf.build()
for node in graph.nodes:
    if node.node_type == "llm_operator":
        node.retry_policy = RetryPolicy(
            max_retries=3,
            backoff=2.0,
            fallback_model="gpt-4o-mini",
            on_failure="halt",
        )

# Option 2: Set on ToolOperator for transient tool failures
for node in graph.nodes:
    if node.node_type == "tool_operator" and node.tool_id == "web_search":
        node.retry_policy = RetryPolicy(
            max_retries=2,
            backoff=1.0,
            backoff_max=10.0,
            on_failure="skip",
        )
```

### How Executors Respect RetryPolicy

- **`ToolExecutor`**: catches transient exceptions (`TimeoutError`, `ConnectionError`, `OSError`), retries with exponential backoff, emits `retry_attempted` events. Permanent exceptions break immediately.
- **`LLMExecutor`**: retries transient API errors. If `fallback_model` is set, switches model on final retry.
- **Separate from output normalization**: output schema validation retries (JSON parse → re-prompt) are controlled by `output_norm_max_retries` on `EngineConfig`, not `RetryPolicy`.

---

## 7c. Multi-Provider LLM Support

DAN supports multiple LLM providers simultaneously via `ProviderRegistry`.

### Supported Providers

| Provider | SDK | Env Var | Model Prefix |
|---|---|---|---|
| **OpenAI** (+ compatible endpoints) | `openai` | `DAN_OPENAI_API_KEY` | `gpt-*`, `o1*`, `o3*`, `o4*` |
| **Anthropic** | `anthropic` (optional) | `DAN_ANTHROPIC_API_KEY` | `claude-*` |
| **Google** | `google-generativeai` (optional) | `DAN_GOOGLE_API_KEY` | `gemini-*` |

### Configuration

```bash
# .env — set keys for the providers you want to use
DAN_LLM_API_KEY=your-default-key        # default provider (OpenAI-compatible)
DAN_LLM_BASE_URL=https://api.vectorengine.ai/v1
DAN_LLM_MODEL=claude-sonnet-4-6

# Additional providers (optional)
DAN_OPENAI_API_KEY=sk-...
DAN_ANTHROPIC_API_KEY=sk-ant-...
DAN_GOOGLE_API_KEY=AIza...
```

### Per-Node Model Dispatch

Each node specifies its model independently. The `ProviderRegistry` resolves model → provider:

1. **Exact override** — `model_provider_map` pins a model to a provider
2. **Prefix match** — `gpt-*` → OpenAI, `claude-*` → Anthropic, `gemini-*` → Google
3. **Default fallback** — `EngineConfig.llm_api_key` + `llm_base_url` create a `"default"` provider

```python
wf = workflow("multi_model")
classifier = wf.llm("classify", model="gpt-4o-mini", prompt="Classify: {input}")
writer = wf.llm("write", model="claude-sonnet-4-6", prompt=f"Write: {classifier}")
summarizer = wf.llm("summarize", model="gemini-2.0-flash", prompt=f"Summarize: {writer}")
classifier >> writer >> summarizer
```

### Programmatic Provider Setup

```python
from dan.engine import Engine, EngineConfig
from dan.providers import ProviderConfig

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",
    llm_api_key="default-key",
    llm_default_model="claude-sonnet-4-6",
    providers={
        "openai": ProviderConfig(api_key="sk-..."),
        "anthropic": ProviderConfig(api_key="sk-ant-..."),
        "google": ProviderConfig(api_key="AIza..."),
    },
)
engine = Engine(config)
```

### Cost Estimation

```python
from dan.providers.costs import estimate_cost

cost = estimate_cost(model="gpt-4o", input_tokens=1000, output_tokens=500)
# Returns estimated cost in USD, or None for unknown models
```

---

## 7d. Built-in Tools (`dan.tools`)

DAN ships 11 batteries-included tools, auto-registered during server startup. Each tool module exports a `TOOL_METADATA` dict and an async callable.

### Tool Categories

| Category | Tools | Description |
|---|---|---|
| **File I/O** | `file_read`, `file_write`, `list_directory` | Workspace-sandboxed file operations |
| **Web** | `web_search`, `web_fetch`, `http_request` | DuckDuckGo search, URL fetch, general HTTP |
| **Shell** | `shell_command` | Subprocess with timeout and allowlist |
| **Document** | `pdf_read` | PDF text extraction |
| **Text Processing** | `text_chunk`, `json_extract`, `regex_match` | Chunking, dot-notation extraction, regex |

### Using Tools in Workflows

```python
wf = workflow("research")

search = wf.tool("search", tool_id="web_search",
    input_ports=[{"name": "query"}],
    output_ports=[{"name": "results"}])

fetch = wf.tool("fetch", tool_id="web_fetch",
    input_ports=[{"name": "url"}],
    output_ports=[{"name": "content"}])

extract = wf.tool("extract", tool_id="json_extract",
    tool_config={"path": "data.results"},
    input_ports=[{"name": "json_text"}],
    output_ports=[{"name": "result"}])
```

### Registering Custom Tools

```python
from dan.executors.tool import ToolRegistry

registry = ToolRegistry()
registry.register_builtin_tools()  # registers all 11 built-in tools

# Add custom tools (override built-in IDs or add new ones)
async def my_tool(query: str) -> dict:
    return {"result": f"processed {query}"}

registry.register("my_custom_tool", my_tool)
```

### Tool Metadata Pattern

Each tool module follows the same pattern — export `TOOL_METADATA` dict with keys: `tool_id`, `description`, `parameters` (JSON Schema), `examples`, `category`, `returns`. The `get_all_tools()` function in `dan.tools` auto-discovers all modules and returns `{tool_id: (function, metadata)}`.

### Workspace Sandboxing

File tools (`file_read`, `file_write`, `list_directory`) enforce `DAN_WORKSPACE_ROOT` boundary — paths outside the workspace are rejected. Defaults to the current working directory.

### Graceful Degradation

Optional-dependency tools (`web_search` requires `duckduckgo-search`, `pdf_read` requires `pypdf`) skip with a warning if the SDK is not installed. The remaining tools continue to work.

---

## 8. Complete Example — Paper Writing Workflow

This is the canonical end-to-end example. It uses LLM nodes, ForEach, WhileLoop, Code, and Tool nodes.

```python
import asyncio, os
from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.engine import Engine, EngineConfig
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy, MergeStrategy

# ── Tool function ─────────────────────────────────────────────────
async def save_paper(content: str, title: str, **kwargs) -> dict:
    from pathlib import Path
    path = Path("output") / f"{title[:40]}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)
    return {"saved_path": str(path), "title": title}

# ── Schemas ───────────────────────────────────────────────────────
OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "abstract": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "abstract", "sections"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "feedback": {"type": "string"},
        "draft": {"type": "string"},
    },
    "required": ["verdict", "feedback", "draft"],
}

# ── Build graph ───────────────────────────────────────────────────
wf = workflow("paper_writing", description="Multi-agent paper writing", tags=["demo"])

# Step 1: Idea generation
idea = wf.llm("idea_gen", prompt="Generate a research idea about: {topic}")

# Step 2: Literature survey (f-string creates edge from idea_gen)
lit = wf.llm("lit_survey", prompt=f"Survey literature for:\n{idea}")

# Step 3: Outline with structured output (f-string creates edges from idea + lit)
outline = wf.llm(
    "outline_planner",
    prompt=f"Research Idea:\n{idea}\n\nLiterature:\n{lit}\n\nCreate outline as JSON.",
    output_schema=OUTLINE_SCHEMA,
)

# Step 4: Parallel section writing (ForEach over outline.sections)
with wf.for_each(
    "section_writers",
    items=outline["sections"],
    parallelism=3,
    merge_strategy=MergeStrategy.APPEND,
) as body:
    body.llm("write_section", prompt="Write section: {item}",
             input_ports=[{"name": "item"}, {"name": "index"}])

# Step 5: Assemble draft (Code node)
assembler = wf.code(
    "assembler",
    code="body='\\n\\n'.join(r.get('text',str(r)) for r in results)\n"
         "draft='# '+str(title)+'\\n\\n'+str(abstract)+'\\n\\n'+body\n"
         "result={'draft':draft,'verdict':'pending','feedback':'Initial draft.'}",
    input_ports=[{"name": "results"}, {"name": "title"}, {"name": "abstract"}],
    output_ports=[{"name": "draft"}, {"name": "verdict"}, {"name": "feedback"}],
)
writers_ref = NodeRef("section_writers", "for_each", wf)
wf.edge(writers_ref["results"], assembler["results"])
wf.edge(outline["title"], assembler["title"])
wf.edge(outline["abstract"], assembler["abstract"])

# Step 6: Review-revise loop (WhileLoop)
with wf.while_loop(
    "review_loop",
    condition="verdict != 'accept'",
    max_iterations=3,
    compaction=CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2),
    failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
    input_ports=[{"name": "draft"}, {"name": "verdict"}, {"name": "feedback"}],
    output_ports=[{"name": "draft"}, {"name": "verdict"}, {"name": "feedback"}],
) as loop_body:
    loop_body.llm(
        "review_and_revise",
        prompt="Review draft:\n{draft}\n\nPrevious feedback: {feedback}\n\nOutput JSON.",
        output_schema=REVIEW_SCHEMA,
        input_ports=[{"name": "draft"}, {"name": "feedback"}],
    )
review_ref = NodeRef("review_loop", "while_loop", wf)
wf.edge(assembler["draft"], review_ref["draft"])
wf.edge(assembler["verdict"], review_ref["verdict"])
wf.edge(assembler["feedback"], review_ref["feedback"])

# Step 7: Format output (Code node)
formatter = wf.code(
    "format_output",
    code="result={'content': draft, 'title': title}",
    input_ports=[{"name": "draft"}, {"name": "title"}],
    output_ports=[{"name": "content"}, {"name": "title"}],
)
wf.edge(review_ref["draft"], formatter["draft"])
wf.edge(outline["title"], formatter["title"])

# Step 8: Save paper (Tool node)
save = wf.tool(
    "save_paper", tool_id="save_paper",
    input_ports=[{"name": "content"}, {"name": "title"}],
    output_ports=[{"name": "saved_path"}, {"name": "title"}],
)
wf.edge(formatter["content"], save["content"])
wf.edge(formatter["title"], save["title"])

graph = wf.build()

# ── Run ───────────────────────────────────────────────────────────
async def main():
    tool_reg = ToolRegistry()
    tool_reg.register("save_paper", save_paper)
    exec_reg = ExecutorRegistry()
    exec_reg.register("tool_operator", ToolExecutor(tool_reg))

    engine = Engine(
        config=EngineConfig(
            llm_base_url=os.getenv("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
            llm_api_key=os.getenv("DAN_LLM_API_KEY", ""),
            llm_default_model=os.getenv("DAN_LLM_MODEL", "claude-sonnet-4-6"),
            checkpoint_enabled=False,
        ),
        executor_registry=exec_reg,
    )
    result = await engine.run(graph, inputs={"topic": "supply chain resilience"})
    print(result.success, result.outputs)

asyncio.run(main())
```

---

## 9. REST API (Server Mode)

Start the server:

```bash
dan-serve
# or: python -m dan.server
```

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graphs` | List all graphs + last opened |
| POST | `/api/graphs` | Create new graph |
| GET | `/api/graphs/{id}` | Load graph JSON |
| PUT | `/api/graphs/{id}` | Save graph JSON |
| DELETE | `/api/graphs/{id}` | Delete graph |
| POST | `/api/graphs/{id}/validate` | Validate graph (design-time checks), return errors/warnings |
| GET | `/api/graphs/{id}/export/markdown` | Export graph as markdown workflow |
| GET | `/api/graphs/{id}/export/python` | Export graph as Python builder code |
| GET | `/api/metrics/mutations` | Get mutation quality metrics |
| POST | `/api/metrics/mutations/reset` | Reset mutation metrics |
| POST | `/api/cache/clear` | Clear memoization/semantic cache files |
| GET | `/api/cache/stats` | Cache health + latest run cache summary |
| GET | `/api/runs/{id}/token-breakdown` | Per-node token composition breakdown |
| GET | `/api/runs/{id}/optimization-report` | Waste detection findings + optimization recommendations |
| GET | `/api/runs/{id}/optimization-mutations` | One-click-apply mutation previews for waste findings |
| POST | `/api/runs` | Start execution (body: `{"graph_id": "...", "inputs": {...}}`) |
| POST | `/api/runs/{id}/resume` | Resume checkpointed run |
| GET | `/api/runs/{id}` | Get run status snapshot |
| GET | `/api/runs` | List all runs |
| WS | `/api/runs/{id}/events` | Live event stream (WebSocket) |

---

## 10. Type Reference

### Node Types

| `node_type` string | Builder method | Default output port | Purpose |
|---|---|---|---|
| `llm_operator` | `wf.llm()` | `text` | LLM call |
| `code_operator` | `wf.code()` | `result` | Python execution |
| `tool_operator` | `wf.tool()` | `result` | Registered function call |
| `gate` (if_else mode) | `wf.gate()` | `true`, `false` | Conditional routing (replaces `if_else`) |
| `gate` (while mode) | `wf.gate()` | `continue`, `done` | Iterative loop (replaces `while_loop`) |
| `for_each` | `wf.for_each()` | `results` | Parallel fan-out over list |
| `parallel_subagents` | `wf.parallel_subagents()` | `results` | Heterogeneous parallel branches |
| `orchestrator` | `wf.orchestrator()` | `results` | Async orchestrator with concurrent teams |
| `composite` | `wf.composite()` | *(declared)* | Sub-graph |
| `reduce` | `wf.reduce()` | `result` | Fan-in aggregation |
| `router` | `wf.router()` | `route` | LLM-powered routing |
| `human_in_the_loop` | `wf.human_in_the_loop()` | `response` | Human input |
| `rag_operator` | `wf.rag()` | `chunks` | Vector-store retrieval |
| `validator` | `wf.validator()` | `valid` | Data validation with rule routing |
| `input` | *(loader/scoped_run only)* | *(variable-based)* | Workflow entry variables — created by loader or scoped_run, not by builder |

### Edge Types

| `edge_type` | Builder method | Purpose |
|---|---|---|
| `data` | `wf.edge()`, `wf.spread_edge()`, f-string, `>>` | Schema-validated data flow (optional `spread=True` for dict destructuring) |
| `control` | `wf.control_edge()` | Conditional routing |
| `context` | `wf.context_edge()` | Shared state read/write (supports `pass_by_reference=True` for lazy artifact refs) |

### Context Enums

| Enum | Values | Used in |
|---|---|---|
| `MergeStrategy` | `APPEND`, `LAST_WRITE_WINS`, `REDUCER` | `wf.for_each()`, `wf.parallel_subagents()` |
| `CompactionStrategy` | `SLIDING_WINDOW`, `KEEP_LAST`, `SUMMARIZE`, `DIFF_BASED`, `NONE` | `CompactionRule(strategy=...)` |
| `ContextMode` | `READ`, `WRITE`, `APPEND` | `wf.context_edge(mode=...)` |

### EngineConfig Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `llm_base_url` | `str` | `"https://api.vectorengine.ai/v1"` | OpenAI-compatible endpoint |
| `llm_api_key` | `str` | `""` | API key |
| `llm_default_model` | `str` | `"claude-sonnet-4-6"` | Fallback model |
| `checkpoint_dir` | `str` | `"./checkpoints"` | Checkpoint directory |
| `checkpoint_enabled` | `bool` | `True` | Enable checkpointing |
| `output_norm_max_retries` | `int` | `3` | Schema validation retries |
| `token_budget` | `int \| None` | `None` | Advisory run-level token budget (guidance only) |
| `cache_enabled` | `bool` | `True` | Enable memoization + semantic cache lookups |
| `cache_max_size_mb` | `int` | `100` | Max in-memory cache size before LRU eviction |
| `cache_dir` | `str \| None` | `None` | Optional persistent cache directory |
| `semantic_cache_threshold` | `float` | `0.95` | Similarity threshold for semantic cache hits |
| `semantic_cache_ttl_hours` | `float` | `24.0` | Semantic cache entry TTL |
| `prompt_caching_enabled` | `bool` | `True` | Enable provider-level prompt cache hints |
| `optimization_rule_approval_mode` | `str` | `"always_approve"` | `"always_approve"` (human gate) or `"auto_accept"` (activate by default) for evolving optimization rules |
| `embedding_providers` | `dict[str, ProviderConfig]` | `{}` | Named embedding providers for `RAGOperator` (e.g., `default`, `openai`, `local`) |
| `embedding_model_provider_map` | `dict[str, str]` | `{}` | Exact embedding model → provider override map |
| `default_embedding_model` | `str` | `"text-embedding-3-small"` | Default embedding model when a `RAGOperator` omits `embedding_model` |

### RunResult Fields

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Unique run identifier |
| `outputs` | `dict[str, Any]` | Final outputs from exit nodes |
| `success` | `bool` | Whether the run completed without errors |
| `node_statuses` | `dict[str, str]` | Per-node status (`"completed"`, `"failed"`, `"skipped"`) |
| `errors` | `dict[str, str]` | Per-node error messages (if any) |
| `metadata` | `dict[str, Any]` | Additional run metadata |

---

## 11. Patterns and Recipes

### Linear Chain

```python
wf = workflow("chain")
a = wf.llm("step1", prompt="Analyze: {input}")
b = wf.llm("step2", prompt=f"Expand on: {a}")
c = wf.llm("step3", prompt=f"Finalize: {b}")
```

### Parallel Fan-Out → Reduce (ForEach)

```python
wf = workflow("parallel")
source = wf.llm("generate", prompt="List items about {topic}", output_schema=LIST_SCHEMA)

with wf.for_each("process", items=source["items"], parallelism=5,
                  merge_strategy=MergeStrategy.APPEND) as body:
    body.llm("analyze", prompt="Analyze: {item}",
             input_ports=[{"name": "item"}, {"name": "index"}])
```

### Heterogeneous Parallel Branches (Parallel Subagents)

```python
wf = workflow("teams")
source = wf.llm("coordinator", prompt="Prepare topic: {input}")
with wf.parallel_subagents("teams", parallelism=2, merge_strategy=MergeStrategy.APPEND) as parallel:
    with parallel.branch("researcher") as sub:
        sub.llm("r", prompt="Research: {input}")
    with parallel.branch("analyst") as sub:
        sub.llm("a", prompt="Analyze: {input}")
teams = NodeRef("teams", "parallel_subagents", wf)
source >> teams  # wire input to parallel node
```

### Async Orchestrator (Concurrent Teams with Event Monitoring)

```python
wf = workflow("managed_teams")
with wf.orchestrator("manager",
    completion_condition="all_done",
    timeout_seconds=300,
    input_mappings={"input": "topic"},
) as orch:
    with orch.team("researcher") as sub:
        sub.llm("r", prompt="Research: {input}")
    with orch.team("analyst") as sub:
        sub.llm("a", prompt="Analyze: {input}")
manager = NodeRef("manager", "orchestrator", wf)
```

> **Difference from parallel_subagents:** The orchestrator spawns teams as `asyncio.create_task` (not `asyncio.gather`), runs its own event-processing loop concurrently, and can react to team events in real-time via an `asyncio.Queue`.

### Conditional Branching

```python
wf = workflow("branch")
classifier = wf.llm("classify", prompt="Classify {input} as 'technical' or 'general'")
wf.if_else("route", condition="'technical' in text")
# Wire control edges to different downstream nodes
```

> **Note:** `wf.if_else()` is a deprecated alias; use `wf.gate("route", gate_mode="if_else", condition="...")` for new code.

### Iterative Refinement

```python
with wf.while_loop("refine", condition="quality < 0.9", max_iterations=5,
                    input_ports=[{"name": "draft"}, {"name": "quality"}],
                    output_ports=[{"name": "draft"}, {"name": "quality"}]) as body:
    body.llm("improve", prompt="Improve this draft: {draft}",
             output_schema={"type": "object",
                           "properties": {"draft": {"type": "string"},
                                          "quality": {"type": "number"}},
                           "required": ["draft", "quality"]},
             input_ports=[{"name": "draft"}])
```

> **Note:** `wf.while_loop()` is a deprecated alias; use `wf.gate("refine", gate_mode="while", condition="...", ...)` for new code.

### Custom Tool Integration

```python
async def query_database(sql: str) -> dict:
    rows = await db.execute(sql)
    return {"rows": rows, "count": len(rows)}

tool_reg = ToolRegistry()
tool_reg.register("query_db", query_database)

wf = workflow("data_pipeline")
wf.tool("fetch_data", tool_id="query_db",
        input_ports=[{"name": "sql"}],
        output_ports=[{"name": "rows"}, {"name": "count"}])
```

**run_python (implemented):** `run_python(code, **context)` — generic tool that executes model-generated Python. Inject context kwargs (e.g. item, results, out_dir) as variables; code must assign to `result` or `output`. Returns `{ result, stdout, stderr, error? }`. Preferred pattern for post-processing: LLM generates code → run_python executes it. Replaces domain-specific tools (plot_backtest, save_grid_csv — now deprecated).

### Progressive Workflow Wrapping

Build standalone workflows and import each as a node in the next level:

```python
# Level 1: standalone
dg_wf = workflow("data_gatherer")
# ... add nodes ...
dg_graph = dg_wf.build()

# Level 2: imports Level 1
sa_wf = workflow("section_analyst")
sa_wf.import_workflow("data_gatherer", dg_graph)
# ... add more nodes around it ...
sa_graph = sa_wf.build()

# Level 3: imports Level 2 inside a ForEach
report_wf = workflow("report")
with report_wf.for_each("sections", items=planner["sections"]) as body:
    body.import_workflow("analyst", sa_graph)
```

See `examples/equity_research.py` for a full 3-level example using all edge types.

### 5f. Loop-Scoped State (GateNode)

`GateNode` (while mode) supports optional `state_schema` and `state_defaults` for automatic loop-scoped state management:

```python
gate = wf.gate(
    "iterate",
    gate_mode="while",
    condition="counter < 5",
    state_schema={"counter": {"type": "integer"}, "results": {"type": "array"}},
    state_defaults={"counter": 0, "results": []},
)
```

When `state_schema` is present, the engine maintains a state bag scoped to the loop via `LocalStateManager`. Body nodes automatically receive state fields as inputs and their outputs matching `state_schema` keys are written back. The gate condition evaluates against scope fields directly — no manual state-threading edges or unpack/repack code nodes needed.

Also available on the deprecated `wf.while_loop(... state_schema={...}, state_defaults={...})`.

**Markdown flow syntax:**

```
orchestrator | loop(body_agent, until: "done", max: 5, state: '{"counter": {"type": "integer"}}', defaults: '{"counter": 0}')
```

The `state` and `defaults` kwargs accept JSON strings that map to `state_schema` and `state_defaults` on the generated `GateNode`.

### Referencing Composite Node Outputs

When wiring edges from `for_each` or `while_loop` nodes, create a `NodeRef` manually:

```python
from dan.builder.refs import NodeRef

ref = NodeRef("section_writers", "for_each", wf)
wf.edge(ref["results"], downstream["input"])
```

---

## 12. Workflow Generation Playbook

Use this when an LLM is asked to generate workflow markdown (`workflow.md`, agent `.md` files) or Python builder scripts.

### 12a. Non-Negotiable Rules

1. **Use strict mode for LLM-generated and new workflows.**
   - Call `compile_workflow(path, strict=True)` or `load(path, strict=True)` when compiling markdown workflows.
   - Strict mode treats parse warnings and ambiguous bare-edge auto-wire as fatal errors, preventing partial graphs and silent single-port wiring.
2. **Make ports explicit and typed.**
   - In markdown agents, always include `> Accepts:` / `> Returns:` with concrete types.
   - In Python builder code, prefer explicit `input_ports` / `output_ports` with `json_schema` for non-trivial nodes.
3. **Treat compiler warnings as failures during generation.**
   - Do not accept outputs that rely on auto-wiring fallback or untyped edges.
   - Regenerate until `validate_graph()` has no fatal issues and no schema-safety warnings.
4. **Use explicit gate ports for loop routing.**
   - For while-mode gates, `gate >> body` now works (default output is `continue`).
   - Explicit `gate["continue"]` and `gate["done"]` are still recommended for clarity but not required.
5. **Prefer deterministic contracts over implicit behavior.**
   - Stable node IDs, stable artifact names, stable output keys.
   - No hidden assumptions about default ports in critical paths.

### 12b. Markdown Authoring Guidance

- **Flow lines:** Use explicit `.port` syntax whenever there is branching, merging, or similarly named ports.
- **Loop semantics:** `loop(body, until: "...")` means *stop when condition is true*.
- **Loop state:** When needed, include `state:` and `defaults:` JSON strings in `loop(...)`.
- **ForEach:** Use `source | each(body, parallel: N)`; to wire a specific array port (e.g. sections) use `source.port | each(...)` (e.g. `outline_planner.sections | each(section_writer, parallel: 4)`). Wire the each node’s output with `each_node_id → next_agent` (e.g. `outline_planner_each_section_writer → assembler`). If body logic has multiple steps, wrap it as a composite agent.
- **Parallel branches:** Use `source | parallel(team_a, team_b, merge: append, parallel: 2)` for heterogeneous parallel subagents. Each branch references an agent (composite or atomic).
- **Context-heavy workflows:** Markdown decompilation prioritizes data-flow readability. Control and context edges are emitted as comments (`<!-- SKIPPED -->`). Round-trip is not lossless for workflows that use those edges. For full round-trip of context-heavy or control-heavy workflows, keep a Python/JSON canonical source.

### 12c. Python Builder Guidance

- Build nodes first, then wire explicit edges (`wf.edge(...)`) for critical paths.
- Use `wf.gate(..., gate_mode="if_else"|"while")` instead of deprecated aliases.
- Avoid relying on inferred defaults for complex composites; provide mappings and ports explicitly.
- For imported workflows (`import_workflow`), verify outer port schemas/mappings after import.

### 12d. Script Generation Guidance (Code/Tool Nodes)

- Generated code should be deterministic and side-effect aware.
- Always return a structured `result` dict with stable keys.
- Capture and propagate diagnostics (`stdout`, `stderr`, explicit error fields).
- Persist generated scripts/artifacts before execution when reproducibility matters.

### 12e. Recommended Generation Loop

1. Generate markdown or builder script.
2. Compile to `Graph` (use `compile_workflow(path, strict=True)` or `load(path, strict=True)` for markdown).
3. Validate (`validate_graph`).
4. Run a tiny smoke execution on representative inputs.
5. (If markdown) decompile/round-trip check before shipping.

### 12f. Build-from-Intent Mode (Chat API)

Build-from-intent creates workflows from natural language. It can be triggered two ways:

1. **Automatically** — when the graph is empty (0 nodes, 0 edges), the chat switches to build mode regardless of the `mode` parameter.
2. **Explicitly** — set `mode="build"` on the chat API request. This forces build mode even on non-empty graphs (useful for rebuilding).

#### API Usage

```bash
# POST /api/chat/message
{
  "workflow_id": "my-workflow",
  "message": "Create a paper writing pipeline with review loop",
  "mode": "build",          # "build" or "mutate" (default: "mutate")
  "history": [],
  "thread_id": null,
  "client_graph_revision": null
}
```

When `mode="build"`, the LLM receives `BUILD_FROM_INTENT_PROMPT` with:
- **Task decomposition guidance** — break intent into stages, map to node types and data flow; dual-branch composition (`data_ingest` + `data_analysis` + drafting/review/compile)
- **Pattern library** — chain, review_loop, fan_out, rag_qa, data_ingest, data_analysis (use `expand_pattern` ops)
- **Intent→pattern mapping** — paper writing → review_loop + chain, RAG QA → rag_qa, multi-step → chain, Management Science → `informs_paper_writing` + `apply_skill(management_science_writing)`
- **Available templates** — pre-built operation sequences (see below)
- **Path-policy guidance** — if user-provided path is outside workspace, ask user to import/mount/copy into workspace
- **Skills** — domain-specific prompt injections via `apply_skill` op (see below)

When `mode="mutate"` (default), the LLM receives `SYSTEM_PROMPT_TEMPLATE` with the current graph summary for modification.

#### Available Templates

Pre-built workflow templates (`WORKFLOW_TEMPLATES`) that the LLM can reference:

| Template | Pattern(s) | Description |
|---|---|---|
| `paper_writing` | `review_loop` + `chain(3)` | Drafter→Reviewer→Gate loop + Outline→Draft→Final Polish chain |
| `rag_qa` | `rag_qa` | RAG retrieval → LLM answer |
| `chain_3` | `chain(3)` | Simple 3-node sequential chain (Step 1 → Step 2 → Step 3) |
| `informs_paper_writing` | `data_ingest` + `data_analysis` + review loop + LaTeX pipeline | 19-node fully-wired template: Input(topic,pdf_dir,data_path) → data_ingest → data_analysis → outline/draft/review → LaTeX assembly → check_latex_deps → citation_verifier → compile_latex → save_paper → package_submission. Includes HumanInTheLoop and Management Science-calibrated prompts. |
| `rag_research` | `data_ingest` + LLM synthesis | Ingest papers at a path, retrieve, and synthesize understanding. |

#### New Patterns

| Pattern | Parameters | Description |
|---|---|---|
| `data_ingest` | `input_var` (str), `collection` (str), `rag_name` (str), `top_k` (int) | Two-stage flow: filesystem discovery/read + RAG index build + retrieval-ready outputs. Creates InputNode → list_directory tool → pdf_read tool → rag_index_documents tool → rag_operator. |
| `data_analysis` | `input_var` (str) | Path-aware data branch: InputNode(data_path) → file_read tool → preprocess code → methods_results_summary LLM (structured output). |

#### New Mutation Operation: `apply_skill`

Injects domain-specific prompt prefixes into targeted nodes:

```json
{
  "op": "apply_skill",
  "skill": "management_science_writing",
  "target_nodes": ["draft_section", "review_draft"],
  "target_tag": "writing"
}
```

- `skill` — key from `SKILL_LIBRARY` (`management_science_writing`, `informs_latex_style`)
- `target_nodes` — list of node IDs to inject the skill prompt into (optional)
- `target_tag` — target all nodes with this value in `metadata.tags` (optional)
- Injection: prepends skill prompt to `system_prompt` (or `prompt_template` fallback)

#### Available Skills

| Skill | Target Tags | Description |
|---|---|---|
| `management_science_writing` | `writing`, `review` | Management Science journal conventions: contribution framing, methods rigor, reviewer criteria |
| `informs_latex_style` | `latex` | INFORMS LaTeX formatting: `informs3.cls`, `plainnat` bibliography, submission package conventions |

#### Build Flow

1. User sends message with `mode="build"` (or graph is empty)
2. LLM produces a `plan_graph_mutations` call targeting the empty/existing graph
3. Backend injects `base_graph_revision` from the current graph state (handles empty graphs)
4. `GraphMutator.dry_run()` validates the plan; auto-retry on failure
5. Client receives `ChatMutationEvent` with the plan and diff preview
6. Client applies the mutation, switches to `mode="mutate"` for follow-up edits

Use `strict=true` in `add_edge` operations when building from intent to fail fast on port typos.

---

## 13. Execution & Observability (Per-Node Logs)

The DAN engine emits a rich stream of events during execution. Every event is strictly associated with its source node (`node_id`) unless it is a workflow-level lifecycle event.

### Event Types
There are 14 typed events: `run_started`, `run_completed`, `run_failed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`, `llm_thinking`, `tool_call_started`, `tool_call_result`, `code_output`, `intermediate_text`.

### Per-Node Tracking
- **Automatic Tagging:** The `Engine` automatically tags every event emitted via `ExecutionContext.emit_event()` with the currently executing `node_id`.
- **Persistence:** The `RunStore` captures the entire event stream into `{run_id}.events.jsonl`, inherently preserving the per-node execution history (tool calls, LLM thinking, standard output).
- **Frontend Subscriptions:**
  - `GET /api/runs/{run_id}/events` provides filtering by `node_id` and `event_type`.
  - The `LogPanel` groups all raw events by `node_id`, ensuring that parallel node outputs are not interleaved but cleanly segregated.
  - The Chat interface derives node progress purely from `node_started`/`node_completed`/`node_failed`/`node_output` events, providing deep-links to the complete per-node history.

---

## 14. Import Map

```python
# Builder
from dan.builder import workflow, decompile, WorkflowBuilder, NodeRef, PortRef, BuildError, namespace_graph, derive_ports

# Engine
from dan.engine import (
    Engine, EngineConfig, RunResult,
    EngineEvent, EventType, EventCallback,
    ExecutionContext, ExecutionState,
    ExecutorRegistry, NodeExecutor, NodeResult, NodeStatus,
    PortDataStore,
    SharedContextStore, ArtifactStore, LocalStateManager,
    CheckpointStore, FileSystemCheckpointStore, NullCheckpointStore,
    ConditionError, evaluate_condition,
    OutputNormalizer, NormResult,
)

# Executors
from dan.executors.tool import ToolExecutor, ToolRegistry

# Models
from dan.models.nodes import LLMOperator, ToolOperator, CodeOperator, NodeBase
from dan.models.control_flow import (
    InputNode, GateNode, IfElseNode, WhileLoopNode, ForEachNode, CompositeNode,
    ParallelSubagentsNode, ValidatorNode,
    ReduceNode, RouterNode, HumanInTheLoopNode,
)
from dan.models.edges import DataEdge, ControlEdge, ContextEdge
from dan.models.ports import InputPort, OutputPort
from dan.models.graph import Graph
from dan.models.context import (
    MergeStrategy, CompactionStrategy, CompactionRule,
    FailurePolicy, ContextDeclaration, ContextMode,
    NodeLocalState, SharedContextDeclaration, ArtifactRef, ContextProjection,
)
```
