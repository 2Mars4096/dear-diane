# 39-3: Query Planning, Ranking & Dedup

**Parent:** [39-web-search-hardening](39-web-search-hardening.md)
**Status:** completed
**Goal:** Decompose complex queries into focused sub-queries, re-rank results by relevance, deduplicate URLs across turns, and reformulate on thin results.

## Context

Currently, `handle_web_search` in `src/dan/server/capabilities/web.py` passes a single query string to the provider and returns results in provider order. For complex questions ("Compare NVIDIA vs AMD GPU revenue growth 2023-2025"), the model must manually issue multiple searches. Results are never deduplicated across turns, so multi-search conversations waste context window on repeated URLs. Provider-order ranking often puts less relevant results first, especially from DDG/Brave.

## Tasks

### A. Multi-Query Decomposition

- [x] 1. Heuristic query decomposer
  - [x] 1-1. New `_decompose_query(query: str) -> list[str]` in `src/dan/server/capabilities/web.py`
  - [x] 1-2. Detect comparison patterns: "X vs Y", "compare X and Y", "X or Y" → split into per-entity queries
  - [x] 1-3. Detect multi-entity patterns: "X, Y, and Z" with shared predicate → one query per entity + predicate
  - [x] 1-4. Detect temporal + entity: "X revenue 2023 to 2025" → keep as single query (temporal ranges are fine for search engines)
  - [x] 1-5. Cap at 3 sub-queries maximum to bound cost
  - [x] 1-6. If no decomposition pattern matches, return the original query as a single-element list

- [x] 2. Parallel sub-query execution
  - [x] 2-1. Run sub-queries via `asyncio.gather`, reusing the existing provider cascade per sub-query
  - [x] 2-2. Merge results: interleave by sub-query (result 1 from Q1, result 1 from Q2, ...) to maintain diversity
  - [x] 2-3. Deduplicate by URL within the merged set (keep first occurrence)
  - [x] 2-4. Cap merged results at `num_results` parameter

- [x] 3. Decomposition metadata
  - [x] 3-1. `SearchResultSet.sub_queries: list[str] | None` — populated when decomposition was applied
  - [x] 3-2. Each `SearchResult.source_query: str` — which sub-query produced this result
  - [x] 3-3. Formatted message includes sub-query labels: `"Sub-query 1: 'NVIDIA GPU revenue'"`

### B. Result Re-Ranking

- [x] 4. TF-based relevance scoring
  - [x] 4-1. New `_score_result(query: str, result: SearchResult) -> float` in `capabilities/web.py`
  - [x] 4-2. Tokenize query and result (title + snippet), case-fold, remove stop words
  - [x] 4-3. Score = (matched query tokens in result) / (total query tokens), weighted: title match 2x, snippet match 1x
  - [x] 4-4. Re-sort results by score descending, breaking ties by original provider order

- [x] 5. Source credibility tiers
  - [x] 5-1. New `_CREDIBILITY_TIERS` dict in `capabilities/web.py`:
    - [x] Tier A (authoritative): `.gov`, `.edu`, `wikipedia.org`, `arxiv.org`, `scholar.google.com`, known reference sites
    - [x] Tier B (established): major news outlets, known tech publications, official docs domains
    - [x] Tier C (general): everything else
  - [x] 5-2. Set `SearchResult.credibility_tier` from domain matching
  - [x] 5-3. Tiebreak re-ranking: within same relevance score, prefer higher credibility tier
  - [x] 5-4. Surface tier label in formatted result: `[1] Title (authoritative)` or just omit for tier C

### C. Cross-Turn URL Dedup

- [x] 6. Canonicalize URLs before dedup and source tracking
  - [x] 6-1. Add a shared URL canonicalizer (strip fragments, trim common tracking params like `utm_*`, `gclid`, `fbclid`, and normalize obvious slash variants where safe)
  - [x] 6-2. Use the canonical URL for dedup comparisons, cache/dedup bookkeeping, and source extraction; keep the original URL for display
  - [x] 6-3. Reuse the canonicalizer in `_extract_cited_sources()` so audit/source lists stop accumulating trivial URL variants

- [x] 7. Turn-scoped URL registry
  - [x] 7-1. Maintain a per-tool-loop local URL registry alongside the existing local dedupe state in `ChatManager`; do not add a shared instance attribute that could leak across concurrent sessions
  - [x] 7-2. When `handle_web_search` returns, register all result URLs using the canonicalized form
  - [x] 7-3. On subsequent searches in the same turn, prefer novel URLs first; only filter duplicates when at least one novel URL remains so repeated-result searches never collapse to an empty answer
  - [x] 7-4. Add note to formatted results: `"(3 duplicate URLs filtered from previous search)"`

- [x] 8. Session-scoped dedup hints
  - [x] 8-1. Across turns (not just within one tool loop), maintain a lightweight `recent_search_urls` set on chat-session / thread metadata (last 50 URLs), not on the shared `ChatManager` object
  - [x] 8-2. Do NOT filter — instead, annotate: `"[1] Title (also appeared in previous search)"` so the LLM knows without losing the reference

### D. Thin-Result Reformulation

- [x] 9. Auto-retry on thin results
  - [x] 9-1. If the search returns <2 results OR all snippets are <30 chars → attempt one reformulation
  - [x] 9-2. Reformulation strategy: drop quoted phrases, remove qualifiers ("latest", "best"), broaden date terms
  - [x] 9-3. Simple heuristic `_reformulate_query(original: str) -> str | None` — returns `None` if no meaningful reformulation is possible
  - [x] 9-4. Bounded to 1 retry; if reformulation also returns thin results, return what we have with a note

### E. Tests

- [x] 10. Test coverage
  - [x] 10-1. Test `_decompose_query` with comparison, multi-entity, temporal, and no-match patterns
  - [x] 10-2. Test parallel sub-query execution and merge/dedup
  - [x] 10-3. Test `_score_result` ranking correctness
  - [x] 10-4. Test credibility tier assignment
  - [x] 10-5. Test URL canonicalization and tracking-param stripping
  - [x] 10-6. Test cross-turn URL dedup filtering and annotation
  - [x] 10-7. Test thin-result reformulation triggers and bounds

## Decisions

- Query decomposition stays heuristic-only and bounded; the implementation caps both sub-queries and provider fan-out so richer search does not become an unbounded call multiplier.
- Within-turn dedup filters only when novel evidence still exists; cross-turn dedup is annotation-only to preserve useful repeated sources.

## Notes

- Multi-query decomposition is heuristic-only at this stage. LLM-based decomposition is intentionally deferred — it would add latency and cost for marginal gain over pattern matching on common question structures.
- Re-ranking is lightweight TF scoring, not embedding-based. Embeddings would be more accurate but add a dependency and latency that is not justified for search result counts of 3-10.
- Cross-turn dedup is annotation-based (not filtering) to avoid hiding potentially relevant results that the model needs to reference.
- Within-turn dedup should be conservative: prefer novel URLs, but do not over-filter to the point that a follow-up search returns zero visible evidence.
- URL canonicalization is a low-effort, high-value improvement: a large share of apparent duplicates differ only by fragments or tracking parameters.
- Validation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_search_models.py -q`.
