# 46-3: Roles, Presets & Equivalence Proof

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
**Goal:** Define the built-in role system, build legacy-type-to-Worker presets, and prove that Worker fully covers compute-oriented node types while retained control/runtime types have a clean compatibility story and parity proof.

## Design: Roles, Not Types

The user-facing concept is **roles**, not type derivations. When someone asks "what kinds of workers are there?", the answer is:

> There's one Worker. We have built-in roles for common patterns — LLM agent, tool runner, script executor, reviewer, manager, router — but you can also just configure a Worker directly. Most of the time, you set `model` or `code` or `tool_ids` and the Worker figures out the rest.

Roles are factory functions in `src/dan/worker/roles.py`. They produce Worker instances with appropriate defaults. They're convenience, not architecture.

The **presets** in `src/dan/worker/presets.py` are the migration bridge: they convert legacy node types (LLMOperator, ToolOperator, etc.) to Worker equivalents and vice versa where appropriate. For retained control/runtime primitives, the bridge may preserve specialized execution semantics instead of pretending the behavior has been fundamentally reduced to one generic Worker shape. This is implementation, not user-facing API.

## Current State

- The role layer is complete: generic `role(...)` plus the built-in Worker shortcuts are live and covered.
- The migration boundary is explicit in code: `BRIDGED_LEGACY_NODE_TYPES`, `EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES`, and `supports_legacy_conversion(...)` make the Worker vs retained-specialized split inspectable.
- The formal parity suite is no longer just a few spot checks. It covers Tier 1 single-node compute parity, direct specialized `vote` parity, retained-control families (`for_each`, `while_loop`, `goal_loop`), retained orchestration families (`parallel_subagents`, `agent_team`, static and deterministic-LLM `orchestrator`), and workflow-scale parity including `examples/paper_writing.py`.
- The remaining 46 work after this sub-plan is compaction and rollout, not unanswered equivalence.

## Tasks

- [x] 1. Implement role factories in `src/dan/worker/roles.py`
  - [x] 1-1. `role(name, **kwargs) -> Worker` — generic factory that sets `role=name` + applies kwargs
  - [x] 1-2. Built-in role shortcuts:
    - `llm_agent(id, model, persona, **kw)` — Worker with `model` + `persona`, optional `llm` hints
    - `tool_runner(id, tool_ids, **kw)` — Worker with `tool_ids`, no model
    - `script(id, code, language="python", **kw)` — Worker with `code`, no model
    - `reviewer(id, model, persona, **kw)` — Worker with `role="reviewer"` + `model` + review-oriented defaults
    - `manager(id, model, sub_workers, **kw)` — Worker with `role="manager"` + `authority=LEAD` + `sub_workers`
    - `router(id, model, **kw)` — Worker with `role="router"` + `model` + conditional output ports
    - `gate(id, condition, gate_mode="if_else", **kw)` — Worker with `control_flow` config
    - `validator(id, rules, **kw)` — Worker with `validation_rules`
    - `observer(id, **kw)` — Worker with `tool_ids=["human_input"]` for human interaction
  - [x] 1-3. All roles return standard `Worker` instances — no subclassing, no special types
  - [x] 1-4. Custom roles: users call `role("my_custom_role", model="...", persona="...", ...)` — no registration needed
- [x] 2. Implement legacy presets in `src/dan/worker/presets.py`
  - [x] 2-1. `from_legacy(node: NodeBase) -> Worker` — convert any existing node to Worker equivalent
  - [x] 2-2. `to_legacy(worker: Worker) -> NodeBase` — convert Worker back to closest legacy type (for backward compat)
  - [x] 2-3. Per-type conversion logic:

**Tier 1 — straightforward mappings (config → Worker fields):**

| Legacy Type | Worker Equivalent |
|---|---|
| `LLMOperator` | `model` + `llm=LLMHints(prompt_template, temperature, output_json_schema, tools, max_tool_rounds)` |
| `ToolOperator` | `tool_ids=[tool_id]` |
| `CodeOperator` | `code` + `language` |
| `RAGOperator` | `tool_ids=["rag_query"]` + RAG config in `metadata` |
| `InputNode` | Passthrough Worker with variables as output ports |
| `ReduceNode` | `code` containing the reducer expression |

**Tier 2 — control/runtime compatibility (topology → Worker-facing contract metadata plus compatible execution path):**

| Legacy Type | Worker Equivalent |
|---|---|
| `IfElseNode` | Worker-facing control metadata and/or direct compatibility wrapper; runtime may remain specialized |
| `GateNode` | Worker-facing control metadata and/or direct compatibility wrapper; runtime may remain specialized |
| `RouterNode` | `role="router"` + `model` + route descriptions in `persona` |
| `ValidatorNode` | `validation_rules` + no model |

**Tier 3 — composites and coordination patterns (sub-graph/protocol → Worker contracts plus compatible runtime path):**

| Legacy Type | Worker Equivalent |
|---|---|
| `CompositeNode` | `body_graph` + `input_mappings` + `output_mappings` |
| `WhileLoopNode` | Worker-facing loop contract and/or compatibility wrapper; runtime may remain specialized |
| `ForEachNode` | `body_graph` + `parallelism` + `merge_strategy` |
| `ParallelSubagentsNode` | Worker participants plus fork/join protocol semantics |
| `OrchestratorNode` | Worker participants plus coordinator/dispatch semantics |
| `AgentTeamNode` | Worker participants plus team-turn/shared-transcript semantics |
| `GoalLoopNode` | goal-oriented loop contract and/or compatibility wrapper; runtime may remain specialized |

**Tier 4 — complex patterns (multi-mode Workers):**

| Legacy Type | Worker Equivalent |
|---|---|
| `ReflectionNode` | `role="reviewer"` + `model` + reflection prompt in `persona` |
| `VoteNode` | `sub_workers` (per-candidate) + `code` (vote aggregation) or `model` (judge) |
| `HumanNode` | `role="observer"` + `tool_ids=["human_input"]` |
| `HumanInTheLoopNode` | Same as HumanNode (alias) |

- [x] 3. Build per-type equivalence tests
  - [x] 3-1. For each legacy type: construct a minimal graph with the old type AND an equivalent Worker graph
  - [x] 3-2. Run both through the engine with identical inputs and mock LLM providers
  - [x] 3-3. Assert identical output data on output ports
  - [x] 3-4. Assert matching event streams (node_started, node_completed, outputs)
  - [x] 3-5. Group tests by tier for clear progress tracking
- [x] 4. Full workflow equivalence test
  - [x] 4-1. Workerize the recursive compute surface of the paper-writing workflow (`examples/paper_writing.py`) via `convert_graph()`, while retaining specialized loop/composite primitives where they remain the honest runtime model
  - [x] 4-2. Run both versions through the engine with mock providers
  - [x] 4-3. Assert structural equivalence of outputs
  - [x] 4-4. No additional compute-oriented Worker model expansion was required by the landed parity suite; the remaining gaps are intentionally retained specialized runtime families
  - [x] 4-5. If a control/runtime type resists forced Workerization but parity is preserved through delegation, document it as an intentional retained primitive instead of treating it as a design failure
- [x] 5. Basis completeness assertion
  - [x] 5-1. Programmatic test: iterate `RUNTIME_NODE_TYPE_MAP`, assert every type has either a direct `legacy_to_worker()` bridge or an explicit documented non-bridged exception
  - [x] 5-2. Programmatic test: every role factory produces a Worker that passes `WorkerExecutor` auto-detection
  - [x] 5-3. Document any exceptions (types deliberately kept separate or compiled from smaller orchestration primitives) in parent plan decisions

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
- **Equivalence is strict.** Same inputs → same outputs → same events. If the new path produces different behavior than the legacy equivalent, that's a bug, not an acceptable divergence.
- **Not every parity proof requires literal absorption.** For compute nodes, the ideal target is a direct Worker equivalent. For control/runtime nodes, parity via retained specialized executors is acceptable if it yields a clearer architecture.
- **Bridge coverage is explicit.** `src/dan/worker/presets.py` now exposes `BRIDGED_LEGACY_NODE_TYPES`, `EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES`, and `supports_legacy_conversion(node_type)` so the current migration boundary is codified in code instead of living only in prose.

## Notes

- Tier 1 compute conversions are mechanical; the harder retained families are the ones where specialization remains the cleaner runtime truth.
- The parity boundary is now explicit instead of implicit: compute families prove direct Worker equivalence, while branch/loop/team/orchestrator families prove retained-specialized parity with Workerized inner compute.
- If a future compute-oriented conversion exposes a real missing Worker capability, the answer is still to loop back to 46-1; this completed sub-plan just shows that the current Worker surface was sufficient for the landed compute and retained-control suites.
