# 39-4: Provider Coverage & Health

**Parent:** [39-web-search-hardening](39-web-search-hardening.md)
**Status:** completed
**Goal:** Add Serper (Google) as a search provider, track provider health for smarter cascade ordering, and add domain/location controls for scoped searches.

## Context

The current provider cascade in `src/dan/tools/web_search.py` is Tavily → Brave → DDG. DDG is the only no-key fallback and is unreliable (rate-limited, often returns garbage results). Serper provides Google Search results at $50/50K searches — competitive with Tavily and significantly better than DDG for factual queries. There is no provider health tracking: if Tavily has been failing all day, DAN still tries it first on every request.

## Tasks

### A. Serper (Google) Provider

- [x] 1. Implement `_serper_search()` in `src/dan/tools/web_search.py`
  - [x] 1-1. Call `https://google.serper.dev/search` with `DAN_SERPER_API_KEY`
  - [x] 1-2. Parse organic results: `title`, `link` → `url`, `snippet`
  - [x] 1-3. Also extract `knowledgeGraph` if present → add as first result with `result_kind="knowledge_graph"`
  - [x] 1-4. Handle rate limits (429), auth errors (401), and timeouts consistently with other providers

- [x] 2. Update cascade order
  - [x] 2-1. New cascade: Tavily → Serper → Brave → DDG (configurable via `DAN_SEARCH_PROVIDER_ORDER`)
  - [x] 2-2. `DAN_SEARCH_PROVIDER_ORDER` env var: comma-separated provider names, e.g., `"serper,tavily,brave,ddg"`
  - [x] 2-3. Default: `"tavily,serper,brave,ddg"` — Tavily first (most users already have it), Serper second
  - [x] 2-4. Skip providers with no configured API key

- [x] 3. Update `.env.example` and docs
  - [x] 3-1. Add `DAN_SERPER_API_KEY` to `.env.example` in the web search section
  - [x] 3-2. Add `DAN_SEARCH_PROVIDER_ORDER` to `.env.example`
  - [x] 3-3. Update `TOOL_METADATA` description in `web_search.py` to mention Serper
  - [x] 3-4. Update `docs/cli.md` web search section

### B. Provider Health Tracking

- [x] 4. Health record model
  - [x] 4-1. New `ProviderHealthRecord` in `src/dan/tools/web_search.py`: `provider`, `last_success_at`, `last_failure_at`, `consecutive_failures`, `avg_latency_ms` (rolling 10)
  - [x] 4-2. In-memory dict `_PROVIDER_HEALTH: dict[str, ProviderHealthRecord]` (no persistence needed — resets on restart)

- [x] 5. Evolve search cache keys for new provider-affecting params
  - [x] 5-1. Update `_search_cache_key()` so cached results vary by every parameter that changes the returned result set (`allowed_domains`, `blocked_domains`, location hints, provider order / multi-provider mode, etc.), not just `(query, num_results)`
  - [x] 5-2. Keep the cache key stable and normalized (sorted domain lists, normalized provider-order string) to avoid unnecessary misses
  - [x] 5-3. Add regression coverage so cached results from one provider/domain scope cannot be replayed for another

- [x] 6. Track success/failure per search
  - [x] 6-1. After each provider attempt (success or failure), update `_PROVIDER_HEALTH`
  - [x] 6-2. Record latency on success, increment `consecutive_failures` on failure, reset on success

- [x] 7. Health-aware cascade ordering
  - [x] 7-1. Preserve the configured provider order by default; use health data for temporary cooldown/skips and diagnostics rather than opportunistically reshuffling mild failures ahead of the user's explicit order
  - [x] 7-2. Providers with `consecutive_failures >= 5` AND `last_failure_at < 60s ago` → skip entirely (fast-fail) and add a synthetic provider failure record
  - [x] 7-3. Reset skip after 60s so the provider gets re-tested
  - [x] 7-4. Log when a provider is temporarily skipped due to health degradation

### C. Domain and Location Controls

- [x] 8. Domain allow/block lists
  - [x] 8-1. Add optional `allowed_domains` and `blocked_domains` to `web_search` capability schema; validate that they are mutually exclusive
  - [x] 8-2. Pass through to Tavily (`include_domains`/`exclude_domains`), Serper (`site:` operator), Brave (no native support — post-filter results)
  - [x] 8-3. Post-filter: after results return, filter out URLs whose domain matches `blocked_domains` or doesn't match `allowed_domains`
  - [x] 8-4. Environment-level defaults: `DAN_SEARCH_BLOCKED_DOMAINS` (comma-separated) for org-wide blocks

- [x] 9. Location hint
  - [x] 9-1. Add optional `location` parameter: `{"country": "US", "language": "en"}`
  - [x] 9-2. Pass to Tavily (no native support — append to query), Serper (`gl`, `hl` params), Brave (`country`, `search_lang`)
  - [x] 9-3. Default from `DAN_SEARCH_COUNTRY` and `DAN_SEARCH_LANGUAGE` env vars

### D. Web Budget Guards

- [x] 10. Add explicit web-search budgets above the tool loop
  - [x] 10-1. Add `max_web_search_calls_per_turn` and `max_web_fetch_attempts_per_turn` guards at the chat-manager / capability-context layer so one turn cannot consume an unbounded number of upstream requests
  - [x] 10-2. Default conservatively (for example, 4 searches and 8 fetch attempts per turn), with env overrides for power users
  - [x] 10-3. When the budget is exhausted, return a clear tool result / warning telling the model to narrow the query or answer from the already retrieved evidence
  - [x] 10-4. Align semantics with future provider-native `max_uses` controls where available so DAN exposes one coherent budget model to users

### E. Parallel Multi-Provider Mode

- [x] 11. Optional `thorough` mode queries two providers
  - [x] 11-1. When `search_depth="thorough"` (from 39-2) AND 2+ providers are available AND configured, run top 2 providers in parallel
  - [x] 11-2. Merge results: interleave, deduplicate by URL, cap at `num_results`
  - [x] 11-3. Set `SearchResultSet.providers: list[str]` to record which providers contributed
  - [x] 11-4. Gate behind `DAN_SEARCH_MULTI_PROVIDER=1` env var (default off — most users have only one key)
  - [x] 11-5. Bound total upstream search fan-out: on the first rollout, do not combine multi-provider mode with 39-3's multi-query decomposition unless the combined provider-query count stays under an explicit cap (for example, max 4 provider searches per capability call)

### F. Tests

- [x] 12. Test coverage
  - [x] 12-1. Test `_serper_search()` with mock HTTP responses (organic results, knowledge graph, errors)
  - [x] 12-2. Test configurable cascade order via `DAN_SEARCH_PROVIDER_ORDER`
  - [x] 12-3. Test health tracking: consecutive failures → cooldown skip → recovery
  - [x] 12-4. Test cache-key separation across provider/domain/location settings
  - [x] 12-5. Test domain allow/block validation and post-filtering
  - [x] 12-6. Test location hint passthrough
  - [x] 12-7. Test per-turn web budgets and exhaustion messaging
  - [x] 12-8. Test multi-provider merge, dedup, and total-fan-out cap

## Decisions

- Provider order remains user/config driven; health data only triggers temporary cooldown skips and diagnostics instead of silently reshuffling the user’s preferred cascade.
- Turn-level web budgets live above the tool implementation in `ChatManager`, which keeps decomposition, multi-provider search, and fetch fallback under one coherent per-turn cap.

## Notes

- Serper is the cheapest Google-quality option ($50/50K searches ≈ $0.001/search). Tavily is $5/1K ≈ $0.005/search. For users who want quality and volume, Serper as primary is compelling.
- Health tracking is in-memory only. Persisting across restarts is not needed — stale health data is worse than fresh probing.
- Domain controls mirror Anthropic's `allowed_domains`/`blocked_domains` design, which is clean and well-tested.
- Multi-provider parallel mode is opt-in because it doubles API cost per search. Good for research-heavy use cases.
- Because this plan adds provider- and scope-sensitive parameters, cache-key correctness is part of the feature, not follow-up polish. A wrong cache hit here would silently return the wrong search corpus.
- Turn-level budgets are a medium-effort guard with outsized value because the existing chat tool loop can run many rounds. Smarter search should not accidentally multiply costs without an explicit cap.
- Validation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_search_models.py -q`.
