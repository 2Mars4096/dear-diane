# 31-28: Long-Horizon Goal Contract

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Carry the bounded concierge deliberation contract into long-horizon orchestration so planning, inline workflow extraction, and goal loops keep checking the same goal, deliverable, constraints, and completion criteria.

## Tasks
- [x] 1. Persist the contract on long-horizon goals
 - [x] 1-1. Store a normalized `goal_contract` in `ConciergeGoal.context` when a plan/long-horizon message becomes a goal.
 - [x] 1-2. Keep route target/action hints alongside the bounded deliberation fields so downstream planners see the same intent summary.
- [x] 2. Inject the contract into deeper orchestration paths
 - [x] 2-1. Pass the contract into `WorkflowPlanner.plan()` through `MetaSession.goal_context`.
 - [x] 2-2. Feed the contract into inline `extract_workflow_intent()` prompts so non-MetaController long-horizon goals use the same guidance.
 - [x] 2-3. Feed the contract into goal-loop strategy prompts when a goal loop state carries one.
- [x] 3. Keep the contract representation shared and bounded
 - [x] 3-1. Add a small shared helper for normalization/rendering so planner, intent extraction, and goal loop prompts stay aligned.
- [x] 4. Verify
 - [x] 4-1. Add focused regressions for runtime goal detection, planner prompt rendering, MetaController propagation, inline intent extraction, and goal-loop prompts.
 - [x] 4-2. Run focused meta-orchestration regression suites.

## Decisions
- The long-horizon path reuses the existing bounded deliberation fields instead of inventing a second orchestration schema.
- The contract is stored in goal/session context as plain JSON-safe data so it can move across concierge, MetaController, and persisted goal-loop state without importing Pydantic runtime models everywhere.
- Prompt-side contract injection complements existing hard execution gates; it does not replace them.

## Notes
- This change covers both MetaController planning and the inline intent-extraction path, because some long-horizon goals bypass MetaController entirely.
- Goal-loop support is additive: the prompt uses the contract when present, but existing `/goal` flows still work unchanged without one.
