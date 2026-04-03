# DAN API Guide (for LLM Callers)

> **Auto-update rule:** This file must be updated whenever nodes, edges, builder methods, engine APIs, executors, or examples change. See `docs/` tracking rules.

## Quick Reference

```python
from dan.builder import workflow, decompile
from dan.engine import Engine, EngineConfig, RunResult
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import MergeStrategy, CompactionStrategy, CompactionRule, FailurePolicy
from dan.worker import Worker
```

---

## 1. Core Concepts

**Graph** — A directed acyclic graph (with loops expressed as composite nodes) of typed nodes connected by typed edges. Serialized as `dan_graph_v1` JSON.

**Node** — An operator (atomic unit of work) or a composite (sub-graph that behaves as a single node). The runtime currently exposes 20+ node types, including the newer `worker` contract primitive.

For new authored compute stages, prefer `wf.worker(...)` or the classic compute aliases (`wf.llm(...)`, `wf.tool(...)`, `wf.code(...)`, `wf.rag(...)`, `wf.input_node(...)`, `wf.reflection(...)`, `wf.human(...)`, `wf.human_in_the_loop(...)`, `wf.vote(...)`, `wf.ensemble(...)`) that now normalize through the Worker contract surface internally. If you want those aliases to emit canonical `worker` nodes directly, use `workflow(..., canonical_workers=True)` or `DAN_WORKER_BUILDER=canary|enabled`. If you need to import the older concrete compute/compatibility model classes directly, prefer `dan.models.legacy` as the canonical compatibility surface rather than mixing imports from `dan.models.nodes` and `dan.models.control_flow`.

**Edge** — A typed connection between node ports. Three types: data, control, context.

**Port** — Named input/output slots on nodes. Every edge connects a source output port to a target input port.

**Engine** — Async execution runtime. Topological scheduling, parallel fan-out, checkpointing.

**Builder DSL** — Fluent Python API for constructing graphs programmatically.

**InputNode:** Created via `wf.input_node(...)`. `wf.input` is still a *property* used only inside scoped sub-graphs as the entry `PortRef`.

---

## 2. Builder DSL — Creating Workflows

### Initialize a Workflow

```python
from dan.builder import workflow

wf = workflow(
    "my_workflow",              # graph name (required)
    description="What it does", # optional
    tags=["demo", "research"],  # optional
    canonical_workers=False,    # optional: emit worker-native compute-like aliases
    lint_autogen="disabled",    # optional: disabled | canary | enabled
    lint_intent_refiner=None,   # optional: callable(base_intent, context) -> refined intent
)
```

### Build and Serialize

```python
graph = wf.build()                                              # -> Graph (validated Pydantic model)
graph = wf.build(lint_autogen="enabled")                       # optional per-build override
json_str = wf.to_json(indent=2)                                 # -> JSON string
d = wf.to_dict()                                                # -> dict
code_str = decompile(graph)                                     # -> canonical executable Python
readable_code = decompile(graph, use_convenience_aliases=True)  # -> optional readability-oriented aliases (llm/tool/code plus simple input/reduce/rag/reflection/human/vote leaves)
```

`lint_autogen` controls whether conservative per-edge lint configs are generated at compile/build time from node contracts:
- `disabled` (default): do not stamp generated lint onto edges
- `canary`: only auto-generate for Worker-to-Worker data edges
- `enabled`: best-effort auto-generate for any data edge with strong enough contract metadata

You can also set `DAN_LINT_AUTOGEN=disabled|canary|enabled` as a process-wide default.

`lint_intent_refiner` is an optional graph-construction-time hook for complex workflows. When provided, compile/build-time lint autogen passes the deterministic intent string plus a small context dict (`graph_name`, source/target IDs/types/descriptions, target role/port, resolved instruction) to the callable and uses the returned text if it is non-empty. If the callable is absent or raises, autogen falls back to the deterministic intent text.

`canonical_workers` controls whether the classic compute-like aliases (`wf.llm(...)`, `wf.tool(...)`, `wf.code(...)`, `wf.input_node(...)`, `wf.reduce(...)`, `wf.rag(...)`, `wf.reflection(...)`, `wf.human(...)`, `wf.human_in_the_loop(...)`, `wf.vote(...)`, `wf.ensemble(...)`) emit legacy compute/compatibility node types or canonical `worker` nodes:
- `False` (default): keep emitting legacy public graph shapes
- `True`: emit `worker` nodes directly while preserving the familiar default ports (`text` for `llm`, `result` for `tool` / `code`)

You can also set `DAN_WORKER_BUILDER=disabled|canary|enabled` as a process-wide default. `canary` and `enabled` both currently opt that broader compute-like alias bucket into Worker-native emission; retained control/runtime primitives such as router, validator, gate, loops, and orchestration nodes still stay explicit.

The markdown loader/compiler now honors that same authoring gate for compute-like agent specs. With `DAN_WORKER_BUILDER=canary|enabled`, loaded `llm` / `tool` / `code` / `human` / `reflection` / `vote`-style agents are Workerized while retained control/runtime primitives such as `gate`, `for_each`, `goal_loop`, `parallel_subagents`, `orchestrator`, and `agent_team` stay explicit.

Chat/editor mutation add-node follows the same additive rollout too. With `DAN_WORKER_BUILDER=canary|enabled`, `GraphMutator` now Workerizes the full safe compute-like mutation bucket: `llm_operator`, `tool_operator`, `code_operator`, `rag_operator`, `input`, `reflection`, `human`, `human_in_the_loop`, `vote`, and `reduce`. It preserves their familiar default ports and maps their specialized config onto Worker metadata while leaving router, validator, gate, loop, and orchestration primitives explicit. Plain leaf Workers also no longer get fake empty `body_graph` stubs during mutation add-node.

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

**Relative files:** In server/local DAN runtimes, inline code-node `open("relative.txt")` resolves relative paths against `DAN_WORKSPACE_ROOT`. If the file lives outside the active workspace, pass an absolute path explicitly.

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

### 3j. Input Node

Explicit workflow entry variables. Each declared variable becomes an output port, and the node also exposes an aggregate `input` output for convenient whole-payload wiring.

```python
node = wf.input_node(
    "workflow_inputs",
    variables=[
        {"name": "topic", "type": "string", "description": "Research topic"},
        {"name": "max_rounds", "type": "number", "default": 3},
    ],
)
```

**Default output port:** `input` (aggregate payload). Variable-specific ports are also available, e.g. `workflow_inputs["topic"]`.

### 3k. Human

Canonical human interaction node. Use this for richer render modes and typed I/O. `wf.human_in_the_loop()` remains available as a legacy/simple alias.

```python
node = wf.human(
    "approve_draft",
    prompt="Review and approve this draft:",
    render_mode="approval",
    instructions="Approve or request changes.",
    timeout_seconds=3600,                   # optional timeout
    default_action="approve",               # fallback on timeout (optional)
)
```

**Default output port:** `response`.

Markdown agent format:

```yaml
type: human
timeout_seconds: 3600
default_action: approve
render_mode: approval
render_target: both
instructions: Approve or reject the draft.
input_schema:
  type: object
output_schema:
  type: object
  properties:
    approved:
      type: boolean
```

Legacy alias:

```python
node = wf.human_in_the_loop(
    "approve_draft",
    prompt="Review and approve this draft:",
)
```

Convenience aliases:

```python
node = wf.approval("approve_draft", prompt="Review and approve this draft:")
node = wf.form(
    "intake_form",
    schema={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    prompt="Fill out the intake form.",
)
```

### 3l. Vote

Runs the same task through multiple candidates and chooses a winner using a configured voting strategy.

```python
node = wf.vote(
    "choose_best",
    prompt="Pick the best answer for the user.",
    candidates=["claude-sonnet-4-6", "gpt-4o"],
    num_votes=2,
    strategy="judge",
)
```

**Default output port:** `winner`.

Alias:

```python
node = wf.ensemble(
    "choose_best",
    prompt="Pick the best answer for the user.",
    models=["claude-sonnet-4-6", "gpt-4o"],
)
```

Markdown agent format:

```yaml
type: vote
candidates:
  - claude-sonnet-4-6
  - gpt-4o
vote_strategy: judge
judge_model: claude-opus-4
parallelism: 2
```

### 3m. Parallel Subagents

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

### 3n. Orchestrator

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

### 3o. Agent Team

Group-chat style multi-agent coordination. Unlike `orchestrator`, team members address each other as peers and the runtime manages turn-taking, handoffs, and shared conversation state.

```python
with wf.team(
    "review_team",
    moderator_prompt="Coordinate the specialists.",
    turn_strategy="free_form",
    shared_context_keys=["conversation_history"],
) as team:
    with team.agent("researcher") as sub:
        sub.llm("research", prompt="Research the topic.")
    with team.agent("writer") as sub:
        sub.llm("write", prompt="Draft the answer.")
```

Alias:

```python
with wf.group_chat("review_team") as team:
    ...
```

**Default output port:** `result`.

Markdown agent format:

```yaml
type: agent_team
moderator_prompt: Coordinate the specialists.
turn_strategy: free_form
shared_context_keys:
  - conversation_history
```

```markdown
## Agents

- [researcher](researcher.md)
- [writer](writer.md)
```

### 3p. Worker

Lightweight universal compute/contract node. Use this when you want one node shape that can act as pass-through, tool runner, code runner, LLM worker, composite body owner, or sub-worker coordinator without introducing a new legacy node type.

```python
node = wf.worker(
    "reviewer",
    role="reviewer",
    instruction="Check the draft for completeness and evidence gaps.",
    model="claude-sonnet-4-6",
    llm={"prompt_template": "Review:\n{input}"},
    context={
        "instruction_profile_ref": "review_profile",
        "toolset_refs": ["analysis_tools"],
    },
    execution={
        "resource_locks": ["shared_review_budget"],
        "blocking_mode": "exclusive",
    },
    input_ports=[{"name": "input", "required": False}],
    output_ports=[{"name": "text"}],
)
```

Useful fields:

| Field | Type | Purpose |
|---|---|---|
| `role` / `instruction` / `persona` | `str` | Short contract/behavior metadata |
| `model` | `str \| None` | LLM model ref |
| `tool_ids` | `list[str]` | Direct tool refs |
| `tool_config` | `dict` | Static args for direct-tool Workers; stored in `Worker.metadata["tool_config"]` |
| `code` / `language` | `str` | Inline deterministic execution |
| `llm` / `llm_hints` | `dict` | Prompt template, system prompt, temperature, output schema, tools |
| `context` | `dict` | Shared refs such as `instruction_profile_ref`, `memory_policy_ref`, `context_bundle_refs`, `toolset_refs`, `provider_policy_ref`, `retry_policy_ref` |
| `authority_policy` | `dict` | Delegation / tier caps |
| `execution` | `dict` | Locks and async coordination hints |
| `control_flow` | `dict` | Lightweight gate-style routing contract (`condition`, `gate_mode`, `max_iterations`, optional loop feedback fields) |
| `body_graph` / `sub_workers` | internal refs | Usually authored via `wf.worker_scope(...)` |
| `input_mappings` / `output_mappings` | `dict[str, str]` | Outer Worker port → inner `body_graph` entry / exit port mapping |
| `parallelism` | `int` | Max concurrent named `sub_workers` when the Worker delegates |
| `merge_strategy` | `str` | Sub-worker fan-in policy (`append`, `last_write_wins`, `reducer`; `reducer` currently expects `metadata["merge_reducer"]`) |
| `spawn_policy` | `dict` | Additional delegation caps such as `max_spawns_per_node` |
| `validation_rules` | `list[dict]` | First-class validator rules for Worker-shaped validation |

Graph-level shared refs for Workers:

```python
wf.resource("instruction_profiles", "review_profile", {"instruction": "Stay skeptical and concrete."})
wf.resource("toolsets", "analysis_tools", {"tool_ids": ["file_read", "web_search"]})
```

Workflow-generation rollout note:
- `DAN_WORKER_GENERATION=disabled|canary|enabled` lets the lightweight planner/spec and deterministic intent-compiler paths prefer Worker-native simple compute stages while leaving legacy generation and specialized control primitives intact.
- `wf.llm(...)`, `wf.tool(...)`, and `wf.code(...)` remain stable public convenience methods, but simple compute aliases are now internally normalized through the Worker contract surface before projecting back to legacy node types for compatibility. That means new Worker-first contract fixes can benefit both direct `wf.worker(...)` authoring and the classic compute aliases without changing their user-facing signatures.
- `wf.worker(..., control_flow={...})` is the lightweight Worker-facing routing surface. At runtime it still delegates to the specialized `gate` executor rather than pretending branch/while semantics are generic LLM/tool behavior.
- `wf.worker(..., validation_rules=[...])` is now the first-class Worker-facing validator surface. Runtime still delegates through the retained validator executor where that is the honest compatibility path.

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

Optional lint config lives first-class on `DataEdge.lint` and is preserved through build/decompile round-trip. Legacy `metadata["lint"]` is still accepted and mirrored for compatibility:

```python
wf.edge(
    writer["result"],
    reviewer["input"],
    lint={
        "enabled": True,
        "structural": {
            "required_keys": ["draft"],
            "string_max_lengths": {"draft": 4000},
            "ranges": {"confidence": {"minimum": 0, "maximum": 1}},
            "format_patterns": {"ticket_id": r"[A-Z]{3}-\d{4}"},
        },
        "semantic": {
            "topic_keywords": ["finance", "summary", "recommendation"],
            "min_keyword_ratio": 0.67,
            "required_entities": ["Acme"],
            "entity_match_mode": "fuzzy",
            "entity_fuzzy_threshold": 0.85,
            "expected_language": "en",
            "contradiction_reference_text": "Acme revenue decreased to 8% this quarter.",
            "contradiction_min_claim_overlap": 0.6,
            "confidence_aggregation": "min",
        },
        "severity": "error",
        "autofix": ["fill_defaults", "truncate", "clamp", "coerce"],
        "retry_budget_ms": 2500,
    },
)
```

`enabled` defaults to `True`. Set it to `False` if you want to keep a resolved lint contract attached to an edge but temporarily disable enforcement without deleting the config.

When `lint_autogen` is enabled and no explicit `lint=` is supplied, the builder/compiler can conservatively auto-generate `DataEdge.lint` from the source/target contracts and mirror it into legacy metadata. That autogen currently understands schema `required`, `maxLength`, numeric `minimum` / `maximum`, and string `pattern` constraints. Tier 2 semantic lint also supports `min_keyword_ratio` for partial keyword coverage, configurable entity matching (`exact`, `fuzzy`, or `embedding`), lightweight `expected_language` checks, conservative contradiction detection via `contradiction_reference_text`, and `confidence_aggregation` (`min` or `mean`) for Tier 3 escalation. If `reference_text` is omitted but `topic_keywords` are present, the semantic similarity path uses those keywords as the fallback reference text. Language mismatches are warning-only diagnostics, and contradiction detection intentionally only catches obvious same-subject numeric / negation / polarity conflicts. Deterministic structural autofix now supports `fill_defaults`, `truncate`, `clamp`, and `coerce`, with iterative re-lint so composed cases like string-number coercion followed by range clamp can settle in one lint pass. Model-backed retry loops are bounded by both `max_retries` and optional `retry_budget_ms` across the whole handoff retry cycle. Explicit `lint=` always wins.

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

### 5g. Worker Scope

Author a Worker-owned body graph and optional named sub-worker graphs without hand-writing `body_graph` keys.

```python
with wf.worker_scope(
    "manager",
    role="manager",
    instruction="Plan locally, then delegate parallel research.",
    model="claude-sonnet-4-6",
    llm={"prompt_template": "Plan from {input}"},
    input_mappings={"brief": "planner::input"},
    output_mappings={"text": "final_text"},
    parallelism=2,
    merge_strategy="last_write_wins",
    spawn_policy={"max_spawns_per_node": 2},
) as manager:
    planner = manager.code("planner", code="result = {'plan': 'draft'}")
    reviewer = manager.worker(
        "reviewer",
        role="reviewer",
        validation_rules=[{"rule_type": "required_keys", "config": {"keys": ["plan"]}}],
        input_ports=[{"name": "input", "required": False}],
    )
    manager.edge(
        planner["result"],
        reviewer["input"],
        lint={"structural": {"required_keys": ["plan"]}, "severity": "error"},
    )

    with manager.sub_worker("research") as research:
        research.code("collect", code="result = {'notes': ['a', 'b']}")
```

Notes:
- Inside the scope, use `.input` / `.item` or the explicit aliases `.entry_input` / `.entry_item`.
- `manager.sub_worker("name")` creates a named subgraph recorded in `Worker.sub_workers`.
- `input_mappings` / `output_mappings` apply to the Worker's `body_graph`; `parallelism`, `merge_strategy`, and `spawn_policy` apply when the Worker delegates to named `sub_workers`.
- The decompiler emits `with wf.worker_scope(...):` for Workers that own a `body_graph` or named `sub_workers`.
- `decompile(graph)` stays canonical and Worker-preserving by default. `decompile(graph, use_convenience_aliases=True)` may lower simple leaf Workers to familiar aliases such as `wf.llm(...)`, `wf.tool(...)`, `wf.code(...)`, `wf.rag(...)`, `wf.input_node(...)`, `wf.reflection(...)`, `wf.human(...)`, `wf.human_in_the_loop(...)`, or `wf.vote(...)` for readability.

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

**Event types:** `run_started`, `run_completed`, `run_failed`, `automatic_recovery_started`, `automatic_recovery_completed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`, `llm_thinking`, `tool_call_started`, `tool_call_result`, `code_output`, `intermediate_text`, `token_budget_advisory`, `context_deferred`, `input_summarized`, `jit_schema_loaded`, `payload_pruned`, `context_tool_called`, `cache_hit`, `cache_miss`, `cache_invalidated`, `semantic_cache_hit`, `state_externalized`, `loop_compaction_applied`, `budget_advisory`, `token_breakdown_recorded`, `waste_detected`, `optimization_report_ready`, `optimization_applied`, `model_selected`, `tier_escalation`.

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

## 7d. Task-Level Model Tiering (`TierPolicy`)

Automatic model selection based on per-task scoring. Instead of using the same model everywhere (or pinning models per node), `TierPolicy` scores each LLM-using node on three dimensions and maps the result to one of four provider-agnostic tiers.

### Tiers

| Tier | Score Range | Anthropic Default | OpenAI Default | Google Default | Character |
|---|---|---|---|---|---|
| **micro** (L0) | `[0, 0.25)` | `claude-3-5-haiku-20241022` | `gpt-4o-mini` | `gemini-2.0-flash` | Cheap, fast |
| **routine** (L1) | `[0.25, 0.50)` | `claude-sonnet-4-6` | `gpt-4o` | `gemini-2.0-flash` | Standard |
| **reasoning** (L2) | `[0.50, 0.75)` | `claude-opus-4` | `o3-mini` | `gemini-2.5-pro` | Expensive, strong |
| **critical** (L3) | `[0.75, 1.0]` | `claude-opus-4` | `o3` | `gemini-2.5-pro` | Most expensive |

### Enabling TierPolicy

Set `default_model_policy` on `EngineConfig`:

```python
from dan.engine import Engine, EngineConfig
from dan.providers.model_policy import TierPolicy

engine = Engine(
    config=EngineConfig(
        llm_api_key="your-key",
        providers={"anthropic": ProviderConfig(api_key="sk-ant-...")},
        default_model_policy=TierPolicy(),  # auto-scoring with default weights
    ),
)
```

Nodes that set an explicit `model` field bypass tiering (resolved as `StaticPolicy`). Nodes with no model and no per-node `model_policy` inherit the engine-level `TierPolicy`.

### How Scoring Works

Each LLM-using node is scored on three orthogonal dimensions:

| Dimension | Weight | What It Measures |
|---|---|---|
| **Difficulty** | `0.45` | How hard the reasoning task is (node type base + prompt length + output schema complexity + tool count) |
| **Impact** | `0.35` | How much damage a wrong answer causes (graph topology: fan-out, feeds-human, terminal position) |
| **Recoverability** | `0.20` | How well errors can be caught (retry policy, output schema validation, loop body, downstream validators, cascade fallback) |

**Composite score:** `tier_score = difficulty × 0.45 + impact × 0.35 + (1 − recoverability) × 0.20`

The score maps to a tier via `TaskTier.from_score(tier_score)`.

### Custom Weights

```python
from dan.providers.model_policy import TierPolicy, TierWeights

TierPolicy(
    weights=TierWeights(difficulty=0.50, impact=0.30, recoverability=0.20),
)
```

Weights must sum to 1.0.

### Explicit Tier Override (`task_tier`)

Pin a minimum tier on any LLM-using node:

```python
node = wf.llm(
    "critical_analysis",
    prompt="Analyze: {data}",
    task_tier="reasoning",  # floor — scoring can only promote, never demote
)
```

Supported on: `LLMOperator`, `ReflectionNode`, `RouterNode`, `OrchestratorNode`, `AgentTeamNode`, `VoteNode`.

### Custom Tier Map

Override the default tier-to-model mapping:

```python
# On TierPolicy directly
TierPolicy(tier_map={
    "micro": "gpt-4o-mini",
    "routine": "gpt-4o",
    "reasoning": "claude-opus-4",
    "critical": "claude-opus-4",
})

# Or on EngineConfig (applies to all TierPolicy nodes)
EngineConfig(
    default_model_policy=TierPolicy(),
    tier_map={
        "micro": "gemini-2.0-flash",
        "routine": "claude-sonnet-4-6",
        "reasoning": "claude-opus-4",
        "critical": "claude-opus-4",
    },
)
```

Partial overrides are merged on top of the auto-detected provider defaults (preference order: anthropic > openai > google).

### Per-Tier Parameter Overrides (`tier_params`)

Differentiate tiers that share a model (e.g., Anthropic reasoning/critical both use Opus) with call-time parameter overrides:

```python
EngineConfig(
    default_model_policy=TierPolicy(),
    tier_params={
        "critical": {"extended_thinking": True, "max_tokens": 8192},
        "micro": {"temperature": 0.3, "max_tokens": 1024},
    },
)
```

Built-in default: Anthropic `critical` tier adds `extended_thinking=True, max_tokens=8192`.

### Escalation Behavior

When output normalization fails (schema validation exhausted), the engine auto-escalates to the next-higher tier:

1. Node is scored as `routine` → model call fails normalization after all retries
2. Engine bumps to `reasoning` tier, resolves the new model, and retries once
3. Maximum one escalation per node execution

Emits a `tier_escalation` event with `from_tier`, `to_tier`, `from_model`, `to_model`, and `reason`.

### Events

| Event Type | Data Fields | When |
|---|---|---|
| `model_selected` | `model`, `policy_strategy`, `tier`, `tier_score`, `difficulty`, `impact`, `recoverability` | After tier scoring resolves a model |
| `tier_escalation` | `from_tier`, `to_tier`, `from_model`, `to_model`, `reason` | On auto-escalation after normalization failure |

### EngineConfig Fields (Tiering)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `default_model_policy` | `ModelPolicy \| None` | `None` | Set to `TierPolicy()` to enable auto-tiering |
| `tier_map` | `dict[str, str] \| None` | `None` | Custom tier→model mapping (merged with provider defaults) |
| `tier_params` | `dict[str, dict] \| None` | `None` | Per-tier LLM parameter overrides (e.g. `extended_thinking`) |

> **Note — Concierge stage-based tiering:** The concierge pipeline also uses `DAN_TIER_MAP` for model selection, but via explicit stage-to-tier mapping (`CONCIERGE_STAGE_TIERS` in `concierge/tiering.py`) rather than the dynamic scoring used by `TierPolicy`. Both systems share the same normalized tier map produced by `normalize_tier_map()`. See `docs/architecture.md` § Concierge Runtime for stage assignments.

---

## 7e. Built-in Tools (`dan.tools`)

DAN ships 32 batteries-included tools, auto-registered during server startup. Each tool module exports a `TOOL_METADATA` dict and an async callable.

### Tool Categories

| Category | Tools | Description |
|---|---|---|
| **System** | `current_datetime`, `clipboard`, `python_eval`, `notify` | Time, clipboard, sandboxed code eval, notifications |
| **File I/O** | `file_read`, `file_write`, `list_directory`, `file_move`, `file_copy`, `file_delete` | Workspace-sandboxed file operations |
| **Data** | `csv_read`, `spreadsheet_read` | CSV/TSV parsing, Excel (.xlsx) reading |
| **Web** | `web_search`, `web_fetch`, `http_request` | Tavily/Brave/DuckDuckGo search, URL fetch with optional browser-backed recovery, general HTTP |
| **Shell** | `shell_command` | Subprocess with timeout and allowlist |
| **Document** | `pdf_read` | PDF: `mode=text` (extract text) or `mode=vision` (vision LLM per page — figures, tables) |
| **Text Processing** | `text_chunk`, `json_extract`, `regex_match`, `text_diff`, `text_translate` | Chunking, dot-notation extraction, regex, unified diff, LLM translation |
| **Git** | `git_status`, `git_diff`, `git_log`, `git_commit`, `git_branch`, `git_worktree` | Safe git operations (no force-push/hard-reset) |
| **Media** | `image_describe`, `audio_transcribe` | Vision LLM image description, Whisper transcription |
| **Communication** | `send_email`, `telegram_poll` | SMTP email via aiosmtplib; native Telegram polls (2-10 options, anonymous/multiple toggles) |
| **Archive** | `compress` | Create zip/tar.gz archives |

`pdf_read` parameters: `path` (required), optional `mode`, `start_page`, `end_page`, `vision_model`, and `vision_prompt`. In `mode="vision"`, the tool reports `pages_requested`, `pages_returned`, `truncated`, and `warning` so callers can tell when a long PDF was capped to the first 25 pages.

`list_directory` parameters: `path` (required), optional `glob_pattern`, `recursive`, `limit`, and `start_after`. Results are sorted by relative path and report page metadata: `count` (entries returned in this page), `total_count` (entries matching the current filter/cursor), `remaining_count`, `truncated`, and `next_start_after`. When `truncated=true`, callers should continue with `start_after=next_start_after` or narrow the listing with `glob_pattern` instead of inferring that later entries are absent.

`web_fetch` accepts `url` plus optional `browser_fallback`. When enabled, the tool retries through DAN's persistent Playwright browser if the plain HTTP fetch fails or only returns a short JavaScript/cookie/challenge shell. Returned metadata now includes `fetch_via` (`"http"` or `"browser"`), `browser_fallback_used`, and `content_requires_browser`. The conversation-layer `web_search` / `web_fetch` capability handlers expose the same `browser_fallback` flag and `SearchResultSet.browser_fallback_count` so callers can tell when grounding depended on browser-rendered content.

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
registry.register_builtin_tools()  # registers all 32 built-in tools

# Add custom tools (override built-in IDs or add new ones)
async def my_tool(query: str) -> dict:
    return {"result": f"processed {query}"}

registry.register("my_custom_tool", my_tool)
```

### Tool Metadata Pattern

Each tool module follows the same pattern — export `TOOL_METADATA` dict with keys: `tool_id`, `description`, `parameters` (JSON Schema), `examples`, `category`, `returns`. The `get_all_tools()` function in `dan.tools` auto-discovers all modules and returns `{tool_id: (function, metadata)}`.

### Workspace Sandboxing

File tools (`file_read`, `file_write`, `list_directory`, `file_move`, `file_copy`, `file_delete`) enforce `DAN_WORKSPACE_ROOT` for relative paths. Relative paths that escape the workspace are rejected. Explicit absolute paths (including `~/...`) are treated as trusted paths and may operate outside the workspace root. Defaults to the current working directory. If you are exposing DAN to untrusted LLM callers, restrict requests to relative paths or add an approval/policy layer before allowing absolute paths.

### Graceful Degradation

Optional-dependency tools raise clear errors when their SDK/config is missing (`web_search` needs `duckduckgo-search`; `pdf_read` text mode needs `pypdf`; `pdf_read` vision mode also needs `pymupdf`, `openai`, and an API key; `spreadsheet_read` needs `openpyxl`; `audio_transcribe`/`image_describe` need `openai`). Missing optional deps do not break unrelated tools.

### Workflow Catalog Tools (Capability-Level)

These are chat-mode capability tools (not workflow `ToolNode` tools) for browsing and managing saved workflows:

| Tool | Description |
|---|---|
| `list_my_workflows` | List all saved workflows with names, descriptions, and sizes |
| `search_workflows(query)` | Keyword search over saved workflow names and descriptions |
| `show_workflow(workflow_id)` | Display workflow structure as ASCII DAG diagram |
| `fork_workflow(workflow_id, new_name?)` | Duplicate a workflow as a starting point for adaptation |

Available in all chat modes. Registered via `register_workflow_catalog_capabilities()`.

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

## 9b. Streaming Response Protocol

After `POST /api/chat/message` returns `{ "stream_channel_id": "chat-abc123" }`, connect to:

```
ws://<host>/api/chat/<stream_channel_id>/events
```

### Event Types

| Event type | Semantics | Key fields |
|---|---|---|
| `chat_token` | Incremental text token | `delta: str`, `accumulated: str` |
| `chat_complete` | Terminal event — final response | `content: str`, `detected_mode: str` |
| `chat_notice` | Non-terminal advisory/notification | `content: str`, `level: str` |
| `chat_queued` | Request queued; reconnect to new channel | `stream_channel_id: str`, `queue_position: int` |
| `chat_error` | Terminal error | `error: str` |
| `chat_interrupted` | Generation was cancelled | `content: str` (partial) |
| `chat_tool_call_start` | Tool invocation began | `tool_name: str` |
| `chat_tool_call_result` | Tool result summary | `tool_call_id: str`, `tool_name: str`, `status: str`, `output_preview: str`, `duration_ms: int` |
| `chat_file_attachment` | File artifact delivered | `path: str` |
| `chat_poll_request` | Poll for user | `question: str`, `options: list[str]` |
| `chat_mutation` | Graph mutation proposed | `mutation_plan: dict` |

### Lifecycle

1. Open WebSocket to `/api/chat/<channel_id>/events`
2. Receive `chat_token` events (if streaming) or go directly to terminal event
3. On `chat_queued`: close current socket, reconnect to `stream_channel_id` from payload
4. On `chat_notice`: surface the warning/notice to the user, but keep listening
5. On `chat_complete` / `chat_error` / `chat_interrupted` / terminal `chat_mutation`: stream is done, close socket
6. A `chat_complete` with `detected_mode: "progress_ack"` is a keepalive — continue listening

---

## 10. Type Reference

### Taxonomy Policy

For LLM callers, the important distinction is:

- **Canonical runtime node kinds** are the `node_type` values stored in `dan_graph_v1` and supported by the runtime graph union in `src/dan/models/graph.py`.
- **Deprecated aliases** are still accepted for compatibility, but new authored graphs should prefer the canonical spelling.
- **Authoring pseudo-types** are surface-level conveniences and should not be emitted as `node_type` values in final graph JSON.
- **Macros/templates** are higher-level authoring constructs that lower into one or more canonical runtime primitives.

Current policy highlights:

- Prefer **`gate`** over legacy **`if_else`** for new branching nodes.
- Prefer **`human`** over legacy **`human_in_the_loop`** for new human-interaction nodes when richer human-node semantics are desired.
- Treat editor palette labels like **`gate_if_else`** / **`gate_while`** as authoring-only pseudo-types that lower to canonical `gate` nodes.
- Planner **`GENERATE`** is intentionally the lightweight path: it only targets the reduced simple subset (`llm_operator`, `tool_operator`, `code_operator`, `gate`) instead of the full runtime union.
- Chat mutation operates on canonical runtime node kinds, while markdown import/export remains an explicitly narrower surface that warns when a runtime shape cannot be represented faithfully.

### Node Types

| `node_type` string | Builder method | Default output port | Purpose |
|---|---|---|---|
| `llm_operator` | `wf.llm()` | `text` | LLM call |
| `code_operator` | `wf.code()` | `result` | Python execution |
| `tool_operator` | `wf.tool()` | `result` | Registered function call |
| `if_else` | `wf.if_else()` | `branch` | Legacy branch node; prefer `gate` for new graphs |
| `gate` (if_else mode) | `wf.gate()` | `true`, `false` | Conditional routing (replaces `if_else`) |
| `gate` (while mode) | `wf.gate()` | `continue`, `done` | Iterative loop (replaces `while_loop`) |
| `while_loop` | `wf.while_loop()` | `result` | Body-subgraph loop container; still runtime-supported alongside `gate` (while mode) |
| `for_each` | `wf.for_each()` | `results` | Parallel fan-out over list |
| `parallel_subagents` | `wf.parallel_subagents()` | `results` | Heterogeneous parallel branches |
| `orchestrator` | `wf.orchestrator()` | `results` | Async orchestrator with concurrent teams |
| `goal_loop` | `wf.goal_loop()` | `result` | Goal-oriented loop over a body sub-graph |
| `composite` | `wf.composite()` | *(declared)* | Sub-graph |
| `reduce` | `wf.reduce()` | `result` | Fan-in aggregation |
| `router` | `wf.router()` | `route` | LLM-powered routing |
| `human` | `wf.human()` | `response` | Canonical human interaction node |
| `human_in_the_loop` | `wf.human_in_the_loop()` | `response` | Legacy/simple human alias |
| `vote` | `wf.vote()` | `winner` | Voting / ensemble selection |
| `rag_operator` | `wf.rag()` | `chunks` | Vector-store retrieval |
| `validator` | `wf.validator()` | `valid` | Data validation with rule routing (`data` input, `invalid` failure branch) |
| `reflection` | `wf.reflection()` | `principles` | Post-run analysis, distills errors into causal principles |
| `input` | `wf.input_node()` | `input` | Explicit workflow entry variables (plus variable-specific output ports) |
| `agent_team` | `wf.team()` / `wf.group_chat()` | `result` | Group-chat style multi-agent coordination |

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
| `pass_by_reference_threshold_tokens` | `int` | `2000` | When `pass_by_reference=True` on ContextEdge, only store ref in ArtifactStore if value exceeds this token count |
| `cache_enabled` | `bool` | `True` | Enable memoization + semantic cache lookups |
| `cache_max_size_mb` | `int` | `100` | Max in-memory cache size before LRU eviction |
| `cache_dir` | `str \| None` | `None` | Optional persistent cache directory |
| `semantic_cache_threshold` | `float` | `0.95` | Similarity threshold for semantic cache hits |
| `semantic_cache_ttl_hours` | `float` | `24.0` | Semantic cache entry TTL |
| `prompt_caching_enabled` | `bool` | `True` | Enable provider-level prompt cache hints |
| `default_model_policy` | `ModelPolicy \| None` | `None` | Engine-wide model selection policy; set to `TierPolicy()` for auto-tiering |
| `tier_map` | `dict[str, str] \| None` | `None` | Custom tier→model mapping for `TierPolicy` (merged with provider defaults) |
| `tier_params` | `dict[str, dict] \| None` | `None` | Per-tier LLM call parameter overrides (e.g. `{"critical": {"extended_thinking": true}}`) |
| `optimization_rule_approval_mode` | `str` | `"always_approve"` | `"always_approve"` (human gate) or `"auto_accept"` (activate by default) for evolving optimization rules |
| `embedding_providers` | `dict[str, ProviderConfig]` | `{}` | Named embedding providers for `RAGOperator` (e.g., `default`, `openai`, `local`) |

When `runtime_self_healing_enabled=True` and checkpointing is available, failed runs may also surface `RunResult.metadata["automatic_recovery"]` with one bounded post-run recovery candidate (currently a checkpoint rerun) that `RunManager` can continue automatically. This is runtime metadata, not a new node type.

Workflow generation chat events may also carry `automatic_recovery` on `chat_generation_summary` and terminal `chat_validation_result` events when bounded post-diagnosis auto-repair runs. The shared envelope uses the same vocabulary across generation/runtime: `scope`, `workflow_id`, `run_id`, `node_id`, `failure_summary`, `attempted_fixes`, `available_actions`, `attempt_index`, `max_attempts`, `recovery_budget_remaining`, `last_action`, and `last_outcome`. Exhausted recovery payloads may also include `escalation_needed`, `escalation_summary`, and `recommended_actions`.

When runtime automatic recovery is active, parent run streams may emit `run_failed` followed by `automatic_recovery_started` and later `automatic_recovery_completed`. Treat the initial `run_failed` as provisional until the bounded recovery sequence finishes. Parent run streams may also include mirrored child rerun node/tool events carrying `data.automatic_recovery=true` plus `recovery_run_id` / `recovery_parent_run_id`, so clients can show the recovery rerun's actual progress without switching streams.
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

## 11b. Builder Convenience Methods (Phase 32)

High-level builder methods that reduce boilerplate for common workflow patterns. Each compiles down to the same graph primitives — they're syntactic sugar, not new node types.

### `wf.chain(*steps) → NodeRef`

Linear sequence of LLM nodes with auto-wiring. Each step is a `(name, prompt)` tuple or `(name, prompt, model)` triple.

```python
wf = workflow("my_chain")
result = wf.chain(
    ("research", "Research {topic}"),
    ("analyze", "Analyze the research findings"),
    ("summarize", "Write a concise summary"),
)
graph = wf.build()
```

Returns the last node's `NodeRef`. `{topic}` in the first step is auto-detected as a graph-level input variable.

### `wf.branch(condition, then_prompt, else_prompt, ...) → tuple[NodeRef, NodeRef, NodeRef]`

Conditional if/else with two LLM branches. Compiles to a `gate` node (`gate_mode="if_else"`) + then-branch LLM + else-branch LLM.

```python
wf = workflow("sentiment_routing")
gate_ref, then_ref, else_ref = wf.branch(
    condition="sentiment > 0.5",
    then_prompt="Summarize the positive findings",
    else_prompt="Draft an alert about negative sentiment",
    name="sentiment_check",
)
upstream >> gate_ref  # wire upstream into the gate
then_ref >> wf.llm("publish", prompt="Format for publishing")
graph = wf.build()
```

Returns `(gate_ref, then_ref, else_ref)` — wire upstream into `gate_ref`, downstream from `then_ref`/`else_ref`. Optional `name`, `then_model`, `else_model` kwargs.

### `wf.review_loop(writer_prompt, reviewer_prompt, ...) → NodeRef`

Writer-reviewer loop with gate in one call. Supports `>>` chaining — the returned NodeRef uses `draft` as both default input and output port.

```python
wf = workflow("reviewed_draft")
result = wf.chain(("research", "Research {topic}"), ("outline", "Create outline"))
loop = wf.review_loop(
    writer_prompt="Write a blog post based on the outline",
    reviewer_prompt="Review this draft for clarity and completeness",
    max_rounds=3,
)
result >> loop  # upstream output wires to loop's 'draft' input
graph = wf.build()
```

Internally creates: writer LLM → reviewer LLM inside a while_loop with `state_defaults` for self-contained initialization. All loop input ports are optional — works standalone or chained. Optional `name`, `writer_model`, `reviewer_model` kwargs.

Custom review criteria (optional — defaults preserve score-based behavior):

```python
draft = wf.review_loop(
    writer_prompt="Write a draft with citations",
    reviewer_prompt="Verify all citations are real",
    condition="citations_valid == true",
    review_fields={"citations_valid": {"type": "boolean"}, "issues": {"type": "string"}},
    feedback_key="issues",
)
```

- `condition` — loop condition (default: `"quality_score < 8"`).
- `review_fields` — reviewer output schema fields (default: `{"quality_score": {"type": "integer"}, "feedback": {"type": "string"}}`).
- `feedback_key` — which review field feeds back to the writer (default: `"feedback"`).

### `wf.map_reduce(items_expr, map_prompt, reduce_prompt, ...) → NodeRef`

Fan-out over items with parallel processing and aggregation.

```python
wf = workflow("parallel_analysis")
result = wf.map_reduce(
    items_expr="{documents}",
    map_prompt="Summarize this document",
    reduce_prompt="Synthesize all summaries into a unified report",
)
graph = wf.build()
```

Internally creates: for_each(map LLM) → reduce LLM. Optional `name`, `map_model`, `reduce_model` kwargs.

### `wf.tool_chain(*steps) → NodeRef`

Chain mixing LLM and tool nodes. Each step is `(name, tool_id_or_None, config_or_prompt)`. `tool_id=None` creates an LLM node.

```python
wf = workflow("tool_pipeline")
result = wf.tool_chain(
    ("search", "web_search", {"query": "{topic}"}),
    ("analyze", None, "Analyze these search results"),
    ("save", "file_write", {"path": "report.md"}),
)
graph = wf.build()
```

### Pipeline `|` Operator

`|` is an alias for `>>` that reads more naturally for linear pipelines:

```python
search = wf.tool("search", tool_id="web_search")
analyze = wf.llm("analyze", prompt="Analyze findings")
report = wf.llm("report", prompt="Write report")
search | analyze | report  # equivalent to search >> analyze >> report
```

### Structural Mutation Macros (Progressive Refinement)

Six macros in `dan.meta.structural_mutations` for modifying existing graphs without full rebuild:

| Macro | Purpose |
|-------|---------|
| `wrap_in_review_loop(graph, node_id, reviewer_prompt, max_rounds)` | Insert reviewer + gate after target node |
| `fan_out_node(graph, node_id, items_expr)` | Wrap target node in for_each + reduce |
| `insert_validator(graph, source_id, target_id, rules)` | Insert validator between two nodes |
| `insert_tool(graph, anchor_id, tool_id, config, position)` | Insert tool node before/after anchor |
| `parallelize(graph, node_ids)` | Wrap listed nodes in parallel_subagents |
| `unwrap_loop(graph, loop_node_id)` | Remove loop structure, straighten pipeline |

`resolve_node(graph, reference_text)` resolves natural-language node references (exact name, fuzzy match, prompt keyword, position).
`summarize_graph(graph)` produces a compact text summary for codegen context injection.

**Compound dispatch** — `dispatch_compound_mutations(graph, user_text)` extracts and applies *all* non-overlapping macro matches from one message (e.g., "add a review loop and fan out the research step" applies both macros). Executes sequentially with message-level atomic rollback: if any macro fails, the entire compound mutation reverts. Single-macro messages delegate to the original `dispatch_structural_mutation()` for backward compatibility.

### Domain Generation Profiles

Per-domain configuration bundles loaded from `src/dan/data/generation_profiles/`. Five seed profiles: `literature_review`, `paper_rendering`, `equity_research`, `data_analysis`, `code_generation`. Each specifies preferred tools, patterns, model tier hints, and prompt guidance. User overrides at `~/.dan/generation_profiles/` take precedence.

```python
from dan.meta.generation_defaults import get_domain_profile

profile = get_domain_profile("equity_research")
# DomainGenerationProfile with preferred_tools, preferred_patterns, model_tier_hints, etc.
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
  "mode": "build",
  "history": [],
  "surface_type": "web",
  "surface_id": "editor-session-1",
  "session_id": "chat-thread-1",
  "surface_context": {
    "identity": {
      "name": "web-assistant",
      "role": "workspace_assistant"
    }
  },
  "client_graph_revision": null
}
```

Legacy clients may still send `surface` and `thread_id`, but new frontends should prefer `surface_type` / `surface_id` / `session_id` and keep `history` limited to `user` / `assistant` turns. When both `session_id` and `thread_id` are present, they may intentionally differ: `session_id` is the narrower execution/session lane, while `thread_id` can carry a broader conversation identity. If one is omitted, the server fills it from the other for backward compatibility.

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

**Build-and-Run shortcut:** When the user asks to build AND run/test in one request, set `auto_apply: true` on `plan_graph_mutations`. If the dry-run passes, the server applies the mutation automatically, saves the graph, and continues the tool loop — the LLM can then call `start_run` immediately in the same turn. `ChatMutationEvent` will have `applied=true`. Omit `auto_apply` (default `false`) for preview-only requests.

**Mechanical alias repair:** Chat-side mutation-preview compilation now rewrites a small set of known stale source-port aliases before dry-run when the node/tool shape makes the replacement unambiguous, for example `http_request.response -> body`. LLM callers should still prefer canonical tool-manifest ports (`status_code`, `headers`, `body`, `result` for `http_request`) instead of relying on repair.

Use `strict=true` in `add_edge` operations when building from intent to fail fast on port typos.
The backend now tolerates two common harmless mutation drifts during dry-run: `remove_node` on an already-missing id is treated as an idempotent no-op, and if a source node exposes exactly one output port the mutator may normalize a guessed source port onto that sole declared port (for example `for_each.item -> results` or `code_operator.output -> result`). Still prefer canonical port names in generated plans.
When building control-flow nodes such as `for_each` or `composite` from chat, add the node first and then use `replace_body_graph` to define its nested body graph. `GraphMutator` now auto-scaffolds the empty `body_graph` entry for those nodes, but the actual body must still be populated explicitly. For `for_each`, remember that `items` / `results` are the top-level node ports; `item` is typically an input port on the body sub-graph's entry node.

### 12g. IntentCompiler — Direct Graph Construction

`IntentCompiler.build_graph(intent, *, domain=None) -> Graph` constructs a `Graph` object directly by calling the builder API in-process — no code string generation or sandbox execution.

```python
from dan.meta.intent_compiler import IntentCompiler, DirectBuildError
from dan.meta.intent_schema import WorkflowIntent, StageIntent, StageType

intent = WorkflowIntent(
    goal="Summarize documents",
    stages=[
        StageIntent(name="summarize", stage_type=StageType.transform, description="Summarize the input"),
    ],
)

compiler = IntentCompiler()
try:
    graph = compiler.build_graph(intent)
    # graph is a validated Graph object ready for Engine.run()
except DirectBuildError as e:
    # Fall back to compile() → sandbox path
    code = compiler.compile(intent)
```

For multi-pattern intents, use `build_graph_composed()`:

```python
graph = compiler.build_graph_composed(intent, ["research_review"])
```

---

## 13. Execution & Observability (Per-Node Logs)

The DAN engine emits a rich stream of events during execution. Every event is strictly associated with its source node (`node_id`) unless it is a workflow-level lifecycle event.

### Event Types
Core execution streams include `run_started`, `run_completed`, `run_failed`, `automatic_recovery_started`, `automatic_recovery_completed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`, `llm_thinking`, `tool_call_started`, `tool_call_result`, `code_output`, and `intermediate_text`.

### Per-Node Tracking
- **Automatic Tagging:** The `Engine` automatically tags every event emitted via `ExecutionContext.emit_event()` with the currently executing `node_id`.
- **Persistence:** The `RunStore` captures the entire event stream into `{run_id}.events.jsonl`, inherently preserving the per-node execution history (tool calls, LLM thinking, standard output).
- **Frontend Subscriptions:**
  - `GET /api/runs/{run_id}/events` provides filtering by `node_id` and `event_type`.
  - The `LogPanel` groups all raw events by `node_id`, ensuring that parallel node outputs are not interleaved but cleanly segregated.
  - The Chat interface derives node progress purely from `node_started`/`node_completed`/`node_failed`/`node_output` events, providing deep-links to the complete per-node history.

---

## 14. Chat Capability Tools

When using `dan-chat` (or the chat API), the LLM has access to a registry of capability tools to inspect, manage, and evolve the system. To access the full suite of file and system tools, set `DAN_FULL_TOOLS=1` in your environment. Without it, only a minimal safe subset is exposed.

### File & System Tools (requires `DAN_FULL_TOOLS=1`)
- `python_eval`: Evaluate arbitrary Python expressions.
- `csv_read`: Read and query CSV files with pandas-like operations.
- `compress`: Create zip/tar archives from directories.
- `file_copy`: Copy files or directories.
- `file_move`: Move or rename files.
- `file_delete`: Delete files securely.
- `notify`: Send cross-channel notifications (desktop bell, webhook).
- `text_diff`: Generate unified diffs between two texts.

### Git Tools (requires `DAN_FULL_TOOLS=1`)
- `git_status`: Get working tree status.
- `git_diff`: View unstaged or staged changes.
- `git_log`: View commit history.
- `git_branch`: List, create, or switch branches.
- `git_commit`: Stage and commit changes.
- `git_worktree`: Manage multiple working trees.

### Introspection & Testing
- `inspect_node`: Read the fully resolved state, metadata, and schemas for a specific node in the current graph.
- `list_test_cases`: Find unit tests for the current workflow.
- `run_test_case`: Execute a specific test case against the workflow and return the trace.

### Configuration
- `get_config`: Read current environment variables and active feature flags (e.g., `DAN_LEARNING_MODE`, `DAN_LLM_MODEL`).
- `set_config`: Update environment variables persistently (writes to `.env` if available).

---

## 15. Import Map

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

# Model policies
from dan.providers.model_policy import (
    ModelPolicy, StaticPolicy, BudgetPolicy, CascadePolicy,
    CapabilityPolicy, RouterPolicy, TierPolicy,
    TaskTier, TierWeights, ModelConstraints,
)
```
