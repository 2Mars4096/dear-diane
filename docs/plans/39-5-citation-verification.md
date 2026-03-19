# 39-5: Citation Verification

**Parent:** [39-web-search-hardening](39-web-search-hardening.md)
**Status:** completed
**Goal:** Provide a narrow end-of-phase hardening layer that checks whether cited numeric/date claims are actually supported by retrieved source content and records that result for audit.

## Context

The existing `check_unsourced_claims()` in `src/dan/server/concierge/actions.py` only detects numeric patterns (dollar amounts, percentages) that appear without any prior `web_search` call. It does not check whether a `[1]` citation actually refers to content that supports the claim. With 39-1's structured `SearchResult` and `CitationRecord` models, we can now do real verification.

## Scope Gate

This plan should be treated as **phase-tail hardening**. Do not start it until:

- 39-2 and 39-4 have landed and users are already getting better grounded answers
- the narrow numeric/date verification version still looks tractable without turning into a full semantic fact-checking project

When started, keep the first version narrow: verify cited **numeric/date claims**
first. Broader semantic fact-checking stays out of scope for this phase.

## Tasks

### A. Citation Extraction from Response

- [x] 1. Parse `[N]` citations from LLM response text
  - [x] 1-1. New `_extract_inline_citations(text: str) -> list[InlineCitation]` in `src/dan/server/chat/helpers.py`
  - [x] 1-2. `InlineCitation` model: `index: int`, `surrounding_text: str` (±50 chars around the citation marker), `claim_text: str` (sentence containing the citation)
  - [x] 1-3. Handle edge cases: `[1,2]`, `[1-3]`, `[1][2]` (combined citations)
  - [x] 1-4. Handle Anthropic native citations separately: when using the native adapter (39-1 task 4), extract from `cited_text` field instead of parsing text

### B. Claim-Source Matching

- [x] 2. Match cited claims against source content
  - [x] 2-1. New `_verify_citation(citation: InlineCitation, search_results: list[SearchResult]) -> CitationVerification` in `chat/helpers.py`
  - [x] 2-2. `CitationVerification` model: `citation_index`, `claim_text`, `source_url`, `source_excerpt_match: str | None`, `verified: bool`, `confidence: float` (0-1), `reason: str`
  - [x] 2-3. Verification logic:
    - [x] Look up `SearchResult` by citation index
    - [x] If index out of range → `verified=False, reason="cited source [N] does not exist"`
    - [x] Extract key terms from `claim_text` (nouns, numbers, proper nouns)
    - [x] Search for those terms in `fetched_content` (or `snippet` if no fetched content)
    - [x] If ≥60% of key terms found in the source → `verified=True, confidence=match_ratio`
    - [x] For the first rollout of this plan, only verify claims containing numbers / percentages / dates and require exact numeric match in the source
  - [x] 2-4. This is intentionally regex/string-matching based, not LLM-based — keeps it fast and deterministic

### C. Post-Response Verification Pass

- [x] 3. Wire verification into the chat response pipeline
  - [x] 3-1. After the LLM produces its final response (post-tool-loop), run `verify_response_citations()`
  - [x] 3-2. Inputs: response text, `SearchResultSet` from tool results in this turn
  - [x] 3-3. Output: `list[CitationVerification]`
  - [x] 3-4. Do NOT block the response — verification runs after the response is already streamed
  - [x] 3-5. If verification finds problems, emit a separate warning/notification event or audit badge rather than mutating the already-streamed assistant text

- [x] 4. Post-response warning policy
  - [x] 4-1. Keep `check_unsourced_claims()` as the pre-response heuristic for obviously unsourced live-data claims; do not overload it with post-stream verification concerns
  - [x] 4-2. If any `CitationVerification.verified == False` with numeric claims → emit a post-response warning/notification and store audit metadata
  - [x] 4-3. Warning text: `"Some cited claims could not be verified against the retrieved source content. Please double-check the cited source before relying on those figures."`
  - [x] 4-4. Gate behind `DAN_VERIFY_CITATIONS=1` env var initially (default off until proven stable)

### D. Verification Metadata in Audit

- [x] 5. Persist verification results
  - [x] 5-1. Extend `audit_tool_records` in `chat_manager.py` with `citation_verifications: list[CitationVerification]`
  - [x] 5-2. Update `CitationRecord.verified` field (from 39-1) with verification result
  - [x] 5-3. Surface in `/cost` or `/status` when verification was run: `"Citations: 4 verified, 1 unverified"`

### E. Tests

- [x] 6. Test coverage
  - [x] 6-1. Test `_extract_inline_citations` with various citation formats (`[1]`, `[1,2]`, `[1-3]`, `[1][2]`, no citations)
  - [x] 6-2. Test `_verify_citation` with matching content, missing content, out-of-range index, numeric claims
  - [x] 6-3. Test end-to-end verification pass with mock search results and LLM response
  - [x] 6-4. Test post-response warning event / metadata generation for unverified numeric claims
  - [x] 6-5. Test Anthropic native citation path (provider-supplied citation spans are captured cleanly and do not require fragile `[N]` parsing)

## Decisions

- Verification remains deterministic and audit-first: it runs after the final answer, records structured results, and never rewrites already-streamed assistant text.
- User-facing warning surfacing now uses a separate non-terminal `chat_notice` event and `/cost` summary metadata rather than mutating the assistant’s already-streamed answer text.

## Notes

- This is intentionally deterministic string matching, not LLM-as-judge. LLM verification would be more accurate but adds latency, cost, and unpredictability. The string-matching approach catches the most common failure modes (citing a non-existent source, citing a source that doesn't contain the claimed number) at near-zero cost.
- The verification pass runs post-response and does not block streaming. It produces metadata that can be surfaced in UI or audit logs.
- For Anthropic native citations (39-1 task 4), the API already provides provider-supplied citation spans (`cited_text`). Treat those as stronger evidence and record them directly instead of re-parsing `[N]` text markers.
- The `DAN_VERIFY_CITATIONS` gate allows shipping the code without activating it until we have confidence it does not produce excessive false positives.
- This plan is in-scope for Phase 28 because dedicated web-search phases are rare, but it stays intentionally narrow so it does not consume the whole phase with open-ended fact-checking complexity.
- Review follow-up: verification metadata must be keyed by both source index and claim text. Matching by source index alone let one verified claim incorrectly mark other claims citing the same source as verified.
- Review follow-up: `/cost` citation summaries must be loaded from request-scoped `workflow_id` / `thread_id` metadata first. Falling back to active-project links or a shared capability context can point at the wrong chat thread during fast-command handling.
- Validation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_post_tool_followup_recovery.py -q`.
