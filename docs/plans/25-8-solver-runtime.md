# 25-8: Solver Runtime — Understand, Plan, Act, Reflect

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Make the top-level router solver-first: infer what the user actually needs, produce a concrete deliverable-oriented plan, execute it, and reflect afterward instead of treating classification as the core control loop.

## Tasks
- [x] 1. Define the solver-first control loop and core models
  - [x] 1-1. Add `Understand -> Plan -> Act -> Reflect` as the top-level runtime contract in `src/dan/server/concierge/`.
  - [x] 1-2. Create a structured `SolverDecision` model that captures `user_goal`, `requested_deliverable`, `execution_mode`, `plan_steps`, `assumptions`, `clarification_question`, `fallback_chain`, and `save_candidate`.
  - [x] 1-3. Keep continuation parsing, explicit file/path handling, status checks, run control, and destructive guards as deterministic prechecks ahead of solver reasoning.
- [x] 2. Implement `GoalResolver`
  - [x] 2-1. Build a planning context package from the current message, project/task summary, recent task turns, active runs, pending actions, and retrieved workflow/experience candidates.
  - [x] 2-2. Use one solver-oriented LLM pass to infer the real goal, requested deliverable, ambiguity level, and best-useful interpretation.
  - [x] 2-3. Enforce the one-question rule: only ask when ambiguity blocks useful action; otherwise proceed with the best helpful assumption.
- [x] 3. Implement `PlanBuilder`
  - [x] 3-1. Turn the resolved goal into ordered plan steps with explicit completion criteria.
  - [x] 3-2. Choose among `direct_action`, `workflow_reuse`, `workflow_adapt`, `workflow_build`, `run_control`, `status_pull`, `experience_lookup`, `conversation_synthesis`, and `meta_delegate`.
  - [x] 3-3. Require every plan to end in a deliverable, not just advice.
- [x] 4. Wire the solver runtime into the concierge foundation
  - [x] 4-1. Make classifier output a fast-path hint rather than the final top-level decision.
  - [x] 4-2. Preserve existing `ProjectStore`, `ProjectContextResolver`, queue, progress, and pending-follow-up behavior from `25-6` / `25-7`.
  - [x] 4-3. Ensure `Concierge.process()` can execute solver decisions while still supporting current fast paths.
- [x] 5. Add tests and rollout guards
  - [x] 5-1. Add unit tests for goal resolution, clarification thresholds, and plan generation.
  - [x] 5-2. Add integration tests showing direct solve vs. workflow reuse vs. workflow build vs. meta delegation.
  - [x] 5-3. Keep a compatibility fallback to the current concierge path during rollout.

## Decisions
- `25-6` and `25-7` remain the shared runtime substrate; this plan changes the top-level decision loop, not the existence of project/task state or policy.
- The LLM is used as the planner/problem solver, not just as an intent picker.
- Classification survives only as a cheap optimization for obvious cases and pending follow-ups.

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/solver.py` | Create — `GoalResolver`, `PlanBuilder`, `SolverDecision` |
| `src/dan/server/concierge/models.py` | Modify — add `SolverDecision` dataclass |
| `src/dan/server/concierge/classifier.py` | Modify — demote to fast-path hint generator |
| `src/dan/server/concierge/__init__.py` | Modify — wire solver runtime as top-level loop |
| `tests/test_concierge/test_solver.py` | Create — goal resolution, planning, and execution mode tests |

## Dependencies

- **25-6 (Concierge Runtime)** provides `Project`, `Task`, `ProjectStore`, `ProjectContextResolver`, and handler backends.
- **25-7 (Behavior Policy)** provides queue, clarification, progress, and safety policy that the solver respects.
- Experience retrieval uses `ExperienceIndex` from the existing experience system.

## Acceptance Criteria

- Solver runtime produces a `SolverDecision` with explicit goal, deliverable, and execution mode for every non-fast-path message.
- One-question clarification is enforced: ambiguous messages either get one focused question or proceed with the best useful assumption.
- Classification remains as a fast-path optimization for obvious cases (yes/no/number, status, explicit paths) but is no longer the top-level brain.
- Project/task state, queueing, and pending-follow-up behavior from 25-6/25-7 remain intact.
- Direct solve, workflow reuse, workflow adapt, workflow build, and meta delegation are all reachable execution modes.

## Notes

- The design target is "deliver a result, not a response."
- The runtime should prefer correcting a useful default over interrogating the user.
- This plan changes the top-level control flow, not the elimination of existing infrastructure.
