# 31-9: Completion Guard

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Validate that DAN has addressed every requirement in the user's message before sending the final response — no silent omissions.

## Problem

The #1 frustration with LLM agents is partial completion: the user asks for 5 things, the agent does 4 and silently drops the 5th. DAN has `ValidatorNode` (9-3) for workflow data validation and `_check_unsourced_claims()` (25-12) for factual grounding, but no **requirement-level completeness check** on the conversational response path.

## Tasks

- [ ] 1. **Requirement extraction**
  - [ ] 1-1. `RequirementExtractor` — parses user message into structured requirements: `list[Requirement]` where `Requirement` has `id: str`, `description: str`, `type: Literal["action", "question", "constraint", "deliverable"]`, `priority: Literal["must", "should", "nice"]`
  - [ ] 1-2. Heuristic extraction: numbered items, bullet points, "and also", "make sure", imperative verbs — cover 80% of cases without an LLM call
  - [ ] 1-3. LLM fallback for ambiguous messages (gated by complexity threshold — skip for simple single-ask messages)

- [ ] 2. **Completion checker**
  - [ ] 2-1. `CompletionChecker` — takes `list[Requirement]` + proposed response, returns `CompletionReport`: which requirements are addressed, which are missing, which are partially addressed
  - [ ] 2-2. Heuristic check: keyword/phrase matching between requirement descriptions and response text (fast, no LLM)
  - [ ] 2-3. LLM verification: for `must`-priority requirements that fail heuristic check, run a focused LLM call: "does this response address: {requirement}?" (single yes/no per requirement)
  - [ ] 2-4. Configurable: `DAN_COMPLETION_CHECK=1` (default on), `DAN_COMPLETION_CHECK_THRESHOLD=2` (skip for messages with < N requirements — even 2-item requests benefit from coverage checks)

- [ ] 3. **Response augmentation**
  - [ ] 3-1. If all requirements met → send response as-is
  - [ ] 3-2. If requirements missing → split handling by requirement type:
    - `auto_fix_text`: only for `question` / explanatory omissions — re-invoke LLM with "you missed these requirements: {list}" appended, get revised response
    - `re-enter_execution`: for missed `action` / `deliverable` requirements — construct a synthetic follow-up message containing only the missed requirements and feed it back through the solver pipeline (same path as a new user message, but auto-generated). This re-enters planning/execution instead of pretending the work was done. Max 1 re-entry per response cycle to avoid loops.
    - `flag`: append a notice: "Note: I addressed X/Y items. I wasn't able to cover: {missing}. Would you like me to address those?"
  - [ ] 3-3. Mode selection: `auto_fix_text` only when all missing items are text-only and ≤ 2; otherwise `re-enter_execution` or `flag` (configurable)
  - [ ] 3-4. Anti-loop: max 1 auto-fix attempt; if still incomplete after retry, fall back to `flag` mode. **Verify all original requirements are still covered after auto-fix** — an auto-fix can inadvertently drop a previously-covered requirement while adding the missing one.

- [ ] 4. **Concierge wiring**
  - [ ] 4-1. Hook into `Concierge._process_inner()` — after all tool calls and execution steps complete but before yielding the final text event. Since `_process_inner` is an async generator that yields events progressively, the completion guard runs after the response text is assembled but before the final `ChatCompleteEvent`. For streaming responses, this means buffering the final response text, checking completeness, and either passing through or augmenting. Note: this should be part of a `ResponsePostProcessor` pipeline alongside PII detokenization (31-10) and unsourced claims checking (25-12) — formalize the chain rather than inserting 3 ad-hoc hooks.
  - [ ] 4-2. Skip for fast commands, simple greetings, and follow-up messages (only check on initial substantive responses)
  - [ ] 4-3. Persist `CompletionReport` for analytics: track completion rate over time
  - [ ] 4-4. `/completion` command: show completion stats (total checks, pass rate, common miss patterns)

- [ ] 5. **Tests and docs**
  - [ ] 5-1. Unit tests: requirement extraction (numbered lists, bullets, prose), completion checking (keyword, LLM), response augmentation (auto-fix, flag)
  - [ ] 5-2. Negative test: missed tool/action requirement cannot be "fixed" by answer rewriting alone
  - [ ] 5-3. Golden test cases: 10 multi-requirement messages with expected extraction results
  - [ ] 5-4. Update architecture, changelog

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
