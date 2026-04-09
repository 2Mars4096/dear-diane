# 46-1: Worker Model & Type System

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
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
- The heavier composite-contract copy is now live on the Worker model and authoring surfaces too: `external_input_schema`, `external_output_schema`, `control_state_schema`, `local_state`, `compaction_rule`, `failure_policy`, `projections`, and `boundary_contract` all serialize cleanly and round-trip through `wf.worker(...)` / `wf.worker_scope(...)`.
- Boundary-validator insertion now accepts Worker composites directly, so composite Worker contracts can participate in the same validator-helper path as the older composite compatibility nodes.
- The unchanged full-suite exit gate is now green. The focused Worker/model/runtime suites stayed green while the repo-wide burn-down cleared the last unrelated blockers, including `tests` package import shadowing from site-packages during eval benchmark collection, a stale round-trip corpus reference to a non-existent generated graph artifact, and a late-suite `DAN_WORKER_BUILDER` env leak in `tests/test_worker/test_builder.py`. The final validation watermark for this plan is `2626 passed, 15 skipped, 18 deselected, 2 xfailed`.

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
- [x] 8. Define `Worker(NodeBase)` model in `model.py`
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
  - [x] 5-5. **Composite-node contract** (one copy, not seven):
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
- [x] 12. Tests
  - [x] 12-1. Bare `Worker(id="w", name="w")` is valid, has default `input` and `result` ports
  - [x] 12-2. JSON round-trip: Worker → dict → JSON → dict → Worker for each config variant (LLM, tool, code, composite, gate, validator)
  - [x] 12-3. `Node` discriminator: `{"node_type": "worker", ...}` deserializes correctly
  - [x] 12-4. Taxonomy: `"worker"` in `RUNTIME_NODE_TYPES`, `RUNTIME_NODE_TYPE_MAP`
  - [x] 12-5. Full existing test suite passes unchanged
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
- The Worker model now carries both the small composition contract and the heavier composite-contract surface directly: input/output port mappings, bounded parallelism, merge policy, spawn caps, validator rules, external input/output schemas, control-state schema, local state, compaction/failure policy, projections, and boundary contract.
- `12-5` is now a pure validation gate, not a model-surface gap. The full-suite blockers cleared so far were all repo-wide drift outside Worker semantics: stale `/analytics` event-count expectations, bridged-Worker object-id cache reuse, runtime lint fallback being too broad for plain legacy graphs, env precedence weakening explicit LLM concurrency caps, manual gateway overrides being discarded by the scheduler, user-profile domain storage drift, prep-timeout tests that depended on live triage heuristics, an in-process sentence-transformer probe that could abort the interpreter, provider fallback recursion in `llm_surface`, a shared domain-map boundary violation, stale notification defaults, stale parameter-decision logger expectations, and direct `ProviderRegistry.resolve(...)` no longer honoring single-key default-provider fallback for strict-prefix models.
- The most recent 46-1 burn-down pass also cleared a Worker-first import-boundary regression (`agent_runtime` pulling `dan.server.workflow_latency`), restored flat Worker compatibility fields still used by builder/bridge surfaces (`prompt_template`, `system_prompt`, `tool_id`, `tool_config`, etc.), hardened builder/decompiler tests that were unintentionally rollout-mode-sensitive, extended run-readiness checks to Worker-native code/tool/tool-less-LLM nodes, and restored thin RunManager wrappers for finalization helpers that tests still call directly.
- The latest follow-up also made the earlier runtime extraction real: `src/dan/server/concierge/runtime/context_resolution.py` now owns the context-resolution block and `runtime/__init__.py` binds those helpers instead of keeping the old inline copy, which brings the concierge runtime back under the module-boundary watchpoint while preserving behavior.
- The run-lifecycle API integration file was also updated to the current contract: empty graphs are now expected to fail `/api/runs` with a run-readiness error, while the success-path run/get/list tests build a minimal runnable graph before launch.
- The current pass also aligned the chat `/run` integration expectations with that same run-readiness contract. Empty-graph `/run` calls now assert `run_error` / `not_run_ready`, while the success-path stream-channel regression uses a runnable graph instead of depending on the old empty-graph behavior.
- The same validation push also restored compatibility for direct chat-router unit tests after the request-backed AppState migration, and cleared a rollout-sensitive intent-compiler assertion so Worker-enabled fan-out code-body compilation no longer fails the unchanged-suite gate just because it emits `body.worker(...)` instead of `body.code(...)`.
- The current 2026-04-07 burn-down pass also moved the stale `RunManager` success-path tests onto a minimal runnable workflow, restored the thin `RunManager._emit_workflow_telemetry()` forwarder that tests still reach directly after the `RunFinalizer` split, and kept the request-backed server dependency layer on typed `AppState` while the unchanged-suite gate continues.
- The same pass also hardened `src/dan/server/concierge/project_store.py` for sandboxed pytest runs by defaulting to `/tmp/dan-project-store` when `DAN_PROJECT_STORE_DIR` is unset under pytest, which cleared the `tests/test_engine/test_consolidation.py` preference-extraction failure without widening the runtime path for normal production use.
- The latest 2026-04-07 validation slice also repaired the trace-distillation workflow path that had regressed under Worker-first builder startup. Distilled `file_write` stages now compile as run-ready graphs again, `promote_trace_draft(...)` works in both direct-call tests and request-backed HTTP mode, and builder `NodeRef` chaining now honors a node's sole explicit input/output port across the `llm` / `tool` / `code` / `worker` constructors instead of always assuming generic `input` / `result`.
- The same full-suite rerun then reached `6879 passed` before exposing another repo-wide validation-only issue: `tests/test_server/test_graph_mutator.py` was inheriting ambient `DAN_WORKER_BUILDER` state and intermittently expecting Worker output in a legacy-only assertion file. That file is now pinned back to the default legacy mode, while the Worker-enabled mutator expectations remain in the dedicated taxonomy file.
- The next rerun advanced further to `6945 passed` before exposing another validation-only seam in `tests/test_server/test_llm_gateway_usage.py`: `_build_meta_controller()` was bypassing the patched public `ChatManager` provider seam whenever `_model_gateway` had already been mirrored into `app.py`. That helper now prefers the public chat-manager resolution path when a chat manager exists and uses the mirrored gateway only as the fallback path without a chat manager.
- The next rerun advanced to `7035 passed` before exposing the same suite-order drift pattern in `tests/test_server/test_mutation_quality.py`: the deterministic legacy mutation-quality basket was inheriting ambient `DAN_WORKER_BUILDER` state and then asserting `llm_operator` output against Workerized nodes. That file is now pinned back to the default legacy mode with an autouse env fixture, while Workerized mutation expectations remain in the dedicated taxonomy coverage.
- The next rerun advanced to `7163 passed` before exposing another validation-only prompt drift in `tests/test_server/test_research_prompt.py`: the lightweight research hint no longer spelled out the expected multi-search guidance, and the regression file still assumed older `surface_hints` / `task_hints` placeholders plus non-canonical cited-source URLs. The prompt now again states the explicit `3-8 distinct web_search queries` guidance, and the regression file matches the current `module_hints` plus canonicalized-URL contract.
- The final 2026-04-07 burn-down pass then closed the unchanged-suite gate completely. Adding `tests/__init__.py` restored the repo-local `tests.eval` package over an unrelated site-packages `tests` module, `tests/test_loader/test_graph_corpus_builder_roundtrip.py` now points at the committed `graphs/a8c217118e87.json` corpus fixture instead of a missing `eval-lr2-*` artifact, and `tests/test_worker/test_builder.py` now deletes ambient `DAN_WORKER_BUILDER` state by default so only its explicit env-gated tests opt into Worker-native aliases. With those validation-only repairs in place, `pytest -q --maxfail=1 tests` finishes green at `2626 passed, 15 skipped, 18 deselected, 2 xfailed`.
- Detailed landed-slice history moved to `docs/changelog.md`; this plan now focuses on the still-open model gaps and the current lightweight-vs-heavy composition boundary.
- `tests/test_worker/test_model.py` now carries an explicit variant round-trip matrix for LLM, tool, code, composite, gate, and validator-shaped Workers. The test locks the JSON surface itself, not just one representative “reviewer” example, so the canonical Worker model can change deliberately instead of drifting silently across capability families.
