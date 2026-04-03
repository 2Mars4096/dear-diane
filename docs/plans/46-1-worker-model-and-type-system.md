# 46-1: Worker Model & Type System

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** in-progress
**Goal:** Define a clean, lightweight `Worker` model with clear field groups, reference-based access to shared context systems, explicit authority/governance and execution semantics, optional typed sub-models for tuning, seamless integration into the existing `Node` union, and a canonical contract metadata surface for downstream systems.

## Design: Clean Field Organization

The Worker model should separate intrinsic node identity from external shared systems:

```
┌─────────────────────────────────────────────────────┐
│  IDENTITY — who is this worker?                      │
│  role, purpose, short instruction, authority         │
├─────────────────────────────────────────────────────┤
│  CAPABILITY REFS — what execution can it use?        │
│  model, tool_ids, code, language                     │
├─────────────────────────────────────────────────────┤
│  CONTEXT ACCESS — what shared systems can it read?   │
│  instruction/memory/context/provider/retry refs      │
├─────────────────────────────────────────────────────┤
│  GOVERNANCE & EXECUTION — what may it do?            │
│  delegation caps, task-tier caps, locks, blocking    │
├─────────────────────────────────────────────────────┤
│  COMPOSITION — does it manage sub-workers?           │
│  body_graph, sub_workers, contract fields            │
├─────────────────────────────────────────────────────┤
│  OPTIONAL TUNING — execution-mode-specific hints     │
│  llm_hints (temperature, output_schema, tools, ...)  │
│  control_flow (condition, gate_mode, max_iterations) │
└─────────────────────────────────────────────────────┘
```

A bare `Worker(id="w1", name="w1")` is valid — it passes inputs through. In the current implementation its canonical default ports are `input` and `result`. Adding fields from any layer activates the corresponding execution mode automatically.

## Current State

- The lightweight core is live: `ContextBindings`, `AuthorityPolicy`, `ExecutionSemantics`, `LLMHints`, `ControlFlowConfig`, `WorkerConfig`, and `Worker`.
- Worker defaults are now stable and tested: bare Workers default to `input -> result`, model-backed or hint-backed Workers default to `text`, and `control_flow` Workers seed gate-style ports.
- Shared systems remain reference-first. Instruction, provider, retry, toolset, and authority refs resolve through `graph.worker_resources` at runtime instead of being copied onto each Worker.
- The first-class composition surface is now live: `input_mappings`, `output_mappings`, `parallelism`, `merge_strategy`, `spawn_policy`, and `validation_rules` all round-trip through Worker authoring/runtime without hiding inside metadata.
- The heavier composite-contract copy is still intentionally deferred. The current runtime uses the new Worker composition fields plus `boundary_contract` and retained specialized executors rather than copying every legacy composite field onto `Worker` up front.

## Tasks

- [x] 1. Create `src/dan/worker/` module
  - [x] 1-1. Create `src/dan/worker/__init__.py` — public API exports
  - [x] 1-2. Create `src/dan/worker/model.py` — all model definitions
- [x] 2. Define `WorkerAuthority` enum in `model.py`
  - [x] 2-1. `LEAF` — executes, cannot delegate or spawn
  - [x] 2-2. `DELEGATE` — can delegate bounded sub-work
  - [x] 2-3. `LEAD` — can coordinate declared sub-workers within bounded policies
  - [x] 2-3. `DIRECTOR` — can restructure topology at runtime
- [x] 3. Define `ContextBindings` sub-model in `model.py`
  - [x] 3-1. `instruction_profile_ref: str | None = None`
  - [x] 3-2. `memory_policy_ref: str | None = None`
  - [x] 3-3. `context_bundle_refs: list[str] = []`
  - [x] 3-4. `provider_policy_ref: str | None = None`
  - [x] 3-5. `retry_policy_ref: str | None = None`
  - [x] 3-6. `toolset_refs: list[str] = []`
  - [x] 3-7. `inherit_defaults: bool = True`
  - [x] 3-8. All refs are explicit and inspectable. A Worker should know what shared systems exist for it, and runtime should be able to resolve them deterministically.
- [x] 4. Define `AuthorityPolicy` sub-model in `model.py`
  - [x] 4-1. `max_spawned_workers: int | None = None`
  - [x] 4-2. `task_tier_cap: Literal["micro", "routine", "reasoning", "critical"] | None = None`
  - [x] 4-3. `allow_delegate: bool = False`
  - [x] 4-4. `allow_memory_write_scopes: list[str] = []`
  - [x] 4-5. `allowed_toolset_refs: list[str] = []`
  - [x] 4-6. Keep this lightweight: policy and caps, not embedded runtime state
- [x] 5. Define `ExecutionSemantics` sub-model in `model.py`
  - [x] 5-1. `resource_locks: list[str] = []`
  - [x] 5-2. `blocking_mode: Literal["auto", "exclusive", "shared"] = "auto"`
  - [x] 5-3. `await_subworkers: bool = True`
  - [x] 5-4. `parallelism_override: int | None = None`
  - [x] 5-5. Document clearly that graph edges/control primitives govern readiness, while locks govern serialization over shared resources
- [x] 6. Define `LLMHints` sub-model in `model.py`
  - [x] 6-1. `prompt_template: str = ""` — prompt with `{port}` placeholders
  - [x] 6-2. `system_prompt: str = ""` — system instructions (overrides the short Worker instruction/profile when set explicitly for LLM context)
  - [x] 6-3. `temperature: float = 0.7`
  - [x] 6-4. `max_tokens: int | None = None`
  - [x] 6-5. `output_json_schema: dict | None = None` — structured output
  - [x] 6-6. `tools: list[dict] = []` — tool schemas in OpenAI function-calling format
  - [x] 6-7. `max_tool_rounds: int = 10` — safety bound on tool-calling loops
  - [x] 6-8. `task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = None`
  - [x] 6-9. `history_policy: HistoryPolicy | None = None`
  - [x] 6-10. All fields have sensible defaults — the entire sub-model is optional on Worker
- [x] 7. Define `ControlFlowConfig` sub-model in `model.py`
  - [x] 4-1. `condition: str` — expression evaluated on inputs
  - [x] 4-2. `gate_mode: Literal["if_else", "while"] = "if_else"`
  - [x] 4-3. `max_iterations: int = 10`
  - [x] 4-4. `feedback_selector: FeedbackSelector | None = None`
- [ ] 8. Define `Worker(NodeBase)` model in `model.py`
  - [x] 5-1. `node_type: Literal["worker"] = "worker"`
  - [x] 5-2. **Identity fields:**
    - `role: str = ""` — natural language role label (optional, for readability and preset loading)
    - `instruction: str = ""` — short local behavioral instruction
    - `persona: str = ""` — compatibility alias / short descriptive instruction only, not a large embedded prompt pack
    - `authority: WorkerAuthority = WorkerAuthority.LEAF`
  - [x] 5-3. **Capability fields:**
    - `model: str | None = None` — LLM model identifier; `None` = no LLM; this is a reference, not an embedded provider config
    - `tool_ids: list[str] = []` — registered tools, resolved from ToolRegistry at runtime
    - `code: str = ""` — inline script for deterministic execution
    - `language: str = "python"` — code language
    - `context: ContextBindings | None = None`
    - `authority_policy: AuthorityPolicy | None = None`
    - `execution: ExecutionSemantics | None = None`
  - [x] 5-4. **Composition fields:**
    - `body_graph: str | None = None` — key into `Graph.sub_graphs`
    - `sub_workers: dict[str, str] = {}` — named sub-worker map (name → sub_graph key)
    - `input_mappings: dict[str, str] = {}` — outer_port → inner_entry_port
    - `output_mappings: dict[str, str] = {}` — inner_exit_port → outer_port
    - `parallelism: int = 1` — max concurrent sub-graph branches
    - `merge_strategy: MergeStrategy = MergeStrategy.APPEND` — fan-in strategy
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
  - [x] 5-6. **Optional tuning sub-models:**
    - `llm_hints: LLMHints | None = None` — LLM-specific config (only when `model` is set)
    - `control_flow: ControlFlowConfig | None = None` — gate/loop config (only when routing)
    - `validation_rules: list[ValidationRule] = []` — validator rules (only when validating)
  - [x] 5-7. `model_post_init` — seed default ports based on config:
    - Current implementation: seed canonical `input` + `result` for bare Workers, `text` for model-backed Workers, `result` for code/tool/body/sub-worker Workers, and `true/false` or `continue/done` for `control_flow` Workers when none are declared
    - Deferred: validator-specific default-port seeding still waits on a cleaner first-class validation surface
  - [x] 5-8. Document and preserve the canonical contract/intent fields on Worker: `description`, `role`, `instruction`/`persona`, input/output port descriptions + schemas, and boundary schemas. Downstream systems must be able to read them without understanding legacy node types.
  - [x] 5-9. Document the resolution order for shared context: graph/team defaults → referenced profiles/bundles → local Worker overrides.
- [x] 9. Define `__init__.py` public API
  - [x] 9-1. Export: `Worker`, `WorkerAuthority`, `ContextBindings`, `AuthorityPolicy`, `ExecutionSemantics`, `LLMHints`, `ControlFlowConfig`
  - [x] 9-2. Export: `role` function (placeholder — implemented in 46-3)
- [x] 10. Add `Worker` to the `Node` union in `graph.py`
  - [x] 10-1. Import `Worker` from `dan.worker`
  - [x] 10-2. Add to the `Union[...]` inside the `Annotated[...]` for `Node`
  - [x] 10-3. Verify Pydantic discriminator resolution: `{"node_type": "worker", ...}` → `Worker`
- [x] 11. Update `node_taxonomy.py`
  - [x] 11-1. Verify `RUNTIME_NODE_TYPE_MAP` picks up `"worker"` automatically
  - [x] 11-2. Add `"worker"` to `GENERATE_SPEC_NODE_TYPES`
  - [x] 11-3. Add `"worker"` to `MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES`
  - [x] 11-4. Verify `SUBGRAPH_BEARING_RUNTIME_NODE_TYPES` picks up Worker (has `body_graph` field)
- [ ] 12. Tests
  - [x] 12-1. Bare `Worker(id="w", name="w")` is valid, has default `input` and `result` ports
  - [x] 12-2. JSON round-trip: Worker → dict → JSON → dict → Worker for each config variant (LLM, tool, code, composite, gate, validator)
  - [x] 12-3. `Node` discriminator: `{"node_type": "worker", ...}` deserializes correctly
  - [x] 12-4. Taxonomy: `"worker"` in `RUNTIME_NODE_TYPES`, `RUNTIME_NODE_TYPE_MAP`
  - [ ] 12-5. Full existing test suite passes unchanged
  - [x] 12-6. `Worker.model_json_schema()` produces valid JSON Schema
  - [x] 12-7. `LLMHints` defaults: omitting `llm_hints` = no LLM execution; setting `llm_hints=LLMHints()` = all defaults
  - [x] 12-8. Shared-context refs are preserved round-trip and remain explicit in JSON
  - [x] 12-9. Authority and execution-semantic fields are serializable and inspectable

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
- **Shared systems are referenced, not copied.** Tool bundles, memory policies, instruction profiles, provider policies, and retry defaults should usually live outside the Worker and be referenced through `ContextBindings`.
- **Authority is policy, not personality.** The Worker should expose delegation/spawn/tier caps explicitly, but the heavy runtime state remains outside the node model.
- **Execution semantics are explicit.** Async scheduling comes from graph readiness plus resource locks, not from hand-wavy worker type assumptions.
- **Control flow in a sub-model.** `ControlFlowConfig` keeps `condition`, `gate_mode`, `max_iterations`, `feedback_selector` together. Only set when the Worker acts as a gate/loop.
- **Validation rules stay flat.** `validation_rules: list[ValidationRule]` is a simple list, not worth a sub-model wrapper. Empty list = no validation.
- **Default ports are smart.** A bare Worker gets `input` + `result`. A gate Worker gets mode-appropriate routing ports. Validator-style defaults remain intentionally conservative until the validation surface is cleaner. This is the "it just works" philosophy at the model level.
- **`instruction` / `persona` vs `llm.system_prompt`.** `instruction` is the preferred short local behavior hint. `persona` remains as a compatibility alias / descriptive label, but should stay short. `llm.system_prompt` is the LLM system message (defaults to the resolved short instruction/profile when not set explicitly). This avoids turning `persona` into a giant embedded prompt blob.
- **Worker contract metadata is canonical.** `description`, `role`, `persona`, port descriptions/schemas, and `external_*_schema` fields are preserved even for non-LLM workers because later systems (generation, lint autogen, editor) consume them.

## Notes

- `src/dan/worker/` stays self-contained: downstream code imports from `dan.worker`, not deep internal paths.
- The key discipline is unchanged: Worker stores what it is and what it may access; shared systems store the heavy details; runtime resolves the references when needed.
- `sub_workers` is the single Worker-side concept for named child participants, even when legacy runtime families still keep their own specialized protocol semantics.
- The Worker model now carries the small, honest composition contract directly: input/output port mappings, bounded parallelism, merge policy, spawn caps, and first-class validator rules. The larger legacy composite-state copy still remains out of scope for now.
- Detailed landed-slice history moved to `docs/changelog.md`; this plan now focuses on the still-open model gaps and the current lightweight-vs-heavy composition boundary.
- `tests/test_worker/test_model.py` now carries an explicit variant round-trip matrix for LLM, tool, code, composite, gate, and validator-shaped Workers. The test locks the JSON surface itself, not just one representative “reviewer” example, so the canonical Worker model can change deliberately instead of drifting silently across capability families.
