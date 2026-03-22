# 29-2: Concierge as Orchestrator

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** in-progress
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
- [x] 1-1. Define `ConciergeGoal` model: `id`, `description`, `status` (active/paused/completed/failed), `created_at`, `updated_at`, `plan_steps` (list), `current_step_index`, `context` (dict), `workflow_id` (optional), `run_history` (list of run_id), `error_history`, `user_interventions`
- [x] 1-2. Define `ConciergeState` model: `active_goals` (list[ConciergeGoal]), `pending_clarifications`, `last_interaction_at`
- [x] 1-3. Persist `ConciergeState` via memory kernel as `WORKING_STATE` items (scope=USER)
- [x] 1-4. Load state on every `process()` call; save after every modification

### 2. Goal lifecycle
- [x] 2-1. `_detect_goal(message, memory_context) -> ConciergeGoal | None` — distinguish goal-bearing messages from simple questions/commands
- [x] 2-2. `_continue_or_new(message, active_goals) -> (ConciergeGoal, action)` — if message relates to an active goal, continue it; otherwise create new
- [x] 2-3. Goal completion: mark completed when workflow succeeds or user confirms satisfaction
- [x] 2-4. Goal failure: mark failed after exhausting retries; store failure as `FAILURE_PATTERN` in memory
- [x] 2-5. Goal pause: explicit user pause or implicit (user starts unrelated topic)

### 3. Planning integration
- [x] 3-1. Move `MetaController.run_session()` core logic into `Concierge._execute_goal()`
- [x] 3-2. Planning step: consult memory kernel (retrieve WORKFLOW_BUILD policy) → decide REUSE/ADAPT/GENERATE
- [x] 3-3. Execution step: build/load workflow → run via RunManager → collect results
- [x] 3-4. Diagnosis step: on failure, extract errors → consult failure patterns/principles → decide repair strategy
- [x] 3-5. Repair step: apply parameter fix / structural fix / regenerate (bounded, max 3 attempts)
- [x] 3-6. Human check-in: after each major step (build, run, repair), optionally present status to user based on `ActionPolicy`

### 4. Autonomous vs. interactive mode
- [x] 4-1. `autonomy_level` config: `INTERACTIVE` (check in after every step), `SUPERVISED` (check in on failure/completion), `AUTONOMOUS` (only check in on unrecoverable failure)
- [x] 4-2. `DAN_CONCIERGE_AUTONOMY` env var (default: `SUPERVISED`)
- [x] 4-3. Surface-specific defaults: CLI/Editor → SUPERVISED, WhatsApp/Telegram → AUTONOMOUS (to reduce message noise)
- [x] 4-4. Per-goal override: user can say "just do it" or "show me every step"

### 5. MetaController deprecation
- [x] 5-1. Extract reusable logic from `MetaController` into shared utility functions
- [x] 5-2. `MetaSession` → replaced by `ConciergeGoal` (superset) for goal model; session still used for execution
- [x] 5-3. `MetaController.run_session()` → replaced by `Concierge._execute_goal()` (concierge calls run_session)
- [x] 5-4. Keep `MetaController` as deprecated wrapper that delegates to concierge for backward compat
- [x] 5-5. Update `MetaGoalHandler` to create a `ConciergeGoal` instead of a `MetaSession`

### 6. Memory-informed decisions
- [x] 6-1. Every `process()` call starts with `memory_kernel.retrieve(message, classify_task_type(message))`
- [x] 6-2. Memory context is passed to goal detection, planning, and execution
- [x] 6-3. After each interaction, extract and store memory candidates (facts learned, errors encountered, preferences observed)
- [x] 6-4. `_extract_memory_candidates(message, response, goal_context) -> list[MemoryItem]`

### 7. Long-running turn reassurance
- [x] 7-1. If a user query has not received an initial user-facing response within 5 seconds, the concierge emits a short reassuring progress note (e.g., "Working on your request — searching sources..." or "Still running the analysis, one moment..."). The note is calming and specific to what is happening, not generic filler.
- [x] 7-2. Implementation: `process()` wraps `_process_inner()` with an `asyncio.Queue` + timeout mechanism. If inner work hasn't yielded within `_REASSURANCE_INITIAL_DELAY` (default 5 s, configurable via `DAN_CONCIERGE_REASSURANCE_DELAY`), a `ChatCompleteEvent` progress note is yielded.
- [ ] 7-3. The progress note content should reflect the current activity phase when possible (tool calls in flight, LLM generation, workflow execution, MCP tool call, etc.). *(deferred — currently uses rotating pool of calming messages; phase-aware hints require plumbing activity state through the generator)*
- [x] 7-4. Subsequent periodic updates every ~15 s for very long turns (`_REASSURANCE_REPEAT_INTERVAL`, configurable via `DAN_CONCIERGE_REASSURANCE_INTERVAL`). Messages rotate through a pool so they don't repeat.
- [x] 7-5. Unit test: 7 tests in `tests/test_server/test_concierge_reassurance.py` — fast response (no reassurance), slow response (reassurance emitted before real answer), multiple reassurances on very long turns, disabled when delay=0, message rotation, exception propagation, multi-event forwarding.

### 8. Tests
- [x] 8-1. Unit tests for ConciergeGoal lifecycle (create, continue, complete, fail, pause)
- [x] 8-2. Unit tests for goal detection (goal-bearing vs. simple question)
- [x] 8-3. Unit tests for planning integration (_execute_goal delegates to MetaController.run_session)
- [x] 8-4. Integration test: state persistence roundtrip + _execute_goal with mocked MetaController
- [x] 8-5. Integration test: autonomous mode runs to completion without user interaction
- [x] 8-6. Integration test: memory candidates extracted and stored after interaction

### 9. Review follow-up: structured task and deliverable state
- [ ] 9-1. Replace regex-derived task state reconstruction from chat prose with a structured per-goal progress ledger stored on `ConciergeGoal`: current objective, completed steps, pending steps, blockers, deliverables, and artifact references.
- [ ] 9-2. Emit progress-state updates during orchestration and build-session execution time, then project a compact derived summary onto project `Task` state instead of reconstructing state only at finalization time.
- [ ] 9-3. Generate user-facing completion summaries from the structured ledger, keeping transcript parsing only as a bounded backward-compat fallback for legacy tasks that lack a ledger.
- [ ] 9-4. Add regressions for paraphrased turns, reordered conversation snippets, and artifact-heavy goals so progress state does not drift when wording changes.

## Decisions

- Planning decision uses same thresholds as `reuse_first_decision` (0.8 reuse, 0.4 adapt) but runs as a lightweight pre-check before MetaController; the heavier interactive REUSE/ADAPT prompt is in the outer process() loop.
- Diagnosis delegates to `BuildSessionManager.diagnose_for_failure()` when a build session is active; falls back to direct `retrieve_by_task(task_type="workflow_repair")` otherwise.
- Check-in is filtered at the final yield — INTERACTIVE includes plan/run/diagnosis detail, SUPERVISED shows only failure/completion, AUTONOMOUS is silent on success.
- 2026-03-21 review decision: authoritative structured progress state should live on `ConciergeGoal`; project `Task` state is a derived projection for UI/search/resume surfaces, not a second source of truth.

## Notes

- 2026-03-09 (b): Wired full plan→execute→diagnose→repair→check-in loop in `_execute_goal()`. Added `_decide_plan_action`, `_diagnose_goal_failure`, `_compose_check_in` helper methods. 12 integration tests in `tests/test_concierge/test_goal_orchestration.py`. Only remaining unchecked tasks: 5-1 (shared utility extraction).
- 2026-03-09 (c): Extracted shared MetaController helpers into `src/dan/meta/utils.py` (task 5-1). `plan_from_dict()`, `topo_sort_workflows()`, `create_meta_session()`, `goal_to_session_fields()`, `validate_session_resumable()`, `session_is_terminal()`. MetaController delegates to shared module. All original 29-2 tasks complete.
- 2026-03-09 reconciliation: core stateful orchestration is live (goal/state models, memory-informed routing, autonomy levels, MetaController bridging, task-local request state, build-session continuation). The remaining checklist is mostly about deeper shared-utility extraction, autonomous end-to-end completion coverage, and richer repair-step wiring.
- 2026-03-21 review follow-up: [product review](../reviews/2026-03-21-product-review.md) surfaced that some completion and task status is still inferred from conversation wording. Section 9 keeps this plan open until goal progress and deliverables are driven by structured state rather than transcript regexing.
- Practical research/report acceptance and full chat-level provenance are tracked separately in [29-8](29-8-practical-research-quality-and-audit.md) so this plan can stay focused on orchestration mechanics.
- The concierge stays as a thin layer above ChatManager. It does not replace ChatManager's LLM interaction — it orchestrates when and how ChatManager is called.
- Goal state is intentionally lightweight. Heavy workflow state lives in the workflow engine; the concierge only tracks goal-level progress.
- The MetaController's `SystemArchitect` (multi-workflow decomposition) is out of scope for this plan. It can be wired in later.
