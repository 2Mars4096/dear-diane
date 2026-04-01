# 46-3: Roles, Presets & Equivalence Proof

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Define the built-in role system, build legacy-type-to-Worker presets, and prove that every existing node type is expressible as a Worker configuration — establishing the Worker as a complete basis.

## Design: Roles, Not Types

The user-facing concept is **roles**, not type derivations. When someone asks "what kinds of workers are there?", the answer is:

> There's one Worker. We have built-in roles for common patterns — LLM agent, tool runner, script executor, reviewer, manager, router — but you can also just configure a Worker directly. Most of the time, you set `model` or `code` or `tool_ids` and the Worker figures out the rest.

Roles are factory functions in `src/dan/worker/roles.py`. They produce Worker instances with appropriate defaults. They're convenience, not architecture.

The **presets** in `src/dan/worker/presets.py` are the migration bridge: they convert legacy node types (LLMOperator, ToolOperator, etc.) to Worker equivalents and vice versa. This is implementation, not user-facing API.

## Tasks

- [ ] 1. Implement role factories in `src/dan/worker/roles.py`
  - [ ] 1-1. `role(name, **kwargs) -> Worker` — generic factory that sets `role=name` + applies kwargs
  - [ ] 1-2. Built-in role shortcuts:
    - `llm_agent(id, model, persona, **kw)` — Worker with `model` + `persona`, optional `llm` hints
    - `tool_runner(id, tool_ids, **kw)` — Worker with `tool_ids`, no model
    - `script(id, code, language="python", **kw)` — Worker with `code`, no model
    - `reviewer(id, model, persona, **kw)` — Worker with `role="reviewer"` + `model` + review-oriented defaults
    - `manager(id, model, sub_workers, **kw)` — Worker with `role="manager"` + `authority=LEAD` + `sub_workers`
    - `router(id, model, **kw)` — Worker with `role="router"` + `model` + conditional output ports
    - `gate(id, condition, gate_mode="if_else", **kw)` — Worker with `control_flow` config
    - `validator(id, rules, **kw)` — Worker with `validation_rules`
    - `observer(id, **kw)` — Worker with `tool_ids=["human_input"]` for human interaction
  - [ ] 1-3. All roles return standard `Worker` instances — no subclassing, no special types
  - [ ] 1-4. Custom roles: users call `role("my_custom_role", model="...", persona="...", ...)` — no registration needed
- [ ] 2. Implement legacy presets in `src/dan/worker/presets.py`
  - [ ] 2-1. `from_legacy(node: NodeBase) -> Worker` — convert any existing node to Worker equivalent
  - [ ] 2-2. `to_legacy(worker: Worker) -> NodeBase` — convert Worker back to closest legacy type (for backward compat)
  - [ ] 2-3. Per-type conversion logic:

**Tier 1 — straightforward mappings (config → Worker fields):**

| Legacy Type | Worker Equivalent |
|---|---|
| `LLMOperator` | `model` + `llm=LLMHints(prompt_template, temperature, output_json_schema, tools, max_tool_rounds)` |
| `ToolOperator` | `tool_ids=[tool_id]` |
| `CodeOperator` | `code` + `language` |
| `RAGOperator` | `tool_ids=["rag_query"]` + RAG config in `metadata` |
| `InputNode` | Passthrough Worker with variables as output ports |
| `ReduceNode` | `code` containing the reducer expression |

**Tier 2 — control flow (topology → Worker + ControlFlowConfig):**

| Legacy Type | Worker Equivalent |
|---|---|
| `IfElseNode` | `control_flow=ControlFlowConfig(condition, gate_mode="if_else")` |
| `GateNode` | `control_flow=ControlFlowConfig(condition, gate_mode, max_iterations)` |
| `RouterNode` | `role="router"` + `model` + route descriptions in `persona` |
| `ValidatorNode` | `validation_rules` + no model |

**Tier 3 — composites (sub-graph → Worker + body_graph/sub_workers):**

| Legacy Type | Worker Equivalent |
|---|---|
| `CompositeNode` | `body_graph` + `input_mappings` + `output_mappings` |
| `WhileLoopNode` | `body_graph` + `control_flow` (while mode) |
| `ForEachNode` | `body_graph` + `parallelism` + `merge_strategy` |
| `ParallelSubagentsNode` | `sub_workers` + `parallelism` + `merge_strategy` |
| `OrchestratorNode` | `role="manager"` + `authority=LEAD` + `model` + `sub_workers` |
| `AgentTeamNode` | `role="manager"` + `sub_workers` + team config in `metadata` |
| `GoalLoopNode` | `body_graph` + `control_flow` (goal metric as condition) |

**Tier 4 — complex patterns (multi-mode Workers):**

| Legacy Type | Worker Equivalent |
|---|---|
| `ReflectionNode` | `role="reviewer"` + `model` + reflection prompt in `persona` |
| `VoteNode` | `sub_workers` (per-candidate) + `code` (vote aggregation) or `model` (judge) |
| `HumanNode` | `role="observer"` + `tool_ids=["human_input"]` |
| `HumanInTheLoopNode` | Same as HumanNode (alias) |

- [ ] 3. Build per-type equivalence tests
  - [ ] 3-1. For each legacy type: construct a minimal graph with the old type AND an equivalent Worker graph
  - [ ] 3-2. Run both through the engine with identical inputs and mock LLM providers
  - [ ] 3-3. Assert identical output data on output ports
  - [ ] 3-4. Assert matching event streams (node_started, node_completed, outputs)
  - [ ] 3-5. Group tests by tier for clear progress tracking
- [ ] 4. Full workflow equivalence test
  - [ ] 4-1. Express the paper-writing workflow (`examples/paper_writing.py`) entirely with Workers
  - [ ] 4-2. Run both versions through the engine with mock providers
  - [ ] 4-3. Assert structural equivalence of outputs
  - [ ] 4-4. If any type cannot be expressed: loop back to 46-1 and extend the Worker model
- [ ] 5. Basis completeness assertion
  - [ ] 5-1. Programmatic test: iterate `RUNTIME_NODE_TYPE_MAP`, assert every type has a `from_legacy()` path
  - [ ] 5-2. Programmatic test: every role factory produces a Worker that passes `WorkerExecutor` auto-detection
  - [ ] 5-3. Document any exceptions (types deliberately kept separate) in parent plan decisions

## Likely Files

**New:**
- `src/dan/worker/roles.py` — role factory functions
- `src/dan/worker/presets.py` — legacy type ↔ Worker conversion
- `tests/test_worker/test_roles.py` — role factory tests
- `tests/test_worker/test_presets.py` — per-type conversion tests
- `tests/test_worker/test_equivalence.py` — engine-level equivalence tests
- `tests/test_worker/test_workflow.py` — full workflow equivalence

## Decisions

- **Roles are functions, not classes.** `llm_agent(...)` returns a `Worker`, not an `LLMAgentWorker`. No subclassing.
- **Custom roles need no registration.** `role("my_role", model="...", persona="...")` works immediately. The role name is just a label.
- **Legacy presets are internal.** `from_legacy()` and `to_legacy()` live in `presets.py` and are used by the migration path (46-5) and equivalence tests. They're not the user-facing API.
- **Equivalence is strict.** Same inputs → same outputs → same events. If a Worker produces different output than its legacy equivalent, that's a bug, not an acceptable divergence.

## Notes

- Tier 1 and Tier 2 conversions are mechanical (field mapping). Tier 3 requires composite contract copying. Tier 4 requires creative decomposition.
- The hardest conversions are `VoteNode` (multi-model ensemble with aggregation) and `AgentTeamNode` (turn-strategy, handoff-policy, moderation). These may require the Worker's `code` field for aggregation/moderation logic that was previously baked into specialized executors.
- If a Tier 4 conversion reveals that the Worker model is genuinely missing a capability, the answer is to extend the model (loop to 46-1), not to add a special-case node type.
