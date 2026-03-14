# 28-5: Cleanup Dead Code

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** completed
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

- [x] 1. Remove dead handler code — **DELETED in Plan 34 concierge rewrite**
  - [x] 1-1. `RunHandler`, `StatusHandler` replaced by tiered executors
  - [x] 1-2. `FileHandler`, `ConversationHandler`, `DirectTaskHandler`, `WorkflowBuildHandler`, `ExperienceHandler`, `PublishHandler`, `MetaGoalHandler`, `WorkflowQueryHandler` deleted
  - [x] 1-3. `HandlerRegistry` deleted — `handlers.py` no longer exists
- [x] 2. Remove dead classifier code — **DELETED in Plan 34 concierge rewrite**
  - [x] 2-1. `classify_intent()` keyword heuristics deleted (file `classifier.py` gone)
  - [x] 2-2. `_classify_adapter_intent()` already removed in 28-1
  - [x] 2-3. `IntentCategory` enum kept — still actively used by `policy.py`, `triage.py`, `runtime.py`, `tier_executors.py`, `tiered_dispatch.py`
- [x] 3. Remove mode-specific code — **kept: mode system still active**
  - [x] 3-1. `detect_chat_mode()`, `CHAT_MODE_ALIASES` kept — actively used in `routers/chat.py`, `chat_local.py`
  - [x] 3-2. `ASK_PROMPT`, `CONVERSATION_PROMPT`, `PLAN_PROMPT`, `DEBUG_PROMPT` deleted in 28-4
  - [x] 3-3. `mode` parameter kept — still used for tool gating in `ChatManager`
- [x] 4. Remove solver routing layer — **DELETED in Plan 34 concierge rewrite**
  - [x] 4-1. `GoalResolver`, `PlanBuilder`, `SolverDecision`, `ExecutionSelector` all deleted
  - [x] 4-2. Solver layer fully removed — `solver.py` and `executor.py` no longer exist
  - [x] 4-3. Tiered executors (`tier_executors.py`) replaced the solver layer
- [x] 5. Update all docs
  - [x] 5-1. `docs/architecture.md` — updated during Plan 34
  - [x] 5-2. `docs/llm-api-guide.md` — updated during Plan 34
  - [x] 5-3. `docs/changelog.md` — entry for cleanup
  - [x] 5-4. `docs/todo.md` — Phase 18 items closed
- [x] 6. Remove deprecated runtime queue plumbing
  - [x] 6-1. Stop threading `ProjectMessageQueue` through `Concierge` now that dispatcher queueing is authoritative
  - [x] 6-2. Delete the no-op `_drain_queued_messages()` runtime stub and its dead call sites
  - [x] 6-3. Update concierge runtime test helpers to construct `Concierge` without the deprecated queue argument
- [x] 7. Final audit (2026-03-14)
  - [x] 7-1. Confirmed `handlers.py`, `classifier.py`, `solver.py`, `executor.py`, `queue.py` all deleted
  - [x] 7-2. Confirmed no stale imports referencing deleted modules in `src/` or `tests/`
  - [x] 7-3. Confirmed no stale test files for deleted modules
  - [x] 7-4. Removed dead `validate_terminal_content()` + `_APOLOGY_ONLY_PATTERNS` + `_APOLOGY_RE` from `policy.py` (only consumer was deleted solver)
  - [x] 7-5. Removed stale `validate_terminal_content` re-export from `concierge/__init__.py`

## Files

| File | Outcome |
|---|---|
| `src/dan/server/concierge/handlers.py` | **Deleted** (Plan 34) |
| `src/dan/server/concierge/classifier.py` | **Deleted** (Plan 34) |
| `src/dan/server/concierge/solver.py` | **Deleted** (Plan 34) |
| `src/dan/server/concierge/executor.py` | **Deleted** (Plan 34) |
| `src/dan/server/concierge/queue.py` | **Deleted** (28-5 slice 2) |
| `src/dan/server/concierge/policy.py` | Removed dead `validate_terminal_content()` + regex constants (28-5 final audit) |
| `src/dan/server/concierge/__init__.py` | Removed stale `validate_terminal_content` re-export (28-5 final audit) |
| `src/dan/server/chat_manager.py` | Dead prompts removed (28-4); mode params kept (still active) |
| `tests/test_concierge/` | Stale test files already deleted in Plan 34 |

## Notes

- This plan ONLY runs after 28-2 has been live and stable for a reasonable period
- The temporary `DAN_LLM_FIRST_CHAT` rollback is gone; the unified prompt path is now the only supported message-building route
- Keep git history accessible — don't squash the removal commit
- 2026-03-10 safe first slice: removed the unused `classify_intent_with_llm_fallback()` branch and stale package re-export from the concierge classifier surface. Kept `classify_intent()`, handler classes, solver/runtime pieces, and mode plumbing intact because they are still active dependencies in the current runtime.
- 2026-03-10 safe second slice: removed dead `ProjectMessageQueue` plumbing from `runtime.py` and test helpers. Kept `queue.py` itself as an importable compatibility shim and preserved a deprecated-but-ignored `Concierge(..., queue=...)` kwarg for direct callers during the transition. Left solver/handler/mode code untouched because those paths are still exercised in the current runtime.
- 2026-03-10 safe third slice: removed the unused `graph_state` parameter from `detect_chat_mode()` and updated the server/CLI auto-mode call sites plus auto-mode docs/tests. Kept the actual mode system (`detect_chat_mode()` itself, `CHAT_MODE_ALIASES`, prompt branches, and `mode` plumbing) because those paths are still active in `app.py`, `chat_local.py`, `handlers.py`, and `ChatManager`.
- 2026-03-10 safe fourth slice: removed the unused `context` parameter from `PlanBuilder.build_plan()`, deleted the dead `format_completion_metadata()` helper from `executor.py`, and added a focused solver regression test. Kept the solver path itself (`GoalResolver`, `PlanBuilder`, `ExecutionSelector`, `_solver_path`) intact because it is still part of the live concierge runtime.
- 2026-03-10 safe fifth slice: removed the unused fallback-policy helper trio (`FALLBACK_LADDER`, `suggest_fallback_strategy()`, `format_terminal_message()`) from `policy.py` and concierge package re-exports, kept `validate_terminal_content()` as the still-live solver guardrail, and added a focused policy regression test. Left the live handler, solver, and mode stacks intact because runtime audit still shows them on active paths.
- 2026-03-10 safe sixth slice: removed the legacy `_build_messages()` prompt fallback and the obsolete `ASK_PROMPT` / `CONVERSATION_PROMPT` / `PLAN_PROMPT` / `DEBUG_PROMPT` constants, leaving the unified prompt path as the only supported message-building route. Follow-up polish kept the empty-workflow placeholder in the unified prompt, added an explicit text-only no-tools override for `send_message()`, and fixed `build_debug_context()` to use the newest failed run by `started_at`. Kept the broader mode system (`mode`, `detect_chat_mode()`, alias normalization, and mode-based tool gating) intact because those still affect routing and tool exposure outside prompt selection.
- 2026-03-13 safe seventh slice: trimmed prompt-design drift inside `chat_manager.py` without changing routing. The unified prompt now uses a compact registry-backed tool-family summary instead of the stale hardcoded tool list / orphaned `{mcp_block}` placeholder, research-report guidance is injected only for research-like asks instead of every turn, the system prompt is assembled as explicit sections instead of repeated string concatenation, and build clarification prompts are domain-agnostic rather than paper-specific. Kept the broader mode system and the older graph-builder prompt templates untouched because they are still either active or need a separate runtime cleanup decision.
- 2026-03-14 final audit: confirmed all legacy files (`handlers.py`, `classifier.py`, `solver.py`, `executor.py`, `queue.py`) fully deleted with no stale imports or test files. Removed dead `validate_terminal_content()` + `_APOLOGY_ONLY_PATTERNS` + `_APOLOGY_RE` from `policy.py` — only consumer was the deleted solver path. Removed stale re-export from `concierge/__init__.py`. Verified `detect_chat_mode()`, `CHAT_MODE_ALIASES`, `normalize_chat_mode()`, `IntentCategory`, `RouteDecision`, `SurfaceMessage`, and the `mode` parameter are all still actively used. Plan complete.
