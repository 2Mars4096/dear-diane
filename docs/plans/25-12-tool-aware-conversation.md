# 25-12: Tool-Aware Conversation & Solver Activation

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** not-started
**Goal:** Eliminate the class of bugs where the LLM fabricates live data by giving conversation paths real tool access and activating the solver runtime on adapter surfaces.

## Problem Statement

Today's architecture has a structural gap:

1. **Keyword classifier gates tool access.** If the classifier doesn't match "stock price" / "share price" exactly, the request falls through to `conversation` → `ask` mode, which is text-only (no tools, no web search, no code execution).
2. **The text-only fallback has no honesty guard.** The LLM in `ask` mode fabricates plausible-looking live data (prices, dates, stats) from stale training data without disclosing it can't look things up.
3. **The solver runtime (25-8) exists but isn't activated on adapter surfaces.** `build_concierge()` creates the solver, but the adapter's chat-mode path routes through the server API which may or may not have the solver enabled for the right messages.

These compound: narrow classifier → wrong handler → LLM without tools → hallucinated "answer" that looks correct but is stale or fabricated.

## Tasks

### Layer 1: Conversation with tool access (medium-term)
- [ ] 1. Give `ConversationHandler` access to `web_search` as an LLM-callable tool
  - [ ] 1-1. Add a `conversation_with_tools` execution mode that passes `web_search` (and optionally other read-only tools) to the LLM alongside the conversation prompt
  - [ ] 1-2. When the LLM decides it needs live data, it calls `web_search` as a tool call rather than fabricating
  - [ ] 1-3. Keep `ask` mode read-only for graph questions; add a new `conversation` mode that includes read-only external tools
- [ ] 2. Give `DirectTaskHandler` fallback the same tool access
  - [ ] 2-1. Replace `_fallback_to_chat` with a tool-aware path so even misclassified direct tasks can still reach web search via LLM tool calling
  - [ ] 2-2. Remove the classifier-gated `_should_search_web` pre-check — let the LLM decide when to search

### Layer 2: Solver activation on all surfaces (long-term)
- [ ] 3. Activate the solver runtime for adapter-originated messages
  - [ ] 3-1. Ensure `build_concierge(use_solver=True)` is the default for server-side concierge used by adapter chat mode
  - [ ] 3-2. The solver's `GoalResolver` replaces keyword classification: "user needs live financial data" → `DIRECT_ACTION` with web search, not "does the message contain 'stock price'?"
  - [ ] 3-3. Verify the solver path works end-to-end from WhatsApp → server API → concierge → solver → web search → formatted reply
- [ ] 4. Teach the solver to recognize live-data needs
  - [ ] 4-1. Add "needs real-time data" as a signal in the solver LLM prompt so the planner routes to web search for prices, weather, scores, etc.
  - [ ] 4-2. The solver should prefer web search over LLM knowledge for any time-sensitive query
  - [ ] 4-3. When web search fails, the solver should say so explicitly rather than falling back to fabrication

### Layer 3: Execution-aware reflection (long-term)
- [ ] 5. Post-execution validation for live-data claims
  - [ ] 5-1. Add a reflection step gated by a cheap regex pre-filter (detect `$123.45`, `12.3%`, date patterns) — only invoke LLM reflection when suspected unsourced numeric claims are found, to avoid adding latency to every response
  - [ ] 5-2. Flag unsourced numeric claims with a disclaimer: "Note: I couldn't verify this with a live source"
  - [ ] 5-3. Track which responses contained unsourced claims for quality monitoring

### Tests and rollout
- [ ] 6. Add targeted tests
  - [ ] 6-1. Test: "rocket lab close price" via conversation path triggers web search tool call (not fabrication)
  - [ ] 6-2. Test: solver-enabled adapter path routes live-data queries to web search
  - [ ] 6-3. Test: text-only fallback with honesty guard refuses to fabricate prices
  - [ ] 6-4. Test: graph-specific ask-mode questions still work without tools (no regression)

## Decisions

- `ask` mode stays tool-less for graph-specific questions (its original purpose)
- A new `conversation` execution mode gets read-only external tools (web search, etc.)
- The solver runtime is the long-term replacement for keyword classification — classification survives only as a fast-path optimization
- The honesty guard in `ASK_PROMPT` is a stop-gap that prevents the worst harm while the tool-aware path is built

## Files

| File | Action |
|---|---|
| `src/dan/server/chat_manager.py` | Modify — add conversation-with-tools mode, honesty guard (done for ASK_PROMPT) |
| `src/dan/server/concierge/handlers.py` | Modify — give ConversationHandler and DirectTaskHandler tool-aware fallback |
| `src/dan/server/concierge/solver.py` | Modify — add live-data detection to GoalResolver planning prompt |
| `src/dan/server/concierge/executor.py` | Modify — wire conversation-with-tools execution mode |
| `tests/test_concierge/test_live_data.py` | Create — fabrication prevention and tool-routing tests |

## Dependencies

- **25-8 (Solver Runtime)** provides GoalResolver, PlanBuilder, SolverDecision
- **25-10 (Execution Selector)** maps solver decisions to handler backends
- **25-11 (Fallback Policy)** ensures "never just stop" — but also "never fabricate"

## Acceptance Criteria

- "What's the close price for rocket lab?" never returns a fabricated number — it either searches the web and returns a sourced result, or says "I'd need to search the web for that"
- Any phrasing of live-data requests reaches web search regardless of exact keywords
- Graph-specific ask-mode questions continue to work tool-free
- Adapter surfaces (WhatsApp, Telegram) benefit from the same tool access as dan-chat

## Notes

- The short-term honesty guard (ASK_PROMPT modification) is already deployed — this plan covers the structural fix
- This plan does NOT add code execution capability — web search is the primary live-data tool
- The solver LLM prompt should explicitly list categories of queries that need live data: prices, weather, scores, exchange rates, event dates, news
