# 31-9: Completion Guard

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Validate that DAN has addressed every requirement in the user's message before sending the final response — no silent omissions.

## Problem

The #1 frustration with LLM agents is partial completion: the user asks for 5 things, the agent does 4 and silently drops the 5th. DAN has `ValidatorNode` (9-3) for workflow data validation and `_check_unsourced_claims()` (25-12) for factual grounding, but no **requirement-level completeness check** on the conversational response path.

## Tasks

- [x] 1. **Requirement extraction**
  - [x] 1-1. `RequirementExtractor` — parses user message into structured requirements: `list[Requirement]` where `Requirement` has `id: str`, `description: str`, `type: Literal["action", "question", "constraint", "deliverable"]`, `priority: Literal["must", "should", "nice"]`
  - [x] 1-2. Heuristic extraction: numbered items, bullet points, "and also", "make sure", imperative verbs, comma-separated, colon lists — cover 80% of cases without an LLM call
  - [x] 1-3. LLM fallback for ambiguous messages (gated by complexity threshold — skip for simple single-ask messages)

- [x] 2. **Completion checker**
  - [x] 2-1. `CompletionChecker` — takes `list[Requirement]` + proposed response, returns `CompletionReport`: which requirements are addressed, which are missing, which are partially addressed
  - [x] 2-2. Heuristic check: keyword/phrase matching with suffix normalization between requirement descriptions and response text (fast, no LLM)
  - [x] 2-3. LLM verification: for `must`-priority requirements that fail heuristic check, run a focused LLM call: "does this response address: {requirement}?" (single yes/no per requirement)
  - [x] 2-4. Configurable: `DAN_COMPLETION_CHECK=1` (default on), `DAN_COMPLETION_CHECK_THRESHOLD=2` (skip for messages with < N requirements — even 2-item requests benefit from coverage checks)

- [x] 3. **Response augmentation**
  - [x] 3-1. If all requirements met → send response as-is
  - [x] 3-2. If requirements missing → split handling by requirement type:
    - `flag`: for missing `question` types — append a note: "Note: I addressed X/Y items. I wasn't able to cover: {missing}."
    - `follow_up`: for missed `action` / `deliverable` requirements — return follow-up message with missed items for re-entry through solver pipeline
  - [x] 3-3. Anti-loop: max 1 auto-fix attempt (`_MAX_AUTO_FIX_ATTEMPTS = 1`); if still incomplete after retry, pass through as-is

- [x] 4. **Concierge wiring**
  - [x] 4-1. Hook into `Concierge._post_process_response()` — after all tool calls and execution steps complete but before yielding the final text event. Short responses (< 50 chars) skip the guard. Exception-safe with logged failures.
  - [x] 4-2. Skip for `/` commands, simple greetings (`_GREETING_TOKENS`), follow-up phrases (`_FOLLOW_UP_PHRASES`: confirm, go ahead, do it, proceed, continue, sounds good, etc.), clarification answers, resume context, numeric selections, and short responses (< 50 chars)
  - [x] 4-3. Persist `CompletionReport` for analytics: track completion rate over time (`CompletionStats`)
  - [x] 4-4. `/completion` command: show completion stats (total checks, pass rate, common miss patterns)

- [x] 5. **Tests and docs**
  - [x] 5-1. Unit tests: requirement extraction (numbered lists, bullets, prose, comma-separated, colon lists), completion checking (keyword, LLM), response augmentation (auto-fix, flag)
  - [x] 5-2. Negative test: missed action/deliverable requirement produces follow-up message (not text rewriting)
  - [x] 5-3. Golden test cases: 10 multi-requirement messages with expected extraction results
  - [x] 5-4. Update architecture, changelog

## Dependencies

- `Concierge` runtime for response interception
- `ChatManager` for LLM calls (verification and auto-fix)
- Existing `_check_unsourced_claims()` pattern (25-12) — similar post-processing hook

## Estimate

1.5-2 days

## Notes

- The requirement extractor should be fast — heuristic-first, LLM only when needed. Most messages have obvious structure (numbered lists, "do X and Y and Z").
- The completion guard is NOT a quality check (that's the LLM's job). It's a coverage check — did you attempt to answer everything asked?
- This pairs well with the proactive follow-up feature (31-12): if DAN flags missing items and the user doesn't follow up, DAN can proactively offer to address them later.
