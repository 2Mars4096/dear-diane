# 29-2: Concierge as Orchestrator

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** not-started
**Goal:** Transform the concierge from a stateless message classifier into a stateful orchestrator that owns goals, plans, and autonomous action — folding the MetaController session loop into the concierge itself.

## Context

Currently the concierge (`runtime.py`) is essentially:

```
classify_intent(message) → pick_handler() → forward_to_handler() → return result
```

No persistent state across messages. No goals. No plans. The MetaController (`meta/controller.py`) has the right loop structure (plan → execute → diagnose → repair) but lives as a separate system that's only triggered by `META_GOAL` intent classification — a path most users never hit.

The concierge should become:

```
receive_message()
  → load active goals + working state from memory kernel
  → retrieve relevant memory (facts, preferences, patterns, failures)
  → decide: continue active goal, start new goal, or just answer
  → if goal: plan → execute → diagnose → iterate (with optional human check-ins)
  → update memory with learnings
  → respond
```

## Tasks

### 1. Concierge state model
- [ ] 1-1. Define `ConciergeGoal` model: `id`, `description`, `status` (active/paused/completed/failed), `created_at`, `updated_at`, `plan_steps` (list), `current_step_index`, `context` (dict), `workflow_id` (optional), `run_history` (list of run_id), `error_history`, `user_interventions`
- [ ] 1-2. Define `ConciergeState` model: `active_goals` (list[ConciergeGoal]), `pending_clarifications`, `last_interaction_at`
- [ ] 1-3. Persist `ConciergeState` via memory kernel as `WORKING_STATE` items (scope=USER)
- [ ] 1-4. Load state on every `process()` call; save after every modification

### 2. Goal lifecycle
- [ ] 2-1. `_detect_goal(message, memory_context) -> ConciergeGoal | None` — distinguish goal-bearing messages from simple questions/commands
- [ ] 2-2. `_continue_or_new(message, active_goals) -> (ConciergeGoal, action)` — if message relates to an active goal, continue it; otherwise create new
- [ ] 2-3. Goal completion: mark completed when workflow succeeds or user confirms satisfaction
- [ ] 2-4. Goal failure: mark failed after exhausting retries; store failure as `FAILURE_PATTERN` in memory
- [ ] 2-5. Goal pause: explicit user pause or implicit (user starts unrelated topic)

### 3. Planning integration
- [ ] 3-1. Move `MetaController.run_session()` core logic into `Concierge._execute_goal()`
- [ ] 3-2. Planning step: consult memory kernel (retrieve WORKFLOW_BUILD policy) → decide REUSE/ADAPT/GENERATE
- [ ] 3-3. Execution step: build/load workflow → run via RunManager → collect results
- [ ] 3-4. Diagnosis step: on failure, extract errors → consult failure patterns/principles → decide repair strategy
- [ ] 3-5. Repair step: apply parameter fix / structural fix / regenerate (bounded, max 3 attempts)
- [ ] 3-6. Human check-in: after each major step (build, run, repair), optionally present status to user based on `ActionPolicy`

### 4. Autonomous vs. interactive mode
- [ ] 4-1. `autonomy_level` config: `INTERACTIVE` (check in after every step), `SUPERVISED` (check in on failure/completion), `AUTONOMOUS` (only check in on unrecoverable failure)
- [ ] 4-2. `DAN_CONCIERGE_AUTONOMY` env var (default: `SUPERVISED`)
- [ ] 4-3. Surface-specific defaults: CLI/Editor → SUPERVISED, WhatsApp/Telegram → AUTONOMOUS (to reduce message noise)
- [ ] 4-4. Per-goal override: user can say "just do it" or "show me every step"

### 5. MetaController deprecation
- [ ] 5-1. Extract reusable logic from `MetaController` into shared utility functions
- [ ] 5-2. `MetaSession` → replaced by `ConciergeGoal` (superset)
- [ ] 5-3. `MetaController.run_session()` → replaced by `Concierge._execute_goal()`
- [ ] 5-4. Keep `MetaController` as deprecated wrapper that delegates to concierge for backward compat
- [ ] 5-5. Update `MetaGoalHandler` to create a `ConciergeGoal` instead of a `MetaSession`

### 6. Memory-informed decisions
- [ ] 6-1. Every `process()` call starts with `memory_kernel.retrieve(message, classify_task_type(message))`
- [ ] 6-2. Memory context is passed to goal detection, planning, and execution
- [ ] 6-3. After each interaction, extract and store memory candidates (facts learned, errors encountered, preferences observed)
- [ ] 6-4. `_extract_memory_candidates(message, response, goal_context) -> list[MemoryItem]`

### 7. Tests
- [ ] 7-1. Unit tests for ConciergeGoal lifecycle (create, continue, complete, fail, pause)
- [ ] 7-2. Unit tests for goal detection (goal-bearing vs. simple question)
- [ ] 7-3. Unit tests for planning integration (reuse/adapt/generate decision)
- [ ] 7-4. Integration test: multi-message goal flow (build → error → fix → success)
- [ ] 7-5. Integration test: autonomous mode runs to completion without user interaction
- [ ] 7-6. Integration test: memory candidates extracted and stored after interaction

## Decisions

- (to be filled during execution)

## Notes

- The concierge stays as a thin layer above ChatManager. It does not replace ChatManager's LLM interaction — it orchestrates when and how ChatManager is called.
- Goal state is intentionally lightweight. Heavy workflow state lives in the workflow engine; the concierge only tracks goal-level progress.
- The MetaController's `SystemArchitect` (multi-workflow decomposition) is out of scope for this plan. It can be wired in later.
