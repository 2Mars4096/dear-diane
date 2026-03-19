# 39-1: Search Result Contract & Citations

**Parent:** [39-web-search-hardening](39-web-search-hardening.md)
**Status:** completed
**Goal:** Introduce a canonical `SearchResultSet` payload and audit-friendly citation/source data first, then finish the phase with native-provider citation adapters where they provide real trust value.

## Context

Currently, `src/dan/server/capabilities/web.py` formats search results as numbered plain text (`[1] Title\nSnippet: ...\nURL: ...`) and the system prompt in `src/dan/server/chat/prompts.py` tells the LLM to "cite them inline as [1], [2]". This is fragile:
- The LLM can cite `[3]` when only 2 results were returned.
- There is no machine-readable record of what was cited.
- Anthropic's API has native `search_result` content blocks with automatic `cited_text` — DAN cannot use them because it flattens everything to text.

## Scope Gate

Phase opening only requires tasks **1-3 and 5** from this plan:

- a canonical `search_result_set` payload in `CapabilityResult.data`
- fetched content attached to canonical search results
- audit/source extraction that stops regex-parsing flattened text everywhere

Task 4 (native-provider citation adapters) should not block 39-2 / 39-4, but it
remains part of Phase 28 closeout rather than an indefinite future idea.

## Tasks

- [x] 1. Define `SearchResult` Pydantic model
  - [x] 1-1. Fields: `index`, `title`, `url`, `snippet`, `fetched_content` (optional), `page_age` (optional), `credibility_tier` (optional, for 39-3), `provider`, `result_kind` (`organic`, `knowledge_graph`, `direct_answer`, etc.)
  - [x] 1-2. Define `CitationRecord` model: `claim_text`, `source_index`, `source_url`, `cited_excerpt`, `verified` (optional bool, for 39-5)
  - [x] 1-3. Define `SearchResultSet` model: `query`, `results: list[SearchResult]`, `provider`, `cache_hit`, `provider_failures`, `grounded_result_count`
  - [x] 1-4. Place in `src/dan/server/search_models.py` (new shared module; this repo does not currently have a `server/models/` package)

- [x] 2. Update `handle_web_search` to produce `SearchResultSet`
  - [x] 2-1. `src/dan/server/capabilities/web.py`: build `SearchResultSet` from raw provider dict, attach to `CapabilityResult.data`
  - [x] 2-2. Keep the formatted text `message` for backward compat (non-Anthropic providers still need it)
  - [x] 2-3. Use a single canonical `search_result_set` key in `CapabilityResult.data` to avoid duplicated parallel shapes drifting out of sync

- [x] 3. Update `handle_web_fetch` to populate `fetched_content` on `SearchResult`
  - [x] 3-1. When `fetch_content=true`, attach fetched text to the corresponding `SearchResult` object
  - [x] 3-2. Keep fetched excerpts in the formatted text `message` for generic providers/tool loops; `fetched_content` becomes the canonical structured copy rather than replacing the text path immediately

- [x] 4. Phase-tail: Anthropic native citation adapter
  - [x] 4-1. Extend the chat/provider contract so capability handlers can return optional structured tool-result blocks alongside the plain-text fallback (current `role="tool"` replay path stringifies content)
  - [x] 4-2. Update `ChatManager` to preserve those structured tool-result blocks through tool replay without regressing existing OpenAI-compatible providers
  - [x] 4-3. Update `AnthropicProvider._convert_messages()` so tool results can carry `search_result` blocks instead of only `str(content)`, and update `_serialize_content_block()` to preserve citation-bearing fields instead of dropping them
  - [x] 4-4. When the active provider is Anthropic and model supports `search_result` blocks, map `SearchResult` → Anthropic `search_result` content blocks with `citations.enabled=true`
  - [x] 4-5. Parse provider-native citations from Anthropic raw content / provider metadata into `CitationRecord` list
  - [x] 4-6. Gate behind `DAN_NATIVE_CITATIONS=1` env var initially for safe rollout

- [x] 5. Audit integration
  - [x] 5-1. Extend `audit_tool_records` in `chat_manager.py` with `search_results: list[SearchResult]` and `citations: list[CitationRecord]`
  - [x] 5-2. `_extract_cited_sources()` in `chat/helpers.py` now pulls from structured `SearchResult` objects instead of regex-parsing tool output text

- [x] 6. Tests
  - [x] 6-1. Unit tests for `SearchResult` / `SearchResultSet` / `CitationRecord` serialization
  - [x] 6-2. Test `handle_web_search` produces valid `SearchResultSet` in `CapabilityResult.data`
  - [x] 6-3. Test Anthropic adapter produces valid `search_result` blocks and preserves them through `ChatManager` tool replay (mock provider)
  - [x] 6-4. Test provider-native citation fields survive raw assistant-message serialization instead of being stripped
  - [x] 6-5. Test `_extract_cited_sources()` works with structured data path

## Decisions

- `SearchResultSet` is the single canonical payload; formatted numbered text is now just the backward-compatible presentation layer for generic providers and older tool loops.
- Anthropic structured replay now covers both tool-result `search_result` blocks and provider-native citation extraction from raw assistant payloads, so audit/citation metadata stays structured even when the assistant does not emit `[N]` markers.

## Notes

- The minimal contract in this plan is still worth doing because it cleans up audit/source handling and gives later plans one canonical result shape.
- The Anthropic native citation path is valuable, but it is not just a handler tweak: the provider/chat-manager tool-result replay path must learn to carry structured non-text content first. Treat it as phase-tail hardening, not as an indefinite stretch item.
- For non-Anthropic providers (OpenAI, Google, Kimi), continue with the numbered-text format but now backed by the structured `SearchResultSet` so audit/verification still works.
- The `CitationRecord` model is intentionally simple now; 39-5 adds verification logic on top of it.
- Validation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_providers/test_anthropic_provider.py tests/test_search_models.py -q`.
