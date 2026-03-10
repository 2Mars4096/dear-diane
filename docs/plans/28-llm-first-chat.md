# 28: LLM-First Chat Architecture

**Status:** in-progress
**Goal:** Replace the 4-layer routing stack (adapter keywords → concierge classifier → 10 handlers → mode-gated tools) with a single LLM-driven path where the model has all tools and decides what to do.

## Problem

The current chat architecture was built incrementally across Phases 12–16. Each layer was added to patch the previous one:

1. **Adapter `_classify_adapter_intent()`** — keyword heuristics route locally before the server
2. **Concierge `classify_intent()`** — 10-category heuristic + LLM fallback selects a handler
3. **10 Handler classes** — each calls ChatManager differently (different modes, different tool access)
4. **ChatManager modes** — ask/agent/conversation/plan/debug filter which tools the LLM sees

Errors compound: "search for papers" hits file keywords at layer 2 → FileHandler searches locally → user never gets web results. "Summarize this paper" misses file cues → ConversationHandler → LLM has no PDF tool → fabricates content.

Cursor solved this: one LLM call, all tools available, the model decides. The LLM is a better router than any keyword heuristic.

## Architecture

```
Surface (WhatsApp / Telegram / CLI / Editor)
              │
              ▼
     ┌─────────────────┐
     │ Context Assembly │   enrich, don't route
     │  • Project scope │
     │  • User profile  │
     │  • Chat history  │
     │  • Surface hints │
     └────────┬────────┘
              ▼
     ┌─────────────────┐
     │  LLM + All Tools│   single call, multi-turn
     │  • System prompt │
     │  • All tools     │
     │  • Tool loop     │   call → result → call → ... → text
     └────────┬────────┘
              ▼
     ┌─────────────────┐
     │ Response Actions │   post-LLM safety/UX
     │  • File delivery │
     │  • Confirmations │
     │  • Claim check   │
     │  • Context save  │
     └────────┬────────┘
              ▼
         User gets reply
```

## Sub-Plans

- [x] [28-1-strip-adapter-routing](28-1-strip-adapter-routing.md) — A. Remove adapter-side keyword classification and local special cases; everything goes to server
- [x] [28-2-unified-tool-dispatch](28-2-unified-tool-dispatch.md) — B. Replace classifier→handler dispatch with single LLM-with-all-tools path; multi-turn tool loop
- [x] [28-3-response-actions](28-3-response-actions.md) — C. Post-LLM response actions: file delivery, destructive confirmation, claim validation, context save
- [x] [28-4-system-prompt-design](28-4-system-prompt-design.md) — D. Unified system prompt with tool guidelines, surface hints, and anti-fabrication rules
- [ ] [28-5-cleanup-dead-code](28-5-cleanup-dead-code.md) — E. Remove dead classifier, handler, and mode-gating code; update docs
- [x] [28-6-real-world-test-scenarios](28-6-real-world-test-scenarios.md) — F. Five end-to-end scenarios: lit review, equity report, deep research, computer task, casual utility

## Migration

Each sub-plan is independently shippable. The old path stays as fallback until the new path is proven:
- 28-1 can ship alone (adapter cleanup, no server changes)
- 28-2 is the core change (new dispatch loop, old path as fallback)
- 28-3 adds safety/UX after 28-2
- 28-4 iterates on prompt quality
- 28-5 is cleanup after all paths verified

## Decisions

- The concierge `ProjectContextResolver` is kept — it enriches context, not routes
- `UserProfile` + `ConversationMemory` are kept — they inform the LLM
- The concierge classifier MAY be kept as a cheap fast-path for unambiguous commands (cancel, status) — but it is never a gatekeeper
- Broader tool access is the direction, but the runtime may keep some mode-based filtering and text-only fallback behavior until later cleanup proves those paths can be removed safely
- Safety (confirm destructive actions, cost thresholds) moves to the Response Actions layer
- Multi-turn tool loop has a cap (default 10 rounds) to prevent runaway

## Files (primary)

| File | Action |
|---|---|
| `src/dan/cli/adapter.py` | Major — strip `_classify_adapter_intent`, `/find`, `/send`, pending state |
| `src/dan/server/concierge/runtime.py` | Major — new `process()` path: context → LLM → tool loop → actions |
| `src/dan/server/chat_manager.py` | Major — multi-turn tool loop, unified prompt work, and later cleanup evaluation for remaining mode-based gating |
| `src/dan/server/concierge/classifier.py` | Reduce to optional fast-path |
| `src/dan/server/concierge/handlers.py` | Most handlers become dead code |
| `src/dan/server/capability_handlers.py` | May need new action tools (send_file_to_user) |
| `src/dan/server/app.py` | Simplify `/api/chat/message` endpoint |

## Acceptance

**Core routing:**
- "search for papers on export controls" → `web_search`, not local file search
- "summarize the paper at ~/path/paper.pdf" → `pdf_read`, real content
- "what time is it?" → `current_datetime`, correct answer
- "send me the late payment doc" → `list_directory` → file delivery
- No `<FunctionCall>` XML ever reaches the user

**Multi-turn:**
- "find papers on X and summarize the top 3" → web_search → web_fetch × 3 → summary
- "run a regression on ~/data.csv" → file_read → shell_command → results summary
- "do a literature review on geopolitics and trade" → 5+ web searches → structured review with real citations

**Utility:**
- "write me a birthday greeting" → pure text, no tools, fast
- "calculate 15% compound growth on $10k over 7 years" → shell_command → exact number
- "copy that to clipboard" → clipboard tool
- "email sarah@example.com about the meeting" → send_email
- "take a screenshot" → screenshot + file delivery

**Long reports:**
- Equity research report, literature review, deep research → split into multiple WhatsApp messages at section boundaries

**Reliability:**
- All existing adapter/CLI/editor chat paths continue working
- The temporary `DAN_LLM_FIRST_CHAT` rollback path was removed later during `28-5` cleanup once the unified prompt path became the only supported message-building route
