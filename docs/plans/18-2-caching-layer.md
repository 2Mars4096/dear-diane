# 18-2: Caching Layer

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** in-progress
**Goal:** Eliminate redundant LLM calls through provider-level prompt caching, node result memoization, and an optional semantic response cache — reducing both token consumption and latency.

## Motivation

Many LLM calls in a workflow are partially or fully redundant: loop iterations share identical system prompts, re-runs repeat deterministic nodes, and similar prompts across different runs produce near-identical responses. Each redundant call wastes tokens and time. Caching at three levels — provider, node, and semantic — captures these savings with different cost/complexity tradeoffs.

## Tasks

- [x] 1. Provider prompt caching
  - [x] 1-1. **Anthropic `cache_control`:** Added `apply_cache_hints()` that inserts `cache_control: {"type": "ephemeral"}` on system messages >1024 estimated tokens. `_split_system()` returns content blocks when cache hints are present. `_extract_usage()` and `_extract_cache_tokens()` capture `cache_read_input_tokens` and `cache_creation_input_tokens`.
  - [x] 1-2. **OpenAI automatic caching:** Added `apply_cache_hints()` that ensures system messages lead for stable prefix overlap. `_extract_usage()` reads `prompt_tokens_details.cached_tokens`.
  - [x] 1-3. **Google Gemini `cached_content`:** Added stub `apply_cache_hints()` (no-op). Full `caching.CachedContent.create()` integration deferred.
  - [x] 1-4. **Provider protocol extension:** Added `cached_input_tokens` and `cache_write_tokens` to `CompletionResult`. Added standalone `apply_cache_hints(provider, messages)` helper using duck-typing (avoids Protocol change). `prompt_caching_enabled` field on `EngineConfig` (default True).
  - [x] 1-5. **Cache metrics:** Extended `CostTracker.record()` with `cached_input_tokens` and `cache_write_tokens` kwargs. Added `cache_summary()` returning `{cached_tokens, cache_write_tokens, cache_hit_rate}`. `snapshot()`/`restore()` include cache state. Wired into `LLMExecutor._call_via_provider()` and both `cost_tracker.record()` call sites.

- [ ] 2. Node result memoization *(deferred — partially complete; hash key construction and cache lookup landed, persistent storage and memory-aware invalidation remain)*
  - [x] 2-1. Add `memoize: bool` field to `NodeBase` (default `False`). When true, node result is cached by content-hash of its effective inputs.
  - [x] 2-2. **Hash key construction:** `sha256(node_type, stable_node_config_hash, effective_inputs_hash, policy_signature)`. For LLM nodes include prompt/model/temperature/schema; for tool/code nodes include tool/code config. Temperature > 0 means non-deterministic — memoize only if explicitly opted in (user accepts variance).
  - [ ] 2-3. **Cache storage:** In-memory `dict` during a single run. Optional persistent cache (`~/.dan/cache/` or configurable path) for cross-run memoization of deterministic nodes. **Session memory coordination (14-1):** When persistent memoization is enabled and a `session_id` is active, cross-run cached results are also written to 14-1's `MemoryStore` as session memory entries (`key=cache:{node_id}:{hash}`, `source_run_id`, `writer_node_id`). This enables session-aware cache reuse: a new run within the same session can retrieve memoized results even if the filesystem cache was cleared, and session memory lifecycle (TTL, cleanup) applies uniformly. *(deferred — persistent cross-run cache and session memory coordination; in-memory per-run cache is sufficient for now)*
  - [x] 2-4. **Cache lookup in scheduler:** Before dispatching to executor, check memoization cache. On hit, skip execution, return cached `NodeResult`, emit `CACHE_HIT` event with node_id, tokens_saved, cost_saved.
  - [ ] 2-5. **Cache invalidation:** Clear entry when node config changes (prompt template, model, temperature, output schema, tool/code config). Add `cache_enabled: bool` to `EngineConfig` (default True) and allow per-run override to bypass all memoization/cache usage. Optional per-node `cache_ttl: int | None` (seconds). **Memory-aware invalidation:** If the hash key includes inputs sourced from session memory (14-1) or memory retrieval (14-3), and those memory entries are updated between runs, the cache entry is stale and must be invalidated. Track `memory_dependency_keys` alongside the hash to enable this check. *(deferred — memory-aware invalidation requires dependency tracking across memory and cache layers; basic config-change invalidation is sufficient for now)*
  - [x] 2-6. **Emit events:** `CACHE_HIT` (node_id, saved_tokens, saved_cost), `CACHE_MISS` (node_id, cache_key_hash), `CACHE_INVALIDATED` (node_id, reason).

- [x] 3. Semantic response cache
  - [x] 3-1. Optional `SemanticCache` layer: embed the rendered prompt, search for semantically similar cached prompts within a configurable threshold. **Query normalization before embedding:** normalize prompts (lowercase, collapse whitespace, strip non-semantic formatting differences) before computing the embedding to increase cache hit rates. GPTCache reports 61–68% hit rates with normalization; without it, trivial formatting differences cause cache misses.
  - [x] 3-2. Reuse existing RAG infrastructure (9-1) end-to-end: embed prompts via `EmbeddingProvider` (same registry used by `RAGExecutor`), store in a dedicated `__semantic_cache__` collection using `VectorStore` (Memory/FAISS/Chroma — whichever backend is configured via `VectorStoreFactory`). Search uses `VectorStore.query()` with similarity threshold. If no `EmbeddingProvider` is registered, semantic cache is unavailable (graceful degradation).
  - [x] 3-3. Configurable similarity threshold (default 0.95 — very high to avoid false matches) and staleness TTL (default 24h).
  - [x] 3-4. Only enabled for nodes with `temperature=0` and deterministic prompt patterns. `semantic_cache: bool` field on `LLMOperator` (default `False`).
  - [x] 3-5. On hit: return cached response, emit `SEMANTIC_CACHE_HIT` event. On miss: after execution, store result with embedding for future queries.

- [ ] 4. Cache management *(deferred — partially complete; LRU eviction and stats API landed, loop warming remains)*
  - [x] 4-1. Max cache size (memory and disk) with LRU eviction policy. Configurable via `EngineConfig.cache_max_size_mb`.
  - [x] 4-2. Per-run cache statistics: total hits, misses, tokens saved, cost saved, hit rate. Expose in `CostTracker` summary.
  - [ ] 4-3. Cache warming for loops: on first iteration, cache system prompt and static context; subsequent iterations benefit from provider cache hits automatically. *(deferred — provider cache hints already handle most loop warming benefit; explicit warming is incremental)*
  - [x] 4-4. `POST /api/cache/clear` endpoint for manual cache management. `GET /api/cache/stats` for cache health metrics.

- [x] 5. Tests
  - [x] 5-1. Unit tests: cache key hashing determinism, LRU eviction, TTL expiry, invalidation on config change, `cache_enabled` bypass
  - [ ] 5-2. Provider caching tests: mock Anthropic response with `cache_creation_input_tokens` / `cache_read_input_tokens`, verify cost adjustment in `CostTracker` *(deferred — requires mock provider responses with cache-specific usage fields; unit tests cover CostTracker accounting)*
  - [x] 5-3. Memoization integration test: run a 3-node chain twice with identical inputs, verify second run skips execution for memoized nodes
  - [x] 5-4. Semantic cache test: two similar prompts (above threshold) return cached result; two dissimilar prompts (below threshold) execute normally
  - [x] 5-5. Backward compat: nodes without `memoize` or `semantic_cache` fields execute identically to current behavior

## Primary Files

- `src/dan/providers/__init__.py` — `supports_prompt_caching`, `apply_cache_hints` protocol additions; `cached_input_tokens` in `CompletionResult`
- `src/dan/providers/anthropic_provider.py` — `cache_control` hint injection and cached-token usage extraction
- `src/dan/providers/google_provider.py` — `cached_content` API integration
- `src/dan/providers/openai_provider.py` — deterministic message/tool ordering + cache-friendly request shaping
- `src/dan/engine/cache.py` *(new)* — `NodeResultCache` (in-memory + persistent), `SemanticCache` (RAG-backed)
- `src/dan/rag/__init__.py` — reuse `EmbeddingProvider` and `EmbeddingRegistry` for semantic cache embedding
- `src/dan/rag/stores/__init__.py` — reuse `VectorStore`, `VectorStoreFactory` for semantic cache storage
- `src/dan/engine/memory_store.py` — session memory persistence for cache mirror entries (workflow/session scoped)
- `src/dan/engine/context_runtime.py` — context-level helpers to read/write cache mirror entries via `MemoryStore`
- `src/dan/models/nodes.py` — `memoize`, `cache_ttl`, `semantic_cache` fields
- `src/dan/engine/events.py` — `CACHE_HIT`, `CACHE_MISS`, `CACHE_INVALIDATED`, `SEMANTIC_CACHE_HIT` event types
- `src/dan/engine/scheduler.py` — cache lookup before executor dispatch, cache write after execution
- `src/dan/engine/executor.py` — `cache_max_size_mb` on `EngineConfig`
- `src/dan/providers/cost_tracker.py` — cached-token cost adjustments, cache statistics
- `src/dan/server/app.py` — `/api/cache/clear`, `/api/cache/stats` endpoints

## Decisions

- Implemented cache runtime in `src/dan/engine/cache.py` with in-memory LRU + optional disk persistence, deterministic hash keys, TTL support, and semantic-cache normalization.
- Scheduler cache flow is pre-dispatch lookup + post-success writeback, with cache events (`CACHE_HIT`, `CACHE_MISS`, `CACHE_INVALIDATED`, `SEMANTIC_CACHE_HIT`) and per-run cache summaries.
- Cache management APIs landed in server (`POST /api/cache/clear`, `GET /api/cache/stats`) and engine config now supports cache toggles/limits (`cache_enabled`, `cache_max_size_mb`, `cache_dir`, semantic threshold/ttl).

## Notes

- **Provider caching is the highest-ROI task.** Anthropic/Gemini/OpenAI all offer meaningful cache discounts when requests are structured correctly. Exact percentages change over time, so encode discount factors in provider pricing metadata (not hardcoded constants scattered across runtime code). This should land first.
- **Memoization is most valuable for loops and re-runs.** A 10-iteration loop where each iteration shares the same system prompt and similar inputs benefits enormously. Re-running a failed workflow should skip all previously-succeeded deterministic nodes.
- **Semantic cache is experimental.** The 0.95 threshold is deliberately conservative — better to miss a cache hit than serve a wrong answer. This feature should remain opt-in and off by default until confidence is established. Consider making the threshold tunable per-node rather than global — some nodes (deterministic formatting) tolerate lower thresholds than others (creative generation).
- **Query normalization is essential for semantic cache hit rates.** Without it, minor prompt formatting differences (whitespace, casing, newline variations) cause embedding drift and cache misses. Normalization should be applied before embedding, not after. GPTCache's approach: lowercase, collapse whitespace, strip trailing punctuation.
- **Persistent cross-run cache raises staleness concerns.** A cached response from yesterday may be wrong if the world has changed (e.g., web search results). TTL and explicit invalidation are essential safeguards.
- **Session memory coordination (14-1) is a natural fit for cross-run memoization.** Both solve "reuse results across runs." Session memory provides the persistence/lifecycle layer; memoization provides the hash-based lookup. By writing memoized results as session memory entries, we avoid maintaining two parallel cross-run storage systems, and session cleanup/expiration policies apply automatically.
- **Memory-aware invalidation prevents stale cached results.** If a node's inputs include memory-retrieved context (via 14-3's `MemoryInjectionPolicy` or 14-1's session pre-load), and that memory changes between runs, the memoized result is stale. Tracking `memory_dependency_keys` in the cache entry enables staleness detection on the next lookup.
