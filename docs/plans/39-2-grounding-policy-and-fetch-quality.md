# 39-2: Grounding Policy & Fetch Quality

**Parent:** [39-web-search-hardening](39-web-search-hardening.md)
**Status:** completed
**Goal:** Make web grounding automatic when the turn needs live data, make fetched excerpts query-relevant instead of blindly truncated, and give users an explicit force-search affordance.

## Context

The current `web_search` capability schema in `src/dan/server/capability_handlers.py` defaults `fetch_content` to `false`. The LLM often omits it, producing snippet-only results. The completion gate in `src/dan/server/chat/helpers.py` (`_has_grounded_web_support`) then detects that `grounded_result_count == 0` and forces a retry — adding latency and an extra LLM turn. Meanwhile, `_fetch_grounding_excerpt()` in `src/dan/server/capabilities/web.py` truncates at a flat 1800 chars regardless of what the query actually needs.

## Tasks

### A. Intent-Aware Auto-Grounding

- [x] 1. Add `grounding_required` flag to capability context
  - [x] 1-1. `CapabilityContext` gains a `grounding_required: bool` field, set by the chat manager based on turn intent
  - [x] 1-2. Detection heuristic: if the action hint includes `search_web`, or the turn contains live-data keywords (already in `_LIVE_DATA_PHRASES` in `chat/helpers.py`), or the mode is `agent`/`conversation` with factual question patterns → `grounding_required=true`

- [x] 2. Auto-promote `fetch_content` when grounding is required
  - [x] 2-1. In `handle_web_search`: if `ctx.grounding_required` and `fetch_content` was not explicitly set by the LLM → promote to `true`
  - [x] 2-2. This eliminates the retry loop: the first search already fetches page content
  - [x] 2-3. Keep explicit `fetch_content=false` honored — the model can still do a quick lookup when it only needs snippets

- [x] 3. Lightweight snippet mode for non-grounding searches
  - [x] 3-1. Quick lookups ("what is the URL for X", navigation queries) should stay snippet-only for speed
  - [x] 3-2. Add `search_depth` parameter to schema: `"quick"` (snippets only, max 3 results) vs `"thorough"` (auto-fetch, more results)
  - [x] 3-3. Default: `"thorough"` when `grounding_required`, `"quick"` otherwise

### B. Query-Relevant Excerpt Extraction

- [x] 4. Replace blind truncation with relevance-windowed extraction
  - [x] 4-1. New `_extract_relevant_excerpt(content: str, query: str, budget: int = 2400) -> str` in `capabilities/web.py`
  - [x] 4-2. Split content into paragraphs, score each by term overlap with query tokens (simple TF, case-insensitive, stop-word filtered)
  - [x] 4-3. Select top-scoring contiguous section(s) up to `budget` chars, with `[...skipped N paragraphs...]` markers for gaps
  - [x] 4-4. Fallback: if no paragraph scores above threshold (e.g., query has no overlap), fall back to first `budget` chars (current behavior)

- [x] 5. Increase excerpt budget
  - [x] 5-1. Raise `_FETCH_EXCERPT_MAX` from 1800 to 2400 chars — the extra 600 chars (~150 tokens) is a small cost for much better grounding
  - [x] 5-2. Make configurable via `DAN_WEB_FETCH_EXCERPT_MAX` env var

### C. Force-Search Affordance

- [x] 6. `/search` command in chat
  - [x] 6-1. Register `/search <query>` in the canonical command registry (`src/dan/server/concierge/command_registry.py`)
  - [x] 6-2. Implement the handler in `dan.server.concierge.actions` or a tiny dedicated module referenced by the registry
  - [x] 6-3. Executes `web_search` with `fetch_content=true` and `num_results=5`, returns results directly
  - [x] 6-4. Equivalent to Cursor's `@Web` — explicit user-initiated search
  - [x] 6-5. Update user-facing docs (`docs/cli.md`, README if needed) because `/search` is a new user-facing command

- [x] 7. `@Web` mention support (lightweight)
  - [x] 7-1. Detect a standalone `@web` / `@Web` token (word-boundary match), not arbitrary substrings like `@webcam`
  - [x] 7-2. If the frontend later adds a first-class `web` structured mention, honor it through the existing mention pipeline; until then use raw token detection as the lightweight fallback
  - [x] 7-3. This is the minimal equivalent of Cursor's `@Web` without requiring the full mention UI to ship first

### D. Adaptive Fetch Fallback

- [x] 8. Walk down ranked results when early fetches fail
  - [x] 8-1. Keep a target of `_MAX_AUTO_FETCH_RESULTS` successful fetched excerpts, but allow more ranked URLs to be attempted when early candidates fail or return unusable text
  - [x] 8-2. Add a hard `_MAX_AUTO_FETCH_ATTEMPTS` budget (for example, 4) so fallback remains bounded
  - [x] 8-3. Stop as soon as the success target is hit or the attempt budget is exhausted
  - [x] 8-4. Surface a summary like `"Fetched 1/2 target pages; 3 attempts made"` so the model can see when grounding is partial

### E. Fetch Failure Visibility

- [x] 9. Surface fetch failures clearly
  - [x] 9-1. When `_fetch_grounding_excerpt` fails, include the error in the `SearchResult.fetched_content` as `"[Fetch failed: HTTP 403 — content unavailable from this source]"`
  - [x] 9-2. The LLM sees the failure and can decide to fetch a different result or note the limitation

### F. Tests

- [x] 10. Test coverage
  - [x] 10-1. Test auto-promotion of `fetch_content` when `grounding_required=true`
  - [x] 10-2. Test `_extract_relevant_excerpt` with various query/content combinations
  - [x] 10-3. Test adaptive fetch fallback: first result fails, later result succeeds, budget stays bounded
  - [x] 10-4. Test `/search` command end-to-end
  - [x] 10-5. Test `@web` mention detection
  - [x] 10-6. Regression: explicit `fetch_content=false` still honored

## Decisions

- `search_depth` is resolved after the fetch/grounding policy so explicit or implied fetching cannot be accidentally clipped back to quick-mode result limits.
- Explicit `/search` is implemented as a direct grounded capability call in concierge runtime instead of a separate bespoke search stack.

## Notes

- The auto-grounding policy removes the biggest daily-use friction: the model forgets `fetch_content=true`, the gate retries, user waits an extra 5-10s.
- Query-relevant extraction is the highest bang-for-buck quality improvement. Current blind truncation often returns the page header/nav instead of the article body.
- `/search` command and `@web` mention are cheap to implement but give users explicit control, matching Cursor's UX.
- The repo already has a canonical command registry and structured mention plumbing; this plan should reuse those surfaces instead of creating a one-off command module or relying on naive substring checks.
- Adaptive fetch fallback is a cheap but high-value improvement because many top-ranked pages fail due to 403s, JS shells, or thin content. We should not stop after the first two URLs if both are unusable.
- Validation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_concierge/test_fast_commands.py -q`.
