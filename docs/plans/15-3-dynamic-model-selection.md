# 15-3: Dynamic Model Selection

**Parent:** [15-behavior-modifiers](15-behavior-modifiers.md)
**Status:** completed
**Goal:** Replace static per-node model assignment with a policy-driven model selection layer that supports budget-aware routing, cascade fallback chains, capability-based matching, and per-run cost tracking — enabling cost-controlled autonomous workflows.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `LLMOperator.model` | `models/nodes.py` | Static `str | None`; `None` falls back to `EngineConfig.llm_default_model` | No policy field; no budget awareness; no capability matching |
| `RetryPolicy.fallback_model` | `models/nodes.py` | Single fallback model string; tried after `max_retries` failures | One-step; no cascade; no cost-based selection |
| `ProviderRegistry` | `providers/registry.py` | `resolve(model) -> LLMProvider` via override → prefix → default | Routes model string to provider; does not select *which* model |
| `LLMProvider` protocol | `providers/__init__.py` | `complete()`, `stream()` — accepts `model`, `messages`, `temperature`, etc. | No cost reporting; no capability metadata |
| `CompletionResult` | `providers/__init__.py` | `text`, `usage` (prompt_tokens, completion_tokens, total_tokens) | Tokens tracked but not cost; result not fed back for budget tracking |
| `COST_PER_1K_TOKENS` | `providers/costs.py` | Static dict mapping model names to `(input_cost, output_cost)` per 1K tokens | Estimation only; covers ~30 models; no dynamic cost tracking |
| `estimate_cost()` | `providers/costs.py` | `estimate_cost(model, prompt_tokens, completion_tokens) -> float | None` | Returns `None` for unknown models; not called by engine |
| `LLMExecutor` | `executors/llm.py` | `model = node.model or context.config.llm_default_model` → `provider_registry.resolve(model)` | Direct resolution; no policy layer; no cost aggregation |
| `EngineConfig` | `engine/executor.py` | `llm_default_model`, `llm_api_key`, `llm_base_url`, provider-specific keys | No budget fields; no policy defaults |
| `OrchestratorNode.model` | `models/control_flow.py` | Static model string for orchestrator LLM calls | Same limitation as `LLMOperator` |
| `RouterNode.model` | `models/control_flow.py` | Static model string for routing decisions | Same limitation |
| Node cost display | `editor/components/DanNode.tsx` | `nodeTimings` shows duration badges; `LogPanel` shows per-node token counts | Duration only; no cost badge; no budget indicator |
| Run cost aggregation | N/A | Not implemented | No per-run total cost tracking |

## Tasks

- [ ] 1. Define model policy models
  - [ ] 1-1. Create `src/dan/providers/model_policy.py` with `ModelPolicy` base model: `strategy` (Literal: `"static"`, `"budget"`, `"cascade"`, `"capability"`, `"router"`), `constraints` (optional `ModelConstraints`). Each strategy has a corresponding config model.
  - [ ] 1-2. Define `StaticPolicy`: `model` (str) — current behavior. Default when a node has a bare `model` string.
  - [ ] 1-3. Define `BudgetPolicy`: `preferred_model` (str), `fallback_model` (str), `max_cost_per_call` (float, optional), `max_cost_per_run` (float, optional), `cost_threshold_model` (str — switch to this when budget nears limit). Requires per-run cost tracking.
  - [ ] 1-4. Define `CascadePolicy`: `models` (list[str] — ordered preference), `cascade_on` (list[str] — conditions to try next: `"error"`, `"timeout"`, `"quality_low"`, `"cost_high"`), `max_attempts` (int, default len(models)). Supersedes `RetryPolicy.fallback_model` for multi-step cascades.
  - [ ] 1-5. Define `CapabilityPolicy`: `required_capabilities` (list[str] — e.g. `"code"`, `"vision"`, `"long_context"`, `"json_mode"`, `"tool_use"`), `prefer` (Literal: `"cheapest"`, `"fastest"`, `"strongest"`). Resolved against a model capability registry.
  - [ ] 1-6. Define `RouterPolicy`: `router_model` (str — the model that decides), `candidates` (list[str]), `routing_prompt` (str, optional). LLM-powered model selection per call.
  - [ ] 1-7. Define `ModelConstraints`: `max_input_tokens` (int, optional), `max_output_tokens` (int, optional), `max_cost` (float, optional), `required_provider` (str, optional). Applied as filters regardless of strategy.

- [ ] 2. Integrate model policy into node models
  - [ ] 2-1. Add `model_policy: ModelPolicy | None` field to `LLMOperator` (`models/nodes.py`) and `OrchestratorNode` / `RouterNode` (`models/control_flow.py`). Default `None` means: if `model` is set, use `StaticPolicy`; if neither is set, use engine default.
  - [ ] 2-2. Define resolution precedence: `model_policy` (if present) > `model` (backward compat — treated as `StaticPolicy`) > `EngineConfig.default_model_policy` (if present) > `EngineConfig.llm_default_model` (ultimate fallback).
  - [ ] 2-3. Ensure backward compatibility: existing graphs with `model` strings and no `model_policy` work identically. `model_policy` is Optional and not serialized when None.
  - [ ] 2-4. Add `default_model_policy: ModelPolicy | None` to `EngineConfig` — sets the policy for all nodes that don't specify their own.

- [ ] 3. Build model selector
  - [ ] 3-1. Create `src/dan/providers/model_selector.py` with `ModelSelector` class. Constructor takes `provider_registry: ProviderRegistry`, `cost_tracker: CostTracker`, `capability_registry: ModelCapabilityRegistry`.
  - [ ] 3-2. Core method: `async select(policy: ModelPolicy, node: NodeBase, context: ExecutionContext) -> str` — resolves policy to a concrete model string. For static: return model. For budget: check remaining budget, pick appropriate model. For cascade: return first candidate (caller retries on failure with next). For capability: filter by capabilities, sort by preference. For router: call router model to decide.
  - [ ] 3-3. Add `CascadeHandler` that wraps executor calls: on failure, advance to next model in cascade and retry. Integrate with existing `RetryPolicy` — cascade is tried before `on_failure` handling.
  - [ ] 3-4. Add `ModelSelector` to `ExecutionContext` (or as field on `EngineConfig`). Initialize in `Engine.run()`.

- [ ] 4. Build cost tracking
  - [ ] 4-1. Create `src/dan/providers/cost_tracker.py` with `CostTracker` class. Accumulates per-node and per-run costs from `CompletionResult.usage` + `estimate_cost()`.
  - [ ] 4-2. Core methods: `record(node_id, model, usage: TokenUsage) -> float` (returns cost), `total_cost() -> float`, `node_cost(node_id) -> float`, `remaining_budget() -> float | None`, `is_over_budget() -> bool`.
  - [ ] 4-3. Add `run_budget: float | None` field to `EngineConfig`. When set, `CostTracker` enforces the budget — `BudgetPolicy` switches to cheap model, or engine halts when budget exceeded (configurable via `on_budget_exceeded: "switch" | "warn" | "halt"`). If both policy-level `max_cost_per_run` and engine-level `run_budget` are present, enforce the stricter limit.
  - [ ] 4-4. Hook into `LLMExecutor`: after each LLM call inside the retry loop, call `cost_tracker.record()` with the `usage` dict returned by `_call_llm`. Emit `COST_RECORDED` event with node_id, model, tokens, cost.
  - [ ] 4-5. Add `CostTracker` to `ExecutionContext`. Snapshot cost state in checkpoints for resume.

- [ ] 5. Build model capability registry
  - [ ] 5-1. Create `src/dan/providers/capabilities.py` with `ModelCapabilityRegistry`. Static mapping of model names to capability sets (e.g., `{"claude-sonnet-4-6": {"code", "tool_use", "vision", "long_context", "json_mode"}}`).
  - [ ] 5-2. Cover major models: OpenAI (gpt-4o, gpt-4o-mini, o1, o3-mini), Anthropic (claude-opus-4, claude-sonnet-4-6, haiku), Google (gemini-2.0-flash, gemini-2.5-pro). Extensible via `register(model, capabilities)`.
  - [ ] 5-3. Method: `filter(required: list[str], prefer: str) -> list[str]` — returns models matching all required capabilities, sorted by preference (cheapest: ascending cost, fastest: heuristic latency rank, strongest: descending capability count / model tier).
  - [ ] 5-4. Integrate with `COST_PER_1K_TOKENS` for cost-based sorting. Add latency tier heuristic (fast: <1s/token, medium: 1-3s, slow: >3s) as static metadata alongside cost.

- [ ] 6. Integrate with LLM executor and scheduler
  - [ ] 6-1. Update `LLMExecutor.execute()`: resolve `model_policy` → concrete model string via `ModelSelector.select()` before provider resolution. Replace `model = node.model or config.llm_default_model` with policy-aware resolution.
  - [ ] 6-2. Handle cascade: wrap LLM call in cascade loop. On qualifying failure (matching `cascade_on` conditions), advance to next model. Emit events for each cascade step.
  - [ ] 6-3. Handle budget: before each LLM call, check `cost_tracker.is_over_budget()`. If budget policy, switch model. If strict budget, halt with `BudgetExceeded` error.
  - [ ] 6-4. Update `OrchestratorExecutor` and `RouterExecutor` (in `executors/control_flow.py`): same model resolution pattern via `ModelSelector`.
  - [ ] 6-5. Emit `MODEL_SELECTED` event: `node_id`, `policy_strategy`, `selected_model`, `reason` (e.g., "budget threshold", "cascade fallback", "capability match").

- [ ] 7. Integrate with hyperedge overrides (coordinate with 15-1)
  - [ ] 7-1. Override hyperedges (type=`"override"`, hook=`"pre_prompt"`) can set `model_policy` on attached nodes. When `HyperedgeResolver.apply_pre_prompt()` encounters a model-policy override, it returns the overridden policy alongside modified messages.
  - [ ] 7-2. Precedence: hyperedge override > node-level `model_policy` > node-level `model` > engine default. This allows graph-level rules like "all writing nodes use claude-opus-4" without editing individual nodes.
  - [ ] 7-3. If 15-1 is not yet complete, this task is skipped — model selection works standalone without hyperedge integration.

- [ ] 8. Extend visual editor
  - [ ] 8-1. Add cost badge to `DanNode.tsx`: show `$0.02` badge next to duration badge on completed nodes. Source from `COST_RECORDED` events.
  - [ ] 8-2. Add run cost summary to `EditorToolbar.tsx` or `LogPanel.tsx`: total run cost, per-node breakdown, budget remaining (if set).
  - [ ] 8-3. Add model policy editor to `ConfigPanel.tsx` for LLM/orchestrator/router nodes: strategy selector dropdown, strategy-specific fields. Show resolved model after run completion.
  - [ ] 8-4. Add `model_policy` to `graph.ts` TypeScript types.

- [ ] 9. Tests and documentation
  - [ ] 9-1. Unit tests: `ModelPolicy` serialization for all strategies, `ModelSelector.select()` for each strategy, `CostTracker` accumulation and budget checks, `ModelCapabilityRegistry` filtering and sorting.
  - [ ] 9-2. Integration tests: budget policy switches model mid-run when threshold exceeded, cascade policy falls back on error, capability policy selects cheapest matching model, router policy delegates selection to LLM (mock).
  - [ ] 9-3. Backward compat tests: graphs with bare `model` strings work identically, `model_policy: null` serialization, existing retry/fallback behavior preserved.
  - [ ] 9-4. Update `docs/architecture.md` §Model Heterogeneity: add model policy strategies, cost tracking, capability registry.
  - [ ] 9-5. Update `docs/llm-api-guide.md`: `model_policy` field reference, strategy examples, `run_budget` config, builder API.
  - [ ] 9-6. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/providers/model_policy.py` *(new)* — `ModelPolicy`, `StaticPolicy`, `BudgetPolicy`, `CascadePolicy`, `CapabilityPolicy`, `RouterPolicy`, `ModelConstraints`
- `src/dan/providers/model_selector.py` *(new)* — `ModelSelector` class
- `src/dan/providers/cost_tracker.py` *(new)* — `CostTracker` class, per-node/per-run cost accumulation
- `src/dan/providers/capabilities.py` *(new)* — `ModelCapabilityRegistry`, static model capability data
- `src/dan/providers/costs.py` — extend with latency tier metadata
- `src/dan/models/nodes.py` — add `model_policy` to `LLMOperator`
- `src/dan/models/control_flow.py` — add `model_policy` to `OrchestratorNode`, `RouterNode`
- `src/dan/engine/executor.py` — add `model_selector`, `cost_tracker` to `ExecutionContext`; add `run_budget`, `default_model_policy`, `on_budget_exceeded` to `EngineConfig`
- `src/dan/engine/events.py` — `MODEL_SELECTED`, `COST_RECORDED`, `BUDGET_WARNING`, `BUDGET_EXCEEDED` event types
- `src/dan/engine/scheduler.py` — initialize `ModelSelector`/`CostTracker` in `Engine.run()`; checkpoint cost state
- `src/dan/executors/llm.py` — policy-aware model resolution, cascade handling, cost recording
- `src/dan/executors/control_flow.py` — `OrchestratorExecutor`, `RouterExecutor` use `ModelSelector`
- `editor/src/components/DanNode.tsx` — cost badge
- `editor/src/components/ConfigPanel.tsx` — model policy editor
- `editor/src/components/LogPanel.tsx` — run cost summary
- `editor/src/types/graph.ts` — `ModelPolicy` TypeScript type
- `tests/test_engine/` — model selection, cost tracking, budget enforcement tests
- `tests/test_providers/` — policy resolution, capability filtering tests

## Decisions

- **Policy is a node field, not an engine setting.** Each node can have its own policy. `EngineConfig.default_model_policy` is a convenient default, not a mandate. This preserves per-node model heterogeneity.
- **Budget is per-run.** `run_budget` is set on `EngineConfig` (or per `Engine.run()` call). No persistent cross-run budget tracking (that's a 14-* concern). Cost tracking resets each run.
- **Cascade supersedes `fallback_model`.** `CascadePolicy.models` is a generalization of `RetryPolicy.fallback_model`. For backward compat, a bare `fallback_model` string is treated as a two-step cascade: `[primary, fallback]`.
- **Capability registry is static-first.** Model capabilities are hardcoded initially with a `register()` extension point. Dynamic capability discovery (probing models) is out of scope.
- **Router policy is expensive.** Each LLM call preceded by a routing LLM call. Useful for high-value nodes. Not recommended as default policy. Builder/editor should warn.
- **Cost estimation is best-effort.** Unknown models return `cost=None`. Cost tracking is never a blocking concern — `on_budget_exceeded="warn"` is the safest default.
- **Budget precedence is explicit.** Effective run budget is `min(run_budget, policy.max_cost_per_run)` when both are set; this avoids conflicting controls.

## Notes

- This plan is independent of 15-1/15-2 but coordinates with override hyperedges (task 7). If 15-1 is not complete, model selection works standalone.
- `RouterPolicy` reuses the existing `RouterNode` pattern: LLM picks from candidates. The difference is this operates at the model-selection level, not the workflow routing level.
- Learned assignment (mentioned in todo.md) is out of initial scope. Requires run history (Phase 8) + memory (Phase 9A) to track which models performed well for which tasks. Can be added as a `"learned"` strategy later.
- Phase 10 CLI mode should expose `run_budget` as a CLI flag for headless cost control.
