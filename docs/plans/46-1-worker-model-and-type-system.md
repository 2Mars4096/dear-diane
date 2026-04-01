# 46-1: Worker Model & Type System

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Define a clean, well-organized `Worker` model with three clear field groups (identity, capability, composition), optional typed sub-models for execution tuning, and seamless integration into the existing `Node` union.

## Design: Clean Field Organization

The Worker model has three conceptual layers, each clearly separated:

```
┌─────────────────────────────────────────────────────┐
│  IDENTITY — who is this worker?                      │
│  role, persona, authority                            │
├─────────────────────────────────────────────────────┤
│  CAPABILITY — what can it do?                        │
│  model, tool_ids, code, language                     │
├─────────────────────────────────────────────────────┤
│  COMPOSITION — does it manage sub-workers?           │
│  body_graph, sub_workers, contract fields            │
├─────────────────────────────────────────────────────┤
│  OPTIONAL TUNING — execution-mode-specific hints     │
│  llm_hints (temperature, output_schema, tools, ...)  │
│  control_flow (condition, gate_mode, max_iterations) │
└─────────────────────────────────────────────────────┘
```

A bare `Worker(id="w1", name="w1")` is valid — it passes inputs through. Adding fields from any layer activates the corresponding execution mode automatically.

## Tasks

- [ ] 1. Create `src/dan/worker/` module
  - [ ] 1-1. Create `src/dan/worker/__init__.py` — public API exports
  - [ ] 1-2. Create `src/dan/worker/model.py` — all model definitions
- [ ] 2. Define `WorkerAuthority` enum in `model.py`
  - [ ] 2-1. `LEAF` — executes, cannot spawn sub-workers
  - [ ] 2-2. `LEAD` — can spawn sub-workers within bounded `SpawnPolicy`
  - [ ] 2-3. `DIRECTOR` — can restructure topology at runtime
- [ ] 3. Define `LLMHints` sub-model in `model.py`
  - [ ] 3-1. `prompt_template: str = ""` — prompt with `{port}` placeholders
  - [ ] 3-2. `system_prompt: str = ""` — system instructions (overrides `persona` when set explicitly for LLM context)
  - [ ] 3-3. `temperature: float = 0.7`
  - [ ] 3-4. `max_tokens: int | None = None`
  - [ ] 3-5. `output_json_schema: dict | None = None` — structured output
  - [ ] 3-6. `tools: list[dict] = []` — tool schemas in OpenAI function-calling format
  - [ ] 3-7. `max_tool_rounds: int = 10` — safety bound on tool-calling loops
  - [ ] 3-8. `task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = None`
  - [ ] 3-9. `history_policy: HistoryPolicy | None = None`
  - [ ] 3-10. All fields have sensible defaults — the entire sub-model is optional on Worker
- [ ] 4. Define `ControlFlowConfig` sub-model in `model.py`
  - [ ] 4-1. `condition: str` — expression evaluated on inputs
  - [ ] 4-2. `gate_mode: Literal["if_else", "while"] = "if_else"`
  - [ ] 4-3. `max_iterations: int = 10`
  - [ ] 4-4. `feedback_selector: FeedbackSelector | None = None`
- [ ] 5. Define `Worker(NodeBase)` model in `model.py`
  - [ ] 5-1. `node_type: Literal["worker"] = "worker"`
  - [ ] 5-2. **Identity fields:**
    - `role: str = ""` — natural language role label (optional, for readability and preset loading)
    - `persona: str = ""` — system prompt / behavioral instructions
    - `authority: WorkerAuthority = WorkerAuthority.LEAF`
  - [ ] 5-3. **Capability fields:**
    - `model: str | None = None` — LLM model identifier; `None` = no LLM
    - `tool_ids: list[str] = []` — registered tools, resolved from ToolRegistry at runtime
    - `code: str = ""` — inline script for deterministic execution
    - `language: str = "python"` — code language
  - [ ] 5-4. **Composition fields:**
    - `body_graph: str | None = None` — key into `Graph.sub_graphs`
    - `sub_workers: dict[str, str] = {}` — named sub-worker map (name → sub_graph key)
    - `input_mappings: dict[str, str] = {}` — outer_port → inner_entry_port
    - `output_mappings: dict[str, str] = {}` — inner_exit_port → outer_port
    - `parallelism: int = 1` — max concurrent sub-graph branches
    - `merge_strategy: MergeStrategy | None = None` — fan-in strategy
    - `spawn_policy: SpawnPolicy | None = None` — for LEAD/DIRECTOR authority
  - [ ] 5-5. **Composite-node contract** (one copy, not seven):
    - `external_input_schema: dict | None = None`
    - `external_output_schema: dict | None = None`
    - `control_state_schema: dict = {}`
    - `local_state: NodeLocalState`
    - `compaction_rule: CompactionRule | None = None`
    - `failure_policy: FailurePolicy`
    - `projections: list[ContextProjection] = []`
    - `boundary_contract: BoundaryContract | None = None`
  - [ ] 5-6. **Optional tuning sub-models:**
    - `llm: LLMHints | None = None` — LLM-specific config (only when `model` is set)
    - `control_flow: ControlFlowConfig | None = None` — gate/loop config (only when routing)
    - `validation_rules: list[ValidationRule] = []` — validator rules (only when validating)
  - [ ] 5-7. `model_post_init` — seed default ports based on config:
    - If `control_flow` is set with `gate_mode="if_else"`: seed `true`/`false` output ports
    - If `control_flow` is set with `gate_mode="while"`: seed `continue`/`done` output ports
    - If `validation_rules` is non-empty: seed `valid`/`invalid` output ports
    - Otherwise: seed generic `data` input port and `result` output port if none declared
- [ ] 6. Define `__init__.py` public API
  - [ ] 6-1. Export: `Worker`, `WorkerAuthority`, `LLMHints`, `ControlFlowConfig`
  - [ ] 6-2. Export: `role` function (placeholder — implemented in 46-3)
- [ ] 7. Add `Worker` to the `Node` union in `graph.py`
  - [ ] 7-1. Import `Worker` from `dan.worker`
  - [ ] 7-2. Add to the `Union[...]` inside the `Annotated[...]` for `Node`
  - [ ] 7-3. Verify Pydantic discriminator resolution: `{"node_type": "worker", ...}` → `Worker`
- [ ] 8. Update `node_taxonomy.py`
  - [ ] 8-1. Verify `RUNTIME_NODE_TYPE_MAP` picks up `"worker"` automatically
  - [ ] 8-2. Add `"worker"` to `GENERATE_SPEC_NODE_TYPES`
  - [ ] 8-3. Add `"worker"` to `MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES`
  - [ ] 8-4. Verify `SUBGRAPH_BEARING_RUNTIME_NODE_TYPES` picks up Worker (has `body_graph` field)
- [ ] 9. Tests
  - [ ] 9-1. Bare `Worker(id="w", name="w")` is valid, has default `data` input and `result` output ports
  - [ ] 9-2. JSON round-trip: Worker → dict → JSON → dict → Worker for each config variant (LLM, tool, code, composite, gate, validator)
  - [ ] 9-3. `Node` discriminator: `{"node_type": "worker", ...}` deserializes correctly
  - [ ] 9-4. Taxonomy: `"worker"` in `RUNTIME_NODE_TYPES`, `RUNTIME_NODE_TYPE_MAP`
  - [ ] 9-5. Full existing test suite passes unchanged
  - [ ] 9-6. `Worker.model_json_schema()` produces valid JSON Schema
  - [ ] 9-7. `LLMHints` defaults: omitting `llm` field = no LLM execution; setting `llm=LLMHints()` = all defaults

## Likely Files

**New:**
- `src/dan/worker/__init__.py`
- `src/dan/worker/model.py`

**Modified:**
- `src/dan/models/graph.py` — add Worker to Node union
- `src/dan/models/node_taxonomy.py` — verify/update policy sets

**Test:**
- `tests/test_worker/test_model.py` (new)

## Decisions

- **LLM tuning in a sub-model, not flat.** `LLMHints` keeps LLM-specific knobs (`temperature`, `output_json_schema`, `tools`, `max_tool_rounds`, `task_tier`, `history_policy`) off the Worker's top level. When `model` is `None`, the `llm` field should be `None` too. This prevents the "grab bag" problem.
- **Control flow in a sub-model.** `ControlFlowConfig` keeps `condition`, `gate_mode`, `max_iterations`, `feedback_selector` together. Only set when the Worker acts as a gate/loop.
- **Validation rules stay flat.** `validation_rules: list[ValidationRule]` is a simple list, not worth a sub-model wrapper. Empty list = no validation.
- **Default ports are smart.** A bare Worker gets `data` input + `result` output. A gate Worker gets mode-appropriate routing ports. A validator gets `valid`/`invalid`. This is the "it just works" philosophy at the model level.
- **`persona` vs `llm.system_prompt`.** `persona` is the Worker's identity — always available, used for logging/display even on non-LLM workers. `llm.system_prompt` is the LLM system message (defaults to `persona` when not set explicitly). This avoids overloading one field.

## Notes

- The `src/dan/worker/` module is self-contained. It imports from `dan.models` (NodeBase, ports, context, control_flow) but nothing outside `dan.worker/` imports from deep inside it — only from the public `__init__.py` API.
- The composite-node contract fields are identical to those on existing types (WhileLoopNode, CompositeNode, etc.) — same Pydantic types, same defaults. This ensures the engine's composite execution path works without changes.
- The `sub_workers` field generalizes `OrchestratorNode.teams` and `AgentTeamNode.agents`. One name, one concept.
