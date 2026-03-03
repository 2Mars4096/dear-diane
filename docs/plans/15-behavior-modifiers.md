# 15: Phase 9B — Behavior Modifiers

**Status:** completed
**Goal:** Implement hyperedges (skills, guardrails, style rules, overrides) as first-class graph-level constructs that attach to arbitrary subsets of nodes and modify execution behavior at runtime — plus a dynamic model selection layer that replaces static per-node model assignment with policy-driven, budget-aware routing.

## Motivation

Phase 9B is the second Deep Systems cluster. It turns two architecture-level concepts — hyperedges and model policies — into working runtime machinery:

- **Hyperedges are designed but not implemented.** `docs/architecture.md` §Hyperedges describes four types (skill, guardrail, style, override) with attachment scopes (node ID, node type, tags, subgraph), precedence rules, and execution hooks. `server/skill_library.py` has a stopgap (build-time prompt injection via `ApplySkill` in `graph_mutator.py`), but this is static graph mutation — not runtime behavior modification. No `Hyperedge` model exists in the graph schema. No executor hooks exist for pre_prompt, post_output, tool_call interception, or validation.
- **Model selection is static.** Each `LLMOperator` declares a fixed `model` string, falling back to `EngineConfig.llm_default_model`. `RetryPolicy.fallback_model` provides one-step fallback on failure. There is no budget awareness, no cascade strategy, no learned assignment, and no per-run cost tracking. The `ProviderRegistry` resolves model→provider but has no concept of selecting *which* model to use.
- **9B is prerequisite for downstream features.** The self-evolving orchestrator (backlog Tier 3) needs rule hyperedges. The coding assistant proof-of-concept needs skill injection. Phase 10 lightweight skills (prompt injection stopgap) becomes unnecessary once real hyperedges land. Dynamic model selection enables cost-controlled autonomous workflows.

## Existing Infrastructure (baseline)

| Component | Location | What exists | Gap |
|---|---|---|---|
| Architecture spec | `docs/architecture.md` §Hyperedges | Four types, attachment scopes, precedence, hook points | Prose-only — not implemented |
| `SKILL_LIBRARY` | `server/skill_library.py` | 2 skills (`management_science_writing`, `informs_latex_style`); `inject_as: "system"` | Build-time only; prepends text to `system_prompt`; no runtime hooks |
| `ApplySkill` | `server/graph_mutator.py` | Mutation op targeting nodes by ID or tag; injects skill text into prompt fields | Static graph mutation — skill text is baked in, not resolved at runtime |
| `NodeExecutor` protocol | `engine/executor.py` | `execute(node, inputs, context) -> NodeResult` | No pre/post hooks; no middleware chain |
| `ExecutionContext` | `engine/executor.py` | Config, state, context stores, event callback, provider registry | No behavior modifier field; no hook registry |
| `LLMExecutor` | `executors/llm.py` | Builds `messages` from `node.system_prompt` + rendered prompt; calls provider | No pre_prompt injection point; no post_output hook |
| `ToolExecutor` | `executors/tool.py` | Looks up tool function, merges args, calls directly | No tool_call interception; no approval gate |
| `NodeBase` | `models/nodes.py` | `metadata` dict available but unused for hyperedge refs | No `attached_hyperedges` field |
| `Graph` | `models/graph.py` | `nodes`, `edges`, `sub_graphs`, `shared_context`, `artifact_refs` | No `hyperedges` field |
| `EdgeBase` / variants | `models/edges.py` | `DataEdge`, `ControlEdge`, `ContextEdge` — all binary (source→target) | No hyperedge type |
| `ProviderRegistry` | `providers/registry.py` | `resolve(model) -> LLMProvider` via overrides → prefix → default | Routes to providers; does not select models |
| `LLMOperator.model` | `models/nodes.py` | Static string or `None` (uses default) | No `model_policy` field |
| `RetryPolicy.fallback_model` | `models/nodes.py` | Single fallback on failure | One-step; no cascade, no budget awareness |
| `COST_PER_1K_TOKENS` | `providers/costs.py` | Static cost table + `estimate_cost()` | Estimation only; no tracking, no budgets |
| `_execute_node` | `engine/scheduler.py` | Resolves inputs → calls executor → stores outputs | No hook insertion before/after executor call |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [15-1](15-1-hyperedge-engine-runtime.md) | Hyperedge Engine Runtime | Hyperedge models, graph schema, execution hooks (pre_prompt, tool_call, post_output, validation), attachment resolution, precedence, migration from `SKILL_LIBRARY` | `models/`, `engine/`, `executors/`, `validation/` |
| [15-2](15-2-hyperedge-markdown-syntax.md) | Hyperedge Markdown Syntax | Skill/rule `.md` file format, workflow reference syntax, attachment scope, loader/compiler/decompiler, builder API, editor palette | `loader/`, `builder/`, `editor/`, `server/` |
| [15-3](15-3-dynamic-model-selection.md) | Dynamic Model Selection | `model_policy` field, policy strategies (static/budget/cascade/capability/router), `ModelSelector` abstraction, budget tracking, cost-aware routing; learned assignment deferred until run-history/memory foundations are in place | `models/`, `providers/`, `engine/`, `executors/` |

## Dependencies / Sequencing

1. **15-1 first.** Defines hyperedge models, graph schema changes, and the runtime hook system. Everything else depends on this.
2. **15-2 second.** Adds markdown authoring and builder API for hyperedges. Requires 15-1 models and runtime to exist.
3. **15-3 independent of 15-1/15-2 but benefits from sequencing after 15-1.** Model policy is a node-level feature, not a hyperedge, but override-type hyperedges (15-1) can set model policies on attached nodes — so 15-3 model resolution should be aware of hyperedge overrides.
4. **Phase 9A (14-*) is not a hard dependency** but context scoping boundaries (14-2) inform how hyperedges propagate into sub-graphs. 15-1 should define propagation rules compatible with 14-2's boundary enforcement model.

## Success Criteria

- Hyperedges are first-class graph-level objects with typed models, persisted in graph JSON, visible in the editor, and resolved at runtime.
- The four hook points (pre_prompt, tool_call, post_output, validation) are operational and composable.
- Existing `SKILL_LIBRARY` / `ApplySkill` functionality is preserved (backward compat) and superseded by runtime hyperedges.
- Skills and rules can be authored as markdown files and referenced from workflow files.
- Model selection supports at least static, budget-aware, and cascade strategies with per-run cost tracking.
- All three authoring surfaces (builder, markdown, editor) can create and manage hyperedges.

## Decisions

- **Hyperedges are graph-level, not edge-level.** They are not a subtype of `EdgeBase`. They attach to node sets via selectors, not via binary source→target. Stored in `Graph.hyperedges`, not mixed into `Graph.edges`.
- **Runtime hooks, not static mutation.** Hyperedges modify behavior at execution time (prompt injection, output validation, tool interception) — not by baking changes into the graph dict before execution. This preserves the original graph definition and allows context-dependent resolution.
- **Backward compatible.** Graphs without `hyperedges` field run identically. `ApplySkill` continues to work as a build-time convenience. Enforcement level is configurable.
- **Skills are the simplest hyperedge.** Start with `pre_prompt` skill injection (migrating from `SKILL_LIBRARY`), then expand to guardrails, style, and overrides.
- **Dynamic model selection is orthogonal to hyperedges** but shares the "behavior modifier" framing. Override hyperedges can set model policies; `ModelSelector` resolves them.

## Notes

- Phase 10's "Lightweight skills (prompt injection)" backlog item becomes unnecessary once 15-1 and 15-2 land. It should be marked as superseded.
- The self-evolving orchestrator (backlog Tier 3) needs rule hyperedges to inject learned principles as guardrails. 15-1 enables this.
- The coding assistant PoC and science-cursor rebuild both need skill hyperedges (see `development-plan.md` §6).
- 14-2 context scoping boundaries define how context crosses agent boundaries. Hyperedge propagation rules should be compatible: a hyperedge attached to a parent graph propagates to sub-graphs unless explicitly excluded (matching the architecture spec).
- Learned assignment is explicitly out of initial 15-3 scope; it should be scheduled as a follow-up after Phase 8 run history (13-1) and Phase 9A session memory (14-1) provide reliable training signals.
