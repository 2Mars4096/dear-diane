# 18-2: Caching Layer

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** not-started
**Goal:** Eliminate redundant LLM calls through provider-level prompt caching, node result memoization, and an optional semantic response cache — reducing both token consumption and latency.

## Motivation

Many LLM calls in a workflow are partially or fully redundant: loop iterations share identical system prompts, re-runs repeat deterministic nodes, and similar prompts across different runs produce near-identical responses. Each redundant call wastes tokens and time. Caching at three levels — provider, node, and semantic — captures these savings with different cost/complexity tradeoffs.

## Tasks

- [ ] 1. Provider prompt caching
  - [ ] 1-1. **Anthropic `cache_control`:** Identify static message blocks (system prompt, few-shot examples) in `AnthropicProvider.complete()` / `.stream()`. Add `cache_control: {"type": "ephemeral"}` breakpoints on long static prefixes (>1024 tokens). Track `cache_creation_input_tokens` and `cache_read_input_tokens` from response `usage`.
  - [ ] 1-2. **OpenAI automatic caching:** Structure requests to maximize prefix sharing — identical system messages and tool definitions across calls. Document the requirements (deterministic message ordering, same tools block). No code change needed beyond ensuring message construction is stable.
  - [ ] 1-3. **Google Gemini `cached_content`:** For workflows with repeated long system instructions, use the `caching.CachedContent.create()` API to create a cached context. Manage TTL and cleanup. Implement in `GoogleProvider`.
  - [ ] 1-4. **Provider protocol extension:** Add `supports_prompt_caching: bool` property and `apply_cache_hints(messages: list[dict]) -> list[dict]` method to `LLMProvider` protocol. Default implementation: no-op passthrough.
  - [ ] 1-5. **Cache metrics:** Extend `CompletionResult.usage` to include `cached_input_tokens: int | None`. Feed into `CostTracker` with reduced cost rate for cached tokens (Anthropic: 90% discount on cache reads; OpenAI: 50% discount).

- [ ] 2. Node result memoization
  - [ ] 2-1. Add `memoize: bool` field to `NodeBase` (default `False`). When true, node result is cached by content-hash of its effective inputs.
  - [ ] 2-2. **Hash key construction:** `sha256(node_type, prompt_template_hash, input_content_hash, model, temperature, output_schema_hash)`. Temperature > 0 means non-deterministic — memoize only if explicitly opted in (user accepts variance).
  - [ ] 2-3. **Cache storage:** In-memory `dict` during a single run. Optional persistent cache (`~/.dan/cache/` or configurable path) for cross-run memoization of deterministic nodes.
  - [ ] 2-4. **Cache lookup in scheduler:** Before dispatching to executor, check memoization cache. On hit, skip execution, return cached `NodeResult`, emit `CACHE_HIT` event with node_id, tokens_saved, cost_saved.
  - [ ] 2-5. **Cache invalidation:** Clear entry when node config changes (prompt template, model, temperature, output schema). Global `--no-cache` flag on `Engine.run()` / CLI to bypass all memoization. Optional per-node `cache_ttl: int | None` (seconds).
  - [ ] 2-6. **Emit events:** `CACHE_HIT` (node_id, saved_tokens, saved_cost), `CACHE_MISS` (node_id, cache_key_hash), `CACHE_INVALIDATED` (node_id, reason).

- [ ] 3. Semantic response cache
  - [ ] 3-1. Optional `SemanticCache` layer: embed the rendered prompt, search for semantically similar cached prompts within a configurable threshold.
  - [ ] 3-2. Reuse existing RAG infrastructure (9-1): embed with same pipeline, store in a dedicated `__semantic_cache__` collection. If RAG is not configured, semantic cache is unavailable (graceful degradation).
  - [ ] 3-3. Configurable similarity threshold (default 0.95 — very high to avoid false matches) and staleness TTL (default 24h).
  - [ ] 3-4. Only enabled for nodes with `temperature=0` and deterministic prompt patterns. `semantic_cache: bool` field on `LLMOperator` (default `False`).
  - [ ] 3-5. On hit: return cached response, emit `SEMANTIC_CACHE_HIT` event. On miss: after execution, store result with embedding for future queries.

- [ ] 4. Cache management
  - [ ] 4-1. Max cache size (memory and disk) with LRU eviction policy. Configurable via `EngineConfig.cache_max_size_mb`.
  - [ ] 4-2. Per-run cache statistics: total hits, misses, tokens saved, cost saved, hit rate. Expose in `CostTracker` summary.
  - [ ] 4-3. Cache warming for loops: on first iteration, cache system prompt and static context; subsequent iterations benefit from provider cache hits automatically.
  - [ ] 4-4. `POST /api/cache/clear` endpoint for manual cache management. `GET /api/cache/stats` for cache health metrics.

- [ ] 5. Tests
  - [ ] 5-1. Unit tests: cache key hashing determinism, LRU eviction, TTL expiry, invalidation on config change, `--no-cache` bypass
  - [ ] 5-2. Provider caching tests: mock Anthropic response with `cache_creation_input_tokens` / `cache_read_input_tokens`, verify cost adjustment in `CostTracker`
  - [ ] 5-3. Memoization integration test: run a 3-node chain twice with identical inputs, verify second run skips execution for memoized nodes
  - [ ] 5-4. Semantic cache test: two similar prompts (above threshold) return cached result; two dissimilar prompts (below threshold) execute normally
  - [ ] 5-5. Backward compat: nodes without `memoize` or `semantic_cache` fields execute identically to current behavior

## Primary Files

- `src/dan/providers/__init__.py` — `supports_prompt_caching`, `apply_cache_hints` protocol additions; `cached_input_tokens` in `CompletionResult`
- `src/dan/providers/anthropic.py` — `cache_control` header injection in message construction
- `src/dan/providers/google.py` — `cached_content` API integration
- `src/dan/engine/cache.py` *(new)* — `NodeResultCache` (in-memory + persistent), `SemanticCache` (RAG-backed)
- `src/dan/models/nodes.py` — `memoize`, `cache_ttl`, `semantic_cache` fields
- `src/dan/engine/events.py` — `CACHE_HIT`, `CACHE_MISS`, `CACHE_INVALIDATED`, `SEMANTIC_CACHE_HIT` event types
- `src/dan/engine/scheduler.py` — cache lookup before executor dispatch, cache write after execution
- `src/dan/engine/executor.py` — `cache_max_size_mb` on `EngineConfig`
- `src/dan/providers/cost_tracker.py` — cached-token cost adjustments, cache statistics
- `src/dan/server/app.py` — `/api/cache/clear`, `/api/cache/stats` endpoints

## Decisions

- (filled in during execution)

## Notes

- **Provider caching is the highest-ROI task.** Anthropic's `cache_control` gives 90% cost reduction on cached reads with ~zero implementation complexity. Gemini's `cached_content` offers 75% reduction. OpenAI's auto-caching is free (50% discount) if requests are structured correctly. These should land first.
- **Memoization is most valuable for loops and re-runs.** A 10-iteration loop where each iteration shares the same system prompt and similar inputs benefits enormously. Re-running a failed workflow should skip all previously-succeeded deterministic nodes.
- **Semantic cache is experimental.** The 0.95 threshold is deliberately conservative — better to miss a cache hit than serve a wrong answer. This feature should remain opt-in and off by default until confidence is established.
- **Persistent cross-run cache raises staleness concerns.** A cached response from yesterday may be wrong if the world has changed (e.g., web search results). TTL and explicit invalidation are essential safeguards.
