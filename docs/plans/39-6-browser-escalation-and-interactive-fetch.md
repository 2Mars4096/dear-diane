# 39-6: Browser Escalation & Interactive Fetch

**Parent:** [39-web-search-hardening](39-web-search-hardening.md)
**Status:** completed
**Goal:** Keep web grounding honest on JavaScript-heavy or browser-gated pages by detecting shell fetches, exposing an explicit browser-fallback path, and surfacing when grounding was recovered through the persistent browser session.

## Tasks

- [x] 1. Add a bounded browser-fallback path to `web_fetch`
  - [x] 1-1. Extend `src/dan/tools/web_fetch.py` with optional `browser_fallback`
  - [x] 1-2. Retry through the persistent Playwright browser when HTTP fetch fails outright
  - [x] 1-3. Reuse the existing browser session/profile instead of inventing a separate browser stack

- [x] 2. Detect browser-gated shell pages
  - [x] 2-1. Flag HTML bodies that only expose short JavaScript/cookie/challenge placeholders
  - [x] 2-2. Prevent those placeholder pages from counting as grounded evidence in `handle_web_search`
  - [x] 2-3. Preserve an explicit note in `web_fetch` responses when a page still looks browser-gated

- [x] 3. Surface browser recovery metadata
  - [x] 3-1. Return `fetch_via`, `browser_fallback_used`, and related metadata from `web_fetch`
  - [x] 3-2. Add `browser_fallback_count` to `SearchResultSet`
  - [x] 3-3. Expose browser-rendered fetches clearly in `handle_web_search` / `handle_web_fetch` messages

- [x] 4. Expose the capability flag to callers
  - [x] 4-1. Add `browser_fallback` to the `web_search` capability schema for grounded fetches
  - [x] 4-2. Add `browser_fallback` to the `web_fetch` capability schema for direct link reads
  - [x] 4-3. Keep the feature bounded and opt-in via explicit tool args or `DAN_WEB_BROWSER_FALLBACK`

- [x] 5. Add regressions
  - [x] 5-1. Test that shell pages no longer count as grounded evidence
  - [x] 5-2. Test handler messaging for browser-gated pages and browser-backed recovery
  - [x] 5-3. Test direct `web_fetch` fallback for HTTP failures and JavaScript shells

## Decisions

- Browser escalation stays opt-in at the capability/tool layer unless `DAN_WEB_BROWSER_FALLBACK=1` is set. That avoids silently turning routine fetches into browser automation on every install.
- Search hardening treats browser-shell pages as unusable evidence even when the plain HTTP request technically succeeded.
- The fallback reuses the persistent browser profile so authenticated sessions can help with fetch recovery, but it remains a bounded fetch recovery step rather than a full browser-agent workflow.

## Notes

- This is a follow-on hardening slice under Phase 28 rather than a new top-level browser-agent initiative because it only upgrades fetch/grounding fidelity.
- Validation: `pytest -q tests/test_concierge/test_live_data.py -k 'web_search_handler or web_fetch_handler or web_fetch_detects_browser_shell_content or web_fetch_browser_fallback'` and `pytest -q tests/test_search_models.py tests/test_post_tool_followup_recovery.py -k 'search_result_set or grounded_result_count or browser_fallback_count or web_search'`.
