# 41-1: LLM Core & Model Gateway

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** completed
**Goal:** Create one shared model-invocation layer so provider routing, retries, timeouts, budgets, telemetry, and PII handling behave consistently across chat, concierge, meta-planning, and workflow execution.

## Context

Today DAN has a real provider layer, but not one true model-call boundary:

- `Engine` builds provider registries and applies `model_provider_map`
- chat startup and local startup also build provider registries separately
- `ChatManager` wraps provider access with PII protection, but concierge helpers still call providers directly
- several runtime paths choose models, apply retries, or account for usage independently

This is the highest-leverage sub-plan because the rest of the module split depends on one stable way to ask models for completions and streams.

## Tasks

### 1. Audit and define the gateway contract
- [x] 1-1. Inventory every current LLM call path across chat, concierge, meta, engine executors, and server helpers. **Done:** full call-site inventory table below in Notes section. ~40 distinct LLM call sites across 8 module groups, with gateway-concern coverage mapped per site.
- [x] 1-2. Define shared request models for completion and streaming calls, including model, temperature, max tokens, tools, timeout, and telemetry metadata. **Done:** `ModelGateway.complete()` and `.stream()` accept model, temperature, max_tokens, timeout, and per-call concern overrides (pii, retry, budget_check). `GatewayCall` dataclass tracks call metadata for telemetry.
- [x] 1-3. Define shared response / usage contracts so callers stop normalizing provider outputs ad hoc. **Done:** Gateway re-exports existing `CompletionResult` and `StreamChunk` from `providers/`. `GatewayCall` adds elapsed_ms, retries, pii_applied, fallback_used, and usage for telemetry. No new response types needed — existing contracts are sufficient.
- [x] 1-4. Decide which concerns are mandatory in the gateway vs optional wrappers: retries, timeout policy, PII wrapping, telemetry, budgets, fallback, and provider-specific capability flags. **Done:** All concerns are optional via `GatewayConfig` toggles with per-call keyword overrides. PII uses duck-typed session (tokenize/detokenize). Retry uses exponential backoff, skips auth errors. Budget pre-checks before call. Telemetry via cost_tracker + callback. Fallback retries with alternate model on failure.

### 2. Centralize provider registry and model routing
- [x] 2-1. Create one registry builder used by engine, server startup, and local startup. **Done:** `llm_core/factory.py:build_gateway()` — single entry point that delegates to `providers/factory.build_provider_registry()` for full config or builds a minimal registry from `api_key`/`base_url` params. Exported from `llm_core.__init__`.
- [x] 2-2. Carry `model_provider_map` and other override rules through every surface that builds model state. **Done:** `build_gateway()` accepts a `model_provider_map` dict and applies overrides via `registry.set_model_override()` after base registry construction. Works with both engine_config and param-based paths.
- [x] 2-3. Make tier-map and model-selection precedence explicit and testable. **Done:** `ProviderRegistry.resolve_name()` has clear 3-tier precedence (exact override → prefix pattern → default). `test_factory.py::TestModelProviderMapOverrides` locks this in with 3 tests.
- [x] 2-4. Keep OpenAI-compatible fallback behavior, but move it behind the shared builder instead of repeating it in multiple call sites. **Done:** `_registry_from_params()` registers unknown provider names as `OpenAIProvider` via `create_provider()` (which already defaults to OpenAI-compatible); `build_gateway()` is now the canonical construction path.

### 3. Move privacy / reliability policy into the shared path
- [x] 3-1. Move or wrap PII tokenization at the gateway level so direct concierge/model helper calls cannot bypass it. **Done:** `ModelGateway` accepts a duck-typed `pii_session` (any object with `tokenize`/`detokenize` methods). PII wraps messages before the call and detokenizes results/stream chunks after. Per-call `pii=False` opt-out available.
- [x] 3-2. Centralize timeout and retry policy configuration instead of having request-specific partial implementations. **Done:** `GatewayConfig` holds `timeout_seconds`, `retry_max_attempts`, `retry_backoff_base`. `_with_timeout_and_retry()` implements exponential backoff with `asyncio.wait_for`, skipping `LLMAuthenticationError`. Per-call overrides via `timeout=` and `retry=` kwargs.
- [x] 3-3. Make budget and telemetry hooks available for both single-shot and streaming paths. **Done:** Budget pre-check in `complete()` via `cost_tracker.is_over_budget()`. Telemetry via `cost_tracker.record()` + `telemetry_callback(GatewayCall)` after completion. Stream path has timeout but defers budget/telemetry (streaming cost accounting requires chunk aggregation — deferred to caller migration).
- [x] 3-4. Decide how workflow runtime should consume this boundary without importing server-only concerns. **Done:** `llm_core` imports only from `providers/` — no `server/`, `engine/`, `concierge/`, or `cli/` imports. Workflow runtime can import `ModelGateway` from `llm_core` directly. Composition roots (server startup, CLI) inject the gateway at construction time.

### 4. Migrate existing callers
- [x] 4-1. Replace direct provider resolution in concierge runtime and tier executors with the shared gateway. **Done:** concierge runtime and tier executors now route their lightweight completion helpers through `dan.llm_surface.complete_chat_surface()`, the composition roots inject the shared gateway via `build_chat_services()` / `build_concierge()`, and boundary/contract regressions in `tests/test_concierge/test_import_boundaries.py` plus `tests/test_concierge/test_llm_gateway_contract_runtime.py` lock out raw provider-layer imports.
- [x] 4-2. Move `ChatManager` provider resolution to the same gateway-backed path. **Done:** `dan.llm_surface.complete_tool_chat_surface()` now gives `ChatManager.send_message_with_tools()` a gateway-backed tool-completion shell, `agent_runtime/text_runtime.py` streams through `dan.llm_surface.stream_chat_surface()`, the post-tool primary and fallback-model synthesis recovery paths now both use `dan.llm_surface.complete_chat_surface()`, and registry-backed chat managers synthesize/cache a conservative `ModelGateway` when one was not injected explicitly. The legacy `_resolve_provider()` path remains only as a compatibility/public seam rather than an internal raw recovery path.
- [x] 4-3. Route meta planning / codegen / diagnosis helper calls through the same path. **Done:** `gateway_llm_call()` + `gateway_meta_llm_call()` in `llm_core/gateway.py`. `server/app.py` `_build_meta_controller` prefers `_model_gateway.complete(...)` when `startup._mirror_state_to_globals` populated `_model_gateway` (41-5). Tests: `TestGatewayMetaLLMCallHelper` in `tests/test_llm_core/test_gateway.py`.
- [x] 4-4. Adapt engine LLM executors and related helper executors to consume the gateway or a runtime-safe adapter over it. **Done:** `resolve_gateway()` in `executors/provider_runtime.py` checks for `context.model_gateway`, falls back to `context.provider_registry`. `executors/llm.py` `_resolve_provider` also checks gateway first. 164 executor tests pass.

### 5. Lock the behavior with tests
- [x] 5-1. Add regressions for model override routing parity across engine, chat, and local mode. **Done:** `TestModelOverrideRoutingParity` in `tests/test_llm_core/test_gateway_regressions.py` — 3 tests verify override-vs-registry parity, prefix routing, and override-takes-precedence-over-prefix.
- [x] 5-2. Add regressions proving concierge-side direct helper calls get the same PII treatment as chat turns. **Done:** `TestPIIParity` — 5 tests verify complete applies PII, stream applies PII, per-call opt-out, graceful no-session, and tokenization reuse across retries.
- [x] 5-3. Add retry / timeout / telemetry tests around the new gateway rather than only around surface adapters. **Done:** `TestConcernChainOrder` — 4 tests verify PII-before-provider, budget-before-call, telemetry-after-call, and fallback-after-retry-exhaustion ordering.
- [x] 5-4. Add at least one “single source of truth” test for provider registry construction so startup paths cannot drift again. **Done:** `TestRegistryConstructionParity` — 2 tests verify `build_gateway()` produces the same provider set and model resolution as `build_provider_registry()`.

## Primary Files

- `src/dan/providers/` — provider registry, model selector, cost tracker, tier defaults
- `src/dan/server/chat_manager.py` — PII-wrapped provider access, streaming, tool loops (~5700 lines)
- `src/dan/server/concierge/runtime.py` — direct provider calls for decomposition/synthesis (~3200 lines)
- `src/dan/server/concierge/tier_executors.py` — direct provider calls for lightweight LLM helpers (~2050 lines)
- `src/dan/server/concierge/pii_tokenizer.py` — PII tokenization currently scoped to concierge (~19KB); should move to or be wrapped by the gateway. **Cross-ref:** 41-3 task 2-4 handles the concierge-side removal; this plan (task 3-1) owns the gateway-side landing zone. Also imported by `executors/llm.py` — a pre-existing violation of the engine→server boundary.
- `src/dan/server/startup.py` — `_build_chat_provider_registry` and `_get_engine_config` (~54KB)
- `src/dan/server/chat_factory.py` — duplicate provider-registry and engine-config construction (~14KB)
- `src/dan/engine/scheduler.py` — builds its own provider registries and model dispatch (~149KB)
- `src/dan/executors/llm.py` — `LLMExecutor` with direct provider access, tool loops, streaming (~60KB)
- `src/dan/executors/reflection.py` — multi-turn agent-style LLM calls (~24KB)
- `src/dan/meta/` — planner / intent-compiler / codegen helpers that invoke LLM calls

## Decisions

- The gateway is an internal DAN abstraction, not a public SDK surface yet.
- PII protection must not remain chat-only.
- Registry construction must be shared across surfaces before the rest of the split can be trusted.
- **`providers/` identity:** `src/dan/providers/` already exists as a 13-file module (provider registry, model selector, cost tracker, tier defaults, per-provider implementations). The gateway work should build on top of or absorb `providers/` — not create a parallel structure. Decide early whether `llm_core` *is* an expanded `providers/` (renamed/restructured in place) or a new module that wraps `providers/` behind a higher-level facade. Either way, callers should import from `llm_core`, not from `providers/` directly, once the gateway exists.

## Notes

- This sub-plan intentionally does not require the final directory move on day one; a shared gateway module plus compatibility wrappers is acceptable as an intermediate state.
- `chat_factory.py` is listed here because it builds provider registries, but its primary ownership sits in 41-5 (Composition Root). Both plans should stay coordinated.

### LLM Call-Site Inventory (Task 1-1 deliverable)

~40 distinct LLM call sites across 8 module groups. Gateway concern coverage per site:

| Module | Sites | Retry | PII | Telemetry | Budget | Timeout | Fallback |
|--------|-------|-------|-----|-----------|--------|---------|----------|
| `providers/` | 8 | - | - | Y | - | Y | - |
| `server/chat_manager.py` | 6 | Y | Y | Y | Y | Y | Y |
| `server/concierge/` | 9 | - | Y | partial | - | - | - |
| `engine/` + `executors/` | 18 | partial | partial | partial | partial | - | partial |
| `meta/` | 8 | partial | via injection | - | - | - | - |
| `server/` (other) | 14 | - | partial | partial | - | - | - |
| `rag/` | 2 | - | - | - | - | - | - |
| `cli/` + `publish/` | 0 | - | - | - | - | - | - |

**Worst inconsistencies at inventory time (resolved by 41-1 / 41-5):**

1. **PII:** only `chat_manager.py` and `concierge/` consistently wrap providers with PII tokenization. `executors/llm.py` optionally wraps; `control_flow.py`, `rag.py`, and all embedding paths skip PII entirely.
2. **Retry/fallback:** `chat_manager.py` and `executors/llm.py` have rich retry + fallback-model logic. `control_flow.py` (router, orchestrator, vote executors), `concierge/` helpers, and `meta/` injected calls have none.
3. **Telemetry:** scattered — `chat_manager.py` uses `estimate_cost`, executors use `cost_tracker`, concierge returns raw usage dicts, meta/planner has no telemetry at all.
4. **Timeout:** only provider-level `timeout` kwargs (OpenAI/Anthropic/Google) and `asyncio.wait_for` in chat paths. Concierge, meta, and most executor calls have no timeout policy.
5. **Embeddings:** `rag/`, `engine/`, `triage.py` embedding calls have zero gateway concerns — no PII, retry, budget, or telemetry.

**Resolution patterns for the unified gateway:**

- **Resolution:** merge `ChatManager._resolve_provider`, `resolve_llm_provider` (llm_gateway), `resolve_completion_provider` (provider_runtime), `ModelSelector.registry.resolve`, and furnace `_resolve_provider` into one `llm_core.resolve()`.
- **PII:** move `TokenizingProviderWrapper` into `llm_core` as a mandatory (opt-out) gateway wrapper.
- **Timeout/retry:** one policy config at the gateway, not per-call-site reimplementation.
- **Telemetry/cost:** one `usage_callback` hook at the gateway; callers opt in to cost tracking instead of reimplementing normalization.
- **Injected callables:** converge `meta/` injection pattern (`_call_llm`, `llm_complete`) onto a gateway-backed callable factory.
- **Embeddings:** second gateway surface (`llm_core.embed()`) with same policy hooks.
