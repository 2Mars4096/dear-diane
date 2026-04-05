# 50-3: Chat and Capability Facade Retirement

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Make `chat_manager.py` and `capability_handlers.py` honest thin boundaries by moving remaining workflow/domain behavior into the modules that already claim to own it.

## Dependencies

- **50-1** provides the key-script/ownership map.
- **50-2** should land first for shared apply/run invariants so the facade retirement moves toward already-stable boundaries instead of creating more local helpers.

## Tasks

- [ ] 1. Freeze new behavior in the facades
  - [ ] 1-1. Inventory the remaining behaviorful branches in `chat_manager.py`: at minimum `_build_workflow_from_intent`, `_auto_apply_mutation`, `_smoke_test_run`, mode-promotion logic, prompt/capability-reference assembly, and any workflow-save/delete paths still owned by this file.
  - [ ] 1-2. Inventory the remaining behaviorful branches in `capability_handlers.py`: at minimum graph delete, latest-mutation lookup, mutation apply/save, `apply_last_mutation`, and any schema/registration logic that embeds domain decisions.
  - [ ] 1-3. Mark the destination boundary for each branch before moving code.
- [ ] 2. Retire workflow/domain ownership from `chat_manager.py`
  - [ ] 2-1. Move workflow build/apply/save/smoke branches into `src/dan/server/chat/*` (for chat-orchestration concerns) and `src/dan/server/agent_runtime/*` (for workflow-generation/build concerns).
  - [ ] 2-2. Keep `chat_manager.py` as a delegator/import shim only while live callers migrate.
  - [ ] 2-3. Delete dead branches and helper glue from the old owner as each move lands.
- [ ] 3. Retire workflow/domain ownership from `capability_handlers.py`
  - [ ] 3-1. Move graph delete, latest-mutation lookup, mutation apply/save, and apply-last-mutation behavior into `src/dan/server/capabilities/*` modules.
  - [ ] 3-2. Audit import sites across the codebase (`grep -rn "from.*capability_handlers import\|import.*capability_handlers"`) before deciding whether to reduce `capability_handlers.py` to registration/schema wiring only or delete it with re-exports.
- [ ] 4. Fix prompt double-pass in chat router
  - [ ] 4-1. Fix `src/dan/server/routers/chat.py:495-497`: `attachment_prompt_context` is currently passed into **both** `prompt_context` and `extra_system_instructions`, which means the same attachment context appears twice in the assembled prompt. Remove the duplication — attachment context should be passed through exactly one slot.
  - [ ] 4-2. Verify that downstream consumers (`messages.py:393-462` which treats `prompt_context` as a `## Context` block, and `tier_executors.py:849-1038` which builds `extra_system_instructions` from stage overlays/attachments/action contexts) receive attachment context through exactly one path after the fix.
- [ ] 5. Narrow prompt/build assembly to the right owners
  - [ ] 5-1. Ensure prompt/capability reference assembly is owned by `src/dan/server/chat/prompt_builder.py` or a similarly scoped module, workflow generation stays in `src/dan/server/agent_runtime/workflow_generation.py`, and chat orchestration stays in `src/dan/server/chat/orchestrator.py` — rather than being quietly mixed back into the facade.
- [ ] 6. Regressions and docs
  - [ ] 6-1. Revalidate chat workflow generation, mutation preview/apply, capability registration, and workflow delete/apply flows.
  - [ ] 6-2. Update architecture/docs wording so the surviving files are described honestly.

## Primary Files

- `src/dan/server/chat_manager.py`
- `src/dan/server/chat/`
- `src/dan/server/agent_runtime/`
- `src/dan/server/capability_handlers.py`
- `src/dan/server/capabilities/`
- `src/dan/server/routers/chat.py` (prompt double-pass at lines 495-497)
- `src/dan/agent_runtime/messages.py` (downstream `prompt_context` / `extra_system_instructions` consumer)

## Success Criteria

- `chat_manager.py` no longer owns workflow build/apply/save/smoke behavior
- `capability_handlers.py` no longer owns graph/mutation behavior beyond registration-level concerns
- the repo has fewer compatibility-named files that still act like control-plane owners
- old branches/helpers are deleted as responsibility moves

## Decisions

- Do not replace one facade with another. The destination modules already exist; this plan finishes the migration into them.
- Temporary shims are acceptable only if they are thin and on a clear deletion path.

## Notes

- This plan treats subtraction as a first-class requirement: moving code is not enough unless the old owner shrinks.
- The method inventory in task 1 is intentionally front-loaded so the scope of the move is visible before any code changes.
- At plan start: `chat_manager.py` is 3759 lines; `capability_handlers.py` is 1260 lines. The latter is moderate in size but the question is behavior-vs-registration ratio, not raw line count.
- **Prompt double-pass (task 4) is a quick win.** The `routers/chat.py:495-497` duplication is a two-line fix that immediately reduces prompt noise. Execute early. The broader prompt-envelope standardization (replacing `prompt_context` vs `extra_system_instructions` with typed slots) is 46-6 scope, not this plan.
