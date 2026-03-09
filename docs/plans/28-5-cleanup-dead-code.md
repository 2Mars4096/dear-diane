# 28-5: Cleanup Dead Code

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** in-progress
**Goal:** Remove dead routing code after the LLM-first path is proven stable.

## Problem

After 28-1 through 28-4, significant code becomes dead:
- 10 handler classes (mostly bypassed)
- `classify_intent()` heuristics (replaced by LLM decisions)
- Mode-specific prompts (replaced by unified prompt)
- Adapter intent classification (deleted in 28-1)
- `detect_chat_mode()` (mode concept removed)
- Deprecated runtime queue plumbing (superseded by `ConcurrentDispatcher`)

This plan cleans up after the migration is complete and verified.

## Tasks

- [ ] 1. Remove dead handler code
  - [ ] 1-1. Keep only handlers still used by the fast-path (if any): likely `RunHandler` for `/cancel`, `StatusHandler` for `/status`
  - [ ] 1-2. Delete `FileHandler`, `ConversationHandler`, `DirectTaskHandler`, `WorkflowBuildHandler`, `ExperienceHandler`, `PublishHandler`, `MetaGoalHandler`, `WorkflowQueryHandler` (logic lives in tools now)
  - [ ] 1-3. Simplify `HandlerRegistry` or remove entirely
- [ ] 2. Remove dead classifier code
  - [ ] 2-1. Delete `classify_intent()` keyword heuristics (keep `classify_intent_with_llm_fallback` if used for fast-path)
- [x] 2-2. Delete `_classify_adapter_intent()` (already removed in 28-1)
  - [ ] 2-3. Delete `IntentCategory` enum if no longer referenced
- [ ] 3. Remove mode-specific code
  - [ ] 3-1. Delete `detect_chat_mode()`, `CHAT_MODE_ALIASES`
  - [ ] 3-2. Delete `ASK_PROMPT`, `CONVERSATION_PROMPT`, `PLAN_PROMPT`, `DEBUG_PROMPT` (already replaced in 28-4)
  - [ ] 3-3. Remove `mode` parameter from `ChatManager.send_message_with_tools()` (or make it no-op)
- [ ] 4. Remove solver routing layer
  - [ ] 4-1. Evaluate whether `GoalResolver`, `PlanBuilder`, `SolverDecision`, `ExecutionSelector` are still needed
  - [ ] 4-2. If the LLM-first path handles everything, the solver layer is redundant — archive it
  - [ ] 4-3. If the solver adds value for complex multi-step goals, keep it as an optional enhancement
- [ ] 5. Update all docs
  - [ ] 5-1. `docs/architecture.md` — rewrite concierge section for new architecture
  - [ ] 5-2. `docs/llm-api-guide.md` — update chat API docs
  - [ ] 5-3. `docs/changelog.md` — entry for the cleanup
  - [ ] 5-4. `docs/todo.md` — close Phase 18 items
- [x] 6. Remove deprecated runtime queue plumbing
  - [x] 6-1. Stop threading `ProjectMessageQueue` through `Concierge` now that dispatcher queueing is authoritative
  - [x] 6-2. Delete the no-op `_drain_queued_messages()` runtime stub and its dead call sites
  - [x] 6-3. Update concierge runtime test helpers to construct `Concierge` without the deprecated queue argument

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/handlers.py` | Major reduction |
| `src/dan/server/concierge/classifier.py` | Major reduction or delete |
| `src/dan/server/concierge/solver.py` | Evaluate keep/archive |
| `src/dan/server/concierge/executor.py` | Evaluate keep/archive |
| `src/dan/server/chat_manager.py` | Remove mode params and dead prompts |
| `tests/test_concierge/` | Update/remove tests for deleted code |

## Notes

- This plan ONLY runs after 28-2 has been live and stable for a reasonable period
- Feature flag `DAN_LLM_FIRST_CHAT=1` should be the default before cleanup begins
- Keep git history accessible — don't squash the removal commit
- 2026-03-10 safe first slice: removed the unused `classify_intent_with_llm_fallback()` branch and stale package re-export from the concierge classifier surface. Kept `classify_intent()`, handler classes, solver/runtime pieces, and mode plumbing intact because they are still active dependencies in the current runtime.
- 2026-03-10 safe second slice: removed dead `ProjectMessageQueue` plumbing from `runtime.py` and test helpers. Kept `queue.py` itself as an importable compatibility shim and preserved a deprecated-but-ignored `Concierge(..., queue=...)` kwarg for direct callers during the transition. Left solver/handler/mode code untouched because those paths are still exercised in the current runtime.
