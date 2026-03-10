# 31-6: Goal-Oriented Loop

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable long-running autonomous execution loops that iterate until a target metric is met or a wall-clock deadline expires — e.g., "beat this Kaggle leaderboard score" or "achieve test coverage >= 90%."

## Problem

DAN's existing `WhileLoop` iterates until a condition on loop variables is true, with `max_iterations` as the safety cap. But there's no first-class support for:
- **Goal-oriented termination:** "keep trying until score >= X" where the score comes from an external evaluation (test suite, Kaggle submission, LLM-as-judge)
- **Wall-clock deadlines:** "stop after 24 hours and return the best result so far"
- **Best-so-far tracking:** across iterations, keep the best result and return it even if the deadline expires
- **Graduated strategy shifts:** if simple approaches fail, escalate to more creative/expensive strategies

The meta-orchestrator's `plan→execute→diagnose→repair` cycle (29-2) is close, but it's designed for single-attempt repair, not open-ended iterative improvement.

## Tasks

- [ ] 1. **GoalSpec model**
  - [ ] 1-1. `GoalSpec` Pydantic model: `metric_name: str`, `target_value: float`, `comparison: Literal[">=", "<=", "==", ">", "<"]`, `timeout_seconds: int | None`, `max_attempts: int = 100`, `evaluation_mode: Literal["script", "llm_judge", "test_suite", "custom"]`
  - [ ] 1-2. `EvaluationResult` model: `score: float`, `passed: bool`, `details: str`, `artifacts: dict[str, Any]`
  - [ ] 1-3. `GoalLoopState` model: `attempts: list[AttemptRecord]`, `best_score: float`, `best_attempt_id: str`, `start_time: datetime`, `strategy_tier: int`

- [ ] 2. **GoalLoop executor**
  - [ ] 2-1. `GoalLoopExecutor` in `executors/control_flow.py`: main loop — attempt → evaluate → if target met, exit with success; if timeout, exit with best-so-far; else diagnose and plan next attempt
  - [ ] 2-2. Pluggable evaluator interface: `ScriptEvaluator` (run a script, parse score from stdout), `LLMJudgeEvaluator` (LLM rates quality 1-10), `TestSuiteEvaluator` (run pytest, count pass/fail), `CustomEvaluator` (user-provided async callable)
  - [ ] 2-3. Best-so-far tracking: persist `GoalLoopState` via `LocalStateManager`; checkpoint after each attempt so crashes resume from last completed attempt
  - [ ] 2-4. Wall-clock enforcement: `asyncio` deadline check before each new attempt; on timeout, return `best_attempt` result with `timeout_reached=True` metadata

- [ ] 3. **Strategy escalation**
  - [ ] 3-1. Strategy tiers: tier 0 (direct attempt), tier 1 (parameter variation), tier 2 (approach change), tier 3 (decomposition/ensemble)
  - [ ] 3-2. Escalation trigger: after N consecutive failures at current tier (configurable, default 3), escalate to next tier
  - [ ] 3-3. Tier-specific prompts: inject strategy guidance into the LLM prompt based on current tier
  - [ ] 3-4. Wire into existing `RepairClassifier` / `RepairEscalator` (19-3) for diagnosis between attempts

- [ ] 4. **Concierge integration**
  - [ ] 4-1. Solver recognizes goal-loop intents: "beat this score", "keep improving until X", "iterate until test passes"
  - [ ] 4-2. `/goal` chat command: `/goal "score >= 0.85" --timeout 24h --eval script:evaluate.py`
  - [ ] 4-3. Progress notifications via `NotificationManager`: periodic updates ("attempt 7/100, best score: 0.82, 3h elapsed")
  - [ ] 4-4. `/goal-status` and `/goal-stop` commands for monitoring and early termination

- [ ] 5. **Builder / authoring**
  - [ ] 5-1. Builder DSL: `wf.goal_loop(node_id, goal=GoalSpec(...), body=...)` context manager
  - [ ] 5-2. Markdown syntax: `type: goal_loop` with goal spec in frontmatter
  - [ ] 5-3. Node type registration, palette entry, config panel fields

- [ ] 6. **Tests and docs**
  - [ ] 6-1. Unit tests: GoalSpec validation, evaluator dispatch, best-so-far tracking, timeout, strategy escalation
  - [ ] 6-2. Integration test: mock evaluator with improving scores, verify exit on target met
  - [ ] 6-3. Update architecture, llm-api-guide, changelog

## Dependencies

- `WhileLoop` / `GateNode` patterns (models, executor)
- `RepairClassifier` / `RepairEscalator` (19-3) for inter-attempt diagnosis
- `NotificationManager` (26-4) for progress updates
- `LocalStateManager` for GoalLoopState persistence
- `CheckpointStore` for crash recovery

## Estimate

2-3 days

## Primary Files

- `src/dan/models/control_flow.py` — GoalSpec, EvaluationResult, GoalLoopState models
- `src/dan/executors/control_flow.py` — GoalLoopExecutor
- `src/dan/engine/evaluators.py` — ScriptEvaluator, LLMJudgeEvaluator, TestSuiteEvaluator, CustomEvaluator
- `src/dan/server/concierge/runtime.py` — /goal command handling

## Notes

- The Kaggle use case is the motivating example: set `target_value` to a leaderboard score, `evaluation_mode` to `script` (which submits and parses the score), and `timeout_seconds` to 86400 (24h). DAN iterates automatically.
- Strategy escalation is inspired by the repair escalation ladder (19-3) but applied to the solution space, not the repair space.
- Each attempt is a full concierge-style interaction — not just re-running the same code. The LLM sees previous attempts, their scores, and the diagnosis, and generates a new approach.
- When an individual attempt involves multiple subtasks, the RCPSP scheduler (31-8) can optimize the internal execution of each attempt.
- The evaluator interface is async — `ScriptEvaluator` can run long-running evaluation scripts (Kaggle submissions that take minutes) without blocking the event loop.
