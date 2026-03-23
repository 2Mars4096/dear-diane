# 41-1: LLM Core & Model Gateway

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** not-started
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
- [ ] 1-1. Inventory every current LLM call path across chat, concierge, meta, engine executors, and server helpers. **Deliverable: a table in this plan's Notes section** listing each call site, its current provider-resolution path, and which gateway concerns (retry, PII, telemetry, budget) it currently applies. This inventory is the foundation for all migration work in section 4.
- [ ] 1-2. Define shared request models for completion and streaming calls, including model, temperature, max tokens, tools, timeout, and telemetry metadata.
- [ ] 1-3. Define shared response / usage contracts so callers stop normalizing provider outputs ad hoc.
- [ ] 1-4. Decide which concerns are mandatory in the gateway vs optional wrappers: retries, timeout policy, PII wrapping, telemetry, budgets, fallback, and provider-specific capability flags.

### 2. Centralize provider registry and model routing
- [ ] 2-1. Create one registry builder used by engine, server startup, and local startup.
- [ ] 2-2. Carry `model_provider_map` and other override rules through every surface that builds model state.
- [ ] 2-3. Make tier-map and model-selection precedence explicit and testable.
- [ ] 2-4. Keep OpenAI-compatible fallback behavior, but move it behind the shared builder instead of repeating it in multiple call sites.

### 3. Move privacy / reliability policy into the shared path
- [ ] 3-1. Move or wrap PII tokenization at the gateway level so direct concierge/model helper calls cannot bypass it.
- [ ] 3-2. Centralize timeout and retry policy configuration instead of having request-specific partial implementations.
- [ ] 3-3. Make budget and telemetry hooks available for both single-shot and streaming paths.
- [ ] 3-4. Decide how workflow runtime should consume this boundary without importing server-only concerns.

### 4. Migrate existing callers
- [ ] 4-1. Replace direct provider resolution in concierge runtime and tier executors with the shared gateway.
- [ ] 4-2. Move `ChatManager` provider resolution to the same gateway-backed path.
- [ ] 4-3. Route meta planning / codegen / diagnosis helper calls through the same path.
- [ ] 4-4. Adapt engine LLM executors and related helper executors to consume the gateway or a runtime-safe adapter over it.

### 5. Lock the behavior with tests
- [ ] 5-1. Add regressions for model override routing parity across engine, chat, and local mode.
- [ ] 5-2. Add regressions proving concierge-side direct helper calls get the same PII treatment as chat turns.
- [ ] 5-3. Add retry / timeout / telemetry tests around the new gateway rather than only around surface adapters.
- [ ] 5-4. Add at least one “single source of truth” test for provider registry construction so startup paths cannot drift again.

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
- The task 1-1 call-site inventory table should be filled in during implementation and kept in this file's Notes section as a living reference.
