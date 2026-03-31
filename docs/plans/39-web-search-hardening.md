# 39: Web Search Hardening

**Status:** completed
**Goal:** Make web search powerful, convenient, and trustworthy within this dedicated phase: land the high-ROI search-quality wins first, then close the phase with the trust/citation hardening that still provides clear value.

## Motivation

Web search is DAN's primary grounding mechanism for live data, research, and factual questions. The current stack (Tavily→Brave→DDG cascade, snippet-only by default, prompt-driven `[1]` citations, 1800-char blind truncation) works but has concrete daily-use pain points:

- **Citations are prompt instructions, not data.** Claude's API returns structured `search_result` blocks with `cited_text` and source attribution. Cursor forces web grounding via `@Web`. DAN tells the model "please cite as [1]" and hopes for the best.
- **`fetch_content` is opt-in and the model often skips it.** The completion gate in `chat/helpers.py` then forces a retry loop, adding latency. Better: auto-ground when the turn needs live data.
- **Fetched excerpts are blind truncations.** 1800 chars from the top of the page, not the section relevant to the query.
- **No result dedup across turns.** Multi-search conversations waste context window on repeated URLs.
- **No Google results.** DDG as the final fallback is unreliable; Serper would give Google-quality results.
- **No post-response verification.** `check_unsourced_claims()` only flags numeric patterns without web_search; it does not verify that cited `[1]` content actually supports the claim.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [39-1](39-1-search-result-contract-and-citations.md) | Search Result Contract & Citations | Minimal canonical `search_result_set` payload and audit/source extraction first; native-provider citation adapter as phase-tail hardening | P2 | completed |
| [39-2](39-2-grounding-policy-and-fetch-quality.md) | Grounding Policy & Fetch Quality | Intent-aware auto-grounding, query-relevant excerpt extraction, fetch policy, explicit force-search affordance | P1 | completed |
| [39-3](39-3-query-planning-ranking-and-dedup.md) | Query Planning, Ranking & Dedup | Multi-query decomposition, result re-ranking, cross-turn URL dedup, thin-result reformulation | P2 | completed |
| [39-4](39-4-provider-coverage-and-health.md) | Provider Coverage & Health | Serper/Google provider, provider health telemetry, health-aware cascade, domain/location controls | P2 | completed |
| [39-5](39-5-citation-verification.md) | Citation Verification | Narrow phase-tail trust hardening: numeric/date claim verification and audit metadata | P3 | completed |
| [39-6](39-6-browser-escalation-and-interactive-fetch.md) | Browser Escalation & Interactive Fetch | Detect JS-shell pages, allow bounded browser-backed fetch recovery, and surface browser-grounding metadata | P2 | completed |

## Dependencies / Sequencing

Phase opening (high user-visible ROI):
```
39-2 (Grounding Policy)            ← biggest daily-use quality win
39-4 (Provider Coverage)           ← Serper + budgets + provider controls
39-3 (Ranking & Dedup)             ← include URL canonicalization + safer dedup
39-1 (Minimal contract slice)      ← canonical payload / audit cleanup, but do not block on native citations
```

Phase closeout hardening (still in-scope for Phase 28):
```
39-1 native-provider citation adapter
39-5 citation verification
```

The phase should optimize for better grounded answers quickly, but because this is
the dedicated web-search wave, the native citation adapter and narrow citation
verification should still be completed at the tail of the phase if they remain
tractable and continue to offer clear trust value.

## Success Criteria

### Initial rollout

- [x] Live-data turns auto-ground without requiring the model to remember `fetch_content=true`
- [x] Fetching walks beyond the first two ranked URLs when early fetches fail, while staying within a bounded fetch budget
- [x] Fetched page excerpts are query-relevant, not blind truncations
- [x] Multi-search conversations deduplicate by canonical URL instead of repeating tracking-parameter variants
- [x] Google-quality results available via Serper when configured
- [x] Web search and fetch behavior stay within explicit per-call / per-turn budgets so smarter search does not explode upstream calls

### Phase-close hardening

- [x] Search results carry structured citation metadata (source URL, cited excerpt, title) as data, not just formatted text
- [x] Structured search results can flow through `ChatManager` and provider-specific replay paths without regressing existing text-only tool loops
- [x] Post-response audit can flag clearly unsupported numeric/date claims against cited sources

## Decisions

- Canonical search state now flows through `SearchResultSet` plus per-turn `CapabilityContext.search_state` / `web_budget_state` instead of ad hoc handler-local dict shapes.
- Citation verification stays audit-first, but the final implementation now also emits a separate non-terminal warning event and persists a thread-level citation summary so users can see verification results without rewriting assistant text.

## Notes

- Design informed by Claude API's `web_search_20260209` tool (structured `search_result` blocks, `cited_text`, domain filters, `max_uses`, dynamic filtering) and Cursor's `@Web` force-search UX.
- Keep backward compatibility: users without any search API key should still get DDG fallback.
- Do not add LLM-based query decomposition at this stage; heuristic decomposition is cheaper and more predictable.
- Preserve bounded external-call behavior: decomposition, multi-provider search, and auto-fetch must compose with explicit caps so one grounded turn does not fan out into an unbounded number of upstream requests.
- The first rollout should not wait on provider-specific citation plumbing or generic post-response verification, but those trust upgrades are still part of this phase rather than punted to an uncertain future pass.
- Validation sweep after implementation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_concierge/test_fast_commands.py tests/test_post_tool_followup_recovery.py tests/test_providers/test_anthropic_provider.py tests/test_search_models.py -q`.
