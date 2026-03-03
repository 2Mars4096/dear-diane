# 15-1: Hyperedge Engine Runtime

**Parent:** [15-behavior-modifiers](15-behavior-modifiers.md)
**Status:** completed
**Goal:** Implement hyperedge models, add them to the graph schema, build the four execution hooks (pre_prompt, tool_call, post_output, validation), and provide runtime resolution with attachment scoping and precedence — turning the architecture spec into working machinery.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| Architecture spec | `docs/architecture.md` §Hyperedges | Four types (skill, guardrail, style, override), attachment scopes (node ID, type, tags, subgraph), precedence rules, hook semantics | Prose-only — zero implementation |
| `SKILL_LIBRARY` | `server/skill_library.py` | 2 skills with `name`, `description`, `tags`, `inject_as`, `text` | Build-time only; no runtime hooks; not graph-level objects |
| `ApplySkill` | `server/graph_mutator.py` | Mutation op: resolves targets by ID/tag, prepends skill text to `system_prompt` or `prompt_template` | Static mutation; text baked into graph dict; idempotency via substring check |
| `NodeBase.metadata` | `models/nodes.py` | `dict[str, Any]` — used for tags, positions, etc. | Could hold hyperedge refs but no schema enforcement |
| `Graph` | `models/graph.py` | `nodes`, `edges`, `sub_graphs`, `shared_context`, `artifact_refs` | No `hyperedges` field |
| `NodeExecutor` | `engine/executor.py` | `execute(node, inputs, context) -> NodeResult` protocol | No pre/post hooks; no middleware |
| `ExecutionContext` | `engine/executor.py` | Config, stores, event callback, registries | No `hyperedge_registry` or hook-related fields |
| `LLMExecutor` | `executors/llm.py` | Builds messages from `node.system_prompt` + rendered prompt → calls provider | Prompt assembly has no injection point |
| `ToolExecutor` | `executors/tool.py` | Looks up tool fn, merges args, `await fn(**args)` | No interception point around the call |
| `_execute_node` | `engine/scheduler.py` | Resolves inputs → finds executor → `executor.execute()` → stores outputs → emits events | Straight-through; no pre/post hook invocation |
| `ValidatorExecutor` | `executors/validator.py` | Validates data against rules and routes to valid/invalid ports | Wired as explicit node, not as automatic post-output check |
| `EngineConfig` | `engine/executor.py` | Provider config, checkpoint config, defaults | No hyperedge-related settings |
| `events.py` | `engine/events.py` | 14 event types | No hyperedge-related event types |
| `validate_graph()` | `validation/graph.py` | Port, edge, cycle, context checks | No hyperedge validation |
| Node type discriminated union | `models/graph.py` | `Node = Annotated[Union[...], ...]` | No hyperedge in union |
| `NodeBase` tags | `models/nodes.py` | `metadata` dict; tags stored ad-hoc as `metadata.get("tags", [])` | No first-class `tags` field |
| Editor `NODE_TYPE_CATALOG` | `editor/src/types/graph.ts` | Lists all node types for palette | No hyperedge category |

## Tasks

- [ ] 1. Define hyperedge models and graph schema
  - [ ] 1-1. Create `src/dan/models/hyperedges.py` with base model `Hyperedge`: `id` (str), `name` (str), `description` (str, optional), `hyperedge_type` (Literal: `"skill"`, `"guardrail"`, `"style"`, `"override"`), `hook` (Literal: `"pre_prompt"`, `"tool_call"`, `"post_output"`, `"validation"`), `content` (str — the injected text, rule expression, or override spec), `config` (dict, optional — type-specific settings like severity, block_on_fail), `enabled` (bool, default True).
  - [ ] 1-2. Add attachment selectors to `Hyperedge`: `attach_to` (list[str] — node IDs), `attach_to_type` (list[str] — node type strings), `attach_to_tags` (list[str] — user-defined tags), `attach_to_subgraph` (list[str] — composite node IDs whose children are targeted), and `attach_globally` (bool, default False). Selectors are optional, but at least one selector must be non-empty **or** `attach_globally=True`.
  - [ ] 1-3. Add `propagate` field (bool, default True) — when True, hyperedges on a parent graph propagate into sub-graphs. When False, they apply only at the current level.
  - [ ] 1-4. Add `priority` field (int, optional) — for explicit ordering within same type. Default precedence: `override` > `guardrail` > `style` > `skill`; within same type, more specific scope wins (node ID > tag > type > subgraph); `priority` breaks ties.
  - [ ] 1-5. Add `hyperedges: list[Hyperedge]` field to `Graph` model (default empty list). Update `dan_graph_v1` JSON contract. Add `Hyperedge` to module exports.
  - [ ] 1-6. Promote `tags` from ad-hoc `metadata["tags"]` to a first-class `tags: list[str]` field on `NodeBase` (default empty list). Maintain backward compat by checking both `node.tags` and `node.metadata.get("tags", [])` during resolution.

- [ ] 2. Build hyperedge resolution engine
  - [ ] 2-1. Create `src/dan/engine/hyperedge_runtime.py` with `HyperedgeResolver` class. Constructor takes `graph: Graph` and optional `parent_hyperedges: list[Hyperedge]` (for propagation from parent graphs). Method `resolve(node: NodeBase, hook: str) -> list[Hyperedge]` returns all matching hyperedges for a node+hook combination, sorted by precedence.
  - [ ] 2-2. Implement attachment matching: a hyperedge matches a node if `attach_globally=True` **or** any selector matches — `node.id in attach_to`, `node.node_type in attach_to_type`, `set(node.tags) & set(attach_to_tags)`, or node is inside a sub-graph of a composite in `attach_to_subgraph`. Cache results per node ID for performance.
  - [ ] 2-3. Implement precedence sorting: type rank (`override=0`, `guardrail=1`, `style=2`, `skill=3`), then scope specificity rank (node_id=0, tag=1, type=2, subgraph=3 — use most specific matching selector), then explicit `priority` field (lower wins), then `id` for determinism.
  - [ ] 2-4. Implement propagation: when `_run_subgraph` is called, `HyperedgeResolver` for the child graph receives parent **candidate** hyperedges (filtered to `propagate=True`) as `parent_hyperedges`; child-node attachment matching is re-evaluated in the child scope. Child-graph hyperedges override parent ones with the same `id`.
  - [ ] 2-5. Add `hyperedge_resolver` field to `ExecutionContext`. Populate in `Engine.run()` / scheduler initialization from `graph.hyperedges`.

- [ ] 3. Implement execution hooks
  - [ ] 3-1. **`pre_prompt` hook** — called before LLM message assembly in `LLMExecutor.execute()`. For each resolved `pre_prompt` hyperedge, inject `content` into the prompt. Skill type: prepend to system message (or add as separate system message if none). Style type: append style instructions to system message. Guardrail type: append constraint instructions to system message. Method: `HyperedgeResolver.apply_pre_prompt(node, messages: list[dict]) -> list[dict]`.
  - [ ] 3-2. **`post_output` hook** — called after executor returns `NodeResult` but before outputs are stored. For each resolved `post_output` hyperedge, evaluate the rule against the output. Guardrail type: validate output against expression/schema in `content`; if fails, emit warning or raise `HyperedgeViolation` (based on `config.block_on_fail`). Method: `HyperedgeResolver.apply_post_output(node, result: NodeResult) -> NodeResult` (may modify or reject).
  - [ ] 3-3. **`tool_call` hook** — wraps the tool invocation in `ToolExecutor.execute()`. Override type: can modify tool args, replace tool function, or block the call entirely. Guardrail type: validate args before call, validate result after call. Method: `HyperedgeResolver.apply_tool_call(node, tool_id: str, args: dict) -> tuple[str, dict, bool]` (possibly modified tool_id, args, and allow/deny flag).
  - [ ] 3-4. **`validation` hook** — runs after `post_output` as a separate validation pass. Expression-based rules evaluated against output data (reusing `conditions.py` safe eval). Schema-based rules checked via `check_schema_compatible()`. Method: `HyperedgeResolver.apply_validation(node, outputs: dict) -> list[ValidationResult]`.
  - [ ] 3-5. Define `HyperedgeViolation` exception. Define `ValidationResult` model: `hyperedge_id`, `passed` (bool), `message`, `severity` (info/warning/error).

- [ ] 4. Integrate hooks into executors and scheduler
  - [ ] 4-1. Update `_execute_node` in `scheduler.py`: keep `pre_prompt` in `LLMExecutor` and `tool_call` in `ToolExecutor` (executor-level hooks), while scheduler handles shared post-execute stages. After `executor.execute()` returns, call `apply_post_output()` and `apply_validation()` before persisting outputs. Emit events for each hook invocation.
  - [ ] 4-2. Update `LLMExecutor.execute()`: after building `messages` list, call `hyperedge_resolver.apply_pre_prompt(node, messages)` to get modified messages. Use modified messages for the LLM call.
  - [ ] 4-3. Update `ToolExecutor.execute()`: before calling `fn(**merged_args)`, call `hyperedge_resolver.apply_tool_call(node, tool_id, merged_args)`. Respect allow/deny flag. Post-output and validation remain centralized in scheduler task 4-1.
  - [ ] 4-4. Update `_run_subgraph` in `scheduler.py`: propagate parent's `HyperedgeResolver` (filtered to `propagate=True`) to child's `ExecutionContext`. Child graph's own hyperedges are merged.
  - [ ] 4-5. Add `EngineConfig.hyperedge_enforcement` field: `"off"` (skip all hooks), `"warn"` (log violations as warnings but don't block), `"strict"` (block on guardrail/validation violations). Default: `"warn"`.

- [ ] 5. Add engine events for hyperedge operations
  - [ ] 5-1. Add event types to `engine/events.py`: `HYPEREDGE_APPLIED` (pre_prompt injection, tool_call modification), `HYPEREDGE_VIOLATION` (guardrail or validation failure), `HYPEREDGE_BLOCKED` (tool call or output blocked by override/guardrail).
  - [ ] 5-2. Emit `HYPEREDGE_APPLIED` in each hook method with `hyperedge_id`, `node_id`, `hook`, and summary of modification.
  - [ ] 5-3. Emit `HYPEREDGE_VIOLATION` when validation/guardrail fails with `hyperedge_id`, `node_id`, `severity`, `message`.
  - [ ] 5-4. Wire events through existing `event_callback` infrastructure; propagate to WebSocket subscribers.

- [ ] 6. Migrate existing skill system
  - [ ] 6-1. Convert `SKILL_LIBRARY` entries to `Hyperedge` instances: `management_science_writing` → skill hyperedge with `hook="pre_prompt"`, `attach_to_tags=["writing", "review"]`. `informs_latex_style` → style hyperedge with `hook="pre_prompt"`, `attach_to_tags=["latex"]`.
  - [ ] 6-2. Update `ApplySkill` mutation op to create `Hyperedge` objects in `graph.hyperedges` instead of baking text into prompts. Backward compat: if graph has no `hyperedges` field, fall back to legacy injection.
  - [ ] 6-3. Ensure existing paper-writing and vibe-research workflows that use `ApplySkill` continue to work identically with the new runtime resolution.

- [ ] 7. Validation and graph well-formedness
  - [ ] 7-1. Extend `validate_graph()`: warn if hyperedge `attach_to` references non-existent node IDs, warn if `attach_to_type` references unknown node types, warn if `attach_to_subgraph` references non-composite nodes.
  - [ ] 7-2. Validate that `hook` matches `hyperedge_type` compatibility: `skill` → `pre_prompt` only; `style` → `pre_prompt` only; `guardrail` → any hook; `override` → `tool_call` or `pre_prompt`.
  - [ ] 7-3. Warn on duplicate hyperedge `id` values.
  - [ ] 7-4. Add hyperedge visualization data to editor API: `GET /api/graphs/{id}` includes resolved hyperedge→node mappings for visual overlay.

- [ ] 8. Tests and documentation
  - [ ] 8-1. Unit tests: `Hyperedge` model serialization/deserialization, `HyperedgeResolver` attachment matching (all four selector types), precedence sorting (type rank, scope rank, priority, determinism), propagation filtering.
  - [ ] 8-2. Integration tests: skill injection modifies LLM prompts (mock provider), guardrail blocks invalid output, override replaces tool args, validation catches schema mismatch, propagation into nested sub-graphs.
  - [ ] 8-3. Backward compat tests: graphs without `hyperedges` field run identically, legacy `ApplySkill` still works, `hyperedge_enforcement="off"` skips all hooks.
  - [ ] 8-4. Update `docs/architecture.md` §Hyperedges: replace conceptual prose with links to model classes, hook implementation details, enforcement config.
  - [ ] 8-5. Update `docs/llm-api-guide.md`: hyperedge model reference, hook behavior, builder/markdown syntax (forward ref to 15-2).
  - [ ] 8-6. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/models/hyperedges.py` *(new)* — `Hyperedge`, `ValidationResult`, `HyperedgeViolation`
- `src/dan/models/graph.py` — add `hyperedges` field to `Graph`
- `src/dan/models/nodes.py` — promote `tags` to first-class `NodeBase` field
- `src/dan/engine/hyperedge_runtime.py` *(new)* — `HyperedgeResolver`: attachment matching, precedence, hook methods
- `src/dan/engine/executor.py` — add `hyperedge_resolver` to `ExecutionContext`; add `hyperedge_enforcement` to `EngineConfig`
- `src/dan/engine/scheduler.py` — hook invocations in `_execute_node`; propagation in `_run_subgraph`
- `src/dan/executors/llm.py` — call `apply_pre_prompt()` before LLM message assembly
- `src/dan/executors/tool.py` — call `apply_tool_call()` around tool invocation
- `src/dan/engine/events.py` — `HYPEREDGE_APPLIED`, `HYPEREDGE_VIOLATION`, `HYPEREDGE_BLOCKED` event types
- `src/dan/validation/graph.py` — hyperedge validation rules
- `src/dan/validation/schema.py` — schema rule evaluation (task 3-4)
- `src/dan/server/skill_library.py` — convert to hyperedge instances
- `src/dan/server/graph_mutator.py` — update `ApplySkill` to create graph-level hyperedges
- `tests/test_engine/` — hyperedge resolution, hook execution, propagation tests
- `tests/test_models/` — hyperedge model serialization tests
- `tests/test_validation/` — hyperedge validation tests

## Decisions

- **Graph-level, not node-level.** Hyperedges live in `Graph.hyperedges`, not as fields on individual nodes. Nodes don't "know" their hyperedges — the resolver computes attachments from selectors. This keeps the node model clean and supports dynamic attachment patterns.
- **Runtime resolution, not build-time mutation.** Hyperedges modify execution behavior when the engine runs, not when the graph is saved. The original graph definition is preserved. This enables context-dependent behavior (e.g., a guardrail that only activates when budget is exceeded).
- **Hooks are injected, not replacing.** The `pre_prompt` hook adds to messages, it doesn't replace the node's own system_prompt. Multiple hyperedges compose additively (skills/styles prepend; guardrails append constraints).
- **Guardrails are advisory by default.** `hyperedge_enforcement="warn"` logs violations without blocking. `"strict"` blocks. This matches the 14-2 boundary enforcement pattern.
- **`tags` promotion is backward compatible.** New `NodeBase.tags` field with `metadata["tags"]` fallback during resolution. Existing graphs continue to work.
- **Global attachments are explicit.** Use `attach_globally=True` for graph-wide policies instead of relying on empty selector semantics.
- **Start with skill/style (pre_prompt), expand to guardrail/override.** Skill injection is the most immediately useful hook and validates the architecture. Guardrails and overrides add complexity (blocking, arg rewriting) and should be tested after the basic pipeline is solid.

## Notes

- The `conditions.py` safe expression evaluator can be reused for guardrail/validation rule evaluation.
- The `ValidatorNode` (Phase 6) is an explicit node for data validation at specific points. Guardrail hyperedges are implicit validation attached to any node — different scope, complementary purpose.
- 15-2 (markdown syntax) depends on these models for compiler/decompiler.
- 15-3 (dynamic model selection) override hyperedges can set `model_policy` on attached nodes — coordinate model resolution.
- 14-2 boundary contracts define sub-graph isolation. Hyperedge propagation (`propagate=True`) must respect boundary enforcement mode.
